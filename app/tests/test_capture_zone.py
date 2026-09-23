"""撮影地のタイムゾーンの上書き.

**瞬間は変えない。** 時計を JST のまま旅行先で撮ったファイルを、撮影地の
壁時計で見せるように付け替えるだけ。
"""

import pytest

from mediaferry.db.capture_zone import ZoneOverrideBusy, ZoneOverrideInvalid, apply_zone_override

from .test_recompute import a_sent_record, a_user_profile
from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_jobs import a_job
from .test_schema_uploads import a_destination

TOKYO = "Asia/Tokyo"
VIETNAM = "Asia/Ho_Chi_Minh"


@pytest.fixture
def dji(db):
    ref = a_user_profile(db, "dji-osmo", "my-dji", timezone=TOKYO)
    return (ref.profile_id, ref.revision_id)


def a_jst_video(db, profile, at="2026-09-22T20:16:31+09:00", **over):
    over.setdefault("captured_at_tz", TOKYO)
    return a_media_file(db, profile, captured_at=at, **over)


HELD = ["checking", "uploading", "asset_known", "tagging", "fixing_datetime"]


def hold(db, record, state):
    """送信ジョブが記録を掴んだ形にする（掴んだ状態は `claim_job_id` を要する）."""
    job = a_job(db, type="upload", status="running")
    db.execute(
        "UPDATE upload_record SET state = ?, claim_job_id = ?, claim_token = 't',"
        " claim_expires_at = '2999-01-01T00:00:00+00:00' WHERE id = ?",
        (state, job, record),
    )


def captured(db, media_id):
    row = db.execute(
        "SELECT captured_at, captured_at_tz, captured_at_zone_override"
        " FROM media_file WHERE id = ?",
        (media_id,),
    ).fetchone()
    return tuple(row)


def a_merged(db, profile, members, at="2026-09-22T19:00:00+09:00"):
    """`members` を先頭から並べた、結合済みのグループとその出力."""
    output = a_jst_video(db, profile, at=at, role="derived")
    group = a_merge_group(db, profile, digest="d", status="merged", output_media_file_id=output)
    for position, member in enumerate(members):
        db.execute("INSERT INTO merge_member VALUES (?, ?, ?, 1)", (group, member, position))
    return output


def test_overriding_moves_the_wall_clock_and_keeps_the_instant(db, dji):
    video = a_jst_video(db, dji)
    outcome = apply_zone_override(db, [video], VIETNAM, TOKYO)
    assert captured(db, video) == ("2026-09-22T18:16:31+07:00", VIETNAM, VIETNAM)
    assert (outcome.changed, outcome.unchanged) == (1, 0)


def test_clearing_returns_to_the_camera_zone(db, dji):
    """戻す先はプロファイルが決めたカメラの時計. 既定のゾーンより先に見る."""
    video = a_jst_video(db, dji)
    apply_zone_override(db, [video], VIETNAM, "UTC")
    apply_zone_override(db, [video], None, "UTC")
    assert captured(db, video) == ("2026-09-22T20:16:31+09:00", TOKYO, None)


def test_clearing_falls_back_to_the_default_zone(db):
    """プロファイルが timezone を持たなければ、カメラの時計は既定のゾーン."""
    ref = a_user_profile(db, "dji-osmo", "no-zone-dji")
    profile = (ref.profile_id, ref.revision_id)
    video = a_jst_video(
        db,
        profile,
        at="2026-09-22T18:16:31+07:00",
        captured_at_tz=VIETNAM,
        captured_at_zone_override=VIETNAM,
    )
    apply_zone_override(db, [video], None, TOKYO)
    assert captured(db, video) == ("2026-09-22T20:16:31+09:00", TOKYO, None)


def test_applying_the_same_zone_twice_changes_nothing(db, dji):
    video = a_jst_video(db, dji)
    apply_zone_override(db, [video], VIETNAM, TOKYO)
    outcome = apply_zone_override(db, [video], VIETNAM, TOKYO)
    assert (outcome.changed, outcome.unchanged) == (0, 1)


def test_recording_the_camera_zone_itself_is_a_change(db, dji):
    """値が動かなくても、利用者が撮影地を決めたことは記録する."""
    video = a_jst_video(db, dji)
    outcome = apply_zone_override(db, [video], TOKYO, TOKYO)
    assert captured(db, video) == ("2026-09-22T20:16:31+09:00", TOKYO, TOKYO)
    assert outcome.changed == 1


def test_changed_rows_are_requeued_and_unchanged_are_not(db, dji):
    destination = a_destination(db)
    moved = a_jst_video(db, dji)
    already = a_jst_video(
        db,
        dji,
        at="2026-09-22T18:00:00+07:00",
        captured_at_tz=VIETNAM,
        captured_at_zone_override=VIETNAM,
    )
    moved_upload = a_sent_record(db, moved, destination)
    already_upload = a_sent_record(db, already, destination)

    outcome = apply_zone_override(db, [moved, already], VIETNAM, TOKYO)

    states = dict(db.execute("SELECT id, state FROM upload_record").fetchall())
    assert states[moved_upload] == "needs_recheck"
    assert states[already_upload] == "complete"
    assert outcome.requeued == 1


def test_recording_without_moving_does_not_requeue(db, dji):
    """撮影地を記録しただけで値が動かなければ、相手に書いた日時との差は無い."""
    destination = a_destination(db)
    video = a_jst_video(db, dji)
    record = a_sent_record(db, video, destination)

    outcome = apply_zone_override(db, [video], TOKYO, TOKYO)

    row = db.execute("SELECT state FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row[0] == "complete"
    assert outcome.requeued == 0


def test_a_changed_row_reopens_a_skipped_stack(db, dji):
    destination = a_destination(db)
    video = a_jst_video(db, dji)
    record = a_sent_record(db, video, destination)
    db.execute(
        "UPDATE upload_record SET stack_state = 'skipped', stack_reason = 'x' WHERE id = ?",
        (record,),
    )
    apply_zone_override(db, [video], VIETNAM, TOKYO)
    row = db.execute("SELECT stack_state FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row[0] is None


def test_a_derived_id_spreads_to_its_active_members(db, dji):
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    second = a_jst_video(db, dji, at="2026-09-22T19:10:00+09:00")
    output = a_merged(db, dji, [first, second])

    apply_zone_override(db, [output], VIETNAM, TOKYO)

    assert captured(db, first)[1:] == (VIETNAM, VIETNAM)
    assert captured(db, second)[1:] == (VIETNAM, VIETNAM)
    # 結合した動画は先頭から継ぐ. 自分では上書きを持たない。
    assert captured(db, output) == ("2026-09-22T17:00:00+07:00", VIETNAM, None)


def test_changing_the_first_member_carries_to_the_output(db, dji):
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    output = a_merged(db, dji, [first])

    apply_zone_override(db, [first], VIETNAM, TOKYO)

    assert captured(db, output)[:2] == ("2026-09-22T17:00:00+07:00", VIETNAM)


def test_changing_a_later_member_leaves_the_output(db, dji):
    """結合した動画が継ぐのは先頭だけ."""
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    second = a_jst_video(db, dji, at="2026-09-22T19:10:00+09:00")
    output = a_merged(db, dji, [first, second])

    apply_zone_override(db, [second], VIETNAM, TOKYO)

    assert captured(db, output)[:2] == ("2026-09-22T19:00:00+09:00", TOKYO)


def test_an_output_whose_first_member_stays_is_not_requeued(db, dji):
    destination = a_destination(db)
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    second = a_jst_video(db, dji, at="2026-09-22T19:10:00+09:00")
    output = a_merged(db, dji, [first, second])
    record = a_sent_record(db, output, destination)

    apply_zone_override(db, [second], VIETNAM, TOKYO)

    row = db.execute("SELECT state FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row[0] == "complete"


def test_a_changed_output_is_requeued(db, dji):
    destination = a_destination(db)
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    output = a_merged(db, dji, [first])
    record = a_sent_record(db, output, destination)

    outcome = apply_zone_override(db, [first], VIETNAM, TOKYO)

    row = db.execute("SELECT state FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row[0] == "needs_recheck"
    assert outcome.requeued == 1


def test_a_none_policy_row_rejects_the_whole_request(db, dji):
    video = a_jst_video(db, dji)
    generic = a_user_profile(db, "generic-dcim", "my-generic")  # timezone_policy: none
    plain = a_media_file(
        db, (generic.profile_id, generic.revision_id), captured_at="2026-09-22T20:16:31+00:00"
    )
    with pytest.raises(ZoneOverrideInvalid):
        apply_zone_override(db, [video, plain], VIETNAM, TOKYO)
    assert captured(db, video) == ("2026-09-22T20:16:31+09:00", TOKYO, None)


@pytest.mark.parametrize("zone", ["Mars/Olympus", "../etc/passwd", ""])
def test_an_unknown_zone_is_rejected(db, dji, zone):
    with pytest.raises(ZoneOverrideInvalid):
        apply_zone_override(db, [a_jst_video(db, dji)], zone, TOKYO)


def test_an_unknown_id_is_rejected(db, dji):
    video = a_jst_video(db, dji)
    with pytest.raises(ZoneOverrideInvalid):
        apply_zone_override(db, [video, "nope"], VIETNAM, TOKYO)
    assert captured(db, video)[2] is None


def test_no_ids_is_rejected(db):
    with pytest.raises(ZoneOverrideInvalid):
        apply_zone_override(db, [], VIETNAM, TOKYO)


@pytest.mark.parametrize(
    "state", ["checking", "uploading", "asset_known", "tagging", "fixing_datetime"]
)
def test_a_record_held_by_an_upload_refuses_the_request(db, dji, state):
    """送信ジョブが掴んでいる記録は、読んだ日時をこの後 Immich へ書く.

    付け替えを通すと、古い日時のまま `complete` で終わり、誰も送り直さない。
    """
    destination = a_destination(db)
    video = a_jst_video(db, dji)
    hold(db, a_sent_record(db, video, destination), state)
    with pytest.raises(ZoneOverrideBusy):
        apply_zone_override(db, [video], VIETNAM, TOKYO)
    assert captured(db, video)[2] is None


@pytest.mark.parametrize("state", ["pending", "needs_recheck", "awaiting_datetime_approval"])
def test_a_record_nobody_holds_does_not_block(db, dji, state):
    """誰も掴んでいない記録は、送るときに今の日時を読み直す."""
    destination = a_destination(db)
    video = a_jst_video(db, dji)
    record = a_sent_record(db, video, destination)
    db.execute("UPDATE upload_record SET state = ? WHERE id = ?", (state, record))
    apply_zone_override(db, [video], VIETNAM, TOKYO)
    assert captured(db, video)[2] == VIETNAM


def test_an_output_held_by_an_upload_refuses_the_request(db, dji):
    destination = a_destination(db)
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    output = a_merged(db, dji, [first])
    hold(db, a_sent_record(db, output, destination), "uploading")
    with pytest.raises(ZoneOverrideBusy):
        apply_zone_override(db, [first], VIETNAM, TOKYO)
    assert captured(db, first)[2] is None


def test_a_member_being_merged_refuses_the_request(db, dji):
    """つないでいる最中の出力は、読み終えた先頭の日時で公開される."""
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    group = a_merge_group(db, dji, digest="d", status="merging")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, first))
    with pytest.raises(ZoneOverrideBusy):
        apply_zone_override(db, [first], VIETNAM, TOKYO)
    assert captured(db, first)[2] is None


def test_the_api_answers_409_while_busy(client, db, dji):
    destination = a_destination(db)
    video = a_jst_video(db, dji)
    hold(db, a_sent_record(db, video, destination), "uploading")
    response = client.post("/api/media/timezone", json={"ids": [video], "timezone": VIETNAM})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_the_api_applies_and_reports(client, db, dji):
    video = a_jst_video(db, dji)
    body = client.post("/api/media/timezone", json={"ids": [video], "timezone": VIETNAM})
    assert body.status_code == 200
    assert body.json() == {"changed": 1, "unchanged": 0, "requeued": 0}
    detail = client.get(f"/api/media/{video}").json()
    assert detail["captured_at_zone_override"] == VIETNAM


def test_the_list_carries_the_override(client, db, dji):
    video = a_jst_video(db, dji)
    client.post("/api/media/timezone", json={"ids": [video], "timezone": VIETNAM})
    rows = client.get("/api/media").json()["media"]
    assert next(m for m in rows if m["id"] == video)["captured_at_zone_override"] == VIETNAM


@pytest.mark.parametrize(
    "body",
    [
        {"ids": ["x"], "timezone": "Mars/X"},
        {"ids": "x", "timezone": VIETNAM},
        {"ids": [1], "timezone": VIETNAM},
        {"ids": ["x"], "timezone": 7},
        {"timezone": VIETNAM},
    ],
)
def test_the_api_rejects_with_400(client, db, dji, body):
    if body.get("ids") == ["x"]:
        body = {**body, "ids": [a_jst_video(db, dji)]}
    response = client.post("/api/media/timezone", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_the_zone_list_contains_iana_names(client):
    zones = client.get("/api/timezones").json()["timezones"]
    assert VIETNAM in zones
    assert zones == sorted(zones)
