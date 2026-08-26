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
    """結合を持つビルトインの数だけジョブが立つ（canon-eos と dji-osmo）."""
    response = client.post("/api/merge-groups/detect")
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert sorted(entry["profile_slug"] for entry in jobs) == ["canon-eos", "dji-osmo"]


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
    # `no-merge`（merge.enabled = false）は入らない。canon-eos と dji-osmo は
    # どちらも merge.enabled = true なので両方入る。
    assert sorted(entry["profile_slug"] for entry in jobs) == ["canon-eos", "dji-osmo"]


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


def test_regrouping_down_to_one_part_is_refused(client, api_db):
    """**つなぐ組は 2 件以上。** 手で作るときと同じ条件を、組み直しにも課す.

    1 件だけの組にはつなぎ目が無く、画面は「なぜ同じ 1 本と判断したか」を
    書けない（`Merge.tsx` の `gapSeconds`）。作れてしまうと、つなぎようのない
    組が一覧に残る。
    """
    from .test_schema_artifacts import a_media_file

    profile = ProfileRegistry(api_db).current("dji-osmo")
    profile_ref = (profile.profile_id, profile.revision_id)
    parts = [
        a_media_file(api_db, profile_ref, rel_path=f"library/shrink/PART_{index}.MP4")
        for index in range(2)
    ]
    group_id = a_merge_group(api_db, profile_ref, "digest-shrink")
    for position, media in enumerate(parts):
        api_db.execute(
            "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
            " VALUES (?, ?, ?, 1)",
            (group_id, media, position),
        )

    response = client.patch(
        f"/api/merge-groups/{group_id}?action=regroup", json={"media_ids": parts[:1]}
    )

    assert response.status_code == 400
    # **画面が理由を出せる code で断る**（§13）。`missing_field` は「必要な項目が
    # 足りません」としか出ず、項目はあって短いだけ、という事実が落ちる。
    error = response.json()["error"]
    assert error["code"] == "bad_request"
    assert error["detail"]
    assert client.get(f"/api/merge-groups/{group_id}").json()["superseded_by_id"] is None


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


def test_pending_leaves_out_the_groups_that_are_already_merged(client, api_db):
    """つなぐ画面は「まだつないでいないもの」だけを出す（Phase 11）.

    **結合済みを混ぜない。** つなぎ目の空白の判定は結合の前後を区別しないので、
    済んだ組にも警告色と「確かめてから決めてください」が出る —— もう決めた後の
    ものに、決める前の文が出ることになる。結合済みは写真タブで見る。

    **`failed` は残す。** まだつながっていない側で、結合に失敗した組の member を
    個別に送る入口はつなぐ画面にしかない（裁定 12）。
    """
    profile = ProfileRegistry(api_db).current("dji-osmo")
    profile_ref = (profile.profile_id, profile.revision_id)
    waiting = a_merge_group(api_db, profile_ref, "digest-waiting")
    failed = a_merge_group(api_db, profile_ref, "digest-failed", status="failed")
    a_merge_group(api_db, profile_ref, "digest-done", status="merged")
    a_merge_group(api_db, profile_ref, "digest-dropped2", status="skipped")

    listed = client.get("/api/merge-groups?pending=true").json()["groups"]

    assert sorted(g["id"] for g in listed) == sorted([waiting, failed])


def test_pending_and_status_are_not_combined(client, api_db):
    """**両方は指定できない.** 黙って片方を無視すると、画面が読み違える."""
    assert client.get("/api/merge-groups?pending=true&status=skipped").status_code == 400


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


def test_a_group_made_after_the_profile_changed_uses_the_current_revision(client, api_db):
    """**手で作る組も、検出と同じリビジョンで指紋を作る**（§8 / §10）.

    取り込んだときの版で指紋を作ると、カメラの種類を保存したあとに手で組んだ
    ものは生まれた瞬間から食い違う。`group_is_current` は現行の版で計算し直す
    ので、`POST /uploads` はその出力を永久に断り、`SENDABLE_CLAUSE` の
    「現行の版か」も食い違ったまま数え続ける（押しても消せない数になる）。
    """
    from dataclasses import replace

    from mediaferry.db.selection import expected_digest

    registry = ProfileRegistry(api_db)
    mine = registry.duplicate("dji-osmo", "rev-cam", "版のカメラ")
    profile_ref = (mine.profile_id, mine.revision_id)
    parts = [
        a_media_file(api_db, profile_ref, rel_path=f"library/rev/PART_{index}.MP4")
        for index in range(2)
    ]
    # 取り込んだあとにカメラの種類を保存する（版が上がる）。
    registry.update("rev-cam", replace(mine.definition, name="名前を変えた"))
    current = ProfileRegistry(api_db).current("rev-cam")

    group_id = client.post("/api/merge-groups", json={"media_ids": parts}).json()["group_id"]

    row = api_db.execute(
        "SELECT profile_revision_id, input_digest FROM merge_group WHERE id = ?", (group_id,)
    ).fetchone()
    assert row["profile_revision_id"] == current.revision_id
    assert row["input_digest"] == expected_digest(api_db, ProfileRegistry(api_db), group_id)


def test_regrouping_after_the_profile_changed_uses_the_current_revision(client, api_db):
    """組み直しも同じ。**旧グループの版を複写しない。**

    複写すると、`SENDABLE_CLAUSE` の「現行の版か」は通るのに `group_is_current`
    は通らない組ができる —— 数には出るのに、送ろうとすると必ず断られる。
    """
    from dataclasses import replace

    from mediaferry.db.selection import expected_digest

    registry = ProfileRegistry(api_db)
    mine = registry.duplicate("dji-osmo", "regroup-cam", "組み直しのカメラ")
    profile_ref = (mine.profile_id, mine.revision_id)
    parts = [
        a_media_file(api_db, profile_ref, rel_path=f"library/regroup-rev/PART_{index}.MP4")
        for index in range(3)
    ]
    group_id = a_merge_group(api_db, profile_ref, "digest-regroup-rev")
    for position, media in enumerate(parts):
        api_db.execute(
            "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
            " VALUES (?, ?, ?, 1)",
            (group_id, media, position),
        )
    registry.update("regroup-cam", replace(mine.definition, name="名前を変えた"))
    current = ProfileRegistry(api_db).current("regroup-cam")

    new_id = client.patch(
        f"/api/merge-groups/{group_id}?action=regroup", json={"media_ids": parts[:2]}
    ).json()["group_id"]

    row = api_db.execute(
        "SELECT profile_revision_id, input_digest FROM merge_group WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["profile_revision_id"] == current.revision_id
    assert row["input_digest"] == expected_digest(api_db, ProfileRegistry(api_db), new_id)


def test_a_group_says_when_its_camera_type_changed_since_it_was_made(client, api_db):
    """**版が上がった組は、画面がそれと分かる形で受け取る**（§13）.

    版が上がると `group_is_current` は必ず断るので、「中身を見て、これを使う」を
    押しても送れるようにはならない。画面がその区別を持てないと、押しても何も
    起きないボタンが残る。
    """
    import json
    from dataclasses import replace

    registry = ProfileRegistry(api_db)
    mine = registry.duplicate("dji-osmo", "stale-cam", "版が上がるカメラ")
    profile_ref = (mine.profile_id, mine.revision_id)
    output = a_media_file(api_db, profile_ref, rel_path="library/stale/OUT.MP4", role="derived")
    group_id = a_merge_group(
        api_db,
        profile_ref,
        "digest-stale-view",
        status="merged",
        verification_json=json.dumps({"passed": False}),
        output_media_file_id=output,
    )

    assert client.get(f"/api/merge-groups/{group_id}").json()["profile_changed"] is False

    registry.update("stale-cam", replace(mine.definition, name="名前を変えた"))

    assert client.get(f"/api/merge-groups/{group_id}").json()["profile_changed"] is True
    listed = client.get("/api/merge-groups").json()["groups"]
    assert [row["profile_changed"] for row in listed if row["id"] == group_id] == [True]
