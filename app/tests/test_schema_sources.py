import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.core.profiles.model import (
    STACK_DISABLED,
    Hints,
    ImmichRule,
    KeepStreams,
    MergeRule,
    ProfileDefinition,
    Require,
    ScanRule,
    TimestampRule,
    definition_to_json,
)
from mediaferry.ids import new_id

# **中身を気にしないテストのための、パース可能な最小の定義。** `{}` だと
# `parse_definition` が `slug` を要求して落ちる —— `GET /media/{id}` は
# 1 件ごとに `ProfileRegistry(conn).all()` で **DB の全プロファイル**を読むので
# （`api/routes_media.py` の `_ranks`）、この定義が壊れていると無関係な
# テストの詳細取得までまとめて落ちる。
#
# **`profile_revision` を直に INSERT するテストは、どれもここを使う。** 各所で
# `'{}'` を書くと、その DB に詳細取得を 1 行足した誰かが理由の分からない 500 に
# 当たる（本番は編集の経路が必ず検証済みの JSON を書くので影響を受けない）。
PLACEHOLDER_DEFINITION_JSON = definition_to_json(
    ProfileDefinition(
        slug="placeholder",
        name="Placeholder",
        hints=Hints(usb_ids=(), volume_labels=()),
        require=Require(roots=(), filename_pattern=".*", min_matching_files=0),
        scan=ScanRule(roots=(), extensions=()),
        timestamp=TimestampRule(
            source="mtime",
            pattern=None,
            format=None,
            fallback="mtime",
            timezone_policy="none",
            timezone=None,
        ),
        merge=MergeRule(
            enabled=False,
            tolerance_seconds=0,
            min_part_size_gib=0,
            sequence_pattern="",
            output_name="",
            keep_streams=KeepStreams(video="primary", audio="none", timecode=False, data=False),
        ),
        stack=STACK_DISABLED,
        immich=ImmichRule(tags=(), tag_pre_existing=False, fix_datetime_after_upload=False),
    )
)


def a_profile(db, slug="dji-osmo"):
    profile_id, revision_id = new_id(), new_id()
    db.execute(
        "INSERT INTO device_profile (id, slug, name, builtin, created_at)"
        " VALUES (?, ?, 'DJI Osmo', 1, ?)",
        (profile_id, slug, now_iso()),
    )
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES (?, ?, 1, ?, 1, ?)",
        (revision_id, profile_id, PLACEHOLDER_DEFINITION_JSON, now_iso()),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
        (revision_id, profile_id),
    )
    return profile_id, revision_id


def a_volume(db, profile=None, **over):
    profile_id, revision_id = profile or (None, None)
    row = {
        "id": new_id(),
        "fs_uuid": "26B1-2FD6",
        "fs_type": "exfat",
        "fs_label": "SD_Card",
        "size_bytes": 512_000_000_000,
        "identity_confidence": "high",
        "profile_id": profile_id,
        "profile_revision_id": revision_id,
        "first_seen_at": now_iso(),
        "last_seen_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO volume_instance ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def a_presence(db, volume_id, **over):
    row = {
        "id": new_id(),
        "volume_instance_id": volume_id,
        "broker_epoch": "e1",
        "generation": 1,
        "device_node": "/dev/sdk",
        "major": 8,
        "minor": 160,
        "sysfs_path": "/sys/x",
        "attached_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO volume_presence ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_profile_slug_is_unique(db):
    a_profile(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_profile(db)


def test_profile_revision_is_immutable(db):
    _, revision_id = a_profile(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE profile_revision SET definition_json = '{\"x\":1}' WHERE id = ?",
            (revision_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM profile_revision WHERE id = ?", (revision_id,))


def test_current_revision_must_belong_to_the_same_profile(db):
    first, _ = a_profile(db, slug="a")
    _, other_revision = a_profile(db, slug="b")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
            (other_revision, first),
        )


def test_source_device_identity_is_the_whole_tuple(db):
    """serial は Linux ガジェットの既定値 (123456789ABCDEF) でありうるので、
    単独では識別子にならない。product まで含めた組で一意にする."""
    base = ("2ca3", "0020", "OsmoPocket4-AAA", "123456789ABCDEF", now_iso(), now_iso())
    db.execute(
        "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), *base),
    )
    # 別の機体は product が違うので入る
    db.execute(
        "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
        " first_seen_at, last_seen_at) VALUES (?, '2ca3', '0020', 'OsmoPocket4-BBB',"
        " '123456789ABCDEF', ?, ?)",
        (new_id(), now_iso(), now_iso()),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id(), *base),
        )


def test_volume_identity_confidence_is_constrained(db):
    with pytest.raises(sqlite3.IntegrityError):
        a_volume(db, identity_confidence="probably")


def test_volume_identity_is_unique_only_when_the_uuid_is_known(db):
    a_volume(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_volume(db)
    # UUID が空のカードは同定できないので、同じ形でも別行として残す
    a_volume(db, fs_uuid="")
    a_volume(db, fs_uuid="")


def test_volume_profile_revision_must_belong_to_the_profile(db):
    first = a_profile(db, slug="a")
    _, other_revision = a_profile(db, slug="b")
    with pytest.raises(sqlite3.IntegrityError):
        a_volume(db, profile=(first[0], other_revision))


def test_source_entry_is_unique_per_volume_and_path(db):
    volume_id = a_volume(db)
    row = (new_id(), volume_id, "DCIM/DJI_001/A.MP4", 10, 1, "abc", 1, "seen", now_iso())
    sql = (
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    db.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, (new_id(), *row[1:]))


def test_presence_rows_survive_independently_of_the_device_node(db):
    """ジョブは device_node ではなく presence.id と generation を持つ."""
    volume_id = a_volume(db)
    for generation in (1, 2):
        db.execute(
            "INSERT INTO volume_presence (id, volume_instance_id, broker_epoch, generation,"
            " device_node, major, minor, sysfs_path, attached_at)"
            " VALUES (?, ?, 'e1', ?, '/dev/sdk', 8, 160, '/sys/x', ?)",
            (new_id(), volume_id, generation, now_iso()),
        )
    assert db.execute("SELECT count(*) FROM volume_presence").fetchone()[0] == 2


def test_a_presence_records_when_auto_import_was_enqueued(db):
    """自動取り込みの印は接続（presence）に付ける.

    volume_instance に付けると、一度取り込んだカードは二度と自動取り込みされ
    ない。watcher のメモリに持つと、プロセスが落ちるたびに二重に積む。
    """
    profile = a_profile(db)
    volume = a_volume(db, profile=profile)
    presence = a_presence(db, volume)
    assert (
        db.execute(
            "SELECT auto_import_at FROM volume_presence WHERE id = ?", (presence,)
        ).fetchone()["auto_import_at"]
        is None
    )
    db.execute("UPDATE volume_presence SET auto_import_at = ? WHERE id = ?", (now_iso(), presence))
    assert (
        db.execute(
            "SELECT auto_import_at FROM volume_presence WHERE id = ?", (presence,)
        ).fetchone()["auto_import_at"]
        is not None
    )


def test_a_volume_records_whether_the_match_was_provisional(db):
    """暫定マッチかどうかを DB に残す.

    watcher は「積んでよいか」を毎 tick DB の現在値から組み直す。判定材料が
    1 つでも VolumeView にしか無いと、その組み直しが成立しない。
    """
    profile = a_profile(db)
    volume = a_volume(db, profile=profile)
    assert (
        db.execute("SELECT provisional FROM volume_instance WHERE id = ?", (volume,)).fetchone()[
            "provisional"
        ]
        == 0
    )
    db.execute("UPDATE volume_instance SET provisional = 1 WHERE id = ?", (volume,))
    assert (
        db.execute("SELECT provisional FROM volume_instance WHERE id = ?", (volume,)).fetchone()[
            "provisional"
        ]
        == 1
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE volume_instance SET provisional = 2 WHERE id = ?", (volume,))


def test_source_entry_carries_the_copresence_of_a_stack(db):
    """**同席の印は「どのスキャンで、どの stem の下で同時に見えたか」.**

    スキャンの id だけだと、1 回のスキャンが書いた**別々の組が同じ印**になる。
    """
    columns = {r["name"] for r in db.execute("PRAGMA table_info(source_entry)")}
    assert {"copresent_key", "extension"} <= columns


def test_existing_rows_have_no_copresence(db):
    """**無いものを在ったことにしない。** 過去に同席したかは記録に無い."""
    row = db.execute("PRAGMA table_info(source_entry)").fetchall()
    by_name = {r["name"]: r for r in row}
    assert by_name["copresent_key"]["dflt_value"] is None
    assert by_name["copresent_key"]["notnull"] == 0
