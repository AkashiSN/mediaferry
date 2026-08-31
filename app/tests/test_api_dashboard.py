"""ホームの「やること」の材料（§13）.

**画面ごとに数えさせない。** 3 つの数を 1 回で返す。
"""

from __future__ import annotations

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import PLACEHOLDER_DEFINITION_JSON, a_profile
from .test_schema_uploads import a_destination, an_upload


def test_merge_candidates_counts_only_what_can_be_acted_on(client, db):
    """**操作できるものだけ数える。** merged と skipped と supersede 済みは出ない."""
    profile = a_profile(db, slug="dash-merge")
    a_merge_group(db, profile, "d-detected", status="detected")
    a_merge_group(db, profile, "d-failed", status="failed")
    a_merge_group(db, profile, "d-merged", status="merged")
    a_merge_group(db, profile, "d-skipped", status="skipped")
    assert client.get("/api/dashboard").json()["merge_candidates"] == 2


def test_a_merged_group_that_failed_verification_is_its_own_task(client, db):
    """**検証に落ちた結合物は、人が中身を見るまで宙に浮く。**

    送る候補には出ず（`SENDABLE_CLAUSE` は `passed` か `adopted_at` を見る）、
    構成ファイルも active な member なので出ない。`merge_candidates` にも
    入らないと、ホームは「やることはありません」と書く一方で、つなぐ画面には
    「中身を見て、これを使う」が出ている状態になる。
    """
    import json

    profile = a_profile(db, slug="dash-review")
    a_media_file(db, profile, rel_path="library/dash/OUT.MP4", role="derived")
    output = db.execute(
        "SELECT id FROM media_file WHERE rel_path = 'library/dash/OUT.MP4'"
    ).fetchone()["id"]
    a_merge_group(
        db,
        profile,
        "d-review",
        status="merged",
        verification_json=json.dumps({"passed": False}),
        output_media_file_id=output,
    )

    body = client.get("/api/dashboard").json()

    assert body["merge_review_total"] == 1
    # **つなぐ候補には数えない。** つなぐ操作はもう済んでいる。
    assert body["merge_candidates"] == 0


def test_a_merged_group_that_passed_is_not_a_task(client, db):
    """合格した結合物は送る候補に出るので、確かめる仕事は残っていない."""
    import json

    profile = a_profile(db, slug="dash-passed")
    a_media_file(db, profile, rel_path="library/dash/OK.MP4", role="derived")
    output = db.execute(
        "SELECT id FROM media_file WHERE rel_path = 'library/dash/OK.MP4'"
    ).fetchone()["id"]
    a_merge_group(
        db,
        profile,
        "d-passed",
        status="merged",
        verification_json=json.dumps({"passed": True}),
        output_media_file_id=output,
    )

    assert client.get("/api/dashboard").json()["merge_review_total"] == 0


def test_an_adopted_group_is_not_a_task(client, db):
    """採用済みは決着している（`work/Merge.tsx` の `adoptable` と同じ条件）."""
    import json

    profile = a_profile(db, slug="dash-adopted")
    a_media_file(db, profile, rel_path="library/dash/ADOPTED.MP4", role="derived")
    output = db.execute(
        "SELECT id FROM media_file WHERE rel_path = 'library/dash/ADOPTED.MP4'"
    ).fetchone()["id"]
    a_merge_group(
        db,
        profile,
        "d-adopted",
        status="merged",
        verification_json=json.dumps({"passed": False}),
        output_media_file_id=output,
        adopted_at="2026-08-20T00:00:00+00:00",
    )

    assert client.get("/api/dashboard").json()["merge_review_total"] == 0


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


def test_a_file_sent_to_one_of_two_destinations_is_no_longer_unsent(client, db):
    """**「まだ送っていません」は「どこにも送っていない」.**

    宛先ごとの「未送信」は写真の一覧で絞れる（`GET /media?status=unsent` に宛先を
    添える）。ホームの数まで宛先ごとにすると、**片方に送った時点で数が減らず**、
    どこを見れば片付くのかが読めない。
    """
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
    assert client.get("/api/dashboard").json()["unsent_total"] == 0


def test_a_record_at_a_disabled_destination_does_not_count_as_sent(client, db):
    """**休止中の宛先は「送った」に数えない**（いま送れる宛先だけを見る）."""
    profile = a_profile(db, slug="dash-paused-sent")
    media = a_media_file(db, profile, rel_path="library/dash/E.JPG")
    paused = a_destination(db, name="paused-one")
    a_destination(db, name="live-one")
    an_upload(
        db,
        paused,
        media,
        state="complete",
        destination_revision_id=paused[1],
        remote_asset_id="asset-2",
    )
    db.execute("UPDATE upload_destination SET enabled = 0 WHERE id = ?", (paused[0],))
    assert client.get("/api/dashboard").json()["unsent_total"] == 1


def test_a_file_still_counts_while_a_send_is_in_flight(client, db):
    """**記録があれば「送った」.** 送信中・失敗・承認待ちは別の枠で数えている."""
    profile = a_profile(db, slug="dash-inflight")
    media = a_media_file(db, profile, rel_path="library/dash/F.JPG")
    only = a_destination(db, name="only")
    an_upload(db, only, media, state="pending", destination_revision_id=only[1])
    assert client.get("/api/dashboard").json()["unsent_total"] == 0


def test_the_home_count_and_the_photo_list_agree(client, db):
    """**ホームが数えたものを、一覧で見られる。**

    条件を 2 か所に書くと、片方だけ直したときに数と並びが食い違う。
    """
    profile = a_profile(db, slug="dash-agree")
    a_media_file(db, profile, rel_path="library/dash/G.JPG")
    sent = a_media_file(db, profile, rel_path="library/dash/H.JPG")
    first = a_destination(db, name="agree-first")
    a_destination(db, name="agree-second")
    an_upload(
        db,
        first,
        sent,
        state="complete",
        destination_revision_id=first[1],
        remote_asset_id="asset-3",
    )

    total = client.get("/api/dashboard").json()["unsent_total"]
    listed = client.get("/api/media?status=unsent").json()

    assert total == 1
    assert listed["total"] == total
    assert sent not in [row["id"] for row in listed["media"]]


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


def test_a_merged_group_from_an_older_profile_revision_is_not_a_task(client, db):
    """**カメラの種類を保存したら、その版で作った結合物は「やること」でもない。**

    `SENDABLE_CLAUSE` は現行の版でないグループの出力を数えない（`group_is_current`
    が必ず断る）。ここで数え続けると、ホームが「確かめてください」と言い、
    採用しても送れないまま数だけが 0 に落ちる —— 行き止まりになる。
    """
    import json

    from mediaferry.ids import new_id

    profile_id, revision_id = a_profile(db, slug="dash-stale-revision")
    a_media_file(db, (profile_id, revision_id), rel_path="library/dash/STALE.MP4", role="derived")
    output = db.execute(
        "SELECT id FROM media_file WHERE rel_path = 'library/dash/STALE.MP4'"
    ).fetchone()["id"]
    a_merge_group(
        db,
        (profile_id, revision_id),
        "d-stale-revision",
        status="merged",
        verification_json=json.dumps({"passed": False}),
        output_media_file_id=output,
    )
    assert client.get("/api/dashboard").json()["merge_review_total"] == 1

    # カメラの種類を保存する（版が上がる）。
    newer = new_id()
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES (?, ?, 2, ?, 1, '2026-08-22T00:00:00+00:00')",
        (newer, profile_id, PLACEHOLDER_DEFINITION_JSON),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = ? WHERE id = ?", (newer, profile_id)
    )

    assert client.get("/api/dashboard").json()["merge_review_total"] == 0


# ------------------------------------------------------------------ 時刻の出し方
#
# **画面は時刻に印を添える**（§13）。システム時刻は `DEFAULT_TIMEZONE` へ直して出し、
# 撮影日時は撮った土地の壁時計のまま出す。どちらも**どの時計のものか**を言うために
# ゾーンが要るが、その 1 つのために画面ごとに `/settings` を引かせない
# （`DashboardProvider` はアプリ全体を包んでいる）。


def test_the_dashboard_carries_the_default_timezone(client, db, monkeypatch):
    """**設定したゾーンを、集計と一緒に配る.**"""
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    assert client.get("/api/dashboard").json()["default_timezone"] == "Asia/Tokyo"


def test_an_unset_timezone_is_reported_as_such(data_root, broker_factory, monkeypatch):
    """**未設定は未設定のまま返す。** 画面はそのとき UTC のまま印を添える.

    **`client` fixture は使えない。** あれは `MEDIAFERRY_DEFAULT_TIMEZONE` を
    立ててからアプリを作るので、後から消しても起動時の env は変わらない。
    """
    from fastapi.testclient import TestClient

    from mediaferry.api.app import create_app

    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.delenv("MEDIAFERRY_DEFAULT_TIMEZONE", raising=False)
    with TestClient(
        create_app(broker_factory=broker_factory), base_url="http://127.0.0.1:8080"
    ) as bare:
        assert bare.get("/api/dashboard").json()["default_timezone"] is None


def test_recent_imports_carry_the_zone_of_the_shot(client, db):
    """**撮影日時には、その値のゾーンを添える.**

    `timezone_policy: none` の値は `+00:00` で保存されるので、オフセットだけでは
    本当に UTC で撮ったものと区別が付かない。空かどうかで見分けられるのは
    `captured_at_tz` だけ。
    """
    profile = a_profile(db, slug="dash-tz")
    a_media_file(
        db,
        profile,
        rel_path="library/dash-tz/A.MP4",
        captured_at="2026-08-26T12:33:05+09:00",
        captured_at_tz="Asia/Tokyo",
    )

    [row] = client.get("/api/dashboard").json()["recent_imports"]

    assert row["captured_at_tz"] == "Asia/Tokyo"


def test_a_shot_without_a_zone_says_so(client, db):
    """**決まらなかったゾーンは `null` のまま返す**（画面が既定のゾーンとみなす）."""
    profile = a_profile(db, slug="dash-notz")
    a_media_file(
        db,
        profile,
        rel_path="library/dash-notz/B.MP4",
        captured_at="2026-08-26T12:33:05+00:00",
        captured_at_tz=None,
    )

    [row] = client.get("/api/dashboard").json()["recent_imports"]

    assert row["captured_at_tz"] is None
