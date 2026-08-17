"""アーティファクトの公開プロトコル（§9.3）.

取り込みと結合の**両方**がこの手順を使う。ファイルの公開と DB のコミットの間に
落ちても、齟齬を検出して回収できる状態にする。

公開は `os.link` で行う。既存があれば EEXIST で失敗するので、`os.replace` の
ように既存を黙って上書きしない。`renameat2(RENAME_NOREPLACE)` は Python 標準
ライブラリから呼べないが、`link` は同じ no-clobber 性を持ち、同一ファイル
システム内で原子的である。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from ..clock import now_iso
from ..core.naming import candidate_paths, staging_rel_path
from ..core.timestamps import CapturedAt
from ..db.connection import immediate
from ..db.jobs import JobContext, LeaseLost
from ..ids import new_id
from .ffprobe import MediaProbe
from .fs import fsync_dir

STEP_WRITING_ROW = 1
STEP_WRITTEN = 2
STEP_FSYNCED = 3
STEP_VERIFIED = 4
STEP_METADATA = 5
STEP_FINAL_PATH = 6
STEP_STAGED = 7
STEP_LINKED = 8
STEP_DIR_FSYNCED = 9
STEP_STAGING_UNLINKED = 10
STEP_COMMITTED = 11

COPY_CHUNK = 4 * 1024 * 1024


class PublishAborted(RuntimeError):
    """staged へ進む前に中止した.

    durable なものは何も残っていない（writing の行だけが残り、次回起動で
    破棄される）。呼び出し元は source_entry を差し戻して再実行してよい。
    """


class PublishInterrupted(RuntimeError):
    """staged 以降で失敗した.

    **呼び出し元はこれを「取り込み失敗」として扱ってはならない。**
    ファイルは検証済みで、公開に必要な情報はすべて永続化されているので、
    起動時の reconciliation が公開を完遂する。source_entry を failed に
    戻すと、次のスキャンで新規と判定されて二重に取り込む。
    """


class StagingLost(RuntimeError):
    """staging も final も無い（または内容が一致しない）.

    自動では続行しない。reconciliation は行を残したまま画面に出す。
    """


@dataclass(frozen=True)
class ArtifactRequest:
    kind: str  # import / merge
    role: str  # original / derived
    profile_id: str
    profile_revision_id: str
    desired_rel_path: str
    source_rel_path: str
    extension: str
    captured: CapturedAt
    mtime_ns: int
    source_entry_id: str | None
    merge_group_id: str | None


@dataclass(frozen=True)
class PublishedArtifact:
    media_file_id: str
    rel_path: str
    size_bytes: int
    sha1: str
    reused_existing: bool


class HashingWriter:
    """書き込みストリームで SHA-1 を計算する. 読み直しを 1 回省く."""

    def __init__(self, fileobj: BinaryIO) -> None:
        self._fileobj = fileobj
        self._digest = hashlib.sha1(usedforsecurity=False)
        self.size = 0

    def write(self, data: bytes) -> int:
        self._fileobj.write(data)
        self._digest.update(data)
        self.size += len(data)
        return len(data)

    @property
    def sha1(self) -> str:
        return self._digest.hexdigest()


class ArtifactPublisher:
    def __init__(self, conn: sqlite3.Connection, data_root: Path, probe: MediaProbe) -> None:
        self._conn = conn
        self._data_root = data_root
        self._probe = probe

    def _checkpoint(self, step: int) -> None:
        """crash consistency テストが差し込む継ぎ目. 本番では何もしない."""

    # ------------------------------------------------------------------
    def publish(
        self,
        ctx: JobContext,
        request: ArtifactRequest,
        write: Callable[[HashingWriter], None],
    ) -> PublishedArtifact:
        staging_id = new_id()
        staging_rel = staging_rel_path(ctx.job_id, staging_id)
        staging_abs = self._data_root / staging_rel

        # 1. writing の行を先に commit する。ここから先はどこで落ちても回収できる。
        self._conn.execute(
            "INSERT INTO artifact_staging (id, kind, job_id, lease_token, state,"
            " staging_rel_path, source_entry_id, merge_group_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'writing', ?, ?, ?, ?, ?)",
            (
                staging_id,
                request.kind,
                ctx.job_id,
                ctx.lease_token,
                staging_rel,
                request.source_entry_id,
                request.merge_group_id,
                now_iso(),
                now_iso(),
            ),
        )
        self._checkpoint(STEP_WRITING_ROW)

        # 2. 書き込み。SHA-1 はストリームで取る。
        #    ジョブ用ディレクトリを新しく作ったときは、その名前を持つ親
        #    （staging/）も fsync する。中のファイルだけ永続化しても、
        #    <job-id> のエントリが失われれば丸ごと消える。
        if not staging_abs.parent.exists():
            staging_abs.parent.mkdir(parents=True, exist_ok=True)
            fsync_dir(staging_abs.parent.parent)
        with staging_abs.open("wb") as fileobj:
            writer = HashingWriter(fileobj)
            write(writer)
            fileobj.flush()
            self._checkpoint(STEP_WRITTEN)
            # mtime は fsync より前に付ける。後に付けると metadata の
            # 永続化が保証されない。
            os.utime(fileobj.fileno(), ns=(request.mtime_ns, request.mtime_ns))
            # 3. 中身とディレクトリエントリの両方を永続化する。親を fsync
            #    しないと、電源断で「DB は staged、ファイルは無い」になる。
            os.fsync(fileobj.fileno())
        fsync_dir(staging_abs.parent)
        self._checkpoint(STEP_FSYNCED)

        # 4. サイズとハッシュの検証
        on_disk = staging_abs.stat().st_size
        if on_disk != writer.size:
            raise PublishAborted(f"書き込みサイズが一致しない（{on_disk} != {writer.size}）")
        self._checkpoint(STEP_VERIFIED)

        # 5. メタデータは公開前に確定させる。実体はあるがメタデータが欠けたまま
        #    永久にスキップされる状態を作らない。
        probe = self._probe.describe(staging_abs, request.extension)
        metadata = {
            "role": request.role,
            # 衝突時の別名系列は必ず「最初に望んだパス」から辿る。再開時に
            # 変更後の final_rel_path から辿ると、名前に接尾辞が二重に付く。
            "desired_rel_path": request.desired_rel_path,
            "profile_id": request.profile_id,
            "profile_revision_id": request.profile_revision_id,
            "kind": probe.kind,
            # UTC へ正規化しない。復元した現地の壁時計が読めなくなる。
            "captured_at": request.captured.at.isoformat(),
            "captured_at_source": request.captured.source,
            "captured_at_tz": request.captured.tz,
            "captured_at_note": request.captured.note,
            "duration_seconds": probe.duration_seconds,
            "probe_state": probe.probe_state,
            "mtime_ns": request.mtime_ns,
            # 衝突時の別名に使う壁時計。ここで確定して永続化する。再開のたびに
            # 計算し直すと、算出方法を変えた版で別の名前へ落ちる。
            "collision_stamp": _collision_stamp(request.mtime_ns),
        }
        self._checkpoint(STEP_METADATA)

        # 6. 公開先の決定
        final_rel = request.desired_rel_path
        self._checkpoint(STEP_FINAL_PATH)

        # 7. staged。ここが後戻りできない点で、以後は reconciliation が公開を
        #    完遂する。だからリースの確認は手順 8 の直前ではなくここで行う。
        #
        #    確認と遷移を 1 つの BEGIN IMMEDIATE に入れるのが要点。別々にすると
        #    その隙間にキャンセルが commit でき、「キャンセル済みと表示した後に
        #    公開される」経路が残る。
        try:
            with immediate(self._conn):
                ctx.assert_lease()
                self._conn.execute(
                    "UPDATE artifact_staging SET state = 'staged', final_rel_path = ?,"
                    " expected_size = ?, content_sha1 = ?, metadata_json = ?, updated_at = ?"
                    " WHERE id = ?",
                    (
                        final_rel,
                        writer.size,
                        writer.sha1,
                        json.dumps(metadata, ensure_ascii=False),
                        now_iso(),
                        staging_id,
                    ),
                )
        except LeaseLost as exc:
            raise PublishAborted(str(exc)) from exc
        self._checkpoint(STEP_STAGED)

        try:
            return self._finish(staging_id)
        except PublishInterrupted:
            raise
        except Exception as exc:
            # ここから先の失敗は「取り込み失敗」ではない。回収は起動時に走る。
            raise PublishInterrupted(str(exc)) from exc

    def resume(self, staging_id: str) -> PublishedArtifact | None:
        """reconciliation から呼ぶ. 永続化済みの情報だけを使い、パスを推測しない."""
        row = self._row(staging_id)
        if row is None:
            return None
        if row["state"] == "writing":
            with contextlib.suppress(OSError):
                (self._data_root / row["staging_rel_path"]).unlink()
            self._conn.execute("DELETE FROM artifact_staging WHERE id = ?", (staging_id,))
            return None
        return self._finish(staging_id)

    # ------------------------------------------------------------------
    def _finish(self, staging_id: str) -> PublishedArtifact:
        """手順 8 以降. 何度呼んでも同じ結果になる."""
        row = self._row(staging_id)
        reused = False
        if row["state"] == "staged":
            reused = self._link(staging_id)
            row = self._row(staging_id)
        return self._commit(row, reused)

    def _link(self, staging_id: str) -> bool:
        """8〜10. no-clobber で公開し、staging を消す. 既存と同内容なら True."""
        row = self._row(staging_id)
        staging_abs = self._data_root / row["staging_rel_path"]
        metadata = json.loads(row["metadata_json"])

        if not staging_abs.exists():
            # 手順 10（staging の unlink）まで進んだ後に落ちた場合。state は
            # staged のままなので、ここを通らないと再開のたびに os.link が
            # FileNotFoundError になり、永久に commit できない。
            return self._adopt_published_final(row)

        names = candidate_paths(
            metadata["desired_rel_path"],
            metadata["collision_stamp"],
            row["content_sha1"],
        )
        reused = False
        final_rel = row["final_rel_path"]
        # 系列を現在の final_rel_path まで進める。再開時に先頭へ戻ると
        # すでに使った名前を試し直し、接尾辞が二重に付く。
        for candidate in names:
            if candidate == final_rel:
                break
        while True:
            final_abs = self._data_root / final_rel
            final_abs.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(staging_abs, final_abs)
                break
            except FileExistsError:
                if _is_same_content(final_abs, row["expected_size"], row["content_sha1"]):
                    reused = True
                    break
                final_rel = next(names)
                self._conn.execute(
                    "UPDATE artifact_staging SET final_rel_path = ?, updated_at = ? WHERE id = ?",
                    (final_rel, now_iso(), staging_id),
                )
        self._checkpoint(STEP_LINKED)

        # 9. 公開先の親を fsync する。怠ると電源断で公開が失われる。
        fsync_dir((self._data_root / final_rel).parent)
        self._checkpoint(STEP_DIR_FSYNCED)

        # 10. staging を消し、その親も fsync する。
        with contextlib.suppress(FileNotFoundError):
            staging_abs.unlink()
        if staging_abs.parent.exists():
            fsync_dir(staging_abs.parent)
        self._checkpoint(STEP_STAGING_UNLINKED)
        return reused

    def _adopt_published_final(self, row: sqlite3.Row) -> bool:
        """staging が無い staged 行を、final の実体だけで判定して引き取る.

        永続化した expected_size と content_sha1 だけを使う。パスも内容も
        推測しない。一致しなければ自動では続行せず、画面に出して判断を仰ぐ。
        """
        final_abs = self._data_root / row["final_rel_path"]
        if _is_same_content(final_abs, row["expected_size"], row["content_sha1"]):
            fsync_dir(final_abs.parent)
            self._checkpoint(STEP_DIR_FSYNCED)
            self._checkpoint(STEP_STAGING_UNLINKED)
            return True
        raise StagingLost(
            f"staging {row['id']} の一時ファイルが無く、{row['final_rel_path']} も"
            "記録した大きさ・ハッシュと一致しない"
        )

    def _commit(self, row: sqlite3.Row, reused: bool) -> PublishedArtifact:
        """11. media_file を作り、呼び出し元のレコードを更新する."""
        metadata = json.loads(row["metadata_json"])
        final_rel = row["final_rel_path"]
        with immediate(self._conn):
            existing = self._conn.execute(
                "SELECT id FROM media_file WHERE rel_path = ?", (final_rel,)
            ).fetchone()
            if existing is not None:
                media_file_id = existing["id"]
            else:
                media_file_id = new_id()
                self._conn.execute(
                    "INSERT INTO media_file (id, role, profile_id, profile_revision_id, rel_path,"
                    " size_bytes, mtime_ns, sha1, kind, captured_at, captured_at_source,"
                    " captured_at_tz, captured_at_note, duration_seconds, probe_state, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        media_file_id,
                        metadata["role"],
                        metadata["profile_id"],
                        metadata["profile_revision_id"],
                        final_rel,
                        row["expected_size"],
                        metadata["mtime_ns"],
                        row["content_sha1"],
                        metadata["kind"],
                        metadata["captured_at"],
                        metadata["captured_at_source"],
                        metadata["captured_at_tz"],
                        metadata["captured_at_note"],
                        metadata["duration_seconds"],
                        metadata["probe_state"],
                        now_iso(),
                    ),
                )
            if row["source_entry_id"] is not None:
                self._conn.execute(
                    "UPDATE source_entry SET media_file_id = ?, state = 'published' WHERE id = ?",
                    (media_file_id, row["source_entry_id"]),
                )
            if row["merge_group_id"] is not None:
                self._conn.execute(
                    "UPDATE merge_group SET output_media_file_id = ?, updated_at = ? WHERE id = ?",
                    (media_file_id, now_iso(), row["merge_group_id"]),
                )
            self._conn.execute(
                "UPDATE artifact_staging SET state = 'published', updated_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
        self._checkpoint(STEP_COMMITTED)
        return PublishedArtifact(
            media_file_id=media_file_id,
            rel_path=final_rel,
            size_bytes=row["expected_size"],
            sha1=row["content_sha1"],
            reused_existing=reused,
        )

    def _row(self, staging_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM artifact_staging WHERE id = ?", (staging_id,)
        ).fetchone()


def _collision_stamp(mtime_ns: int) -> str:
    """衝突時の別名に使う、カード上の壁時計.

    `timestamps.py` と同じ前提に立つ（カードの時刻欄に UTC オフセットが
    書かれていない）。その前提の下では、プロファイルの `timezone` を付けても
    表示される桁は変わらない（オフセットの付与は瞬間を移動しない）。
    """
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=UTC).strftime("%Y%m%d%H%M%S")


def _is_same_content(path: Path, expected_size: int, expected_sha1: str) -> bool:
    """大きさとハッシュの両方で判定する. ハッシュだけだと stat の齟齬を見逃す."""
    if not path.exists() or path.stat().st_size != expected_size:
        return False
    return _sha1_of(path) == expected_sha1


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as f:
        while chunk := f.read(COPY_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
