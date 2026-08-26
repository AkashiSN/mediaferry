"""結合ジョブ（§9.8）.

1 ジョブが 1 グループを扱う。出力は `work/<job-id>/` に作り、検証してから
`ArtifactPublisher.publish_prepared` で `derived/` へ公開する。最終パスへ
直接書かない。

**検証結果は公開の前に commit する。** 公開の途中で落ちても検証をやり直さない。

合格・不合格にかかわらず公開する。不合格は `adopted_at = NULL` のまま残り、
既定の選択肢から外れる（§10）。`work/` に置いたままにすると、リース失効時の
掃除で消えてしまう。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..adapters.ffmpeg import MergeCancelled, MergeRunner
from ..adapters.ffprobe import MediaProbe
from ..adapters.publisher import (
    ArtifactPublisher,
    ArtifactRequest,
    PublishCancelled,
    PublishInterrupted,
)
from ..core.merge.grouping import MergePart
from ..core.merge.output import merged_rel_path
from ..core.merge.verify import ProbedFile, verify
from ..core.naming import work_rel_path
from ..core.timestamps import CapturedAt, mtime_ns_of
from ..db.jobs import JobContext
from ..db.merges import MergeRepository
from ..db.profiles import ProfileRef

# 空き容量の見積りに乗せる余裕。DB とサムネイルの分。
FREE_SPACE_MARGIN = 512 * 1024 * 1024
# TS フォールバックのピーク。全パートの .ts と結合後の出力が同時に置かれる。
TS_PEAK_FACTOR = 2
# 結合の途中に work/ へ置かれる器。出力の拡張子はプロファイルが決めるので、
# ここに無い器を選ぶと進捗が 0 のままになる。
MERGE_ARTIFACT_SUFFIXES = (".mp4", ".mov", ".ts")


def merge_bytes_written(work: Path) -> int:
    """`work/` に書けた量. **ffmpeg は別プロセスなので育ち方でしか測れない.**

    TS 経路は「各パートの `.ts`」と「結合後の出力」を両方置くので、両方数える。
    """
    return sum(
        path.stat().st_size
        for path in work.glob("*")
        if path.suffix.lower() in MERGE_ARTIFACT_SUFFIXES
    )


class MergeInputsChanged(RuntimeError):
    """構成ファイルが読めない（消えた、欠損が立っている）."""


class NotEnoughSpace(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeResult:
    media_file_id: str
    rel_path: str
    route: str
    passed: bool


class Merger:
    def __init__(
        self,
        conn: sqlite3.Connection,
        repo: MergeRepository,
        publisher: ArtifactPublisher,
        runner: MergeRunner,
        probe: MediaProbe,
        data_root: Path,
    ) -> None:
        self._conn = conn
        self._repo = repo
        self._publisher = publisher
        self._runner = runner
        self._probe = probe
        self._data_root = data_root

    def run(
        self, ctx: JobContext, group_id: str, expected_digest: str, profile: ProfileRef
    ) -> MergeResult:
        # 構成か状態が変わっていれば、1 バイトも読まずに止まる。
        self._repo.claim_for_merge(group_id, expected_digest)
        try:
            return self._merge(ctx, group_id, profile)
        except MergeCancelled, PublishCancelled:
            # どちらも staged より前。durable なものは残っていないので、
            # グループを detected へ戻して再実行できるようにする。
            self._repo.release(group_id)
            raise
        except PublishInterrupted:
            # staged 以降。ファイルは検証済みで、公開に必要な情報は永続化
            # されている。**failed にしない**（起動時の reconciliation が完遂する）。
            ctx.emit("warning", "結合物の公開は起動時に再開される")
            raise
        except Exception as exc:
            self._repo.mark_failed(group_id, str(exc))
            raise
        finally:
            shutil.rmtree(self._data_root / work_rel_path(ctx.job_id), ignore_errors=True)

    # ------------------------------------------------------------------
    def _merge(self, ctx: JobContext, group_id: str, profile: ProfileRef) -> MergeResult:
        members = self._repo.members(group_id)
        parts = [self._data_root / row["rel_path"] for row in members]
        for row, path in zip(members, parts, strict=True):
            if row["missing_at"] is not None or not path.exists():
                raise MergeInputsChanged(f"{row['rel_path']} が読めない")
        self._assert_space(members)

        rule = profile.definition.merge
        desired = merged_rel_path(profile.definition.slug, rule, _as_merge_parts(members))
        extension = PurePosixPath(desired).suffix.lstrip(".").upper()

        work = self._data_root / work_rel_path(ctx.job_id)
        work.mkdir(parents=True, exist_ok=True)
        # **全パートを先に probe する。** 先頭の構成を全体に当てはめると、
        # 保持しない data track の位置が違うパートで別のストリームを選ぶ。
        probed_parts = [self._probed(path, extension) for path in parts]

        # **経路の判断はジョブの記録に残す。** どちらを通ったかは「その出力が
        # なぜその形なのか」の説明なので、コンテナのログにしか無いと画面から
        # 追えない（実機で `tmcd` が原因の TS 落ちを、ログを読むまで気づけなかった）。
        fell_back = False

        def note(message: str) -> None:
            nonlocal fell_back
            # note が出るのは TS へ落ちるときだけ（並びが違う場合も TS へ行く）。
            fell_back = True
            ctx.emit("warning", message, {"merge_group_id": group_id})

        total_bytes = sum(row["size_bytes"] for row in members)

        def beat() -> None:
            ctx.heartbeat(
                {
                    "phase": "merge",
                    "rel_path": desired,
                    "route": "ts" if fell_back else "concat",
                    "parts": len(parts),
                    "bytes_done": merge_bytes_written(work),
                    "bytes_total": total_bytes * (2 if fell_back else 1),
                }
            )

        outcome = self._runner.merge(
            parts,
            [probed.streams for probed in probed_parts],
            rule.keep_streams,
            work,
            PurePosixPath(desired).name,
            beat,
            ctx.cancelled,
            on_note=note,
        )

        verification = verify(
            probed_parts,
            self._probed(outcome.output_path, extension),
            rule.keep_streams,
            outcome.route,
            outcome.dropped_by_route,
        )
        # 公開の前に残す。公開の途中で落ちても検証をやり直さない。
        self._repo.record_verification(group_id, verification.to_json(), outcome.tool_version)
        ctx.emit(
            "info" if verification.passed else "warning",
            f"検証は{'合格' if verification.passed else '不合格'}（経路 {outcome.route}）",
            {"merge_group_id": group_id},
        )

        # 公開の直前にキャンセルを再確認する。リースは公開の手順 7 が見る。
        if ctx.cancelled():
            raise MergeCancelled("公開の直前にキャンセルを観測した")
        published = self._publisher.publish_prepared(
            ctx,
            ArtifactRequest(
                kind="merge",
                role="derived",
                profile_id=profile.profile_id,
                profile_revision_id=profile.revision_id,
                desired_rel_path=desired,
                source_rel_path=members[0]["rel_path"],
                extension=extension,
                captured=_captured_of(members[0]),
                mtime_ns=_recording_end_ns(members, profile.definition.timestamp.mtime_semantics),
                mtime_semantics=profile.definition.timestamp.mtime_semantics,
                source_entry_id=None,
                merge_group_id=group_id,
            ),
            outcome.output_path,
        )
        self._repo.mark_merged(group_id)
        return MergeResult(
            media_file_id=published.media_file_id,
            rel_path=published.rel_path,
            route=outcome.route,
            passed=verification.passed,
        )

    def _probed(self, path: Path, extension: str) -> ProbedFile:
        result = self._probe.describe(path, extension)
        return ProbedFile(
            duration_seconds=result.duration_seconds,
            size_bytes=path.stat().st_size,
            streams=tuple(result.streams),
        )

    def _assert_space(self, members: list[sqlite3.Row]) -> None:
        """**TS 経路のピークで見積もる。**

        TS フォールバックでは、全パートの `.ts` と結合後の出力が同時に
        `work/` に存在する。入力の合計しか要求しないと、`.ts` を作り終えた後の
        出力生成で ENOSPC になり、「始める前に止める」という約束を破る。
        """
        needed = TS_PEAK_FACTOR * sum(row["size_bytes"] for row in members)
        stat = os.statvfs(self._data_root)
        if needed + FREE_SPACE_MARGIN > stat.f_bavail * stat.f_frsize:
            raise NotEnoughSpace(f"{needed} バイトの結合に空き容量が足りない")


def _as_merge_parts(members: list[sqlite3.Row]) -> list[MergePart]:
    return [
        MergePart(
            media_file_id=row["media_file_id"],
            rel_path=row["rel_path"],
            sha1=row["sha1"],
            captured_at=datetime.fromisoformat(row["captured_at"]),
            duration_seconds=row["duration_seconds"],
            size_bytes=row["size_bytes"],
            probe_state=row["probe_state"],
        )
        for row in members
    ]


def _captured_of(row: sqlite3.Row) -> CapturedAt:
    """派生物は先頭パートの撮影日時を引き継ぐ."""
    return CapturedAt(
        at=datetime.fromisoformat(row["captured_at"]),
        source=row["captured_at_source"],
        tz=row["captured_at_tz"],
        note=row["captured_at_note"],
    )


def _recording_end_ns(members: list[sqlite3.Row], semantics: str) -> int:
    """録画終了時刻（最後のパートの開始 + duration）を mtime にする（§9.8 手順 6）.

    **取り込んだファイルと同じ表現にする**（`timestamps.mtime_ns_of`）。片方だけ
    別の表現にすると、`library/` と `derived/` で epoch がオフセットぶんずれる。

    オフセットの無い値はシステムの TZ で読まれてしまうので UTC と見なす。
    """
    last = members[-1]
    start = datetime.fromisoformat(last["captured_at"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    duration = last["duration_seconds"] or 0.0
    return mtime_ns_of(start, semantics) + int(duration * 1e9)
