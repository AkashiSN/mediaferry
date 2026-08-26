import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.ids import new_id

from .test_schema_jobs import a_job
from .test_schema_sources import PLACEHOLDER_DEFINITION_JSON, a_profile, a_volume


def a_media_file(db, profile, **over):
    profile_id, revision_id = profile
    row = {
        "id": new_id(),
        "role": "original",
        "profile_id": profile_id,
        "profile_revision_id": revision_id,
        "rel_path": f"library/dji-osmo/DCIM/{new_id()}.MP4",
        "size_bytes": 100,
        "mtime_ns": 1,
        "sha1": "0" * 40,
        "kind": "video",
        "captured_at": now_iso(),
        "captured_at_source": "filename",
        # 取り込み時は「取り込みに使った版」と同じ（`0011`）。
        "captured_at_revision_id": revision_id,
        "duration_seconds": 1.5,
        "probe_state": "ok",
        "created_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO media_file ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def a_staging(db, job_id, **over):
    row = {
        "id": new_id(),
        "kind": "import",
        "job_id": job_id,
        "lease_token": "lease-1",
        "state": "writing",
        "staging_rel_path": f"staging/{job_id}/{new_id()}",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO artifact_staging ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def a_source_entry(db, volume_id):
    entry_id = new_id()
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES (?, ?, ?, 10, 1, 'abc', 1, 'seen', ?)",
        (entry_id, volume_id, f"DCIM/{entry_id}.MP4", now_iso()),
    )
    return entry_id


def test_captured_at_revision_must_be_present(db):
    """**必ず値を持つ**（`0011`）.

    `ALTER TABLE` では NOT NULL を後から足せないので trigger で作る。無いと
    「どの定義で算出した日時か」が分からない行ができ、再計算の provenance が
    最初から欠ける。
    """
    profile = a_profile(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, captured_at_revision_id=None)


def test_captured_at_revision_must_belong_to_the_same_profile(db):
    """**同じプロファイルの版であること**（`0011`）.

    単一の FK は `profile_revision` のどの行でも通してしまうので、別機種の版を
    指した行が作れる。`UNIQUE (profile_id, id)` に突き合わせて塞ぐ。
    """
    profile = a_profile(db)
    _, other_revision = a_profile(db, slug="canon-eos")
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, captured_at_revision_id=other_revision)
    media = a_media_file(db, profile)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE media_file SET captured_at_revision_id = ? WHERE id = ?",
            (other_revision, media),
        )


def test_captured_at_revision_can_advance_within_the_same_profile(db):
    """再計算はこの列だけを進める. `profile_revision_id` は触らない."""
    profile_id, revision_id = a_profile(db)
    media = a_media_file(db, (profile_id, revision_id))
    next_revision = new_id()
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES (?, ?, 2, ?, 1, ?)",
        (next_revision, profile_id, PLACEHOLDER_DEFINITION_JSON, now_iso()),
    )
    db.execute(
        "UPDATE media_file SET captured_at_revision_id = ? WHERE id = ?", (next_revision, media)
    )
    row = db.execute(
        "SELECT profile_revision_id, captured_at_revision_id FROM media_file WHERE id = ?", (media,)
    ).fetchone()
    assert row["profile_revision_id"] == revision_id
    assert row["captured_at_revision_id"] == next_revision


def test_media_rel_path_is_unique(db):
    profile = a_profile(db)
    path = "library/dji-osmo/DCIM/A.MP4"
    a_media_file(db, profile, rel_path=path)
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, rel_path=path)


def test_published_video_must_carry_a_duration(db):
    """公開前にメタデータを確定させる（§9.3 手順 5）ので、
    probe に成功した動画が duration 無しで残ることはない."""
    profile = a_profile(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, kind="video", probe_state="ok", duration_seconds=None)
    a_media_file(db, profile, kind="video", probe_state="failed", duration_seconds=None)
    a_media_file(db, profile, kind="photo", probe_state="not_applicable", duration_seconds=None)


def test_probe_state_has_no_not_run(db):
    profile = a_profile(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, probe_state="not_run")


def test_staged_rows_must_carry_everything_needed_to_resume(db):
    """reconciliation はパスを推測しない。staged になった時点で
    final_rel_path / content_sha1 / expected_size / metadata_json が揃う."""
    job_id = a_job(db)
    entry_id = a_source_entry(db, a_volume(db))
    # match で、狙いの CHECK （揃っているか）で落ちたことを確かめる。
    # kind 側の CHECK で落ちると、欠けた列を見逃したまま通ってしまう。
    with pytest.raises(sqlite3.IntegrityError, match="final_rel_path"):
        a_staging(
            db,
            job_id,
            state="staged",
            final_rel_path="library/x/A.MP4",
            source_entry_id=entry_id,
        )
    a_staging(
        db,
        job_id,
        state="staged",
        final_rel_path="library/x/A.MP4",
        content_sha1="0" * 40,
        expected_size=10,
        metadata_json="{}",
        source_entry_id=entry_id,
    )


def test_staging_kind_decides_which_back_reference_is_set(db):
    job_id = a_job(db)
    volume_id = a_volume(db)
    entry_id = a_source_entry(db, volume_id)
    a_staging(db, job_id, kind="import", source_entry_id=entry_id)
    with pytest.raises(sqlite3.IntegrityError):
        a_staging(db, job_id, kind="import")  # 参照元が無い
    with pytest.raises(sqlite3.IntegrityError):
        a_staging(db, job_id, kind="merge", source_entry_id=entry_id)


def test_source_entry_cannot_point_at_a_missing_media_file(db):
    volume_id = a_volume(db)
    entry_id = a_source_entry(db, volume_id)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE source_entry SET media_file_id = ? WHERE id = ?", (new_id(), entry_id))


def test_active_merge_groups_have_distinct_input_digests(db):
    profile = a_profile(db)
    first = a_merge_group(db, profile, digest="d1")
    with pytest.raises(sqlite3.IntegrityError):
        a_merge_group(db, profile, digest="d1")
    # supersede すれば同じ digest の新グループを作れる
    second = a_merge_group(db, profile, digest="d2")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    a_merge_group(db, profile, digest="d1")


def a_merge_group(db, profile, digest, **over):
    profile_id, revision_id = profile
    row = {
        "id": new_id(),
        "profile_id": profile_id,
        "profile_revision_id": revision_id,
        "status": "detected",
        "input_digest": digest,
        "detected_by": "auto",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO merge_group ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_a_media_file_belongs_to_at_most_one_active_group(db):
    profile = a_profile(db)
    media_id = a_media_file(db, profile)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (first, media_id))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (second, media_id))


def test_superseding_a_group_frees_its_members(db):
    profile = a_profile(db)
    media_id = a_media_file(db, profile)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (first, media_id))
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (second, media_id))
    active = db.execute(
        "SELECT active FROM merge_member WHERE merge_group_id = ?", (first,)
    ).fetchone()[0]
    assert active == 0


def test_a_superseded_group_cannot_gain_active_members(db):
    """旧グループの再構成で active member が復活すると、候補の除外が誤る."""
    profile = a_profile(db)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    with pytest.raises(sqlite3.IntegrityError, match="live state"):
        db.execute(
            "INSERT INTO merge_member VALUES (?, ?, 0, 1)", (first, a_media_file(db, profile))
        )
    # 非 active としてなら履歴に残せる
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 0)", (first, a_media_file(db, profile)))
    with pytest.raises(sqlite3.IntegrityError, match="live state"):
        db.execute("UPDATE merge_member SET active = 1 WHERE merge_group_id = ?", (first,))


def test_a_member_cannot_be_moved_into_a_superseded_group(db):
    """active な member の親を付け替えると trigger を迂回できてしまう."""
    profile = a_profile(db)
    media_id = a_media_file(db, profile)
    active_group = a_merge_group(db, profile, digest="d1")
    doomed = a_merge_group(db, profile, digest="d2")
    successor = a_merge_group(db, profile, digest="d3")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (successor, doomed))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (active_group, media_id))
    with pytest.raises(sqlite3.IntegrityError, match="live state"):
        db.execute(
            "UPDATE merge_member SET merge_group_id = ? WHERE merge_group_id = ?",
            (doomed, active_group),
        )


def test_an_active_group_cannot_hold_an_inactive_member(db):
    """active は親の状態の写しなので、片方だけずらせない."""
    profile = a_profile(db)
    group = a_merge_group(db, profile, digest="d1")
    with pytest.raises(sqlite3.IntegrityError, match="live state"):
        db.execute(
            "INSERT INTO merge_member VALUES (?, ?, 0, 0)", (group, a_media_file(db, profile))
        )


def test_supersede_cannot_be_undone(db):
    profile = a_profile(db)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    with pytest.raises(sqlite3.IntegrityError, match="irreversible"):
        db.execute("UPDATE merge_group SET superseded_by_id = NULL WHERE id = ?", (first,))


def test_a_group_cannot_supersede_itself(db):
    profile = a_profile(db)
    group = a_merge_group(db, profile, digest="d1")
    with pytest.raises(sqlite3.IntegrityError, match="itself"):
        db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (group, group))


def test_member_positions_are_unique_within_a_group(db):
    profile = a_profile(db)
    group = a_merge_group(db, profile, digest="d1")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, a_media_file(db, profile)))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, a_media_file(db, profile))
        )


def test_media_file_accepts_the_container_source(db):
    """`container` を出所として保存できる."""
    media_id = a_media_file(
        db,
        a_profile(db),
        captured_at_source="container",
        container_wall="2026-08-26T12:35:08.000000Z",
    )
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    assert row["captured_at_source"] == "container"
    assert row["container_wall"] == "2026-08-26T12:35:08.000000Z"


def test_media_file_still_refuses_an_unknown_source(db):
    """CHECK を広げても、知らない出所は弾く."""
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, a_profile(db), captured_at_source="gps")


def test_the_listing_indexes_break_ties_by_rel_path(db):
    """**同じ撮影日時の並びは、乱数ではなく名前で決まる.**

    索引が `id DESC` で終わっていると、`ORDER BY captured_at DESC, rel_path DESC`
    を索引で満たせず一時 B-tree のソートに落ちる。
    """
    indexes = {
        row["name"]: row["sql"]
        for row in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'media_file'"
        )
        if row["sql"]
    }
    assert "rel_path DESC" in indexes["media_file_listing"]
    assert "rel_path DESC" in indexes["media_file_derived_listing"]
    assert "id DESC" not in indexes["media_file_listing"]
    assert "id DESC" not in indexes["media_file_derived_listing"]


def test_the_captured_revision_triggers_survive_the_rebuild(db):
    """**作り直すと trigger も消える.** `0011` が守っていたものを落とさない."""
    names = {
        row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    assert "media_file_captured_revision_insert" in names
    assert "media_file_captured_revision_update" in names
