"""結合グループの手動編集と supersede（Phase 3 の先送り、§13）.

破棄と再結合はどちらも**公開済みの `media_file` を取り残す**。旧グループを
`superseded_by_id` で向け直す仕組みが要り、それは手動編集と共通なので画面と
一緒に入れる、と決めた（`docs/decisions.md`）。
"""

from __future__ import annotations

import sqlite3

import pytest

from mediaferry.db.merges import GroupNotEditable, MergeRepository

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import a_profile
from .test_schema_uploads import a_destination, an_upload


@pytest.fixture
def world(db):
    profile = a_profile(db, slug="supersede-test")
    parts = [
        a_media_file(db, profile, rel_path=f"library/supersede/PART_{index}.MP4")
        for index in range(3)
    ]
    group = a_merge_group(db, profile, "digest-1", status="merged")
    output = a_media_file(db, profile, rel_path="derived/supersede/MERGED.MP4", role="derived")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    for position, media in enumerate(parts):
        db.execute(
            "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
            " VALUES (?, ?, ?, 1)",
            (group, media, position),
        )
    return MergeRepository(db), group, parts, output, profile


def test_a_discarded_group_keeps_what_was_published(db, world):
    """**公開済みの派生物は消さない。** 削除はデータを失う経路（§3）."""
    repo, group, _, output, _ = world

    repo.discard(group)

    row = repo.get(group)
    assert row["status"] == "skipped"
    # 派生物は残る（選択肢から外れるだけ）。
    assert (
        db.execute("SELECT count(*) AS n FROM media_file WHERE id = ?", (output,)).fetchone()["n"]
        == 1
    )


def test_a_regrouped_set_points_the_old_group_at_the_new_one(db, world):
    """再結合は「新しいグループを作って旧を supersede」."""
    repo, group, parts, _, _ = world

    new_group = repo.supersede(group, parts[:2], digest="digest-2")

    old = repo.get(group)
    assert old["superseded_by_id"] == new_group
    assert [row["media_file_id"] for row in repo.members(new_group)] == parts[:2]
    assert repo.get(new_group)["status"] == "detected"
    assert repo.get(new_group)["detected_by"] == "manual"


def test_the_supersede_is_one_transaction(db, world, monkeypatch):
    """**割れると `input_digest` の部分索引が 2 行を許して UNIQUE 違反になる。**

    途中で落ちたときに、新しいグループだけが残らないことを確かめる。
    """
    repo, group, parts, _, _ = world
    real = MergeRepository._invalidate_pending

    def fail_at_the_end(self, group_id, reason):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("途中で落ちた")

    monkeypatch.setattr(MergeRepository, "_invalidate_pending", fail_at_the_end)

    with pytest.raises(sqlite3.OperationalError):
        repo.supersede(group, parts[:2], digest="digest-2")

    monkeypatch.setattr(MergeRepository, "_invalidate_pending", real)
    assert db.execute("SELECT count(*) AS n FROM merge_group").fetchone()["n"] == 1
    assert repo.get(group)["superseded_by_id"] is None


def test_a_group_being_sent_right_now_cannot_be_edited(db, world):
    """**進行中の記録がある間は動かさない**（§10 の根拠が消える）."""
    repo, group, parts, _, _ = world
    destination = a_destination(db, name="supersede-dest")
    _, revision_id, _ = destination
    an_upload(
        db,
        destination,
        parts[0],
        state="uploading",
        destination_revision_id=revision_id,
        claim_job_id=_a_job(db),
        claim_token="t",
        claim_expires_at="2999-01-01T00:00:00+00:00",
    )

    with pytest.raises(GroupNotEditable):
        repo.discard(group)


def test_a_group_with_a_queued_job_cannot_be_edited(db, world):
    """**これから送られる根拠**になっている間も動かさない."""
    repo, group, parts, _, _ = world
    destination = a_destination(db, name="queued-dest")
    destination_id, revision_id, _ = destination
    an_upload(db, destination, parts[0], state="pending", destination_revision_id=revision_id)
    _a_job(db, params={"destination_id": destination_id}, status="queued")

    with pytest.raises(GroupNotEditable):
        repo.discard(group)


def test_a_finished_group_can_still_be_edited(db, world):
    """**一度送ったら二度と直せない、にしない。** 破棄と再結合の目的が消える."""
    repo, group, parts, _, _ = world
    destination = a_destination(db, name="done-dest")
    _, revision_id, _ = destination
    an_upload(
        db,
        destination,
        parts[0],
        state="complete",
        destination_revision_id=revision_id,
        remote_asset_id="asset-1",
    )

    repo.discard(group)

    assert repo.get(group)["status"] == "skipped"


def test_pending_records_are_invalidated_in_the_same_transaction(db, world):
    """**編集と同じ取引で無効化する。**

    残すと、編集の直後に既存のジョブが claim して「根拠が消えた」で無効化され、
    理由の分かりにくい失敗が並ぶ。
    """
    repo, group, parts, _, _ = world
    destination = a_destination(db, name="pending-dest")
    _, revision_id, _ = destination
    record = an_upload(
        db, destination, parts[0], state="pending", destination_revision_id=revision_id
    )

    repo.discard(group)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["invalidated_at"] is not None
    assert "結合" in row["invalidated_reason"]


def _a_job(db, params=None, status="running"):
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    job_id = new_id()
    import json

    db.execute(
        "INSERT INTO job (id, type, status, params_json, created_at) VALUES (?, 'upload', ?, ?, ?)",
        (job_id, status, json.dumps(params or {}), now_iso()),
    )
    return job_id


def test_an_earlier_reason_for_invalidation_is_kept(db, world):
    """**最初の理由と時刻を上書きしない**（監査で読めなくなる。Phase 3 の 4 巡目）."""
    repo, group, parts, _, _ = world
    destination = a_destination(db, name="reason-dest")
    _, revision_id, _ = destination
    record = an_upload(
        db,
        destination,
        parts[0],
        state="pending",
        destination_revision_id=revision_id,
        invalidated_at="2026-08-01T00:00:00+00:00",
        invalidated_reason="宛先を編集した",
    )

    repo.discard(group)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["invalidated_reason"] == "宛先を編集した"
    assert row["invalidated_at"] == "2026-08-01T00:00:00+00:00"


def test_pending_records_are_invalidated_when_the_group_is_regrouped(db, world):
    """**組み直しでも無効化する。**

    `superseded_by_id` を立てた trigger が旧 member を `active = 0` にするので、
    その後で「active な member」を条件に無効化しても 1 件も当たらない（順序の罠）。
    残ると、後続の送信ジョブが古い根拠を claim して、遅れて別の理由で失敗する。
    """
    repo, group, parts, _, _ = world
    destination = a_destination(db, name="regroup-pending")
    _, revision_id, _ = destination
    record = an_upload(
        db, destination, parts[2], state="pending", destination_revision_id=revision_id
    )

    repo.supersede(group, parts[:2], digest="digest-2")

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["invalidated_at"] is not None
    assert "結合" in row["invalidated_reason"]
