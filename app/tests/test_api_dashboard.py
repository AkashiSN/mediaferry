"""ホームの「やること」の材料（§13）.

**画面ごとに数えさせない。** 3 つの数を 1 回で返す。
"""

from __future__ import annotations

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import a_profile
from .test_schema_uploads import a_destination, an_upload


def test_merge_candidates_counts_only_what_can_be_acted_on(client, db):
    """**操作できるものだけ数える。** merged と skipped と supersede 済みは出ない."""
    profile = a_profile(db, slug="dash-merge")
    a_merge_group(db, profile, "d-detected", status="detected")
    a_merge_group(db, profile, "d-failed", status="failed")
    a_merge_group(db, profile, "d-merged", status="merged")
    a_merge_group(db, profile, "d-skipped", status="skipped")
    assert client.get("/api/dashboard").json()["merge_candidates"] == 2


def test_merge_candidates_ignores_superseded_groups(client, db):
    profile = a_profile(db, slug="dash-superseded")
    newer = a_merge_group(db, profile, "d-newer", status="detected")
    older = a_merge_group(db, profile, "d-older", status="detected")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, older))
    assert client.get("/api/dashboard").json()["merge_candidates"] == 1


def test_unsent_total_does_not_double_count_across_destinations(client, db):
    """**和を取らない。** 2 つの宛先に未送信の 1 件は 1 件."""
    profile = a_profile(db, slug="dash-unsent")
    a_media_file(db, profile, rel_path="library/dash/A.JPG")
    a_destination(db, name="one")
    a_destination(db, name="two")
    assert client.get("/api/dashboard").json()["unsent_total"] == 1


def test_unsent_total_ignores_disabled_destinations(client, db):
    """**休止中の宛先しか残っていなければ、送るやることは無い**（送り先が選べない）."""
    profile = a_profile(db, slug="dash-disabled")
    a_media_file(db, profile, rel_path="library/dash/B.JPG")
    destination_id, _, _ = a_destination(db, name="paused")
    db.execute("UPDATE upload_destination SET enabled = 0 WHERE id = ?", (destination_id,))
    assert client.get("/api/dashboard").json()["unsent_total"] == 0


def test_unsent_total_counts_a_file_sent_to_only_one_of_two(client, db):
    profile = a_profile(db, slug="dash-partial")
    media = a_media_file(db, profile, rel_path="library/dash/C.JPG")
    first = a_destination(db, name="first")
    a_destination(db, name="second")
    an_upload(
        db,
        first,
        media,
        state="complete",
        destination_revision_id=first[1],
        remote_asset_id="asset-1",
    )
    assert client.get("/api/dashboard").json()["unsent_total"] == 1


def test_awaiting_total_sums_across_destinations(client, db):
    profile = a_profile(db, slug="dash-awaiting")
    media = a_media_file(db, profile, rel_path="library/dash/D.JPG")
    for name in ("a", "b"):
        an_upload(db, a_destination(db, name=name), media, state="awaiting_datetime_approval")
    assert client.get("/api/dashboard").json()["awaiting_total"] == 2


def test_unsent_total_ignores_archived_destinations(client, db):
    """**アーカイブ済みの宛先しか残っていなければ、送るやることは無い**（§10）."""
    profile = a_profile(db, slug="dash-archived")
    a_media_file(db, profile, rel_path="library/dash/E.JPG")
    destination_id, _, _ = a_destination(db, name="archived")
    db.execute(
        "UPDATE upload_destination SET archived_at = ? WHERE id = ?",
        ("2026-08-18T00:00:00+00:00", destination_id),
    )
    assert client.get("/api/dashboard").json()["unsent_total"] == 0


def test_unsent_total_does_not_treat_an_invalidated_record_as_sent(client, db):
    """無効化された記録は「送った」ではない（§10）."""
    profile = a_profile(db, slug="dash-invalidated")
    media = a_media_file(db, profile, rel_path="library/dash/F.JPG")
    destination = a_destination(db, name="invalidated-dest")
    an_upload(
        db,
        destination,
        media,
        state="complete",
        destination_revision_id=destination[1],
        remote_asset_id="asset-2",
        invalidated_at="2026-08-18T00:00:00+00:00",
        invalidated_reason="宛先を編集した",
    )
    assert client.get("/api/dashboard").json()["unsent_total"] == 1


def test_unsent_total_uses_the_shared_sendable_definition(client, db):
    """**選択肢に出ないものは、送るやることにも数えない**（`SENDABLE_CLAUSE` を使う）.

    結合グループの構成ファイル（active な merge_member）は §10 で選択肢から
    外れる。宛先が未送信でも「送る」ボタンは押せないので数えない。
    """
    profile = a_profile(db, slug="dash-sendable")
    media = a_media_file(db, profile, rel_path="library/dash/G.JPG")
    group = a_merge_group(db, profile, "d-sendable")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, media))
    a_destination(db, name="only")
    assert client.get("/api/dashboard").json()["unsent_total"] == 0


def test_awaiting_total_ignores_invalidated_records(client, db):
    """無効化された記録は承認待ちに数えない（§10）."""
    profile = a_profile(db, slug="dash-awaiting-invalid")
    media = a_media_file(db, profile, rel_path="library/dash/H.JPG")
    an_upload(
        db,
        a_destination(db, name="invalidated-awaiting"),
        media,
        state="awaiting_datetime_approval",
        invalidated_at="2026-08-18T00:00:00+00:00",
        invalidated_reason="宛先を編集した",
    )
    assert client.get("/api/dashboard").json()["awaiting_total"] == 0
