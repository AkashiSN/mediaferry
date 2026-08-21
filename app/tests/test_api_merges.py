import json

import pytest

from mediaferry.db.connection import Database
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_media_file, a_merge_group


@pytest.fixture
def api_db(client, data_root):
    """API と同じ DB ファイルを、テスト用の別接続で開く.

    **接続は共有しない**（トランザクションは接続に属する）。`client` に依存
    させるのは、アプリの起動で migration とビルトインの同期を先に済ませるため。
    """
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


def test_detecting_enqueues_one_job_per_profile(client):
    response = client.post("/api/merge-groups/detect")
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert [entry["profile_slug"] for entry in jobs] == ["dji-osmo"]


def test_detecting_an_unknown_profile_is_a_404(client):
    assert client.post("/api/merge-groups/detect?profile_slug=nope").status_code == 404


def test_the_group_list_carries_its_members_and_verification(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    media_id = a_media_file(api_db, (profile.profile_id, profile.revision_id))
    group_id = a_merge_group(
        api_db,
        (profile.profile_id, profile.revision_id),
        "digest-1",
        verification_json=json.dumps({"passed": True, "route": "concat"}),
    )
    api_db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group_id, media_id),
    )
    body = client.get("/api/merge-groups").json()
    assert [group["id"] for group in body["groups"]] == [group_id]
    assert body["groups"][0]["verification"]["passed"] is True
    assert [member["media_file_id"] for member in body["groups"][0]["members"]] == [media_id]


def test_merging_fixes_the_digest_and_the_revision_in_the_job(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    job_id = client.post(f"/api/merge-groups/{group_id}/merge").json()["job_id"]
    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["merge_group_id"] == group_id
    assert params["input_digest"] == "digest-1"
    assert params["profile_revision_id"] == profile.revision_id


def _bump_revision(api_db, profile, new_id="rev-new"):
    """プロファイルを編集した状態を作る（新しいリビジョンが現行になる）."""
    api_db.execute(
        "INSERT INTO profile_revision (id, profile_id, revision, definition_json,"
        " schema_version, created_at)"
        " SELECT ?, profile_id, revision + 1, definition_json, schema_version, created_at"
        " FROM profile_revision WHERE id = ?",
        (new_id, profile.revision_id),
    )
    api_db.execute(
        "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
        (new_id, profile.profile_id),
    )


def test_the_job_keeps_the_revision_the_group_was_detected_with(client, api_db):
    """**編集してから投入しても、グループが検出されたときの規則で結合する。**

    現行を読み直すと、確認画面で見た構成と違う規則で結合される。
    """
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    _bump_revision(api_db, profile)

    job_id = client.post(f"/api/merge-groups/{group_id}/merge").json()["job_id"]

    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["profile_revision_id"] == profile.revision_id


def test_adopting_a_group_without_an_output_is_a_409(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    assert client.patch(f"/api/merge-groups/{group_id}?action=adopt").status_code == 409


def test_a_group_can_be_discarded(client, api_db):
    """破棄は**公開済みの media_file を消さない**（選択肢から外れるだけ。§3）."""
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")

    assert client.patch(f"/api/merge-groups/{group_id}?action=discard").status_code == 200

    assert client.get(f"/api/merge-groups/{group_id}").json()["status"] == "skipped"


def test_an_unknown_action_is_a_400(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    assert client.patch(f"/api/merge-groups/{group_id}?action=explode").status_code == 400


def test_a_missing_group_is_a_404(client):
    assert client.get("/api/merge-groups/nope").status_code == 404
    assert client.post("/api/merge-groups/nope/merge").status_code == 404


def test_the_selectable_list_is_served(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    media_id = a_media_file(api_db, (profile.profile_id, profile.revision_id))
    body = client.get("/api/uploads/selectable").json()
    assert [item["media_file_id"] for item in body["selectable"]] == [media_id]
    assert body["selectable"][0]["reason"] == "default"


def test_the_preview_does_not_store_anything(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    for index in (1, 2):
        a_media_file(
            api_db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/DJI_001/DJI_20260817143000_{index:04d}_D.MP4",
            sha1=f"{index:040d}",
            size_bytes=16 * 1024**3,
            duration_seconds=1500.0,
            captured_at=f"2026-08-17T14:{30 + 25 * (index - 1):02d}:00+00:00",
        )

    body = client.post("/api/merge-groups/preview?profile_slug=dji-osmo").json()

    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["output_rel_path"].endswith("_0001-0002_MERGED.MP4")
    assert api_db.execute("SELECT count(*) FROM merge_group").fetchone()[0] == 0


def test_the_selectable_list_reports_when_it_was_truncated(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    for index in (1, 2):
        a_media_file(
            api_db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/T{index}.MP4",
        )
    body = client.get("/api/uploads/selectable?limit=1").json()
    assert len(body["selectable"]) == 1
    assert body["truncated"] is True


def test_profiles_that_do_not_merge_are_not_detected(client, api_db):
    """`archived` ではなく `merge.enabled = false` で確かめる.

    archive は `registry.active()` が先に外すので、`_targets` から
    `merge.enabled` の条件を消しても通ってしまう。
    """
    when = "2026-08-17T00:00:00+00:00"
    profile = ProfileRegistry(api_db).current("dji-osmo")
    definition = json.loads(
        api_db.execute(
            "SELECT definition_json FROM profile_revision WHERE id = ?", (profile.revision_id,)
        ).fetchone()[0]
    )
    definition["slug"] = "no-merge"
    definition["merge"]["enabled"] = False
    api_db.execute(
        "INSERT INTO device_profile (id, slug, name, builtin, created_at)"
        " VALUES ('p-nomerge', 'no-merge', 'No merge', 0, ?)",
        (when,),
    )
    api_db.execute(
        "INSERT INTO profile_revision (id, profile_id, revision, definition_json,"
        " schema_version, created_at) VALUES ('r-nomerge', 'p-nomerge', 1, ?, 1, ?)",
        (json.dumps(definition), when),
    )
    api_db.execute(
        "UPDATE device_profile SET current_revision_id = 'r-nomerge' WHERE id = 'p-nomerge'"
    )

    jobs = client.post("/api/merge-groups/detect").json()["jobs"]
    assert [entry["profile_slug"] for entry in jobs] == ["dji-osmo"]


def test_a_group_can_be_regrouped_by_hand(client, api_db):
    """**再結合は「新しいグループを作って旧を supersede」**（§3）."""
    from .test_schema_artifacts import a_media_file

    profile = ProfileRegistry(api_db).current("dji-osmo")
    profile_ref = (profile.profile_id, profile.revision_id)
    parts = [
        a_media_file(api_db, profile_ref, rel_path=f"library/regroup/PART_{index}.MP4")
        for index in range(3)
    ]
    group_id = a_merge_group(api_db, profile_ref, "digest-regroup")
    for position, media in enumerate(parts):
        api_db.execute(
            "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
            " VALUES (?, ?, ?, 1)",
            (group_id, media, position),
        )

    response = client.patch(
        f"/api/merge-groups/{group_id}?action=regroup", json={"media_ids": parts[:2]}
    )

    assert response.status_code == 200
    new_id = response.json()["group_id"]
    assert client.get(f"/api/merge-groups/{group_id}").json()["superseded_by_id"] == new_id
    assert len(client.get(f"/api/merge-groups/{new_id}").json()["members"]) == 2


def test_a_group_can_be_created_by_hand(client, api_db):
    """検出が拾えなかった並びを人が組む."""
    from .test_schema_artifacts import a_media_file

    profile = ProfileRegistry(api_db).current("dji-osmo")
    profile_ref = (profile.profile_id, profile.revision_id)
    parts = [
        a_media_file(api_db, profile_ref, rel_path=f"library/manual/PART_{index}.MP4")
        for index in range(2)
    ]

    response = client.post("/api/merge-groups", json={"media_ids": parts})

    assert response.status_code == 200
    body = client.get(f"/api/merge-groups/{response.json()['group_id']}").json()
    assert body["detected_by"] == "manual"
    assert len(body["members"]) == 2


def test_a_media_already_in_a_group_cannot_be_grouped_again(client, api_db):
    """1 つのファイルが active な member でいられるのは 1 グループだけ."""
    from .test_schema_artifacts import a_media_file

    profile = ProfileRegistry(api_db).current("dji-osmo")
    profile_ref = (profile.profile_id, profile.revision_id)
    parts = [
        a_media_file(api_db, profile_ref, rel_path=f"library/twice/PART_{index}.MP4")
        for index in range(2)
    ]
    assert client.post("/api/merge-groups", json={"media_ids": parts}).status_code == 200

    again = client.post("/api/merge-groups", json={"media_ids": parts})

    assert again.status_code == 409


def test_regrouping_across_two_groups_answers_409_not_500(client, api_db):
    """実機で 500 になった形. **画面から「2 つを 1 つに」を試すと必ずここを通る.**"""
    from .test_schema_artifacts import a_media_file

    profile = ProfileRegistry(api_db).current("dji-osmo")
    profile_ref = (profile.profile_id, profile.revision_id)
    groups = []
    for name in ("a", "b"):
        parts = [
            a_media_file(api_db, profile_ref, rel_path=f"library/regroup/{name}_{index}.MP4")
            for index in range(2)
        ]
        group_id = a_merge_group(api_db, profile_ref, f"digest-{name}")
        for position, media in enumerate(parts):
            api_db.execute(
                "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
                " VALUES (?, ?, ?, 1)",
                (group_id, media, position),
            )
        groups.append((group_id, parts))

    everyone = groups[0][1] + groups[1][1]
    response = client.patch(
        f"/api/merge-groups/{groups[0][0]}?action=regroup", json={"media_ids": everyone}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_the_list_shows_live_groups_only(client, api_db):
    """**一覧は「いま操作できるもの」だけ。** 履歴は明示的に求めたときだけ出す.

    既定に混ぜると、破棄と組み直しのたびに操作できない行が増え、同じファイル名が
    繰り返し並ぶ。実機で 2 回破棄しただけで 3 つ並び、どれが生きているのか
    読み取れなくなった。
    """
    profile = ProfileRegistry(api_db).current("dji-osmo")
    profile_ref = (profile.profile_id, profile.revision_id)
    live = a_merge_group(api_db, profile_ref, "digest-live")
    dropped = a_merge_group(api_db, profile_ref, "digest-dropped", status="skipped")
    replaced = a_merge_group(api_db, profile_ref, "digest-replaced")
    api_db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (live, replaced))
    # **置き換えられた行は status を指定しても出さない。** 構成そのものが古い。
    gone = a_merge_group(api_db, profile_ref, "digest-gone", status="skipped")
    api_db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (live, gone))

    assert [g["id"] for g in client.get("/api/merge-groups").json()["groups"]] == [live]
    # 履歴は status を指定すれば見える（画面の「破棄した組み合わせ」）。
    assert [g["id"] for g in client.get("/api/merge-groups?status=skipped").json()["groups"]] == [
        dropped
    ]


def test_a_discarded_group_can_be_deleted_from_the_history(client, api_db):
    """画面の「破棄した組み合わせ」から消せる."""
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(
        api_db, (profile.profile_id, profile.revision_id), "digest-x", status="skipped"
    )
    assert client.delete(f"/api/merge-groups/{group_id}").status_code == 200
    assert client.get("/api/merge-groups?status=skipped").json()["groups"] == []


def test_a_live_group_is_not_deletable(client, api_db):
    """生きている候補は破棄が先（消すのは記録だけ）."""
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-y")
    response = client.delete(f"/api/merge-groups/{group_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_the_group_shows_what_it_produced(client, api_db):
    """**「結合済み」だけでは何ができたのか分からない**（ライブラリまで行くことになる）."""
    from .test_schema_artifacts import a_media_file

    profile = ProfileRegistry(api_db).current("dji-osmo")
    ref = (profile.profile_id, profile.revision_id)
    output = a_media_file(
        api_db, ref, rel_path="derived/dji-osmo/DCIM/OUT.MP4", role="derived", size_bytes=42
    )
    group_id = a_merge_group(api_db, ref, "digest-out", status="merged")
    api_db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group_id)
    )
    body = client.get(f"/api/merge-groups/{group_id}").json()
    assert body["output"]["rel_path"] == "derived/dji-osmo/DCIM/OUT.MP4"
    assert body["output"]["size_bytes"] == 42
    assert body["output"]["missing"] is False


def test_a_group_without_an_output_says_so(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-none")
    assert client.get(f"/api/merge-groups/{group_id}").json()["output"] is None
