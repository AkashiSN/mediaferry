import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.db.uploads import CLAIMABLE_CLAUSE, CLAIMABLE_STATES
from mediaferry.ids import new_id

from .test_schema_artifacts import a_media_file
from .test_schema_jobs import a_job
from .test_schema_sources import a_profile


def a_destination(db, name="home", epoch=1):
    dest_id, cred_id, rev_id = new_id(), new_id(), new_id()
    db.execute(
        "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
        " VALUES (?, ?, 'immich', 1, ?)",
        (dest_id, name, now_iso()),
    )
    db.execute(
        "INSERT INTO destination_credential"
        " (id, destination_id, revision, secret_encrypted, key_fingerprint, created_at)"
        " VALUES (?, ?, 1, X'00', 'kf', ?)",
        (cred_id, dest_id, now_iso()),
    )
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch, base_url,"
        " credential_id, created_at) VALUES (?, ?, 1, ?, 'http://immich.invalid', ?, ?)",
        (rev_id, dest_id, epoch, cred_id, now_iso()),
    )
    db.execute(
        "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?", (rev_id, dest_id)
    )
    return dest_id, rev_id, cred_id


def an_upload(db, dest, media_id, **over):
    dest_id, rev_id, _ = dest
    row = {
        "id": new_id(),
        "destination_id": dest_id,
        "target_epoch": 1,
        "media_file_id": media_id,
        "state": "pending",
        "selection_rule": "default",
        "origin": "unknown",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO upload_record ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_destination_revision_is_immutable(db):
    _, rev_id, _ = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE destination_revision SET base_url = 'http://x.invalid' WHERE id = ?",
            (rev_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM destination_revision WHERE id = ?", (rev_id,))


def test_a_revision_cannot_borrow_another_destinations_credential(db):
    """他宛先の鍵で送ると、確認画面と違う先へ資産が渡る."""
    first = a_destination(db, name="a")
    _, _, other_cred = a_destination(db, name="b")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
            " base_url, credential_id, created_at)"
            " VALUES (?, ?, 2, 1, 'http://immich.invalid', ?, ?)",
            (new_id(), first[0], other_cred, now_iso()),
        )


def test_current_revision_must_belong_to_the_destination(db):
    first = a_destination(db, name="a")
    _, other_rev, _ = a_destination(db, name="b")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
            (other_rev, first[0]),
        )


def test_upload_record_revision_must_match_destination_and_epoch(db):
    """epoch を跨いだ revision を掴むと、進めた向き先の履歴が混ざる."""
    profile = a_profile(db)
    dest = a_destination(db)
    media_id = a_media_file(db, profile)
    an_upload(db, dest, media_id, destination_revision_id=dest[1])
    another_revision(db, dest, epoch=2)
    with pytest.raises(sqlite3.IntegrityError):
        # epoch 2 の行に epoch 1 の revision を掴ませる
        an_upload(
            db, dest, a_media_file(db, profile), target_epoch=2, destination_revision_id=dest[1]
        )


def another_revision(db, dest, epoch):
    """向き先を変えて epoch を進めた新しいリビジョン."""
    dest_id, _, cred_id = dest
    rev_id = new_id()
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch, base_url,"
        " credential_id, created_at) VALUES (?, ?, 2, ?, 'http://other.invalid', ?, ?)",
        (rev_id, dest_id, epoch, cred_id, now_iso()),
    )
    return rev_id


def test_one_record_per_destination_epoch_and_media(db):
    """**これを守っているのは部分 UNIQUE 索引 `upload_record_live_identity`**.

    索引の述語は `WHERE invalidated_at IS NULL` なので、一意なのは**有効な行**。
    無効化された行は監査履歴として、同じ組に何行あってもよい
    （`test_an_invalidated_row_can_sit_beside_a_live_one`）。
    """
    profile = a_profile(db)
    dest = a_destination(db)
    media_id = a_media_file(db, profile)
    an_upload(db, dest, media_id)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, media_id)
    # epoch を進めれば、旧記録を監査履歴として残したまま送り直せる
    another_revision(db, dest, epoch=2)
    an_upload(db, dest, media_id, target_epoch=2)


def test_an_invalidated_row_can_sit_beside_a_live_one(db):
    """**守る不変条件は「有効な記録は 1 組につき高々 1 つ」。**

    消滅を無効化して送り直すと、同じ (宛先, epoch, メディア) に行が 2 つ並ぶ。
    無効化された方は監査履歴で、有効なのは新しい方だけ。
    """
    profile = a_profile(db)
    dest = a_destination(db)
    media = a_media_file(db, profile)
    old = an_upload(db, dest, media, state="complete", destination_revision_id=dest[1])
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'remote_missing'"
        " WHERE id = ?",
        (now_iso(), old),
    )

    fresh = an_upload(db, dest, media)

    live = db.execute("SELECT id FROM upload_record WHERE invalidated_at IS NULL").fetchall()
    assert [row["id"] for row in live] == [fresh]


def test_the_upload_record_schema_survives_the_rebuild(db):
    """**作り直しで guard を落としていないこと。**

    表を作り直すと trigger も索引も一緒に消える。**明示の一覧と突き合わせる**
    ——「たぶん全部作り直した」では、消えた guard に気づけない。
    """
    found = {
        (row["type"], row["name"])
        for row in db.execute(
            "SELECT type, name FROM sqlite_master WHERE tbl_name = 'upload_record'"
            "   AND type IN ('trigger', 'index') AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert found == {
        ("trigger", "upload_record_epoch_must_exist"),
        ("trigger", "upload_record_identity_is_immutable"),
        ("trigger", "upload_record_selection_rule_immutable"),
        ("trigger", "upload_record_first_check_immutable"),
        ("trigger", "upload_record_stack_shape_insert"),
        ("trigger", "upload_record_stack_shape_update"),
        ("trigger", "upload_record_stacked_needs_its_asset"),
        ("trigger", "upload_record_stacked_needs_its_asset_insert"),
        ("index", "upload_record_by_media"),
        ("index", "upload_record_claimable"),
        ("index", "upload_record_unstacked"),
        ("index", "upload_record_live_pair"),
        ("index", "upload_record_live_identity"),
    }

    # **CHECK と FK は個数だけを見る。** 一致しても中身が違えば通ってしまうが、
    # 作り直しで丸ごと写し落とすことは捕まえられる。文字列を丸ごと固定すると、
    # 表を作り直すたびに無思考で更新される側へ倒れる。
    #
    # FK 5 本のうち複合の 1 本が 3 列なので、`foreign_key_list` は 7 行を返す。
    fk_rows = db.execute("PRAGMA foreign_key_list('upload_record')").fetchall()
    assert len(fk_rows) == 7
    # 表制約 4 個 + 列の CHECK 6 個（state / selection_rule / origin /
    # first_check_result / remote_is_trashed / stack_state）。
    table_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'upload_record'"
    ).fetchone()["sql"]
    assert table_sql.count("CHECK") == 10


def test_a_record_cannot_name_an_epoch_that_has_no_revision(db):
    """複合 FK は destination_revision_id が NULL だと効かない."""
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError, match="epoch"):
        an_upload(db, dest, a_media_file(db, profile), target_epoch=7)


def test_the_identity_of_a_record_cannot_be_rewritten(db):
    """書き換えられると、INSERT 時の epoch guard も複合 FK も迂回できる."""
    profile = a_profile(db)
    dest = a_destination(db)
    record_id = an_upload(db, dest, a_media_file(db, profile))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE upload_record SET target_epoch = 9 WHERE id = ?", (record_id,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE upload_record SET media_file_id = ? WHERE id = ?",
            (a_media_file(db, profile), record_id),
        )


def test_an_active_record_must_name_its_owner_and_revision(db):
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), state="uploading")


def test_a_terminal_record_must_not_keep_a_claim(db):
    profile = a_profile(db)
    dest = a_destination(db)
    job_id = a_job(db, type="upload")
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(
            db,
            dest,
            a_media_file(db, profile),
            state="complete",
            destination_revision_id=dest[1],
            claim_job_id=job_id,
            claim_token="t",
            claim_expires_at=now_iso(),
        )


def test_a_complete_record_remembers_which_revision_it_used(db):
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), state="complete")
    an_upload(
        db, dest, a_media_file(db, profile), state="complete", destination_revision_id=dest[1]
    )


def test_claim_columns_are_all_null_or_all_set(db):
    """未来の期限だけが残ると、明示操作しても期限まで claim できなくなる."""
    profile = a_profile(db)
    dest = a_destination(db)
    job_id = a_job(db, type="upload")
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(
            db,
            dest,
            a_media_file(db, profile),
            state="checking",
            destination_revision_id=dest[1],
            claim_job_id=job_id,
        )
    an_upload(
        db,
        dest,
        a_media_file(db, profile),
        state="checking",
        destination_revision_id=dest[1],
        claim_job_id=job_id,
        claim_token="t",
        claim_expires_at=now_iso(),
    )


def test_selection_rule_cannot_be_rewritten(db):
    """再試行で上書きすると、なぜ送信を許可したかが失われる."""
    profile = a_profile(db)
    dest = a_destination(db)
    record_id = an_upload(db, dest, a_media_file(db, profile))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE upload_record SET selection_rule = 'adopted_derived' WHERE id = ?",
            (record_id,),
        )
    # 状態を進めること自体は妨げない
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE id = ?", (record_id,))


def test_first_check_result_is_write_once(db):
    """初回 checking の結果は pre_existing の証明に使う。書き換えられては困る."""
    profile = a_profile(db)
    dest = a_destination(db)
    record_id = an_upload(db, dest, a_media_file(db, profile))
    db.execute("UPDATE upload_record SET first_check_result = 'reject' WHERE id = ?", (record_id,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE upload_record SET first_check_result = 'accept' WHERE id = ?", (record_id,)
        )


def test_states_and_origins_are_constrained(db):
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), state="uploaded")
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), origin="probably_ours")


def test_purged_credentials_keep_only_the_fingerprint(db):
    _, _, cred_id = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE destination_credential SET purged_at = ? WHERE id = ?", (now_iso(), cred_id)
        )
    db.execute(
        "UPDATE destination_credential SET secret_encrypted = NULL, purged_at = ? WHERE id = ?",
        (now_iso(), cred_id),
    )


def _plan(db, sql, params):
    return " | ".join(row["detail"] for row in db.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_claiming_still_drives_off_the_claimable_index(db):
    """**索引を足したら EXPLAIN で駆動を確かめる**（Phase 5 の 5・6 巡目の教訓）.

    `upload_record_live_identity` は部分索引（`WHERE invalidated_at IS NULL`）で、
    `upload_record_claimable` と述語が同じ。先頭に `destination_id` を置くと
    先頭 2 列が等値で当たり、SQLite が `claimable` より優先する。そうなると
    claim は「pending の行だけを辿る」から「その宛先・epoch の**有効な全行**
    （complete を含む）を辿って state で捨てる」へ落ちる。claim はファイル
    1 本ごとに走るので、同期 1 回が O(N^2) になる（complete 5 万 + pending 5 件で、
    SELECT 20 回が 0.000 s → 0.095 s）。**`ANALYZE` はどこでも取っていない**ので、
    悪い計画はそのまま実機に出る。

    **`claim_next` が実際に組み立てる WHERE を使う**（`CLAIMABLE_CLAUSE`）。
    手で書き写すと、条件が変わったときにこの退行を捕まえられない。
    """
    plan = _plan(
        db,
        f"SELECT id FROM upload_record WHERE {CLAIMABLE_CLAUSE}"  # noqa: S608 - 値は params
        "   AND (claim_expires_at IS NULL OR claim_expires_at < ?)"
        " ORDER BY created_at LIMIT 1",
        ("d", 1, *CLAIMABLE_STATES, now_iso()),
    )
    # 索引名だけでなく search key も見る。`state` が鍵に入っていなければ、
    # 名前が合っていても「全行を辿って捨てる」計画のままになりうる。
    assert "upload_record_claimable (destination_id=? AND state=?)" in plan, plan
    assert "upload_record_live_identity" not in plan, plan


def test_the_progress_denominator_still_drives_off_the_claimable_index(db):
    """`sendable_totals`。`claim_next` と同じ `CLAIMABLE_CLAUSE` を使う."""
    plan = _plan(
        db,
        "SELECT COUNT(*) AS files, COALESCE(SUM(media_file.size_bytes), 0) AS bytes"  # noqa: S608
        " FROM upload_record JOIN media_file ON media_file.id = upload_record.media_file_id"
        f" WHERE {CLAIMABLE_CLAUSE}",
        ("d", 1, *CLAIMABLE_STATES),
    )
    assert "upload_record_claimable (destination_id=? AND state=?)" in plan, plan
    assert "upload_record_live_identity" not in plan, plan


def test_the_recheck_scan_still_drives_off_the_claimable_index(db):
    """`records_for_recheck`。現行 epoch の `complete` を全件返す."""
    plan = _plan(
        db,
        "SELECT * FROM upload_record WHERE destination_id = ? AND target_epoch = ?"
        "   AND state = 'complete' AND invalidated_at IS NULL"
        " ORDER BY created_at",
        ("d", 1),
    )
    assert "upload_record_claimable (destination_id=? AND state=?)" in plan, plan
    assert "upload_record_live_identity" not in plan, plan
