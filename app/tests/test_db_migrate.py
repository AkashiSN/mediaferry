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


def test_an_existing_raw_remote_id_is_converted_to_a_fingerprint(tmp_path):
    """**指紋化より前に作られた DB の平文を残さない**（§12.3）.

    残ると、相手が API キーを echo していた場合にその平文が DB に居座り、
    一覧の API 応答にも出続ける。`preflight` も生値と指紋を比べて、
    変わっていない向き先を「変わった」と誤判定する。
    """
    from mediaferry.core.destinations.identity import fingerprint

    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    _a_destination_revision(conn, "user-a")

    # 指紋化の版が入る前の DB を作る（適用の記録を外し、生値へ戻す）。
    conn.execute("DELETE FROM schema_migration WHERE version = 5")
    conn.execute("DROP TRIGGER destination_revision_no_update")
    conn.execute("UPDATE destination_revision SET remote_user_id = 'user-a'")
    # 古い DB では trigger が居るところから始まる。
    conn.execute(
        "CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision"
        " BEGIN SELECT RAISE(ABORT, 'destination_revision is immutable'); END"
    )

    assert apply_migrations(conn) == [5]

    assert conn.execute("SELECT remote_user_id FROM destination_revision").fetchone()[0] == (
        fingerprint("user-a")
    )
    # 版を戻した trigger も元どおり（リビジョンは不変のまま）。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE destination_revision SET remote_user_id = 'x'")
    conn.close()


def _a_destination_revision(conn, user_id, suffix="1"):
    """転送先とリビジョンを 1 つ作る（repository を通さず、最小の行だけ）."""
    when = "2026-08-18T00:00:00+00:00"
    destination, credential, revision = f"d-{suffix}", f"c-{suffix}", f"r-{suffix}"
    conn.execute(
        "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
        " VALUES (?, ?, 'immich', 1, ?)",
        (destination, f"home-{suffix}", when),
    )
    conn.execute(
        "INSERT INTO destination_credential (id, destination_id, revision, secret_encrypted,"
        " key_fingerprint, created_at) VALUES (?, ?, 1, X'00', 'fp', ?)",
        (credential, destination, when),
    )
    conn.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
        " base_url, public_url, credential_id, remote_user_id, server_instance_id,"
        " verified_at, created_at)"
        " VALUES (?, ?, 1, 1, 'http://immich.invalid:2283', NULL, ?, ?, NULL, ?, ?)",
        (revision, destination, credential, user_id, when, when),
    )
    conn.execute(
        "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
        (revision, destination),
    )
    return destination, revision


def test_a_value_that_is_already_a_fingerprint_is_not_hashed_again(tmp_path):
    """**生値と指紋が混ざった DB が正規に作れる**（§12.3）.

    指紋化を入れた版のアプリは新しいリビジョンを指紋で保存するが、この版が
    無ければ `schema_migration` はまだ 4。そこへ版 5 を当てたとき、指紋をもう
    一度ハッシュすると観測値の一重指紋と永久に一致せず、その宛先が恒久的に
    拒否される。**指紋であることは形の推定ではなく接頭辞で分かる**（生の
    観測値と見分けが付かない形にすると、鍵をそのまま残す経路が開く）。
    """
    from mediaferry.core.destinations.identity import fingerprint

    conn = Database(tmp_path / "mixed.sqlite3").connect()
    apply_migrations(conn)
    _a_destination_revision(conn, "user-a", suffix="1")
    _a_destination_revision(conn, fingerprint("user-b"), suffix="2")

    # 版 5 が入る前の DB にする（適用の記録を外し、生値の行を作り直す）。
    conn.execute("DELETE FROM schema_migration WHERE version = 5")
    conn.execute("DROP TRIGGER destination_revision_no_update")
    conn.execute("UPDATE destination_revision SET remote_user_id = 'user-a' WHERE id = 'r-1'")
    conn.execute(
        "UPDATE destination_revision SET remote_user_id = ? WHERE id = 'r-2'",
        (fingerprint("user-b"),),
    )
    conn.execute(
        "CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision"
        " BEGIN SELECT RAISE(ABORT, 'destination_revision is immutable'); END"
    )

    assert apply_migrations(conn) == [5]

    stored = dict(conn.execute("SELECT id, remote_user_id FROM destination_revision"))
    assert stored["r-1"] == fingerprint("user-a")
    assert stored["r-2"] == fingerprint("user-b")  # 二重にしない


def test_a_raw_value_shaped_like_a_hash_is_still_converted(tmp_path):
    """**形で指紋を推定しない。** 64 文字の 16 進の API キーはありうる.

    侵害された旧 Immich が `users/me` の `id` にその鍵を返していた DB で、
    「指紋の形だから変換しない」と判断すると、**鍵の平文が
    `destination_revision` に残り、転送先の一覧と `verify` の応答に出る**
    （0005 が塞ごうとしている脅威そのもの）。
    """
    from mediaferry.core.destinations.identity import fingerprint

    looks_like_a_hash = "a" * 64
    conn = Database(tmp_path / "hexkey.sqlite3").connect()
    apply_migrations(conn)
    _a_destination_revision(conn, "user-a", suffix="1")
    conn.execute("DELETE FROM schema_migration WHERE version = 5")
    conn.execute("DROP TRIGGER destination_revision_no_update")
    conn.execute(
        "UPDATE destination_revision SET remote_user_id = ? WHERE id = 'r-1'", (looks_like_a_hash,)
    )
    conn.execute(
        "CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision"
        " BEGIN SELECT RAISE(ABORT, 'destination_revision is immutable'); END"
    )

    assert apply_migrations(conn) == [5]

    stored = conn.execute("SELECT remote_user_id FROM destination_revision").fetchone()[0]
    assert stored == fingerprint(looks_like_a_hash)
    assert looks_like_a_hash not in stored
    conn.close()


def test_every_identifier_stored_before_the_check_is_dropped(tmp_path):
    """**検査より前の版が保存した識別子は、値を見て選り分けない**（§12.3 / §14）.

    形で選ると、`test-api-key` のように unreserved だけでできた鍵が残る
    （一覧の API 応答に平文が出続ける）。SQL からは鍵を復号できないので
    「形が正しいかどうか」しか見られないが、**版そのものが「検査を入れる前に
    書かれた行」という cohort を指せる**ので、値に関係なく一度外す。

    `complete` は `remote_checked_at` も外す。外さないと「リモートに存在しない
    と確認済み」と同じ形になり、requeue で送り直せてしまう（実際には在る）。
    再確認はチェックサムで照合するので、正しい識別子はそこで戻る。
    """
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile
    from .test_schema_uploads import a_destination, an_upload

    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    profile = a_profile(conn)
    destination = a_destination(conn)
    _, revision_id, _ = destination
    common = {"destination_revision_id": revision_id, "remote_checked_at": "2026-08-18T00:00:00Z"}
    # 鍵そのもの（unreserved だけ）・NUL 入り・空文字・正しい UUID。
    stored = {
        value: an_upload(
            conn,
            destination,
            a_media_file(conn, profile),
            state="complete",
            remote_asset_id=value,
            **common,
        )
        for value in ("test-api-key", "s\x00e\x00c", "", "6f9619ff-8b86-d011-b42d-00c04fc964ff")
    }
    awaiting = an_upload(
        conn,
        destination,
        a_media_file(conn, profile),
        state="awaiting_datetime_approval",
        remote_asset_id="test-api-key",
        **common,
    )

    conn.execute("DELETE FROM schema_migration WHERE version = 6")
    assert apply_migrations(conn) == [6]

    rows = {row["id"]: row for row in conn.execute("SELECT * FROM upload_record")}
    for value, record_id in stored.items():
        assert rows[record_id]["remote_asset_id"] is None, value
        assert "識別子" in rows[record_id]["last_error"]
        # **「リモートに存在しない」に見せない。** requeue ではなく再確認で戻す。
        assert rows[record_id]["remote_checked_at"] is None
        assert rows[record_id]["invalidated_at"] is None
    # 承認は人の操作で、指す資産が無いと直しようがない。
    assert rows[awaiting]["remote_asset_id"] is None
    assert rows[awaiting]["invalidated_at"] is not None
    assert "承認" in rows[awaiting]["invalidated_reason"]
    conn.close()
