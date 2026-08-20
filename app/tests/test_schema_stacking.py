"""`0015` のスタック 3 列（§9.11）.

**状態機械には状態を足さない。** 結果は `upload_record` の 3 列で持ち、組み合わせの
不変は trigger で守る（`ALTER TABLE` では表制約を足せない）。
"""

import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.db.connection import Database
from mediaferry.db.migrate import apply_migrations

from .test_schema_artifacts import a_media_file
from .test_schema_sources import a_profile
from .test_schema_uploads import a_destination, an_upload


def a_complete_record(db, **over):
    profile = a_profile(db)
    media = a_media_file(db, profile)
    dest = a_destination(db)
    return an_upload(
        db,
        dest,
        media,
        state="complete",
        origin="created_by_us",
        destination_revision_id=dest[1],
        remote_asset_id="asset-1",
        **over,
    )


def test_a_stacked_record_needs_a_stack_id(db):
    record = a_complete_record(db)
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute("UPDATE upload_record SET stack_state = 'stacked' WHERE id = ?", (record,))


def test_a_stacked_record_must_not_carry_a_reason(db):
    record = a_complete_record(db)
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute(
            "UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = 's',"
            " stack_reason = '理由' WHERE id = ?",
            (record,),
        )


def test_a_skipped_record_needs_a_reason(db):
    record = a_complete_record(db)
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute("UPDATE upload_record SET stack_state = 'skipped' WHERE id = ?", (record,))


def test_a_skipped_record_must_not_carry_a_stack_id(db):
    record = a_complete_record(db)
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute(
            "UPDATE upload_record SET stack_state = 'skipped', stack_reason = '理由',"
            " remote_stack_id = 's' WHERE id = ?",
            (record,),
        )


def test_an_unevaluated_record_must_not_carry_leftovers(db):
    """未評価へ戻すときは理由も消す（消し忘れると画面に古い理由が残る）."""
    record = a_complete_record(db)
    db.execute(
        "UPDATE upload_record SET stack_state = 'skipped', stack_reason = '理由' WHERE id = ?",
        (record,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute("UPDATE upload_record SET stack_state = NULL WHERE id = ?", (record,))


def test_an_unknown_stack_state_is_refused(db):
    record = a_complete_record(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE upload_record SET stack_state = 'ほか' WHERE id = ?", (record,))


def test_an_insert_is_checked_too(db):
    """行を作る側でも見る（**片側だけの trigger は抜け道になる**）."""
    profile = a_profile(db)
    media = a_media_file(db, profile)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        an_upload(
            db,
            dest,
            media,
            state="complete",
            destination_revision_id=dest[1],
            stack_state="stacked",
        )


def test_a_stacked_record_can_go_back_to_needs_recheck(db):
    """**`state = 'complete'` は条件に入れない。**

    再計算の差し戻し（`_requeue`）は `complete` → `needs_recheck` を動かす。
    条件に入れると正当な差し戻しが ABORT する。スタック済みという事実は、
    レコードが再確認へ戻っても真のままである。
    """
    record = a_complete_record(db)
    db.execute(
        "UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = 's' WHERE id = ?",
        (record,),
    )
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE id = ?", (record,))
    assert _row(db, record)["stack_state"] == "stacked"


def test_the_extraction_uses_the_partial_index(db):
    """**索引を足したら EXPLAIN で駆動を確かめる**（Phase 5 の 5・6 巡目の教訓）."""
    plan = db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM upload_record"
        " WHERE destination_id = ? AND target_epoch = ? AND state = 'complete'"
        "   AND stack_state IS NULL AND invalidated_at IS NULL AND id > ?"
        " ORDER BY id LIMIT 50",
        ("d", 1, ""),
    ).fetchall()
    details = " ".join(row["detail"] for row in plan)
    # 鍵が 2 本とも search key に入っていること。索引名だけの一致では、先頭
    # prefix（destination_id だけ）で使われている場合と区別できない。
    assert "upload_record_unstacked (destination_id=? AND target_epoch=?" in details
    # 並べ替えが消えていること（`id` は索引の第 3 列）。
    assert "USE TEMP B-TREE FOR ORDER BY" not in details


def test_the_dead_concurrency_setting_is_removed(tmp_path, monkeypatch):
    """**効かないまま残っていた設定行を消す**（§21）.

    `0014` までを適用した DB に行を入れてから `0015` を当てる。
    """
    import shutil

    from mediaferry.db import migrate as migrate_module
    from mediaferry.db.migrate import MIGRATIONS_DIR

    staged = tmp_path / "migrations"
    staged.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0015":
            shutil.copy(path, staged / path.name)
    monkeypatch.setattr(migrate_module, "MIGRATIONS_DIR", staged)

    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    conn.execute(
        "INSERT INTO app_setting (key, value, updated_at) VALUES ('UPLOAD_CONCURRENCY', '4', ?)",
        (now_iso(),),
    )
    assert _settings_rows(conn) == 1

    shutil.copy(MIGRATIONS_DIR / "0015_stacking.sql", staged / "0015_stacking.sql")
    apply_migrations(conn)
    assert _settings_rows(conn) == 0
    conn.close()


def _settings_rows(conn):
    return conn.execute(
        "SELECT count(*) AS n FROM app_setting WHERE key = 'UPLOAD_CONCURRENCY'"
    ).fetchone()["n"]


def _row(db, record_id):
    return db.execute("SELECT * FROM upload_record WHERE id = ?", (record_id,)).fetchone()


def test_a_stacked_record_must_keep_its_remote_asset(db):
    """**将来の消し忘れを fail-closed にする**（`0016`）.

    スタックは「その `remote_asset_id` を送った結果」なので、ID を消すなら
    結果も一緒に捨てる。
    """
    record = a_complete_record(db)
    db.execute(
        "UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = 's' WHERE id = ?",
        (record,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute("UPDATE upload_record SET remote_asset_id = NULL WHERE id = ?", (record,))


def test_clearing_both_together_is_allowed(db):
    record = a_complete_record(db)
    db.execute(
        "UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = 's' WHERE id = ?",
        (record,),
    )
    db.execute(
        "UPDATE upload_record SET remote_asset_id = NULL, stack_state = NULL,"
        " remote_stack_id = NULL WHERE id = ?",
        (record,),
    )
    assert _row(db, record)["stack_state"] is None


def test_a_skipped_record_may_lose_its_remote_asset(db):
    """見送りは「送らなかった」記録なので、資産 ID とは独立."""
    record = a_complete_record(db)
    db.execute(
        "UPDATE upload_record SET stack_state = 'skipped', stack_reason = '相方が居ない'"
        " WHERE id = ?",
        (record,),
    )
    db.execute("UPDATE upload_record SET remote_asset_id = NULL WHERE id = ?", (record,))
    assert _row(db, record)["stack_state"] == "skipped"
