import base64
import os

import pytest

from mediaferry.db.connection import Database

from .fake_immich import API_KEY, FakeImmich


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("MEDIAFERRY_SECRET_KEY", base64.b64encode(os.urandom(32)).decode())


@pytest.fixture
def api_db(client, data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


def a_body(immich, **over):
    """アプリは差し替え無しで fake へ接続する（`base_url` がループバックの実 URL）."""
    body = {"name": "home", "base_url": immich.url, "api_key": API_KEY}
    body.update(over)
    return body


def test_creating_a_destination_verifies_the_connection(secret_env, immich, client):
    response = client.post("/api/destinations", json=a_body(immich))
    assert response.status_code == 200
    body = response.json()
    assert body["remote_user_id"] == immich.user_id
    assert body["warnings"] == []


def test_the_api_key_never_comes_back(secret_env, immich, client):
    client.post("/api/destinations", json=a_body(immich))
    listed = client.get("/api/destinations").json()["destinations"]
    assert API_KEY not in str(listed)
    assert "api_key" not in str(listed)


def test_a_wrong_key_is_refused_and_stores_nothing(secret_env, immich, client, api_db):
    response = client.post("/api/destinations", json=a_body(immich, api_key="wrong"))
    assert response.status_code == 502
    assert api_db.execute("SELECT count(*) FROM upload_destination").fetchone()[0] == 0


def test_an_unusable_url_is_a_400(secret_env, immich, client):
    response = client.post("/api/destinations", json=a_body(immich, base_url="javascript:x"))
    assert response.status_code == 400


def test_a_second_destination_on_the_same_account_is_warned(secret_env, immich, client):
    client.post("/api/destinations", json=a_body(immich))
    body = client.post("/api/destinations", json=a_body(immich, name="vpn")).json()
    assert body["warnings"]


def test_rotating_the_key_keeps_the_epoch(secret_env, immich, client, api_db):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(f"/api/destinations/{destination_id}", json={"api_key": API_KEY})
    assert response.status_code == 200
    epochs = [row[0] for row in api_db.execute("SELECT target_epoch FROM destination_revision")]
    assert epochs == [1, 1]


@pytest.fixture
def second_immich():
    """別ホストに見える 2 台目（ポートが違えば `_host_of` は別ホストと見なす）."""

    server = FakeImmich()
    server.start()
    yield server
    server.stop()


def test_a_changed_host_needs_an_answer(secret_env, immich, second_immich, client):
    """**到達できる 2 台目を使う。** 届かない URL だと検証で 502 になり、
    epoch の判断まで到達しない."""
    second_immich.user_id = immich.user_id  # 同じユーザを指したまま経路だけ変える
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(
        f"/api/destinations/{destination_id}",
        json={"base_url": second_immich.url, "api_key": API_KEY},
    )
    assert response.status_code == 409
    assert "same_library" in response.json()["detail"]


def test_renaming_does_not_create_a_revision(secret_env, immich, client, api_db):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    assert (
        client.patch(f"/api/destinations/{destination_id}", json={"name": "family"}).status_code
        == 200
    )
    assert api_db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1
    assert api_db.execute("SELECT name FROM upload_destination").fetchone()[0] == "family"


def test_a_failed_verification_does_not_disable_the_destination(secret_env, immich, client, api_db):
    """検証に失敗した編集は、どの欄も反映しない（§12.3）."""
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(
        f"/api/destinations/{destination_id}",
        json={"enabled": False, "base_url": "http://unreachable.invalid:2283", "api_key": API_KEY},
    )
    assert response.status_code == 502
    assert api_db.execute("SELECT enabled FROM upload_destination").fetchone()[0] == 1


def test_the_answer_can_be_given(secret_env, immich, second_immich, client, api_db):
    second_immich.user_id = immich.user_id
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(
        f"/api/destinations/{destination_id}",
        json={"base_url": second_immich.url, "api_key": API_KEY, "same_library": False},
    )
    assert response.status_code == 200
    assert api_db.execute("SELECT max(target_epoch) FROM destination_revision").fetchone()[0] == 2


def test_advancing_the_epoch_invalidates_the_queued_records(secret_env, immich, client, api_db):
    """epoch が進んだら、旧 epoch の未 claim 項目は理由付きで破棄する（§8）."""
    from mediaferry.db.profiles import ProfileRegistry

    from .test_schema_artifacts import a_media_file

    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    profile = ProfileRegistry(api_db).current("dji-osmo")
    media_id = a_media_file(api_db, (profile.profile_id, profile.revision_id))
    client.post("/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]})
    immich.user_id = "someone-else"  # 別アカウントへ向き替える

    body = client.patch(f"/api/destinations/{destination_id}", json={"api_key": API_KEY}).json()

    assert body["target_epoch"] == 2
    assert body["invalidated_records"] == 1
    row = api_db.execute("SELECT invalidated_reason FROM upload_record").fetchone()
    assert row["invalidated_reason"]


def test_verifying_reports_where_it_points(secret_env, immich, client):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    body = client.post(f"/api/destinations/{destination_id}/verify").json()
    assert body["matches"] is True

    immich.user_id = "someone-else"
    body = client.post(f"/api/destinations/{destination_id}/verify").json()
    assert body["matches"] is False


def test_archiving_takes_it_out_of_the_list(secret_env, immich, client):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    assert client.post(f"/api/destinations/{destination_id}/archive").status_code == 200
    assert client.get("/api/destinations").json()["destinations"] == []


def test_a_destination_needs_a_master_key(immich, client):
    """`SECRET_KEY` が無ければ作らせない（§12.3）."""
    assert client.post("/api/destinations", json=a_body(immich)).status_code == 400


def test_starting_up_with_destinations_but_no_key_is_refused(
    secret_env, immich, client, data_root, broker, monkeypatch
):
    from mediaferry.api.app import create_app

    client.post("/api/destinations", json=a_body(immich))
    monkeypatch.delenv("MEDIAFERRY_SECRET_KEY")
    with pytest.raises(RuntimeError):
        from fastapi.testclient import TestClient

        with TestClient(create_app(broker_factory=lambda: broker)):
            pass


def test_an_unknown_field_in_a_patch_is_refused(secret_env, immich, client):
    """知らない欄を黙って捨てない（利用者は反映されたと思う）."""
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(f"/api/destinations/{destination_id}", json={"basurl": "typo"})
    assert response.status_code == 400
    assert "basurl" in response.json()["detail"].lower().replace("'", "")
