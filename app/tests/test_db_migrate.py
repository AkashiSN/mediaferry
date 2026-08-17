import sqlite3
import stat

import pytest

from mediaferry.db.connection import Database, immediate
from mediaferry.db.migrate import MigrationError, apply_migrations


def test_connect_sets_wal_and_foreign_keys(tmp_path):
    conn = Database(tmp_path / "var" / "mediaferry.sqlite3").connect()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    conn.close()


def test_every_connect_is_a_separate_connection(tmp_path):
    """トランザクションは接続に属する. スコープごとに 1 本作る."""
    database = Database(tmp_path / "db.sqlite3")
    first, second = database.connect(), database.connect()
    assert first is not second
    apply_migrations(first)
    with immediate(first):
        # 別接続なので、こちらのトランザクションには巻き込まれない
        assert second.execute("SELECT 1").fetchone()[0] == 1
    first.close()
    second.close()


def test_a_connection_can_be_created_in_one_thread_and_used_in_another(tmp_path):
    """asyncio.to_thread はどの worker で走るかを保証しない.

    既定の check_same_thread=True だと、poller の claim_next もハンドラも
    最初の 1 回で ProgrammingError になる。
    """
    import threading

    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    errors = []

    def use():
        try:
            conn.execute("SELECT count(*) FROM schema_migration").fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=use)
    thread.start()
    thread.join(timeout=5)
    assert errors == []
    conn.close()


def test_database_file_is_not_world_readable(tmp_path):
    """API キーの暗号文と履歴が入るので、DB は 0600・親は 0700 にする."""
    path = tmp_path / "var" / "mediaferry.sqlite3"
    conn = Database(path).connect()
    conn.execute("CREATE TABLE t (x)")  # WAL を実際に作らせる
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    wal = path.with_name(path.name + "-wal")
    assert stat.S_IMODE(wal.stat().st_mode) == 0o600
    conn.close()


def test_permissions_are_repaired_on_every_connect(tmp_path):
    """緩い権限で作られた既存 DB をそのまま運用しない."""
    path = tmp_path / "var" / "mediaferry.sqlite3"
    Database(path).connect().close()
    path.chmod(0o644)
    path.parent.chmod(0o755)
    Database(path).connect().close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_apply_migrations_is_idempotent(tmp_path):
    conn = Database(tmp_path / "db.sqlite3").connect()
    first = apply_migrations(conn)
    assert first  # 1 版以上が適用される
    assert apply_migrations(conn) == []
    conn.close()


def test_a_migration_that_manages_its_own_transaction_is_rejected(tmp_path, monkeypatch):
    """トランザクションは runner が所有する. ファイル側が COMMIT すると、
    版の記録と DDL が別トランザクションに割れる."""
    from mediaferry.db import migrate

    bogus = tmp_path / "migrations"
    bogus.mkdir()
    (bogus / "0001_bogus.sql").write_text("BEGIN;\nCREATE TABLE t (x);\nCOMMIT;\n")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", bogus)

    conn = Database(tmp_path / "db.sqlite3").connect()
    with pytest.raises(MigrationError, match="BEGIN"):
        apply_migrations(conn)
    conn.close()


def test_a_trigger_body_is_not_mistaken_for_a_transaction(tmp_path, monkeypatch):
    """trigger は BEGIN ... END; と書く. スキーマの大半が trigger を使う."""
    from mediaferry.db import migrate

    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "0001_trigger.sql").write_text(
        "CREATE TABLE t (x);\n"
        "CREATE TRIGGER t_ro BEFORE UPDATE ON t\n"
        "BEGIN\n"
        "    SELECT RAISE(ABORT, 'immutable');\n"
        "END;\n"
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)

    conn = Database(tmp_path / "db.sqlite3").connect()
    assert apply_migrations(conn) == [1]
    conn.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE t SET x = 2")
    conn.close()


def test_a_failing_migration_leaves_no_partial_schema(tmp_path, monkeypatch):
    from mediaferry.db import migrate

    bogus = tmp_path / "migrations"
    bogus.mkdir()
    (bogus / "0001_bad.sql").write_text("CREATE TABLE ok (x);\nCREATE TABLE ok (x);\n")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", bogus)

    conn = Database(tmp_path / "db.sqlite3").connect()
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(conn)
    assert conn.in_transaction is False
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'ok'").fetchone()[0] == 0
    conn.close()


def test_editing_an_applied_migration_is_refused(tmp_path, monkeypatch):
    """適用済みの版を書き換えると、環境ごとにスキーマが食い違う."""
    from mediaferry.db import migrate

    folder = tmp_path / "migrations"
    folder.mkdir()
    path = folder / "0001_first.sql"
    path.write_text("CREATE TABLE t (x);\n")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)

    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    path.write_text("CREATE TABLE t (x, y);\n")
    with pytest.raises(MigrationError, match="0001_first.sql"):
        apply_migrations(conn)
    conn.close()


def test_immediate_rolls_back_on_error(db):
    db.execute("CREATE TABLE t (x INTEGER)")
    with pytest.raises(ValueError), immediate(db):
        db.execute("INSERT INTO t VALUES (1)")
        raise ValueError("boom")
    assert db.execute("SELECT count(*) FROM t").fetchone()[0] == 0


def test_immediate_takes_the_write_lock_immediately(tmp_path):
    """BEGIN IMMEDIATE でないと、後から昇格するときに SQLITE_BUSY で失敗しうる."""
    database = Database(tmp_path / "db.sqlite3")
    a = database.connect()
    apply_migrations(a)
    b = database.connect()
    b.execute("PRAGMA busy_timeout = 0")
    with immediate(a), pytest.raises(sqlite3.OperationalError):
        b.execute("BEGIN IMMEDIATE")
    a.close()
    b.close()
