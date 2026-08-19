"""公開プロトコルの途中で本当にプロセスを落とす子プロセス.

`os._exit` を使うのは、例外だと `finally` と atexit が走ってしまい、
「電源が落ちた」状況にならないため。
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from mediaferry.adapters.ffprobe import ProbeResult
from mediaferry.adapters.publisher import ArtifactPublisher, ArtifactRequest
from mediaferry.core.timestamps import CapturedAt
from mediaferry.db.connection import Database
from mediaferry.db.jobs import JobStore
from mediaferry.db.migrate import apply_migrations
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.ids import new_id

# このモジュールは 2 通りで読まれる —— 子プロセスとして直接起動されるときと、
# test_crash_consistency.py が PAYLOAD を取るためにパッケージとして import する
# とき。どちらでも同じ土台を使えるようにする。
try:
    from .exif_fixtures import a_jpeg_with
except ImportError:  # pragma: no cover - 素のスクリプトとして起動されたとき
    from exif_fixtures import a_jpeg_with

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


def _resolve_from_exif(staging_abs):  # noqa: ANN001, ANN202
    """ステージ済みのファイルから決める（§9.3 手順 5）."""
    from mediaferry.adapters.exif import read_datetime_original

    wall = read_datetime_original(staging_abs)
    return CapturedAt(at=wall.replace(tzinfo=UTC), source="exif", tz=None, note=None)


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
    store.enqueue("import" if kind == "import" else "merge", {})
    ctx = store.claim_next()

    source_entry_id = merge_group_id = None
    if kind in ("import", "import_exif"):
        volume_id, source_entry_id = _a_source(conn, profile)
    else:
        merge_group_id = _a_merge_group(conn, profile)

    publisher = CrashingPublisher(conn, data_root, _Probe(), die_after=die_after)
    # **遅延解決（source: exif）も 11 段すべてで回収できること。** 解決は
    # 手順 4 の後・手順 5 の中で完結するので、手順 7 の commit より前に載る。
    exif = kind == "import_exif"
    request = ArtifactRequest(
        kind="merge" if kind.startswith("merge") else "import",
        role="derived" if kind.startswith("merge") else "original",
        profile_id=profile.profile_id,
        profile_revision_id=profile.revision_id,
        desired_rel_path=(
            "derived/dji-osmo/DCIM/MERGED.MP4"
            if kind.startswith("merge")
            else ("library/dji-osmo/DCIM/A.JPG" if exif else "library/dji-osmo/DCIM/A.MP4")
        ),
        source_rel_path="DCIM/A.JPG" if exif else "DCIM/A.MP4",
        extension="JPG" if exif else "MP4",
        captured=None
        if exif
        else CapturedAt(
            at=datetime.fromisoformat("2026-08-17T14:30:00+09:00"),
            source="filename",
            tz="Asia/Tokyo",
            note=None,
        ),
        mtime_ns=1_700_000_000_000_000_000,
        source_entry_id=source_entry_id,
        merge_group_id=merge_group_id,
        resolve_captured=_resolve_from_exif if exif else None,
    )
    payload = a_jpeg_with(b"2026:03:04 05:06:07") if exif else PAYLOAD
    if kind == "merge_prepared":
        work = data_root / "work" / ctx.job_id
        work.mkdir(parents=True, exist_ok=True)
        prepared = work / "MERGED.MP4"
        prepared.write_bytes(PAYLOAD)
        publisher.publish_prepared(ctx, request, prepared)
    else:
        publisher.publish(ctx, request, lambda writer: writer.write(payload))
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
    # verification_json も埋める。_settle_merges が merged へ倒すときの前提
    # （mark_merged の CAS が検証結果を要求する）を crash 試験でも満たす。
    conn.execute(
        "INSERT INTO merge_group (id, profile_id, profile_revision_id, status, input_digest,"
        " detected_by, verification_json, tool_version, created_at, updated_at)"
        " VALUES (?, ?, ?, 'merging', 'digest-1', 'auto', '{\"passed\": true}', 'ffmpeg', ?, ?)",
        (group_id, profile.profile_id, profile.revision_id, now_iso(), now_iso()),
    )
    return group_id


if __name__ == "__main__":
    main()
