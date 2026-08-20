"""ソースからライブラリへの取り込み（§9.4）.

ファイルは 1 つずつ順に処理する。USB が律速なので並列化しない。公開は
ArtifactPublisher に委譲し、中断ファイルが転送先に残る問題は staging と
no-clobber 公開で構造的に起きないようにしてある。
"""

from __future__ import annotations

import errno
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..adapters.exif import read_datetime_original
from ..adapters.ffprobe import PHOTO_EXTENSIONS
from ..adapters.fs import open_beneath
from ..adapters.publisher import (
    ArtifactPublisher,
    ArtifactRequest,
    HashingWriter,
    PublishAborted,
    PublishInterrupted,
)
from ..clock import now_iso
from ..core.naming import library_rel_path
from ..core.timestamps import CapturedAt, resolve_captured_at
from ..db.jobs import LEASE_SECONDS, JobContext
from ..db.profiles import ProfileRef

COPY_CHUNK = 4 * 1024 * 1024
# 空き容量の見積りに乗せる余裕。DB とサムネイルの分。
FREE_SPACE_MARGIN = 512 * 1024 * 1024
# リース (60 秒) の 1/3 ごとに延ばす。16GiB のコピーはリースより長く、
# 転送速度は環境で桁が変わるので、バイト数ではなく時間で決める。
HEARTBEAT_INTERVAL = LEASE_SECONDS / 3

# カードが抜けたときに出る errno。残りを試しても同じように失敗する。
_DEVICE_GONE = frozenset({errno.EIO, errno.ENODEV, errno.ENXIO, errno.ESTALE, errno.EBADF})


class NotEnoughSpace(RuntimeError):
    pass


class CopyCancelled(RuntimeError):
    """コピーの途中でキャンセル要求を観測した."""


@dataclass(frozen=True)
class ImportOutcome:
    published: int
    skipped: int
    failed: int


class ImportFailed(RuntimeError):
    """1 件以上の取り込みに失敗した. ジョブを failed にするために送出する."""

    def __init__(self, message: str, outcome: ImportOutcome) -> None:
        super().__init__(message)
        self.outcome = outcome


class Importer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        publisher: ArtifactPublisher,
        data_root: Path,
        default_timezone: str | None,
    ) -> None:
        self._conn = conn
        self._publisher = publisher
        self._data_root = data_root
        self._default_timezone = default_timezone

    def run(
        self, ctx: JobContext, dirfd: int, volume_instance_id: str, profile: ProfileRef
    ) -> ImportOutcome:
        pending = list(
            self._conn.execute(
                "SELECT * FROM source_entry WHERE volume_instance_id = ?"
                " AND state IN ('seen', 'failed') ORDER BY rel_path",
                (volume_instance_id,),
            )
        )
        skipped = self._conn.execute(
            "SELECT count(*) FROM source_entry"
            " WHERE volume_instance_id = ? AND state = 'published'",
            (volume_instance_id,),
        ).fetchone()[0]

        # 取り込みを一切開始しない条件を先に確かめる。途中で止まると
        # 中途半端な状態がユーザに見えるため。
        needed = sum(row["size_bytes"] for row in pending)
        if needed + FREE_SPACE_MARGIN > self._free_bytes():
            raise NotEnoughSpace(f"{needed} バイトの取り込みに空き容量が足りない")
        for row in pending:
            resolve_captured_at(
                profile.definition, row["rel_path"], row["mtime_ns"], self._default_timezone
            )

        published = failed = 0
        for row in pending:
            if ctx.cancelled():
                break
            ctx.heartbeat()
            try:
                self._publish_one(ctx, dirfd, row, profile)
            except PublishAborted, CopyCancelled:
                # staged より前なので durable なものは残っていない。差し戻す。
                # キャンセルなら降りる。失効なら失敗として上へ投げる。
                self._conn.execute(
                    "UPDATE source_entry SET state = 'seen' WHERE id = ?", (row["id"],)
                )
                if ctx.cancelled():
                    break
                raise
            except PublishInterrupted:
                # staged 以降で失敗した。ファイルは検証済みで、起動時の
                # reconciliation が公開を完遂する。**failed に戻さない**
                # （戻すと次のスキャンで新規と判定され、二重に取り込む）。
                ctx.emit("warning", f"{row['rel_path']} の公開は起動時に再開される")
                raise
            except OSError as exc:
                failed += 1
                self._mark_failed(row["id"])
                ctx.emit("error", f"{row['rel_path']} の取り込みに失敗した: {exc}")
                if exc.errno in _DEVICE_GONE:
                    # カードが抜かれた。残りを試しても同じように失敗する。
                    ctx.emit("error", "ソースが読めなくなったので中断する")
                    break
                continue
            except Exception as exc:  # noqa: BLE001 - 1 件の失敗で全体を止めない
                failed += 1
                self._mark_failed(row["id"])
                ctx.emit("error", f"{row['rel_path']} の取り込みに失敗した: {exc}")
                continue
            published += 1
            ctx.emit("info", f"{row['rel_path']} を取り込んだ")

        outcome = ImportOutcome(published=published, skipped=skipped, failed=failed)
        if failed:
            # 1 件でも落ちたらジョブは失敗にする。全件失敗しても succeeded に
            # なると、監視も画面も「取り込めた」と読んでしまう。
            raise ImportFailed(f"{failed} 件の取り込みに失敗した（成功 {published} 件）", outcome)
        return outcome

    def _mark_failed(self, entry_id: str) -> None:
        self._conn.execute("UPDATE source_entry SET state = 'failed' WHERE id = ?", (entry_id,))

    def _captured_for(
        self, row: sqlite3.Row, profile: ProfileRef
    ) -> tuple[CapturedAt | None, Callable[[Path], CapturedAt] | None]:
        """値か、ステージ済みファイルから決める読み方か、どちらか一方を返す.

        **画像以外では EXIF を読まない。** `exifread` は認識できない入力に対して
        例外ではなく警告を出すので、Canon の MOV のように `source: exif` の
        プロファイルを通る動画で呼ぶと、1 本ごとに警告が並ぶ。振り分けは
        `MediaProbe` と同じ拡張子の規則で行う（判定が 2 箇所に散らない）。
        """
        extension = PurePosixPath(row["rel_path"]).suffix.lstrip(".").upper()
        if profile.definition.timestamp.source != "exif" or extension not in PHOTO_EXTENSIONS:
            return (
                resolve_captured_at(
                    profile.definition, row["rel_path"], row["mtime_ns"], self._default_timezone
                ),
                None,
            )

        def resolve(staging_abs: Path) -> CapturedAt:
            return resolve_captured_at(
                profile.definition,
                row["rel_path"],
                row["mtime_ns"],
                self._default_timezone,
                exif_wall=read_datetime_original(staging_abs),
            )

        return None, resolve

    def _publish_one(
        self, ctx: JobContext, dirfd: int, row: sqlite3.Row, profile: ProfileRef
    ) -> None:
        # **EXIF はステージ済みのファイルから読む**（§9.3 手順 5）。ここでは
        # まだ staging が無いので、値ではなく「読み方」を渡す。
        captured, resolver = self._captured_for(row, profile)
        request = ArtifactRequest(
            kind="import",
            role="original",
            profile_id=profile.profile_id,
            profile_revision_id=profile.revision_id,
            desired_rel_path=library_rel_path("original", profile.definition.slug, row["rel_path"]),
            source_rel_path=row["rel_path"],
            extension=PurePosixPath(row["rel_path"]).suffix.lstrip(".").upper(),
            captured=captured,
            mtime_ns=row["mtime_ns"],
            source_entry_id=row["id"],
            merge_group_id=None,
            resolve_captured=resolver,
        )
        self._conn.execute(
            "UPDATE source_entry SET state = 'importing', observed_at = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )

        def write(writer: HashingWriter) -> None:
            fd = open_beneath(dirfd, row["rel_path"])
            last_beat = time.monotonic()
            with os.fdopen(fd, "rb") as source:
                while chunk := source.read(COPY_CHUNK):
                    writer.write(chunk)
                    # chunk 境界がキャンセルポイント（§9.9）。ここで見ないと、
                    # 16GiB のコピーが終わるまで停止要求に応じられない。
                    if ctx.cancelled():
                        raise CopyCancelled(row["rel_path"])
                    if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL:
                        # **バイト数ではなく経過時間で打つ。** 低速なカードや
                        # read が詰まった場合、閾値バイトに達する前にリースが
                        # 切れて全件が中止される。
                        ctx.heartbeat()
                        last_beat = time.monotonic()

        self._publisher.publish(ctx, request, write)

    def _free_bytes(self) -> int:
        stat = os.statvfs(self._data_root)
        return stat.f_bavail * stat.f_frsize
