"""プロファイルの編集 API（Task 5）.

**中心の判断: ビルトインへの mutation は `duplicate` を除いて全部拒む。**
現行の `_upsert_revision` は `builtin` を見ないので、放置すると次のアプリ更新で
`sync_builtins` がユーザの編集を黙って上書きする。`archive` も同じ ——
`sync_builtins` は `archived_at` を戻さないので、一度 archive されたビルトインは
再起動しても復活しない。「読み取り専用」が破れたまま元に戻せなくなる。

スキーマ（`builtin` 列、`archived_at`、版の不変 trigger、複合外部キー）は `0002`
で揃っているので移行は要らない。
"""

from __future__ import annotations

import copy

from .test_profile_model import a_definition


def a_user_profile(client, slug="my-camera"):
    body = a_definition(slug=slug, name="私のカメラ")
    response = client.post("/api/profiles", json={"definition": body})
    assert response.status_code == 200, response.text
    return response.json()


# ----------------------------------------------------------------------
# 読み取り


def test_the_list_says_which_profiles_are_builtin(client):
    profiles = client.get("/api/profiles").json()["profiles"]
    by_slug = {p["slug"]: p for p in profiles}
    assert by_slug["dji-osmo"]["builtin"] is True
    assert by_slug["dji-osmo"]["archived"] is False


def test_a_single_profile_returns_its_definition(client):
    got = client.get("/api/profiles/dji-osmo").json()
    assert got["slug"] == "dji-osmo"
    assert got["revision"] == 1
    assert got["definition"]["timestamp"]["source"] == "filename"


def test_an_unknown_slug_is_404(client):
    response = client.get("/api/profiles/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ----------------------------------------------------------------------
# ビルトインの保護


def test_editing_a_builtin_is_refused(client):
    """次のアプリ更新で `sync_builtins` が黙って上書きするため."""
    definition = client.get("/api/profiles/dji-osmo").json()["definition"]
    definition["merge"]["tolerance_seconds"] = 9
    response = client.put("/api/profiles/dji-osmo", json={"definition": definition})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    # 版が増えていないこと
    assert client.get("/api/profiles/dji-osmo").json()["revision"] == 1


def test_archiving_a_builtin_is_refused(client):
    """`sync_builtins` は `archived_at` を戻さない.

    一度 archive すると、再起動しても候補から消えたままになる。
    """
    response = client.post("/api/profiles/dji-osmo/archive")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert client.get("/api/profiles/dji-osmo").json()["archived"] is False


def test_duplicating_a_builtin_makes_an_editable_copy(client):
    """§6 の「GUI で編集しようとすると複製が作られる」の受け皿."""
    made = client.post(
        "/api/profiles/dji-osmo/duplicate", json={"slug": "my-dji", "name": "私の DJI"}
    )
    assert made.status_code == 200, made.text
    copy_of = client.get("/api/profiles/my-dji").json()
    assert copy_of["builtin"] is False
    assert copy_of["revision"] == 1
    # 中身は元と同じ（slug と name を除く）
    original = client.get("/api/profiles/dji-osmo").json()["definition"]
    assert copy_of["definition"]["merge"] == original["merge"]
    # **元のビルトインは変わらない**
    assert client.get("/api/profiles/dji-osmo").json()["builtin"] is True


# ----------------------------------------------------------------------
# ユーザ定義の編集


def test_editing_a_user_profile_creates_a_new_revision(client):
    a_user_profile(client)
    definition = client.get("/api/profiles/my-camera").json()["definition"]
    definition["merge"]["tolerance_seconds"] = 9
    response = client.put("/api/profiles/my-camera", json={"definition": definition})
    assert response.status_code == 200, response.text
    got = client.get("/api/profiles/my-camera").json()
    assert got["revision"] == 2
    assert got["definition"]["merge"]["tolerance_seconds"] == 9


def test_the_old_revision_is_kept(client, database):
    """過去データの解釈が後から変わらない（§6）."""
    a_user_profile(client)
    before = client.get("/api/profiles/my-camera").json()
    definition = copy.deepcopy(before["definition"])
    definition["merge"]["tolerance_seconds"] = 9
    client.put("/api/profiles/my-camera", json={"definition": definition})
    conn = database.connect()
    try:
        revisions = conn.execute(
            "SELECT count(*) FROM profile_revision r JOIN device_profile p"
            " ON p.id = r.profile_id WHERE p.slug = 'my-camera'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert revisions == 2, "旧リビジョンが残っていない"


def test_the_slug_cannot_change(client):
    """ライブラリのパス（`library/<slug>/`）に使うので、変えると過去が宙に浮く."""
    a_user_profile(client)
    definition = client.get("/api/profiles/my-camera").json()["definition"]
    definition["slug"] = "renamed"
    response = client.put("/api/profiles/my-camera", json={"definition": definition})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_an_invalid_definition_creates_no_revision(client, database):
    """**検証は commit の前。** 通ってから直すと、壊れた版が残る."""
    a_user_profile(client)
    definition = client.get("/api/profiles/my-camera").json()["definition"]
    definition["require"]["filename_pattern"] = "("
    response = client.put("/api/profiles/my-camera", json={"definition": definition})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert client.get("/api/profiles/my-camera").json()["revision"] == 1


def test_creating_a_profile_with_an_existing_slug_is_refused(client):
    a_user_profile(client)
    response = client.post("/api/profiles", json={"definition": a_definition(slug="my-camera")})
    assert response.status_code == 409


# ----------------------------------------------------------------------
# archive


def test_archiving_a_user_profile_removes_it_from_the_candidates(client, database):
    a_user_profile(client)
    assert client.post("/api/profiles/my-camera/archive").status_code == 200
    assert client.get("/api/profiles/my-camera").json()["archived"] is True

    from mediaferry.db.profiles import ProfileRegistry

    conn = database.connect()
    try:
        assert "my-camera" not in [r.definition.slug for r in ProfileRegistry(conn).active()]
    finally:
        conn.close()


def test_an_archived_revision_is_still_readable(client, database):
    """使用済みの版は消さない. 過去データの解釈が変わらないため（§6）."""
    made = a_user_profile(client)
    client.post("/api/profiles/my-camera/archive")

    from mediaferry.db.profiles import ProfileRegistry

    conn = database.connect()
    try:
        assert ProfileRegistry(conn).definition_of(made["revision_id"]).slug == "my-camera"
    finally:
        conn.close()


def test_archiving_twice_is_refused(client):
    a_user_profile(client)
    assert client.post("/api/profiles/my-camera/archive").status_code == 200
    assert client.post("/api/profiles/my-camera/archive").status_code == 409


def test_the_list_still_shows_archived_profiles(client):
    """**一覧からも消すと「外した」のか「消えた」のか分からない。**

    archive は削除ではない（使用済みの版は参照が残る）。画面で区別できるよう、
    一覧には出したうえで印を付ける。
    """
    a_user_profile(client)
    client.post("/api/profiles/my-camera/archive")
    profiles = {p["slug"]: p for p in client.get("/api/profiles").json()["profiles"]}
    assert "my-camera" in profiles, "archive したら一覧から消えている"
    assert profiles["my-camera"]["archived"] is True
    assert profiles["dji-osmo"]["archived"] is False


# ----------------------------------------------------------------------
# 再計算（Task 6）


def _await_job(client, job_id, timeout=20.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running", "cancelling"}:
            assert job["status"] == "succeeded", job
            return job
        time.sleep(0.05)
    raise AssertionError(f"ジョブ {job_id} が終わらない")


def test_recompute_enqueues_a_job_pinned_to_the_current_revision(client, database):
    """キュー投入時の版を params に固定する.

    実行時に現行を読み直すと、キューで待っている間の編集で、確認した規則とは
    違う定義で再計算されることになる（`detect_groups` と同じ形）。
    """
    a_user_profile(client)
    profile = client.get("/api/profiles/my-camera").json()
    job_id = client.post("/api/profiles/my-camera/recompute").json()["job_id"]
    _await_job(client, job_id)

    conn = database.connect()
    try:
        row = conn.execute("SELECT type, params_json FROM job WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    assert row["type"] == "recompute_timestamps"
    assert profile["revision_id"] in row["params_json"]


def test_recompute_is_allowed_on_a_builtin(client):
    """ビルトインは**編集**できないだけで、再計算は要る.

    `DEFAULT_TIMEZONE` を後から設定した場合、既存レコードを直す手段がこれしかない
    （§12.1）。
    """
    response = client.post("/api/profiles/dji-osmo/recompute")
    assert response.status_code == 200, response.text
    _await_job(client, response.json()["job_id"])


def test_recompute_on_an_unknown_profile_is_404(client):
    response = client.post("/api/profiles/nope/recompute")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
