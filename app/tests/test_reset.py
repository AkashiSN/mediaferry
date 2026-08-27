"""リセット（Phase 11 の R13）.

**Phase 9 の削除の規則はここには掛からない**（`docs/history/phase11-design.md` の 6）。
`DELETE /media/{id}` が守るのは「Immich にしか無いものを mediaferry から消させない」
で、1 件ずつ判断している場面の不変条件。リセットは**mediaferry が持っているものを
捨てる操作**で、Immich にある資産は対象ではない —— 消しに行かないし、消えない。

**段は 4 つで、取り消せなさが違う。** 送信の記録を捨てると `origin` が
`pre_existing` に落ち、`first_check_result` は不変なので二度と戻らない（§9.10）。
「作業の記録」と同じ段に置かない。
"""

from __future__ import annotations

import pytest

from mediaferry.clock import now_iso
from mediaferry.db.connection import Database
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.ids import new_id

from .test_schema_artifacts import a_media_file, a_merge_group, a_source_entry, a_staging
from .test_schema_sources import a_volume
from .test_schema_uploads import a_destination, an_upload


@pytest.fixture
def api_db(client, data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


@pytest.fixture
def ref(api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    return profile.profile_id, profile.revision_id


def a_job(db, status: str = "succeeded") -> str:
    job_id = new_id()
    db.execute(
        "INSERT INTO job (id, type, status, params_json, created_at)"
        " VALUES (?, 'scan', ?, '{}', ?)",
        (job_id, status, now_iso()),
    )
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
        (job_id, now_iso()),
    )
    return job_id


def count(db, table: str) -> int:
    return db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608


def a_published_staging(db, job_id: str, ref) -> str:
    """**公開が完了した後の形。** `artifact_staging` の行は `published` のまま残り、
    `job_id NOT NULL REFERENCES job(id) ON DELETE RESTRICT` で `job` を掴んでいる.
    """
    # **`client` fixture が既定の DJI ボリューム（`fs_uuid="26B1-2FD6"`）を持つ。**
    # `a_volume` の既定値のままだと UNIQUE (fs_uuid, fs_type, size_bytes) が衝突する。
    entry = a_source_entry(db, a_volume(db, ref, fs_uuid="RESET-TEST-PUBLISHED"))
    return a_staging(
        db,
        job_id,
        state="published",
        source_entry_id=entry,
        final_rel_path="library/dji-osmo/DCIM/PUBLISHED.MP4",
        expected_size=1,
        content_sha1="0" * 40,
        metadata_json="{}",
    )


# ------------------------------------------------------------------ 段


def test_resetting_the_job_records_keeps_everything_else(client, api_db, ref):
    """いちばん浅い段。**作り直せるものだけ**を捨てる."""
    a_job(api_db)
    media = a_media_file(api_db, ref, rel_path="library/A.MP4")
    destination = a_destination(api_db)
    an_upload(api_db, destination, media)

    assert client.post("/api/reset", json={"scope": "jobs"}).status_code == 200

    assert count(api_db, "job") == 0
    assert count(api_db, "job_event") == 0
    assert count(api_db, "upload_record") == 1
    assert count(api_db, "media_file") == 1


def test_resetting_the_upload_records_keeps_the_library(client, api_db, ref):
    """送信の記録だけを捨てる段。**ファイルもその記録も消えない.**"""
    media = a_media_file(api_db, ref, rel_path="library/B.MP4")
    destination = a_destination(api_db)
    an_upload(api_db, destination, media)

    assert client.post("/api/reset", json={"scope": "uploads"}).status_code == 200

    assert count(api_db, "upload_record") == 0
    assert count(api_db, "media_file") == 1
    # **送り先そのものは設定。** 取り込んだデータではないので残す。
    assert count(api_db, "upload_destination") == 1


def test_resetting_the_library_removes_the_files_and_their_records(client, api_db, ref, data_root):
    """取り込んだファイルの段。**実体も記録も消える.**"""
    media = a_media_file(api_db, ref, rel_path="library/dji-osmo/DCIM/C.MP4")
    group = a_merge_group(api_db, ref, "digest-reset", status="merged")
    api_db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (media, group))
    on_disk = data_root / "library" / "dji-osmo" / "DCIM" / "C.MP4"
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_bytes(b"x")

    assert client.post("/api/reset", json={"scope": "library"}).status_code == 200

    assert count(api_db, "media_file") == 0
    assert count(api_db, "merge_group") == 0
    assert not on_disk.exists()
    # **置き場所そのものは残す。** 次の取り込みが作り直す手間を増やさない。
    assert (data_root / "library").is_dir()


def test_resetting_everything_also_forgets_the_cards(client, api_db, ref):
    """いちばん深い段。**カードの記録も消す**（信頼の記録もここで消える）."""
    a_volume(api_db, fs_uuid="9999-9999")

    assert client.post("/api/reset", json={"scope": "all"}).status_code == 200

    assert count(api_db, "volume_instance") == 0
    # **送り先とカメラの種類は残す。** どちらも取り込んだデータではなく設定で、
    # 消すと接続をやり直すことになる（API キーも失われる）。
    assert count(api_db, "device_profile") > 0


def test_the_deeper_stage_includes_the_shallower_one(client, api_db, ref):
    """**段は積み上げ.** 深い段は浅い段を含む —— 別々に押させない."""
    a_job(api_db)
    media = a_media_file(api_db, ref, rel_path="library/D.MP4")
    destination = a_destination(api_db)
    an_upload(api_db, destination, media)

    assert client.post("/api/reset", json={"scope": "library"}).status_code == 200

    assert count(api_db, "job") == 0
    assert count(api_db, "upload_record") == 0
    assert count(api_db, "media_file") == 0


# ------------------------------------------------------------------ 断り


def test_a_reset_is_refused_while_a_job_is_running(client, api_db):
    """**走っている仕事の足元を外さない.**

    `artifact_staging` が指している `source_entry` は `ON DELETE RESTRICT` で
    消せないし、消せたとしても走っている取り込みが書き込み先を失う。
    """
    job_id = a_job(api_db, status="running")

    response = client.post("/api/reset", json={"scope": "all"})

    assert response.status_code == 409
    # **何も消していない。** 断ったのに一部だけ消えていると、次に押す判断ができない。
    # **全体の件数では見ない** —— 監視がこの間にスキャンを積むことがある。
    assert api_db.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone() is not None


def test_an_unknown_scope_is_refused(client):
    """**知らない段は受け取らない.** 黙って浅い方へ倒すと、消えない話になる."""
    assert client.post("/api/reset", json={"scope": "everything"}).status_code == 400


# ------------------------------------------------------------------ 公開済みの staging


@pytest.mark.parametrize("scope", ["jobs", "uploads", "library", "all"])
def test_a_reset_works_after_something_was_published(client, api_db, ref, scope):
    """**一度でも公開した DB でリセットが通る.**

    `artifact_staging` は公開後も `published` のまま残り、`job` を
    `ON DELETE RESTRICT` で掴む。`job` を先に消すと 4 段すべてが外部キーで落ちる。
    """
    job_id = a_job(api_db)
    a_published_staging(api_db, job_id, ref)
    api_db.commit()

    response = client.post("/api/reset", json={"scope": scope})
    assert response.status_code == 200
    assert count(api_db, "job") == 0
    assert count(api_db, "artifact_staging") == 0
    if scope in ("library", "all"):
        # **`library` の段の `DELETE FROM artifact_staging` は 0 件になる**
        # （手順 1 で `published` の行を消し切っているので）。ここで `+=` の
        # 代わりに `=` を使うと、手順 1 の分の報告が 0 で上書きされる —— 実際に
        # 消えた行数と食い違う。
        assert response.json()["removed"]["artifact_staging"] == 1


def test_a_reset_is_refused_while_a_publish_waits_to_be_recovered(client, api_db, ref):
    """**回収待ちの取り込みは消さずに断る.**

    `writing` / `staged` の行は中断した公開の復旧に要る。黙って消すと戻せない。
    """
    job_id = a_job(api_db)
    # 既定の fs_uuid は `client` fixture の既定ボリュームと衝突する（上と同じ注意）。
    entry = a_source_entry(api_db, a_volume(api_db, ref, fs_uuid="RESET-TEST-PENDING"))
    a_staging(
        api_db,
        job_id,
        state="staged",
        source_entry_id=entry,
        final_rel_path="library/dji-osmo/DCIM/HALF.MP4",
        expected_size=1,
        content_sha1="0" * 40,
        metadata_json="{}",
    )
    api_db.commit()

    response = client.post("/api/reset", json={"scope": "jobs"})
    assert response.status_code == 409
    # **この 2 行が消えていないことだけを見る。** 断ったのに片付いていると、
    # 次の判断ができない。**全体の件数では見ない**
    # （`test_a_reset_is_refused_while_a_job_is_running` と同じ注意）——
    # `client` fixture の既定ボリュームを監視が自動でスキャンし、別の `job` を
    # 積むことがある（実測: フルスイート実行時に発生し、件数一致の断言は
    # flaky だった）。
    assert api_db.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone() is not None
    assert (
        api_db.execute("SELECT 1 FROM artifact_staging WHERE state = 'staged'").fetchone()
        is not None
    )


def test_a_refused_reset_says_which_thing_to_wait_for(client, api_db, ref):
    """**理由ごとに `code` を分ける.** 画面は `code` で文面を選ぶので、
    どちらも `job_in_flight` だと「何を待てばよいか」が読めない。
    """
    job_id = a_job(api_db)
    entry = a_source_entry(api_db, a_volume(api_db, ref, fs_uuid="RESET-TEST-CODE-STAGING"))
    a_staging(
        api_db,
        job_id,
        state="staged",
        source_entry_id=entry,
        final_rel_path="library/dji-osmo/DCIM/HALF.MP4",
        expected_size=1,
        content_sha1="0" * 40,
        metadata_json="{}",
    )
    api_db.commit()
    body = client.post("/api/reset", json={"scope": "jobs"}).json()
    assert body["error"]["code"] == "staging_pending"


def test_a_running_job_still_reports_job_in_flight(client, api_db):
    """走っている作業の側は `job_in_flight` のまま（既存の文言を変えない）."""
    a_job(api_db, status="running")
    api_db.commit()
    body = client.post("/api/reset", json={"scope": "jobs"}).json()
    assert body["error"]["code"] == "job_in_flight"
