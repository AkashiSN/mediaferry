"""ソースボリュームのスキャン（§9.5）.

dirfd 起点で scan.roots 配下を列挙し、既知の source_entry と照合する。
この段階でフル SHA-1 は計算しない（16GiB を読む必要があるため）。
同一性の判定には quick_fingerprint を使う。

**最後まで見たら `volume_instance.scanned_at` に印を付ける。中身が空でも付ける**
——「数えたか」は行の有無からは分からない（§11）。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from ..adapters.fs import iter_media_files, open_beneath
from ..clock import now_iso
from ..core.fingerprint import FINGERPRINT_VERSION, quick_fingerprint
from ..db.jobs import JobContext
from ..db.profiles import ProfileRef
from ..db.sources import mark_scanned
from ..ids import new_id


@dataclass(frozen=True)
class ScanOutcome:
    total: int
    new: int
    already_imported: int
    ambiguous: int


class Scanner:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def scan(
        self, ctx: JobContext, dirfd: int, volume_instance_id: str, profile: ProfileRef
    ) -> ScanOutcome:
        defn = profile.definition
        total = new = imported = ambiguous = 0
        counted = True
        for found in iter_media_files(dirfd, defn.scan.roots, defn.scan.extensions):
            if ctx.cancelled():
                counted = False
                break
            total += 1
            ctx.heartbeat()
            fingerprint = self._fingerprint(dirfd, found.rel_path, found.size_bytes)
            verdict = self._reconcile_entry(volume_instance_id, found, fingerprint)
            if verdict == "imported":
                imported += 1
            elif verdict == "ambiguous":
                ambiguous += 1
            else:
                new += 1
            ctx.emit("info", f"{found.rel_path}: {verdict}", {"size_bytes": found.size_bytes})
        if counted:
            # **最後まで見たときだけ「数えた」と書く。** 途中で降りたスキャンを
            # 数え終わったことにすると、1 件も見ていないカードに画面が
            # 「取り込むものはありません。」と書く。
            mark_scanned(self._conn, volume_instance_id)
        return ScanOutcome(total=total, new=new, already_imported=imported, ambiguous=ambiguous)

    def _fingerprint(self, dirfd: int, rel_path: str, size: int) -> str:
        fd = open_beneath(dirfd, rel_path)
        with os.fdopen(fd, "rb") as fileobj:
            return quick_fingerprint(fileobj, size)

    def _reconcile_entry(self, volume_instance_id: str, found, fingerprint: str) -> str:  # noqa: ANN001
        row = self._conn.execute(
            "SELECT * FROM source_entry WHERE volume_instance_id = ? AND rel_path = ?",
            (volume_instance_id, found.rel_path),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
                " quick_fingerprint, fingerprint_version, state, observed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'seen', ?)",
                (
                    new_id(),
                    volume_instance_id,
                    found.rel_path,
                    found.size_bytes,
                    found.mtime_ns,
                    fingerprint,
                    FINGERPRINT_VERSION,
                    now_iso(),
                ),
            )
            return "new"

        same = (
            row["size_bytes"] == found.size_bytes
            and row["quick_fingerprint"] == fingerprint
            and row["fingerprint_version"] == FINGERPRINT_VERSION
        )
        if same and row["mtime_ns"] == found.mtime_ns and row["state"] == "published":
            self._touch(row["id"])
            return "imported"
        if same and found.mtime_ns < row["mtime_ns"]:
            # 指紋は一致するが mtime が記録より古い。フルハッシュで確認する
            # （deep_verify で扱う。ここでは曖昧として画面に出す）。
            self._touch(row["id"])
            return "ambiguous"
        self._conn.execute(
            "UPDATE source_entry SET size_bytes = ?, mtime_ns = ?, quick_fingerprint = ?,"
            " fingerprint_version = ?, state = 'seen', media_file_id = NULL, observed_at = ?"
            " WHERE id = ?",
            (
                found.size_bytes,
                found.mtime_ns,
                fingerprint,
                FINGERPRINT_VERSION,
                now_iso(),
                row["id"],
            ),
        )
        return "new"

    def _touch(self, entry_id: str) -> None:
        self._conn.execute(
            "UPDATE source_entry SET observed_at = ? WHERE id = ?", (now_iso(), entry_id)
        )
