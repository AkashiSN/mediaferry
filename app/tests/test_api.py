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


def test_devices_says_how_much_is_left_and_when_it_was_counted(client):
    """ホームの「N 件を取り込む」の材料が、実際に `/devices` から出ていること.

    数える前は「まだ数えていない」（`scanned_at` が空）。数えると件数と時刻の
    両方が埋まり、運び終えると件数だけが 0 に戻る。**「まだ数えていない」と
    「数えたが空」の区別が付かないと、挿した直後の画面が嘘をつく。**
    """
    before = client.get("/api/devices").json()["volumes"][0]
    assert (before["pending_count"], before["scanned_at"], before["busy"]) == (0, None, False)

    volume_id = before["volume_instance_id"]
    _await_job(client, client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"])

    counted = client.get("/api/devices").json()["volumes"][0]
    assert counted["pending_count"] == 1
    assert counted["scanned_at"] is not None

    _await_job(client, client.post(f"/api/volumes/{volume_id}/import").json()["job_id"])

    settled = client.get("/api/devices").json()["volumes"][0]
    assert settled["pending_count"] == 0
    # **運び終えても「まだ数えていない」には戻らない**（0 件と未計測は別）。
    assert settled["scanned_at"] is not None


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


def test_the_import_summary_counts_what_it_skipped(client):
    """2 度目の取り込みが「何もしなかった」ように読めてはいけない.

    スキップは効いているのに件数がどこにも出ないと、チェックリストの
    「取込済のファイルはスキップされる」を画面からもログからも確かめられない。
    """
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    _await_job(client, client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"])
    _await_job(client, client.post(f"/api/volumes/{volume_id}/import").json()["job_id"])
    second = client.post(f"/api/volumes/{volume_id}/import").json()["job_id"]
    _await_job(client, second)
    events = client.get(f"/api/jobs/{second}/events", params={"after_seq": 0}).json()["events"]
    summary = events[-1]["message"]
    assert summary == "取り込み完了: 0 件 / スキップ 1 件 / 失敗 0 件"


def test_the_scan_summary_counts_what_left_the_card(client, fake_card):
    """カードから消えたファイルを外したことが、ログから確かめられる.

    件数が出ないと、`pending_count` が減った理由が誰にも分からない。実機では
    「取り込む 1488 件」が「68 件」に変わるので、黙って変えてはいけない。
    """
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    _await_job(client, client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"])
    (fake_card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()

    second = client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"]
    _await_job(client, second)

    events = client.get(f"/api/jobs/{second}/events", params={"after_seq": 0}).json()["events"]
    assert events[-1]["message"] == "スキャン完了: 新規 0 件 / 取込済 0 件 / 消えた 1 件"


def test_a_file_that_left_the_card_is_no_longer_counted_as_pending(client, fake_card):
    """画面の「残り N 件」が実体に合う.

    合わないと、押せば必ず失敗する取り込みを画面が勧めることになる。
    """
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    _await_job(client, client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"])
    assert client.get("/api/devices").json()["volumes"][0]["pending_count"] == 1
    (fake_card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()

    _await_job(client, client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"])

    assert client.get("/api/devices").json()["volumes"][0]["pending_count"] == 0


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


def test_the_app_starts_on_an_empty_dataset(tmp_path, broker_factory, monkeypatch):
    """**手順書の mkdir を無くす。** データセットを作って chown しただけで起動する.

    これが通らないと、5 つのディレクトリを手で作る手順が消せない（同一
    ファイルシステムの検査が `stat` で落ちる）。
    """
    root = tmp_path / "dataset"
    root.mkdir()
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    with TestClient(create_app(broker_factory=broker_factory), base_url="http://127.0.0.1:8080"):
        pass
    assert sorted(p.name for p in root.iterdir()) == [
        "derived",
        "library",
        "staging",
        "var",
        "work",
    ]


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
        # **この POST の成否も見る。** 監視役の `scan` が必ず 1 本走るので、
        # 戻り値を捨てると口が 403 や 500 を返すようになっても緑のままになる。
        assert client.post(f"/api/volumes/{volume_id}/scan").status_code == 200
        # **どの 1 本かは決め打ちにしない。** 監視役も挿さっているカードに
        # `scan` を積み、ジョブは 1 本ずつ直列に走るので、先に claim されるのは
        # そちらのことがある。自分の id を待つと止まる。見たいのは
        # 「走っているハンドラを停止が待つか」だけ。
        deadline = time.monotonic() + 10
        while all(job["status"] != "running" for job in client.get("/api/jobs").json()["jobs"]):
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


def test_devices_persists_whether_the_match_was_provisional(client, database, fake_card):
    """判定の暫定フラグを DB に残す.

    watcher は毎 tick DB の現在値から「積んでよいか」を組み直すので、
    VolumeView にしか無い値があると組み直せない（§12.1）。

    **両方の値を通す。** 既定が 0 なので、確定マッチだけを見ても
    「書いている」ことの証拠にならない。
    """

    def stored_flag(view):
        conn = database.connect()
        try:
            row = conn.execute(
                "SELECT provisional FROM volume_instance WHERE id = ?",
                (view["volume_instance_id"],),
            ).fetchone()
            assert row is not None
            return bool(row["provisional"])
        finally:
            conn.close()

    view = client.get("/api/devices").json()["volumes"][0]
    assert view["provisional"] is False
    assert stored_flag(view) is False

    # 一致するファイルを消すと「対象だが中身が無い」になる（Phase 0 の発見 B）。
    for path in (fake_card / "DCIM" / "DJI_001").iterdir():
        path.unlink()
    view = client.get("/api/devices").json()["volumes"][0]
    assert view["provisional"] is True, "暫定マッチの筋書きになっていない"
    assert stored_flag(view) is True


def test_a_running_job_carries_its_progress(client, api_db=None):
    """走っているジョブの「いまどこか」を API から見せる（画面が 2 秒ごとに引く）."""
    import json as json_module

    from mediaferry.db.jobs import JobStore

    conn = client.app.state.mediaferry.database.connect()
    try:
        store = JobStore(conn)
        store.enqueue("import", {})
        ctx = store.claim_next()
        ctx.heartbeat({"phase": "copy", "bytes_done": 5, "bytes_total": 10})
        body = client.get(f"/api/jobs/{ctx.job_id}").json()
        assert body["progress"]["phase"] == "copy"
        assert body["progress"]["bytes_done"] == 5
        listed = {job["id"]: job for job in client.get("/api/jobs").json()["jobs"]}
        assert listed[ctx.job_id]["progress"]["bytes_total"] == 10
        # 終わったら消える。
        store.finish(ctx.job_id, ctx.lease_token, "succeeded")
        assert client.get(f"/api/jobs/{ctx.job_id}").json()["progress"] is None
        assert json_module.dumps(body)
    finally:
        conn.close()


def test_a_finished_job_never_shows_progress(client):
    """**終わったジョブに「いま何をしているか」は無い**（読む側でも守る）.

    落とすのは終了時の 1 回きりなので、書き手が取り違えると（`finish` と
    `finish_claimed`）画面に残り続ける。実機で「完了」なのに「結合中 …」と
    出ていた。
    """
    from mediaferry.db.jobs import JobStore

    conn = client.app.state.mediaferry.database.connect()
    try:
        store = JobStore(conn)
        store.enqueue("merge", {})
        ctx = store.claim_next()
        ctx.heartbeat({"phase": "merge", "bytes_done": 5})
        # 落とし忘れた行を直に作る。
        conn.execute(
            "UPDATE job SET status = 'succeeded', finished_at = '2026-08-21T00:00:00+00:00'"
            " WHERE id = ?",
            (ctx.job_id,),
        )
        assert client.get(f"/api/jobs/{ctx.job_id}").json()["progress"] is None
    finally:
        conn.close()


def test_a_job_says_which_card_it_belongs_to(client, db):
    """「いま動いていること」がどのカードの作業かを言えるようにする."""
    db.execute(
        "INSERT INTO job (id, type, status, params_json, created_at)"
        " VALUES ('j1', 'import', 'running', ?, '2026-08-24T00:00:00Z')",
        ('{"volume_instance_id": "vol-1"}',),
    )
    db.commit()
    # **自分が入れた 1 本を名指しで取る。** 監視役が積んだ `scan` も一覧に並ぶ。
    jobs = {job["id"]: job for job in client.get("/api/jobs").json()["jobs"]}
    assert jobs["j1"]["volume_instance_id"] == "vol-1"


def test_a_job_with_no_card_says_so(client, db):
    db.execute(
        "INSERT INTO job (id, type, status, params_json, created_at)"
        " VALUES ('j2', 'upload', 'running', '{}', '2026-08-24T00:00:00Z')",
    )
    db.commit()
    jobs = {job["id"]: job for job in client.get("/api/jobs").json()["jobs"]}
    assert jobs["j2"]["volume_instance_id"] is None
