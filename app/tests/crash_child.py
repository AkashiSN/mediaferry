"""公開プロトコルの途中で本当にプロセスを落とす子プロセス.

`os._exit` を使うのは、例外だと `finally` と atexit が走ってしまい、
「電源が落ちた」状況にならないため。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from mediaferry.adapters.ffprobe import ProbeResult
from mediaferry.adapters.publisher import ArtifactPublisher, ArtifactRequest
from mediaferry.core.timestamps import CapturedAt
from mediaferry.db.connection import Database
from mediaferry.db.jobs import JobStore
from mediaferry.db.migrate import apply_migrations
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.ids import new_id

PAYLOAD = b"payload-for-crash-tests"


class _Probe:
    def describe(self, path, extension):  # noqa: ANN001, ANN201
        return ProbeResult("video", 2.0, "ok")


class CrashingPublisher(ArtifactPublisher):
    def __init__(self, *args, die_after: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._die_after = die_after

    def _checkpoint(self, step: int) -> None:
        if step == self._die_after:
            os._exit(9)  # noqa: SLF001


def main() -> None:
    data_root = Path(sys.argv[1])
    die_after = int(sys.argv[2])
    kind = sys.argv[3]

    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    apply_migrations(conn)
    registry = ProfileRegistry(conn)
    registry.sync_builtins()
    profile = registry.current("dji-osmo")

    store = JobStore(conn)
    store.enqueue(kind if kind == "merge" else "import", {})
    ctx = store.claim_next()

    source_entry_id = merge_group_id = None
    if kind == "import":
        volume_id, source_entry_id = _a_source(conn, profile)
    else:
        merge_group_id = _a_merge_group(conn, profile)

    publisher = CrashingPublisher(conn, data_root, _Probe(), die_after=die_after)
    request = ArtifactRequest(
        kind=kind,
        role="original" if kind == "import" else "derived",
        profile_id=profile.profile_id,
        profile_revision_id=profile.revision_id,
        desired_rel_path=(
            "library/dji-osmo/DCIM/A.MP4"
            if kind == "import"
            else "derived/dji-osmo/DCIM/MERGED.MP4"
        ),
        source_rel_path="DCIM/A.MP4",
        extension="MP4",
        captured=CapturedAt(
            at=datetime.fromisoformat("2026-08-17T14:30:00+09:00"),
            source="filename",
            tz="Asia/Tokyo",
            note=None,
        ),
        mtime_ns=1_700_000_000_000_000_000,
        source_entry_id=source_entry_id,
        merge_group_id=merge_group_id,
    )
    publisher.publish(ctx, request, lambda writer: writer.write(PAYLOAD))
    # ここへ来るのは die_after が 11 より大きいときだけ。
    sys.exit(0)


def _a_source(conn, profile):  # noqa: ANN001, ANN202
    from mediaferry.clock import now_iso

    volume_id, entry_id = new_id(), new_id()
    conn.execute(
        "INSERT INTO volume_instance (id, fs_uuid, fs_type, fs_label, size_bytes,"
        " identity_confidence, profile_id, profile_revision_id, first_seen_at, last_seen_at)"
        " VALUES (?, '26B1-2FD6', 'exfat', 'SD_Card', 1, 'high', ?, ?, ?, ?)",
        (volume_id, profile.profile_id, profile.revision_id, now_iso(), now_iso()),
    )
    conn.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES (?, ?, 'DCIM/A.MP4', ?, 1, 'abc', 1, 'importing', ?)",
        (entry_id, volume_id, len(PAYLOAD), now_iso()),
    )
    return volume_id, entry_id


def _a_merge_group(conn, profile):  # noqa: ANN001, ANN202
    from mediaferry.clock import now_iso

    group_id = new_id()
    conn.execute(
        "INSERT INTO merge_group (id, profile_id, profile_revision_id, status, input_digest,"
        " detected_by, created_at, updated_at)"
        " VALUES (?, ?, ?, 'merging', 'digest-1', 'auto', ?, ?)",
        (group_id, profile.profile_id, profile.revision_id, now_iso(), now_iso()),
    )
    return group_id


if __name__ == "__main__":
    main()
