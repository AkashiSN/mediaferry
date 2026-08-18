"""起動時の齟齬回収（§9.6）.

library/ と derived/ の両方を対象にする。一時ファイルを無条件に消さないのは、
別ジョブが使用中の可能性があるため。必ずジョブの所有権とリース状態、および
artifact_staging の参照を確認してから消す。

孤立ファイルは**削除しない**。自動削除はデータを失う経路になるので、画面に
出してユーザの判断に委ねる。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.publisher import ArtifactPublisher, StagingLost
from ..clock import now_iso
from ..db.destinations import DestinationRepository
from ..db.jobs import JobStore
from ..db.uploads import UploadRepository

logger = logging.getLogger(__name__)

HASH_CHUNK = 4 * 1024 * 1024
LIVE_JOB_STATES = ("queued", "running", "cancelling")


@dataclass(frozen=True)
class OrphanFile:
    rel_path: str
    size_bytes: int
    sha1: str


@dataclass
class ReconcileReport:
    discarded: int = 0
    resumed: int = 0
    recommitted: int = 0
    missing: int = 0
    restored: int = 0
    cleaned_dirs: int = 0
    merges_completed: int = 0
    merges_released: int = 0
    # 回収できない staging を抱えていて、自動では動かせないグループ。
    merges_blocked: int = 0
    uploads_released: int = 0
    uploads_invalidated: int = 0
    credentials_purged: int = 0
    orphans: list[OrphanFile] = field(default_factory=list)
    # 自動では続行できなかった staging（実体が無い、内容が一致しない）。
    # 行は残す。画面に出して判断を仰ぐ。
    unrecoverable: list[str] = field(default_factory=list)


class Reconciler:
    def __init__(
        self,
        conn: sqlite3.Connection,
        data_root: Path,
        publisher: ArtifactPublisher,
        store: JobStore,
        *,
        uploads: UploadRepository | None = None,
        destinations: DestinationRepository | None = None,
    ) -> None:
        # **黙って skip しない。** 片方だけ渡す配線ミスをすると、claim の解放も
        # 旧 epoch の sweep も旧鍵の破棄も、何も言わずに行われなくなる。
        if (uploads is None) != (destinations is None):
            raise ValueError("uploads と destinations は組で渡す")
        self._conn = conn
        self._data_root = data_root
        self._publisher = publisher
        self._store = store
        self._uploads = uploads
        self._destinations = destinations

    def run(self) -> ReconcileReport:
        report = ReconcileReport()
        # 先にジョブを倒す。生きているジョブが無いことを確定させてから
        # staging と work を掃除する。
        self._store.sweep_interrupted()
        self._recover_staging(report)
        self._settle_merges(report)
        self._settle_uploads(report)
        self._sync_missing(report)
        self._collect_orphans(report)
        self._clean_job_dirs(report)
        return report

    def _settle_uploads(self, report: ReconcileReport) -> None:
        """中断したアップロードの claim を外し、根拠が消えた行を無効化する.

        `_settle_merges` の後に走らせる。そこでグループの状態が確定するので、
        「今のグループの状態」で根拠を評価できる。

        **旧 epoch の sweep と旧鍵の破棄もここで行う。** どちらも宛先の編集時に
        1 度は走るが、**その直後に落ちた場合に取り残される**（理由の無い pending が
        永久に残り、旧鍵は次の編集まで消えない）。起動時にもう一度均す。
        """
        if self._uploads is None or self._destinations is None:
            return
        report.uploads_released = self._uploads.release_interrupted()
        report.uploads_invalidated = self._uploads.invalidate_stale()
        for row in self._destinations.list_destinations(include_archived=True):
            current = self._destinations.get_current_or_none(row["id"])
            if current is None:
                continue
            report.uploads_invalidated += self._uploads.invalidate_old_epoch(
                row["id"], current["target_epoch"], "宛先の向き先が変わった"
            )
            report.credentials_purged += self._destinations.purge_superseded_credentials(row["id"])

    def _settle_merges(self, report: ReconcileReport) -> None:
        """`merging` のまま残ったグループを決着させる.

        公開まで進んでいれば（`output_media_file_id` が入っていれば）merged へ、
        進んでいなければ detected へ戻す。戻さないと再試行もできない。
        起動時に呼ぶので、走っているジョブは既に倒れている。

        **回収できなかった `artifact_staging` を抱えたグループは動かさない。**
        `_recover_staging` が `StagingLost` を残したものがこれにあたる。
        detected へ戻すと再試行でき、古い staged 行と新しい公開が同じ
        グループを指して、後の reconciliation がどちらの出力を書き込むかで
        履歴が上書きされる。「自動では続行しない」という契約を守る。
        """
        blocked = {
            row["merge_group_id"]
            for row in self._conn.execute(
                "SELECT DISTINCT merge_group_id FROM artifact_staging"
                " WHERE merge_group_id IS NOT NULL AND state <> 'published'"
            )
        }
        for row in self._conn.execute(
            "SELECT id, output_media_file_id FROM merge_group WHERE status = 'merging'"
        ).fetchall():
            if row["id"] in blocked:
                report.merges_blocked += 1
                logger.warning("結合 %s は回収できない staging を抱えている", row["id"])
                continue
            if row["output_media_file_id"] is not None:
                target, counter = "merged", "merges_completed"
            else:
                target, counter = "detected", "merges_released"
            self._conn.execute(
                "UPDATE merge_group SET status = ?, updated_at = ? WHERE id = ?",
                (target, now_iso(), row["id"]),
            )
            setattr(report, counter, getattr(report, counter) + 1)

    def _recover_staging(self, report: ReconcileReport) -> None:
        # published の行は commit と同じトランザクションで media_file を作るので、
        # 通常は齟齬が出ない。手で DB をいじった場合や将来の版のために拾っておく。
        rows = list(
            self._conn.execute(
                "SELECT s.id AS id, s.state AS state FROM artifact_staging s"
                " LEFT JOIN media_file m ON m.rel_path = s.final_rel_path"
                " WHERE s.state <> 'published' OR m.id IS NULL"
            )
        )
        for row in rows:
            state = row["state"]
            try:
                self._publisher.resume(row["id"])
            except StagingLost:
                # 実体が無いか内容が合わない。黙って消さず、画面に出す。
                logger.warning("staging %s は自動で回収できない", row["id"])
                report.unrecoverable.append(row["id"])
                continue
            except OSError:
                # 1 件の失敗で回収全体を止めない。行は残るので次回も試す。
                logger.exception("staging %s の回収に失敗した", row["id"])
                report.unrecoverable.append(row["id"])
                continue
            if state == "writing":
                report.discarded += 1
            elif state == "staged":
                report.resumed += 1
            else:
                report.recommitted += 1

    def _sync_missing(self, report: ReconcileReport) -> None:
        """欠損を立てるだけでなく、戻ってきたら消す.

        データセットが一時的に見えなかっただけで永久に「欠損」のまま残ると、
        そのメディアはアップロードの安全条件（§10）から外れ続ける。
        """
        for row in self._conn.execute("SELECT id, rel_path, missing_at FROM media_file"):
            exists = (self._data_root / row["rel_path"]).exists()
            if not exists and row["missing_at"] is None:
                self._conn.execute(
                    "UPDATE media_file SET missing_at = ? WHERE id = ?", (now_iso(), row["id"])
                )
                report.missing += 1
            elif exists and row["missing_at"] is not None:
                self._conn.execute(
                    "UPDATE media_file SET missing_at = NULL WHERE id = ?", (row["id"],)
                )
                report.restored += 1

    def _collect_orphans(self, report: ReconcileReport) -> None:
        known = {row["rel_path"] for row in self._conn.execute("SELECT rel_path FROM media_file")}
        staged = {
            row["final_rel_path"]
            for row in self._conn.execute(
                "SELECT final_rel_path FROM artifact_staging WHERE final_rel_path IS NOT NULL"
            )
        }
        for top in ("library", "derived"):
            base = self._data_root / top
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(self._data_root))
                if rel in known or rel in staged:
                    continue
                report.orphans.append(
                    OrphanFile(rel_path=rel, size_bytes=path.stat().st_size, sha1=_sha1_of(path))
                )

    def _clean_job_dirs(self, report: ReconcileReport) -> None:
        live_jobs = {
            row["id"]
            for row in self._conn.execute(
                f"SELECT id FROM job WHERE status IN ({','.join('?' * len(LIVE_JOB_STATES))})",  # noqa: S608
                LIVE_JOB_STATES,
            )
        }
        # 回収できずに残った行が指すディレクトリは消さない。
        referenced = {
            row["job_id"]
            for row in self._conn.execute(
                "SELECT DISTINCT job_id FROM artifact_staging WHERE state <> 'published'"
            )
        }
        for top in ("staging", "work"):
            base = self._data_root / top
            if not base.exists():
                continue
            for path in sorted(base.iterdir()):
                if not path.is_dir():
                    continue
                if path.name in live_jobs or path.name in referenced:
                    continue
                shutil.rmtree(path)
                report.cleaned_dirs += 1


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as f:
        while chunk := f.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
