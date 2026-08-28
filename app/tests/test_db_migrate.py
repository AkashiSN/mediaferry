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


def test_a_shipped_migration_is_never_edited(tmp_path):
    """**適用済みの版は書き換えない**（`migrate.py` 自身の契約）.

    書き換えると、その版を当てた DB は `MigrationError` で開けなくなる
    —— 移行が走る前に落ちるので、データを直す機会も無い。ここでは
    「版のファイルは追加のみ」を、記録した checksum（`migration_checksums.txt`）で
    固定する。直したいことがあるなら**新しい版を足す**（その一覧に 1 行足す）。
    """
    import hashlib
    from pathlib import Path

    from mediaferry.db.migrate import MIGRATIONS_DIR

    frozen = {
        name: digest
        for name, digest in (
            line.split()
            for line in (Path(__file__).parent / "migration_checksums.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.startswith("#")
        )
    }
    shipped = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))
    assert shipped == sorted(frozen), "版を足したら、この一覧にも足す"

    digests = {
        name: hashlib.sha256((MIGRATIONS_DIR / name).read_bytes()).hexdigest() for name in shipped
    }
    # **中身そのものを固定する。** 名前の一覧だけを見ていると、既存の版を書き換えても
    # 気付けない（書き換えると、前の版で作った DB が `MigrationError` で開けなくなる）。
    assert digests == frozen, "既存の版は書き換えない。直したいことがあるなら新しい版を足す"
    # 記録は `schema_migration` にも入る。同じ計算で照合できることを確かめる。
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    stored = {
        row["name"]: row["checksum"] for row in conn.execute("SELECT * FROM schema_migration")
    }
    assert stored == digests
    conn.close()


def test_the_recompute_lookups_do_not_scan_per_row(tmp_path):
    """`source_entry_by_media`。再計算の対象抽出は `media_file` 1 行ごとに相関副問い合わせを回す.

    索引が無いと `source_entry` を毎行 SCAN し、並べ替えに一時 B-tree を作る。
    数万件のライブラリでは**最初の `assert_lease` に届く前に 60 秒を超え**、
    正常なジョブがリース切れで落ちる（`jobs/recompute.py`）。
    """
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    plan = " | ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN"
            " SELECT m.id,"
            " (SELECT s.rel_path FROM source_entry s"
            "   WHERE s.media_file_id = m.id AND s.state = 'published'"
            "   ORDER BY s.observed_at, s.id LIMIT 1) AS source_rel_path"
            " FROM media_file m WHERE m.profile_id = ? AND m.role = 'original'"
            " ORDER BY m.rel_path",
            ("x",),
        )
    )
    conn.close()

    assert "SCAN s" not in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_the_derived_lookup_does_not_scan_members_per_row(tmp_path):
    """派生物の抽出も同じ形（`merge_group.output_media_file_id` は FK だが索引が無い）.

    **`SCAN g` が出ないことでは確かめられない。** 索引を落とすと SQLite は
    `merge_member` の側から回すので `SCAN mm` に変わるだけで、`SCAN g` は
    どちらでも出ない（そう書いた最初の版は変異試験を素通りした）。
    """
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    plan = " | ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN"
            " SELECT f.captured_at FROM merge_group g"
            " JOIN merge_member mm ON mm.merge_group_id = g.id AND mm.active = 1"
            " JOIN media_file f ON f.id = mm.media_file_id"
            " WHERE g.output_media_file_id = ?"
            " ORDER BY mm.position LIMIT 1",
            ("x",),
        )
    )
    conn.close()

    assert "SCAN g" not in plan, plan
    assert "SCAN mm" not in plan, plan


def test_the_unsent_count_looks_up_records_by_media_and_destination(tmp_path):
    """`upload_record_live_pair`。「まだ送っていない」の集計が、宛先の全レコードを走査しない.

    ダッシュボードは media 1 件ごとに「この宛先の有効な記録があるか」を尋ねる。
    条件は `media_file_id` と `destination_id` の 2 つの等値で、統計が無いと
    SQLite は 1 列だけの `upload_record_claimable (destination_id, ...)` を選び
    うる —— そうなると media 1 件ごとにその宛先の全レコードを読む（実測で
    media 8,000 件のとき 5.6 秒。行数が倍になるたびに 4 倍）。

    **統計に頼らずに決まることを見る。** ここで作るのは空の DB なので、
    `sqlite_stat1` は無い。
    """
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    plan = " | ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN"
            " SELECT count(*) FROM media_file m WHERE NOT EXISTS ("
            "  SELECT 1 FROM upload_record u WHERE u.media_file_id = m.id"
            "   AND u.destination_id = ? AND u.invalidated_at IS NULL)",
            ("d1",),
        )
    )
    conn.close()

    assert "upload_record_live_pair" in plan, plan


def test_the_recompute_keyset_is_bounded_by_the_profile(tmp_path):
    """`media_file_by_profile`。ページの**返却件数**だけでなく、**探索する行数**も抑える.

    `rel_path` の UNIQUE 索引だけだと、`LIMIT` は返す件数しか縛らない。
    別プロファイルの大きなライブラリがあると、1 ページ読むだけでその全行を
    走査しうる（`original` は `rel_path` の並び上、`derived/` も先に通る）。
    **`fetch` の最中は heartbeat もキャンセル観測も無い**ので、そこでリース窓を
    超える（`jobs/recompute.py` の `_pass`）。
    """
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    plans = [
        " | ".join(
            row[3]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN"
                " SELECT m.id, m.rel_path FROM media_file m"
                " WHERE m.profile_id = ? AND m.role = ? AND m.rel_path > ?"
                " ORDER BY m.rel_path LIMIT ?",
                ("x", role, "", 10),
            )
        )
        for role in ("original", "derived")
    ]
    conn.close()

    for plan in plans:
        assert "media_file_by_profile" in plan, plan
        assert "TEMP B-TREE" not in plan, plan


def test_a_profile_filtered_listing_does_not_sort_the_whole_profile(tmp_path):
    """`media_file_listing`。`media_file_by_profile` だけでは一覧の実行計画が退行する.

    一覧は `captured_at DESC, rel_path DESC` 固定で、ページは 50 件（§11）。
    `media_file_captured_at` を辿れば先頭ページで止まれるのに、
    `(profile_id, role, rel_path)` が選ばれると**そのプロファイルの全行を拾って
    から並べ替える**。プロファイルが大半を占める通常の構成ほど悪化する。

    **`media_file_listing` は `rel_path DESC` で終わる**ので、`ORDER BY`
    と索引がちょうど噛み合う。一時ソートは一切要らない。
    """
    # **一覧が実際に組み立てる WHERE を使う。** 問い合わせを手で書き写すと、
    # 絞り込みの形（`IN` か `=` か）を変えても試験が落ちない。
    from mediaferry.api.routes_media import _filters

    clause, params = _filters(
        "m",
        kind=None,
        role=None,
        profile="x",
        captured_from=None,
        captured_to=None,
        q=None,
        destination_id=None,
        status=None,
    )
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    plan = " | ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN"  # noqa: S608 - 値は params で渡す
            f" SELECT m.* FROM media_file m WHERE {clause}"
            " ORDER BY m.captured_at DESC, m.rel_path DESC LIMIT ? OFFSET ?",
            (*params, 50, 0),
        )
    )
    conn.close()

    assert "media_file_listing" in plan, plan
    # 索引が `ORDER BY` と同じ並び（`captured_at DESC, rel_path DESC`）で終わるので、
    # tie-break も含めて一時ソートが一切要らない。全件を拾ってから並べ替える形
    # （`FOR ORDER BY`）にも、tie-break だけを一時 B-tree に落とす形
    # （`FOR LAST TERM OF ORDER BY`）にも戻っていないことを見る。
    assert "TEMP B-TREE" not in plan, plan


def test_a_role_filtered_listing_does_not_scan_the_capture_time_index(tmp_path):
    """`media_file_derived_listing`。「つないだ動画」の絞り込みが、撮影日時の索引を全走査しない.

    `derived` は `original` に比べて桁で少ない。`captured_at` 側の索引を辿ると、
    `LIMIT` を満たすまでに何行 `role` を確かめるかが読めない（実測: original
    60,000 行 / derived 200 行で 55〜66 ms）。`(captured_at DESC, rel_path DESC)
    WHERE role = 'derived'` の部分索引を辿れば `role = 'derived'` の行だけを
    最初から並び順に読める。

    **全体索引ではなく部分索引で持つ。** `(role, captured_at DESC, rel_path DESC)` の
    形にすると role='original' 側にも使えてしまい、`db/selection.py` の
    `SENDABLE_CLAUSE` の OR 節の両方の枝から拾われて `MULTI-INDEX OR` に化ける
    （`test_the_unsent_listing_does_not_multi_index_or`）。role='derived' だけの
    部分索引なら、その枝からしか使われない。

    **`media_file_derived_listing` は `rel_path DESC` で終わる**ので、
    `ORDER BY` とちょうど噛み合う。`role = 'derived'` の行だけを `captured_at` の
    並びで最初から読め、tie-break を含めて一時ソートは一切要らない。
    """
    from mediaferry.api.routes_media import _filters

    clause, params = _filters(
        "m",
        kind=None,
        role="derived",
        profile=None,
        captured_from=None,
        captured_to=None,
        q=None,
        destination_id=None,
        status=None,
    )
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    plan = " | ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN"  # noqa: S608 - 値は params で渡す
            f" SELECT m.* FROM media_file m WHERE {clause}"
            " ORDER BY m.captured_at DESC, m.rel_path DESC LIMIT ? OFFSET ?",
            (*params, 50, 0),
        )
    )
    conn.close()

    assert "media_file_derived_listing" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_the_unsent_listing_does_not_multi_index_or(tmp_path):
    """`media_file_derived_listing`。`status=unsent` の絞り込みが `MULTI-INDEX OR` に落ちない.

    `db/selection.py` の `SENDABLE_CLAUSE` は
    `(m.role = 'original' AND ...) OR (m.role = 'derived' AND ...)` という形で、
    **両方の枝が `role` の等値をリテラルで持つ**。`(role, captured_at DESC,
    rel_path DESC)` の全体索引はどちらの枝からも使えるので、SQLite が
    `MULTI-INDEX OR` を選ぶ。OR の結果は `captured_at` の並び順に出ないため
    最後に全件ソートが入り、`GET /media?status=unsent&destination_id=…` が
    退行する（実測: original 60,000 行 / derived 200 行で中央値 0.58 ms → 74 ms）。

    `media_file_derived_listing` は role='derived' だけの部分索引なので、
    role='original' の枝には索引が無く、`MULTI-INDEX OR` の対象から外れる ——
    既存の経路（`media_file_captured_at` を辿りながら絞り込む）に戻ることを見る。
    """
    from mediaferry.api.routes_media import _filters

    # **一覧が実際に組み立てる WHERE を使う。** `status=unsent` は
    # `SENDABLE_CLAUSE` を経由するので、手で書き写すとこの退行を捕まえられない。
    clause, params = _filters(
        "m",
        kind=None,
        role=None,
        profile=None,
        captured_from=None,
        captured_to=None,
        q=None,
        destination_id="d1",
        status="unsent",
    )
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    plan = " | ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN"  # noqa: S608 - 値は params で渡す
            f" SELECT m.* FROM media_file m WHERE {clause}"
            " ORDER BY m.captured_at DESC, m.rel_path DESC LIMIT ? OFFSET ?",
            (*params, 50, 0),
        )
    )
    conn.close()

    assert "MULTI-INDEX OR" not in plan, plan
    # **並べ替えの tie-break（`LAST TERM OF ORDER BY`）は許すが、全件ソートは許さない。**
    # `MULTI-INDEX OR` の結果は並び順に出ないので `USE TEMP B-TREE FOR ORDER BY`
    # （`LAST TERM OF` を伴わない全件ソート）になる。良い計画は tie-break だけの
    # `USE TEMP B-TREE FOR LAST TERM OF ORDER BY` で、"FOR ORDER BY" を部分文字列に
    # 持たない。
    assert "FOR ORDER BY" not in plan, plan


def _fk_fixture(folder):
    """親子 1 組を作る版。子が親を参照している."""
    (folder / "0001_base.sql").write_text(
        "CREATE TABLE parent (id TEXT PRIMARY KEY, tag TEXT NOT NULL"
        "   CHECK (tag IN ('a', 'b')));\n"
        "CREATE TABLE child (id TEXT PRIMARY KEY,"
        "   parent_id TEXT NOT NULL REFERENCES parent(id) ON DELETE RESTRICT);\n"
        "INSERT INTO parent VALUES ('p1', 'a');\n"
        "INSERT INTO child VALUES ('c1', 'p1');\n",
        encoding="utf-8",
    )


_REBUILD = (
    "CREATE TABLE parent_new (id TEXT PRIMARY KEY, tag TEXT NOT NULL"
    "   CHECK (tag IN ('a', 'b', 'c')));\n"
    "INSERT INTO parent_new SELECT id, tag FROM parent;\n"
    "DROP TABLE parent;\n"
    "ALTER TABLE parent_new RENAME TO parent;\n"
)


def test_rebuilding_a_referenced_table_fails_without_the_marker(tmp_path, monkeypatch):
    """**目印が無ければ、いままでどおり外部キーが効いている.**

    これが通らないと、次のテストが「目印のおかげ」なのか「もともと通る」のかが
    分からない。
    """
    from mediaferry.db import migrate

    folder = tmp_path / "m"
    folder.mkdir()
    _fk_fixture(folder)
    (folder / "0002_rebuild.sql").write_text(_REBUILD, encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)
    conn = Database(tmp_path / "db.sqlite3").connect()
    with pytest.raises(sqlite3.IntegrityError):
        apply_migrations(conn)
    conn.close()


def test_a_migration_can_declare_that_foreign_keys_must_be_off(tmp_path, monkeypatch):
    """**外部キーを持つ表は、FK を外さないと作り直せない.**

    `PRAGMA foreign_keys` はトランザクションの中では黙って無視されるので、
    移行ファイルの中からは外せない。runner が外側で切り替える。
    """
    from mediaferry.db import migrate

    folder = tmp_path / "m"
    folder.mkdir()
    _fk_fixture(folder)
    (folder / "0002_rebuild.sql").write_text(
        "-- mediaferry:foreign-keys-off\n" + _REBUILD, encoding="utf-8"
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)
    conn = Database(tmp_path / "db.sqlite3").connect()
    assert apply_migrations(conn) == [1, 2]
    # 子は残り、親は新しい CHECK を持ち、参照は壊れていない。
    assert conn.execute("SELECT parent_id FROM child").fetchone()[0] == "p1"
    conn.execute("INSERT INTO parent VALUES ('p2', 'c')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # **適用のあと、外部キーは必ず戻っている。**
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_a_migration_that_leaves_dangling_references_is_refused(tmp_path, monkeypatch):
    """**外部キーを外すことを許す代わりに、適用後に必ず確かめる.**

    SQLite 公式の 12 手順が最後に `PRAGMA foreign_key_check` を求めているのと
    同じ手当て。これが無いと、目印を付けた版は壊れた参照を黙って残せる。
    """
    from mediaferry.db import migrate

    folder = tmp_path / "m"
    folder.mkdir()
    _fk_fixture(folder)
    (folder / "0002_rebuild.sql").write_text(
        "-- mediaferry:foreign-keys-off\nDELETE FROM parent;\n",  # 子が孤児になる
        encoding="utf-8",
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)
    conn = Database(tmp_path / "db.sqlite3").connect()
    with pytest.raises(MigrationError, match="参照が壊れている"):
        apply_migrations(conn)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # **失敗した版は「適用済み」として記録されていない。**
    # 検査が COMMIT より後だと、版の記録だけが確定済みで残ってしまう。
    assert (
        conn.execute("SELECT count(*) FROM schema_migration WHERE version = 2").fetchone()[0] == 0
    )
    # 記録が残っていないので、もう一度呼んでも黙って通らず、同じ失敗になる。
    with pytest.raises(MigrationError, match="参照が壊れている"):
        apply_migrations(conn)
    conn.close()


def test_a_marker_that_only_appears_mid_file_does_not_turn_off_foreign_keys(tmp_path, monkeypatch):
    """**目印は先頭行だけを見る.** 本文の途中に同じ文字列（引用や説明文）が
    現れても、外部キーは外れない。
    """
    from mediaferry.db import migrate

    folder = tmp_path / "m"
    folder.mkdir()
    _fk_fixture(folder)
    (folder / "0002_rebuild.sql").write_text(
        "-- この版は、かつて -- mediaferry:foreign-keys-off を使っていた\n" + _REBUILD,
        encoding="utf-8",
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)
    conn = Database(tmp_path / "db.sqlite3").connect()
    with pytest.raises(sqlite3.IntegrityError):
        apply_migrations(conn)
    conn.close()


def test_every_shipped_migration_leaves_the_references_intact(tmp_path):
    """**外部キーを外して走る版がありうるので、全部流した後に必ず確かめる.**

    子から参照されている表を作り直す版は、手順を 1 つ間違えると参照が
    壊れたまま通る。
    """
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
