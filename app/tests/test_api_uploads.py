import hashlib

import pytest

from mediaferry.db.connection import Database
from mediaferry.db.profiles import ProfileRegistry

from .fake_immich import API_KEY
from .test_api_destinations import a_body, secret_env  # noqa: F401
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"


@pytest.fixture
def api_db(client, data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


@pytest.fixture
def world(secret_env, immich, client, api_db, data_root):  # noqa: F811
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    profile = ProfileRegistry(api_db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "A.MP4").write_bytes(PAYLOAD)
    media_id = a_media_file(
        api_db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
    )
    return immich, destination_id, media_id, api_db


def test_uploads_are_created_per_pair(world, client):
    _, destination_id, media_id, api_db = world

    body = client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    ).json()

    assert [pair["result"] for pair in body["pairs"]] == ["created"]
    assert api_db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 1


def test_an_unknown_media_id_rejects_the_request(world, client):
    _, destination_id, _, api_db = world
    response = client.post(
        "/api/uploads", json={"media_ids": ["nope"], "destination_ids": [destination_id]}
    )
    assert response.status_code == 400
    assert api_db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 0


def test_the_records_can_be_listed_and_filtered(world, client):
    _, destination_id, media_id, _ = world
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})
    body = client.get(f"/api/uploads?destination_id={destination_id}&state=pending").json()
    assert [row["state"] for row in body["records"]] == ["pending"]
    assert body["records"][0]["media_file_id"] == media_id


def test_a_failed_record_can_be_retried(world, client):
    """**既定でない根拠**を持つレコードで確かめる.

    `default` のままだと、`selection_rule` を書き換える変異が「同じ値で上書き」に
    なり、不変 trigger も発火しないので見分けられない。
    """
    from .test_selection import a_group, a_pair

    _, destination_id, _, api_db = world
    profile = ProfileRegistry(api_db).current("dji-osmo")
    members = a_pair(api_db, profile)
    a_group(api_db, profile, members, status="failed", verification=None)
    client.post(
        "/api/uploads", json={"media_ids": [members[0][0]], "destination_ids": [destination_id]}
    )
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    api_db.execute("UPDATE upload_record SET state = 'failed'")

    assert client.post(f"/api/uploads/{record_id}/retry").status_code == 200

    row = api_db.execute("SELECT state, selection_rule FROM upload_record").fetchone()
    assert row["state"] == "pending"
    # 再試行は「なぜ最初に送信を許可したか」を変えない（§8）。
    assert row["selection_rule"] == "failed_group_member"


def test_retrying_something_that_is_not_failed_is_a_409(world, client):
    _, destination_id, media_id, api_db = world
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    assert client.post(f"/api/uploads/{record_id}/retry").status_code == 409


def _an_awaiting_record(client, api_db, destination_id, media_id):
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    revision_id = api_db.execute("SELECT current_revision_id FROM upload_destination").fetchone()[0]
    api_db.execute(
        "UPDATE upload_record SET state = 'awaiting_datetime_approval',"
        " remote_asset_id = 'asset-1', destination_revision_id = ?",
        (revision_id,),
    )
    return record_id


def test_rejecting_completes_without_touching_the_remote(world, client):
    server, destination_id, media_id, api_db = world
    record_id = _an_awaiting_record(client, api_db, destination_id, media_id)

    assert client.post(f"/api/uploads/{record_id}/reject").status_code == 200

    assert api_db.execute("SELECT state FROM upload_record").fetchone()[0] == "complete"
    assert server.datetimes == {}


def test_approving_enqueues_a_job_that_owns_the_side_effect(world, client):
    """承認は同期で PUT せず、claim を取れるジョブとして走らせる（Task 11）."""
    import json

    server, destination_id, media_id, api_db = world
    record_id = _an_awaiting_record(client, api_db, destination_id, media_id)

    job_id = client.post(f"/api/uploads/{record_id}/approve").json()["job_id"]

    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["mode"] == "approve"
    assert params["upload_record_id"] == record_id
    # ジョブが走るまでリモートは変わらない。
    assert server.datetimes == {}


def test_approving_something_that_is_not_waiting_is_a_409(world, client):
    _, destination_id, media_id, api_db = world
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    assert client.post(f"/api/uploads/{record_id}/approve").status_code == 409


def test_a_vanished_asset_can_be_sent_again(world, client):
    """再確認で「リモートに存在しない」と分かったものだけ送り直せる（§9.10）."""
    _, destination_id, media_id, api_db = world
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    revision_id = api_db.execute("SELECT current_revision_id FROM upload_destination").fetchone()[0]
    api_db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = NULL,"
        " remote_checked_at = '2026-08-17T00:00:00+00:00', destination_revision_id = ?",
        (revision_id,),
    )

    assert client.post(f"/api/uploads/{record_id}/requeue").status_code == 200

    assert api_db.execute("SELECT state FROM upload_record").fetchone()[0] == "pending"


def test_a_healthy_complete_record_cannot_be_requeued(world, client):
    """送信済みのものを、確認もせずに送り直させない."""
    _, destination_id, media_id, api_db = world
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    revision_id = api_db.execute("SELECT current_revision_id FROM upload_destination").fetchone()[0]
    api_db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = 'asset-1',"
        " remote_checked_at = '2026-08-17T00:00:00+00:00', destination_revision_id = ?",
        (revision_id,),
    )
    assert client.post(f"/api/uploads/{record_id}/requeue").status_code == 409


def test_starting_an_upload_enqueues_a_job_for_that_destination(world, client, api_db):
    _, destination_id, media_id, _ = world
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})

    job_id = client.post(f"/api/destinations/{destination_id}/upload").json()["job_id"]

    import json

    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["destination_id"] == destination_id
    assert params["mode"] == "send"
    # **秘密を params に入れない。**
    assert API_KEY not in json.dumps(params)


def test_a_recheck_is_the_same_job_type_with_another_mode(world, client, api_db):
    _, destination_id, _, _ = world
    job_id = client.post(f"/api/destinations/{destination_id}/recheck").json()["job_id"]
    import json

    row = api_db.execute("SELECT type, params_json FROM job WHERE id = ?", (job_id,)).fetchone()
    assert row["type"] == "upload"
    assert json.loads(row["params_json"])["mode"] == "recheck"


def test_a_second_approval_is_not_queued(world, client):
    """同じレコードの承認ジョブを二重に積まない.

    積めると、1 本目が終わった後の残りが軒並み失敗として画面に並ぶ。
    """
    _, destination_id, media_id, api_db = world
    record_id = _an_awaiting_record(client, api_db, destination_id, media_id)

    assert client.post(f"/api/uploads/{record_id}/approve").status_code == 200
    assert client.post(f"/api/uploads/{record_id}/approve").status_code == 409

    assert api_db.execute("SELECT count(*) FROM job WHERE type = 'upload'").fetchone()[0] == 1


def test_the_list_can_be_narrowed_by_destination_and_state(world, client, immich):  # noqa: F811
    """絞り込みが効かないと、別の宛先の記録まで混ざって見える."""
    _, home, media_id, api_db = world
    family = client.post("/api/destinations", json=a_body(immich, name="family")).json()["id"]
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [home, family]})
    api_db.execute("UPDATE upload_record SET state = 'failed' WHERE destination_id = ?", (family,))

    body = client.get(f"/api/uploads?destination_id={home}").json()
    assert [row["destination_id"] for row in body["records"]] == [home]

    body = client.get("/api/uploads?state=failed").json()
    assert [row["destination_id"] for row in body["records"]] == [family]


# --- スタックの表示とフィルタ（Phase 6 / §9.11） ------------------------


def a_stacked_and_a_skipped(db):
    """`stacked` 1 件と `skipped` 1 件を作り、その id を返す."""
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile
    from .test_schema_uploads import a_destination, an_upload

    # ビルトインは fixture が同期済みなので、衝突しない slug を使う。
    profile = a_profile(db, slug="stack-test-cam")
    dest = a_destination(db, name="stack-test")
    made = {}
    for name, fields in (
        ("stacked", {"stack_state": "stacked", "remote_stack_id": "stack-1"}),
        ("skipped", {"stack_state": "skipped", "stack_reason": "相方が見つからない"}),
        ("open", {}),
    ):
        made[name] = an_upload(
            db,
            dest,
            a_media_file(db, profile),
            state="complete",
            destination_revision_id=dest[1],
            remote_asset_id=f"asset-{name}",
            **fields,
        )
    return dest[0], made


def test_a_record_shows_its_stack_state(secret_env, client, api_db):  # noqa: F811
    _, made = a_stacked_and_a_skipped(api_db)

    response = client.get("/api/uploads")
    assert response.status_code == 200, response.json()
    records = {row["id"]: row for row in response.json()["records"]}

    assert records[made["stacked"]]["stack_state"] == "stacked"
    assert records[made["stacked"]]["remote_stack_id"] == "stack-1"
    assert records[made["skipped"]]["stack_reason"] == "相方が見つからない"
    assert records[made["open"]]["stack_state"] is None


def test_records_can_be_filtered_by_stack_state(secret_env, client, api_db):  # noqa: F811
    _, made = a_stacked_and_a_skipped(api_db)

    body = client.get("/api/uploads?stack_state=skipped").json()

    assert [row["id"] for row in body["records"]] == [made["skipped"]]


def test_unevaluated_records_can_be_listed(secret_env, client, api_db):  # noqa: F811
    _, made = a_stacked_and_a_skipped(api_db)

    body = client.get("/api/uploads?stack_state=unevaluated").json()

    assert [row["id"] for row in body["records"]] == [made["open"]]


def test_the_list_leaves_out_invalidated_records(secret_env, client, api_db):  # noqa: F811
    """**無効になった記録は一覧に出さない。**

    無効な記録には何もできない（承認は 409 `already_invalidated`、却下も 409）ので、
    出すとカードが消せなくなる。ダッシュボードの件数（`awaiting_total` など）も
    無効を除いて数えるため、出すと画面ごとに数が食い違う。
    """
    from mediaferry.clock import now_iso

    _, made = a_stacked_and_a_skipped(api_db)
    api_db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'x' WHERE id = ?",
        (now_iso(), made["skipped"]),
    )

    body = client.get("/api/uploads").json()

    assert made["skipped"] not in [row["id"] for row in body["records"]]
    assert made["stacked"] in [row["id"] for row in body["records"]]


def test_a_record_carries_the_file_name_it_is_about(secret_env, client, api_db):  # noqa: F811
    """**画面は内部の ID を出さない**（§13）ので、一覧にファイルの位置を添える."""
    _, made = a_stacked_and_a_skipped(api_db)

    records = {row["id"]: row for row in client.get("/api/uploads").json()["records"]}

    assert records[made["skipped"]]["rel_path"]


def test_an_unknown_stack_state_is_refused(secret_env, client):  # noqa: F811
    """**絞ったつもりで全件が出る**を作らない."""
    assert client.get("/api/uploads?stack_state=nonsense").status_code == 400


def test_the_dashboard_counts_stacks_per_destination(secret_env, client, api_db):  # noqa: F811
    a_stacked_and_a_skipped(api_db)

    summary = client.get("/api/dashboard").json()["destinations"][0]

    assert summary["stacked"] == 1
    assert summary["stack_skipped"] == 1


def test_the_dashboard_does_not_count_invalidated_records(secret_env, client, api_db):  # noqa: F811
    from mediaferry.clock import now_iso

    _, made = a_stacked_and_a_skipped(api_db)
    api_db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'x' WHERE id = ?",
        (now_iso(), made["skipped"]),
    )

    summary = client.get("/api/dashboard").json()["destinations"][0]

    assert summary["stack_skipped"] == 0


def test_the_dashboard_counts_stacks_not_records(secret_env, client, api_db):  # noqa: F811
    """**1 つのスタックに 2 件以上のレコードが属する。** 行を数えると「2 組」に見える."""
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile
    from .test_schema_uploads import a_destination, an_upload

    profile = a_profile(api_db, slug="stack-count-cam")
    dest = a_destination(api_db, name="stack-count")
    for index in range(2):
        an_upload(
            api_db,
            dest,
            a_media_file(api_db, profile),
            state="complete",
            destination_revision_id=dest[1],
            remote_asset_id=f"asset-{index}",
            stack_state="stacked",
            remote_stack_id="stack-1",
        )

    summary = next(
        row
        for row in client.get("/api/dashboard").json()["destinations"]
        if row["name"] == "stack-count"
    )

    assert summary["stacked"] == 1


def test_requeue_clears_the_stack_result(secret_env, client, api_db):  # noqa: F811
    """**送り直す前に、前回の結果を捨てる。**

    残すと `unstacked_batch` が拾わず、新しい資産で組み直せない。
    """
    from mediaferry.clock import now_iso

    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile
    from .test_schema_uploads import a_destination, an_upload

    profile = a_profile(api_db, slug="requeue-cam")
    dest = a_destination(api_db, name="requeue-dest")
    record = an_upload(
        api_db,
        dest,
        a_media_file(api_db, profile),
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id=None,
        remote_checked_at=now_iso(),
        stack_state="skipped",
        stack_reason="相方が見つからない",
    )

    assert client.post(f"/api/uploads/{record}/requeue").status_code == 200

    row = api_db.execute(
        "SELECT stack_state, stack_reason FROM upload_record WHERE id = ?", (record,)
    ).fetchone()
    assert row["stack_state"] is None
    assert row["stack_reason"] is None


def test_retry_clears_the_stack_result(secret_env, client, api_db):  # noqa: F811
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile
    from .test_schema_uploads import a_destination, an_upload

    profile = a_profile(api_db, slug="retry-cam")
    dest = a_destination(api_db, name="retry-dest")
    record = an_upload(
        api_db,
        dest,
        a_media_file(api_db, profile),
        state="failed",
        destination_revision_id=dest[1],
        stack_state="skipped",
        stack_reason="相方が見つからない",
    )

    assert client.post(f"/api/uploads/{record}/retry").status_code == 200

    row = api_db.execute("SELECT stack_state FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["stack_state"] is None


def test_a_refusal_does_not_put_internal_ids_in_what_the_screen_shows(world, client):
    """**断る理由に内部の ID を混ぜない**（§13）.

    画面は `bad_request` の `detail` をそのまま括弧で添えるので、ここに ID を
    入れると利用者の画面に生の UUID が出る。
    """
    _, destination_id, media_id, _ = world

    unknown_media = client.post(
        "/api/uploads",
        json={"media_ids": ["01J8XR-not-a-real-id"], "destination_ids": [destination_id]},
    )
    assert unknown_media.status_code == 400
    assert "01J8XR-not-a-real-id" not in str(unknown_media.json())

    unknown_destination = client.post(
        "/api/uploads",
        json={"media_ids": [media_id], "destination_ids": ["01J8XS-not-a-real-id"]},
    )
    assert unknown_destination.status_code == 400
    assert "01J8XS-not-a-real-id" not in str(unknown_destination.json())


def test_a_bad_filter_value_is_not_echoed_back(world, client):
    """絞り込みの値も反射させない（`stack_state` は内部の語彙でもある）."""
    response = client.get("/api/uploads?stack_state=not-a-real-state")

    assert response.status_code == 400
    assert "not-a-real-state" not in str(response.json())


def test_the_approval_list_does_not_look_things_up_per_row(world, client, api_db, monkeypatch):
    """**行ごとに引き直さない。** 確認の一覧は 1 度に 200 件出す（`APPROVE_PAGE`）.

    差分（現在値・補正案）を作るために行ごとに `media_file` とカメラの種類を
    引いていると、1 画面で数百本の問い合わせになる。一覧が継いで返す値で足りる。
    """
    from mediaferry.db.profiles import ProfileRegistry as Registry

    from .test_schema_artifacts import a_media_file

    profile = ProfileRegistry(api_db).current("dji-osmo")
    revision_id = api_db.execute("SELECT current_revision_id FROM upload_destination").fetchone()[0]
    destination_id = world[1]
    for index in range(10):
        media_id = a_media_file(
            api_db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/N{index}.MP4",
        )
        client.post(
            "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
        )
    api_db.execute(
        "UPDATE upload_record SET state = 'awaiting_datetime_approval',"
        " remote_asset_id = 'asset-1', destination_revision_id = ?",
        (revision_id,),
    )

    looked_up: list[str] = []
    original = Registry.by_id
    monkeypatch.setattr(
        Registry,
        "by_id",
        lambda self, profile_id: (looked_up.append(profile_id), original(self, profile_id))[1],
    )

    records = client.get("/api/uploads?state=awaiting_datetime_approval&limit=201").json()[
        "records"
    ]

    assert len(records) == 10
    assert all(record["proposed"] is not None for record in records)
    # **行数に比例して増えない。** カメラの種類は 1 つしか無い。
    assert len(set(looked_up)) == 1
    assert len(looked_up) <= 1, looked_up
