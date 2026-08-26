import sqlite3
import stat

import pytest

from mediaferry.clock import now_iso
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


def test_a_database_from_the_previous_release_still_opens(tmp_path):
    """**適用済みの版は書き換えない**（`migrate.py` 自身の契約）.

    書き換えると、前の版で作った DB は `MigrationError` で開けなくなる
    —— 移行が走る前に落ちるので、データを直す機会も無い。ここでは
    「版のファイルは追加のみ」を、記録した checksum（`migration_checksums.txt`）で
    固定する。値が変わったら**新しい版を足す**（その一覧に 1 行足す）。
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


def test_untrusted_remote_state_is_dropped_whatever_its_shape(tmp_path):
    """**相手由来の値は、形を見ずに捨てる**（§12.3 / §14）.

    形で選り分けると、同じ形の秘密が残る。値自身の接頭辞も出所にならない
    （API キーを発行するのは相手なので、`sha256:` で始まる鍵を選べる）。
    信用できるのは版そのもの（この版より前に書かれた行）だけ。
    """
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile
    from .test_schema_uploads import a_destination, an_upload

    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    profile = a_profile(conn)
    destination = a_destination(conn)
    destination_id, revision_id, _ = destination
    common = {"destination_revision_id": revision_id, "remote_checked_at": "2026-08-18T00:00:00Z"}
    stored = {
        value: an_upload(
            conn,
            destination,
            a_media_file(conn, profile),
            state="complete",
            remote_asset_id=value,
            remote_is_trashed=1,
            **common,
        )
        # 鍵そのもの（unreserved だけ）・NUL 入り・空文字・正しい UUID。
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
    # 向き先の記録には、指紋のふりをした鍵を入れておく。
    conn.execute("DELETE FROM schema_migration WHERE version = 7")
    conn.execute("DROP TRIGGER destination_revision_no_update")
    conn.execute(
        "UPDATE destination_revision SET remote_user_id = ?, server_instance_id = 'sha256:x'",
        ("sha256:SECRET-API-KEY",),
    )
    conn.execute(
        "CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision"
        " BEGIN SELECT RAISE(ABORT, 'destination_revision is immutable'); END"
    )

    assert apply_migrations(conn) == [7]

    revision = conn.execute("SELECT * FROM destination_revision").fetchone()
    assert revision["remote_user_id"] is None
    assert revision["server_instance_id"] is None
    rows = {row["id"]: row for row in conn.execute("SELECT * FROM upload_record")}
    for value, record_id in stored.items():
        assert rows[record_id]["remote_asset_id"] is None, value
        assert rows[record_id]["remote_checked_at"] is None, value
        # **観測はまとめて捨てる。** 片方だけ残すと「どの資産の、いつの観測か
        # 分からないゴミ箱状態」が一覧に出る。
        assert rows[record_id]["remote_is_trashed"] is None, value
        assert "捨てた" in rows[record_id]["last_error"]
        assert rows[record_id]["invalidated_at"] is None
    assert rows[awaiting]["invalidated_at"] is not None
    assert "承認" in rows[awaiting]["invalidated_reason"]
    # リビジョンは不変のまま（trigger を戻している）。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE destination_revision SET remote_user_id = 'x'")
    conn.close()


def test_existing_rows_get_the_revision_they_were_imported_with(tmp_path):
    """`0011`。既存行の `captured_at` は取り込みに使った版で算出されている.

    **列を分けるのは provenance のため**（§6）。`profile_revision_id` は「その
    レコードが使用した不変の版」なので、再計算で値だけを新しい定義から作ると
    嘘になり、版ごと進めると timestamp 以外の新定義も適用したと偽る。
    """
    from .test_schema_sources import a_profile

    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    profile_id, revision_id = a_profile(conn)
    _, other_revision = a_profile(conn, slug="canon-eos")

    # 0011 が入る前の DB へ戻す。
    conn.execute("DELETE FROM schema_migration WHERE version = 11")
    conn.execute("DROP TRIGGER media_file_captured_revision_insert")
    conn.execute("DROP TRIGGER media_file_captured_revision_update")
    conn.execute("ALTER TABLE media_file DROP COLUMN captured_at_revision_id")
    conn.execute(
        "INSERT INTO media_file (id, role, profile_id, profile_revision_id, rel_path,"
        " size_bytes, mtime_ns, sha1, kind, captured_at, captured_at_source, probe_state,"
        " created_at) VALUES ('m-1', 'original', ?, ?, 'library/dji-osmo/A.JPG', 10, 1,"
        " '0000000000000000000000000000000000000000', 'photo', ?, 'filename', 'ok', ?)",
        (profile_id, revision_id, now_iso(), now_iso()),
    )

    assert apply_migrations(conn) == [11]

    assert (
        conn.execute("SELECT captured_at_revision_id FROM media_file").fetchone()[0] == revision_id
    )
    # 移行で入れた値の上に、trigger の 2 つの契約がそのまま乗る。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE media_file SET captured_at_revision_id = NULL")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE media_file SET captured_at_revision_id = ?", (other_revision,))
    conn.close()


def test_the_recompute_lookups_do_not_scan_per_row(tmp_path):
    """`0012`。再計算の対象抽出は `media_file` 1 行ごとに相関副問い合わせを回す.

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
    """`0019`。「まだ送っていない」の集計が、宛先の全レコードを走査しない.

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
    """`0013`。ページの**返却件数**だけでなく、**探索する行数**も抑える.

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
    """`0014`。`0013` は一覧の実行計画を退行させる.

    一覧は `captured_at DESC, id DESC` 固定で、ページは 50 件（§11）。
    `media_file_captured_at` を辿れば先頭ページで止まれるのに、`0013` の
    `(profile_id, role, rel_path)` が選ばれると**そのプロファイルの全行を拾って
    から並べ替える**。プロファイルが大半を占める通常の構成ほど悪化する。
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
            " ORDER BY m.captured_at DESC, m.id DESC LIMIT ? OFFSET ?",
            (*params, 50, 0),
        )
    )
    conn.close()

    assert "TEMP B-TREE" not in plan, plan


def test_a_role_filtered_listing_does_not_scan_the_capture_time_index(tmp_path):
    """`0023`。「つないだ動画」の絞り込みが、撮影日時の索引を全走査しない.

    `derived` は `original` に比べて桁で少ない。`captured_at` 側の索引を辿ると、
    `LIMIT` を満たすまでに何行 `role` を確かめるかが読めない（実測: original
    60,000 行 / derived 200 行で 55〜66 ms）。`(captured_at DESC, id DESC)
    WHERE role = 'derived'` の部分索引を辿れば `role = 'derived'` の行だけを
    最初から並び順に読める。

    **`0022` の全体索引ではなく `0023` の部分索引を見る。** `0022`
    （`role, captured_at DESC, id DESC`）は role='original' 側にも使える形で、
    `db/selection.py` の `SENDABLE_CLAUSE` の OR 節の両方の枝から拾われて
    `MULTI-INDEX OR` に化け、`GET /media?status=unsent&…` を退行させた
    （`test_the_unsent_listing_does_not_multi_index_or`）。`0023` は
    role='derived' だけの部分索引に差し替えることでその退行を塞ぐ。
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
            " ORDER BY m.captured_at DESC, m.id DESC LIMIT ? OFFSET ?",
            (*params, 50, 0),
        )
    )
    conn.close()

    assert "media_file_derived_listing" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_the_unsent_listing_does_not_multi_index_or(tmp_path):
    """`0023`。`status=unsent` の絞り込みが `MULTI-INDEX OR` に落ちない.

    `db/selection.py` の `SENDABLE_CLAUSE` は
    `(m.role = 'original' AND ...) OR (m.role = 'derived' AND ...)` という形で、
    **両方の枝が `role` の等値をリテラルで持つ**。`0022`
    （`role, captured_at DESC, id DESC`）はどちらの枝からも使えたため、SQLite が
    `MULTI-INDEX OR` を選んでいた。OR の結果は `captured_at` の並び順に出ないので
    最後に全件ソートが入り、`GET /media?status=unsent&destination_id=…` が
    退行した（実測: original 60,000 行 / derived 200 行で中央値 0.58 ms → 74 ms。
    詳細は `.superpowers/sdd/phase9-plan/task-3-report.md`）。

    `0023` は role='derived' だけの部分索引に差し替えたので、role='original' の
    枝には索引が無くなり、`MULTI-INDEX OR` の対象から外れる —— 既存の経路
    （`media_file_captured_at` を辿りながら絞り込む）に戻ることを見る。
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
            " ORDER BY m.captured_at DESC, m.id DESC LIMIT ? OFFSET ?",
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


def test_a_group_discarded_before_the_change_gives_its_files_back(tmp_path):
    """`0017`。**既存の DB を埋め戻さないと、実機がそのまま詰まる.**

    実機には「破棄済みなのに member が active」なグループが 2 つ残っていた。
    その状態では再検出も組み直しもできないので、版を足すだけでは直らない。
    """
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile

    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    profile_id, revision_id = a_profile(conn)
    media = [
        a_media_file(conn, (profile_id, revision_id), rel_path=f"library/dji-osmo/P{i}.MP4")
        for i in range(2)
    ]

    # 0017 が入る前の DB へ戻す（active は superseded_by_id だけの写しだった）。
    conn.execute("DELETE FROM schema_migration WHERE version = 17")
    conn.execute("DROP TRIGGER merge_group_discard_deactivates_members")
    conn.execute("DROP TRIGGER merge_group_discard_is_final")
    conn.execute("DROP TRIGGER merge_member_insert_matches_parent")
    conn.execute("DROP TRIGGER merge_member_update_matches_parent")
    conn.execute(
        "INSERT INTO merge_group (id, profile_id, profile_revision_id, status, input_digest,"
        " detected_by, created_at, updated_at)"
        " VALUES ('g-old', ?, ?, 'skipped', 'digest-old', 'auto', ?, ?)",
        (profile_id, revision_id, now_iso(), now_iso()),
    )
    for position, media_id in enumerate(media):
        conn.execute(
            "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
            " VALUES ('g-old', ?, ?, 1)",
            (media_id, position),
        )

    # 0003 の版の trigger を戻す（0017 はこれを DROP するところから始まる）。
    for name, event in (
        ("merge_member_insert_matches_parent", "BEFORE INSERT ON merge_member"),
        (
            "merge_member_update_matches_parent",
            "BEFORE UPDATE OF merge_group_id, active ON merge_member",
        ),
    ):
        conn.execute(
            f"CREATE TRIGGER {name} {event}"  # noqa: S608
            " WHEN NEW.active <> (SELECT superseded_by_id IS NULL FROM merge_group"
            "                     WHERE id = NEW.merge_group_id)"
            " BEGIN SELECT RAISE(ABORT, 'member active flag must match the group"
            " supersede state'); END"
        )

    assert apply_migrations(conn) == [17]

    assert conn.execute("SELECT count(*) FROM merge_member WHERE active = 1").fetchone()[0] == 0
    conn.close()


def test_a_card_that_was_already_counted_stays_counted(tmp_path):
    """`0021`。**版を足すだけでは、既に数え終えたカードが「未計測」に戻る.**

    それまで「数えた時刻」は `source_entry.observed_at` の最大値から導いて
    いた。列を足しただけでは既存の行は `NULL` なので、更新した瞬間にホームが
    数え終わったカードにも「中身を数えています。」を出す。
    """
    from .test_schema_sources import a_volume

    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    volume_id = a_volume(conn)
    empty_id = a_volume(conn, fs_uuid="0000-0001")
    for index, observed in enumerate(["2026-08-24T00:00:00Z", "2026-08-25T09:00:00Z"]):
        conn.execute(
            "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
            " quick_fingerprint, fingerprint_version, state, observed_at)"
            " VALUES (?, ?, ?, 1, 1, 'x', 1, 'published', ?)",
            (f"e-{index}", volume_id, f"DCIM/{index}.MP4", observed),
        )
    # 0021 が入る前の DB へ戻す。
    conn.execute("DELETE FROM schema_migration WHERE version = 21")
    conn.execute("ALTER TABLE volume_instance DROP COLUMN scanned_at")

    assert apply_migrations(conn) == [21]

    rows = {row["id"]: row["scanned_at"] for row in conn.execute("SELECT * FROM volume_instance")}
    # 最後に数えた時刻を引き継ぐ（いちばん古い方を採ると、数え直した直後の
    # カードが「ずっと前に数えたまま」に見える）。
    assert rows[volume_id] == "2026-08-25T09:00:00Z"
    # 行が無いカードは埋め戻せない。**次に挿したときに数え直される**（§12.1 の
    # `auto_scan_at` は presence ごとなので、挿し直しで新しい行になる）。
    assert rows[empty_id] is None
    conn.close()


def test_progress_left_on_a_finished_job_is_cleared(tmp_path):
    """`0018`。**版を足すだけでは、既に終わっている行は誰も直さない.**

    進捗を落とすのは終了時の 1 回きり。実機では、直す前の版で完了したジョブに
    「結合中 …」が残り続けていた。
    """
    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    conn.execute("DELETE FROM schema_migration WHERE version = 18")
    conn.execute(
        "INSERT INTO job (id, type, status, params_json, progress_json, created_at, finished_at)"
        " VALUES ('j-old', 'merge', 'succeeded', '{}', '{\"phase\": \"merge\"}', ?, ?)",
        (now_iso(), now_iso()),
    )
    conn.execute(
        "INSERT INTO job (id, type, status, params_json, progress_json, created_at)"
        " VALUES ('j-live', 'import', 'running', '{}', '{\"phase\": \"copy\"}', ?)",
        (now_iso(),),
    )

    assert apply_migrations(conn) == [18]

    rows = {row["id"]: row["progress_json"] for row in conn.execute("SELECT * FROM job")}
    assert rows["j-old"] is None
    # 走っているものには触らない。
    assert rows["j-live"] is not None
    conn.close()


def test_existing_skips_go_back_to_unevaluated(tmp_path):
    """`0025`。組の判定から撮影時刻を外したので、既存の見送りを未評価へ戻す.

    **リビジョンの公開では戻らない。** `_publish_revision` が比べるのは
    `StackRule` へパースした後の値で、`tolerance_seconds` は dataclass に無く
    パーサも読まないため、旧 JSON と新 JSON が同じ値にパースされて差が出ない。
    他の戻し口（`retry` は `failed` だけ、`requeue` はリモートから消えた
    `complete` だけ、`recompute` の `_reopen_stack` はその実行で `captured_at` が
    動いた行だけ）も、**両方が見送りの組**には効かない。一度きりの移行で戻す。

    **既に組んだものには触らない。** `stacked` は「その資産を送った結果」なので、
    未評価へ戻すと同じ組をもう一度作りに行く。
    """
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile
    from .test_schema_uploads import a_destination, an_upload

    conn = Database(tmp_path / "old.sqlite3").connect()
    apply_migrations(conn)
    profile = a_profile(conn)
    destination = a_destination(conn)
    revision_id = destination[1]
    before = "2026-01-01T00:00:00Z"
    skipped = an_upload(
        conn,
        destination,
        a_media_file(conn, profile),
        state="complete",
        destination_revision_id=revision_id,
        remote_asset_id="asset-skipped",
        stack_state="skipped",
        stack_reason="組の相方が見つからない",
        updated_at=before,
    )
    stacked = an_upload(
        conn,
        destination,
        a_media_file(conn, profile),
        state="complete",
        destination_revision_id=revision_id,
        remote_asset_id="asset-stacked",
        stack_state="stacked",
        remote_stack_id="stack-1",
        updated_at=before,
    )
    unevaluated = an_upload(
        conn,
        destination,
        a_media_file(conn, profile),
        state="complete",
        destination_revision_id=revision_id,
        remote_asset_id="asset-unevaluated",
        updated_at=before,
    )
    conn.execute("DELETE FROM schema_migration WHERE version = 25")

    assert apply_migrations(conn) == [25]

    rows = {row["id"]: row for row in conn.execute("SELECT * FROM upload_record")}
    assert rows[skipped]["stack_state"] is None
    assert rows[skipped]["stack_reason"] is None
    assert rows[skipped]["updated_at"] != before
    # 既に組んだものはそのまま。
    assert rows[stacked]["stack_state"] == "stacked"
    assert rows[stacked]["remote_stack_id"] == "stack-1"
    assert rows[stacked]["updated_at"] == before
    # 未評価の行は書き換えない（同じ値を書くだけでも `updated_at` が動く）。
    assert rows[unevaluated]["updated_at"] == before
    conn.close()


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
