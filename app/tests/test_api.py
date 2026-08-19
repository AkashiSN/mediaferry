from fastapi.testclient import TestClient

from mediaferry.api.app import create_app


def test_health_reports_the_schema_version(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["schema_version"] >= 4


def test_startup_seeds_the_builtin_profiles(client):
    slugs = [p["slug"] for p in client.get("/api/profiles").json()["profiles"]]
    assert "dji-osmo" in slugs


def test_settings_report_their_source_lock_and_tier(client):
    settings = {s["key"]: s for s in client.get("/api/settings").json()["settings"]}
    assert settings["DATA_ROOT"]["source"] == "env"
    assert settings["DATA_ROOT"]["locked"] is True
    assert settings["DATA_ROOT"]["tier"] == "bootstrap"
    assert settings["LOG_LEVEL"]["source"] == "default"
    assert settings["LOG_LEVEL"]["writable"] is True


def test_secrets_are_masked_in_the_api(client, monkeypatch):
    settings = {s["key"]: s for s in client.get("/api/settings").json()["settings"]}
    assert settings["AUTH_PASSWORD"]["value"] in (None, "********")
    assert settings["SECRET_KEY"]["writable"] is False


def test_the_master_key_cannot_be_stored_through_the_api(client):
    """暗号文と復号鍵が同じバックアップに入ると、暗号化が何も守らなくなる."""
    response = client.put("/api/settings", json={"key": "SECRET_KEY", "value": "A" * 44})
    assert response.status_code == 409


def test_a_written_setting_reports_when_it_applies(client):
    body = client.put("/api/settings", json={"key": "LOG_LEVEL", "value": "debug"}).json()
    assert body["applies"] == "runtime"
    body = client.put("/api/settings", json={"key": "HTTP_PORT", "value": "9001"}).json()
    assert body["applies"] == "restart"


def test_writing_an_env_locked_setting_is_a_conflict(client):
    assert client.put("/api/settings", json={"key": "DATA_ROOT", "value": "/x"}).status_code == 409


def test_writing_an_invalid_setting_is_a_bad_request(client):
    response = client.put("/api/settings", json={"key": "HTTP_PORT", "value": "nope"})
    assert response.status_code == 400


def test_devices_lists_the_volume_with_its_profile(client):
    volumes = client.get("/api/devices").json()["volumes"]
    assert len(volumes) == 1
    assert volumes[0]["profile_slug"] == "dji-osmo"
    assert volumes[0]["trusted"] is False


def test_scan_then_import_walks_the_whole_path(client, data_root):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    scan = client.post(f"/api/volumes/{volume_id}/scan").json()
    _await_job(client, scan["job_id"])
    assert client.get("/api/media").json()["media"] == []

    imported = client.post(f"/api/volumes/{volume_id}/import").json()
    _await_job(client, imported["job_id"])

    media = client.get("/api/media").json()["media"]
    assert len(media) == 1
    assert media[0]["rel_path"].startswith("library/dji-osmo/")
    assert (data_root / media[0]["rel_path"]).exists()


def test_trusting_a_volume_sticks(client):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    assert client.post(f"/api/volumes/{volume_id}/trust").status_code == 200
    assert client.get("/api/devices").json()["volumes"][0]["trusted"] is True


def test_jobs_can_be_listed_cancelled_and_followed(client):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    job_id = client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"]
    _await_job(client, job_id)
    assert any(j["id"] == job_id for j in client.get("/api/jobs").json()["jobs"])
    events = client.get(f"/api/jobs/{job_id}/events", params={"after_seq": 0}).json()["events"]
    assert events
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code in (200, 409)


def test_orphans_are_exposed(client, data_root):
    assert client.get("/api/orphans").json()["orphans"] == []


def test_closing_a_volume_releases_the_handle(client):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    job_id = client.post(f"/api/volumes/{volume_id}/import").json()["job_id"]
    _await_job(client, job_id)
    assert client.post(f"/api/volumes/{volume_id}/close").status_code == 200


def test_closing_a_volume_a_job_is_holding_is_a_conflict(client):
    """実行中のワーカーの fd を、API の別スレッドから閉じさせない."""
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    volumes = client.app.state.mediaferry.volumes
    selection = volumes.selection_for(volume_id)
    volumes.open(selection)
    try:
        assert client.post(f"/api/volumes/{volume_id}/close").status_code == 409
    finally:
        volumes.release(selection)
    assert client.post(f"/api/volumes/{volume_id}/close").status_code == 200


def test_a_queued_job_uses_the_profile_revision_it_was_queued_with(db):
    """キューで待っている間にプロファイルを編集しても、規則は変わらない.

    現行リビジョンを読み直すと、確認画面と違う規則で取り込まれる。
    """
    from mediaferry.api.jobs_wiring import _fixed_profile
    from mediaferry.core.profiles.model import definition_to_json
    from mediaferry.db.profiles import ProfileRegistry
    from mediaferry.jobs.volumes import VolumeObservation, VolumeSelection

    registry = ProfileRegistry(db)
    registry.sync_builtins()
    queued = registry.current("dji-osmo")
    changed = definition_to_json(queued.definition).replace(
        '"tolerance_seconds":5', '"tolerance_seconds":9'
    )
    registry._upsert_revision("dji-osmo", changed)  # noqa: SLF001
    assert registry.current("dji-osmo").definition.merge.tolerance_seconds == 9

    selection = VolumeSelection(
        volume_instance_id="v1",
        presence_id="p1",
        observation=VolumeObservation("", 1, "8:160", 8, 160, ""),
        profile_id=queued.profile_id,
        profile_revision_id=queued.revision_id,
    )
    profile = _fixed_profile(db, selection)
    assert profile.revision_id == queued.revision_id
    assert profile.definition.merge.tolerance_seconds == 5


def test_shutdown_waits_for_the_running_handler(data_root, broker, monkeypatch):
    """to_thread のハンドラは task の cancel では止まらない.

    待たずに接続と dirfd を閉じると、まだコピー中のスレッドから見て資源が
    突然消える。lifespan は worker の完了まで待つ。
    """
    import time

    from mediaferry.api import jobs_wiring

    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    order = []

    def slow_scan(self, ctx, conn):
        while not ctx.cancelled():
            time.sleep(0.01)
        # 停止を待っていなければ、ここへ来る前に "shutdown" が積まれる。
        time.sleep(0.3)
        order.append("handler")

    monkeypatch.setattr(jobs_wiring.JobWorld, "run_scan", slow_scan)

    app = create_app(broker_factory=lambda: broker)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        # 状態を変える要求には二重送信 Cookie の対が要る（§14）。
        token = "test-csrf-token"  # noqa: S105 - テスト用の見せかけの値
        client.cookies.set("XSRF-TOKEN", token)
        client.headers["X-CSRF-Token"] = token
        volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
        job_id = client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"]
        deadline = time.monotonic() + 10
        while client.get(f"/api/jobs/{job_id}").json()["status"] != "running":
            assert time.monotonic() < deadline, "ジョブが走り出さない"
            time.sleep(0.01)
    order.append("shutdown")

    assert order == ["handler", "shutdown"]


def test_a_job_carries_the_presence_it_was_queued_against(client, data_root):
    """volume_instance_id だけだと、抜き差し後に別のカードを取り込みうる（§9.2）."""
    from mediaferry.db.connection import Database

    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    job_id = client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"]
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    params = conn.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    conn.close()
    for key in ("presence_id", "broker_epoch", "generation", "volume_key", "profile_revision_id"):
        assert key in params


def test_the_device_list_reports_identity_confidence(client):
    volume = client.get("/api/devices").json()["volumes"][0]
    assert volume["identity_confidence"] in {"high", "low"}


def _await_job(client, job_id, timeout=20.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()["status"]
        if status not in {"queued", "running", "cancelling"}:
            assert status == "succeeded", client.get(f"/api/jobs/{job_id}").json()
            return
        time.sleep(0.05)
    raise AssertionError(f"ジョブ {job_id} が終わらない")


def test_a_profile_can_be_tried_against_a_volume(client):
    """**判定を試せる**（§11 の `POST /profiles/{id}/test`）.

    プロファイルを直す前に「このカードにどう当たるか」を見られないと、
    編集の結果を確かめる方法が無い（編集そのものは Phase 5）。
    """
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]

    body = client.post(f"/api/profiles/dji-osmo/test?volume_instance_id={volume_id}").json()

    assert body["matched"] is True
    assert body["profile"] == "dji-osmo"
    assert body["reason"]


def test_trying_an_unknown_profile_is_a_404(client):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    response = client.post(f"/api/profiles/nope/test?volume_instance_id={volume_id}")
    assert response.status_code == 404


def test_trying_against_an_unknown_volume_is_a_404(client):
    response = client.post("/api/profiles/dji-osmo/test?volume_instance_id=nope")
    assert response.status_code == 404
