# mediaferry Phase 1（基盤 + 取り込み）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB スキーマ・`ArtifactPublisher`・`Reconciler`・プロファイルリビジョンを確定し、既知の DJI カードを実 USB から手動 scan / import できる API を作る。

**Architecture:** `core/` は OS もネットワークも知らない純粋ロジック（指紋・プロファイル判定・時刻解決・衝突名・暗号）。副作用は `adapters/`（dirfd 走査・ffprobe・公開）に閉じる。`db/` は SQLite の単一書き込み者としてスキーマ・マイグレーション・リポジトリを持つ。`jobs/` は単一 asyncio ワーカーで、実処理は `asyncio.to_thread` に逃がす。`api/` は FastAPI で loopback バインドのみ。取り込みと結合の**両方**が §9.3 の公開プロトコル（`ArtifactPublisher`）を通り、`Reconciler` が起動時に齟齬を回収する。

**Tech Stack:** Python 3.12 / uv workspace / SQLite（WAL）/ FastAPI / cryptography（AES-256-GCM）/ PyYAML / ffprobe / pytest / ruff

**Spec:** `docker/mediaferry/docs/design.md`（正本）。実測で確定した事項は `docker/mediaferry/docs/phase0-findings.md`、作業の前提は `docker/mediaferry/docs/HANDOFF.md`。

## Global Constraints

すべてのタスクの要件に、以下が暗黙に含まれる。

- **作業ディレクトリは `docker/mediaferry/`。** コマンドはすべてここから実行する。
- Python は `>=3.12`。ruff の `line-length = 100`、`target-version = "py312"`、
  lint は `select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`（`ANN401` のみ ignore）。
  `**/tests/*` は `S101` / `S105`〜`S107` / `ANN` が免除される
  （リーストークンや API キーは固定値でなければテストで検証できない）。
  **`docs/` は ruff の対象外**（`extend-exclude = ["docs"]`）。ruff は Markdown の
  コードブロックも整形するので、除外しないと仕様書と実装計画そのものが書き換わる。
  この計画に載っている Python はすべて ruff を通した形で書いてある。
- すべてのモジュールは `from __future__ import annotations` で始める（既存コードの作法）。
- **コメントと docstring は日本語。**「いま書かれているコードを現在形で説明する」だけを書く。
  過去の経緯（「以前は〜だった」「〜へ移行した」）はコードに書かず、`docs/` に残す
  （リポジトリの `CLAUDE.md` の規約）。
- **環境固有の値をリポジトリに含めない。** IP アドレス、ホスト名、データセットのパス、
  API キー、タイムゾーンの実値をコードにもテストにも書かない。
- **DB に絶対パスを保存しない。** `DATA_ROOT` からの相対パスのみが正規形（§7）。
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**（§12.3）。
- 外部コマンドは必ず引数配列で起動する。シェル文字列を組み立てない（§14）。
- ソース側のパス解決は dirfd 起点の**単一構成要素のみ**。`..`・絶対パス・シンボリック
  リンクを辿らない（`O_NOFOLLOW`）（§14）。
- システム時刻（`created_at` / `updated_at` / `observed_at` など）は **UTC の
  ISO-8601 文字列**（`2026-08-17T13:04:05.123456+00:00`）で DB に入れる。生成は
  `mediaferry.clock` の関数だけを使う。
  **例外は `media_file.captured_at`。** これは解決したオフセット付きで保存する
  （`2026-08-17T14:30:00+09:00`）。UTC へ正規化すると、`force_offset` で復元した
  現地の壁時計が読めなくなる。
- ID は `uuid4().hex`（32 文字の TEXT）。`job_event.id` だけ整数の自動採番。
- **migration の SQL ファイルは DDL だけを書く。** `BEGIN` / `COMMIT` も
  `schema_migration` への INSERT も書かない（runner が所有する）。
  **適用済みのファイルは編集しない。** 新しい版のファイルを足す
  （checksum で検出して起動を止める）。
- **DB 接続はスコープごとに 1 本。** API のリクエスト、ワーカーのジョブ、
  reconciler がそれぞれ自分の接続を開いて閉じる。サービスのコンストラクタは
  接続を受け取るが、その寿命は所有しない。
- テストのマーカー: root を要するものは `needs_root`、実 Immich を要するものは
  `needs_immich`。既定の `pytest` では実行されない。
- 各タスクの最後に必ず `uv run pytest`・`uv run ruff check .`・`uv run ruff format .` を通す。
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付けて実行する。** 変異の前後で
  バイト数が変わらない書き換え（`>` を `<` にする、`a or b` を `b or a` にする等）
  では、`.pyc` の無効化条件（mtime の秒＋サイズ）をすり抜けて古いバイトコードが
  使われ、変異が効いているかを読み違える。
- コミットは Conventional Commits + 日本語の本文（例: `feat(mediaferry): add the artifact publisher`）。
  **本文に Claude のセッション URL を書かない**（`CLAUDE.md` の規約）。
- **Phase 1 は配布可能なリリースにしない。** `BIND_HOST` の既定は `127.0.0.1` のまま。

### 検証コマンド

```bash
cd docker/mediaferry
uv sync --all-packages     # --all-packages が必須。素の sync ではメンバーが入らない
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

---

## ファイル構成

Phase 1 で作る／触るファイルと、それぞれの責務。

| ファイル | 責務 |
| --- | --- |
| `app/src/mediaferry/clock.py` | 現在時刻の単一の出所。テストで差し替える |
| `app/src/mediaferry/ids.py` | ID 採番 |
| `app/src/mediaferry/settings.py` | env > DB > 既定値の解決、起動時の検証 |
| `app/src/mediaferry/db/connection.py` | 接続の PRAGMA と `BEGIN IMMEDIATE` |
| `app/src/mediaferry/db/migrate.py` | マイグレーション適用 |
| `app/src/mediaferry/db/migrations/000{1..4}_*.sql` | スキーマ。1 ファイル 1 版、自分で BEGIN/COMMIT する |
| `app/src/mediaferry/db/profiles.py` | `ProfileRegistry`。ビルトインの投入とリビジョン解決 |
| `app/src/mediaferry/db/jobs.py` | `JobStore`。CAS による claim、リース、`job_event` |
| `app/src/mediaferry/db/artifacts.py` | `artifact_staging` / `media_file` のリポジトリ |
| `app/src/mediaferry/db/sources.py` | `source_device` / `volume_instance` / `volume_presence` / `source_entry` |
| `app/src/mediaferry/core/fingerprint.py` | `quick_fingerprint` |
| `app/src/mediaferry/core/naming.py` | ライブラリ内の保存名と衝突時の決定的系列 |
| `app/src/mediaferry/core/timestamps.py` | `captured_at` の解決（`force_offset`・DST） |
| `app/src/mediaferry/core/profiles/model.py` | プロファイル定義の型と検証 |
| `app/src/mediaferry/core/profiles/matching.py` | `hints` / `require` の判定 |
| `app/src/mediaferry/core/profiles/builtin/dji-osmo.yaml` | ビルトイン定義 |
| `app/src/mediaferry/core/crypto.py` | API キーの AEAD 暗号化フォーマット |
| `app/src/mediaferry/adapters/fs.py` | dirfd 起点の安全な `openat` と走査 |
| `app/src/mediaferry/adapters/ffprobe.py` | メディアの種別・duration の確定 |
| `app/src/mediaferry/adapters/publisher.py` | `ArtifactPublisher`（§9.3） |
| `app/src/mediaferry/jobs/runner.py` | 単一 asyncio ワーカー、キャンセル、リース更新 |
| `app/src/mediaferry/jobs/scan.py` | `Scanner`（§9.5） |
| `app/src/mediaferry/jobs/importer.py` | `Importer`（§9.4） |
| `app/src/mediaferry/jobs/reconcile.py` | `Reconciler`（§9.6） |
| `app/src/mediaferry/api/app.py` | FastAPI ファクトリと lifespan |
| `app/src/mediaferry/api/routes_*.py` | ルータ |
| `app/src/mediaferry/__main__.py` | 起動エントリ |
| `app/tests/**` | 単体・統合・crash consistency |
| `docs/phase1-backup.md` | バックアップとリストア、再構築できる範囲（§18-4） |
| `docs/phase1-manual-checklist.md` | 実 USB での手動確認手順 |

---

### Task 1: DB 接続とマイグレーション適用

**Files:**
- Create: `app/src/mediaferry/clock.py`
- Create: `app/src/mediaferry/ids.py`
- Create: `app/src/mediaferry/db/__init__.py`
- Create: `app/src/mediaferry/db/connection.py`
- Create: `app/src/mediaferry/db/migrate.py`
- Create: `app/src/mediaferry/db/migrations/__init__.py`
- Create: `app/tests/conftest.py`
- Modify: `app/pyproject.toml`（SQL をホイールに含める）
- Modify: `pyproject.toml`（ruff から `docs/` を除外する）
- Test: `app/tests/test_db_migrate.py`

**Interfaces:**
- Consumes: なし（このタスクが土台）
- Produces:
  - `mediaferry.clock.utcnow() -> datetime`（tz-aware, UTC）
  - `mediaferry.clock.iso(dt: datetime) -> str`
  - `mediaferry.clock.now_iso() -> str`
  - `mediaferry.ids.new_id() -> str`（32 文字 hex）
  - `mediaferry.db.connection.Database(path: Path)` — `.connect() -> sqlite3.Connection`,
    `.path`, `.enforce_permissions()`
  - `mediaferry.db.connection.immediate(conn) -> ContextManager[sqlite3.Connection]`
  - `mediaferry.db.migrate.apply_migrations(conn) -> list[int]`
  - `mediaferry.db.migrate.MigrationError`
  - pytest フィクスチャ `database`（`Database`）、`db`（マイグレーション適用済みの接続）、
    `data_root`

**接続はスコープごとに 1 本作る。使い回さない。** SQLite のトランザクションは
**接続に属していてスレッドには属さない**。1 本を API のスレッドとワーカーの
スレッドで共有すると、API の書き込みが publisher の `BEGIN IMMEDIATE` の内側へ
入り込んで一緒に commit / rollback されるし、同じ接続で 2 つ目の `BEGIN` が
走れば `cannot start a transaction within a transaction` になる。
`check_same_thread=False` は接続オブジェクトの破損を防ぐだけで、この問題には
効かない。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_db_migrate.py`:

```python
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
```

`app/tests/conftest.py`:

```python
import pytest

from mediaferry.db.connection import Database
from mediaferry.db.migrate import apply_migrations


@pytest.fixture
def data_root(tmp_path):
    """§7 のレイアウト. staging は library と同じファイルシステムに要る."""
    root = tmp_path / "data"
    for name in ("library", "derived", "staging", "work", "var"):
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
def database(data_root):
    return Database(data_root / "var" / "mediaferry.sqlite3")


@pytest.fixture
def db(database):
    conn = database.connect()
    apply_migrations(conn)
    yield conn
    conn.close()
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_db_migrate.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.db'`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/clock.py`:

```python
"""現在時刻の単一の出所.

DB に入る時刻はすべてここを通す。テストは `freeze` で固定した値を使い、
「1 秒ずれたから落ちる」テストを書かなくて済むようにする。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """UTC の ISO-8601 文字列にする. DB の時刻表現はこれだけ."""
    return dt.astimezone(UTC).isoformat()


def now_iso() -> str:
    return iso(utcnow())
```

`app/src/mediaferry/ids.py`:

```python
"""ID の採番.

uuid4 の hex を使う。ハイフン無しなのは、パスやログに出したときに
選択・コピーしやすいため。
"""

from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    return uuid4().hex
```

`app/src/mediaferry/db/connection.py`:

```python
"""SQLite 接続の作法をここに集約する.

**接続はスコープ（API のリクエスト、ワーカーのジョブ、reconciler）ごとに
1 本作る。** トランザクションは接続に属していてスレッドには属さないので、
1 本を共有すると、あるスレッドの UPDATE が別スレッドの `BEGIN IMMEDIATE` の
内側に入り、一緒に commit / rollback されてしまう。2 つ目の `BEGIN` は
`cannot start a transaction within a transaction` で落ちる。

PRAGMA の大半は接続ごとの状態で、ファイルに永続するのは `journal_mode` だけ。
接続を開くたびに設定しないと、外部キーが無効な接続が混ざる。
"""

from __future__ import annotations

import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_MS = 5000

DB_MODE = 0o600
DIR_MODE = 0o700
SIDECAR_SUFFIXES = ("-wal", "-shm")


class Database:
    """DB ファイルの場所と、そこへの接続の作り方.

    接続を保持しない。呼び出し側が自分のスコープで開いて閉じる。
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        # `check_same_thread=False` が要るのは、接続を作るスレッドと使う
        # スレッドが違うため。`asyncio.to_thread` はどの worker で走るかを
        # 保証しないし、FastAPI の同期ルータも lifespan とは別スレッドで動く。
        #
        # **これは「1 本を同時に共有してよい」という意味ではない。** 危険なのは
        # フラグではなく共有そのもの（トランザクションは接続に属する）。所有者を
        # スコープごとに 1 つに保ち、同時に 2 か所から使わないことで守る。
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        self.enforce_permissions()
        return conn

    def enforce_permissions(self) -> None:
        """毎回直す. 緩い権限で作られた既存 DB をそのまま運用しない.

        WAL と SHM は SQLite が DB ファイルの権限を写して作るが、既に存在する
        ファイルの権限は直さないので、こちらで揃える。API キーの暗号文と
        アップロード履歴が入る。
        """
        if stat.S_IMODE(self.path.parent.stat().st_mode) != DIR_MODE:
            self.path.parent.chmod(DIR_MODE)
        sidecars = (self.path.with_name(self.path.name + s) for s in SIDECAR_SUFFIXES)
        for target in (self.path, *sidecars):
            if target.exists() and stat.S_IMODE(target.stat().st_mode) != DB_MODE:
                target.chmod(DB_MODE)


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE` で書き込みトランザクションを開く.

    既定の遅延開始だと、読んでから書きに昇格する時点で他の接続と衝突し、
    `busy_timeout` があっても即座に SQLITE_BUSY になる。claim（§8）は
    この排他に依存している。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
```

`app/src/mediaferry/db/migrate.py`:

```python
"""マイグレーションの適用.

**トランザクションは runner が所有する。** SQL ファイルは DDL だけを書き、
`BEGIN` / `COMMIT` も版の記録も書かない。ファイル側に任せると、記録の
INSERT を書き忘れた版が DDL だけ commit された状態で失敗し、次回起動で
再適用されて「table already exists」になる。

`executescript` は保留中のトランザクションを暗黙に COMMIT するので、Python の
`BEGIN` で囲むことはできない。代わりに、トランザクションを含む 1 本のスクリプトを
runner が組み立てて渡す。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_VERSION_RE = re.compile(r"^(\d{4})_")
# trigger 本体の `BEGIN ... END;` と区別する。トランザクションの BEGIN は
# 直後がセミコロン（またはモード指定 + セミコロン）で終わる。
_TRANSACTION_RE = re.compile(
    r"^\s*(BEGIN(\s+(IMMEDIATE|DEFERRED|EXCLUSIVE))?\s*;|COMMIT\s*;|ROLLBACK\s*;)",
    re.IGNORECASE | re.MULTILINE,
)


class MigrationError(RuntimeError):
    pass


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """未適用の版を順に適用し、適用した版番号を返す."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " checksum TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    applied = {row["version"]: row for row in conn.execute("SELECT * FROM schema_migration")}
    done: list[int] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = _version_of(path)
        body = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()

        if version in applied:
            if applied[version]["checksum"] != checksum:
                # 適用済みの版を書き換えると、開発機と本番でスキーマが食い違う。
                raise MigrationError(
                    f"{path.name} は適用済みだが内容が変わっている。"
                    "新しい版のファイルを足すこと（開発中の DB なら作り直す）"
                )
            continue

        if _TRANSACTION_RE.search(body):
            raise MigrationError(
                f"{path.name} が BEGIN / COMMIT を含んでいる。トランザクションは runner が所有する"
            )
        _apply_one(conn, version, path.name, body, checksum)
        done.append(version)
    return done


def _apply_one(conn: sqlite3.Connection, version: int, name: str, body: str, checksum: str) -> None:
    """DDL と版の記録を 1 つのトランザクションで適用する.

    `executescript` はプレースホルダを受け取らないので、版の記録もリテラルで
    組み立てる。version は int、checksum は hex、name はファイル名なので、
    いずれも SQL の構造を壊す文字を含まない。
    """
    record = (
        # executescript にプレースホルダは渡せないのでリテラルで組み立てる。
        "INSERT INTO schema_migration (version, name, checksum, applied_at)"  # noqa: S608
        f" VALUES ({version}, '{name}', '{checksum}', datetime('now'));"
    )
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{body}\n{record}\nCOMMIT;")
    except Exception:
        # executescript の途中で失敗するとトランザクションが開いたまま残り、
        # 以後の SQL がその中で走ってしまう。
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _version_of(path: Path) -> int:
    match = _VERSION_RE.match(path.name)
    if match is None:
        raise MigrationError(f"{path.name} のファイル名が 4 桁の版番号で始まっていない")
    return int(match.group(1))
```

`app/src/mediaferry/db/__init__.py` と `app/src/mediaferry/db/migrations/__init__.py` は空ファイル。

SQL ファイルをホイールに含めるため `app/pyproject.toml` に追記する:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/mediaferry"]

# .sql はソースディストリビューションから漏れやすい。明示的に含める。
[tool.hatch.build.targets.wheel.force-include]
"src/mediaferry/db/migrations" = "mediaferry/db/migrations"
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_db_migrate.py -v`
Expected: `test_apply_migrations_is_idempotent` 以外 PASS。
`migrations/` が空なので `assert first` で落ちる。**Task 2 の後に通る**ので、
このタスクでは `@pytest.mark.xfail(reason="Task 2 で最初の migration が入る", strict=True)`
を `test_apply_migrations_is_idempotent` に付けてコミットし、Task 2 で外す。

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/clock.py app/src/mediaferry/ids.py app/src/mediaferry/db app/tests/conftest.py app/tests/test_db_migrate.py app/pyproject.toml
git commit -m "feat(mediaferry): add the sqlite connection and migration runner"
```

---

### Task 2: スキーマ 0001 — ジョブと設定

**Files:**
- Create: `app/src/mediaferry/db/migrations/0001_jobs_and_settings.sql`
- Modify: `app/tests/test_db_migrate.py`（Task 1 の `xfail` を外す）
- Test: `app/tests/test_schema_jobs.py`

**Interfaces:**
- Consumes: `apply_migrations`, `db` フィクスチャ（Task 1）
- Produces: テーブル `job` / `job_event` / `app_setting`

ジョブを最初の版に置くのは、`artifact_staging` と `upload_record` が `job.id` を
参照するため。外部キーの親テーブルが先に存在していないと、テストで行を作れない。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_schema_jobs.py`:

```python
import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.ids import new_id


def a_job(db, **over):
    row = {
        "id": new_id(),
        "type": "import",
        "status": "queued",
        "params_json": "{}",
        "created_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO job ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_job_status_is_constrained(db):
    with pytest.raises(sqlite3.IntegrityError):
        a_job(db, status="halfway")


def test_job_type_is_constrained(db):
    with pytest.raises(sqlite3.IntegrityError):
        a_job(db, type="rm_rf")


def test_lease_columns_are_all_null_or_all_set(db):
    """片方だけ残ると、期限切れ判定が「期限なし」に化ける."""
    with pytest.raises(sqlite3.IntegrityError):
        a_job(db, lease_token="t")
    a_job(db, lease_token="t", lease_expires_at=now_iso())


def test_job_event_seq_is_unique_per_job(db):
    job_id = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
        (job_id, now_iso()),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'y', ?)",
            (job_id, now_iso()),
        )


def test_job_event_id_is_monotonic_across_jobs(db):
    """SSE は id の昇順で再開するので、ジョブをまたいで単調でなければならない."""
    first, second = a_job(db), a_job(db)
    for job_id in (first, second):
        db.execute(
            "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
            (job_id, now_iso()),
        )
    ids = [r[0] for r in db.execute("SELECT id FROM job_event ORDER BY id")]
    assert ids == sorted(ids) and len(set(ids)) == 2


def test_job_event_ids_are_not_reused_after_a_job_is_deleted(db):
    """AUTOINCREMENT が無いと rowid が再利用され、SSE が既読の id を再発行する.

    Last-Event-ID より小さい id は配信済みとして飛ばされるので、再利用された
    イベントはクライアントに永久に届かない。
    """
    first = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
        (first, now_iso()),
    )
    used = db.execute("SELECT max(id) FROM job_event").fetchone()[0]
    db.execute("DELETE FROM job WHERE id = ?", (first,))

    second = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'y', ?)",
        (second, now_iso()),
    )
    assert db.execute("SELECT max(id) FROM job_event").fetchone()[0] > used


def test_job_events_go_away_with_the_job(db):
    job_id = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
        (job_id, now_iso()),
    )
    db.execute("DELETE FROM job WHERE id = ?", (job_id,))
    assert db.execute("SELECT count(*) FROM job_event").fetchone()[0] == 0


def test_app_setting_keys_are_unique(db):
    db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'info', ?)", (now_iso(),))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'debug', ?)", (now_iso(),))
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_schema_jobs.py -v`
Expected: FAIL（`sqlite3.OperationalError: no such table: job`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/db/migrations/0001_jobs_and_settings.sql`:

```sql
-- ジョブと設定。artifact_staging と upload_record が job.id を参照するので
-- 最初の版に置く。

CREATE TABLE job (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL CHECK (type IN (
                         'scan', 'import', 'detect_groups', 'merge',
                         'upload', 'recompute_timestamps', 'deep_verify')),
    status           TEXT NOT NULL CHECK (status IN (
                         'queued', 'running', 'cancelling', 'cancelled',
                         'interrupted', 'succeeded', 'failed')),
    params_json      TEXT NOT NULL,
    progress_json    TEXT,
    lease_token      TEXT,
    lease_expires_at TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    error            TEXT,
    -- 片方だけ残ると「期限なし」と区別できなくなる。
    CHECK ((lease_token IS NULL AND lease_expires_at IS NULL)
        OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX job_queued ON job (created_at) WHERE status = 'queued';
CREATE INDEX job_running ON job (lease_expires_at) WHERE status IN ('running', 'cancelling');

-- id は SSE の Last-Event-ID に使うので、ジョブをまたいで単調増加させる。
CREATE TABLE job_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    level     TEXT NOT NULL CHECK (level IN ('debug', 'info', 'warning', 'error')),
    message   TEXT NOT NULL,
    data_json TEXT,
    at        TEXT NOT NULL,
    UNIQUE (job_id, seq)
);

CREATE TABLE app_setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

`app/tests/test_db_migrate.py` の `test_apply_migrations_is_idempotent` から
`xfail` マーカーを削除する。

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_schema_jobs.py app/tests/test_db_migrate.py -v`
Expected: すべて PASS

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/db/migrations/0001_jobs_and_settings.sql app/tests/test_schema_jobs.py app/tests/test_db_migrate.py
git commit -m "feat(mediaferry): add the job and setting tables"
```

---

### Task 3: スキーマ 0002 — プロファイルとソース側

**Files:**
- Create: `app/src/mediaferry/db/migrations/0002_profiles_and_sources.sql`
- Test: `app/tests/test_schema_sources.py`

**Interfaces:**
- Consumes: 0001（`schema_migration`）
- Produces: `device_profile` / `profile_revision` / `source_device` /
  `volume_instance` / `volume_presence` / `source_entry`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_schema_sources.py`:

```python
import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.ids import new_id


def a_profile(db, slug="dji-osmo"):
    profile_id, revision_id = new_id(), new_id()
    db.execute(
        "INSERT INTO device_profile (id, slug, name, builtin, created_at)"
        " VALUES (?, ?, 'DJI Osmo', 1, ?)",
        (profile_id, slug, now_iso()),
    )
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES (?, ?, 1, '{}', 1, ?)",
        (revision_id, profile_id, now_iso()),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
        (revision_id, profile_id),
    )
    return profile_id, revision_id


def a_volume(db, profile=None, **over):
    profile_id, revision_id = profile or (None, None)
    row = {
        "id": new_id(),
        "fs_uuid": "26B1-2FD6",
        "fs_type": "exfat",
        "fs_label": "SD_Card",
        "size_bytes": 512_000_000_000,
        "identity_confidence": "high",
        "profile_id": profile_id,
        "profile_revision_id": revision_id,
        "first_seen_at": now_iso(),
        "last_seen_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO volume_instance ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_profile_slug_is_unique(db):
    a_profile(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_profile(db)


def test_profile_revision_is_immutable(db):
    _, revision_id = a_profile(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE profile_revision SET definition_json = '{\"x\":1}' WHERE id = ?",
            (revision_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM profile_revision WHERE id = ?", (revision_id,))


def test_current_revision_must_belong_to_the_same_profile(db):
    first, _ = a_profile(db, slug="a")
    _, other_revision = a_profile(db, slug="b")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
            (other_revision, first),
        )


def test_source_device_identity_is_the_whole_tuple(db):
    """serial は Linux ガジェットの既定値 (123456789ABCDEF) でありうるので、
    単独では識別子にならない。product まで含めた組で一意にする."""
    base = ("2ca3", "0020", "OsmoPocket4-AAA", "123456789ABCDEF", now_iso(), now_iso())
    db.execute(
        "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), *base),
    )
    # 別の機体は product が違うので入る
    db.execute(
        "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
        " first_seen_at, last_seen_at) VALUES (?, '2ca3', '0020', 'OsmoPocket4-BBB',"
        " '123456789ABCDEF', ?, ?)",
        (new_id(), now_iso(), now_iso()),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id(), *base),
        )


def test_volume_identity_confidence_is_constrained(db):
    with pytest.raises(sqlite3.IntegrityError):
        a_volume(db, identity_confidence="probably")


def test_volume_identity_is_unique_only_when_the_uuid_is_known(db):
    a_volume(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_volume(db)
    # UUID が空のカードは同定できないので、同じ形でも別行として残す
    a_volume(db, fs_uuid="")
    a_volume(db, fs_uuid="")


def test_volume_profile_revision_must_belong_to_the_profile(db):
    first = a_profile(db, slug="a")
    _, other_revision = a_profile(db, slug="b")
    with pytest.raises(sqlite3.IntegrityError):
        a_volume(db, profile=(first[0], other_revision))


def test_source_entry_is_unique_per_volume_and_path(db):
    volume_id = a_volume(db)
    row = (new_id(), volume_id, "DCIM/DJI_001/A.MP4", 10, 1, "abc", 1, "seen", now_iso())
    sql = (
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    db.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, (new_id(), *row[1:]))


def test_presence_rows_survive_independently_of_the_device_node(db):
    """ジョブは device_node ではなく presence.id と generation を持つ."""
    volume_id = a_volume(db)
    for generation in (1, 2):
        db.execute(
            "INSERT INTO volume_presence (id, volume_instance_id, broker_epoch, generation,"
            " device_node, major, minor, sysfs_path, attached_at)"
            " VALUES (?, ?, 'e1', ?, '/dev/sdk', 8, 160, '/sys/x', ?)",
            (new_id(), volume_id, generation, now_iso()),
        )
    assert db.execute("SELECT count(*) FROM volume_presence").fetchone()[0] == 2
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_schema_sources.py -v`
Expected: FAIL（`no such table: device_profile`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/db/migrations/0002_profiles_and_sources.sql`:

```sql
-- プロファイルとソース側（デバイス・ボリューム・スキャン結果）。

CREATE TABLE device_profile (
    id                  TEXT PRIMARY KEY,
    -- ライブラリのパスに使うので作成後は変更しない。
    slug                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    builtin             INTEGER NOT NULL CHECK (builtin IN (0, 1)),
    archived_at         TEXT,
    current_revision_id TEXT,
    created_at          TEXT NOT NULL,
    -- 他プロファイルの版を現行にできないよう複合外部キーで縛る。
    FOREIGN KEY (id, current_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT
);

CREATE TABLE profile_revision (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES device_profile(id) ON DELETE RESTRICT,
    revision        INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (profile_id, revision),
    UNIQUE (profile_id, id)
);

-- 過去データの解釈が後から変わらないよう、版は不変にする。
CREATE TRIGGER profile_revision_no_update BEFORE UPDATE ON profile_revision
BEGIN
    SELECT RAISE(ABORT, 'profile_revision is immutable');
END;

CREATE TRIGGER profile_revision_no_delete BEFORE DELETE ON profile_revision
BEGIN
    SELECT RAISE(ABORT, 'profile_revision is immutable');
END;

-- serial は機種の既定値でありうるので、識別は 4 つ組で行う。
-- SQLite の UNIQUE は NULL 同士を区別するため、欠損は '' で表す。
CREATE TABLE source_device (
    id             TEXT PRIMARY KEY,
    usb_vendor_id  TEXT NOT NULL,
    usb_product_id TEXT NOT NULL,
    usb_product    TEXT NOT NULL DEFAULT '',
    serial         TEXT NOT NULL DEFAULT '',
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    UNIQUE (usb_vendor_id, usb_product_id, usb_product, serial)
);

-- カードはリーダーの間を移動するので、デバイスとは独立に記憶する。
CREATE TABLE volume_instance (
    id                      TEXT PRIMARY KEY,
    fs_uuid                 TEXT NOT NULL DEFAULT '',
    fs_type                 TEXT NOT NULL,
    fs_label                TEXT NOT NULL DEFAULT '',
    size_bytes              INTEGER NOT NULL,
    identity_confidence     TEXT NOT NULL CHECK (identity_confidence IN ('high', 'low')),
    content_manifest_digest TEXT,
    last_source_device_id   TEXT REFERENCES source_device(id) ON DELETE SET NULL,
    profile_id              TEXT REFERENCES device_profile(id) ON DELETE RESTRICT,
    profile_revision_id     TEXT,
    trusted_at              TEXT,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT
);

-- UUID の無いカードは同定できない。推測でしかない同定に UNIQUE を掛けない。
CREATE UNIQUE INDEX volume_instance_identity
    ON volume_instance (fs_uuid, fs_type, size_bytes) WHERE fs_uuid <> '';

-- 同じ identity のカードが同時に 2 枚挿さりうるので、接続ごとに行を持つ。
-- 行は「接続」1 つに対応する。列挙のたびに増やさない。増やすと、キューに
-- 積んだときの presence と実行時の presence が別物になって必ず stale になり、
-- 抜けたポートの古い行が live のまま残って同定の確度を永久に下げる。
CREATE TABLE volume_presence (
    id                 TEXT PRIMARY KEY,
    volume_instance_id TEXT NOT NULL REFERENCES volume_instance(id) ON DELETE CASCADE,
    broker_epoch       TEXT NOT NULL,
    generation         INTEGER NOT NULL,
    device_node        TEXT NOT NULL,
    major              INTEGER NOT NULL,
    minor              INTEGER NOT NULL,
    sysfs_path         TEXT NOT NULL,
    attached_at        TEXT NOT NULL,
    detached_at        TEXT,
    UNIQUE (volume_instance_id, broker_epoch, generation, major, minor)
);

CREATE INDEX volume_presence_live
    ON volume_presence (volume_instance_id) WHERE detached_at IS NULL;

CREATE TABLE source_entry (
    id                  TEXT PRIMARY KEY,
    volume_instance_id  TEXT NOT NULL REFERENCES volume_instance(id) ON DELETE CASCADE,
    -- カード上の原名。保存先の名前 (media_file.rel_path) とは衝突時に食い違う。
    rel_path            TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    mtime_ns            INTEGER NOT NULL,
    quick_fingerprint   TEXT NOT NULL,
    fingerprint_version INTEGER NOT NULL,
    media_file_id       TEXT,
    state               TEXT NOT NULL CHECK (state IN ('seen', 'importing', 'published', 'failed')),
    observed_at         TEXT NOT NULL,
    UNIQUE (volume_instance_id, rel_path)
);
```

`source_entry.media_file_id` に外部キーを付けないのは、`media_file` が
0003 で作られるため。**0003 でこの列に外部キーを足す**（Task 4 で行う）。

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_schema_sources.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験でテストが効いていることを確かめる**

`0002` の `UNIQUE (usb_vendor_id, usb_product_id, usb_product, serial)` を
`UNIQUE (serial)` に書き換え、`uv run pytest app/tests/test_schema_sources.py -v`
が `test_source_device_identity_is_the_whole_tuple` で落ちることを確認してから戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/migrations/0002_profiles_and_sources.sql app/tests/test_schema_sources.py
git commit -m "feat(mediaferry): add the profile and source tables"
```

---

### Task 4: スキーマ 0003 — アーティファクトと結合

**Files:**
- Create: `app/src/mediaferry/db/migrations/0003_artifacts_and_merges.sql`
- Create: `app/tests/__init__.py`（空。テスト間の相対 import に要る）
- Test: `app/tests/test_schema_artifacts.py`

**Interfaces:**
- Consumes: 0001（`job`）、0002（`profile_revision`、`source_entry`）
- Produces: `artifact_staging` / `media_file` / `merge_group` / `merge_member`

`source_entry.media_file_id` へ後付けで外部キーを張るため、SQLite の
`ALTER TABLE ... ADD CONSTRAINT` が無い制約を、テーブル再作成で回避する。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_schema_artifacts.py`:

```python
import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.ids import new_id

from .test_schema_jobs import a_job
from .test_schema_sources import a_profile, a_volume


def a_media_file(db, profile, **over):
    profile_id, revision_id = profile
    row = {
        "id": new_id(),
        "role": "original",
        "profile_id": profile_id,
        "profile_revision_id": revision_id,
        "rel_path": f"library/dji-osmo/DCIM/{new_id()}.MP4",
        "size_bytes": 100,
        "mtime_ns": 1,
        "sha1": "0" * 40,
        "kind": "video",
        "captured_at": now_iso(),
        "captured_at_source": "filename",
        "duration_seconds": 1.5,
        "probe_state": "ok",
        "created_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO media_file ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def a_staging(db, job_id, **over):
    row = {
        "id": new_id(),
        "kind": "import",
        "job_id": job_id,
        "lease_token": "lease-1",
        "state": "writing",
        "staging_rel_path": f"staging/{job_id}/{new_id()}",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO artifact_staging ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def a_source_entry(db, volume_id):
    entry_id = new_id()
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES (?, ?, ?, 10, 1, 'abc', 1, 'seen', ?)",
        (entry_id, volume_id, f"DCIM/{entry_id}.MP4", now_iso()),
    )
    return entry_id


def test_media_rel_path_is_unique(db):
    profile = a_profile(db)
    path = "library/dji-osmo/DCIM/A.MP4"
    a_media_file(db, profile, rel_path=path)
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, rel_path=path)


def test_published_video_must_carry_a_duration(db):
    """公開前にメタデータを確定させる（§9.3 手順 5）ので、
    probe に成功した動画が duration 無しで残ることはない."""
    profile = a_profile(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, kind="video", probe_state="ok", duration_seconds=None)
    a_media_file(db, profile, kind="video", probe_state="failed", duration_seconds=None)
    a_media_file(db, profile, kind="photo", probe_state="not_applicable", duration_seconds=None)


def test_probe_state_has_no_not_run(db):
    profile = a_profile(db)
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, profile, probe_state="not_run")


def test_staged_rows_must_carry_everything_needed_to_resume(db):
    """reconciliation はパスを推測しない。staged になった時点で
    final_rel_path / content_sha1 / expected_size / metadata_json が揃う."""
    job_id = a_job(db)
    entry_id = a_source_entry(db, a_volume(db))
    # match で、狙いの CHECK （揃っているか）で落ちたことを確かめる。
    # kind 側の CHECK で落ちると、欠けた列を見逃したまま通ってしまう。
    with pytest.raises(sqlite3.IntegrityError, match="final_rel_path"):
        a_staging(
            db,
            job_id,
            state="staged",
            final_rel_path="library/x/A.MP4",
            source_entry_id=entry_id,
        )
    a_staging(
        db,
        job_id,
        state="staged",
        final_rel_path="library/x/A.MP4",
        content_sha1="0" * 40,
        expected_size=10,
        metadata_json="{}",
        source_entry_id=entry_id,
    )


def test_staging_kind_decides_which_back_reference_is_set(db):
    job_id = a_job(db)
    volume_id = a_volume(db)
    entry_id = a_source_entry(db, volume_id)
    a_staging(db, job_id, kind="import", source_entry_id=entry_id)
    with pytest.raises(sqlite3.IntegrityError):
        a_staging(db, job_id, kind="import")  # 参照元が無い
    with pytest.raises(sqlite3.IntegrityError):
        a_staging(db, job_id, kind="merge", source_entry_id=entry_id)


def test_source_entry_cannot_point_at_a_missing_media_file(db):
    volume_id = a_volume(db)
    entry_id = a_source_entry(db, volume_id)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE source_entry SET media_file_id = ? WHERE id = ?", (new_id(), entry_id))


def test_active_merge_groups_have_distinct_input_digests(db):
    profile = a_profile(db)
    first = a_merge_group(db, profile, digest="d1")
    with pytest.raises(sqlite3.IntegrityError):
        a_merge_group(db, profile, digest="d1")
    # supersede すれば同じ digest の新グループを作れる
    second = a_merge_group(db, profile, digest="d2")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    a_merge_group(db, profile, digest="d1")


def a_merge_group(db, profile, digest, **over):
    profile_id, revision_id = profile
    row = {
        "id": new_id(),
        "profile_id": profile_id,
        "profile_revision_id": revision_id,
        "status": "detected",
        "input_digest": digest,
        "detected_by": "auto",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO merge_group ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_a_media_file_belongs_to_at_most_one_active_group(db):
    profile = a_profile(db)
    media_id = a_media_file(db, profile)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (first, media_id))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (second, media_id))


def test_superseding_a_group_frees_its_members(db):
    profile = a_profile(db)
    media_id = a_media_file(db, profile)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (first, media_id))
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (second, media_id))
    active = db.execute(
        "SELECT active FROM merge_member WHERE merge_group_id = ?", (first,)
    ).fetchone()[0]
    assert active == 0


def test_a_superseded_group_cannot_gain_active_members(db):
    """旧グループの再構成で active member が復活すると、候補の除外が誤る."""
    profile = a_profile(db)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    with pytest.raises(sqlite3.IntegrityError, match="supersede"):
        db.execute(
            "INSERT INTO merge_member VALUES (?, ?, 0, 1)", (first, a_media_file(db, profile))
        )
    # 非 active としてなら履歴に残せる
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 0)", (first, a_media_file(db, profile)))
    with pytest.raises(sqlite3.IntegrityError, match="supersede"):
        db.execute("UPDATE merge_member SET active = 1 WHERE merge_group_id = ?", (first,))


def test_a_member_cannot_be_moved_into_a_superseded_group(db):
    """active な member の親を付け替えると trigger を迂回できてしまう."""
    profile = a_profile(db)
    media_id = a_media_file(db, profile)
    active_group = a_merge_group(db, profile, digest="d1")
    doomed = a_merge_group(db, profile, digest="d2")
    successor = a_merge_group(db, profile, digest="d3")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (successor, doomed))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (active_group, media_id))
    with pytest.raises(sqlite3.IntegrityError, match="supersede"):
        db.execute(
            "UPDATE merge_member SET merge_group_id = ? WHERE merge_group_id = ?",
            (doomed, active_group),
        )


def test_an_active_group_cannot_hold_an_inactive_member(db):
    """active は親の状態の写しなので、片方だけずらせない."""
    profile = a_profile(db)
    group = a_merge_group(db, profile, digest="d1")
    with pytest.raises(sqlite3.IntegrityError, match="supersede"):
        db.execute(
            "INSERT INTO merge_member VALUES (?, ?, 0, 0)", (group, a_media_file(db, profile))
        )


def test_supersede_cannot_be_undone(db):
    profile = a_profile(db)
    first = a_merge_group(db, profile, digest="d1")
    second = a_merge_group(db, profile, digest="d2")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (second, first))
    with pytest.raises(sqlite3.IntegrityError, match="irreversible"):
        db.execute("UPDATE merge_group SET superseded_by_id = NULL WHERE id = ?", (first,))


def test_a_group_cannot_supersede_itself(db):
    profile = a_profile(db)
    group = a_merge_group(db, profile, digest="d1")
    with pytest.raises(sqlite3.IntegrityError, match="itself"):
        db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (group, group))


def test_member_positions_are_unique_within_a_group(db):
    profile = a_profile(db)
    group = a_merge_group(db, profile, digest="d1")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, a_media_file(db, profile)))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, a_media_file(db, profile))
        )
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_schema_artifacts.py -v`
Expected: FAIL（`no such table: media_file`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/db/migrations/0003_artifacts_and_merges.sql`:

```sql
-- 公開されたメディアと、公開途中の状態、結合グループ。

CREATE TABLE media_file (
    id                  TEXT PRIMARY KEY,
    role                TEXT NOT NULL CHECK (role IN ('original', 'derived')),
    profile_id          TEXT NOT NULL REFERENCES device_profile(id) ON DELETE RESTRICT,
    profile_revision_id TEXT NOT NULL,
    -- DATA_ROOT からの相対パス。保存先の名前であり、カード上の原名ではない。
    rel_path            TEXT NOT NULL UNIQUE,
    size_bytes          INTEGER NOT NULL,
    mtime_ns            INTEGER NOT NULL,
    sha1                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('photo', 'video')),
    captured_at         TEXT NOT NULL,
    captured_at_source  TEXT NOT NULL CHECK (captured_at_source IN ('filename', 'exif', 'mtime')),
    captured_at_tz      TEXT,
    captured_at_note    TEXT,
    duration_seconds    REAL,
    -- ffprobe を実行していない状態 (not_run) は公開済みレコードには無い。
    probe_state         TEXT NOT NULL CHECK (probe_state IN ('ok', 'failed', 'not_applicable')),
    missing_at          TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT,
    -- probe に成功した動画は必ず duration を持つ（§9.7 の境界判定が依存する）。
    CHECK (kind <> 'video' OR probe_state <> 'ok' OR duration_seconds IS NOT NULL)
);

CREATE INDEX media_file_sha1 ON media_file (sha1);
CREATE INDEX media_file_captured_at ON media_file (captured_at);

CREATE TABLE merge_group (
    id                   TEXT PRIMARY KEY,
    profile_id           TEXT NOT NULL REFERENCES device_profile(id) ON DELETE RESTRICT,
    profile_revision_id  TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN (
                             'detected', 'merging', 'merged', 'failed', 'skipped')),
    input_digest         TEXT NOT NULL,
    output_media_file_id TEXT REFERENCES media_file(id) ON DELETE RESTRICT,
    detected_by          TEXT NOT NULL CHECK (detected_by IN ('auto', 'manual')),
    superseded_by_id     TEXT REFERENCES merge_group(id) ON DELETE RESTRICT,
    tool_version         TEXT,
    verification_json    TEXT,
    adopted_at           TEXT,
    error                TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX merge_group_active_digest
    ON merge_group (input_digest) WHERE superseded_by_id IS NULL;

-- active は merge_group.superseded_by_id の写し。SQLite の部分索引は
-- 他テーブルの列を見られないので、trigger で同期する。
CREATE TABLE merge_member (
    merge_group_id TEXT NOT NULL REFERENCES merge_group(id) ON DELETE CASCADE,
    media_file_id  TEXT NOT NULL REFERENCES media_file(id) ON DELETE RESTRICT,
    position       INTEGER NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    PRIMARY KEY (merge_group_id, media_file_id),
    UNIQUE (merge_group_id, position)
);

CREATE UNIQUE INDEX merge_member_one_active_group
    ON merge_member (media_file_id) WHERE active = 1;

CREATE TRIGGER merge_group_supersede_deactivates_members
AFTER UPDATE OF superseded_by_id ON merge_group
WHEN NEW.superseded_by_id IS NOT NULL AND OLD.superseded_by_id IS NULL
BEGIN
    UPDATE merge_member SET active = 0 WHERE merge_group_id = NEW.id;
END;

-- supersede は不可逆。戻せると active と親の状態が乖離し、旧グループの
-- member が復活して現グループの候補判定を壊す。
CREATE TRIGGER merge_group_supersede_is_final
BEFORE UPDATE OF superseded_by_id ON merge_group
WHEN OLD.superseded_by_id IS NOT NULL AND NEW.superseded_by_id IS NOT OLD.superseded_by_id
BEGIN
    SELECT RAISE(ABORT, 'supersede is irreversible');
END;

CREATE TRIGGER merge_group_no_self_supersede
BEFORE UPDATE OF superseded_by_id ON merge_group
WHEN NEW.superseded_by_id = NEW.id
BEGIN
    SELECT RAISE(ABORT, 'a group cannot supersede itself');
END;

-- active の denormalize は片方向の trigger だけだと、既に superseded の
-- グループへ後から active な member を足して壊せる。
-- active は親の superseded 状態の写しなので、両方向で一致を強制する。
-- 片方向だけだと、active な member の merge_group_id を superseded な
-- グループへ付け替えて迂回できる。
CREATE TRIGGER merge_member_insert_matches_parent
BEFORE INSERT ON merge_member
WHEN NEW.active <> (
    SELECT superseded_by_id IS NULL FROM merge_group WHERE id = NEW.merge_group_id
)
BEGIN
    SELECT RAISE(ABORT, 'member active flag must match the group supersede state');
END;

CREATE TRIGGER merge_member_update_matches_parent
BEFORE UPDATE OF merge_group_id, active ON merge_member
WHEN NEW.active <> (
    SELECT superseded_by_id IS NULL FROM merge_group WHERE id = NEW.merge_group_id
)
BEGIN
    SELECT RAISE(ABORT, 'member active flag must match the group supersede state');
END;

-- 取り込みと結合が同じ公開プロトコルを通る。片方だけ回収不能にしない。
CREATE TABLE artifact_staging (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('import', 'merge')),
    job_id           TEXT NOT NULL REFERENCES job(id) ON DELETE RESTRICT,
    lease_token      TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('writing', 'staged', 'published')),
    staging_rel_path TEXT NOT NULL,
    final_rel_path   TEXT,
    expected_size    INTEGER,
    content_sha1     TEXT,
    metadata_json    TEXT,
    source_entry_id  TEXT REFERENCES source_entry(id) ON DELETE RESTRICT,
    merge_group_id   TEXT REFERENCES merge_group(id) ON DELETE RESTRICT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    -- staged 以降は永続情報だけで公開を再開できる。
    CHECK (state = 'writing' OR (final_rel_path IS NOT NULL AND expected_size IS NOT NULL
           AND content_sha1 IS NOT NULL AND metadata_json IS NOT NULL)),
    CHECK ((kind = 'import' AND source_entry_id IS NOT NULL AND merge_group_id IS NULL)
        OR (kind = 'merge' AND merge_group_id IS NOT NULL AND source_entry_id IS NULL))
);

CREATE INDEX artifact_staging_open ON artifact_staging (state) WHERE state <> 'published';

-- media_file が 0002 の時点では無かったので、外部キーをここで足す。
-- SQLite に ADD CONSTRAINT が無いため、作り直して移し替える。
CREATE TABLE source_entry_new (
    id                  TEXT PRIMARY KEY,
    volume_instance_id  TEXT NOT NULL REFERENCES volume_instance(id) ON DELETE CASCADE,
    rel_path            TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    mtime_ns            INTEGER NOT NULL,
    quick_fingerprint   TEXT NOT NULL,
    fingerprint_version INTEGER NOT NULL,
    media_file_id       TEXT REFERENCES media_file(id) ON DELETE SET NULL,
    state               TEXT NOT NULL CHECK (state IN ('seen', 'importing', 'published', 'failed')),
    observed_at         TEXT NOT NULL,
    UNIQUE (volume_instance_id, rel_path)
);

INSERT INTO source_entry_new SELECT * FROM source_entry;
DROP TABLE source_entry;
ALTER TABLE source_entry_new RENAME TO source_entry;
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_schema_artifacts.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`artifact_staging` の `CHECK (state = 'writing' OR ...)` を削除し、
`test_staged_rows_must_carry_everything_needed_to_resume` が落ちることを
確認してから戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/migrations/0003_artifacts_and_merges.sql app/tests/__init__.py app/tests/test_schema_artifacts.py
git commit -m "feat(mediaferry): add the artifact and merge tables"
```

---

### Task 5: スキーマ 0004 — 転送先とアップロード

**Files:**
- Create: `app/src/mediaferry/db/migrations/0004_destinations_and_uploads.sql`
- Test: `app/tests/test_schema_uploads.py`

**Interfaces:**
- Consumes: 0001（`job`）、0003（`media_file`, `merge_group`）
- Produces: `upload_destination` / `destination_credential` / `destination_revision` /
  `upload_record`

Phase 3 まで使わないが、**後から直すと全 credential の migration が要る**ため
今のうちに確定させる（HANDOFF §5）。宛先の取り違えは最も危険な誤りなので、
アプリの検証ではなく DB の制約で防ぐ（§8）。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_schema_uploads.py`:

```python
import sqlite3

import pytest

from mediaferry.clock import now_iso
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
    profile = a_profile(db)
    dest = a_destination(db)
    media_id = a_media_file(db, profile)
    an_upload(db, dest, media_id)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, media_id)
    # epoch を進めれば、旧記録を監査履歴として残したまま送り直せる
    another_revision(db, dest, epoch=2)
    an_upload(db, dest, media_id, target_epoch=2)


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
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_schema_uploads.py -v`
Expected: FAIL（`no such table: upload_destination`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/db/migrations/0004_destinations_and_uploads.sql`:

```sql
-- 転送先プロファイルとアップロード履歴。
-- 宛先の取り違えはアプリの検証だけに頼らず、複合外部キーで DB が防ぐ。

CREATE TABLE upload_destination (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    kind                TEXT NOT NULL CHECK (kind IN ('immich')),
    enabled             INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    -- 物理削除しない。履歴と監査情報を残す。
    archived_at         TEXT,
    current_revision_id TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (id, current_revision_id)
        REFERENCES destination_revision(destination_id, id) ON DELETE RESTRICT
);

CREATE TABLE destination_credential (
    id               TEXT PRIMARY KEY,
    destination_id   TEXT NOT NULL REFERENCES upload_destination(id) ON DELETE RESTRICT,
    revision         INTEGER NOT NULL,
    -- core/crypto.py の自己記述フォーマット。参照が絶えたら消して purged_at を立てる。
    secret_encrypted BLOB,
    key_fingerprint  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    purged_at        TEXT,
    UNIQUE (destination_id, revision),
    UNIQUE (destination_id, id),
    CHECK ((secret_encrypted IS NOT NULL AND purged_at IS NULL)
        OR (secret_encrypted IS NULL AND purged_at IS NOT NULL))
);

-- ある時点の接続設定一式のスナップショット。編集のたびに行が増える。
CREATE TABLE destination_revision (
    id                 TEXT PRIMARY KEY,
    destination_id     TEXT NOT NULL REFERENCES upload_destination(id) ON DELETE RESTRICT,
    revision           INTEGER NOT NULL,
    -- 向き先が変わったときだけ進む。履歴を引き継いでよいかの境界。
    target_epoch       INTEGER NOT NULL,
    -- API を叩きに行くエンドポイント。CDN やリバースプロキシを経由しない。
    base_url           TEXT NOT NULL,
    -- 画面のリンク生成にだけ使う。通信には使わない。
    public_url         TEXT,
    credential_id      TEXT NOT NULL,
    -- 同一性ではなく、向き先が変わったことを検知する guard。
    remote_user_id     TEXT,
    server_instance_id TEXT,
    verified_at        TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE (destination_id, revision),
    UNIQUE (destination_id, id),
    UNIQUE (destination_id, target_epoch, id),
    FOREIGN KEY (destination_id, credential_id)
        REFERENCES destination_credential(destination_id, id) ON DELETE RESTRICT
);

CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;

CREATE TRIGGER destination_revision_no_delete BEFORE DELETE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;

CREATE TABLE upload_record (
    id                      TEXT PRIMARY KEY,
    destination_id          TEXT NOT NULL REFERENCES upload_destination(id) ON DELETE RESTRICT,
    target_epoch            INTEGER NOT NULL,
    media_file_id           TEXT NOT NULL REFERENCES media_file(id) ON DELETE RESTRICT,
    state                   TEXT NOT NULL CHECK (state IN (
                                'pending', 'checking', 'uploading', 'asset_known', 'tagging',
                                'fixing_datetime', 'awaiting_datetime_approval',
                                'complete', 'failed', 'needs_recheck')),
    -- 送信を許可した根拠。claim 時にどの条件で再評価するかを決める。
    selection_rule          TEXT NOT NULL CHECK (selection_rule IN (
                                'default', 'failed_group_member', 'adopted_derived')),
    origin                  TEXT NOT NULL CHECK (origin IN (
                                'created_by_us', 'pre_existing', 'unknown')),
    -- 初回 checking が reject なら「以前から存在した」ことを証明できる。
    -- accept だったことは自作の証明にならない。
    first_check_result      TEXT CHECK (first_check_result IN ('accept', 'reject')),
    remote_asset_id         TEXT,
    remote_is_trashed       INTEGER CHECK (remote_is_trashed IN (0, 1)),
    remote_checked_at       TEXT,
    checksum                TEXT,
    attempts                INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    eligibility_reason      TEXT,
    merge_group_id          TEXT REFERENCES merge_group(id) ON DELETE RESTRICT,
    claim_job_id            TEXT REFERENCES job(id) ON DELETE RESTRICT,
    claim_token             TEXT,
    claim_expires_at        TEXT,
    destination_revision_id TEXT,
    -- 状態機械とは直交するフラグ。state の列挙には混ぜない。
    invalidated_at          TEXT,
    invalidated_reason      TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (destination_id, target_epoch, media_file_id),
    CHECK ((claim_job_id IS NULL AND claim_token IS NULL AND claim_expires_at IS NULL)
        OR (claim_job_id IS NOT NULL AND claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),
    -- 進行中なら所有者と、どの設定で送っているかが必ず分かる。
    CHECK (state NOT IN ('checking', 'uploading', 'asset_known', 'tagging', 'fixing_datetime')
        OR (claim_job_id IS NOT NULL AND destination_revision_id IS NOT NULL)),
    -- 終端と待機状態に claim が残っていると、明示操作しても期限まで claim できない。
    CHECK (state NOT IN ('pending', 'needs_recheck', 'complete', 'failed',
                         'awaiting_datetime_approval')
        OR claim_job_id IS NULL),
    -- 送信済みなのにどの設定へ送ったか分からない、を作らない。
    CHECK (state <> 'complete' OR destination_revision_id IS NOT NULL),
    FOREIGN KEY (destination_id, target_epoch, destination_revision_id)
        REFERENCES destination_revision(destination_id, target_epoch, id) ON DELETE RESTRICT
);

-- 複合外部キーは destination_revision_id が NULL だと効かない。pending の行が
-- 存在しない epoch を名乗れると、後から同じ epoch の revision が別の意味で
-- 作られたときに、どの設定へ送ったかを復元できなくなる。
CREATE TRIGGER upload_record_epoch_must_exist
BEFORE INSERT ON upload_record
WHEN NOT EXISTS (
    SELECT 1 FROM destination_revision
     WHERE destination_id = NEW.destination_id AND target_epoch = NEW.target_epoch
)
BEGIN
    SELECT RAISE(ABORT, 'no revision exists for this destination and epoch');
END;

-- 同一性の 3 欄は不変。書き換えられると、INSERT 時の guard も複合 FK も
-- 迂回して「存在しない epoch の pending 行」を作れる。
CREATE TRIGGER upload_record_identity_is_immutable
BEFORE UPDATE OF destination_id, target_epoch, media_file_id ON upload_record
WHEN NEW.destination_id IS NOT OLD.destination_id
  OR NEW.target_epoch IS NOT OLD.target_epoch
  OR NEW.media_file_id IS NOT OLD.media_file_id
BEGIN
    SELECT RAISE(ABORT, 'the identity of an upload record is immutable');
END;

CREATE INDEX upload_record_by_media ON upload_record (media_file_id);
CREATE INDEX upload_record_claimable
    ON upload_record (destination_id, state) WHERE invalidated_at IS NULL;

CREATE TRIGGER upload_record_selection_rule_immutable
BEFORE UPDATE OF selection_rule ON upload_record
WHEN NEW.selection_rule <> OLD.selection_rule
BEGIN
    SELECT RAISE(ABORT, 'selection_rule is immutable');
END;

CREATE TRIGGER upload_record_first_check_immutable
BEFORE UPDATE OF first_check_result ON upload_record
WHEN OLD.first_check_result IS NOT NULL AND NEW.first_check_result IS NOT OLD.first_check_result
BEGIN
    SELECT RAISE(ABORT, 'first_check_result is immutable');
END;
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_schema_uploads.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`upload_record` の複合外部キー `(destination_id, target_epoch, destination_revision_id)`
を単純な `destination_revision_id` への外部キーに置き換え、
`test_upload_record_revision_must_match_destination_and_epoch` が落ちることを
確認してから戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/migrations/0004_destinations_and_uploads.sql app/tests/test_schema_uploads.py
git commit -m "feat(mediaferry): add the destination and upload tables"
```

---

### Task 6: 設定の解決（env > DB > 既定値）

**Files:**
- Create: `app/src/mediaferry/settings.py`
- Test: `app/tests/test_settings.py`

**Interfaces:**
- Consumes: `app_setting` テーブル（Task 2）、`mediaferry.clock.now_iso`
- Produces:
  - `mediaferry.settings.SETTING_SPECS: dict[str, SettingSpec]`
  - `mediaferry.settings.Tier`（`BOOTSTRAP` / `RESTART` / `RUNTIME`）
  - `mediaferry.settings.SettingValue(key, value, source, locked, tier, writable)`
  - `mediaferry.settings.SettingsService(conn, env)` — `.snapshot()`, `.describe_all()`, `.set(key, value) -> Tier`
  - `mediaferry.settings.Settings`（型付きスナップショット。`data_root`, `bind_host`,
    `http_port`, `auth_password`, `secret_key`, `upload_concurrency`,
    `upload_timeout_seconds`, `upload_max_attempts`, `auto_import`,
    `default_timezone`, `log_level`）
  - 例外 `SettingLocked` / `SettingInvalid`
  - `mediaferry.settings.startup_warnings(settings) -> list[str]`
  - `mediaferry.settings.bootstrap_data_root(env) -> Path`

**設定を 3 層に分ける。** どこで値が効くかを型に載せないと、「UI で変えたのに
反映されない」「DB に置いてはいけない秘密が置ける」が両方起きる。

| 層 | 意味 | キー |
| --- | --- | --- |
| `BOOTSTRAP` | **env のみ。DB に保存できない。** DB 自身の場所を決める値と、DB の外に無いと意味が無い秘密 | `DATA_ROOT`, `BROKER_SOCKET`, `SECRET_KEY`, `AUTH_PASSWORD` |
| `RESTART` | DB に保存でき、次回起動から効く | `BIND_HOST`, `HTTP_PORT` |
| `RUNTIME` | DB に保存でき、次のジョブ／リクエストから効く | `DEFAULT_TIMEZONE`, `LOG_LEVEL`, `AUTO_IMPORT`, `UPLOAD_*` |

`SECRET_KEY` が `BOOTSTRAP` なのは §12.3 の境界そのものだから。DB に置けると
「暗号文と復号鍵が同じバックアップに入る」ので、暗号化が何も守らなくなる。
`AUTH_PASSWORD` も Phase 1 では env のみとする（Phase 4 で認証を入れるときに
Argon2 ハッシュの保存先を別に作る。平文を `app_setting` に置く経路は作らない）。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_settings.py`:

```python
import base64

import pytest

from mediaferry.clock import now_iso
from mediaferry.settings import (
    SettingInvalid,
    SettingLocked,
    SettingsService,
    Tier,
    startup_warnings,
)


def service(db, **env):
    return SettingsService(db, env={f"MEDIAFERRY_{k}": v for k, v in env.items()})


def test_defaults_are_used_when_nothing_is_set(db):
    snapshot = service(db).snapshot()
    assert snapshot.bind_host == "127.0.0.1"
    assert snapshot.http_port == 8080
    assert str(snapshot.broker_socket) == "/run/mediaferry/broker.sock"
    assert snapshot.auto_import == "trusted"
    assert snapshot.upload_concurrency == 2
    assert snapshot.default_timezone is None


def test_db_overrides_the_default(db):
    db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'debug', ?)", (now_iso(),))
    assert service(db).snapshot().log_level == "debug"


def test_env_overrides_the_db(db):
    """TrueNAS のアプリ設定画面が常に事実と一致するようにするため、env が勝つ."""
    db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'debug', ?)", (now_iso(),))
    assert service(db, LOG_LEVEL="warning").snapshot().log_level == "warning"


def test_env_backed_settings_are_locked(db):
    described = {s.key: s for s in service(db, HTTP_PORT="9000").describe_all()}
    assert described["HTTP_PORT"].source == "env"
    assert described["HTTP_PORT"].locked is True
    assert described["LOG_LEVEL"].locked is False


def test_writing_a_locked_setting_is_refused(db):
    with pytest.raises(SettingLocked):
        service(db, HTTP_PORT="9000").set("HTTP_PORT", "9001")


def test_bootstrap_secrets_cannot_be_stored_in_the_db(db):
    """暗号文と復号鍵が同じバックアップに入ると、暗号化が何も守らなくなる."""
    svc = service(db)
    for key in ("SECRET_KEY", "AUTH_PASSWORD", "DATA_ROOT", "BROKER_SOCKET"):
        with pytest.raises(SettingLocked, match="env"):
            svc.set(key, "x")
    assert db.execute("SELECT count(*) FROM app_setting").fetchone()[0] == 0


def test_bootstrap_rows_in_the_db_are_ignored(db):
    """書けないだけでなく読みもしない.

    set() は BOOTSTRAP を弾くが、旧版・手動編集・将来の不具合で行が紛れ込むと、
    読む側が拾った瞬間に「鍵は DB の外」という境界が崩れる。
    """
    for key, value in (
        ("SECRET_KEY", base64.b64encode(bytes(32)).decode()),
        ("AUTH_PASSWORD", "s3cret"),
        ("DATA_ROOT", "/elsewhere"),
        ("BROKER_SOCKET", "/tmp/evil.sock"),  # noqa: S108
    ):
        db.execute("INSERT INTO app_setting VALUES (?, ?, ?)", (key, value, now_iso()))

    snapshot = service(db).snapshot()
    assert snapshot.secret_key is None
    assert snapshot.auth_password is None
    assert str(snapshot.data_root) == "/data"
    assert str(snapshot.broker_socket) == "/run/mediaferry/broker.sock"

    described = {s.key: s for s in service(db).describe_all()}
    assert described["SECRET_KEY"].source == "default"
    assert described["DATA_ROOT"].source == "default"


def test_set_reports_when_the_value_takes_effect(db):
    svc = service(db)
    assert svc.set("HTTP_PORT", "9001") is Tier.RESTART
    assert svc.set("LOG_LEVEL", "debug") is Tier.RUNTIME


def test_runtime_values_are_visible_to_the_next_snapshot(db):
    """UI で TZ を設定した直後の取り込みが古い値を見ないこと."""
    svc = service(db)
    assert svc.snapshot().default_timezone is None
    svc.set("DEFAULT_TIMEZONE", "Asia/Tokyo")
    assert svc.snapshot().default_timezone == "Asia/Tokyo"


def test_set_validates_before_storing(db):
    svc = service(db)
    with pytest.raises(SettingInvalid):
        svc.set("HTTP_PORT", "not-a-port")
    with pytest.raises(SettingInvalid):
        svc.set("AUTO_IMPORT", "always")
    with pytest.raises(SettingInvalid):
        svc.set("DEFAULT_TIMEZONE", "Mars/Olympus")
    assert db.execute("SELECT count(*) FROM app_setting").fetchone()[0] == 0


def test_unknown_keys_are_refused(db):
    with pytest.raises(SettingInvalid):
        service(db).set("SHELL", "/bin/sh")


def test_secret_key_must_be_32_random_bytes_in_base64(db):
    # base64 として読めない
    with pytest.raises(SettingInvalid, match="base64"):
        service(db, SECRET_KEY="hunter2").snapshot()
    # base64 としては読めるが 256bit ではない（パスワードを base64 にしただけ）
    short = base64.b64encode(b"hunter2").decode()
    with pytest.raises(SettingInvalid, match="32"):
        service(db, SECRET_KEY=short).snapshot()
    ok = base64.b64encode(bytes(32)).decode()
    assert service(db, SECRET_KEY=ok).snapshot().secret_key == bytes(32)


def test_secrets_are_masked_when_described(db):
    """API 応答にもログにも値そのものを出さない."""
    described = {s.key: s for s in service(db, AUTH_PASSWORD="s3cret").describe_all()}
    assert described["AUTH_PASSWORD"].value == "********"
    assert "s3cret" not in repr(described["AUTH_PASSWORD"])
    assert described["AUTH_PASSWORD"].writable is False


def test_non_loopback_without_auth_warns(db):
    warnings = startup_warnings(service(db, BIND_HOST="0.0.0.0").snapshot())  # noqa: S104
    assert any("認証" in w for w in warnings)
    assert startup_warnings(service(db).snapshot()) == []
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_settings.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.settings'`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/settings.py`:

```python
"""インフラ設定の解決.

優先順位は 環境変数 > DB（Web 画面） > 既定値。env で指定された項目は画面で
ロックされる。TrueNAS のアプリ設定画面に書いた値が、常にアプリの実際の挙動と
一致する状態を保つための順序である。

転送先プロファイルはここに含まれない。ユーザのデータであって基盤の設定では
ないので、DB だけで管理する（§12）。
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .clock import now_iso

ENV_PREFIX = "MEDIAFERRY_"
MASK = "********"


class Tier(Enum):
    """値がどこに置けて、いつ効くか."""

    BOOTSTRAP = "bootstrap"  # env のみ。DB に保存できない
    RESTART = "restart"  # DB に保存でき、次回起動から効く
    RUNTIME = "runtime"  # DB に保存でき、次のジョブ／リクエストから効く


class SettingLocked(RuntimeError):
    """env で固定されているか、DB へ保存してはいけない項目を書こうとした."""


class SettingInvalid(ValueError):
    """未知のキー、または値が仕様を満たさない."""


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: str | None
    parse: Callable[[str], Any]
    tier: Tier
    secret: bool = False


@dataclass(frozen=True)
class SettingValue:
    key: str
    value: str | None
    source: str  # env / db / default
    locked: bool
    tier: Tier
    writable: bool


@dataclass(frozen=True)
class Settings:
    data_root: Path
    broker_socket: Path
    bind_host: str
    http_port: int
    auth_password: str | None
    secret_key: bytes | None
    upload_concurrency: int
    upload_timeout_seconds: int
    upload_max_attempts: int
    auto_import: str
    default_timezone: str | None
    log_level: str


def _port(raw: str) -> int:
    if not raw.isdigit() or not (1 <= int(raw) <= 65535):
        raise SettingInvalid(f"ポート番号として解釈できない: {raw}")
    return int(raw)


def _positive_int(raw: str) -> int:
    if not raw.isdigit() or int(raw) < 1:
        raise SettingInvalid(f"1 以上の整数である必要がある: {raw}")
    return int(raw)


def _choice(*allowed: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if raw not in allowed:
            raise SettingInvalid(f"{raw} は {allowed} のいずれかでなければならない")
        return raw

    return parse


def _timezone(raw: str) -> str:
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SettingInvalid(f"IANA タイムゾーンとして解釈できない: {raw}") from exc
    return raw


def _secret_key(raw: str) -> bytes:
    """パスワードではなく 256bit のランダム鍵を base64 で受け取る."""
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SettingInvalid("SECRET_KEY は base64 で与える") from exc
    if len(key) != 32:
        raise SettingInvalid(f"SECRET_KEY は 32 バイトである必要がある（{len(key)} バイト）")
    return key


SETTING_SPECS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        # DB 自身の置き場所を決めるので、DB に保存できない。
        SettingSpec("DATA_ROOT", "/data", Path, Tier.BOOTSTRAP),
        # compose.yaml が既にこのキーを app に渡している。
        SettingSpec("BROKER_SOCKET", "/run/mediaferry/broker.sock", Path, Tier.BOOTSTRAP),
        # マスター鍵を DB へ置けると、暗号文と復号鍵が同じバックアップに入る。
        SettingSpec("SECRET_KEY", None, _secret_key, Tier.BOOTSTRAP, secret=True),
        # Phase 4 で認証を入れるときに Argon2 ハッシュの保存先を別に作る。
        # 平文を app_setting へ置く経路は作らない。
        SettingSpec("AUTH_PASSWORD", None, str, Tier.BOOTSTRAP, secret=True),
        SettingSpec("BIND_HOST", "127.0.0.1", str, Tier.RESTART),
        SettingSpec("HTTP_PORT", "8080", _port, Tier.RESTART),
        SettingSpec("UPLOAD_CONCURRENCY", "2", _positive_int, Tier.RUNTIME),
        SettingSpec("UPLOAD_TIMEOUT_SECONDS", "86400", _positive_int, Tier.RUNTIME),
        SettingSpec("UPLOAD_MAX_ATTEMPTS", "3", _positive_int, Tier.RUNTIME),
        SettingSpec("AUTO_IMPORT", "trusted", _choice("trusted", "off"), Tier.RUNTIME),
        # 既定値を置かない。UTC を既定にすると force_offset が補正にならないまま
        # 誤った時刻で確定する（§12.2）。
        SettingSpec("DEFAULT_TIMEZONE", None, _timezone, Tier.RUNTIME),
        SettingSpec(
            "LOG_LEVEL", "info", _choice("debug", "info", "warning", "error"), Tier.RUNTIME
        ),
    )
}


def bootstrap_data_root(env: Mapping[str, str]) -> Path:
    """DB を開く前に要る値. env と既定値だけで決まる."""
    return Path(env.get(ENV_PREFIX + "DATA_ROOT", SETTING_SPECS["DATA_ROOT"].default))


class SettingsService:
    def __init__(self, conn: sqlite3.Connection, env: Mapping[str, str]) -> None:
        self._conn = conn
        self._env = env

    def _raw(self, key: str) -> tuple[str | None, str]:
        spec = SETTING_SPECS[key]
        from_env = self._env.get(ENV_PREFIX + key)
        if from_env is not None:
            return from_env, "env"
        # BOOTSTRAP は DB を見ない。書けないだけでなく、読みもしない
        # （書けてしまった行が後から効くことを防ぐ）。
        if spec.tier is not Tier.BOOTSTRAP:
            row = self._conn.execute(
                "SELECT value FROM app_setting WHERE key = ?", (key,)
            ).fetchone()
            if row is not None:
                return row["value"], "db"
        return spec.default, "default"

    def describe_all(self) -> list[SettingValue]:
        out = []
        for key, spec in SETTING_SPECS.items():
            raw, source = self._raw(key)
            value = MASK if (spec.secret and raw is not None) else raw
            locked = source == "env"
            out.append(
                SettingValue(
                    key=key,
                    value=value,
                    source=source,
                    locked=locked,
                    tier=spec.tier,
                    writable=not locked and spec.tier is not Tier.BOOTSTRAP,
                )
            )
        return out

    def set(self, key: str, value: str) -> Tier:
        """保存して、その値がいつ効くかを返す."""
        spec = SETTING_SPECS.get(key)
        if spec is None:
            raise SettingInvalid(f"未知の設定キー: {key}")
        if spec.tier is Tier.BOOTSTRAP:
            raise SettingLocked(f"{key} は env でのみ設定できる（DB には保存しない）")
        if ENV_PREFIX + key in self._env:
            raise SettingLocked(f"{key} は環境変数で固定されている")
        spec.parse(value)
        self._conn.execute(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, now_iso()),
        )
        return spec.tier

    def snapshot(self) -> Settings:
        parsed: dict[str, Any] = {}
        for key, spec in SETTING_SPECS.items():
            raw, _ = self._raw(key)
            parsed[key] = None if raw is None else spec.parse(raw)
        return Settings(
            data_root=parsed["DATA_ROOT"],
            broker_socket=parsed["BROKER_SOCKET"],
            bind_host=parsed["BIND_HOST"],
            http_port=parsed["HTTP_PORT"],
            auth_password=parsed["AUTH_PASSWORD"],
            secret_key=parsed["SECRET_KEY"],
            upload_concurrency=parsed["UPLOAD_CONCURRENCY"],
            upload_timeout_seconds=parsed["UPLOAD_TIMEOUT_SECONDS"],
            upload_max_attempts=parsed["UPLOAD_MAX_ATTEMPTS"],
            auto_import=parsed["AUTO_IMPORT"],
            default_timezone=parsed["DEFAULT_TIMEZONE"],
            log_level=parsed["LOG_LEVEL"],
        )


def startup_warnings(settings: Settings) -> list[str]:
    """危険な組み合わせを起動ログと UI バナーに出す.

    認証は必須にしない（LAN 内で無設定で使えることを優先する）が、意図せず
    公開している状態には気づけるようにする。
    """
    warnings: list[str] = []
    if settings.auth_password is None and not _is_loopback(settings.bind_host):
        warnings.append(
            f"認証が無効なまま {settings.bind_host} で待ち受けている。"
            "LAN の他の端末から操作できる状態になっている。"
        )
    return warnings


def _is_loopback(host: str) -> bool:
    if host in {"localhost"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_settings.py -v`
Expected: すべて PASS

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/settings.py app/tests/test_settings.py
git commit -m "feat(mediaferry): resolve settings from env, db and defaults"
```

---

### Task 7: API キーの暗号化フォーマット

**Files:**
- Create: `app/src/mediaferry/core/__init__.py`
- Create: `app/src/mediaferry/core/crypto.py`
- Modify: `app/pyproject.toml`（`cryptography` を追加）
- Test: `app/tests/test_crypto.py`

**Interfaces:**
- Consumes: `mediaferry.settings.Settings.secret_key`（Task 6）
- Produces:
  - `mediaferry.core.crypto.SecretAad(credential_id, destination_id, revision, schema_version)`
  - `mediaferry.core.crypto.SecretBox(master_key: bytes)` — `.key_id`, `.encrypt(str, SecretAad) -> bytes`, `.decrypt(bytes, SecretAad) -> str`
  - 例外 `WrongKeyError` / `SecretCorrupt`

**方式の確定**: `cryptography` の `AESGCM`（AES-256-GCM）を使う。仕様は
XChaCha20-Poly1305 を第一候補としているが、`cryptography` は XChaCha20 を
公開していない。§12.3 の「無ければ AES-256-GCM」に従う。アルゴリズム番号を
ヘッダに持たせるので、後から追加しても既存の暗号文を読める。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_crypto.py`:

```python
import os

import pytest

from mediaferry.core.crypto import SecretAad, SecretBox, SecretCorrupt, WrongKeyError


def an_aad(**over):
    fields = {
        "credential_id": "cred-1",
        "destination_id": "dest-1",
        "revision": 1,
        "schema_version": 1,
    }
    fields.update(over)
    return SecretAad(**fields)


@pytest.fixture
def box():
    return SecretBox(os.urandom(32))


def test_round_trip(box):
    blob = box.encrypt("immich-api-key", an_aad())
    assert box.decrypt(blob, an_aad()) == "immich-api-key"


def test_the_plaintext_does_not_appear_in_the_blob(box):
    assert b"immich-api-key" not in box.encrypt("immich-api-key", an_aad())


def test_nonce_is_fresh_for_every_encryption(box):
    first = box.encrypt("k", an_aad())
    second = box.encrypt("k", an_aad())
    assert first != second


def test_moving_a_row_to_another_destination_is_detected(box):
    """AAD に destination_id を含めるので、行の差し替えが復号で落ちる."""
    blob = box.encrypt("k", an_aad())
    with pytest.raises(SecretCorrupt):
        box.decrypt(blob, an_aad(destination_id="dest-2"))
    with pytest.raises(SecretCorrupt):
        box.decrypt(blob, an_aad(revision=2))


def test_a_different_key_is_reported_as_wrong_key_not_corruption(box):
    """誤鍵を「壊れた credential」として上書きしてしまわないため、
    復号を試みる前に key_id で弾く."""
    other = SecretBox(os.urandom(32))
    blob = box.encrypt("k", an_aad())
    with pytest.raises(WrongKeyError) as exc:
        other.decrypt(blob, an_aad())
    assert exc.value.expected == other.key_id
    assert exc.value.found == box.key_id


def test_key_id_is_stable_and_does_not_leak_the_key():
    key = os.urandom(32)
    assert SecretBox(key).key_id == SecretBox(key).key_id
    assert SecretBox(key).key_id.encode() not in key


def test_a_truncated_blob_is_corruption_not_a_crash(box):
    blob = box.encrypt("k", an_aad())
    with pytest.raises(SecretCorrupt):
        box.decrypt(blob[:-1], an_aad())
    with pytest.raises(SecretCorrupt):
        box.decrypt(b"nonsense", an_aad())


def test_the_key_must_be_32_bytes():
    with pytest.raises(ValueError, match="32"):
        SecretBox(os.urandom(16))
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_crypto.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.core'`）

- [ ] **Step 3: 実装する**

`app/pyproject.toml` の `dependencies` に `"cryptography>=43"` を追加してから
`uv sync --all-packages` する。

`app/src/mediaferry/core/__init__.py` は空ファイル。

`app/src/mediaferry/core/crypto.py`:

```python
"""転送先 API キーの保存形式.

Immich API は可逆な値を要求するのでハッシュ化できない。マスター鍵による
AEAD 暗号化で保存する。マスター鍵は環境変数にあり DATA_ROOT の外なので、
DB やバックアップ単体の流出には効く。app の RCE には効かない（§12.3）。

形式を仕様で固定してあるのは、後から変えると全 credential の migration が
要るため。ヘッダは自己記述で、アルゴリズムと鍵の指紋を含む。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"mfk"
FORMAT_VERSION = 1
ALG_AES_256_GCM = 1
NONCE_BYTES = 12
KEY_BYTES = 32
KEY_ID_CHARS = 16


class WrongKeyError(RuntimeError):
    """暗号文が別のマスター鍵で作られている.

    復号の失敗と区別する。区別しないと、鍵を取り違えた状態で
    「壊れた credential」として上書きしてしまう。
    """

    def __init__(self, expected: str, found: str) -> None:
        super().__init__(f"key_id が一致しない（期待 {expected} / 実際 {found}）")
        self.expected = expected
        self.found = found


class SecretCorrupt(RuntimeError):
    """形式が壊れているか、AAD が一致しない."""


@dataclass(frozen=True)
class SecretAad:
    """暗号文に束縛する文脈.

    行を別の宛先・別の版へ差し替える攻撃を復号時に検出する。
    """

    credential_id: str
    destination_id: str
    revision: int
    schema_version: int

    def to_bytes(self) -> bytes:
        payload = {"v": FORMAT_VERSION, **asdict(self)}
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SecretBox:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != KEY_BYTES:
            raise ValueError(f"マスター鍵は {KEY_BYTES} バイトである必要がある")
        self._aead = AESGCM(master_key)
        self.key_id = hashlib.sha256(b"mediaferry-key-id" + master_key).hexdigest()[:KEY_ID_CHARS]

    def encrypt(self, plaintext: str, aad: SecretAad) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        body = self._aead.encrypt(nonce, plaintext.encode("utf-8"), aad.to_bytes())
        return self._header() + nonce + body

    def decrypt(self, blob: bytes, aad: SecretAad) -> str:
        header = self._header()
        if not blob.startswith(MAGIC) or len(blob) < len(header) + NONCE_BYTES + 16:
            raise SecretCorrupt("暗号文のヘッダが読めない")
        found_key_id = blob[len(MAGIC) + 2 : len(header)].decode("ascii", errors="replace")
        if found_key_id != self.key_id:
            raise WrongKeyError(expected=self.key_id, found=found_key_id)
        if blob[len(MAGIC)] != FORMAT_VERSION or blob[len(MAGIC) + 1] != ALG_AES_256_GCM:
            raise SecretCorrupt("未知の形式またはアルゴリズム")
        nonce = blob[len(header) : len(header) + NONCE_BYTES]
        body = blob[len(header) + NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, body, aad.to_bytes()).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise SecretCorrupt("復号できない（AAD 不一致または改竄）") from exc

    def _header(self) -> bytes:
        return MAGIC + bytes([FORMAT_VERSION, ALG_AES_256_GCM]) + self.key_id.encode("ascii")
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_crypto.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`encrypt` / `decrypt` の `aad.to_bytes()` を `b""` に置き換え、
`test_moving_a_row_to_another_destination_is_detected` が落ちることを
確認してから戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core app/tests/test_crypto.py app/pyproject.toml uv.lock
git commit -m "feat(mediaferry): fix the on-disk format for encrypted api keys"
```

---

### Task 8: quick_fingerprint

**Files:**
- Create: `app/src/mediaferry/core/fingerprint.py`
- Test: `app/tests/test_fingerprint.py`

**Interfaces:**
- Consumes: なし（純粋関数）
- Produces:
  - `mediaferry.core.fingerprint.FINGERPRINT_VERSION: int`
  - `mediaferry.core.fingerprint.window_offsets(size: int) -> list[int]`
  - `mediaferry.core.fingerprint.quick_fingerprint(fileobj: BinaryIO, size: int) -> str`（hex）

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_fingerprint.py`:

```python
import hashlib
import io

from mediaferry.core.fingerprint import (
    FINGERPRINT_VERSION,
    WINDOW_BYTES,
    WINDOW_COUNT,
    quick_fingerprint,
    window_offsets,
)


def a_file(size, seed=b"\x01"):
    return io.BytesIO((seed * size)[:size])


def test_small_files_are_read_whole():
    assert window_offsets(1000) == [0]


def test_offsets_are_deterministic_and_ordered():
    first = window_offsets(10_000_000)
    assert first == window_offsets(10_000_000)
    assert first == sorted(first)
    assert len(first) == WINDOW_COUNT
    assert first[0] == 0
    assert first[-1] == 10_000_000 - WINDOW_BYTES


def test_windows_never_overlap_or_repeat():
    """窓が重なると、同じバイトを二重に読んで読み取り量だけが増える.

    現在の定数では閾値の直上でも間隔が窓幅を下回らないので、重複は起きない。
    window_offsets の set() はこの前提が崩れたときの保険。
    """
    for size in (
        WINDOW_BYTES * WINDOW_COUNT + 1,
        WINDOW_BYTES * WINDOW_COUNT + 10,
        3_000_000,
        16 * 1024**3,
    ):
        offsets = window_offsets(size)
        assert offsets == sorted(set(offsets))
        assert all(b - a >= WINDOW_BYTES for a, b in zip(offsets, offsets[1:], strict=False))
        assert offsets[-1] + WINDOW_BYTES <= size


def test_files_up_to_one_mib_are_read_whole():
    """窓の合計より小さいファイルを分割して読む意味は無い.

    閾値を下げると、64KiB を超えるだけのファイルが全体ではなく先頭だけの
    指紋になり、末尾を差し替えても同じ指紋になる。
    """
    assert window_offsets(WINDOW_BYTES + 1) == [0]
    assert window_offsets(WINDOW_BYTES * WINDOW_COUNT) == [0]

    size = 500_000
    data = bytearray(b"\x01" * size)
    changed = bytearray(data)
    changed[-1] = 0xFF
    assert quick_fingerprint(io.BytesIO(bytes(data)), size) != quick_fingerprint(
        io.BytesIO(bytes(changed)), size
    )


def test_size_is_part_of_the_digest():
    """サイズを含めないと、連結の曖昧さで別の内容が同じ指紋になりうる."""
    assert quick_fingerprint(a_file(100), 100) != quick_fingerprint(a_file(200), 200)


def test_same_bytes_give_the_same_digest():
    assert quick_fingerprint(a_file(5000), 5000) == quick_fingerprint(a_file(5000), 5000)


def test_a_change_inside_a_sampled_window_is_detected():
    size = 4 * 1024 * 1024
    base = bytearray(size)
    changed = bytearray(size)
    changed[0] = 0xFF
    assert quick_fingerprint(io.BytesIO(bytes(base)), size) != quick_fingerprint(
        io.BytesIO(bytes(changed)), size
    )


def test_a_change_in_a_later_window_is_detected():
    """窓ごとに seek しないと、先頭 1MiB を連続して読むだけになる.

    16GiB のカードでは、それ以降の差し替えを一切検出できなくなる。
    """
    size = 4 * 1024 * 1024
    base = bytearray(size)
    changed = bytearray(size)
    changed[size - 1] = 0xFF  # 最後の窓の中
    assert quick_fingerprint(io.BytesIO(bytes(base)), size) != quick_fingerprint(
        io.BytesIO(bytes(changed)), size
    )


def test_the_digest_has_the_documented_construction():
    """仕様の式 sha1(b"mfq" + u8(version) + u64le(size) + windows) と一致する."""
    data = bytes(range(256)) * 4  # 1024 バイト
    expected = hashlib.sha1(  # noqa: S324
        b"mfq" + bytes([FINGERPRINT_VERSION]) + len(data).to_bytes(8, "little") + data,
        usedforsecurity=False,
    ).hexdigest()
    assert quick_fingerprint(io.BytesIO(data), len(data)) == expected


def test_an_empty_file_has_a_digest():
    assert len(quick_fingerprint(io.BytesIO(b""), 0)) == 40
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_fingerprint.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/core/fingerprint.py`:

```python
"""スキャン時の同一性判定に使う軽量な指紋.

(rel_path, size, mtime) だけだと、SD を再フォーマットして連番が再利用され、
たまたま同じサイズ・同じ mtime の別ファイルが同じパスに来る場合を取りこぼす。
16GiB に毎回フル SHA-1 を掛けるのは実用に耐えないので、決定的な位置の
16 窓（合計 1MiB）だけを読む。

**これは同一性の確率的キャッシュキーであって、完全性検査ではない。**
サンプリング対象外だけが変化・破損したファイルは検出できない。ビットロットの
検出には media_file.sha1 とソースのフルハッシュを突き合わせる deep_verify を使う。
"""

from __future__ import annotations

import hashlib
from typing import BinaryIO

FINGERPRINT_VERSION = 1
WINDOW_BYTES = 64 * 1024
WINDOW_COUNT = 16
DOMAIN = b"mfq"


def window_offsets(size: int) -> list[int]:
    """読む窓の先頭オフセットを決定的に算出する.

    1MiB 以下ならファイル全体を 1 窓として読む。範囲が重なる場合は
    重複を除いて昇順で返す。
    """
    if size <= WINDOW_BYTES * WINDOW_COUNT:
        return [0]
    span = size - WINDOW_BYTES
    offsets = [round(i * span / (WINDOW_COUNT - 1)) for i in range(WINDOW_COUNT)]
    return sorted(set(offsets))


def quick_fingerprint(fileobj: BinaryIO, size: int) -> str:
    """ドメイン分離子と固定幅のサイズを含めて連結の曖昧さを排除する."""
    digest = hashlib.sha1(usedforsecurity=False)  # noqa: S324
    digest.update(DOMAIN)
    digest.update(bytes([FINGERPRINT_VERSION]))
    digest.update(size.to_bytes(8, "little"))
    for offset in window_offsets(size):
        fileobj.seek(offset)
        remaining = WINDOW_BYTES if size > WINDOW_BYTES * WINDOW_COUNT else size
        while remaining > 0:
            chunk = fileobj.read(remaining)
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_fingerprint.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`fileobj.seek(offset)` を削り、`test_a_change_in_a_later_window_is_detected` が
落ちることを確認してから戻す（seek が無いと先頭 1MiB を連続して読むだけになり、
16GiB のカードでは末尾の差し替えを検出できない）。全読みの閾値
`WINDOW_BYTES * WINDOW_COUNT` を `WINDOW_BYTES` に下げ、
`test_files_up_to_one_mib_are_read_whole` が落ちることも確認する。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core/fingerprint.py app/tests/test_fingerprint.py
git commit -m "feat(mediaferry): add the quick fingerprint used by scanning"
```

---

### Task 9: プロファイル定義の型・検証・ビルトイン

**Files:**
- Create: `app/src/mediaferry/core/profiles/__init__.py`
- Create: `app/src/mediaferry/core/profiles/model.py`
- Create: `app/src/mediaferry/core/profiles/builtin/dji-osmo.yaml`
- Modify: `app/pyproject.toml`（`pyyaml` を追加）
- Test: `app/tests/test_profile_model.py`

**Interfaces:**
- Consumes: なし（純粋）
- Produces:
  - `mediaferry.core.profiles.model.PROFILE_SCHEMA_VERSION: int`
  - dataclass `ProfileDefinition`（`slug`, `name`, `hints`, `require`, `scan`,
    `timestamp`, `merge`, `immich`）と内訳の `Hints` / `Require` / `ScanRule` /
    `TimestampRule` / `MergeRule` / `KeepStreams` / `ImmichRule`
  - `parse_definition(data: Mapping[str, Any]) -> ProfileDefinition`
  - `definition_to_json(defn: ProfileDefinition) -> str`
  - `load_builtin_definitions() -> list[ProfileDefinition]`
  - 例外 `ProfileInvalid`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_profile_model.py`:

```python
import json

import pytest

from mediaferry.core.profiles.model import (
    ProfileInvalid,
    definition_to_json,
    load_builtin_definitions,
    parse_definition,
)


def a_definition(**over):
    data = {
        "slug": "dji-osmo",
        "name": "DJI Osmo Pocket",
        "hints": {"usb_ids": ["2ca3:*"], "volume_labels": ["SD_Card"]},
        "require": {
            "roots": ["DCIM", "PANORAMA"],
            "filename_pattern": r"^DJI_\d{14}_\d{4}_D\.(MP4|JPG)$",
            "min_matching_files": 1,
        },
        "scan": {"roots": ["DCIM", "PANORAMA"], "extensions": ["MP4", "JPG"]},
        "timestamp": {
            "source": "filename",
            "pattern": r"^DJI_(?P<ts>\d{14})_",
            "format": "%Y%m%d%H%M%S",
            "fallback": "mtime",
            "timezone_policy": "force_offset",
            "timezone": None,
        },
        "merge": {
            "enabled": True,
            "tolerance_seconds": 5,
            "min_part_size_gib": 15,
            "sequence_pattern": r"_(?P<seq>\d{4})_D$",
            "output_name": "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4",
            "keep_streams": {"video": "primary", "audio": "all", "timecode": True, "data": False},
        },
        "immich": {
            "tags": ["DJI Osmo Pocket 4"],
            "tag_pre_existing": True,
            "fix_datetime_after_upload": True,
        },
    }
    data.update(over)
    return data


def test_a_complete_definition_parses():
    defn = parse_definition(a_definition())
    assert defn.slug == "dji-osmo"
    assert defn.scan.extensions == ("MP4", "JPG")
    assert defn.merge.keep_streams.data is False
    assert defn.timestamp.timezone is None


def test_lrf_is_excluded_because_it_is_not_in_the_extensions():
    assert "LRF" not in parse_definition(a_definition()).scan.extensions


@pytest.mark.parametrize("root", ["..", "/DCIM", "DCIM/../..", "a/b", ""])
def test_roots_must_be_single_safe_components(root):
    """マウントルートの外へ抜ける経路を定義から作らせない."""
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(scan={"roots": [root], "extensions": ["MP4"]}))


def test_output_name_cannot_contain_a_path_separator():
    merged = a_definition()["merge"] | {"output_name": "../evil.MP4"}
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(merge=merged))


def test_broken_regexes_are_rejected_at_parse_time():
    require = a_definition()["require"] | {"filename_pattern": "^DJI_(unclosed"}
    with pytest.raises(ProfileInvalid, match="filename_pattern"):
        parse_definition(a_definition(require=require))


def test_slug_is_restricted_to_path_safe_characters():
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(slug="../etc"))
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(slug="DJI Osmo"))


def test_timezone_policy_is_constrained():
    ts = a_definition()["timestamp"] | {"timezone_policy": "guess"}
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(timestamp=ts))


def test_a_filename_source_needs_a_pattern_and_a_format():
    ts = a_definition()["timestamp"] | {"pattern": None}
    with pytest.raises(ProfileInvalid, match="pattern"):
        parse_definition(a_definition(timestamp=ts))
    # format が無いと、取り出した ts をどう読むかが決まらない
    ts = a_definition()["timestamp"] | {"format": None}
    with pytest.raises(ProfileInvalid, match="format"):
        parse_definition(a_definition(timestamp=ts))


def test_the_pattern_must_capture_ts():
    ts = a_definition()["timestamp"] | {"pattern": r"^DJI_(\d{14})_"}
    with pytest.raises(ProfileInvalid, match="ts"):
        parse_definition(a_definition(timestamp=ts))


def test_unknown_keys_are_rejected():
    """綴りを間違えた設定が黙って無視されると、効いていない設定に気づけない."""
    with pytest.raises(ProfileInvalid, match="tolerance_second"):
        parse_definition(a_definition(merge=a_definition()["merge"] | {"tolerance_second": 5}))


def test_json_round_trip_is_stable():
    defn = parse_definition(a_definition())
    once = definition_to_json(defn)
    assert parse_definition(json.loads(once)) == defn
    assert definition_to_json(parse_definition(json.loads(once))) == once


def test_the_json_keys_are_sorted_at_every_level():
    """リビジョンの差分検出に使うので、順序は内容だけで決まる必要がある.

    dataclass のフィールドを並べ替えただけで JSON が変わると、中身が同じ
    プロファイルが変更扱いになって無意味なリビジョンが増える。
    """
    loaded = json.loads(definition_to_json(parse_definition(a_definition())))

    def assert_sorted(node):
        if isinstance(node, dict):
            assert list(node) == sorted(node)
            for value in node.values():
                assert_sorted(value)

    assert_sorted(loaded)


def test_the_builtin_dji_profile_is_valid_and_has_no_local_timezone():
    """地域固定の値をリポジトリに含めない。TZ は設定で与える（§12.2）."""
    builtins = {d.slug: d for d in load_builtin_definitions()}
    assert "dji-osmo" in builtins
    assert builtins["dji-osmo"].timestamp.timezone is None
    assert builtins["dji-osmo"].timestamp.timezone_policy == "force_offset"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_profile_model.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/pyproject.toml` の `dependencies` に `"pyyaml>=6"` を追加し、`uv sync --all-packages`。

`app/src/mediaferry/core/profiles/__init__.py` は空ファイル。

`app/src/mediaferry/core/profiles/model.py`:

```python
"""デバイスプロファイルの定義と検証.

機種差をコードの分岐ではなく設定の差分として表す。定義は DB のリビジョンに
JSON で保存され、取り込み・結合・アップロードの各レコードが使用したリビジョン
ID を持つ。

パスを含む項目は、マウントルートの外へ抜ける経路を作らせないため、単一の
安全な構成要素だけを許す（§14）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

PROFILE_SCHEMA_VERSION = 1
BUILTIN_DIR = Path(__file__).parent / "builtin"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TIMESTAMP_SOURCES = ("filename", "exif", "mtime")
_TIMEZONE_POLICIES = ("none", "force_offset")
_VIDEO_KEEP = ("primary", "all")
_AUDIO_KEEP = ("none", "primary", "all")


class ProfileInvalid(ValueError):
    """定義が仕様を満たさない."""


@dataclass(frozen=True)
class Hints:
    usb_ids: tuple[str, ...]
    volume_labels: tuple[str, ...]


@dataclass(frozen=True)
class Require:
    roots: tuple[str, ...]
    filename_pattern: str
    min_matching_files: int


@dataclass(frozen=True)
class ScanRule:
    roots: tuple[str, ...]
    extensions: tuple[str, ...]


@dataclass(frozen=True)
class TimestampRule:
    source: str
    pattern: str | None
    format: str | None
    fallback: str
    timezone_policy: str
    timezone: str | None


@dataclass(frozen=True)
class KeepStreams:
    video: str
    audio: str
    timecode: bool
    data: bool


@dataclass(frozen=True)
class MergeRule:
    enabled: bool
    tolerance_seconds: int
    min_part_size_gib: int
    sequence_pattern: str
    output_name: str
    keep_streams: KeepStreams


@dataclass(frozen=True)
class ImmichRule:
    tags: tuple[str, ...]
    tag_pre_existing: bool
    fix_datetime_after_upload: bool


@dataclass(frozen=True)
class ProfileDefinition:
    slug: str
    name: str
    hints: Hints
    require: Require
    scan: ScanRule
    timestamp: TimestampRule
    merge: MergeRule
    immich: ImmichRule


def parse_definition(data: Mapping[str, Any]) -> ProfileDefinition:
    _reject_unknown(
        data,
        {"slug", "name", "hints", "require", "scan", "timestamp", "merge", "immich"},
        "profile",
    )
    slug = _string(data, "slug")
    if not _SLUG_RE.match(slug):
        raise ProfileInvalid(f"slug は英小文字・数字・ハイフンのみ: {slug}")
    return ProfileDefinition(
        slug=slug,
        name=_string(data, "name"),
        hints=_parse_hints(_mapping(data, "hints")),
        require=_parse_require(_mapping(data, "require")),
        scan=_parse_scan(_mapping(data, "scan")),
        timestamp=_parse_timestamp(_mapping(data, "timestamp")),
        merge=_parse_merge(_mapping(data, "merge")),
        immich=_parse_immich(_mapping(data, "immich")),
    )


def definition_to_json(defn: ProfileDefinition) -> str:
    """DB へ入れる正規形. 差分検出に使うのでキー順を固定する."""
    return json.dumps(asdict(defn), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_builtin_definitions() -> list[ProfileDefinition]:
    out = []
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        out.append(parse_definition(yaml.safe_load(path.read_text(encoding="utf-8"))))
    return out


# ----------------------------------------------------------------------
def _reject_unknown(data: Mapping[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise ProfileInvalid(f"{where} に未知のキー: {', '.join(unknown)}")


def _mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ProfileInvalid(f"{key} はオブジェクトである必要がある")
    return value


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ProfileInvalid(f"{key} は空でない文字列である必要がある")
    return value


def _bool(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ProfileInvalid(f"{key} は真偽値である必要がある")
    return value


def _positive_int(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProfileInvalid(f"{key} は 0 以上の整数である必要がある")
    return value


def _strings(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ProfileInvalid(f"{key} は文字列の配列である必要がある")
    for item in value:
        if not isinstance(item, str):
            raise ProfileInvalid(f"{key} の要素は文字列である必要がある")
    return tuple(value)


def _safe_components(names: Sequence[str], key: str) -> tuple[str, ...]:
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
            raise ProfileInvalid(f"{key} は単一の安全なディレクトリ名である必要がある: {name!r}")
    return tuple(names)


def _regex(data: Mapping[str, Any], key: str) -> str:
    pattern = _string(data, key)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ProfileInvalid(f"{key} が正規表現として不正: {exc}") from exc
    return pattern


def _parse_hints(data: Mapping[str, Any]) -> Hints:
    _reject_unknown(data, {"usb_ids", "volume_labels"}, "hints")
    return Hints(usb_ids=_strings(data, "usb_ids"), volume_labels=_strings(data, "volume_labels"))


def _parse_require(data: Mapping[str, Any]) -> Require:
    _reject_unknown(data, {"roots", "filename_pattern", "min_matching_files"}, "require")
    return Require(
        roots=_safe_components(_strings(data, "roots"), "require.roots"),
        filename_pattern=_regex(data, "filename_pattern"),
        min_matching_files=_positive_int(data, "min_matching_files"),
    )


def _parse_scan(data: Mapping[str, Any]) -> ScanRule:
    _reject_unknown(data, {"roots", "extensions"}, "scan")
    extensions = _strings(data, "extensions")
    for ext in extensions:
        if ext != ext.upper() or ext.startswith("."):
            raise ProfileInvalid(f"scan.extensions はドット無しの大文字で書く: {ext!r}")
    return ScanRule(
        roots=_safe_components(_strings(data, "roots"), "scan.roots"), extensions=extensions
    )


def _parse_timestamp(data: Mapping[str, Any]) -> TimestampRule:
    _reject_unknown(
        data,
        {"source", "pattern", "format", "fallback", "timezone_policy", "timezone"},
        "timestamp",
    )
    source = _string(data, "source")
    if source not in _TIMESTAMP_SOURCES:
        raise ProfileInvalid(f"timestamp.source は {_TIMESTAMP_SOURCES} のいずれか")
    fallback = _string(data, "fallback")
    if fallback not in _TIMESTAMP_SOURCES:
        raise ProfileInvalid(f"timestamp.fallback は {_TIMESTAMP_SOURCES} のいずれか")
    policy = _string(data, "timezone_policy")
    if policy not in _TIMEZONE_POLICIES:
        raise ProfileInvalid(f"timestamp.timezone_policy は {_TIMEZONE_POLICIES} のいずれか")
    pattern = data.get("pattern")
    fmt = data.get("format")
    if source == "filename":
        if not isinstance(pattern, str):
            raise ProfileInvalid("source が filename なら timestamp.pattern が要る")
        _regex(data, "pattern")
        if "(?P<ts>" not in pattern:
            raise ProfileInvalid("timestamp.pattern は名前付きグループ ts を持つ必要がある")
        if not isinstance(fmt, str):
            raise ProfileInvalid("source が filename なら timestamp.format が要る")
    timezone = data.get("timezone")
    if timezone is not None and not isinstance(timezone, str):
        raise ProfileInvalid("timestamp.timezone は文字列か null")
    return TimestampRule(
        source=source,
        pattern=pattern if isinstance(pattern, str) else None,
        format=fmt if isinstance(fmt, str) else None,
        fallback=fallback,
        timezone_policy=policy,
        timezone=timezone,
    )


def _parse_keep_streams(data: Mapping[str, Any]) -> KeepStreams:
    _reject_unknown(data, {"video", "audio", "timecode", "data"}, "merge.keep_streams")
    video, audio = _string(data, "video"), _string(data, "audio")
    if video not in _VIDEO_KEEP:
        raise ProfileInvalid(f"keep_streams.video は {_VIDEO_KEEP} のいずれか")
    if audio not in _AUDIO_KEEP:
        raise ProfileInvalid(f"keep_streams.audio は {_AUDIO_KEEP} のいずれか")
    return KeepStreams(
        video=video, audio=audio, timecode=_bool(data, "timecode"), data=_bool(data, "data")
    )


def _parse_merge(data: Mapping[str, Any]) -> MergeRule:
    _reject_unknown(
        data,
        {
            "enabled",
            "tolerance_seconds",
            "min_part_size_gib",
            "sequence_pattern",
            "output_name",
            "keep_streams",
        },
        "merge",
    )
    output_name = _string(data, "output_name")
    _safe_components([output_name], "merge.output_name")
    return MergeRule(
        enabled=_bool(data, "enabled"),
        tolerance_seconds=_positive_int(data, "tolerance_seconds"),
        min_part_size_gib=_positive_int(data, "min_part_size_gib"),
        sequence_pattern=_regex(data, "sequence_pattern"),
        output_name=output_name,
        keep_streams=_parse_keep_streams(_mapping(data, "keep_streams")),
    )


def _parse_immich(data: Mapping[str, Any]) -> ImmichRule:
    _reject_unknown(data, {"tags", "tag_pre_existing", "fix_datetime_after_upload"}, "immich")
    return ImmichRule(
        tags=_strings(data, "tags"),
        tag_pre_existing=_bool(data, "tag_pre_existing"),
        fix_datetime_after_upload=_bool(data, "fix_datetime_after_upload"),
    )
```

`app/src/mediaferry/core/profiles/builtin/dji-osmo.yaml`:

```yaml
# DJI Osmo Pocket 系。SD カードと内蔵ストレージの両方が同じ形をしている。
slug: dji-osmo
name: DJI Osmo Pocket
hints:
  # 候補の順位付けにのみ使う。単独では確定しない。
  usb_ids: ["2ca3:*"]
  volume_labels: ["SD_Card", "Pocket4"]
require:
  roots: ["DCIM", "PANORAMA"]
  filename_pattern: '^DJI_\d{14}_\d{4}_D\.(MP4|JPG)$'
  min_matching_files: 1
scan:
  roots: ["DCIM", "PANORAMA"]
  # .LRF はここに無いので取り込まれない。
  extensions: ["MP4", "JPG"]
timestamp:
  source: filename
  pattern: '^DJI_(?P<ts>\d{14})_'
  format: "%Y%m%d%H%M%S"
  # PANO_0001.JPG のように pattern に当たらないファイルがある。
  fallback: mtime
  # DJI は creation_time を UTC で書きつつオフセットも GPS も書かないので、
  # Immich が撮影地の TZ を判定できない。壁時計にオフセットを付けて書き戻す。
  timezone_policy: force_offset
  # 地域固定の値は持たない。MEDIAFERRY_DEFAULT_TIMEZONE か画面で与える。
  timezone: null
merge:
  enabled: true
  tolerance_seconds: 5
  # ~16GiB での自動分割を「分割」と「連続した別録画」の区別に使う。
  min_part_size_gib: 15
  sequence_pattern: '_(?P<seq>\d{4})_D$'
  output_name: "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4"
  keep_streams:
    video: primary
    audio: all
    timecode: true
    # djmd / dbgi は Immich が使わない。落として転送量を減らす。
    data: false
immich:
  tags: ["DJI Osmo Pocket 4"]
  tag_pre_existing: true
  fix_datetime_after_upload: true
```

YAML をホイールに含めるため `app/pyproject.toml` の `force-include` に追記する:

```toml
# .sql と .yaml はソースディストリビューションから漏れやすい。明示的に含める。
[tool.hatch.build.targets.wheel.force-include]
"src/mediaferry/db/migrations" = "mediaferry/db/migrations"
"src/mediaferry/core/profiles/builtin" = "mediaferry/core/profiles/builtin"
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_profile_model.py -v`
Expected: すべて PASS

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/core/profiles app/tests/test_profile_model.py app/pyproject.toml uv.lock
git commit -m "feat(mediaferry): add device profile definitions and the dji builtin"
```

---

### Task 10: プロファイルの判定（hints と require）

**Files:**
- Create: `app/src/mediaferry/core/profiles/matching.py`
- Test: `app/tests/test_profile_matching.py`

**Interfaces:**
- Consumes: `ProfileDefinition`（Task 9）
- Produces:
  - `mediaferry.core.profiles.matching.VolumeFacts(usb_vendor_id, usb_product_id, fs_label)`
  - `Protocol SourceTree`: `.has_root(name: str) -> bool`,
    `.iter_names(root: str, limit: int) -> Iterable[str]`
  - `MatchOutcome(slug, provisional, reason)`（`slug` が `None` なら対象外）
  - `resolve_profile(definitions, facts, tree, remembered_slug=None) -> MatchOutcome`
  - `hint_score(defn, facts) -> int`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_profile_matching.py`:

```python
from mediaferry.core.profiles.matching import VolumeFacts, hint_score, resolve_profile
from mediaferry.core.profiles.model import parse_definition

from .test_profile_model import a_definition


class DictTree:
    """dirfd の代わり. core は OS を知らない."""

    def __init__(self, contents):
        self._contents = contents

    def has_root(self, name):
        return name in self._contents

    def iter_names(self, root, limit):
        return list(self._contents.get(root, []))[:limit]


def dji():
    return parse_definition(a_definition())


def generic():
    data = a_definition(
        slug="generic-dcim",
        name="Generic DCIM",
        hints={"usb_ids": [], "volume_labels": []},
        require={
            "roots": ["DCIM"],
            "filename_pattern": r".*\.(MP4|JPG|JPEG|MOV)$",
            "min_matching_files": 1,
        },
    )
    return parse_definition(data)


def specific(slug, usb_ids=(), labels=()):
    """generic と同じ中身に一致する専用プロファイル.

    slug は順位規則の検証用に指定する。アルファベット順で偶然正解になると、
    規則が効いているかが分からない。
    """
    data = a_definition(
        slug=slug,
        name=slug,
        hints={"usb_ids": list(usb_ids), "volume_labels": list(labels)},
        require={
            "roots": ["DCIM"],
            "filename_pattern": r".*\.JPG$",
            "min_matching_files": 1,
        },
    )
    return parse_definition(data)


def dji_facts(**over):
    fields = {"usb_vendor_id": "2ca3", "usb_product_id": "0020", "fs_label": "SD_Card"}
    fields.update(over)
    return VolumeFacts(**fields)


def test_hints_rank_candidates_but_do_not_confirm():
    assert hint_score(dji(), dji_facts()) > hint_score(generic(), dji_facts())
    # hints だけ一致しても中身が空なら確定しない
    outcome = resolve_profile([dji(), generic()], dji_facts(), DictTree({}))
    assert outcome.slug is None


def test_content_confirms_the_profile():
    tree = DictTree({"DCIM": ["DJI_20260817120000_0001_D.MP4"]})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree)
    assert outcome.slug == "dji-osmo"
    assert outcome.provisional is False


def test_usb_ids_alone_never_confirm():
    """USB ID だけで確定させる経路を塞ぐ。中身は他機種のもの."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree)
    assert outcome.slug == "generic-dcim"


def test_an_empty_dcim_is_a_provisional_match_with_low_confidence():
    """Osmo の内蔵ストレージは DCIM を持つが空だった（Phase 0 実測）."""
    tree = DictTree({"DCIM": [], "PANORAMA": []})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree)
    assert outcome.slug == "dji-osmo"
    assert outcome.provisional is True
    assert "空" in outcome.reason


def test_an_empty_tree_without_matching_hints_is_out_of_scope():
    tree = DictTree({"DCIM": []})
    outcome = resolve_profile(
        [dji(), generic()], dji_facts(usb_vendor_id="abcd", fs_label="BACKUP"), tree
    )
    assert outcome.slug is None
    assert outcome.provisional is False


def test_falls_back_to_generic_dcim():
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    outcome = resolve_profile([dji(), generic()], VolumeFacts("1234", "5678", "PHONE"), tree)
    assert outcome.slug == "generic-dcim"


def test_a_remembered_profile_is_still_revalidated():
    """記憶を無条件に信用しない。中身が変わっていれば別のプロファイルになる."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree, remembered_slug="dji-osmo")
    assert outcome.slug == "generic-dcim"


def test_a_remembered_profile_wins_ties():
    """hints も中身も同じなら、前回と同じ判定を続ける.

    slug 順で決まってしまうと、プロファイルを 1 つ増やしただけで既存カードの
    判定が入れ替わる。
    """
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    both = [specific("aa-first"), specific("zz-second")]
    facts = VolumeFacts("1234", "5678", "PHONE")
    assert resolve_profile(both, facts, tree, remembered_slug="zz-second").slug == "zz-second"
    assert resolve_profile(both, facts, tree, remembered_slug="aa-first").slug == "aa-first"


def test_hints_break_a_tie_between_two_matching_profiles():
    """hints は確定させないが、どちらも一致するときの順位は決める."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    both = [specific("aa-first"), specific("zz-second", labels=["PHONE"])]
    assert resolve_profile(both, VolumeFacts("1234", "5678", "PHONE"), tree).slug == "zz-second"


def test_the_generic_fallback_loses_to_a_specific_profile():
    """generic は最後の手段。slug 順で先に来ても専用プロファイルを押しのけない."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    both = [generic(), specific("zz-second")]
    assert resolve_profile(both, VolumeFacts("1234", "5678", "PHONE"), tree).slug == "zz-second"


def test_usb_id_globs_match_any_product():
    # ラベルを外し、スコアが USB の glob だけから来ることを確かめる
    assert hint_score(dji(), dji_facts(usb_product_id="9999", fs_label="OTHER")) > 0
    assert hint_score(dji(), dji_facts(usb_vendor_id="ffff", fs_label="OTHER")) == 0
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_profile_matching.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/core/profiles/matching.py`:

```python
"""ボリュームごとのプロファイル判定.

`hints` は候補の順位付けにのみ使い、単独では確定させない。確定は必ず
マウント先の中身が `require` を満たすことで行う。USB ID だけで確定すると、
同じ ID の別機種や、他人のカードを誤って取り込む経路になる。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Protocol

from .model import ProfileDefinition

# require の確認で読むファイル名の上限。数万件のカードで全件読まない。
NAME_SCAN_LIMIT = 2000

GENERIC_SLUG = "generic-dcim"


@dataclass(frozen=True)
class VolumeFacts:
    usb_vendor_id: str
    usb_product_id: str
    fs_label: str


class SourceTree(Protocol):
    """ボリュームの中身への読み取り専用の窓."""

    def has_root(self, name: str) -> bool: ...

    def iter_names(self, root: str, limit: int) -> Iterable[str]: ...


@dataclass(frozen=True)
class MatchOutcome:
    """プロファイル判定の結果.

    **ボリュームの同定確度 (`identity_confidence`) はここに含めない。**
    「中身がプロファイルに一致したか」と「前回と同じカードか」は別の問いで、
    混ぜると、同じ UUID の別カードが DJI のファイルを持っているだけで
    信頼を引き継いでしまう。
    """

    slug: str | None
    provisional: bool
    reason: str


def hint_score(defn: ProfileDefinition, facts: VolumeFacts) -> int:
    """一致した hint の数. 0 なら順位付けに寄与しない."""
    score = 0
    usb = f"{facts.usb_vendor_id}:{facts.usb_product_id}".lower()
    if any(fnmatch(usb, pattern.lower()) for pattern in defn.hints.usb_ids):
        score += 1
    if any(facts.fs_label == label for label in defn.hints.volume_labels):
        score += 1
    return score


def resolve_profile(
    definitions: Sequence[ProfileDefinition],
    facts: VolumeFacts,
    tree: SourceTree,
    remembered_slug: str | None = None,
) -> MatchOutcome:
    """中身の検証を通った最初のプロファイルを採用する."""
    ordered = sorted(
        definitions,
        key=lambda d: (
            -hint_score(d, facts),
            0 if d.slug == remembered_slug else 1,
            1 if d.slug == GENERIC_SLUG else 0,
            d.slug,
        ),
    )
    provisional: MatchOutcome | None = None
    for defn in ordered:
        roots = [root for root in defn.require.roots if tree.has_root(root)]
        if not roots:
            continue
        matches = _count_matching_files(defn, tree, roots)
        if matches >= defn.require.min_matching_files:
            return MatchOutcome(
                slug=defn.slug,
                provisional=False,
                reason=f"{', '.join(roots)} に一致するファイルが {matches} 件",
            )
        if provisional is None and hint_score(defn, facts) > 0:
            # 中身が空でも正当なボリュームがある（まだ撮影していない内蔵ストレージ）。
            # 対象だと分かるように残すが、自動取り込みの対象にはしない。
            provisional = MatchOutcome(
                slug=defn.slug,
                provisional=True,
                reason=f"{', '.join(roots)} はあるが一致するファイルが無い（空）",
            )
    if provisional is not None:
        return provisional
    return MatchOutcome(slug=None, provisional=False, reason="対象外")


def _count_matching_files(defn: ProfileDefinition, tree: SourceTree, roots: Sequence[str]) -> int:
    pattern = re.compile(defn.require.filename_pattern)
    found = 0
    for root in roots:
        for name in tree.iter_names(root, NAME_SCAN_LIMIT):
            if pattern.match(name):
                found += 1
                if found >= defn.require.min_matching_files:
                    return found
    return found
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_profile_matching.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`resolve_profile` の `if matches >= defn.require.min_matching_files:` を
`if roots:` に緩め、`test_usb_ids_alone_never_confirm` が落ちることを確認してから戻す。
並び順の 3 項（hints / remembered / generic を最後に）も 1 つずつ削り、
それぞれ対応するテストが落ちることを確認する。**slug のアルファベット順で
偶然正解になる組み合わせでは順位規則を検証できない**ので、専用プロファイルの
slug は generic より後ろに来るものを使う。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core/profiles/matching.py app/tests/test_profile_matching.py
git commit -m "feat(mediaferry): resolve the profile from hints and volume contents"
```

---

### Task 11: 撮影日時の解決

**Files:**
- Create: `app/src/mediaferry/core/timestamps.py`
- Test: `app/tests/test_timestamps.py`

**Interfaces:**
- Consumes: `ProfileDefinition`（Task 9）
- Produces:
  - `mediaferry.core.timestamps.CapturedAt(at: datetime, source: str, tz: str | None, note: str | None)`
  - `resolve_captured_at(defn, rel_path, mtime_ns, default_timezone) -> CapturedAt`
  - 例外 `TimezoneUnresolved`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_timestamps.py`:

```python
from datetime import UTC, datetime

import pytest

from mediaferry.core.profiles.model import parse_definition
from mediaferry.core.timestamps import TimezoneUnresolved, resolve_captured_at

from .test_profile_model import a_definition


def defn(**timestamp_over):
    ts = a_definition()["timestamp"] | timestamp_over
    return parse_definition(a_definition(timestamp=ts))


def mtime_ns_of(wall_utc: str) -> int:
    dt = datetime.fromisoformat(wall_utc).replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def test_filename_wall_clock_gets_the_configured_offset():
    """DJI は creation_time を UTC で書きつつオフセットを書かないので、
    ファイル名の壁時計に profile の TZ を付けて撮影時刻とする."""
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"), "DCIM/DJI_20260817143000_0001_D.MP4", 0, None
    )
    assert got.at.isoformat() == "2026-08-17T14:30:00+09:00"
    assert got.source == "filename"
    assert got.tz == "Asia/Tokyo"


def test_the_default_timezone_is_used_when_the_profile_has_none():
    got = resolve_captured_at(defn(), "DCIM/DJI_20260817143000_0001_D.MP4", 0, "Europe/Berlin")
    assert got.at.utcoffset().total_seconds() == 2 * 3600
    assert got.tz == "Europe/Berlin"


def test_the_profile_timezone_wins_over_the_default():
    """既定値は「プロファイルが決めていないとき」の受け皿.

    逆順にすると、機種に固定した TZ が全体設定で黙って上書きされる。
    """
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"), "DCIM/DJI_20260817143000_0001_D.MP4", 0, "Europe/Berlin"
    )
    assert got.tz == "Asia/Tokyo"
    assert got.at.isoformat() == "2026-08-17T14:30:00+09:00"


def test_force_offset_without_any_timezone_is_an_error():
    """UTC を既定にすると補正にならないまま誤った時刻で確定する（§12.2）."""
    with pytest.raises(TimezoneUnresolved):
        resolve_captured_at(defn(), "DCIM/DJI_20260817143000_0001_D.MP4", 0, None)


def test_files_that_miss_the_pattern_fall_back_to_mtime():
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"),
        "PANORAMA/PANO_0001.JPG",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.source == "mtime"
    # exFAT の mtime はローカル時刻なので、UTC 表現の壁時計にオフセットを付ける
    assert got.at.isoformat() == "2026-08-17T05:00:00+09:00"


def test_policy_none_keeps_the_instant_as_recorded():
    """Canon は EXIF にローカル時刻を書くので介入しない."""
    got = resolve_captured_at(
        defn(timezone_policy="none", timezone=None),
        "DCIM/DJI_20260817143000_0001_D.MP4",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.at == datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
    assert got.tz is None


def test_an_ambiguous_wall_clock_takes_the_earlier_one_and_says_so():
    """DST の戻りで 1 時間が 2 回ある."""
    got = resolve_captured_at(
        defn(timezone="Europe/Berlin"), "DCIM/DJI_20261025023000_0001_D.MP4", 0, None
    )
    assert got.at.utcoffset().total_seconds() == 2 * 3600  # 先に来る CEST
    assert "曖昧" in got.note


def test_a_nonexistent_wall_clock_shifts_forward_and_says_so():
    """DST の進みで存在しない 1 時間がある."""
    got = resolve_captured_at(
        defn(timezone="Europe/Berlin"), "DCIM/DJI_20260329023000_0001_D.MP4", 0, None
    )
    assert got.at.isoformat() == "2026-03-29T03:30:00+02:00"
    assert "存在しない" in got.note


def test_an_unparsable_timestamp_in_the_name_falls_back():
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"),
        "DCIM/DJI_99999999999999_0001_D.MP4",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.source == "mtime"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_timestamps.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/core/timestamps.py`:

```python
"""撮影日時の解決.

`force_offset` は、ファイル名または mtime から得た**壁時計**にプロファイルの
オフセットを付与する。DJI が MP4 の creation_time を UTC で書きつつオフセットも
GPS も書かないため、Immich が撮影地の TZ を判定できず、UTC の壁時計をそのまま
localDateTime として採用してしまう問題への対処である。

mtime の壁時計は UTC 表現から取る。**これは「カードの時刻欄に UTC オフセットが
書かれていない」ことを前提にしている。** Linux の exfat ドライバは、
`OffsetFromUtc` の valid bit が立っていればそのオフセットで UTC へ変換し、
立っていないときだけマウントの `time_offset`（既定 0）を使う
（`fs/exfat/misc.c` の `exfat_get_entry_time`）。DJI はファイル名に壁時計を
埋めるので、両者が一致するかを実機で確かめられる。手順は
`phase1-manual-checklist.md` にあり、**一致しない機種が出たらここを
プロファイルの timezone で描画する形へ変える**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from .profiles.model import ProfileDefinition


class TimezoneUnresolved(RuntimeError):
    """force_offset なのに TZ がプロファイルにも既定値にも無い."""


@dataclass(frozen=True)
class CapturedAt:
    at: datetime
    source: str  # filename / exif / mtime
    tz: str | None
    note: str | None


def resolve_captured_at(
    defn: ProfileDefinition,
    rel_path: str,
    mtime_ns: int,
    default_timezone: str | None,
) -> CapturedAt:
    wall, source = _wall_clock(defn, rel_path, mtime_ns)
    if defn.timestamp.timezone_policy == "none":
        return CapturedAt(at=wall.replace(tzinfo=UTC), source=source, tz=None, note=None)

    name = defn.timestamp.timezone or default_timezone
    if name is None:
        raise TimezoneUnresolved(f"プロファイル {defn.slug} は force_offset だが timezone が未設定")
    at, note = _attach_offset(wall, ZoneInfo(name))
    return CapturedAt(at=at, source=source, tz=name, note=note)


def _wall_clock(defn: ProfileDefinition, rel_path: str, mtime_ns: int) -> tuple[datetime, str]:
    rule = defn.timestamp
    if rule.source == "filename" and rule.pattern is not None and rule.format is not None:
        match = re.search(rule.pattern, PurePosixPath(rel_path).name)
        if match is not None:
            try:
                return datetime.strptime(match.group("ts"), rule.format), "filename"  # noqa: DTZ007
            except ValueError:
                pass
    # fallback は mtime のみを想定する（exif は Phase 5 の canon-eos で足す）。
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=UTC).replace(tzinfo=None), "mtime"


def _attach_offset(wall: datetime, zone: ZoneInfo) -> tuple[datetime, str | None]:
    """壁時計にオフセットを付ける. DST の境界は決め打ちで解決し、記録する."""
    earlier = wall.replace(tzinfo=zone, fold=0)
    later = wall.replace(tzinfo=zone, fold=1)
    if earlier.utcoffset() != later.utcoffset():
        # 同じ壁時計が 2 回あるか、1 回も無い。offset の大小で見分ける。
        if earlier.utcoffset() > later.utcoffset():
            return earlier, "壁時計が曖昧（DST の戻り）。先に来る方を採用した"
        shifted = wall + timedelta(hours=1)
        return (
            shifted.replace(tzinfo=zone),
            "壁時計が存在しない（DST の進み）。1 時間後ろへずらした",
        )
    return earlier, None
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_timestamps.py -v`
Expected: すべて PASS

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/core/timestamps.py app/tests/test_timestamps.py
git commit -m "feat(mediaferry): resolve captured_at from filenames and mtimes"
```

---

### Task 12: 保存先の名前と衝突時の決定的系列

**Files:**
- Create: `app/src/mediaferry/core/naming.py`
- Test: `app/tests/test_naming.py`

**Interfaces:**
- Consumes: なし（純粋）
- Produces:
  - `mediaferry.core.naming.library_rel_path(role, profile_slug, source_rel_path) -> str`
  - `mediaferry.core.naming.staging_rel_path(job_id, artifact_id) -> str`
  - `mediaferry.core.naming.work_rel_path(job_id) -> str`
  - `mediaferry.core.naming.candidate_paths(rel_path, stamp, sha1_hex) -> Iterator[str]`
  - `mediaferry.core.naming.safe_source_rel_path(rel_path) -> str`
  - 例外 `UnsafePath`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_naming.py`:

```python
from itertools import islice

import pytest

from mediaferry.core.naming import (
    UnsafePath,
    candidate_paths,
    library_rel_path,
    safe_source_rel_path,
    staging_rel_path,
)


def test_library_mirrors_the_path_on_the_card():
    """ユーザが NAS を直接開いて辿れることを保証する."""
    assert (
        library_rel_path("original", "dji-osmo", "DCIM/DJI_001/A.MP4")
        == "library/dji-osmo/DCIM/DJI_001/A.MP4"
    )


def test_derived_files_live_under_their_own_tree():
    assert library_rel_path("derived", "dji-osmo", "DCIM/A.MP4") == "derived/dji-osmo/DCIM/A.MP4"


@pytest.mark.parametrize(
    "path", ["../etc/passwd", "/etc/passwd", "DCIM/../../x", "", "DCIM//A.MP4", "DCIM/./A.MP4"]
)
def test_unsafe_source_paths_are_refused(path):
    with pytest.raises(UnsafePath):
        safe_source_rel_path(path)


@pytest.mark.parametrize("path", ["/etc/passwd", ""])
def test_absolute_and_empty_paths_say_which_rule_they_broke(path):
    """構成要素の検査でも弾けるが、そのメッセージでは原因が分からない.

    API とログに出るのはこの文言なので、先頭で相対パスかどうかを見て分ける。
    """
    with pytest.raises(UnsafePath, match="相対パス"):
        safe_source_rel_path(path)


def test_the_first_candidate_is_the_plain_path():
    stamp = "20260817143005"
    first = next(candidate_paths("library/x/DCIM/A.MP4", stamp, "abcdef1234"))
    assert first == "library/x/DCIM/A.MP4"


def test_the_series_is_deterministic():
    stamp = "20260817143005"
    got = list(islice(candidate_paths("library/x/DCIM/A.MP4", stamp, "abcdef1234"), 5))
    assert got == [
        "library/x/DCIM/A.MP4",
        "library/x/DCIM/A_20260817143005.MP4",
        "library/x/DCIM/A_20260817143005_abcdef12.MP4",
        "library/x/DCIM/A_20260817143005_abcdef12_2.MP4",
        "library/x/DCIM/A_20260817143005_abcdef12_3.MP4",
    ]


def test_the_series_keeps_the_extension_and_the_directory():
    series = candidate_paths("derived/x/DCIM/B.tar.gz", "20260102030405", "0" * 40)
    second = list(islice(series, 2))[1]
    assert second == "derived/x/DCIM/B.tar_20260102030405.gz"


def test_staging_paths_are_scoped_to_the_job():
    """起動時の掃除がジョブ単位でできるように、job-id でディレクトリを分ける."""
    assert staging_rel_path("job-1", "art-1") == "staging/job-1/art-1"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_naming.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/core/naming.py`:

```python
"""ライブラリ内のパスの決め方.

デバイス上の相対パスを保つ。この鏡写しの構造は意図的な設計価値で、ユーザが
NAS を直接開いて中身を辿れることを保証する。プロファイル slug で分けるのは、
複数機種のファイル名が衝突しうるため（IMG_0001.JPG は多くの機種で使われる）。

衝突時は**既存のファイルを絶対に動かさず**、新しく公開する側の名前を変える。
既存を動かすと media_file.rel_path と、それを参照する merge_member /
upload_record が壊れる。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath

SHA1_PREFIX_CHARS = 8


class UnsafePath(ValueError):
    """`..`・絶対パス・空の構成要素を含むパス."""


def safe_source_rel_path(rel_path: str) -> str:
    """カード上の相対パスを検証して正規形で返す."""
    if not rel_path or rel_path.startswith("/"):
        raise UnsafePath(f"相対パスではない: {rel_path!r}")
    parts = rel_path.split("/")
    for part in parts:
        if not part or part in {".", ".."} or "\0" in part or "\\" in part:
            raise UnsafePath(f"安全でない構成要素: {part!r}")
    return "/".join(parts)


def library_rel_path(role: str, profile_slug: str, source_rel_path: str) -> str:
    top = "library" if role == "original" else "derived"
    return f"{top}/{profile_slug}/{safe_source_rel_path(source_rel_path)}"


def staging_rel_path(job_id: str, artifact_id: str) -> str:
    return f"staging/{job_id}/{artifact_id}"


def work_rel_path(job_id: str) -> str:
    return f"work/{job_id}"


def candidate_paths(rel_path: str, stamp: str, sha1_hex: str) -> Iterator[str]:
    """公開先の候補を決定的な順序で無限に返す.

    途中で落ちて再実行しても同じ名前に落ち着くよう、乱数も現在時刻も使わない。
    `stamp` はソースの mtime 由来の壁時計（`YYYYMMDDHHMMSS`）で、staged の
    時点で永続化されたものをそのまま受け取る。
    """
    path = PurePosixPath(rel_path)
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    short = sha1_hex[:SHA1_PREFIX_CHARS]

    yield rel_path
    yield str(parent / f"{stem}_{stamp}{suffix}")
    yield str(parent / f"{stem}_{stamp}_{short}{suffix}")
    n = 2
    while True:
        yield str(parent / f"{stem}_{stamp}_{short}_{n}{suffix}")
        n += 1
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_naming.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`safe_source_rel_path` の構成要素の検査を削って
`test_unsafe_source_paths_are_refused` が落ちること、先頭の「相対パスか」の
検査を削って `test_absolute_and_empty_paths_say_which_rule_they_broke` が
落ちることを確認してから戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core/naming.py app/tests/test_naming.py
git commit -m "feat(mediaferry): decide library paths and the collision series"
```

---

### Task 13: ProfileRegistry（ビルトインの投入とリビジョン）

**Files:**
- Create: `app/src/mediaferry/db/profiles.py`
- Test: `app/tests/test_profile_registry.py`

**Interfaces:**
- Consumes: `device_profile` / `profile_revision`（Task 3）、
  `load_builtin_definitions` / `definition_to_json` / `parse_definition`（Task 9）
- Produces:
  - `mediaferry.db.profiles.ProfileRef(profile_id, revision_id, revision, definition)`
  - `ProfileRegistry(conn)` — `.sync_builtins() -> list[str]`, `.current(slug) -> ProfileRef`,
    `.active() -> list[ProfileRef]`, `.definition_of(revision_id) -> ProfileDefinition`,
    `.by_id(profile_id) -> ProfileRef`
  - 例外 `UnknownProfile`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_profile_registry.py`:

```python
import pytest

from mediaferry.core.profiles.model import definition_to_json
from mediaferry.db.profiles import ProfileRegistry, UnknownProfile


def test_sync_seeds_the_builtins(db):
    registry = ProfileRegistry(db)
    assert "dji-osmo" in registry.sync_builtins()
    ref = registry.current("dji-osmo")
    assert ref.revision == 1
    assert ref.definition.slug == "dji-osmo"


def test_sync_is_idempotent(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    assert registry.sync_builtins() == []
    assert db.execute("SELECT count(*) FROM profile_revision").fetchone()[0] == 1


def test_a_changed_builtin_creates_a_new_revision_and_keeps_the_old_one(db):
    """過去データの解釈が変わらないよう、旧リビジョンは残す."""
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    old = registry.current("dji-osmo")

    # profile_revision は trigger で不変なので、行ではなく定義側を差し替える
    changed = definition_to_json(old.definition).replace(
        '"tolerance_seconds":5', '"tolerance_seconds":9'
    )
    registry._upsert_revision("dji-osmo", changed)  # noqa: SLF001

    new = registry.current("dji-osmo")
    assert new.revision == 2
    assert new.definition.merge.tolerance_seconds == 9
    assert registry.definition_of(old.revision_id).merge.tolerance_seconds == 5


def test_current_points_at_the_latest_revision(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    row = db.execute("SELECT current_revision_id FROM device_profile").fetchone()
    assert row["current_revision_id"] == registry.current("dji-osmo").revision_id


def test_unknown_slug_raises(db):
    with pytest.raises(UnknownProfile):
        ProfileRegistry(db).current("nope")


def test_archived_profiles_are_not_active(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    assert [ref.definition.slug for ref in registry.active()] == ["dji-osmo"]
    db.execute("UPDATE device_profile SET archived_at = '2026-01-01T00:00:00+00:00'")
    assert registry.active() == []
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_profile_registry.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/db/profiles.py`:

```python
"""プロファイルとリビジョンの解決.

編集は既存定義を書き換えず新しいリビジョンを作る。取り込み・結合・アップロードの
各レコードが使用したリビジョン ID を持つので、後からプロファイルを変えても
過去データの解釈は変わらない。

ビルトインはアプリの更新で内容が変わりうる。起動時に定義を突き合わせ、
変わっていれば新リビジョンを作る。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..clock import now_iso
from ..core.profiles.model import (
    PROFILE_SCHEMA_VERSION,
    ProfileDefinition,
    definition_to_json,
    load_builtin_definitions,
    parse_definition,
)
from ..ids import new_id
from .connection import immediate


class UnknownProfile(LookupError):
    pass


@dataclass(frozen=True)
class ProfileRef:
    profile_id: str
    revision_id: str
    revision: int
    definition: ProfileDefinition


class ProfileRegistry:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def sync_builtins(self) -> list[str]:
        """定義が変わったビルトインの slug を返す."""
        changed = []
        for defn in load_builtin_definitions():
            if self._upsert_revision(defn.slug, definition_to_json(defn), name=defn.name):
                changed.append(defn.slug)
        return changed

    def _upsert_revision(self, slug: str, definition_json: str, name: str | None = None) -> bool:
        """現行と違えば新リビジョンを作る. 作ったら True."""
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
                " FROM device_profile p"
                " LEFT JOIN profile_revision r ON r.id = p.current_revision_id"
                " WHERE p.slug = ?",
                (slug,),
            ).fetchone()
            if row is not None and row["definition_json"] == definition_json:
                return False

            profile_id = row["profile_id"] if row is not None else new_id()
            if row is None:
                self._conn.execute(
                    "INSERT INTO device_profile (id, slug, name, builtin, created_at)"
                    " VALUES (?, ?, ?, 1, ?)",
                    (profile_id, slug, name or slug, now_iso()),
                )
            revision = (row["revision"] or 0) + 1 if row is not None else 1
            revision_id = new_id()
            self._conn.execute(
                "INSERT INTO profile_revision"
                " (id, profile_id, revision, definition_json, schema_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    revision_id,
                    profile_id,
                    revision,
                    definition_json,
                    PROFILE_SCHEMA_VERSION,
                    now_iso(),
                ),
            )
            self._conn.execute(
                "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
                (revision_id, profile_id),
            )
        return True

    def current(self, slug: str) -> ProfileRef:
        row = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            raise UnknownProfile(slug)
        return _to_ref(row)

    def by_id(self, profile_id: str) -> ProfileRef:
        row = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise UnknownProfile(profile_id)
        return _to_ref(row)

    def active(self) -> list[ProfileRef]:
        rows = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.archived_at IS NULL ORDER BY p.slug"
        )
        return [_to_ref(row) for row in rows]

    def definition_of(self, revision_id: str) -> ProfileDefinition:
        row = self._conn.execute(
            "SELECT definition_json FROM profile_revision WHERE id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise UnknownProfile(revision_id)
        return parse_definition(json.loads(row["definition_json"]))


def _to_ref(row: sqlite3.Row) -> ProfileRef:
    return ProfileRef(
        profile_id=row["profile_id"],
        revision_id=row["revision_id"],
        revision=row["revision"],
        definition=parse_definition(json.loads(row["definition_json"])),
    )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_profile_registry.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`_upsert_revision` の「定義が同じなら何もしない」判定を削って
`test_sync_is_idempotent` が、`current_revision_id` の UPDATE を削って
`test_current_points_at_the_latest_revision` が落ちることを確認してから戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/profiles.py app/tests/test_profile_registry.py
git commit -m "feat(mediaferry): resolve profiles through immutable revisions"
```

---

### Task 14: ジョブストアと単一ワーカー

**Files:**
- Create: `app/src/mediaferry/db/jobs.py`
- Create: `app/src/mediaferry/jobs/__init__.py`
- Create: `app/src/mediaferry/jobs/runner.py`
- Modify: `app/tests/conftest.py`（`anyio_backend` フィクスチャを追加）
- Test: `app/tests/test_job_store.py`
- Test: `app/tests/test_job_runner.py`

**Interfaces:**
- Consumes: `job` / `job_event`（Task 2）、`immediate`（Task 1）
- Produces:
  - `mediaferry.db.jobs.LEASE_SECONDS: int`
  - `mediaferry.db.jobs.JobContext` — `.job_id`, `.lease_token`, `.params`,
    `.cancelled() -> bool`, `.emit(level, message, data=None)`, `.heartbeat()`,
    `.assert_lease()`
  - `mediaferry.db.jobs.JobStore(conn, clock=now_iso)` — `.enqueue(type, params) -> str`,
    `.claim_next() -> JobContext | None`, `.finish(job_id, token, status, error=None)`,
    `.request_cancel(job_id) -> bool`, `.sweep_interrupted() -> int`,
    `.reap_expired_leases() -> int`, `.get(job_id) -> sqlite3.Row | None`,
    `.list_jobs(limit) -> list[sqlite3.Row]`, `.events(job_id, after_seq) -> list[sqlite3.Row]`
  - 例外 `LeaseLost`
  - `mediaferry.jobs.runner.JobRunner(database, poll_interval=0.5)` —
    `.register(job_type, handler)`（`handler(ctx, conn)`）、
    `await .run_forever()`、`await .stop()`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/conftest.py` に追記:

```python
@pytest.fixture
def anyio_backend():
    """JobRunner は asyncio ワーカーなので、anyio の trio 側は使わない."""
    return "asyncio"
```

`app/tests/test_job_store.py`:

```python
import sqlite3
import threading

import pytest

from mediaferry.db.jobs import JobStore, LeaseLost


def test_enqueue_then_claim(db):
    store = JobStore(db)
    job_id = store.enqueue("scan", {"volume_instance_id": "v1"})
    ctx = store.claim_next()
    assert ctx is not None
    assert ctx.job_id == job_id
    assert ctx.params == {"volume_instance_id": "v1"}
    assert store.get(job_id)["status"] == "running"


def test_only_one_worker_wins_a_job(db, database):
    """SQLite に行ロックは無い。BEGIN IMMEDIATE の中の条件付き UPDATE で所有権を取る."""
    second = database.connect()
    JobStore(db).enqueue("scan", {})
    claims = [JobStore(db).claim_next(), JobStore(second).claim_next()]
    assert sum(1 for c in claims if c is not None) == 1
    second.close()


def test_concurrent_claimers_do_not_both_win(db, database):
    """逐次に呼ぶと 2 人目の SELECT が既に running を見るだけで、競合にならない.

    本当に確かめたいのは「同時に取りに来ても 1 人しか勝たない」なので、
    スレッドで同時に走らせる。
    """
    JobStore(db).enqueue("scan", {})
    connections = [database.connect() for _ in range(4)]
    start = threading.Barrier(len(connections))
    won: list[object] = []
    lock = threading.Lock()

    def claim(conn):
        start.wait(timeout=5)
        ctx = JobStore(conn).claim_next()
        if ctx is not None:
            with lock:
                won.append(ctx.job_id)

    threads = [threading.Thread(target=claim, args=(conn,)) for conn in connections]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    for conn in connections:
        conn.close()
    assert len(won) == 1


def test_claiming_sets_a_lease_and_heartbeat_extends_it(db):
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    before = store.get(ctx.job_id)["lease_expires_at"]
    ctx.heartbeat()
    assert store.get(ctx.job_id)["lease_expires_at"] >= before


def test_a_stale_token_cannot_touch_the_job(db):
    """キャンセルされた古いジョブが新しいジョブの状態を上書きしないため."""
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    with pytest.raises(LeaseLost):
        store.finish(ctx.job_id, "someone-elses-token", "succeeded")
    store.finish(ctx.job_id, ctx.lease_token, "succeeded")


def test_finishing_clears_the_lease(db):
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    store.finish(ctx.job_id, ctx.lease_token, "succeeded")
    row = store.get(ctx.job_id)
    assert row["status"] == "succeeded"
    assert row["lease_token"] is None and row["lease_expires_at"] is None
    assert row["finished_at"] is not None


def test_cancel_is_cooperative(db):
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert ctx.cancelled() is False
    assert store.request_cancel(ctx.job_id) is True
    assert ctx.cancelled() is True


def test_cancelling_a_queued_job_finishes_it_immediately(db):
    """queued を cancelling にすると、claim_next が拾わないので誰も終わらせられない."""
    store = JobStore(db)
    job_id = store.enqueue("import", {})
    assert store.request_cancel(job_id) is True
    row = store.get(job_id)
    assert row["status"] == "cancelled"
    assert row["finished_at"] is not None
    assert store.claim_next() is None


def test_assert_lease_refuses_a_cancelling_job(db):
    """「キャンセル済み」と表示した後に公開されることを防ぐ境界."""
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    ctx.assert_lease()
    store.request_cancel(ctx.job_id)
    with pytest.raises(LeaseLost):
        ctx.assert_lease()


def test_assert_lease_refuses_an_expired_lease(db):
    store = JobStore(db, lease_seconds=-1)
    store.enqueue("import", {})
    ctx = store.claim_next()
    with pytest.raises(LeaseLost):
        ctx.assert_lease()


def test_heartbeat_cannot_revive_an_expired_lease(db):
    """失効したジョブは reap されて interrupted になる. 復活させない."""
    store = JobStore(db, lease_seconds=-1)
    store.enqueue("import", {})
    ctx = store.claim_next()
    with pytest.raises(LeaseLost):
        ctx.heartbeat()


def test_a_cancel_cannot_slip_between_the_lease_check_and_the_transition(db, database):
    """BEGIN IMMEDIATE の中で確認してから同じ transaction で進める."""
    from mediaferry.db.connection import immediate

    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    other_conn = database.connect()
    other = JobStore(other_conn)
    try:
        with immediate(db):
            ctx.assert_lease()
            # 別接続の cancel は書き込みロックを取れないので待たされる
            with pytest.raises(sqlite3.OperationalError):
                other_conn.execute("PRAGMA busy_timeout = 0")
                other.request_cancel(ctx.job_id)
    finally:
        other_conn.close()


def test_cancelling_a_finished_job_does_nothing(db):
    store = JobStore(db)
    job_id = store.enqueue("import", {})
    ctx = store.claim_next()
    store.finish(job_id, ctx.lease_token, "succeeded")
    assert store.request_cancel(job_id) is False


def test_startup_marks_running_jobs_interrupted(db):
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert store.sweep_interrupted() == 1
    assert store.get(ctx.job_id)["status"] == "interrupted"
    assert store.get(ctx.job_id)["lease_token"] is None


def test_events_are_numbered_per_job_and_readable_from_a_cursor(db):
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    ctx.emit("info", "1 件目")
    ctx.emit("info", "2 件目", {"index": 2})
    assert [e["seq"] for e in store.events(ctx.job_id, after_seq=0)] == [1, 2]
    assert [e["message"] for e in store.events(ctx.job_id, after_seq=1)] == ["2 件目"]


def test_expired_leases_are_reaped(db):
    store = JobStore(db, lease_seconds=-1)  # 即座に失効する
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert store.reap_expired_leases() == 1
    assert store.get(ctx.job_id)["status"] == "interrupted"


def test_a_live_lease_is_not_reaped(db):
    """期限を見ずに reap すると、正常に走っているジョブを毎周期で殺す."""
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert store.reap_expired_leases() == 0
    assert store.get(ctx.job_id)["status"] == "running"
    ctx.assert_lease()  # リースも無傷
```

`app/tests/test_job_runner.py`:

```python
import anyio
import pytest

from mediaferry.db.jobs import JobStore
from mediaferry.jobs.runner import JobRunner


@pytest.mark.anyio
async def test_the_runner_executes_a_handler(db, database):
    store = JobStore(db)
    seen = []

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", lambda ctx, conn: seen.append(ctx.params["v"]))
    job_id = store.enqueue("scan", {"v": 7})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(job_id)["status"] == "queued":
                await anyio.sleep(0.01)
            while store.get(job_id)["status"] == "running":
                await anyio.sleep(0.01)
        await runner.stop()

    assert seen == [7]
    assert store.get(job_id)["status"] == "succeeded"


@pytest.mark.anyio
async def test_a_failing_handler_records_the_error_and_keeps_the_worker_alive(db, database):
    store = JobStore(db)

    def boom(ctx, conn):
        raise RuntimeError("ffprobe が見つからない")

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", boom)
    failing = store.enqueue("scan", {})
    later = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(later)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(failing)["status"] == "failed"
    assert "ffprobe" in store.get(failing)["error"]


@pytest.mark.anyio
async def test_a_cancelled_job_ends_as_cancelled(db, database):
    store = JobStore(db)
    started = anyio.Event()

    def slow(ctx, conn):
        started.set()
        while not ctx.cancelled():
            pass

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("import", slow)
    job_id = store.enqueue("import", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            await started.wait()
        store.request_cancel(job_id)
        with anyio.fail_after(5):
            while store.get(job_id)["status"] in {"running", "cancelling"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(job_id)["status"] == "cancelled"


@pytest.mark.anyio
async def test_each_job_gets_its_own_connection(db, database):
    """ハンドラの接続がワーカーの poller と同じだと、claim と publish の
    トランザクションが混ざる."""
    store = JobStore(db)
    seen = []

    def capture(ctx, conn):
        seen.append(conn)

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", capture)
    first = store.enqueue("scan", {})
    second = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(second)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert store.get(first)["status"] == "succeeded"


@pytest.mark.anyio
async def test_a_job_claimed_while_stopping_is_cancelled(db, database, monkeypatch):
    """claim を待っている間に停止要求が来ると、_current がまだ None なので
    stop() は cancel を打てない. 掴んだ後に確認しないと、停止が長いジョブの
    完走待ちになる.

    停止済みの状態で run_forever を呼んでも loop に入らないので、本当に
    「claim 済み・戻る直前」で止めて競合を作る。
    """
    import threading

    store = JobStore(db)
    job_id = store.enqueue("import", {})
    observed = []

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("import", lambda ctx, conn: observed.append(ctx.cancelled()))

    claimed = threading.Event()
    release = threading.Event()
    original = JobStore.claim_next

    def claim_then_wait(self):
        ctx = original(self)
        if ctx is not None:
            claimed.set()
            release.wait(timeout=5)
        return ctx

    monkeypatch.setattr(JobStore, "claim_next", claim_then_wait)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            assert await anyio.to_thread.run_sync(lambda: claimed.wait(5))
        await runner.stop()  # _current はまだ None
        release.set()
        with anyio.fail_after(5):
            while store.get(job_id)["status"] in {"queued", "running", "cancelling"}:
                await anyio.sleep(0.01)

    assert observed == [True]
    assert store.get(job_id)["status"] == "cancelled"


@pytest.mark.anyio
async def test_stop_waits_for_the_running_handler(db, database):
    """to_thread のハンドラは cancel では止まらない. 待たずに資源を閉じない."""
    store = JobStore(db)
    started = anyio.Event()
    finished = []

    def slow(ctx, conn):
        started.set()
        while not ctx.cancelled():
            pass
        finished.append(ctx.job_id)

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("import", slow)
    job_id = store.enqueue("import", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            await started.wait()
        store.request_cancel(job_id)
        await runner.stop()

    assert finished == [job_id]
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_job_store.py app/tests/test_job_runner.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.db.jobs'`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/db/jobs.py`:

```python
"""ジョブの永続化と所有権.

SQLite に行ロックは無いので、所有権は `BEGIN IMMEDIATE` の中の条件付き
UPDATE（CAS）で取る。更新できた 1 ワーカーだけが実行者になる。

実行中のジョブはリースを持ち、heartbeat で延長する。ファイルを公開する直前に
リースの有効性を確認するので、失効したジョブが後から書き込むことはない。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ..clock import iso, utcnow
from ..ids import new_id
from .connection import immediate

LEASE_SECONDS = 60

ACTIVE = ("running", "cancelling")


class LeaseLost(RuntimeError):
    """自分のトークンではその行を操作できない."""


@dataclass
class JobContext:
    job_id: str
    lease_token: str
    params: dict[str, Any]
    _store: JobStore = field(repr=False)

    def cancelled(self) -> bool:
        row = self._store.get(self.job_id)
        return row is None or row["status"] != "running"

    def heartbeat(self) -> None:
        self._store.extend_lease(self.job_id, self.lease_token)

    def assert_lease(self) -> None:
        """外部への副作用の直前に呼ぶ. 延長はしない."""
        self._store.assert_lease(self.job_id, self.lease_token)

    def emit(self, level: str, message: str, data: dict[str, Any] | None = None) -> None:
        self._store.emit(self.job_id, level, message, data)


class JobStore:
    def __init__(self, conn: sqlite3.Connection, lease_seconds: int = LEASE_SECONDS) -> None:
        self._conn = conn
        self._lease_seconds = lease_seconds

    def enqueue(self, job_type: str, params: dict[str, Any]) -> str:
        """params に秘密を入れない（画面と SSE に出る）."""
        job_id = new_id()
        self._conn.execute(
            "INSERT INTO job (id, type, status, params_json, created_at)"
            " VALUES (?, ?, 'queued', ?, ?)",
            (job_id, job_type, json.dumps(params, ensure_ascii=False), iso(utcnow())),
        )
        return job_id

    def claim_next(self) -> JobContext | None:
        token = new_id()
        now = utcnow()
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT id, params_json FROM job"
                " WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = self._conn.execute(
                "UPDATE job SET status = 'running', lease_token = ?, lease_expires_at = ?,"
                " started_at = COALESCE(started_at, ?)"
                " WHERE id = ? AND status = 'queued'",
                (token, self._expiry(), iso(now), row["id"]),
            )
            if updated.rowcount != 1:
                return None
        return JobContext(
            job_id=row["id"],
            lease_token=token,
            params=json.loads(row["params_json"]),
            _store=self,
        )

    def assert_lease(self, job_id: str, token: str) -> None:
        """自分がまだ実行者かを確かめる. 延長はしない.

        `cancelling` を通さないのが要点。通すと「キャンセル済みと表示した後に
        公開される」経路が開く。期限切れも通さない（延長で復活させない）。

        SELECT だけなので、呼び出し側の `BEGIN IMMEDIATE` の中で使える。
        書き込みロックを取った状態で確認してから同じトランザクションで
        状態を進めれば、確認と遷移の間にキャンセルが割り込めない。
        """
        row = self._conn.execute(
            "SELECT 1 FROM job WHERE id = ? AND lease_token = ? AND status = 'running'"
            " AND lease_expires_at > ?",
            (job_id, token, iso(utcnow())),
        ).fetchone()
        if row is None:
            raise LeaseLost(f"ジョブ {job_id} のリースが無効（キャンセル・失効・別の所有者）")

    def extend_lease(self, job_id: str, token: str) -> None:
        """heartbeat. 期限切れのリースは復活させない."""
        updated = self._conn.execute(
            "UPDATE job SET lease_expires_at = ? WHERE id = ? AND lease_token = ?"
            " AND status IN ('running', 'cancelling') AND lease_expires_at > ?",
            (self._expiry(), job_id, token, iso(utcnow())),
        )
        if updated.rowcount != 1:
            raise LeaseLost(f"ジョブ {job_id} のリースを失っている")

    def finish(self, job_id: str, token: str, status: str, error: str | None = None) -> None:
        updated = self._conn.execute(
            "UPDATE job SET status = ?, error = ?, finished_at = ?,"
            " lease_token = NULL, lease_expires_at = NULL"
            " WHERE id = ? AND lease_token = ? AND status IN ('running', 'cancelling')",
            (status, error, iso(utcnow()), job_id, token),
        )
        if updated.rowcount != 1:
            raise LeaseLost(f"ジョブ {job_id} のリースを失っている")

    def finish_claimed(self, job_id: str, token: str) -> str:
        """正常終了の決着を 1 文で付ける.

        「status を読む → finish する」を分けると、その間に入った cancel が
        succeeded で上書きされる。cancel API は成功を返したのにジョブは
        succeeded、という食い違いになる。
        """
        row = self._conn.execute(
            "UPDATE job SET"
            " status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'succeeded' END,"
            " finished_at = ?, lease_token = NULL, lease_expires_at = NULL"
            " WHERE id = ? AND lease_token = ? AND status IN ('running', 'cancelling')"
            " RETURNING status",
            (iso(utcnow()), job_id, token),
        ).fetchone()
        if row is None:
            raise LeaseLost(f"ジョブ {job_id} のリースを失っている")
        return row["status"]

    def request_cancel(self, job_id: str) -> bool:
        """queued は即 cancelled、running だけ cancelling にする.

        queued を cancelling にすると、claim_next は queued しか取らないので
        誰も終わらせられず、画面に永遠に「キャンセル中」が残る。
        """
        with immediate(self._conn):
            done = self._conn.execute(
                "UPDATE job SET status = 'cancelled', finished_at = ?"
                " WHERE id = ? AND status = 'queued'",
                (iso(utcnow()), job_id),
            )
            if done.rowcount == 1:
                return True
            updated = self._conn.execute(
                "UPDATE job SET status = 'cancelling' WHERE id = ? AND status = 'running'",
                (job_id,),
            )
            return updated.rowcount == 1

    def sweep_interrupted(self) -> int:
        """起動時に、前回落ちたまま running だったジョブを倒す."""
        updated = self._conn.execute(
            "UPDATE job SET status = 'interrupted', finished_at = ?,"
            " lease_token = NULL, lease_expires_at = NULL"
            " WHERE status IN ('running', 'cancelling')",
            (iso(utcnow()),),
        )
        return updated.rowcount

    def reap_expired_leases(self) -> int:
        updated = self._conn.execute(
            "UPDATE job SET status = 'interrupted', finished_at = ?,"
            " lease_token = NULL, lease_expires_at = NULL"
            " WHERE status IN ('running', 'cancelling') AND lease_expires_at < ?",
            (iso(utcnow()), iso(utcnow())),
        )
        return updated.rowcount

    def emit(self, job_id: str, level: str, message: str, data: dict | None = None) -> None:
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM job_event WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._conn.execute(
                "INSERT INTO job_event (job_id, seq, level, message, data_json, at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    row["seq"] + 1,
                    level,
                    message,
                    None if data is None else json.dumps(data, ensure_ascii=False),
                    iso(utcnow()),
                ),
            )

    def get(self, job_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()

    def list_jobs(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(
            self._conn.execute("SELECT * FROM job ORDER BY created_at DESC LIMIT ?", (limit,))
        )

    def events(self, job_id: str, after_seq: int = 0) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM job_event WHERE job_id = ? AND seq > ? ORDER BY seq",
                (job_id, after_seq),
            )
        )

    def _expiry(self) -> str:
        return iso(utcnow() + timedelta(seconds=self._lease_seconds))
```

`app/src/mediaferry/jobs/__init__.py` は空ファイル。

`app/src/mediaferry/jobs/runner.py`:

```python
"""単一の asyncio ワーカー.

SQLite の書き込みを 1 本に絞るため、同時に走るジョブは 1 つだけにする。
実処理は同期関数として書き、`asyncio.to_thread` へ逃がしてイベントループを
塞がないようにする。キャンセルは協調的で、ハンドラが `ctx.cancelled()` を
見て自分で降りる。

**ジョブごとに DB 接続を開く。** ハンドラにはその接続を渡し、`JobContext` も
同じ接続に束ね直す。§9.3 の手順 7 は「リースの確認」と「staged への遷移」を
1 つの `BEGIN IMMEDIATE` に入れるので、両者が別接続だと成立しない。

`stop()` は「今のジョブが終わったら降りる」を意味する。`to_thread` で
走っているハンドラは task の cancel では止まらないので、呼び出し側は
`run_forever()` の完了を待つ。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import replace

from ..db.connection import Database
from ..db.jobs import JobContext, JobStore

logger = logging.getLogger(__name__)

Handler = Callable[[JobContext, sqlite3.Connection], None]


class JobRunner:
    def __init__(self, database: Database, poll_interval: float = 0.5) -> None:
        self._database = database
        self._poll_interval = poll_interval
        self._handlers: dict[str, Handler] = {}
        self._stopping = asyncio.Event()
        self._poll_store: JobStore | None = None
        self._current: JobContext | None = None

    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    async def stop(self) -> None:
        """降りるよう伝える. 実際に終わるのは `run_forever()` の完了時.

        走っているジョブにはキャンセルを要求する。要求しないと、ハンドラは
        `ctx.cancelled()` が偽のまま最後まで走り、停止が「待つだけ」になる。
        """
        self._stopping.set()
        current, store = self._current, self._poll_store
        if current is not None and store is not None:
            await asyncio.to_thread(store.request_cancel, current.job_id)

    async def run_forever(self) -> None:
        poller = self._database.connect()
        self._poll_store = poll_store = JobStore(poller)
        try:
            while not self._stopping.is_set():
                ctx = await asyncio.to_thread(poll_store.claim_next)
                if ctx is None:
                    # 停止要求が来るまで待つ。来なければ次の周回で claim を試す。
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                    continue
                if self._stopping.is_set():
                    # claim を待っている間に停止要求が来た。この時点では
                    # `_current` がまだ None なので stop() は cancel を打てない。
                    # 掴んでしまったジョブは、ハンドラが最初のキャンセル確認で
                    # 降りるように cancel を立ててから渡す。
                    await asyncio.to_thread(poll_store.request_cancel, ctx.job_id)
                await self._run_one(ctx, poll_store)
        finally:
            self._poll_store = None
            poller.close()

    async def _run_one(self, ctx: JobContext, poll_store: JobStore) -> None:
        row = poll_store.get(ctx.job_id)
        handler = self._handlers.get(row["type"])
        if handler is None:
            poll_store.finish(
                ctx.job_id, ctx.lease_token, "failed", f"未登録のジョブ種別: {row['type']}"
            )
            return

        conn = await asyncio.to_thread(self._database.connect)
        store = JobStore(conn)
        # ハンドラの中の JobStore と ArtifactPublisher を同じ接続に揃える。
        ctx = replace(ctx, _store=store)
        self._current = ctx
        try:
            await asyncio.to_thread(handler, ctx, conn)
        except Exception as exc:  # noqa: BLE001 - どのジョブが落ちてもワーカーは生かす
            logger.exception("ジョブ %s が失敗した", ctx.job_id)
            store.finish(ctx.job_id, ctx.lease_token, "failed", str(exc))
            return
        finally:
            self._current = None
            if conn.in_transaction:  # pragma: no cover - 取りこぼしの検出用
                logger.error("ジョブ %s がトランザクションを開いたまま終わった", ctx.job_id)
                conn.execute("ROLLBACK")
            conn.close()
        # 状態の読み出しと決着を分けると、その間の cancel が上書きされる。
        poll_store.finish_claimed(ctx.job_id, ctx.lease_token)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_job_store.py app/tests/test_job_runner.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`assert_lease` と `reap_expired_leases` から `lease_expires_at` の比較を外し、
`test_assert_lease_refuses_an_expired_lease` と `test_a_live_lease_is_not_reaped`
が落ちることを確認してから戻す。`finish` からトークンの条件を外し、
`test_a_stale_token_cannot_touch_the_job` が落ちることも確認する。

**`claim_next` の UPDATE 側の `AND status = 'queued'` は変異させても検出
できない。** SELECT が `BEGIN IMMEDIATE` の内側にあるので claimer 同士は完全に
直列化され、2 人目の SELECT はもう queued の行を見ない。この CAS は「将来
`BEGIN IMMEDIATE` を外したときの保険」であって、現状では到達しない経路。
テストで確かめられるのは「同時に取りに来ても 1 人しか勝たない」までで、
それは `test_concurrent_claimers_do_not_both_win` が担う。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/jobs.py app/src/mediaferry/jobs app/tests/test_job_store.py app/tests/test_job_runner.py app/tests/conftest.py
git commit -m "feat(mediaferry): add the job store and the single asyncio worker"
```

---

### Task 15: dirfd 起点の走査アダプタ

**Files:**
- Create: `app/src/mediaferry/adapters/fs.py`
- Test: `app/tests/test_adapter_fs.py`

**Interfaces:**
- Consumes: `SourceTree` プロトコル（Task 10）、`safe_source_rel_path`（Task 12）
- Produces:
  - `mediaferry.adapters.fs.DirfdTree(dirfd)` — `SourceTree` の実装
  - `mediaferry.adapters.fs.FoundFile(rel_path, size_bytes, mtime_ns)`
  - `mediaferry.adapters.fs.iter_media_files(dirfd, roots, extensions) -> Iterator[FoundFile]`
  - `mediaferry.adapters.fs.open_beneath(dirfd, rel_path) -> int`
  - `mediaferry.adapters.fs.fsync_dir(path: Path) -> None`
  - `mediaferry.adapters.fs.assert_same_filesystem(*paths: Path) -> None`
  - 例外 `EscapeAttempt` / `CrossDeviceLayout`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_adapter_fs.py`:

```python
import os
from pathlib import Path

import pytest

from mediaferry.adapters.fs import (
    CrossDeviceLayout,
    DirfdTree,
    EscapeAttempt,
    assert_same_filesystem,
    iter_media_files,
    open_beneath,
)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "DCIM" / "DJI_001").mkdir(parents=True)
    (tmp_path / "DCIM" / "DJI_001" / "A.MP4").write_bytes(b"payload")
    (tmp_path / "DCIM" / "DJI_001" / "A.LRF").write_bytes(b"low res")
    (tmp_path / "DCIM" / "DJI_001" / "._A.MP4").write_bytes(b"apple double")
    (tmp_path / "DCIM" / ".hidden").mkdir()
    (tmp_path / "DCIM" / ".hidden" / "B.MP4").write_bytes(b"x")
    (tmp_path / "PANORAMA").mkdir()
    (tmp_path / "PANORAMA" / "PANO_0001.JPG").write_bytes(b"jpg")
    (tmp_path / "MISC").mkdir()
    (tmp_path / "MISC" / "C.MP4").write_bytes(b"outside the roots")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    yield fd
    os.close(fd)


def test_only_configured_roots_and_extensions_are_listed(tree):
    found = {f.rel_path for f in iter_media_files(tree, ("DCIM", "PANORAMA"), ("MP4", "JPG"))}
    assert found == {"DCIM/DJI_001/A.MP4", "PANORAMA/PANO_0001.JPG"}


def test_dot_directories_and_apple_doubles_are_skipped(tree):
    found = {f.rel_path for f in iter_media_files(tree, ("DCIM",), ("MP4",))}
    assert found == {"DCIM/DJI_001/A.MP4"}


@pytest.mark.parametrize("configured", ["MP4", "mp4", "Mp4"])
def test_extension_matching_is_case_insensitive(tmp_path, configured):
    """カード上の名前と、呼び出し側が渡す拡張子の両方で大小文字を問わない."""
    (tmp_path / "DCIM").mkdir()
    (tmp_path / "DCIM" / "a.mp4").write_bytes(b"x")
    (tmp_path / "DCIM" / "B.MP4").write_bytes(b"x")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        found = {f.rel_path for f in iter_media_files(fd, ("DCIM",), (configured,))}
        assert found == {"DCIM/a.mp4", "DCIM/B.MP4"}
    finally:
        os.close(fd)


def test_found_files_carry_size_and_mtime(tree):
    found = next(iter(iter_media_files(tree, ("PANORAMA",), ("JPG",))))
    assert found.size_bytes == 3
    assert found.mtime_ns > 0


def test_open_beneath_reads_through_the_dirfd(tree):
    fd = open_beneath(tree, "DCIM/DJI_001/A.MP4")
    with os.fdopen(fd, "rb") as f:
        assert f.read() == b"payload"


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "DCIM/../../x"])
def test_open_beneath_refuses_to_escape(tree, path):
    with pytest.raises(EscapeAttempt):
        open_beneath(tree, path)


def test_symlinks_are_not_followed(tmp_path):
    (tmp_path / "DCIM").mkdir()
    (tmp_path / "DCIM" / "link.MP4").symlink_to("/etc/passwd")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert list(iter_media_files(fd, ("DCIM",), ("MP4",))) == []
        with pytest.raises(OSError):
            open_beneath(fd, "DCIM/link.MP4")
    finally:
        os.close(fd)


def test_the_tree_view_answers_the_matching_questions(tree):
    view = DirfdTree(tree)
    assert view.has_root("DCIM") is True
    assert view.has_root("NOPE") is False
    assert "PANO_0001.JPG" in list(view.iter_names("PANORAMA", 100))


def test_the_tree_view_walks_into_subdirectories(tree):
    """DJI は DCIM/DJI_001/ の下にファイルを置く. 直下だけ見ると 0 件になる."""
    assert "A.MP4" in list(DirfdTree(tree).iter_names("DCIM", 100))


def test_same_filesystem_check_passes_for_one_dataset(data_root):
    assert_same_filesystem(data_root / "staging", data_root / "library", data_root / "derived")


def test_same_filesystem_check_reports_a_split_layout(data_root, monkeypatch):
    """別デバイスだと os.link が EXDEV で必ず失敗する. 起動時に気づく."""
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self.name == "staging":
            return os.stat_result(tuple(result)[:2] + (result.st_dev + 1,) + tuple(result)[3:])
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    with pytest.raises(CrossDeviceLayout):
        assert_same_filesystem(data_root / "staging", data_root / "library")
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_adapter_fs.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/adapters/fs.py`:

```python
"""dirfd を起点にした読み取り.

パス解決には常に単一のパス構成要素だけを使い、`..`・絶対パス・シンボリック
リンクを辿らない（`O_NOFOLLOW`）。これで `openat2(RESOLVE_BENEATH)` と同等の
閉じ込めを構成的に実現する。mountd が渡す dirfd は detached mount 由来なので、
その `..` はボリュームルートに固定されている。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# macOS が書く AppleDouble の残骸。本体と同じ拡張子を持つので、名前で弾く。
APPLE_DOUBLE_PREFIX = "._"


class EscapeAttempt(ValueError):
    """マウントルートの外へ出ようとするパス."""


class CrossDeviceLayout(RuntimeError):
    """staging と公開先が別のファイルシステムにある."""


@dataclass(frozen=True)
class FoundFile:
    rel_path: str
    size_bytes: int
    mtime_ns: int


def open_beneath(dirfd: int, rel_path: str) -> int:
    """dirfd の下のファイルを開く. 中間ディレクトリも 1 段ずつ辿る."""
    parts = rel_path.split("/")
    for part in parts:
        if not part or part in {".", ".."} or "\\" in part or "\0" in part:
            raise EscapeAttempt(f"安全でない構成要素: {part!r}")
    if rel_path.startswith("/"):
        raise EscapeAttempt("絶対パスは受け付けない")

    current = dirfd
    opened: list[int] = []
    try:
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            opened.append(current)
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
    finally:
        for fd in opened:
            os.close(fd)


def iter_media_files(
    dirfd: int, roots: Iterable[str], extensions: Iterable[str]
) -> Iterator[FoundFile]:
    """scan.roots の下から scan.extensions に一致するファイルを列挙する."""
    wanted = {ext.upper() for ext in extensions}
    for root in roots:
        yield from _walk(dirfd, root, root, wanted)


def _walk(dirfd: int, root: str, rel_prefix: str, wanted: set[str]) -> Iterator[FoundFile]:
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dirfd)
    except OSError:
        return
    try:
        for entry in sorted(os.scandir(fd), key=lambda e: e.name):
            name = entry.name
            if name.startswith(".") or name.startswith(APPLE_DOUBLE_PREFIX):
                continue
            rel = f"{rel_prefix}/{name}"
            if entry.is_dir(follow_symlinks=False):
                yield from _walk(fd, name, rel, wanted)
            elif entry.is_file(follow_symlinks=False) and _extension(name) in wanted:
                stat = entry.stat(follow_symlinks=False)
                yield FoundFile(rel_path=rel, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
    finally:
        os.close(fd)


def _extension(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return ext.upper()


class DirfdTree:
    """`resolve_profile` に渡す読み取り専用の窓."""

    def __init__(self, dirfd: int) -> None:
        self._dirfd = dirfd

    def has_root(self, name: str) -> bool:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._dirfd)
        except OSError:
            return False
        os.close(fd)
        return True

    def iter_names(self, root: str, limit: int) -> list[str]:
        """root 配下のファイル名を（サブディレクトリも辿って）返す.

        DJI は DCIM/DJI_001/ の下に置くので、直下だけを見ると 0 件になる。
        """
        names: list[str] = []
        self._collect(self._dirfd, root, names, limit)
        return names

    def _collect(self, parent_fd: int, name: str, names: list[str], limit: int) -> None:
        if len(names) >= limit:
            return
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError:
            return
        try:
            for entry in sorted(os.scandir(fd), key=lambda e: e.name):
                if len(names) >= limit:
                    return
                if entry.name.startswith("."):
                    continue
                if entry.is_file(follow_symlinks=False):
                    names.append(entry.name)
                elif entry.is_dir(follow_symlinks=False):
                    self._collect(fd, entry.name, names, limit)
        finally:
            os.close(fd)


def fsync_dir(path: Path) -> None:
    """ディレクトリエントリを永続化する. これを怠ると電源断で公開が失われる."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def assert_same_filesystem(*paths: Path) -> None:
    """staging と公開先が同じファイルシステムにあることを起動時に確かめる.

    公開は `os.link` による原子的操作である必要がある。別デバイスにあると
    `EXDEV` で必ず失敗し、それが分かるのは最初の取り込みの最中になる。
    """
    devices = {path: path.stat().st_dev for path in paths}
    if len(set(devices.values())) > 1:
        detail = ", ".join(f"{path}={dev}" for path, dev in devices.items())
        raise CrossDeviceLayout(
            f"staging と公開先が別のファイルシステムにある（{detail}）。"
            "DATA_ROOT の下に 1 つのデータセットとして置くこと"
        )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_adapter_fs.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`open_beneath` の `O_NOFOLLOW` を外して `test_symlinks_are_not_followed` が、
`_walk` の `follow_symlinks=False` を `True` にして同じテストが、構成要素の
検査を削って `test_open_beneath_refuses_to_escape` が落ちることを確認してから
戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/adapters/fs.py app/tests/test_adapter_fs.py
git commit -m "feat(mediaferry): walk source volumes through the dirfd only"
```

---

### Task 16: ffprobe アダプタ

**Files:**
- Create: `app/src/mediaferry/adapters/ffprobe.py`
- Test: `app/tests/test_adapter_ffprobe.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `mediaferry.adapters.ffprobe.ProbeResult(kind, duration_seconds, probe_state, streams)`
  - `mediaferry.adapters.ffprobe.MediaProbe(ffprobe_path="ffprobe", timeout_seconds=60)` —
    `.describe(path: Path, extension: str) -> ProbeResult`
  - `mediaferry.adapters.ffprobe.PHOTO_EXTENSIONS: frozenset[str]`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_adapter_ffprobe.py`:

```python
import json
import shutil
import subprocess

import pytest

from mediaferry.adapters.ffprobe import MediaProbe


@pytest.fixture
def a_video(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    path = tmp_path / "clip.mp4"
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.parametrize("extension", ["JPG", "jpg", "Jpg"])
def test_photos_are_not_probed(tmp_path, extension):
    path = tmp_path / "a.JPG"
    path.write_bytes(b"not really a jpeg")
    got = MediaProbe(ffprobe_path="/nonexistent").describe(path, extension)
    assert got.kind == "photo"
    assert got.probe_state == "not_applicable"
    assert got.duration_seconds is None


def test_a_video_gets_a_duration(a_video):
    got = MediaProbe().describe(a_video, "MP4")
    assert got.kind == "video"
    assert got.probe_state == "ok"
    assert 1.8 < got.duration_seconds < 2.2
    assert any(s["codec_type"] == "video" for s in got.streams)


def test_a_broken_video_fails_without_raising(tmp_path):
    path = tmp_path / "broken.MP4"
    path.write_bytes(b"\x00" * 128)
    got = MediaProbe().describe(path, "MP4")
    assert got.kind == "video"
    assert got.probe_state == "failed"
    assert got.duration_seconds is None


def test_a_missing_ffprobe_is_reported_as_failed_not_a_crash(tmp_path):
    path = tmp_path / "a.MP4"
    path.write_bytes(b"\x00")
    got = MediaProbe(ffprobe_path="/nonexistent/ffprobe").describe(path, "MP4")
    assert got.probe_state == "failed"


def test_the_command_is_an_argument_array(monkeypatch, tmp_path):
    """シェル文字列を組み立てない（§14）."""
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"format": {"duration": "1.0"}, "streams": []}), ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = tmp_path / "a.MP4"
    path.write_bytes(b"\x00")
    MediaProbe().describe(path, "MP4")
    assert isinstance(seen["args"], list)
    assert str(path) in seen["args"]
    # 終了ステータスを見る。壊れた入力でも JSON らしきものを出しうるので、
    # パースが通ったことを成功の判定に使わない。
    assert seen["kwargs"]["check"] is True
    # 16GiB のファイルで ffprobe が固まったままワーカーを止めない。
    assert seen["kwargs"]["timeout"] > 0


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["ffprobe"]),
        subprocess.TimeoutExpired(["ffprobe"], 60),
    ],
)
def test_subprocess_failures_are_reported_as_failed(monkeypatch, tmp_path, error):
    """非ゼロ終了もタイムアウトも failed にする。ワーカーごと落とさない."""

    def fake_run(args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = tmp_path / "a.MP4"
    path.write_bytes(b"\x00")
    got = MediaProbe().describe(path, "MP4")
    assert got.probe_state == "failed"
    assert got.duration_seconds is None
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_adapter_ffprobe.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/adapters/ffprobe.py`:

```python
"""メディアの種別と duration の確定.

公開前にメタデータを確定させるため（§9.3 手順 5）、ここで得た結果が
そのまま media_file に入る。ffprobe が正当に失敗した場合と、そもそも実行して
いない場合を probe_state で区別する。

duration は §9.7 の結合グループ検出が境界判定に使うので、失敗を
「0 秒」に丸めない。
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PHOTO_EXTENSIONS = frozenset({"JPG", "JPEG", "PNG", "HEIC", "DNG", "CR2", "CR3", "RAW"})


@dataclass(frozen=True)
class ProbeResult:
    kind: str  # photo / video
    duration_seconds: float | None
    probe_state: str  # ok / failed / not_applicable
    streams: list[dict[str, Any]] = field(default_factory=list)


class MediaProbe:
    def __init__(self, ffprobe_path: str = "ffprobe", timeout_seconds: int = 60) -> None:
        self._ffprobe = ffprobe_path
        self._timeout = timeout_seconds

    def describe(self, path: Path, extension: str) -> ProbeResult:
        if extension.upper() in PHOTO_EXTENSIONS:
            return ProbeResult(kind="photo", duration_seconds=None, probe_state="not_applicable")
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    self._ffprobe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            payload = json.loads(completed.stdout)
            duration = float(payload["format"]["duration"])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            logger.warning("ffprobe に失敗した: %s (%s)", path.name, exc)
            return ProbeResult(kind="video", duration_seconds=None, probe_state="failed")
        return ProbeResult(
            kind="video",
            duration_seconds=duration,
            probe_state="ok",
            streams=payload.get("streams", []),
        )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_adapter_ffprobe.py -v`
Expected: すべて PASS（ffmpeg が無い環境では 1 件 skip）

- [ ] **Step 5: 変異試験**

`check=True` を `check=False` に、`timeout` を外して
`test_the_command_is_an_argument_array` が落ちること、`except` から
`subprocess.SubprocessError` を外して
`test_subprocess_failures_are_reported_as_failed` が落ちることを確認してから
戻す。**壊れた動画は JSON のパースで先に落ちるので、それだけでは
`check=True` の有無を検出できない。**

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/adapters/ffprobe.py app/tests/test_adapter_ffprobe.py
git commit -m "feat(mediaferry): finalise media metadata with ffprobe"
```

---

### Task 17: ArtifactPublisher（§9.3 の公開プロトコル）

**Files:**
- Create: `app/src/mediaferry/adapters/publisher.py`
- Test: `app/tests/test_publisher.py`

**Interfaces:**
- Consumes: `artifact_staging` / `media_file`（Task 4）、`JobContext`（Task 14）、
  `MediaProbe`（Task 16）、`candidate_paths` / `staging_rel_path`（Task 12）、
  `CapturedAt`（Task 11）、`fsync_dir`（Task 15）
- Produces:
  - `mediaferry.adapters.publisher.STEP_*`（1〜11 の定数）
  - `ArtifactRequest(kind, role, profile_id, profile_revision_id, desired_rel_path,
    source_rel_path, extension, captured, mtime_ns, source_entry_id, merge_group_id)`
  - `PublishedArtifact(media_file_id, rel_path, size_bytes, sha1, reused_existing)`
  - `HashingWriter(fileobj)` — `.write(bytes) -> int`, `.size`, `.sha1`
  - `ArtifactPublisher(conn, data_root, probe)` —
    `.publish(ctx, request, write) -> PublishedArtifact`,
    `.resume(staging_id) -> PublishedArtifact | None`
  - 例外 `PublishAborted`（staged 前。durable なものは残っていない）/
    `PublishInterrupted`（staged 以降。reconciliation が完遂する）/
    `StagingLost`（回収不能。自動では続行しない）

**この契約は import と merge の両方を想定して固定する。** Phase 2 で derived 専用の
crash model を後付けすると、importer と別実装になって結合物だけが回収不能になる。

**`ArtifactPublisher` と `JobContext` は同じ接続を使う。** 手順 7 は
「リースの確認」と「staged への遷移」を 1 つの `BEGIN IMMEDIATE` に入れる必要が
あり、別々の接続だと同じトランザクションにできない。ワーカーは 1 ジョブにつき
1 本の接続を開き、`JobStore` と `ArtifactPublisher` の両方をそれに束ねる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_publisher.py`:

```python
import hashlib
import json
import sqlite3
from datetime import datetime

import pytest

from mediaferry.adapters.ffprobe import MediaProbe, ProbeResult
from mediaferry.adapters.publisher import (
    ArtifactPublisher,
    ArtifactRequest,
    HashingWriter,
    PublishAborted,
    PublishInterrupted,
    StagingLost,
)
from mediaferry.core.timestamps import CapturedAt
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_source_entry
from .test_schema_sources import a_volume


class StubProbe(MediaProbe):
    def __init__(self, result=None):
        self.result = result or ProbeResult("video", 2.0, "ok")

    def describe(self, path, extension):
        return self.result


@pytest.fixture
def setup(db, data_root):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    publisher = ArtifactPublisher(db, data_root, StubProbe())
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    return publisher, ctx, profile, volume_id


def a_request(profile, entry_id, **over):
    fields = {
        "kind": "import",
        "role": "original",
        "profile_id": profile.profile_id,
        "profile_revision_id": profile.revision_id,
        "desired_rel_path": "library/dji-osmo/DCIM/A.MP4",
        "source_rel_path": "DCIM/A.MP4",
        "extension": "MP4",
        "captured": CapturedAt(
            at=datetime.fromisoformat("2026-08-17T14:30:00+09:00"),
            source="filename",
            tz="Asia/Tokyo",
            note=None,
        ),
        "mtime_ns": 1_700_000_000_000_000_000,
        "source_entry_id": entry_id,
        "merge_group_id": None,
    }
    fields.update(over)
    return ArtifactRequest(**fields)


def write_payload(payload):
    def write(writer):
        writer.write(payload)

    return write


def test_publish_puts_the_file_in_the_library_and_records_it(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    got = publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))

    assert (data_root / "library/dji-osmo/DCIM/A.MP4").read_bytes() == b"payload"
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (got.media_file_id,)).fetchone()
    assert row["rel_path"] == "library/dji-osmo/DCIM/A.MP4"
    assert row["sha1"] == hashlib.sha1(b"payload", usedforsecurity=False).hexdigest()
    assert row["size_bytes"] == 7
    assert row["duration_seconds"] == 2.0
    assert row["probe_state"] == "ok"
    assert row["captured_at"].startswith("2026-08-17T14:30:00")


def test_the_source_entry_is_linked_and_marked_published(setup, db):
    publisher, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    got = publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"x"))
    row = db.execute("SELECT * FROM source_entry WHERE id = ?", (entry_id,)).fetchone()
    assert row["media_file_id"] == got.media_file_id
    assert row["state"] == "published"


def test_the_staging_file_is_gone_and_the_row_is_published(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x"))
    assert list((data_root / "staging").rglob("*")) == [data_root / "staging" / ctx.job_id]
    assert db.execute("SELECT state FROM artifact_staging").fetchone()["state"] == "published"


def test_an_existing_different_file_is_never_overwritten(setup, db, data_root):
    """SD をフォーマットして連番が再利用されたケース. 既存は絶対に動かさない."""
    publisher, ctx, profile, volume_id = setup
    target = data_root / "library/dji-osmo/DCIM/A.MP4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"an older recording")

    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"new payload")
    )

    assert target.read_bytes() == b"an older recording"
    assert got.rel_path != "library/dji-osmo/DCIM/A.MP4"
    assert got.rel_path.startswith("library/dji-osmo/DCIM/A_")
    assert (data_root / got.rel_path).read_bytes() == b"new payload"


def test_the_alternate_name_is_deterministic(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    target = data_root / "library/dji-osmo/DCIM/A.MP4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"older")
    first = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    # 同じ入力を別の source_entry で再公開すると、同じ内容なので同じ行に落ちる
    second = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    assert first.rel_path == second.rel_path
    assert second.reused_existing is True
    assert first.media_file_id == second.media_file_id


def test_publishing_the_same_content_twice_does_not_duplicate(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    first = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"same")
    )
    second = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"same")
    )
    assert first.media_file_id == second.media_file_id
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 1


def test_a_lost_lease_stops_the_publish_before_it_touches_the_library(setup, db, data_root):
    """キャンセル済みと表示した後に公開されることを防ぐ."""
    publisher, ctx, profile, volume_id = setup
    JobStore(db).finish(ctx.job_id, ctx.lease_token, "cancelled")
    with pytest.raises(PublishAborted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x")
        )
    assert not (data_root / "library/dji-osmo/DCIM/A.MP4").exists()


def test_a_cancel_requested_during_the_write_stops_the_publish(setup, db, data_root):
    """cancelling でも extend_lease が通ってしまうと、この境界が破れる."""
    publisher, ctx, profile, volume_id = setup

    def write(writer):
        writer.write(b"payload")
        JobStore(db).request_cancel(ctx.job_id)

    with pytest.raises(PublishAborted):
        publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write)
    assert not (data_root / "library/dji-osmo/DCIM/A.MP4").exists()
    # writing のまま残るので、次回起動の reconciliation が破棄する
    assert db.execute("SELECT state FROM artifact_staging").fetchone()["state"] == "writing"


def test_a_cancel_cannot_land_between_the_lease_check_and_the_staged_transition(
    setup, db, database, data_root
):
    """確認と遷移が同じ BEGIN IMMEDIATE の中にあることを、書き込みロックで確かめる.

    分けると、その隙間に別接続の cancel が commit でき、「キャンセル済みと
    表示した後に公開される」経路が残る。ここでは確認の直後に別接続から
    cancel を試み、書き込みロックに阻まれることを見る。
    """
    publisher, ctx, profile, volume_id = setup
    other_conn = database.connect()
    other_conn.execute("PRAGMA busy_timeout = 0")
    other = JobStore(other_conn)
    outcome = []

    real_assert = ctx.assert_lease

    def assert_then_try_to_cancel():
        real_assert()
        try:
            other.request_cancel(ctx.job_id)
        except sqlite3.OperationalError:
            outcome.append("blocked")
        else:
            outcome.append("slipped in")

    ctx.assert_lease = assert_then_try_to_cancel
    try:
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    finally:
        other_conn.close()

    assert outcome == ["blocked"]


def test_a_failure_after_staging_is_not_reported_as_an_import_failure(setup, db, data_root):
    """staged 以降は reconciliation が完遂する. 呼び出し元が failed に倒すと二重取り込みになる."""
    publisher, ctx, profile, volume_id = setup
    publisher._checkpoint = _die_after(8)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )


def test_resume_after_the_staging_file_is_gone(setup, db, data_root):
    """手順 10 まで進んで落ちた行は staged のまま. os.link を試すと必ず失敗する."""
    publisher, ctx, profile, volume_id = setup
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    staging_id = db.execute("SELECT id FROM artifact_staging").fetchone()["id"]
    got = publisher.resume(staging_id)
    assert got.rel_path == "library/dji-osmo/DCIM/A.MP4"
    assert (data_root / got.rel_path).read_bytes() == b"payload"
    assert db.execute("SELECT state FROM artifact_staging").fetchone()["state"] == "published"


def test_resume_does_not_retry_names_it_already_rejected(setup, db, data_root, monkeypatch):
    """再開は現在の final_rel_path から続ける.

    先頭へ戻しても落ち着く名前は同じだが、棄却済みの名前を試すたびに
    その既存ファイルの SHA-1 を読み直す。16GiB のカードでは実費になる。
    """
    from mediaferry.adapters import publisher as publisher_module

    publisher, ctx, profile, volume_id = setup
    # A.MP4 を別内容で占有させ、1 本目を別名へ追いやる
    publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"XX"))
    publisher._checkpoint = _die_after(8)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"YY")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    row = db.execute("SELECT * FROM artifact_staging WHERE state = 'staged'").fetchone()
    (data_root / row["final_rel_path"]).write_bytes(b"ZZZ")  # 第三者が別内容で占有

    attempts = []
    real_link = publisher_module.os.link
    monkeypatch.setattr(
        publisher_module.os,
        "link",
        lambda src, dst: (attempts.append(str(dst)), real_link(src, dst))[1],
    )
    publisher.resume(row["id"])

    assert not any(a.endswith("/A.MP4") for a in attempts), (
        f"棄却済みの名前を試し直している: {attempts}"
    )


def test_a_staged_row_whose_files_are_all_gone_is_not_silently_dropped(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001
    (data_root / "library/dji-osmo/DCIM/A.MP4").unlink()

    staging_id = db.execute("SELECT id FROM artifact_staging").fetchone()["id"]
    with pytest.raises(StagingLost):
        publisher.resume(staging_id)
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 1


class _Crash(RuntimeError):
    pass


def _die_after(step):
    def checkpoint(current):
        if current == step:
            raise _Crash(f"step {step}")

    return checkpoint


def test_the_staged_row_carries_everything_needed_to_resume(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    seen = {}

    original = publisher._checkpoint  # noqa: SLF001

    def spy(step):
        if step == 7:
            seen["row"] = dict(db.execute("SELECT * FROM artifact_staging").fetchone())
        original(step)

    publisher._checkpoint = spy  # noqa: SLF001
    publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))

    row = seen["row"]
    assert row["state"] == "staged"
    assert row["final_rel_path"] == "library/dji-osmo/DCIM/A.MP4"
    assert row["expected_size"] == 7
    assert row["content_sha1"]
    assert json.loads(row["metadata_json"])["kind"] == "video"


def test_the_published_file_keeps_the_source_mtime(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x")
    )
    assert (data_root / got.rel_path).stat().st_mtime_ns == 1_700_000_000_000_000_000


def test_merge_artifacts_use_the_same_protocol(setup, db, data_root):
    from .test_schema_artifacts import a_merge_group

    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    got = publisher.publish(
        ctx,
        a_request(
            profile,
            None,
            kind="merge",
            role="derived",
            desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
            source_rel_path="DCIM/MERGED.MP4",
            source_entry_id=None,
            merge_group_id=group_id,
        ),
        write_payload(b"merged"),
    )
    assert (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").read_bytes() == b"merged"
    output_id = db.execute(
        "SELECT output_media_file_id FROM merge_group WHERE id = ?", (group_id,)
    ).fetchone()[0]
    assert output_id == got.media_file_id


def test_durability_order_is_utime_then_fsync_then_dir_then_staged(setup, db, monkeypatch):
    """os._exit のテストは page cache を失わないので、この順序は測れない.

    mtime を fsync の後に付けると metadata が永続化されず、staging の親を
    fsync しないと「DB は staged、ファイルは無い」になる。
    """
    from mediaferry.adapters import publisher as publisher_module

    calls = []
    monkeypatch.setattr(publisher_module.os, "utime", lambda *a, **k: calls.append("utime"))
    real_fsync = publisher_module.os.fsync
    monkeypatch.setattr(
        publisher_module.os,
        "fsync",
        lambda fd: (calls.append("fsync"), real_fsync(fd))[1],
    )
    monkeypatch.setattr(
        publisher_module, "fsync_dir", lambda path: calls.append(f"fsync_dir:{path.name}")
    )

    publisher, ctx, profile, volume_id = setup
    original = publisher._checkpoint  # noqa: SLF001
    monkeypatch.setattr(
        publisher,
        "_checkpoint",
        lambda step: (calls.append(f"step{step}"), original(step))[1],
    )
    publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x"))

    upto_staged = calls[: calls.index("step7") + 1]
    assert upto_staged.index("utime") < upto_staged.index("fsync")
    assert upto_staged.index("fsync") < upto_staged.index(f"fsync_dir:{ctx.job_id}")
    assert upto_staged.index(f"fsync_dir:{ctx.job_id}") < upto_staged.index("step7")


def test_the_hashing_writer_matches_hashlib(tmp_path):
    with (tmp_path / "f").open("wb") as f:
        writer = HashingWriter(f)
        writer.write(b"ab")
        writer.write(b"cd")
    assert writer.size == 4
    assert writer.sha1 == hashlib.sha1(b"abcd", usedforsecurity=False).hexdigest()
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_publisher.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/adapters/publisher.py`:

```python
"""アーティファクトの公開プロトコル（§9.3）.

取り込みと結合の**両方**がこの手順を使う。ファイルの公開と DB のコミットの間に
落ちても、齟齬を検出して回収できる状態にする。

公開は `os.link` で行う。既存があれば EEXIST で失敗するので、`os.replace` の
ように既存を黙って上書きしない。`renameat2(RENAME_NOREPLACE)` は Python 標準
ライブラリから呼べないが、`link` は同じ no-clobber 性を持ち、同一ファイル
システム内で原子的である。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from ..clock import now_iso
from ..core.naming import candidate_paths, staging_rel_path
from ..core.timestamps import CapturedAt
from ..db.connection import immediate
from ..db.jobs import JobContext, LeaseLost
from ..ids import new_id
from .ffprobe import MediaProbe
from .fs import fsync_dir

STEP_WRITING_ROW = 1
STEP_WRITTEN = 2
STEP_FSYNCED = 3
STEP_VERIFIED = 4
STEP_METADATA = 5
STEP_FINAL_PATH = 6
STEP_STAGED = 7
STEP_LINKED = 8
STEP_DIR_FSYNCED = 9
STEP_STAGING_UNLINKED = 10
STEP_COMMITTED = 11

COPY_CHUNK = 4 * 1024 * 1024


class PublishAborted(RuntimeError):
    """staged へ進む前に中止した.

    durable なものは何も残っていない（writing の行だけが残り、次回起動で
    破棄される）。呼び出し元は source_entry を差し戻して再実行してよい。
    """


class PublishInterrupted(RuntimeError):
    """staged 以降で失敗した.

    **呼び出し元はこれを「取り込み失敗」として扱ってはならない。**
    ファイルは検証済みで、公開に必要な情報はすべて永続化されているので、
    起動時の reconciliation が公開を完遂する。source_entry を failed に
    戻すと、次のスキャンで新規と判定されて二重に取り込む。
    """


class StagingLost(RuntimeError):
    """staging も final も無い（または内容が一致しない）.

    自動では続行しない。reconciliation は行を残したまま画面に出す。
    """


@dataclass(frozen=True)
class ArtifactRequest:
    kind: str  # import / merge
    role: str  # original / derived
    profile_id: str
    profile_revision_id: str
    desired_rel_path: str
    source_rel_path: str
    extension: str
    captured: CapturedAt
    mtime_ns: int
    source_entry_id: str | None
    merge_group_id: str | None


@dataclass(frozen=True)
class PublishedArtifact:
    media_file_id: str
    rel_path: str
    size_bytes: int
    sha1: str
    reused_existing: bool


class HashingWriter:
    """書き込みストリームで SHA-1 を計算する. 読み直しを 1 回省く."""

    def __init__(self, fileobj: BinaryIO) -> None:
        self._fileobj = fileobj
        self._digest = hashlib.sha1(usedforsecurity=False)
        self.size = 0

    def write(self, data: bytes) -> int:
        self._fileobj.write(data)
        self._digest.update(data)
        self.size += len(data)
        return len(data)

    @property
    def sha1(self) -> str:
        return self._digest.hexdigest()


class ArtifactPublisher:
    def __init__(self, conn: sqlite3.Connection, data_root: Path, probe: MediaProbe) -> None:
        self._conn = conn
        self._data_root = data_root
        self._probe = probe

    def _checkpoint(self, step: int) -> None:
        """crash consistency テストが差し込む継ぎ目. 本番では何もしない."""

    # ------------------------------------------------------------------
    def publish(
        self,
        ctx: JobContext,
        request: ArtifactRequest,
        write: Callable[[HashingWriter], None],
    ) -> PublishedArtifact:
        staging_id = new_id()
        staging_rel = staging_rel_path(ctx.job_id, staging_id)
        staging_abs = self._data_root / staging_rel

        # 1. writing の行を先に commit する。ここから先はどこで落ちても回収できる。
        self._conn.execute(
            "INSERT INTO artifact_staging (id, kind, job_id, lease_token, state,"
            " staging_rel_path, source_entry_id, merge_group_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'writing', ?, ?, ?, ?, ?)",
            (
                staging_id,
                request.kind,
                ctx.job_id,
                ctx.lease_token,
                staging_rel,
                request.source_entry_id,
                request.merge_group_id,
                now_iso(),
                now_iso(),
            ),
        )
        self._checkpoint(STEP_WRITING_ROW)

        # 2. 書き込み。SHA-1 はストリームで取る。
        #    ジョブ用ディレクトリを新しく作ったときは、その名前を持つ親
        #    （staging/）も fsync する。中のファイルだけ永続化しても、
        #    <job-id> のエントリが失われれば丸ごと消える。
        if not staging_abs.parent.exists():
            staging_abs.parent.mkdir(parents=True, exist_ok=True)
            fsync_dir(staging_abs.parent.parent)
        with staging_abs.open("wb") as fileobj:
            writer = HashingWriter(fileobj)
            write(writer)
            fileobj.flush()
            self._checkpoint(STEP_WRITTEN)
            # mtime は fsync より前に付ける。後に付けると metadata の
            # 永続化が保証されない。
            os.utime(fileobj.fileno(), ns=(request.mtime_ns, request.mtime_ns))
            # 3. 中身とディレクトリエントリの両方を永続化する。親を fsync
            #    しないと、電源断で「DB は staged、ファイルは無い」になる。
            os.fsync(fileobj.fileno())
        fsync_dir(staging_abs.parent)
        self._checkpoint(STEP_FSYNCED)

        # 4. サイズとハッシュの検証
        on_disk = staging_abs.stat().st_size
        if on_disk != writer.size:
            raise PublishAborted(f"書き込みサイズが一致しない（{on_disk} != {writer.size}）")
        self._checkpoint(STEP_VERIFIED)

        # 5. メタデータは公開前に確定させる。実体はあるがメタデータが欠けたまま
        #    永久にスキップされる状態を作らない。
        probe = self._probe.describe(staging_abs, request.extension)
        metadata = {
            "role": request.role,
            # 衝突時の別名系列は必ず「最初に望んだパス」から辿る。再開時に
            # 変更後の final_rel_path から辿ると、名前に接尾辞が二重に付く。
            "desired_rel_path": request.desired_rel_path,
            "profile_id": request.profile_id,
            "profile_revision_id": request.profile_revision_id,
            "kind": probe.kind,
            # UTC へ正規化しない。復元した現地の壁時計が読めなくなる。
            "captured_at": request.captured.at.isoformat(),
            "captured_at_source": request.captured.source,
            "captured_at_tz": request.captured.tz,
            "captured_at_note": request.captured.note,
            "duration_seconds": probe.duration_seconds,
            "probe_state": probe.probe_state,
            "mtime_ns": request.mtime_ns,
            # 衝突時の別名に使う壁時計。ここで確定して永続化する。再開のたびに
            # 計算し直すと、算出方法を変えた版で別の名前へ落ちる。
            "collision_stamp": _collision_stamp(request.mtime_ns),
        }
        self._checkpoint(STEP_METADATA)

        # 6. 公開先の決定
        final_rel = request.desired_rel_path
        self._checkpoint(STEP_FINAL_PATH)

        # 7. staged。ここが後戻りできない点で、以後は reconciliation が公開を
        #    完遂する。だからリースの確認は手順 8 の直前ではなくここで行う。
        #
        #    確認と遷移を 1 つの BEGIN IMMEDIATE に入れるのが要点。別々にすると
        #    その隙間にキャンセルが commit でき、「キャンセル済みと表示した後に
        #    公開される」経路が残る。
        try:
            with immediate(self._conn):
                ctx.assert_lease()
                self._conn.execute(
                    "UPDATE artifact_staging SET state = 'staged', final_rel_path = ?,"
                    " expected_size = ?, content_sha1 = ?, metadata_json = ?, updated_at = ?"
                    " WHERE id = ?",
                    (
                        final_rel,
                        writer.size,
                        writer.sha1,
                        json.dumps(metadata, ensure_ascii=False),
                        now_iso(),
                        staging_id,
                    ),
                )
        except LeaseLost as exc:
            raise PublishAborted(str(exc)) from exc
        self._checkpoint(STEP_STAGED)

        try:
            return self._finish(staging_id)
        except PublishInterrupted:
            raise
        except Exception as exc:
            # ここから先の失敗は「取り込み失敗」ではない。回収は起動時に走る。
            raise PublishInterrupted(str(exc)) from exc

    def resume(self, staging_id: str) -> PublishedArtifact | None:
        """reconciliation から呼ぶ. 永続化済みの情報だけを使い、パスを推測しない."""
        row = self._row(staging_id)
        if row is None:
            return None
        if row["state"] == "writing":
            with contextlib.suppress(OSError):
                (self._data_root / row["staging_rel_path"]).unlink()
            self._conn.execute("DELETE FROM artifact_staging WHERE id = ?", (staging_id,))
            return None
        return self._finish(staging_id)

    # ------------------------------------------------------------------
    def _finish(self, staging_id: str) -> PublishedArtifact:
        """手順 8 以降. 何度呼んでも同じ結果になる."""
        row = self._row(staging_id)
        reused = False
        if row["state"] == "staged":
            reused = self._link(staging_id)
            row = self._row(staging_id)
        return self._commit(row, reused)

    def _link(self, staging_id: str) -> bool:
        """8〜10. no-clobber で公開し、staging を消す. 既存と同内容なら True."""
        row = self._row(staging_id)
        staging_abs = self._data_root / row["staging_rel_path"]
        metadata = json.loads(row["metadata_json"])

        if not staging_abs.exists():
            # 手順 10（staging の unlink）まで進んだ後に落ちた場合。state は
            # staged のままなので、ここを通らないと再開のたびに os.link が
            # FileNotFoundError になり、永久に commit できない。
            return self._adopt_published_final(row)

        names = candidate_paths(
            metadata["desired_rel_path"],
            metadata["collision_stamp"],
            row["content_sha1"],
        )
        reused = False
        final_rel = row["final_rel_path"]
        # 系列を現在の final_rel_path まで進める。先頭へ戻しても落ち着く名前は
        # 同じだが、棄却済みの名前を試すたびにその既存ファイルの SHA-1 を
        # 読み直すので、大きなファイルほど無駄が効く。
        for candidate in names:
            if candidate == final_rel:
                break
        while True:
            final_abs = self._data_root / final_rel
            final_abs.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(staging_abs, final_abs)
                break
            except FileExistsError:
                if _is_same_content(final_abs, row["expected_size"], row["content_sha1"]):
                    reused = True
                    break
                final_rel = next(names)
                self._conn.execute(
                    "UPDATE artifact_staging SET final_rel_path = ?, updated_at = ? WHERE id = ?",
                    (final_rel, now_iso(), staging_id),
                )
        self._checkpoint(STEP_LINKED)

        # 9. 公開先の親を fsync する。怠ると電源断で公開が失われる。
        fsync_dir((self._data_root / final_rel).parent)
        self._checkpoint(STEP_DIR_FSYNCED)

        # 10. staging を消し、その親も fsync する。
        with contextlib.suppress(FileNotFoundError):
            staging_abs.unlink()
        if staging_abs.parent.exists():
            fsync_dir(staging_abs.parent)
        self._checkpoint(STEP_STAGING_UNLINKED)
        return reused

    def _adopt_published_final(self, row: sqlite3.Row) -> bool:
        """staging が無い staged 行を、final の実体だけで判定して引き取る.

        永続化した expected_size と content_sha1 だけを使う。パスも内容も
        推測しない。一致しなければ自動では続行せず、画面に出して判断を仰ぐ。
        """
        final_abs = self._data_root / row["final_rel_path"]
        if _is_same_content(final_abs, row["expected_size"], row["content_sha1"]):
            fsync_dir(final_abs.parent)
            self._checkpoint(STEP_DIR_FSYNCED)
            self._checkpoint(STEP_STAGING_UNLINKED)
            return True
        raise StagingLost(
            f"staging {row['id']} の一時ファイルが無く、{row['final_rel_path']} も"
            "記録した大きさ・ハッシュと一致しない"
        )

    def _commit(self, row: sqlite3.Row, reused: bool) -> PublishedArtifact:
        """11. media_file を作り、呼び出し元のレコードを更新する."""
        metadata = json.loads(row["metadata_json"])
        final_rel = row["final_rel_path"]
        with immediate(self._conn):
            existing = self._conn.execute(
                "SELECT id FROM media_file WHERE rel_path = ?", (final_rel,)
            ).fetchone()
            if existing is not None:
                media_file_id = existing["id"]
            else:
                media_file_id = new_id()
                self._conn.execute(
                    "INSERT INTO media_file (id, role, profile_id, profile_revision_id, rel_path,"
                    " size_bytes, mtime_ns, sha1, kind, captured_at, captured_at_source,"
                    " captured_at_tz, captured_at_note, duration_seconds, probe_state, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        media_file_id,
                        metadata["role"],
                        metadata["profile_id"],
                        metadata["profile_revision_id"],
                        final_rel,
                        row["expected_size"],
                        metadata["mtime_ns"],
                        row["content_sha1"],
                        metadata["kind"],
                        metadata["captured_at"],
                        metadata["captured_at_source"],
                        metadata["captured_at_tz"],
                        metadata["captured_at_note"],
                        metadata["duration_seconds"],
                        metadata["probe_state"],
                        now_iso(),
                    ),
                )
            if row["source_entry_id"] is not None:
                self._conn.execute(
                    "UPDATE source_entry SET media_file_id = ?, state = 'published' WHERE id = ?",
                    (media_file_id, row["source_entry_id"]),
                )
            if row["merge_group_id"] is not None:
                self._conn.execute(
                    "UPDATE merge_group SET output_media_file_id = ?, updated_at = ? WHERE id = ?",
                    (media_file_id, now_iso(), row["merge_group_id"]),
                )
            self._conn.execute(
                "UPDATE artifact_staging SET state = 'published', updated_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
        self._checkpoint(STEP_COMMITTED)
        return PublishedArtifact(
            media_file_id=media_file_id,
            rel_path=final_rel,
            size_bytes=row["expected_size"],
            sha1=row["content_sha1"],
            reused_existing=reused,
        )

    def _row(self, staging_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM artifact_staging WHERE id = ?", (staging_id,)
        ).fetchone()


def _collision_stamp(mtime_ns: int) -> str:
    """衝突時の別名に使う、カード上の壁時計.

    `timestamps.py` と同じ前提に立つ（カードの時刻欄に UTC オフセットが
    書かれていない）。その前提の下では、プロファイルの `timezone` を付けても
    表示される桁は変わらない（オフセットの付与は瞬間を移動しない）。
    """
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=UTC).strftime("%Y%m%d%H%M%S")


def _is_same_content(path: Path, expected_size: int, expected_sha1: str) -> bool:
    """大きさとハッシュの両方で判定する. ハッシュだけだと stat の齟齬を見逃す."""
    if not path.exists() or path.stat().st_size != expected_size:
        return False
    return _sha1_of(path) == expected_sha1


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as f:
        while chunk := f.read(COPY_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_publisher.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`os.link` を `os.replace` に変え、
`test_an_existing_different_file_is_never_overwritten` が落ちることを確認してから戻す。
手順 7 の `ctx.assert_lease()` を `immediate()` の外へ出し、
`test_a_cancel_cannot_land_between_the_lease_check_and_the_staged_transition` が
落ちることも確認する（**リースの確認そのものを消すだけでは、確認と遷移が
同じトランザクションにあるかを検証できない**）。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/adapters/publisher.py app/tests/test_publisher.py
git commit -m "feat(mediaferry): add the shared artifact publish protocol"
```

---

### Task 18: Scanner

**Files:**
- Create: `app/src/mediaferry/jobs/scan.py`
- Test: `app/tests/test_scanner.py`

**Interfaces:**
- Consumes: `iter_media_files` / `open_beneath`（Task 15）、`quick_fingerprint`（Task 8）、
  `source_entry`（Task 3）、`ProfileRef`（Task 13）、`JobContext`（Task 14）
- Produces:
  - `mediaferry.jobs.scan.ScanOutcome(total, new, already_imported, ambiguous)`
  - `Scanner(conn)` — `.scan(ctx, dirfd, volume_instance_id, profile) -> ScanOutcome`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_scanner.py`:

```python
import os

import pytest

from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.scan import Scanner

from .test_schema_sources import a_volume


@pytest.fixture
def scanning(db, tmp_path):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    card = tmp_path / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").write_bytes(b"a" * 100)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.LRF").write_bytes(b"low")
    fd = os.open(card, os.O_RDONLY | os.O_DIRECTORY)
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    yield Scanner(db), ctx, fd, volume_id, profile, card
    os.close(fd)


def test_scanning_records_entries_for_matching_files(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.total == 1
    assert outcome.new == 1
    row = db.execute("SELECT * FROM source_entry").fetchone()
    assert row["rel_path"] == "DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"
    assert row["size_bytes"] == 100
    assert row["state"] == "seen"
    assert len(row["quick_fingerprint"]) == 40


def test_rescanning_an_unchanged_card_finds_nothing_new(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published'")
    second = scanner.scan(ctx, fd, volume_id, profile)
    assert second.already_imported == 1
    assert second.new == 0
    assert db.execute("SELECT count(*) FROM source_entry").fetchone()[0] == 1


def test_a_reused_filename_with_different_content_is_new_again(scanning, db):
    """SD をフォーマットして連番が再利用されたケース."""
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published'")
    target = card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4"
    target.write_bytes(b"b" * 100)  # 同じサイズ、違う中身
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.new == 1
    assert db.execute("SELECT state FROM source_entry").fetchone()["state"] == "seen"


def test_an_older_mtime_than_recorded_is_ambiguous(scanning, db):
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published', mtime_ns = mtime_ns + 1000000000")
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.ambiguous == 1


def test_an_old_fingerprint_version_is_recomputed(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET fingerprint_version = 0, quick_fingerprint = 'stale'")
    scanner.scan(ctx, fd, volume_id, profile)
    row = db.execute("SELECT * FROM source_entry").fetchone()
    assert row["fingerprint_version"] == 1
    assert row["quick_fingerprint"] != "stale"


def test_an_old_version_is_recomputed_even_when_the_fingerprint_matches(scanning, db):
    """版を上げる意味は「前の版の判定を信用しない」こと.

    指紋の文字列が一致しているかどうかで版の検査を代用すると、算出方法を
    変えた版でたまたま一致した行を取り込み済みのまま据え置いてしまう。
    """
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published', fingerprint_version = 0")

    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.new == 1
    assert outcome.already_imported == 0
    row = db.execute("SELECT * FROM source_entry").fetchone()
    assert row["fingerprint_version"] == 1
    assert row["state"] == "seen"


def test_an_entry_that_was_never_imported_is_still_new(scanning, db):
    """スキャンしただけの行を「取り込み済み」と報告すると、永久に取り込まれない."""
    scanner, ctx, fd, volume_id, profile, _ = scanning
    first = scanner.scan(ctx, fd, volume_id, profile)
    assert first.new == 1

    second = scanner.scan(ctx, fd, volume_id, profile)
    assert second.already_imported == 0
    assert second.new == 1
    assert db.execute("SELECT state FROM source_entry").fetchone()["state"] == "seen"


def test_the_lease_is_kept_alive_while_scanning(scanning, db):
    """16GiB のカードは 1 スキャンがリース (60 秒) より長くなりうる.

    heartbeat を打たないと、途中で失効して reap され、走り続けているのに
    interrupted として表示される。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    (card / "DCIM" / "DJI_001" / "DJI_20260817143001_0002_D.MP4").write_bytes(b"c" * 10)
    beats = []
    real_heartbeat = ctx.heartbeat
    ctx.heartbeat = lambda: (beats.append(1), real_heartbeat())[1]

    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert len(beats) == outcome.total == 2


def test_cancelling_stops_the_scan(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    JobStore(db).request_cancel(ctx.job_id)
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.total == 0


def test_progress_events_name_the_file(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    messages = [e["message"] for e in JobStore(db).events(ctx.job_id)]
    assert any("DJI_20260817143000_0001_D.MP4" in m for m in messages)
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_scanner.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/jobs/scan.py`:

```python
"""ソースボリュームのスキャン（§9.5）.

dirfd 起点で scan.roots 配下を列挙し、既知の source_entry と照合する。
この段階でフル SHA-1 は計算しない（16GiB を読む必要があるため）。
同一性の判定には quick_fingerprint を使う。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from ..adapters.fs import iter_media_files, open_beneath
from ..clock import now_iso
from ..core.fingerprint import FINGERPRINT_VERSION, quick_fingerprint
from ..db.jobs import JobContext
from ..db.profiles import ProfileRef
from ..ids import new_id


@dataclass(frozen=True)
class ScanOutcome:
    total: int
    new: int
    already_imported: int
    ambiguous: int


class Scanner:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def scan(
        self, ctx: JobContext, dirfd: int, volume_instance_id: str, profile: ProfileRef
    ) -> ScanOutcome:
        defn = profile.definition
        total = new = imported = ambiguous = 0
        for found in iter_media_files(dirfd, defn.scan.roots, defn.scan.extensions):
            if ctx.cancelled():
                break
            total += 1
            ctx.heartbeat()
            fingerprint = self._fingerprint(dirfd, found.rel_path, found.size_bytes)
            verdict = self._reconcile_entry(volume_instance_id, found, fingerprint)
            if verdict == "imported":
                imported += 1
            elif verdict == "ambiguous":
                ambiguous += 1
            else:
                new += 1
            ctx.emit("info", f"{found.rel_path}: {verdict}", {"size_bytes": found.size_bytes})
        return ScanOutcome(total=total, new=new, already_imported=imported, ambiguous=ambiguous)

    def _fingerprint(self, dirfd: int, rel_path: str, size: int) -> str:
        fd = open_beneath(dirfd, rel_path)
        with os.fdopen(fd, "rb") as fileobj:
            return quick_fingerprint(fileobj, size)

    def _reconcile_entry(self, volume_instance_id: str, found, fingerprint: str) -> str:  # noqa: ANN001
        row = self._conn.execute(
            "SELECT * FROM source_entry WHERE volume_instance_id = ? AND rel_path = ?",
            (volume_instance_id, found.rel_path),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
                " quick_fingerprint, fingerprint_version, state, observed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'seen', ?)",
                (
                    new_id(),
                    volume_instance_id,
                    found.rel_path,
                    found.size_bytes,
                    found.mtime_ns,
                    fingerprint,
                    FINGERPRINT_VERSION,
                    now_iso(),
                ),
            )
            return "new"

        same = (
            row["size_bytes"] == found.size_bytes
            and row["quick_fingerprint"] == fingerprint
            and row["fingerprint_version"] == FINGERPRINT_VERSION
        )
        if same and row["mtime_ns"] == found.mtime_ns and row["state"] == "published":
            self._touch(row["id"])
            return "imported"
        if same and found.mtime_ns < row["mtime_ns"]:
            # 指紋は一致するが mtime が記録より古い。フルハッシュで確認する
            # （deep_verify で扱う。ここでは曖昧として画面に出す）。
            self._touch(row["id"])
            return "ambiguous"
        self._conn.execute(
            "UPDATE source_entry SET size_bytes = ?, mtime_ns = ?, quick_fingerprint = ?,"
            " fingerprint_version = ?, state = 'seen', media_file_id = NULL, observed_at = ?"
            " WHERE id = ?",
            (
                found.size_bytes,
                found.mtime_ns,
                fingerprint,
                FINGERPRINT_VERSION,
                now_iso(),
                row["id"],
            ),
        )
        return "new"

    def _touch(self, entry_id: str) -> None:
        self._conn.execute(
            "UPDATE source_entry SET observed_at = ? WHERE id = ?", (now_iso(), entry_id)
        )
```

**注**: `same` の再計算のために `fingerprint_version` が古い行は
`same` が偽になり、下の UPDATE で再計算された指紋に更新される。
`state` が `published` のまま維持されないのは、指紋の版が変わると
「同じファイルか」を主張できないため。テスト
`test_an_old_version_is_recomputed_even_when_the_fingerprint_matches` が
この挙動を固定している（指紋の文字列も壊すテストでは、版の検査を通らない）。

**注**: `same` の `row["size_bytes"] == found.size_bytes` は変異させても
検出できない。`quick_fingerprint` が size を digest に含むので、サイズが
違えば指紋も必ず違う。冗長だが、指紋の定義を変えたときの保険として残す。

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_scanner.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`same` から `fingerprint_version` の比較を、取り込み済み判定から
`row["state"] == "published"` を、ループから `ctx.heartbeat()` をそれぞれ削り、
対応するテストが落ちることを確認してから戻す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/jobs/scan.py app/tests/test_scanner.py
git commit -m "feat(mediaferry): scan source volumes into source entries"
```

---

### Task 19: Importer

**Files:**
- Create: `app/src/mediaferry/jobs/importer.py`
- Test: `app/tests/test_importer.py`

**Interfaces:**
- Consumes: `ArtifactPublisher`（Task 17）、`open_beneath`（Task 15）、
  `resolve_captured_at`（Task 11）、`library_rel_path`（Task 12）、`ProfileRef`（Task 13）
- Produces:
  - `mediaferry.jobs.importer.ImportOutcome(published, skipped, failed)`
  - `mediaferry.jobs.importer.ImportFailed(message, outcome)`
  - `Importer(conn, publisher, data_root, default_timezone)` —
    `.run(ctx, dirfd, volume_instance_id, profile) -> ImportOutcome`
  - 例外 `NotEnoughSpace`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_importer.py`:

```python
import errno
import os

import pytest

from mediaferry.adapters.publisher import ArtifactPublisher, PublishInterrupted
from mediaferry.core.timestamps import TimezoneUnresolved
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.importer import Importer, ImportFailed, NotEnoughSpace
from mediaferry.jobs.scan import Scanner

from .test_publisher import StubProbe
from .test_schema_sources import a_volume


@pytest.fixture
def importing(db, data_root, tmp_path):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    card = tmp_path / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").write_bytes(b"a" * 100)
    (card / "PANORAMA").mkdir()
    (card / "PANORAMA" / "PANO_0001.JPG").write_bytes(b"jpeg")
    fd = os.open(card, os.O_RDONLY | os.O_DIRECTORY)
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    Scanner(db).scan(ctx, fd, volume_id, profile)
    publisher = ArtifactPublisher(db, data_root, StubProbe())
    importer = Importer(db, publisher, data_root, default_timezone="Asia/Tokyo")
    yield importer, ctx, fd, volume_id, profile
    os.close(fd)


def test_import_mirrors_the_path_on_the_card(importing, db, data_root):
    importer, ctx, fd, volume_id, profile = importing
    outcome = importer.run(ctx, fd, volume_id, profile)
    assert outcome.published == 2
    assert (
        data_root / "library/dji-osmo/DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"
    ).read_bytes() == b"a" * 100
    assert (data_root / "library/dji-osmo/PANORAMA/PANO_0001.JPG").exists()


def test_captured_at_comes_from_the_filename_when_it_matches(importing, db):
    importer, ctx, fd, volume_id, profile = importing
    importer.run(ctx, fd, volume_id, profile)
    row = db.execute(
        "SELECT * FROM media_file WHERE rel_path LIKE '%DJI_20260817143000%'"
    ).fetchone()
    assert row["captured_at"].startswith("2026-08-17T14:30:00")
    assert row["captured_at_source"] == "filename"
    assert row["captured_at_tz"] == "Asia/Tokyo"


def test_files_without_a_timestamp_in_the_name_fall_back_to_mtime(importing, db):
    importer, ctx, fd, volume_id, profile = importing
    importer.run(ctx, fd, volume_id, profile)
    row = db.execute("SELECT * FROM media_file WHERE rel_path LIKE '%PANO_0001%'").fetchone()
    assert row["captured_at_source"] == "mtime"


def test_reimporting_is_a_no_op(importing, db, data_root):
    importer, ctx, fd, volume_id, profile = importing
    importer.run(ctx, fd, volume_id, profile)
    second = importer.run(ctx, fd, volume_id, profile)
    assert second.published == 0
    assert second.skipped == 2
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 2


def test_missing_timezone_stops_before_touching_anything(db, data_root, importing):
    """force_offset なのに TZ が無いなら、取り込みを一切開始しない（§12.2）."""
    _, ctx, fd, volume_id, profile = importing
    importer = Importer(
        db, ArtifactPublisher(db, data_root, StubProbe()), data_root, default_timezone=None
    )
    with pytest.raises(TimezoneUnresolved):
        importer.run(ctx, fd, volume_id, profile)
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0
    assert not list((data_root / "library").rglob("*"))


def test_not_enough_space_stops_before_starting(importing, db, monkeypatch):
    importer, ctx, fd, volume_id, profile = importing
    monkeypatch.setattr(importer, "_free_bytes", lambda: 1)
    with pytest.raises(NotEnoughSpace):
        importer.run(ctx, fd, volume_id, profile)
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0


def test_cancelling_stops_between_files(importing, db, data_root, monkeypatch):
    """キャンセル済みなら 1 件目にも手を付けない.

    ファイル単位の確認が無くても chunk 境界で降りるので結果は同じだが、
    16GiB のカードでは「開いて読み始めてから降りる」だけで待たされる。
    """
    importer, ctx, fd, volume_id, profile = importing
    attempted = []
    original = importer._publish_one  # noqa: SLF001
    monkeypatch.setattr(
        importer,
        "_publish_one",
        lambda *a, **k: (attempted.append(1), original(*a, **k))[1],
    )

    JobStore(db).request_cancel(ctx.job_id)
    outcome = importer.run(ctx, fd, volume_id, profile)
    assert outcome.published == 0
    assert attempted == []


def test_a_failing_file_does_not_stop_the_rest_but_fails_the_job(importing, db, monkeypatch):
    """ファイル単位では続行する. ただしジョブは failed で終わる.

    全件失敗しても succeeded になると、監視も画面も「取り込めた」と読む。
    """
    importer, ctx, fd, volume_id, profile = importing
    calls = {"n": 0}
    original = importer._publish_one  # noqa: SLF001

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("メタデータが読めない")
        return original(*args, **kwargs)

    monkeypatch.setattr(importer, "_publish_one", flaky)
    with pytest.raises(ImportFailed) as exc:
        importer.run(ctx, fd, volume_id, profile)
    assert exc.value.outcome.failed == 1
    assert exc.value.outcome.published == 1
    failed_count = db.execute(
        "SELECT count(*) FROM source_entry WHERE state = 'failed'"
    ).fetchone()[0]
    assert failed_count == 1


def test_a_vanished_card_stops_the_run_instead_of_grinding_through(importing, db, monkeypatch):
    """取り込み中にカードを抜くケース（手動チェックリスト #5）."""
    importer, ctx, fd, volume_id, profile = importing

    def gone(*args, **kwargs):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(importer, "_publish_one", gone)
    with pytest.raises(ImportFailed) as exc:
        importer.run(ctx, fd, volume_id, profile)
    assert exc.value.outcome.published == 0
    # 2 件目は試さずに降りる
    assert exc.value.outcome.failed == 1


def test_an_interrupted_publish_leaves_the_entry_for_reconciliation(importing, db, monkeypatch):
    """staged 以降の失敗で failed に戻すと、次のスキャンで二重に取り込む."""
    importer, ctx, fd, volume_id, profile = importing

    def interrupted(*args, **kwargs):
        raise PublishInterrupted("link に失敗した")

    monkeypatch.setattr(importer, "_publish_one", interrupted)
    with pytest.raises(PublishInterrupted):
        importer.run(ctx, fd, volume_id, profile)
    failed_count = db.execute(
        "SELECT count(*) FROM source_entry WHERE state = 'failed'"
    ).fetchone()[0]
    assert failed_count == 0


def test_the_copy_heartbeats_on_elapsed_time_not_bytes(importing, db, monkeypatch):
    """低速なカードだと、閾値バイトに達する前にリースが切れる."""
    from mediaferry.jobs import importer as importer_module

    importer, ctx, fd, volume_id, profile = importing
    monkeypatch.setattr(importer_module, "COPY_CHUNK", 8)
    monkeypatch.setattr(importer_module, "HEARTBEAT_INTERVAL", 0)
    beats = []
    monkeypatch.setattr(ctx, "heartbeat", lambda: beats.append(1))
    importer.run(ctx, fd, volume_id, profile)
    # 100 バイトを 8 バイトずつなので、ファイル単位の 1 回より多く打つ
    assert len(beats) > 2


def test_the_copy_stops_at_a_chunk_boundary_when_cancelled(importing, db, monkeypatch):
    """chunk 境界がキャンセルポイント（§9.9）. 見ないと 16GiB 待たされる.

    最後まで読んでも staged の直前で assert_lease が止めるので、結果だけを
    見ると差が出ない。**降りるまでに何バイト読んだか**が違いになる。
    キャンセルはコピーが始まってから出す。開始前に出すと、ファイル単位の
    確認で先に抜けてこの境界を通らない。
    """
    from mediaferry.adapters.publisher import HashingWriter
    from mediaferry.jobs import importer as importer_module

    importer, ctx, fd, volume_id, profile = importing
    monkeypatch.setattr(importer_module, "COPY_CHUNK", 8)

    written = []
    real_write = HashingWriter.write

    def spy_write(self, data):
        written.append(len(data))
        if len(written) == 1:
            JobStore(db).request_cancel(ctx.job_id)
        return real_write(self, data)

    monkeypatch.setattr(HashingWriter, "write", spy_write)

    outcome = importer.run(ctx, fd, volume_id, profile)
    assert outcome.published == 0
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0
    # 100 バイトのファイルを 8 バイト刻みで読み切る前に降りている
    assert sum(written) < 100
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_importer.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/jobs/importer.py`:

```python
"""ソースからライブラリへの取り込み（§9.4）.

ファイルは 1 つずつ順に処理する。USB が律速なので並列化しない。公開は
ArtifactPublisher に委譲し、中断ファイルが転送先に残る問題は staging と
no-clobber 公開で構造的に起きないようにしてある。
"""

from __future__ import annotations

import errno
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..adapters.fs import open_beneath
from ..adapters.publisher import (
    ArtifactPublisher,
    ArtifactRequest,
    HashingWriter,
    PublishAborted,
    PublishInterrupted,
)
from ..clock import now_iso
from ..core.naming import library_rel_path
from ..core.timestamps import resolve_captured_at
from ..db.jobs import LEASE_SECONDS, JobContext
from ..db.profiles import ProfileRef

COPY_CHUNK = 4 * 1024 * 1024
# 空き容量の見積りに乗せる余裕。DB とサムネイルの分。
FREE_SPACE_MARGIN = 512 * 1024 * 1024
# リース (60 秒) の 1/3 ごとに延ばす。16GiB のコピーはリースより長く、
# 転送速度は環境で桁が変わるので、バイト数ではなく時間で決める。
HEARTBEAT_INTERVAL = LEASE_SECONDS / 3

# カードが抜けたときに出る errno。残りを試しても同じように失敗する。
_DEVICE_GONE = frozenset({errno.EIO, errno.ENODEV, errno.ENXIO, errno.ESTALE, errno.EBADF})


class NotEnoughSpace(RuntimeError):
    pass


class CopyCancelled(RuntimeError):
    """コピーの途中でキャンセル要求を観測した."""


@dataclass(frozen=True)
class ImportOutcome:
    published: int
    skipped: int
    failed: int


class ImportFailed(RuntimeError):
    """1 件以上の取り込みに失敗した. ジョブを failed にするために送出する."""

    def __init__(self, message: str, outcome: ImportOutcome) -> None:
        super().__init__(message)
        self.outcome = outcome


class Importer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        publisher: ArtifactPublisher,
        data_root: Path,
        default_timezone: str | None,
    ) -> None:
        self._conn = conn
        self._publisher = publisher
        self._data_root = data_root
        self._default_timezone = default_timezone

    def run(
        self, ctx: JobContext, dirfd: int, volume_instance_id: str, profile: ProfileRef
    ) -> ImportOutcome:
        pending = list(
            self._conn.execute(
                "SELECT * FROM source_entry WHERE volume_instance_id = ?"
                " AND state IN ('seen', 'failed') ORDER BY rel_path",
                (volume_instance_id,),
            )
        )
        skipped = self._conn.execute(
            "SELECT count(*) FROM source_entry"
            " WHERE volume_instance_id = ? AND state = 'published'",
            (volume_instance_id,),
        ).fetchone()[0]

        # 取り込みを一切開始しない条件を先に確かめる。途中で止まると
        # 中途半端な状態がユーザに見えるため。
        needed = sum(row["size_bytes"] for row in pending)
        if needed + FREE_SPACE_MARGIN > self._free_bytes():
            raise NotEnoughSpace(f"{needed} バイトの取り込みに空き容量が足りない")
        for row in pending:
            resolve_captured_at(
                profile.definition, row["rel_path"], row["mtime_ns"], self._default_timezone
            )

        published = failed = 0
        for row in pending:
            if ctx.cancelled():
                break
            ctx.heartbeat()
            try:
                self._publish_one(ctx, dirfd, row, profile)
            except (PublishAborted, CopyCancelled):
                # staged より前なので durable なものは残っていない。差し戻す。
                # キャンセルなら降りる。失効なら失敗として上へ投げる。
                self._conn.execute(
                    "UPDATE source_entry SET state = 'seen' WHERE id = ?", (row["id"],)
                )
                if ctx.cancelled():
                    break
                raise
            except PublishInterrupted:
                # staged 以降で失敗した。ファイルは検証済みで、起動時の
                # reconciliation が公開を完遂する。**failed に戻さない**
                # （戻すと次のスキャンで新規と判定され、二重に取り込む）。
                ctx.emit("warning", f"{row['rel_path']} の公開は起動時に再開される")
                raise
            except OSError as exc:
                failed += 1
                self._mark_failed(row["id"])
                ctx.emit("error", f"{row['rel_path']} の取り込みに失敗した: {exc}")
                if exc.errno in _DEVICE_GONE:
                    # カードが抜かれた。残りを試しても同じように失敗する。
                    ctx.emit("error", "ソースが読めなくなったので中断する")
                    break
                continue
            except Exception as exc:  # noqa: BLE001 - 1 件の失敗で全体を止めない
                failed += 1
                self._mark_failed(row["id"])
                ctx.emit("error", f"{row['rel_path']} の取り込みに失敗した: {exc}")
                continue
            published += 1
            ctx.emit("info", f"{row['rel_path']} を取り込んだ")

        outcome = ImportOutcome(published=published, skipped=skipped, failed=failed)
        if failed:
            # 1 件でも落ちたらジョブは失敗にする。全件失敗しても succeeded に
            # なると、監視も画面も「取り込めた」と読んでしまう。
            raise ImportFailed(f"{failed} 件の取り込みに失敗した（成功 {published} 件）", outcome)
        return outcome

    def _mark_failed(self, entry_id: str) -> None:
        self._conn.execute("UPDATE source_entry SET state = 'failed' WHERE id = ?", (entry_id,))

    def _publish_one(
        self, ctx: JobContext, dirfd: int, row: sqlite3.Row, profile: ProfileRef
    ) -> None:
        captured = resolve_captured_at(
            profile.definition, row["rel_path"], row["mtime_ns"], self._default_timezone
        )
        request = ArtifactRequest(
            kind="import",
            role="original",
            profile_id=profile.profile_id,
            profile_revision_id=profile.revision_id,
            desired_rel_path=library_rel_path("original", profile.definition.slug, row["rel_path"]),
            source_rel_path=row["rel_path"],
            extension=PurePosixPath(row["rel_path"]).suffix.lstrip(".").upper(),
            captured=captured,
            mtime_ns=row["mtime_ns"],
            source_entry_id=row["id"],
            merge_group_id=None,
        )
        self._conn.execute(
            "UPDATE source_entry SET state = 'importing', observed_at = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )

        def write(writer: HashingWriter) -> None:
            fd = open_beneath(dirfd, row["rel_path"])
            last_beat = time.monotonic()
            with os.fdopen(fd, "rb") as source:
                while chunk := source.read(COPY_CHUNK):
                    writer.write(chunk)
                    # chunk 境界がキャンセルポイント（§9.9）。ここで見ないと、
                    # 16GiB のコピーが終わるまで停止要求に応じられない。
                    if ctx.cancelled():
                        raise CopyCancelled(row["rel_path"])
                    if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL:
                        # **バイト数ではなく経過時間で打つ。** 低速なカードや
                        # read が詰まった場合、閾値バイトに達する前にリースが
                        # 切れて全件が中止される。
                        ctx.heartbeat()
                        last_beat = time.monotonic()

        self._publisher.publish(ctx, request, write)

    def _free_bytes(self) -> int:
        stat = os.statvfs(self._data_root)
        return stat.f_bavail * stat.f_frsize
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_importer.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`PublishInterrupted` の except 節を削って
`test_an_interrupted_publish_leaves_the_entry_for_reconciliation` が、
`_DEVICE_GONE` の break を削って
`test_a_vanished_card_stops_the_run_instead_of_grinding_through` が落ちることを
確認してから戻す。

**キャンセルの 2 箇所は、`run()` の前にキャンセルしても検出できない。**
どちらを消しても最後は `assert_lease` が止めるので結果が同じになる。差が出るのは
「何バイト読んだか」「次のファイルに手を付けたか」なので、コピーが始まってから
キャンセルを出し、その量を測る。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/jobs/importer.py app/tests/test_importer.py
git commit -m "feat(mediaferry): import source files into the library"
```

---

### Task 20: Reconciler

**Files:**
- Create: `app/src/mediaferry/jobs/reconcile.py`
- Test: `app/tests/test_reconciler.py`

**Interfaces:**
- Consumes: `ArtifactPublisher.resume`（Task 17）、`JobStore.sweep_interrupted`（Task 14）
- Produces:
  - `mediaferry.jobs.reconcile.OrphanFile(rel_path, size_bytes, sha1)`
  - `ReconcileReport(discarded, resumed, recommitted, orphans, missing, cleaned_dirs)`
  - `Reconciler(conn, data_root, publisher, store)` — `.run() -> ReconcileReport`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_reconciler.py`:

```python
import json

import pytest

from mediaferry.adapters.publisher import ArtifactPublisher, PublishInterrupted
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.reconcile import Reconciler

from .test_publisher import StubProbe, _Crash, _die_after, a_request, write_payload
from .test_schema_artifacts import a_source_entry
from .test_schema_sources import a_volume


@pytest.fixture
def world(db, data_root):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    publisher = ArtifactPublisher(db, data_root, StubProbe())
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    return store, publisher, profile, volume_id, Reconciler(db, data_root, publisher, store)


def test_a_writing_row_is_discarded_with_its_temp_file(world, db, data_root):
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    staging = data_root / "staging" / ctx.job_id / "half-written"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"half")
    db.execute(
        "INSERT INTO artifact_staging (id, kind, job_id, lease_token, state, staging_rel_path,"
        " source_entry_id, created_at, updated_at)"
        " VALUES ('s1', 'import', ?, ?, 'writing', ?, ?, '2026-01-01T00:00:00+00:00',"
        " '2026-01-01T00:00:00+00:00')",
        (
            ctx.job_id,
            ctx.lease_token,
            f"staging/{ctx.job_id}/half-written",
            a_source_entry(db, volume_id),
        ),
    )
    report = reconciler.run()
    assert report.discarded == 1
    assert not staging.exists()
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 0


def test_a_staged_row_is_published_from_persisted_facts_alone(world, db, data_root):
    """パスを推測せず、final_rel_path と content_sha1 だけで再開する."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    entry_id = a_source_entry(db, volume_id)
    publisher._checkpoint = _die_after(7)  # noqa: SLF001
    with pytest.raises(_Crash):
        publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    report = reconciler.run()
    assert report.resumed == 1
    assert (data_root / "library/dji-osmo/DCIM/A.MP4").read_bytes() == b"payload"
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 1


def test_a_crash_between_link_and_commit_still_publishes(world, db, data_root):
    """手順 10 まで進んだ行は staged のまま残り、再開すると commit だけをやり直す."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    entry_id = a_source_entry(db, volume_id)
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    reconciler.run()
    assert db.execute("SELECT media_file_id FROM source_entry").fetchone()[0] is not None
    assert (data_root / "library/dji-osmo/DCIM/A.MP4").read_bytes() == b"payload"


def test_a_published_row_without_a_media_file_is_recommitted(world, db, data_root):
    """commit は 1 トランザクションなので通常は起きない. 手で DB を壊した場合の保険."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    db.execute("UPDATE source_entry SET media_file_id = NULL, state = 'importing'")
    db.execute("DELETE FROM media_file WHERE id = ?", (got.media_file_id,))

    report = reconciler.run()
    assert report.recommitted == 1
    assert db.execute("SELECT media_file_id FROM source_entry").fetchone()[0] is not None


def test_orphans_are_reported_and_never_deleted(world, data_root):
    """自動削除するとデータを失う経路になる. 画面に出してユーザの判断に委ねる."""
    *_, reconciler = world
    orphan = data_root / "library/dji-osmo/DCIM/UNKNOWN.MP4"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"who put this here")
    report = reconciler.run()
    assert [o.rel_path for o in report.orphans] == ["library/dji-osmo/DCIM/UNKNOWN.MP4"]
    assert orphan.exists()


def test_a_missing_file_marks_the_record_and_a_restored_one_clears_it(world, db, data_root):
    """一時的に dataset が見えなかっただけで永久に欠損扱いにしない."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    path = data_root / got.rel_path
    payload = path.read_bytes()

    path.unlink()
    assert reconciler.run().missing == 1
    missing_at = db.execute(
        "SELECT missing_at FROM media_file WHERE id = ?", (got.media_file_id,)
    ).fetchone()[0]
    assert missing_at is not None

    path.write_bytes(payload)
    assert reconciler.run().restored == 1
    missing_at = db.execute(
        "SELECT missing_at FROM media_file WHERE id = ?", (got.media_file_id,)
    ).fetchone()[0]
    assert missing_at is None


def test_an_unrecoverable_staging_row_is_reported_not_dropped(world, db, data_root):
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001
    (data_root / "library/dji-osmo/DCIM/A.MP4").unlink()

    report = reconciler.run()
    assert len(report.unrecoverable) == 1
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 1


def test_a_row_that_could_not_be_recovered_keeps_its_files_for_the_next_startup(
    world, db, data_root, monkeypatch
):
    """回収に失敗した行は次回も試す。その材料を同じ回で捨てない.

    手順 9（公開先の fsync）で落ちた行は、公開先の実体も staging の一時
    ファイルも残っている。ここで staging のディレクトリを消すと次回は
    回収できず、公開先を孤立ファイルとして報告すると、公開途中のファイルを
    ユーザに「素性の分からないファイル」として見せることになる。
    """
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    publisher._checkpoint = _die_after(9)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    staging_dir = data_root / "staging" / ctx.job_id
    assert list(staging_dir.iterdir())  # 一時ファイルはまだある

    def unavailable(staging_id):
        raise OSError("データセットが一時的に見えない")

    monkeypatch.setattr(publisher, "resume", unavailable)
    report = reconciler.run()

    assert len(report.unrecoverable) == 1
    assert staging_dir.exists()
    assert [o.rel_path for o in report.orphans] == []


def test_stale_job_directories_are_cleaned_but_live_ones_are_kept(world, db, data_root):
    """使用中の可能性があるものを消さない. 所有者のジョブの状態を必ず確かめる."""
    store, publisher, profile, volume_id, reconciler = world
    dead = store.enqueue("import", {})
    ctx = store.claim_next()
    store.finish(dead, ctx.lease_token, "failed")
    (data_root / "staging" / dead).mkdir(parents=True)
    (data_root / "work" / dead).mkdir(parents=True)

    queued = store.enqueue("import", {})
    (data_root / "staging" / queued).mkdir(parents=True)

    report = reconciler.run()
    assert not (data_root / "staging" / dead).exists()
    assert not (data_root / "work" / dead).exists()
    assert (data_root / "staging" / queued).exists()
    assert report.cleaned_dirs == 2


def test_running_jobs_are_marked_interrupted_first(world, db):
    store, *_, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    reconciler.run()
    assert store.get(ctx.job_id)["status"] == "interrupted"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_reconciler.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/jobs/reconcile.py`:

```python
"""起動時の齟齬回収（§9.6）.

library/ と derived/ の両方を対象にする。一時ファイルを無条件に消さないのは、
別ジョブが使用中の可能性があるため。必ずジョブの所有権とリース状態、および
artifact_staging の参照を確認してから消す。

孤立ファイルは**削除しない**。自動削除はデータを失う経路になるので、画面に
出してユーザの判断に委ねる。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.publisher import ArtifactPublisher, StagingLost
from ..clock import now_iso
from ..db.jobs import JobStore

logger = logging.getLogger(__name__)

HASH_CHUNK = 4 * 1024 * 1024
LIVE_JOB_STATES = ("queued", "running", "cancelling")


@dataclass(frozen=True)
class OrphanFile:
    rel_path: str
    size_bytes: int
    sha1: str


@dataclass
class ReconcileReport:
    discarded: int = 0
    resumed: int = 0
    recommitted: int = 0
    missing: int = 0
    restored: int = 0
    cleaned_dirs: int = 0
    orphans: list[OrphanFile] = field(default_factory=list)
    # 自動では続行できなかった staging（実体が無い、内容が一致しない）。
    # 行は残す。画面に出して判断を仰ぐ。
    unrecoverable: list[str] = field(default_factory=list)


class Reconciler:
    def __init__(
        self,
        conn: sqlite3.Connection,
        data_root: Path,
        publisher: ArtifactPublisher,
        store: JobStore,
    ) -> None:
        self._conn = conn
        self._data_root = data_root
        self._publisher = publisher
        self._store = store

    def run(self) -> ReconcileReport:
        report = ReconcileReport()
        # 先にジョブを倒す。生きているジョブが無いことを確定させてから
        # staging と work を掃除する。
        self._store.sweep_interrupted()
        self._recover_staging(report)
        self._sync_missing(report)
        self._collect_orphans(report)
        self._clean_job_dirs(report)
        return report

    def _recover_staging(self, report: ReconcileReport) -> None:
        # published の行は commit と同じトランザクションで media_file を作るので、
        # 通常は齟齬が出ない。手で DB をいじった場合や将来の版のために拾っておく。
        rows = list(
            self._conn.execute(
                "SELECT s.id AS id, s.state AS state FROM artifact_staging s"
                " LEFT JOIN media_file m ON m.rel_path = s.final_rel_path"
                " WHERE s.state <> 'published' OR m.id IS NULL"
            )
        )
        for row in rows:
            state = row["state"]
            try:
                self._publisher.resume(row["id"])
            except StagingLost:
                # 実体が無いか内容が合わない。黙って消さず、画面に出す。
                logger.warning("staging %s は自動で回収できない", row["id"])
                report.unrecoverable.append(row["id"])
                continue
            except OSError:
                # 1 件の失敗で回収全体を止めない。行は残るので次回も試す。
                logger.exception("staging %s の回収に失敗した", row["id"])
                report.unrecoverable.append(row["id"])
                continue
            if state == "writing":
                report.discarded += 1
            elif state == "staged":
                report.resumed += 1
            else:
                report.recommitted += 1

    def _sync_missing(self, report: ReconcileReport) -> None:
        """欠損を立てるだけでなく、戻ってきたら消す.

        データセットが一時的に見えなかっただけで永久に「欠損」のまま残ると、
        そのメディアはアップロードの安全条件（§10）から外れ続ける。
        """
        for row in self._conn.execute("SELECT id, rel_path, missing_at FROM media_file"):
            exists = (self._data_root / row["rel_path"]).exists()
            if not exists and row["missing_at"] is None:
                self._conn.execute(
                    "UPDATE media_file SET missing_at = ? WHERE id = ?", (now_iso(), row["id"])
                )
                report.missing += 1
            elif exists and row["missing_at"] is not None:
                self._conn.execute(
                    "UPDATE media_file SET missing_at = NULL WHERE id = ?", (row["id"],)
                )
                report.restored += 1

    def _collect_orphans(self, report: ReconcileReport) -> None:
        known = {row["rel_path"] for row in self._conn.execute("SELECT rel_path FROM media_file")}
        staged = {
            row["final_rel_path"]
            for row in self._conn.execute(
                "SELECT final_rel_path FROM artifact_staging WHERE final_rel_path IS NOT NULL"
            )
        }
        for top in ("library", "derived"):
            base = self._data_root / top
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(self._data_root))
                if rel in known or rel in staged:
                    continue
                report.orphans.append(
                    OrphanFile(rel_path=rel, size_bytes=path.stat().st_size, sha1=_sha1_of(path))
                )

    def _clean_job_dirs(self, report: ReconcileReport) -> None:
        live_jobs = {
            row["id"]
            for row in self._conn.execute(
                f"SELECT id FROM job WHERE status IN ({','.join('?' * len(LIVE_JOB_STATES))})",  # noqa: S608
                LIVE_JOB_STATES,
            )
        }
        # 回収できずに残った行が指すディレクトリは消さない。
        referenced = {
            row["job_id"]
            for row in self._conn.execute(
                "SELECT DISTINCT job_id FROM artifact_staging WHERE state <> 'published'"
            )
        }
        for top in ("staging", "work"):
            base = self._data_root / top
            if not base.exists():
                continue
            for path in sorted(base.iterdir()):
                if not path.is_dir():
                    continue
                if path.name in live_jobs or path.name in referenced:
                    continue
                shutil.rmtree(path)
                report.cleaned_dirs += 1


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as f:
        while chunk := f.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_reconciler.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`_collect_orphans` に `path.unlink()` を足し、
`test_orphans_are_reported_and_never_deleted` が落ちることを確認してから戻す。
`_clean_job_dirs` から `referenced` の除外を、`_collect_orphans` から `staged` の
除外をそれぞれ外し、
`test_a_row_that_could_not_be_recovered_keeps_its_files_for_the_next_startup` が
落ちることも確認する。**この 2 つは、回収がすべて成功する筋書きでは検出
できない**（成功した行は published になって known に入るため）。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/jobs/reconcile.py app/tests/test_reconciler.py
git commit -m "feat(mediaferry): recover artifacts and jobs at startup"
```

---

### Task 21: crash consistency テスト一式

**Files:**
- Create: `app/tests/crash_child.py`
- Test: `app/tests/test_crash_consistency.py`

**Interfaces:**
- Consumes: `ArtifactPublisher`（Task 17）、`Reconciler`（Task 20）
- Produces: なし（テストのみ）

§9.3 の**手順の数だけ**ケースを作り、import と merge の両方で行う。
子プロセスを `os._exit` で落とすので、Python の後始末も走らない。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/crash_child.py`:

```python
"""公開プロトコルの途中で本当にプロセスを落とす子プロセス.

`os._exit` を使うのは、例外だと `finally` と atexit が走ってしまい、
「電源が落ちた」状況にならないため。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from mediaferry.adapters.ffprobe import ProbeResult
from mediaferry.adapters.publisher import ArtifactPublisher, ArtifactRequest
from mediaferry.core.timestamps import CapturedAt
from mediaferry.db.connection import Database
from mediaferry.db.jobs import JobStore
from mediaferry.db.migrate import apply_migrations
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.ids import new_id

PAYLOAD = b"payload-for-crash-tests"


class _Probe:
    def describe(self, path, extension):  # noqa: ANN001, ANN201
        return ProbeResult("video", 2.0, "ok")


class CrashingPublisher(ArtifactPublisher):
    def __init__(self, *args, die_after: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._die_after = die_after

    def _checkpoint(self, step: int) -> None:
        if step == self._die_after:
            os._exit(9)  # noqa: SLF001


def main() -> None:
    data_root = Path(sys.argv[1])
    die_after = int(sys.argv[2])
    kind = sys.argv[3]

    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    apply_migrations(conn)
    registry = ProfileRegistry(conn)
    registry.sync_builtins()
    profile = registry.current("dji-osmo")

    store = JobStore(conn)
    store.enqueue(kind if kind == "merge" else "import", {})
    ctx = store.claim_next()

    source_entry_id = merge_group_id = None
    if kind == "import":
        volume_id, source_entry_id = _a_source(conn, profile)
    else:
        merge_group_id = _a_merge_group(conn, profile)

    publisher = CrashingPublisher(conn, data_root, _Probe(), die_after=die_after)
    request = ArtifactRequest(
        kind=kind,
        role="original" if kind == "import" else "derived",
        profile_id=profile.profile_id,
        profile_revision_id=profile.revision_id,
        desired_rel_path=(
            "library/dji-osmo/DCIM/A.MP4"
            if kind == "import"
            else "derived/dji-osmo/DCIM/MERGED.MP4"
        ),
        source_rel_path="DCIM/A.MP4",
        extension="MP4",
        captured=CapturedAt(
            at=datetime.fromisoformat("2026-08-17T14:30:00+09:00"),
            source="filename",
            tz="Asia/Tokyo",
            note=None,
        ),
        mtime_ns=1_700_000_000_000_000_000,
        source_entry_id=source_entry_id,
        merge_group_id=merge_group_id,
    )
    publisher.publish(ctx, request, lambda writer: writer.write(PAYLOAD))
    # ここへ来るのは die_after が 11 より大きいときだけ。
    sys.exit(0)


def _a_source(conn, profile):  # noqa: ANN001, ANN202
    from mediaferry.clock import now_iso

    volume_id, entry_id = new_id(), new_id()
    conn.execute(
        "INSERT INTO volume_instance (id, fs_uuid, fs_type, fs_label, size_bytes,"
        " identity_confidence, profile_id, profile_revision_id, first_seen_at, last_seen_at)"
        " VALUES (?, '26B1-2FD6', 'exfat', 'SD_Card', 1, 'high', ?, ?, ?, ?)",
        (volume_id, profile.profile_id, profile.revision_id, now_iso(), now_iso()),
    )
    conn.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES (?, ?, 'DCIM/A.MP4', ?, 1, 'abc', 1, 'importing', ?)",
        (entry_id, volume_id, len(PAYLOAD), now_iso()),
    )
    return volume_id, entry_id


def _a_merge_group(conn, profile):  # noqa: ANN001, ANN202
    from mediaferry.clock import now_iso

    group_id = new_id()
    conn.execute(
        "INSERT INTO merge_group (id, profile_id, profile_revision_id, status, input_digest,"
        " detected_by, created_at, updated_at)"
        " VALUES (?, ?, ?, 'merging', 'digest-1', 'auto', ?, ?)",
        (group_id, profile.profile_id, profile.revision_id, now_iso(), now_iso()),
    )
    return group_id


if __name__ == "__main__":
    main()
```

`app/tests/test_crash_consistency.py`:

```python
import subprocess
import sys
from pathlib import Path

import pytest

from mediaferry.adapters.ffprobe import ProbeResult
from mediaferry.adapters.publisher import ArtifactPublisher
from mediaferry.db.connection import Database
from mediaferry.db.jobs import JobStore
from mediaferry.db.migrate import apply_migrations
from mediaferry.jobs.reconcile import Reconciler

from .crash_child import PAYLOAD

CHILD = Path(__file__).parent / "crash_child.py"
STEPS = list(range(1, 12))


class _Probe:
    def describe(self, path, extension):
        return ProbeResult("video", 2.0, "ok")


def crash_at(data_root, step, kind):
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(CHILD), str(data_root), str(step), kind],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 9, (
        f"step {step} で落ちなかった: rc={completed.returncode} {completed.stderr}"
    )


def reconcile(data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    apply_migrations(conn)
    publisher = ArtifactPublisher(conn, data_root, _Probe())
    report = Reconciler(conn, data_root, publisher, JobStore(conn)).run()
    return conn, report


@pytest.mark.parametrize("kind", ["import", "merge"])
@pytest.mark.parametrize("step", STEPS)
def test_reconciliation_recovers_from_a_crash_at_any_step(data_root, step, kind):
    crash_at(data_root, step, kind)
    conn, report = reconcile(data_root)

    final = (
        "library/dji-osmo/DCIM/A.MP4" if kind == "import" else "derived/dji-osmo/DCIM/MERGED.MP4"
    )
    rows = conn.execute("SELECT count(*) FROM media_file").fetchone()[0]

    if step <= 6:
        # staged より前は「作業がなかったこと」になる。呼び出し元が再実行する。
        assert rows == 0
        assert report.discarded == 1
        assert not (data_root / final).exists()
    else:
        # staged 以降は永続情報だけで公開を再開できる。
        assert rows == 1
        assert (data_root / final).read_bytes() == PAYLOAD
        state = conn.execute("SELECT state FROM artifact_staging").fetchone()["state"]
        assert state == "published"

    # どの段階で落ちても、staging に中間ファイルは残らない（空のディレクトリは可）。
    assert [p for p in (data_root / "staging").rglob("*") if p.is_file()] == []
    assert report.orphans == []
    conn.close()


@pytest.mark.parametrize("step", [8, 9, 10])
def test_the_source_entry_is_linked_after_recovery(data_root, step):
    crash_at(data_root, step, "import")
    conn, _ = reconcile(data_root)
    row = conn.execute("SELECT * FROM source_entry").fetchone()
    assert row["state"] == "published"
    assert row["media_file_id"] is not None
    conn.close()


@pytest.mark.parametrize("step", STEPS)
def test_reconciliation_is_idempotent(data_root, step):
    crash_at(data_root, step, "import")
    conn, _ = reconcile(data_root)
    before = conn.execute("SELECT count(*) FROM media_file").fetchone()[0]
    conn.close()
    conn, second = reconcile(data_root)
    assert conn.execute("SELECT count(*) FROM media_file").fetchone()[0] == before
    assert second.orphans == []
    conn.close()


def test_a_crash_after_staging_never_overwrites_a_conflicting_file(data_root):
    """公開の直前に外から同名の別ファイルが現れても上書きしない."""
    crash_at(data_root, 7, "import")
    target = data_root / "library/dji-osmo/DCIM/A.MP4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"someone else's file")

    conn, _ = reconcile(data_root)
    assert target.read_bytes() == b"someone else's file"
    row = conn.execute("SELECT rel_path FROM media_file").fetchone()
    assert row["rel_path"] != "library/dji-osmo/DCIM/A.MP4"
    assert (data_root / row["rel_path"]).read_bytes() == PAYLOAD
    conn.close()
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_crash_consistency.py -v`
Expected: FAIL（`crash_child.py` が無い、または回収されない）

- [ ] **Step 3: 通るまで直す**

（実際には Task 17 と Task 20 が正しく実装されていれば、ここは一発で通る。）

このタスクは**新しい実装を書かない**。落ちたケースがあれば、
`ArtifactPublisher` か `Reconciler` の欠陥である。落ちたケースごとに
どの不変条件が破れたかを特定して直す。想定される修正点:

- `_recover_staging` の SQL が `published` かつ `media_file` 無しの行を拾えていない
- `_link` の EEXIST 経路で `final_rel_path` の更新を commit していない
- `resume` が `writing` の staging ファイルを消し損ねている

- [ ] **Step 4: すべて通ることを確認する**

Run: `uv run pytest app/tests/test_crash_consistency.py -v`
Expected: 22 + 3 + 11 + 1 件すべて PASS

- [ ] **Step 5: 変異試験**

`ArtifactPublisher.publish` の手順 7（`state = 'staged'` の UPDATE）から
`final_rel_path` の永続化を外し、step 8 以降のケースが落ちることを確認してから戻す。
`_link` の衝突経路で `final_rel_path` の更新を commit しないようにして
`test_a_crash_after_staging_never_overwrites_a_conflicting_file` が、
`_adopt_published_final` の分岐を削って step 10 のケースが落ちることも確認する。

**手順 10 の `staging_abs.unlink()` は、この一式では検出できない。** 残っても
Reconciler の `_clean_job_dirs` が同じ回でジョブのディレクトリごと消すため。
これは Task 17 の `test_the_staging_file_is_gone_and_the_row_is_published` が
担う（公開直後の状態を見るので、掃除に覆い隠されない）。

- [ ] **Step 6: コミット**

```bash
git add app/tests/crash_child.py app/tests/test_crash_consistency.py
git commit -m "test(mediaferry): kill the process at every publish step"
```

---

### Task 22: USB の product をプロトコルに乗せる

**Files:**
- Modify: `protocol/src/mediaferry_protocol/messages.py`（`UsbInfo` に `product`）
- Modify: `mountd/src/mountd/devices.py`（sysfs から `product` を読む）
- Modify: `protocol/tests/test_messages.py`
- Modify: `mountd/tests/test_devices.py`
- **Modify: 既存の `UsbInfo(...)` 呼び出しをすべて更新する。** 現在は
  `vendor_id` / `product_id` / `serial` の 3 引数で作られている:
  `app/tests/test_broker_client.py`、`protocol/tests/test_messages.py`、
  `mountd/tests/test_server.py`、`mountd/tests/test_mounts.py`。
  既定値は付けない（付けると「product を送っていない mountd」が黙って
  通ってしまい、Phase 0 で分かった機体同定の穴が残る）

**Interfaces:**
- Consumes: なし
- Produces: `UsbInfo(vendor_id, product_id, product, serial)`

Phase 0 の実測で、**DJI Osmo Pocket 4 の `serial` は Linux ガジェットの既定値
`123456789ABCDEF` だった**。機体を識別する文字列は `product`
（`OsmoPocket4-<機体固有>`）側にある。ところが現在の `UsbInfo` に `product` が
無く、`source_device` は常に空文字を保存することになる。**同じ機種を 2 台使うと
同一デバイスと誤認する。** スキーマ（Task 3）は 4 つ組で一意にしてあるので、
値を運ぶ側を先に直す。

- [ ] **Step 1: 失敗するテストを書く**

`protocol/tests/test_messages.py` に追記:

```python
def test_usb_info_carries_the_product_string():
    """serial は機種の既定値でありうる. 機体固有の文字列は product 側にある."""
    usb = usb_from_wire(
        {
            "vendor_id": "2ca3",
            "product_id": "0020",
            "product": "OsmoPocket4-ABC123",
            "serial": "123456789ABCDEF",
        }
    )
    assert usb.product == "OsmoPocket4-ABC123"


def test_usb_product_may_be_absent():
    """product を公開しないデバイスもある. null を受け付ける."""
    usb = usb_from_wire(
        {"vendor_id": "2ca3", "product_id": "0020", "product": None, "serial": None}
    )
    assert usb.product is None


def test_a_wire_message_without_the_product_field_is_rejected():
    """「値が無い」と「欄ごと無い」を分ける.

    product を送らない mountd を黙って通すと、serial が機種の既定値である
    機体を 2 台挿したときに同一デバイスと誤認する。
    """
    with pytest.raises(ProtocolError, match="product"):
        usb_from_wire({"vendor_id": "2ca3", "product_id": "0020", "serial": None})


def test_usb_info_has_no_default_values():
    """既定値を付けると、product を埋め忘れた組み立てが黙って通る."""
    assert [f.name for f in fields(UsbInfo) if f.default is not MISSING] == []
```

`mountd/tests/test_devices.py` に追記（既存の sysfs fake の作法に合わせる）:

既存の `make_sysfs` に `product` 属性を足したうえで:

```python
def test_usb_product_is_read_from_sysfs(tmp_path):
    """/sys/.../product に機体固有の文字列がある."""
    make_sysfs(tmp_path)
    vols = {v.device_node: v for v in enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe)}
    assert vols["/dev/sdk"].usb.product == "OsmoPocket4-ABC123"


def test_a_missing_product_attribute_is_none(tmp_path):
    make_sysfs(tmp_path)
    (tmp_path / "devices/pci0000:00/usb2/2-4/product").unlink()
    vols = {v.device_node: v for v in enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe)}
    assert vols["/dev/sdk"].usb.product is None
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest protocol/tests/test_messages.py mountd/tests/test_devices.py -v`
Expected: FAIL（`UsbInfo` に `product` が無い / `missing field: product`）

- [ ] **Step 3: 実装する**

`protocol/src/mediaferry_protocol/messages.py`:

```python
@dataclass(frozen=True)
class UsbInfo:
    vendor_id: str
    product_id: str
    # 機体固有の文字列。serial は機種の既定値でありうるので、デバイスの
    # 同定にはこれを含めた 4 つ組を使う。
    product: str | None
    serial: str | None
```

`usb_from_wire` に `product=_optional(d, "product", str)` を足す。

`mountd/src/mountd/devices.py` の USB 属性読み出しに `product` を加える。
`vendor`・`serial` と同じ経路（`/sys/.../product` を読み、無ければ `None`）で
取得する。**`_resolve_usb` の局所変数 `product` は `idProduct` を指しているので、
`product_id` へ改名してから足す**（そのままだと機体名で上書きされる）。

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest protocol mountd -v`
Expected: すべて PASS（`needs_root` は既定で skip）

- [ ] **Step 5: 変異試験**

`usb_from_wire` の `_optional` を `d.get` に変えて
`test_a_wire_message_without_the_product_field_is_rejected` が、`UsbInfo` の欄に
既定値を付けて `test_usb_info_has_no_default_values` が落ちることを確認してから
戻す。**この 2 つは別々の穴**で、前者は wire の欠落、後者は Python 側の
組み立て漏れを塞ぐ。

- [ ] **Step 6: コミット**

```bash
git add protocol mountd app/tests/test_broker_client.py
git commit -m "feat(mountd): report the usb product string over the wire"
```

---

### Task 23: ボリュームサービス（デバイス検出とプロファイル判定の永続化）

**Files:**
- Create: `app/src/mediaferry/db/sources.py`
- Create: `app/src/mediaferry/core/manifest.py`
- Create: `app/src/mediaferry/jobs/volumes.py`
- Modify: `app/src/mediaferry/adapters/broker_client.py`（呼び出しを直列化する）
- Modify: `app/src/mediaferry/adapters/fs.py`（`exists_beneath` を追加）
- Modify: `app/tests/conftest.py`（fake ブローカーのフィクスチャを追加）
- Test: `app/tests/test_volume_service.py`

**Interfaces:**
- Consumes: `BrokerClient`（既存）、`DirfdTree`（Task 15）、`resolve_profile`（Task 10）、
  `ProfileRegistry`（Task 13）
- Produces:
  - `mediaferry.core.manifest.content_manifest_digest(names: Iterable[str]) -> str`
  - `mediaferry.adapters.fs.exists_beneath(dirfd, rel_path) -> bool`
  - `mediaferry.db.sources.upsert_device` / `resolve_volume_instance` / `sync_presence` /
    `detach_absent`
  - `mediaferry.jobs.volumes.VolumeObservation(broker_epoch, generation, volume_key,
    major, minor, fs_uuid)` — `.of(volume)`
  - `mediaferry.jobs.volumes.VolumeSelection(volume_instance_id, presence_id, observation,
    profile_id, profile_revision_id)` — `.to_params()` / `.from_params()`
  - `mediaferry.jobs.volumes.VolumeView(volume_instance_id, volume_key, fs_label, size_bytes,
    profile_slug, identity_confidence, provisional, trusted, reason, selection)`
  - `VolumeService(conn, registry, client)` — `.refresh() -> list[VolumeView]`,
    `.selection_for(volume_instance_id) -> VolumeSelection`,
    `.open(selection) -> VolumeHandle`, `.release(selection)`,
    `.close(volume_instance_id)`, `.trust(volume_instance_id)`, `.close_all()`,
    `.opened() -> list[str]`
  - 例外 `StaleSelection` / `VolumeBusy`

**この 3 つを Phase 1 で確定させる。** どれも「polling で足りる」の判断とは
独立に必要で、後から入れると呼び出し側を全部書き直すことになる。

1. **ジョブは選択した瞬間の presence を持つ。** `volume_instance_id` だけを
   渡して実行時に「最新の presence」を選ぶと、抜き差しでカードが入れ替わって
   いても、そのカードの現在値から正しい `expect` を組み立ててしまい、
   **ブローカー側の TOCTOU 検証をすり抜ける**（§9.2）。
2. **ブローカーの呼び出しを直列化する。** `BrokerClient` は 1 本の
   `SOCK_SEQPACKET` で要求と応答を対応付けるので、API のスレッドとワーカーの
   スレッドが同時に使うと応答を取り違える。
3. **プロファイルの一致度とボリュームの同定確度を混ぜない。**
   `identity_confidence`（§8）は「前回と同じカードだと言えるか」であって、
   「中身がプロファイルに何件一致したか」ではない。混ぜると、同じ UUID・
   同じ容量の別カードが DJI のファイルを持っているだけで `high` になり、
   前のカードの `trusted_at` を引き継ぐ。

**`VolumeService` は長寿命で、自分専用の DB 接続を持つ。** その接続は他の
どのコンポーネントとも共有しない。DB を触るメソッドはすべて自分のロックの
中で実行するので、複数のリクエストスレッドから呼ばれても、他のコンポーネントの
トランザクションに巻き込まれることも、巻き込むこともない。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/conftest.py` に追記:

```python
import os
import socket
import threading

from mediaferry.adapters.broker_client import BrokerClient
from mediaferry_protocol.messages import UsbInfo, VolumeInfo
from mountd.server import BrokerServer


class FakeMountManager:
    """マウントはせず、用意したディレクトリの dirfd を返す.

    プロトコルは実物の BrokerServer が話すので、取り違えは見逃さない。
    """

    def __init__(self, target):
        self.target = target
        self._open = {}
        self._n = 0

    def mount(self, volume, expect, verify):
        self._n += 1
        handle = f"h{self._n}"
        verify()
        self._open[handle] = os.open(self.target, os.O_RDONLY | os.O_DIRECTORY)
        return handle, self._open[handle]

    def release(self, handle):
        fd = self._open.pop(handle, None)
        if fd is not None:
            os.close(fd)

    def release_all(self):
        for handle in list(self._open):
            self.release(handle)


@pytest.fixture
def fake_card(tmp_path):
    card = tmp_path / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").write_bytes(b"a" * 100)
    return card


@pytest.fixture
def mount_manager(fake_card):
    """`target` を差し替えると、以後の open だけが新しい中身を見る.

    既に渡した dirfd は古いディレクトリを指したままになるので、
    「カードが差し替わったのに古い fd を使い回す」経路を再現できる。
    """
    return FakeMountManager(fake_card)


@pytest.fixture
def volumes():
    """broker が列挙するボリューム.

    テストはこのリストを書き換えて抜き差しを表す。**クライアント側だけを
    差し替えてはいけない**。サーバは自分の lister で volume_key を引くので、
    知らないキーを開こうとして `unknown_volume` になる（実機では起きない状態）。
    """
    return [
        VolumeInfo(
            volume_key="8:160",
        device_node="/dev/sdk",
        major=8,
        minor=160,
        sysfs_path="/sys/x",
        fs_type="exfat",
        fs_uuid="26B1-2FD6",
        fs_label="SD_Card",
        size_bytes=512_000_000_000,
        usb=UsbInfo(
            vendor_id="2ca3",
            product_id="0020",
            product="OsmoPocket4-ABC123",
            serial="123456789ABCDEF",
        ),
            broker_epoch="",
            generation=1,
        )
    ]


@pytest.fixture
def broker(mount_manager, tmp_path, volumes):
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        mount_manager=mount_manager,
        lister=lambda: list(volumes),
        allowed_uids=None,
    )
    client_sock, server_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    thread = threading.Thread(target=server.handle_connection, args=(server_sock,), daemon=True)
    thread.start()
    client = BrokerClient.from_socket(client_sock)
    yield client
    client.close()
    thread.join(timeout=5)
```

`app/tests/test_volume_service.py`:

```python
import threading
from dataclasses import replace

import pytest

from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.volumes import StaleSelection, VolumeBusy, VolumeService


def service(db, broker):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    return VolumeService(db, registry, broker)


def test_refresh_registers_the_device_the_volume_and_the_presence(db, broker):
    views = service(db, broker).refresh()
    assert len(views) == 1
    assert db.execute("SELECT count(*) FROM source_device").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM volume_instance").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM volume_presence").fetchone()[0] == 1


def test_the_profile_is_resolved_from_the_contents(db, broker):
    view = service(db, broker).refresh()[0]
    assert view.profile_slug == "dji-osmo"
    assert view.provisional is False


def test_an_empty_card_is_provisional(db, broker, fake_card):
    for path in (fake_card / "DCIM" / "DJI_001").iterdir():
        path.unlink()
    view = service(db, broker).refresh()[0]
    assert view.profile_slug == "dji-osmo"
    assert view.provisional is True


def test_a_first_sighting_is_never_high_confidence(db, broker):
    """初めて見るカードは §12.1 のとおり必ず承認を待つ."""
    view = service(db, broker).refresh()[0]
    assert view.identity_confidence == "low"
    assert view.trusted is False


def test_a_returning_card_with_a_matching_manifest_becomes_high(db, broker):
    svc = service(db, broker)
    svc.refresh()
    assert svc.refresh()[0].identity_confidence == "high"


def test_without_a_remembered_manifest_survival_alone_is_not_enough(db, broker):
    """記憶が無いカードは、既知ファイルが残っていても high にしない（§12.1）.

    残存率の判定に落ちると、manifest を一度も記録していない相手に対して
    「前回と連続的だ」と主張することになる。
    """
    svc = service(db, broker)
    view = svc.refresh()[0]
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES ('e1', ?, 'DCIM/DJI_001/DJI_20260817143000_0001_D.MP4', 100, 1, 'abc', 1,"
        " 'published', '2026-01-01T00:00:00+00:00')",
        (view.volume_instance_id,),
    )
    db.execute("UPDATE volume_instance SET content_manifest_digest = NULL")
    assert svc.refresh()[0].identity_confidence == "low"


def test_devices_that_differ_only_by_product_are_not_merged(db, broker, volumes):
    """Osmo の serial は機種の既定値なので、product を落とすと 2 台が 1 台になる."""
    first = volumes[0]
    volumes.append(
        replace(
            first,
            volume_key="8:176",
            major=8,
            minor=176,
            fs_uuid="AAAA-BBBB",
            usb=replace(first.usb, product="OsmoPocket4-XYZ789"),
        )
    )
    service(db, broker).refresh()
    assert db.execute("SELECT count(*) FROM source_device").fetchone()[0] == 2


def test_a_reformatted_card_drops_back_to_low(db, broker, fake_card):
    """UUID を保持したまま中身が入れ替わったカードを high のままにしない."""
    svc = service(db, broker)
    svc.refresh()
    svc.refresh()
    (fake_card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()
    (fake_card / "DCIM" / "DJI_001").rmdir()
    (fake_card / "DCIM" / "OTHER").mkdir()
    assert svc.refresh()[0].identity_confidence == "low"


def test_a_card_without_a_uuid_is_always_low(db, broker, volumes):
    volumes[0] = replace(volumes[0], fs_uuid="")
    svc = service(db, broker)
    svc.refresh()
    assert svc.refresh()[0].identity_confidence == "low"


def test_profile_match_does_not_raise_identity_confidence(db, broker, fake_card):
    """中身が DJI のファイルであることは「同じカードだ」の証明にならない."""
    view = service(db, broker).refresh()[0]
    assert view.profile_slug == "dji-osmo"
    assert view.provisional is False
    assert view.identity_confidence == "low"  # 初回なので low のまま


def test_the_volume_is_closed_after_probing(db, broker):
    """対象を確かめたら dirfd を握り続けない. 明示的に開くまでは閉じておく."""
    svc = service(db, broker)
    svc.refresh()
    assert svc.opened() == []


def test_open_and_release_manage_the_dirfd(db, broker):
    """release でその場で閉じる. 次のジョブのために取っておかない."""
    import os

    svc = service(db, broker)
    view = svc.refresh()[0]
    handle = svc.open(view.selection)
    assert svc.opened() == [view.volume_instance_id]
    assert "DCIM" in os.listdir(handle.dirfd)
    svc.release(view.selection)
    assert svc.opened() == []
    # os.listdir(-1) はカレントディレクトリを黙って返すので、閉じたことは
    # 契約（closed と dirfd の無効化）で確かめる。
    assert handle.closed is True
    assert handle.dirfd == -1


def test_opening_the_same_volume_twice_is_refused(db, broker):
    """observation は媒体の同一性を保証しないので、黙って共有しない."""
    svc = service(db, broker)
    selection = svc.refresh()[0].selection
    svc.open(selection)
    with pytest.raises(VolumeBusy):
        svc.open(selection)
    svc.release(selection)


def test_a_selection_from_an_older_generation_is_refused(db, broker):
    """抜き差しで /dev/sdX が再利用され、別のカードが同じノードに来る."""
    svc = service(db, broker)
    view = svc.refresh()[0]
    observation = replace(
        view.selection.observation, generation=view.selection.observation.generation - 1
    )
    with pytest.raises(StaleSelection):
        svc.open(replace(view.selection, observation=observation))


def test_a_selection_from_a_previous_mountd_run_is_refused(db, broker):
    """generation は mountd の再起動で 0 に戻る. epoch が無いと偶然一致する."""
    svc = service(db, broker)
    view = svc.refresh()[0]
    observation = replace(view.selection.observation, broker_epoch="a-previous-run")
    with pytest.raises(StaleSelection):
        svc.open(replace(view.selection, observation=observation))


def test_closing_a_volume_a_job_is_using_is_refused(db, broker):
    """実行中のワーカーの fd を、API の別スレッドから閉じない."""
    svc = service(db, broker)
    view = svc.refresh()[0]
    svc.open(view.selection)
    with pytest.raises(VolumeBusy):
        svc.close(view.volume_instance_id)
    svc.release(view.selection)
    svc.close(view.volume_instance_id)


def test_the_broker_is_not_called_concurrently(db, broker):
    """1 本の SOCK_SEQPACKET を同時に使うと応答を取り違える."""
    svc = service(db, broker)
    svc.refresh()
    errors = []

    def hammer():
        try:
            for _ in range(20):
                svc.refresh()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []


def test_the_same_card_keeps_its_identity_and_presence_across_refreshes(db, broker):
    """列挙のたびに presence を増やすと、キュー投入時と実行時で別物になる."""
    svc = service(db, broker)
    first = svc.refresh()[0]
    second = svc.refresh()[0]
    assert first.volume_instance_id == second.volume_instance_id
    assert first.selection == second.selection
    assert db.execute("SELECT count(*) FROM volume_presence").fetchone()[0] == 1


def test_a_selection_survives_intervening_refreshes(db, broker):
    """GET /devices → scan → import の間に何度 refresh が挟まっても開ける."""
    svc = service(db, broker)
    selection = svc.selection_for(svc.refresh()[0].volume_instance_id)
    svc.refresh()
    svc.refresh()
    handle = svc.open(selection)
    assert "DCIM" in __import__("os").listdir(handle.dirfd)
    svc.release(selection)


def test_a_vanished_presence_is_detached(db, broker, volumes):
    """抜いたポートの行が live のままだと、同一 identity の同時接続を誤検出する."""
    svc = service(db, broker)
    svc.refresh()
    volumes.clear()
    svc.refresh()
    rows = db.execute("SELECT count(*) AS n FROM volume_presence WHERE detached_at IS NULL")
    assert rows.fetchone()["n"] == 0


def test_reinserting_into_another_port_does_not_pin_confidence_low(db, broker, volumes):
    """抜いて別ポートへ挿し直したカードが、以後ずっと low のままにならない."""
    svc = service(db, broker)
    svc.refresh()
    svc.refresh()
    volumes[0] = replace(
        volumes[0],
        major=8,
        minor=176,
        volume_key="8:176",
        generation=volumes[0].generation + 1,
    )
    assert svc.refresh()[0].identity_confidence == "high"


def test_no_handle_survives_a_finished_job(db, broker):
    """使い終わった handle が残っていると、次のジョブがそれを掴む."""
    svc = service(db, broker)
    selection = svc.refresh()[0].selection
    svc.open(selection)
    svc.release(selection)
    svc.refresh()
    assert svc.opened() == []


def test_two_cards_with_the_same_identity_are_both_low_on_the_first_sighting(db, broker, volumes):
    """判定を live 集合の確定より前に行うと、先に見た方だけが high になる.

    先に 1 本だけで high を作っておき、**2 本目が現れた最初の refresh** を見る。
    最初から 2 本を返すと、初回は remembered digest が無くて両方 low になり、
    反映しながら判定する実装でも通ってしまう。
    """
    svc = service(db, broker)
    svc.refresh()
    assert svc.refresh()[0].identity_confidence == "high"

    volumes.append(replace(volumes[0], major=8, minor=176, volume_key="8:176"))
    views = svc.refresh()
    assert len(views) == 2
    assert [view.identity_confidence for view in views] == ["low", "low"]


def a_swapped_card(tmp_path):
    """同じ UUID・容量だが中身の違うカード（複製・再フォーマット相当）."""
    other = tmp_path / "swapped"
    (other / "DCIM" / "100CANON").mkdir(parents=True)
    (other / "DCIM" / "100CANON" / "IMG_0001.JPG").write_bytes(b"other camera")
    return other


def test_a_swapped_card_is_judged_on_its_own_contents(
    db, broker, mount_manager, tmp_path
):
    """observation が完全一致でも、開いてある dirfd を使い回してはいけない.

    mountd の generation は「観測した集合の指紋が変わったとき」だけ進む
    (mountd/server.py::_observe)。Phase 1 は polling なので、同じ UUID・型・
    容量のカードが同じ major:minor で観測の合間に差し替わると、generation も
    epoch も据え置きのままになる。既存 fd は open_tree で切り離した旧カードを
    指したままなので、流用すると旧カードの中身で新カードを判定する。
    """
    import os

    svc = service(db, broker)
    selection = svc.refresh()[0].selection
    handle = svc.open(selection)
    svc.release(selection)

    # ジョブが終われば handle はその場で閉じる。取っておくと、次のジョブが
    # 差し替え後もこの fd（＝旧カード）を読むことになる。
    assert handle.closed is True

    # 新しく open するものだけが差し替わる（世代も epoch も据え置き）。
    mount_manager.target = a_swapped_card(tmp_path)

    # ビルトインは dji-osmo だけなので hints は一致し続ける。旧 dirfd を
    # 流用していれば DJI のファイルが見えて確定 (provisional False) になる。
    # 新カードを見ていれば DCIM はあるが要件を満たさず provisional になる。
    view = svc.refresh()[0]
    assert view.provisional is True
    assert view.identity_confidence == "low"

    # 判定だけでなく、次に開く dirfd も新しいカードでなければならない。
    # 画面には新カードが見えるのに取り込むのは旧カード、が最悪の食い違い。
    current = svc.open(view.selection)
    dcim = os.open("DCIM", os.O_RDONLY | os.O_DIRECTORY, dir_fd=current.dirfd)
    try:
        assert "100CANON" in os.listdir(dcim)
        assert "DJI_001" not in os.listdir(dcim)
    finally:
        os.close(dcim)
        svc.release(view.selection)


def test_a_card_without_a_uuid_keeps_its_selection_across_refreshes(db, broker, volumes):
    """毎 refresh で新しい volume_instance を作ると、直前に選んだ selection が
    次の refresh で detached になる."""
    volumes[0] = replace(volumes[0], fs_uuid="")
    svc = service(db, broker)
    first = svc.refresh()[0]
    second = svc.refresh()[0]
    assert first.volume_instance_id == second.volume_instance_id
    assert first.selection == second.selection
    assert db.execute("SELECT count(*) FROM volume_instance").fetchone()[0] == 1
    handle = svc.open(first.selection)
    assert "DCIM" in __import__("os").listdir(handle.dirfd)
    svc.release(first.selection)


def test_trust_is_recorded_and_reported(db, broker):
    svc = service(db, broker)
    view = svc.refresh()[0]
    assert view.trusted is False
    svc.trust(view.volume_instance_id)
    assert svc.refresh()[0].trusted is True


def test_the_serial_alone_does_not_identify_the_device(db, broker):
    """Osmo の serial は Linux ガジェットの既定値だった（Phase 0 実測）."""
    svc = service(db, broker)
    svc.refresh()
    row = db.execute("SELECT * FROM source_device").fetchone()
    assert row["serial"] == "123456789ABCDEF"
    assert row["usb_product_id"] == "0020"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_volume_service.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 実装する**

`app/src/mediaferry/db/sources.py`:

```python
"""ソース側のレコードの upsert.

デバイスの同定は (vendor, product_id, product, serial) の組で行う。serial 単独は
機種の既定値でありうるので識別子にしない。ボリュームは (fs_uuid, fs_type,
size_bytes) で引くが、これは識別子ではなく推測である。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ..clock import now_iso
from ..ids import new_id


def upsert_device(conn: sqlite3.Connection, usb) -> str | None:  # noqa: ANN001
    if usb is None:
        return None
    key = (usb.vendor_id, usb.product_id, usb.product or "", usb.serial or "")
    row = conn.execute(
        "SELECT id FROM source_device WHERE usb_vendor_id = ? AND usb_product_id = ?"
        " AND usb_product = ? AND serial = ?",
        key,
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE source_device SET last_seen_at = ? WHERE id = ?", (now_iso(), row["id"])
        )
        return row["id"]
    device_id = new_id()
    conn.execute(
        "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (device_id, *key, now_iso(), now_iso()),
    )
    return device_id


def resolve_volume_instance(conn: sqlite3.Connection, volume, device_id: str | None) -> str:  # noqa: ANN001
    """観測したボリュームを既存の行に結び付ける. 無ければ作る.

    UUID があれば `(fs_uuid, fs_type, size_bytes)` で引く（これは識別子では
    なく推測なので、確度は別に判定する）。

    **UUID が無いときは、同じ接続がまだ live かどうかで引く。** 毎回新しい行を
    作ると、同じカードが挿さったままでも refresh のたびに `volume_instance` と
    `presence` が変わり、直前に画面で選んだ selection が次の refresh で
    detached になる。

    ただし世代が変われば同定は継承しない。UUID が無い以上「同じカードだ」と
    言えないので、抜き差しをまたぐ継承は誤同定になる。
    """
    row = None
    if volume.fs_uuid:
        row = conn.execute(
            "SELECT id FROM volume_instance WHERE fs_uuid = ? AND fs_type = ? AND size_bytes = ?",
            (volume.fs_uuid, volume.fs_type or "", volume.size_bytes),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT v.id AS id FROM volume_instance v"
            " JOIN volume_presence p ON p.volume_instance_id = v.id"
            " WHERE v.fs_uuid = '' AND p.detached_at IS NULL AND p.broker_epoch = ?"
            " AND p.generation = ? AND p.major = ? AND p.minor = ? LIMIT 1",
            (volume.broker_epoch, volume.generation, volume.major, volume.minor),
        ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE volume_instance SET last_seen_at = ?, last_source_device_id = ?,"
            " fs_label = ? WHERE id = ?",
            (now_iso(), device_id, volume.fs_label or "", row["id"]),
        )
        return row["id"]
    volume_id = new_id()
    conn.execute(
        "INSERT INTO volume_instance (id, fs_uuid, fs_type, fs_label, size_bytes,"
        " identity_confidence, last_source_device_id, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'low', ?, ?, ?)",
        (
            volume_id,
            volume.fs_uuid or "",
            volume.fs_type or "",
            volume.fs_label or "",
            volume.size_bytes,
            device_id,
            now_iso(),
            now_iso(),
        ),
    )
    return volume_id


def sync_presence(conn: sqlite3.Connection, volume_instance_id: str, volume) -> str:  # noqa: ANN001
    """観測した接続を 1 行に対応させる. 列挙のたびに増やさない.

    増やすと、キューに積んだときの `presence_id` と実行時のそれが別物になり、
    同じカードが挿さったままでも `StaleSelection` になる。
    """
    key = (volume_instance_id, volume.broker_epoch, volume.generation, volume.major, volume.minor)
    row = conn.execute(
        "SELECT id FROM volume_presence WHERE volume_instance_id = ? AND broker_epoch = ?"
        " AND generation = ? AND major = ? AND minor = ?",
        key,
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE volume_presence SET detached_at = NULL, device_node = ?, sysfs_path = ?"
            " WHERE id = ?",
            (volume.device_node, volume.sysfs_path, row["id"]),
        )
        return row["id"]
    presence_id = new_id()
    conn.execute(
        "INSERT INTO volume_presence (id, volume_instance_id, broker_epoch, generation,"
        " device_node, major, minor, sysfs_path, attached_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            presence_id,
            volume_instance_id,
            volume.broker_epoch,
            volume.generation,
            volume.device_node,
            volume.major,
            volume.minor,
            volume.sysfs_path,
            now_iso(),
        ),
    )
    return presence_id


def detach_absent(conn: sqlite3.Connection, seen_presence_ids: Sequence[str]) -> int:
    """今回の観測に無い live な接続に detached_at を立てる.

    立てないと、抜いたポートの行が永久に live のままになり、
    「同一 identity の同時接続」を誤検出して確度が上がらなくなる。
    """
    placeholders = ",".join("?" * len(seen_presence_ids))
    condition = f" AND id NOT IN ({placeholders})" if seen_presence_ids else ""
    cursor = conn.execute(
        "UPDATE volume_presence SET detached_at = ?"  # noqa: S608
        f" WHERE detached_at IS NULL{condition}",
        (now_iso(), *seen_presence_ids),
    )
    return cursor.rowcount
```

`app/src/mediaferry/jobs/volumes.py`:

```python
"""接続中ボリュームの列挙とプロファイル判定（§9.2）.

判定はボリュームごとに行う。デバイス単位ではない。記憶したプロファイルは
候補として使うが、require は必ず再検証する。記憶を無条件に信用しない。

判定のためだけに開いた dirfd は、確かめたらすぐ閉じる。取り込みのために
開くのは別の操作にする。
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from dataclasses import asdict, dataclass, fields

from ..adapters.broker_client import BrokerClient, VolumeHandle
from ..adapters.fs import DirfdTree, exists_beneath
from ..clock import now_iso
from ..core.manifest import content_manifest_digest
from ..core.profiles.matching import VolumeFacts, resolve_profile
from ..db.profiles import ProfileRegistry
from ..db.sources import (
    detach_absent,
    resolve_volume_instance,
    sync_presence,
    upsert_device,
)

# manifest に含める名前の上限。数万件のカードで全件読まない。
MANIFEST_LIMIT = 500
# 既知ファイルの残存率をどれだけ標本し、どこから「連続的」と見なすか。
SURVIVAL_SAMPLE = 50
SURVIVAL_THRESHOLD = 0.5


class StaleSelection(RuntimeError):
    """選択した時点のボリュームが、もうそこに無い."""


class VolumeBusy(RuntimeError):
    """実行中のジョブが掴んでいる."""


@dataclass(frozen=True)
class VolumeObservation:
    """今この瞬間に観測した「接続」の同一性.

    キューに積んだ操作が、選んだ時点と同じ接続に対して実行されることを
    確かめるために使う。

    **これは「接続」の同一性であって「媒体」の同一性ではない。** mountd の
    `generation` は観測した集合の指紋が変わったときだけ進むので、同じ UUID・
    型・容量のカードが観測の合間に同じノードで差し替わると据え置きになる。
    したがって、**開いてある dirfd を使い回してよい根拠にはできない**。
    """

    broker_epoch: str
    generation: int
    volume_key: str
    major: int
    minor: int
    fs_uuid: str

    @classmethod
    def of(cls, volume) -> VolumeObservation:  # noqa: ANN001
        return cls(
            broker_epoch=volume.broker_epoch,
            generation=volume.generation,
            volume_key=volume.volume_key,
            major=volume.major,
            minor=volume.minor,
            fs_uuid=volume.fs_uuid or "",
        )


@dataclass(frozen=True)
class VolumeSelection:
    """「この操作はこのボリュームのこの接続に対して行う」という固定."""

    volume_instance_id: str
    presence_id: str
    observation: VolumeObservation
    profile_id: str
    profile_revision_id: str

    def to_params(self) -> dict:
        params = asdict(self.observation)
        params.update(
            volume_instance_id=self.volume_instance_id,
            presence_id=self.presence_id,
            profile_id=self.profile_id,
            profile_revision_id=self.profile_revision_id,
        )
        return params

    @classmethod
    def from_params(cls, params: dict) -> VolumeSelection:
        return cls(
            volume_instance_id=params["volume_instance_id"],
            presence_id=params["presence_id"],
            observation=VolumeObservation(
                **{f.name: params[f.name] for f in fields(VolumeObservation)}
            ),
            profile_id=params["profile_id"],
            profile_revision_id=params["profile_revision_id"],
        )


@dataclass(frozen=True)
class VolumeView:
    volume_instance_id: str
    volume_key: str
    fs_label: str
    size_bytes: int
    profile_slug: str | None
    # §8 の「前回と同じカードだと言えるか」。プロファイルの一致度ではない。
    identity_confidence: str
    provisional: bool
    trusted: bool
    reason: str
    selection: VolumeSelection | None


class VolumeService:
    def __init__(
        self, conn: sqlite3.Connection, registry: ProfileRegistry, client: BrokerClient
    ) -> None:
        self._conn = conn
        self._registry = registry
        self._client = client
        # 実行中のジョブが掴んでいる handle。ジョブが終われば閉じる。
        self._open: dict[str, VolumeHandle] = {}
        self._lock = threading.RLock()

    def refresh(self) -> list[VolumeView]:
        """今この場にあるものを 1 つのスナップショットとして DB に反映する.

        **判定（probe）は、live 集合が確定してから行う。** 各ボリュームを
        「反映しながら判定」すると、別ポートへ挿し直した直後の refresh では
        旧 presence がまだ live なので「同一 identity の同時接続」と誤判定して
        確度が上がらないし、同じ identity の 2 枚を初めて同時に列挙したときは
        先に判定した方だけが high になる。
        """
        with self._lock:
            # スナップショットは 1 回だけ取る。pass ごとに取り直すと、
            # その間の抜き差しで pass の対象がずれる。
            volumes = self._client.list_volumes()

            # pass 1: 観測を DB へ反映する
            observed = []
            seen_presence: list[str] = []
            for volume in volumes:
                device_id = upsert_device(self._conn, volume.usb)
                volume_id = resolve_volume_instance(self._conn, volume, device_id)
                presence_id = sync_presence(self._conn, volume_id, volume)
                seen_presence.append(presence_id)
                observed.append((volume, volume_id, presence_id))

            # pass 2: 消えた接続を detach する
            detach_absent(self._conn, seen_presence)

            # pass 3: 確定した live 集合を使って判定する
            definitions = [ref.definition for ref in self._registry.active()]
            return [
                self._probe(volume, volume_id, presence_id, definitions)
                for volume, volume_id, presence_id in observed
            ]

    def _probe(self, volume, volume_id, presence_id, definitions) -> VolumeView:  # noqa: ANN001
        remembered = self._conn.execute(
            "SELECT p.slug AS slug, v.trusted_at AS trusted_at,"
            " v.content_manifest_digest AS digest FROM volume_instance v"
            " LEFT JOIN device_profile p ON p.id = v.profile_id WHERE v.id = ?",
            (volume_id,),
        ).fetchone()
        facts = VolumeFacts(
            usb_vendor_id=volume.usb.vendor_id if volume.usb else "",
            usb_product_id=volume.usb.product_id if volume.usb else "",
            fs_label=volume.fs_label or "",
        )
        # **判定は必ず開き直す。開いてある handle を流用しない。**
        #
        # mountd の `generation` は uevent の数ではなく、観測した集合の
        # `(volume_key, fs_uuid, fs_type, size_bytes)` が前回と変わったときだけ
        # 進む（`mountd/server.py::_observe`）。Phase 1 は uevent を購読せず
        # polling なので、同じ UUID・型・容量のカードが同じ major:minor で
        # 観測の合間に差し替わると、**generation も epoch も据え置きのまま**に
        # なる。既存の dirfd は `open_tree` で切り離した旧カードを指したままな
        # ので、流用すると旧カードの中身で新カードを判定することになる。
        # 複製カードだけでなく、UUID を保持した再フォーマットも同じ。
        #
        # 代償は「GET /devices のたびに mount / umount が走る」こと。Phase 1 は
        # 手動操作しか無いので許容する。避けたければ mountd 側に uevent を
        # 取りこぼさない incarnation を持たせて handle と一覧の両方へ刻印する
        # 必要があり、それは Phase 1 の範囲を超える。
        observation = VolumeObservation.of(volume)
        handle = self._client.open_volume(volume)
        try:
            tree = DirfdTree(handle.dirfd)
            outcome = resolve_profile(definitions, facts, tree, remembered["slug"])
            digest = self._manifest_of(handle.dirfd, tree, outcome, definitions)
            confidence = self._identity_confidence(volume, volume_id, remembered, digest, handle)
        finally:
            with contextlib.suppress(Exception):
                self._client.close_volume(handle)

        profile_id = revision_id = None
        if outcome.slug is not None:
            ref = self._registry.current(outcome.slug)
            profile_id, revision_id = ref.profile_id, ref.revision_id
        self._conn.execute(
            "UPDATE volume_instance SET profile_id = ?, profile_revision_id = ?,"
            " identity_confidence = ?, content_manifest_digest = ?, last_seen_at = ?"
            " WHERE id = ?",
            (profile_id, revision_id, confidence, digest, now_iso(), volume_id),
        )
        selection = None
        if profile_id is not None:
            selection = VolumeSelection(
                volume_instance_id=volume_id,
                presence_id=presence_id,
                observation=observation,
                profile_id=profile_id,
                profile_revision_id=revision_id,
            )
        return VolumeView(
            volume_instance_id=volume_id,
            volume_key=volume.volume_key,
            fs_label=volume.fs_label or "",
            size_bytes=volume.size_bytes,
            profile_slug=outcome.slug,
            identity_confidence=confidence,
            provisional=outcome.provisional,
            trusted=remembered["trusted_at"] is not None,
            reason=outcome.reason,
            selection=selection,
        )

    def _manifest_of(self, dirfd, tree, outcome, definitions) -> str:  # noqa: ANN001
        roots = ("DCIM",)
        if outcome.slug is not None:
            roots = next(d.scan.roots for d in definitions if d.slug == outcome.slug)
        names = []
        for root in roots:
            names.extend(f"{root}/{name}" for name in tree.iter_names(root, MANIFEST_LIMIT))
        return content_manifest_digest(names)

    def _identity_confidence(self, volume, volume_id, remembered, digest, handle) -> str:  # noqa: ANN001
        """§8 の確度. **プロファイルの一致度とは無関係**.

        `high` にできるのは「前回と連続的だ」と言えるときだけ。read-only で
        扱う以上ボリュームに永続マーカーを書けないので、これは推測である
        （§12.1 に限界を明示する）。
        """
        if not volume.fs_uuid:
            return "low"
        if self._has_other_live_presence(volume_id, volume):
            return "low"
        if remembered["digest"] is None:
            # 初めて見るカードは §12.1 のとおり必ず承認を待つ。
            return "low"
        if remembered["digest"] == digest:
            return "high"
        return "high" if self._known_files_survive(volume_id, handle) else "low"

    def _has_other_live_presence(self, volume_id: str, volume) -> bool:  # noqa: ANN001
        row = self._conn.execute(
            "SELECT count(*) AS n FROM volume_presence WHERE volume_instance_id = ?"
            " AND detached_at IS NULL AND (major <> ? OR minor <> ?)",
            (volume_id, volume.major, volume.minor),
        ).fetchone()
        return row["n"] > 0

    def _known_files_survive(self, volume_id: str, handle) -> bool:  # noqa: ANN001
        rows = list(
            self._conn.execute(
                "SELECT rel_path FROM source_entry WHERE volume_instance_id = ?"
                " AND state = 'published' LIMIT ?",
                (volume_id, SURVIVAL_SAMPLE),
            )
        )
        if not rows:
            return False
        alive = sum(1 for row in rows if exists_beneath(handle.dirfd, row["rel_path"]))
        return alive / len(rows) >= SURVIVAL_THRESHOLD

    # ------------------------------------------------------------------
    def selection_for(self, volume_instance_id: str) -> VolumeSelection:
        matches = [
            view.selection
            for view in self.refresh()
            if view.volume_instance_id == volume_instance_id and view.selection is not None
        ]
        if not matches:
            raise StaleSelection(f"ボリューム {volume_instance_id} は今この場に無い")
        if len(matches) > 1:
            # 同じ identity のカードが 2 枚同時に挿さっている。どちらを指した
            # のか決められないので、勝手に選ばない（§8 の presence 分離の趣旨）。
            raise StaleSelection(
                "同じ識別子のボリュームが複数接続されている。どれを操作するか決められない"
            )
        return matches[0]

    def open(self, selection: VolumeSelection) -> VolumeHandle:
        """選択した瞬間の接続と同じものだけを開く.

        `volume_instance_id` だけで開き直すと、抜き差しで別のカードが同じ
        ノードに来ていても、その現在値から正しい expect を作ってしまい、
        ブローカーの検証をすり抜ける。

        **開いた handle をジョブ間でキャッシュしない。** `VolumeObservation` は
        物理媒体の同一性を保証しないので（`_probe` のコメント参照）、次の
        ジョブへ使い回すと「判定は新しいカード、読むのは古いカードの
        detached clone」という食い違いが起きる。単一ワーカーなので開き直す
        コストも実質かからない。
        """
        with self._lock:
            if selection.volume_instance_id in self._open:
                # 単一ワーカーなのでここへは来ない。来たら契約違反なので、
                # 同じ媒体である保証が無いまま共有せずに知らせる。
                raise VolumeBusy("このボリュームは既に開かれている")
            volume = self._match_selection(selection)
            handle = self._client.open_volume(volume)
            self._open[selection.volume_instance_id] = handle
            return handle

    def _match_selection(self, selection: VolumeSelection):  # noqa: ANN202
        presence = self._conn.execute(
            "SELECT detached_at FROM volume_presence WHERE id = ?", (selection.presence_id,)
        ).fetchone()
        if presence is None or presence["detached_at"] is not None:
            raise StaleSelection("選択した接続はもう存在しない")
        for volume in self._client.list_volumes():
            if VolumeObservation.of(volume) == selection.observation:
                return volume
        raise StaleSelection("選択した時点のボリュームが見つからない（抜き差しされた）")

    def release(self, selection: VolumeSelection) -> None:
        """ジョブが使い終わった handle を閉じる.

        次のジョブのために取っておかない。取っておくと、同じ observation の
        まま媒体が差し替わったときに古い dirfd を渡すことになる。
        """
        with self._lock:
            handle = self._open.pop(selection.volume_instance_id, None)
            if handle is not None:
                with contextlib.suppress(Exception):
                    self._client.close_volume(handle)

    def close(self, volume_instance_id: str) -> None:
        """画面からの取り外し操作. 実行中のジョブが掴んでいれば拒否する.

        ジョブが終われば `release` で閉じているので、通常は何もすることが無い。
        """
        with self._lock:
            if volume_instance_id in self._open:
                raise VolumeBusy("実行中のジョブがこのボリュームを使っている")

    def close_all(self) -> None:
        with self._lock:
            for volume_instance_id in list(self._open):
                handle = self._open.pop(volume_instance_id)
                with contextlib.suppress(Exception):
                    self._client.close_volume(handle)

    def opened(self) -> list[str]:
        return sorted(self._open)

    def trust(self, volume_instance_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE volume_instance SET trusted_at = ? WHERE id = ?",
                (now_iso(), volume_instance_id),
            )


```

`app/src/mediaferry/core/manifest.py`:

```python
"""ボリュームの中身の軽い要約.

「前回と同じカードか」を推測するために使う。フォーマット直後や別カードへの
差し替えを検出することが目的で、完全な保証ではない（§8）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

DOMAIN = b"mfm"
VERSION = 1


def content_manifest_digest(names: Iterable[str]) -> str:
    """名前の集合から決定的なダイジェストを作る.

    順序に依存しないよう並べ替える。ディレクトリを走査する順序は
    ファイルシステムによって変わる。
    """
    digest = hashlib.sha256()
    digest.update(DOMAIN)
    digest.update(bytes([VERSION]))
    for name in sorted(set(names)):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
```

`app/src/mediaferry/adapters/fs.py` に追記:

```python
def exists_beneath(dirfd: int, rel_path: str) -> bool:
    """dirfd の下にそのパスがあるか. 開けるかどうかで判定する."""
    try:
        fd = open_beneath(dirfd, rel_path)
    except (OSError, EscapeAttempt):
        return False
    os.close(fd)
    return True
```

`app/src/mediaferry/adapters/broker_client.py` を直列化する。Phase 0 で
**`BrokerClient` は thread-safe ではない**と確定している。1 本の
`SOCK_SEQPACKET` で要求と応答を対応付けるので、同時に使うと別の要求の応答を
受け取る（fd を含む応答なら、別のボリュームの dirfd を掴む）。

```python
class BrokerClient:
    def __init__(self, socket_path: Path) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._sock.connect(str(socket_path))
        self._handles: dict[str, VolumeHandle] = {}
        # 要求と応答は 1 本のソケットで対応付ける。API のスレッドと
        # ワーカーのスレッドが同時に使うと、応答を取り違える。
        self._lock = threading.Lock()

    def _call(self, payload: dict, expect_fd: bool = False) -> tuple[dict, list[int]]:
        with self._lock:
            send_message(self._sock, payload)
            reply, fds = recv_message(self._sock, max_fds=1 if expect_fd else 0)
        ...  # 以降は現状のまま
```

`from_socket` にも同じ初期化を入れる（`client._lock = threading.Lock()`）。

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_volume_service.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`_identity_confidence` の 4 つの分岐と、`_match_selection` の observation 比較、
`open` の二重チェック、`release` の close をそれぞれ削り、対応するテストが
落ちることを確認してから戻す。`upsert_device` の同定から `product` を落とす
変異も忘れない（Task 22 の存在理由）。

**「初回は必ず low」の分岐は、`_known_files_survive` が偶然 False を返すため
素朴な筋書きでは検出できない。** manifest を消したうえで published な
source_entry を残す筋書きを作る。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/sources.py app/src/mediaferry/core/manifest.py app/src/mediaferry/jobs/volumes.py app/src/mediaferry/adapters/fs.py app/src/mediaferry/adapters/broker_client.py app/tests/test_volume_service.py app/tests/conftest.py
git commit -m "feat(mediaferry): register volumes and resolve their profiles"
```

---

### Task 24: API とアプリの組み立て

**Files:**
- Create: `app/src/mediaferry/api/__init__.py`
- Create: `app/src/mediaferry/api/app.py`
- Create: `app/src/mediaferry/api/deps.py`
- Create: `app/src/mediaferry/api/jobs_wiring.py`
- Create: `app/src/mediaferry/api/routes_system.py`
- Create: `app/src/mediaferry/api/routes_devices.py`
- Create: `app/src/mediaferry/api/routes_media.py`
- Create: `app/src/mediaferry/__main__.py`
- Modify: `app/pyproject.toml`（`fastapi` と `uvicorn` を追加）
- Modify: `app/Dockerfile`（ffmpeg の導入と、起動コマンドをスパイク CLI から本体へ）
- Test: `app/tests/test_api.py`

**Interfaces:**
- Consumes: これまでの全モジュール
- Produces:
  - `mediaferry.api.app.create_app(env=os.environ, broker_factory=None) -> FastAPI`
  - `mediaferry.api.app.AppState`（`database`, `env`, `registry_conn`, `volumes`,
    `runner`, `last_reconcile`）
  - `mediaferry.api.deps.state(request) -> AppState`
  - `mediaferry.api.deps.conn(request) -> Iterator[sqlite3.Connection]`（リクエスト単位）

**Phase 1 の範囲**: SSE（`GET /events`）は Phase 4 の Web UI と一緒に入れる。
ここでは `GET /api/jobs/{id}/events?after_seq=` のポーリングを提供する。
`BIND_HOST` の既定は loopback のまま変えない。

**接続のスコープを守る。**

| スコープ | 接続 | 用途 |
| --- | --- | --- |
| 起動 | 1 本。手順を終えたら閉じる | migration、ビルトイン同期、reconciliation |
| API リクエスト | リクエストごとに 1 本 | ルータの読み書き |
| ジョブ 1 件 | ジョブごとに 1 本 | `JobStore` と `ArtifactPublisher` の両方をこれに束ねる |
| `VolumeService` | 専用に 1 本（長寿命） | 自分のロックの中でだけ使う |

**停止時は、走っているジョブが終わるのを待ってから資源を閉じる。**
`asyncio.to_thread` で走っているハンドラは `task.cancel()` では止まらない。
待たずに `close_all()` や `conn.close()` を呼ぶと、コピーの途中の
バックグラウンドスレッドから見て dirfd と DB が突然消える。

**待ち時間に timeout を付けて worker を cancel してもいけない。** ハンドラの
スレッドは止まらないのに coroutine 側の `finally` だけが走り、同じことが起きる。
`runner.stop()` が走っているジョブにキャンセルを要求し、ハンドラは
`ctx.cancelled()` を見て降りる。猶予を超えた場合はコンテナの SIGKILL に委ねる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from mediaferry.api.app import create_app


@pytest.fixture
def client(data_root, broker, monkeypatch):
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    app = create_app(broker_factory=lambda: broker)
    with TestClient(app) as client:
        yield client


def test_health_reports_the_schema_version(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["schema_version"] >= 4


def test_startup_seeds_the_builtin_profiles(client):
    slugs = [p["slug"] for p in client.get("/api/profiles").json()["profiles"]]
    assert "dji-osmo" in slugs


def test_settings_report_their_source_lock_and_tier(client):
    settings = {s["key"]: s for s in client.get("/api/settings").json()["settings"]}
    assert settings["DATA_ROOT"]["source"] == "env"
    assert settings["DATA_ROOT"]["locked"] is True
    assert settings["DATA_ROOT"]["tier"] == "bootstrap"
    assert settings["LOG_LEVEL"]["source"] == "default"
    assert settings["LOG_LEVEL"]["writable"] is True


def test_secrets_are_masked_in_the_api(client, monkeypatch):
    settings = {s["key"]: s for s in client.get("/api/settings").json()["settings"]}
    assert settings["AUTH_PASSWORD"]["value"] in (None, "********")
    assert settings["SECRET_KEY"]["writable"] is False


def test_the_master_key_cannot_be_stored_through_the_api(client):
    """暗号文と復号鍵が同じバックアップに入ると、暗号化が何も守らなくなる."""
    response = client.put("/api/settings", json={"key": "SECRET_KEY", "value": "A" * 44})
    assert response.status_code == 409


def test_a_written_setting_reports_when_it_applies(client):
    body = client.put("/api/settings", json={"key": "LOG_LEVEL", "value": "debug"}).json()
    assert body["applies"] == "runtime"
    body = client.put("/api/settings", json={"key": "HTTP_PORT", "value": "9001"}).json()
    assert body["applies"] == "restart"


def test_writing_an_env_locked_setting_is_a_conflict(client):
    assert client.put("/api/settings", json={"key": "DATA_ROOT", "value": "/x"}).status_code == 409


def test_writing_an_invalid_setting_is_a_bad_request(client):
    response = client.put("/api/settings", json={"key": "HTTP_PORT", "value": "nope"})
    assert response.status_code == 400


def test_devices_lists_the_volume_with_its_profile(client):
    volumes = client.get("/api/devices").json()["volumes"]
    assert len(volumes) == 1
    assert volumes[0]["profile_slug"] == "dji-osmo"
    assert volumes[0]["trusted"] is False


def test_scan_then_import_walks_the_whole_path(client, data_root):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    scan = client.post(f"/api/volumes/{volume_id}/scan").json()
    _await_job(client, scan["job_id"])
    assert client.get("/api/media").json()["media"] == []

    imported = client.post(f"/api/volumes/{volume_id}/import").json()
    _await_job(client, imported["job_id"])

    media = client.get("/api/media").json()["media"]
    assert len(media) == 1
    assert media[0]["rel_path"].startswith("library/dji-osmo/")
    assert (data_root / media[0]["rel_path"]).exists()


def test_trusting_a_volume_sticks(client):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    assert client.post(f"/api/volumes/{volume_id}/trust").status_code == 200
    assert client.get("/api/devices").json()["volumes"][0]["trusted"] is True


def test_jobs_can_be_listed_cancelled_and_followed(client):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    job_id = client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"]
    _await_job(client, job_id)
    assert any(j["id"] == job_id for j in client.get("/api/jobs").json()["jobs"])
    events = client.get(f"/api/jobs/{job_id}/events", params={"after_seq": 0}).json()["events"]
    assert events
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code in (200, 409)


def test_orphans_are_exposed(client, data_root):
    assert client.get("/api/orphans").json()["orphans"] == []


def test_closing_a_volume_releases_the_handle(client):
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    job_id = client.post(f"/api/volumes/{volume_id}/import").json()["job_id"]
    _await_job(client, job_id)
    assert client.post(f"/api/volumes/{volume_id}/close").status_code == 200


def test_closing_a_volume_a_job_is_holding_is_a_conflict(client):
    """実行中のワーカーの fd を、API の別スレッドから閉じさせない."""
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    volumes = client.app.state.mediaferry.volumes
    selection = volumes.selection_for(volume_id)
    volumes.open(selection)
    try:
        assert client.post(f"/api/volumes/{volume_id}/close").status_code == 409
    finally:
        volumes.release(selection)
    assert client.post(f"/api/volumes/{volume_id}/close").status_code == 200


def test_a_queued_job_uses_the_profile_revision_it_was_queued_with(db):
    """キューで待っている間にプロファイルを編集しても、規則は変わらない.

    現行リビジョンを読み直すと、確認画面と違う規則で取り込まれる。
    """
    from mediaferry.api.jobs_wiring import _fixed_profile
    from mediaferry.core.profiles.model import definition_to_json
    from mediaferry.db.profiles import ProfileRegistry
    from mediaferry.jobs.volumes import VolumeObservation, VolumeSelection

    registry = ProfileRegistry(db)
    registry.sync_builtins()
    queued = registry.current("dji-osmo")
    changed = definition_to_json(queued.definition).replace(
        '"tolerance_seconds":5', '"tolerance_seconds":9'
    )
    registry._upsert_revision("dji-osmo", changed)  # noqa: SLF001
    assert registry.current("dji-osmo").definition.merge.tolerance_seconds == 9

    selection = VolumeSelection(
        volume_instance_id="v1",
        presence_id="p1",
        observation=VolumeObservation("", 1, "8:160", 8, 160, ""),
        profile_id=queued.profile_id,
        profile_revision_id=queued.revision_id,
    )
    profile = _fixed_profile(db, selection)
    assert profile.revision_id == queued.revision_id
    assert profile.definition.merge.tolerance_seconds == 5


def test_shutdown_waits_for_the_running_handler(data_root, broker, monkeypatch):
    """to_thread のハンドラは task の cancel では止まらない.

    待たずに接続と dirfd を閉じると、まだコピー中のスレッドから見て資源が
    突然消える。lifespan は worker の完了まで待つ。
    """
    import time

    from mediaferry.api import jobs_wiring

    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    order = []

    def slow_scan(self, ctx, conn):
        while not ctx.cancelled():
            time.sleep(0.01)
        # 停止を待っていなければ、ここへ来る前に "shutdown" が積まれる。
        time.sleep(0.3)
        order.append("handler")

    monkeypatch.setattr(jobs_wiring.JobWorld, "run_scan", slow_scan)

    app = create_app(broker_factory=lambda: broker)
    with TestClient(app) as client:
        volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
        job_id = client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"]
        deadline = time.monotonic() + 10
        while client.get(f"/api/jobs/{job_id}").json()["status"] != "running":
            assert time.monotonic() < deadline, "ジョブが走り出さない"
            time.sleep(0.01)
    order.append("shutdown")

    assert order == ["handler", "shutdown"]


def test_a_job_carries_the_presence_it_was_queued_against(client, data_root):
    """volume_instance_id だけだと、抜き差し後に別のカードを取り込みうる（§9.2）."""
    from mediaferry.db.connection import Database

    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    job_id = client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"]
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    params = conn.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    conn.close()
    for key in ("presence_id", "broker_epoch", "generation", "volume_key", "profile_revision_id"):
        assert key in params


def test_the_device_list_reports_identity_confidence(client):
    volume = client.get("/api/devices").json()["volumes"][0]
    assert volume["identity_confidence"] in {"high", "low"}


def _await_job(client, job_id, timeout=20.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()["status"]
        if status not in {"queued", "running", "cancelling"}:
            assert status == "succeeded", client.get(f"/api/jobs/{job_id}").json()
            return
        time.sleep(0.05)
    raise AssertionError(f"ジョブ {job_id} が終わらない")
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_api.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.api'`）

- [ ] **Step 3: 実装する**

`app/pyproject.toml` の `dependencies` に `"fastapi>=0.115"` と `"uvicorn>=0.32"` を
追加し、`uv sync --all-packages`。

`app/src/mediaferry/api/deps.py`:

```python
"""リクエストからアプリの状態と、そのリクエスト専用の DB 接続を取り出す."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import TYPE_CHECKING

from fastapi import Depends, Request

if TYPE_CHECKING:
    from .app import AppState


def state(request: Request) -> AppState:
    return request.app.state.mediaferry


def conn(app_state: AppState = Depends(state)) -> Iterator[sqlite3.Connection]:  # noqa: B008
    """リクエストごとに接続を開いて閉じる.

    トランザクションは接続に属するので、ワーカーと共有するとお互いの
    トランザクションに入り込む。
    """
    connection = app_state.database.connect()
    try:
        yield connection
    finally:
        connection.close()
```

`app/src/mediaferry/api/app.py`:

```python
"""FastAPI の組み立てと起動時の手順.

起動時に必ず行うこと:
  1. マイグレーション適用
  2. ビルトインプロファイルの同期
  3. reconciliation（前回の中断からの回収）
  4. ワーカーの開始

`BIND_HOST` の既定は loopback。認証と CSRF が入る Phase 4 より前に LAN へ
公開しない。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI

from ..adapters.broker_client import BrokerClient
from ..adapters.ffprobe import MediaProbe
from ..adapters.fs import assert_same_filesystem
from ..adapters.publisher import ArtifactPublisher
from ..db.connection import Database
from ..db.jobs import JobStore
from ..db.migrate import apply_migrations
from ..db.profiles import ProfileRegistry
from ..jobs.reconcile import Reconciler, ReconcileReport
from ..jobs.runner import JobRunner
from ..jobs.volumes import VolumeService
from ..settings import SettingsService, bootstrap_data_root, startup_warnings
from .jobs_wiring import JobWorld
from .routes_devices import router as devices_router
from .routes_media import router as media_router
from .routes_system import router as system_router

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    database: Database
    env: Mapping[str, str]
    volumes: VolumeService
    runner: JobRunner
    last_reconcile: ReconcileReport = field(default_factory=ReconcileReport)


def create_app(
    env: Mapping[str, str] | None = None,
    broker_factory: Callable[[], BrokerClient] | None = None,
) -> FastAPI:
    env = dict(os.environ if env is None else env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(bootstrap_data_root(env) / "var" / "mediaferry.sqlite3")

        # 起動の手順はこの 1 本で行い、終わったら閉じる。
        startup = database.connect()
        try:
            apply_migrations(startup)
            settings = SettingsService(startup, env).snapshot()
            for warning in startup_warnings(settings):
                logger.warning("%s", warning)
            # 公開は os.link なので、staging と公開先が別デバイスだと必ず失敗する。
            assert_same_filesystem(
                settings.data_root / "staging",
                settings.data_root / "library",
                settings.data_root / "derived",
            )
            ProfileRegistry(startup).sync_builtins()
            report = Reconciler(
                startup,
                settings.data_root,
                ArtifactPublisher(startup, settings.data_root, MediaProbe()),
                JobStore(startup),
            ).run()
        finally:
            startup.close()

        client = broker_factory() if broker_factory else BrokerClient(settings.broker_socket)
        # VolumeService は長寿命なので専用の接続を持つ。他とは共有しない。
        volumes_conn = database.connect()
        volumes = VolumeService(volumes_conn, ProfileRegistry(volumes_conn), client)
        world = JobWorld(database, env, volumes)
        runner = JobRunner(database)
        runner.register("scan", world.run_scan)
        runner.register("import", world.run_import)

        state = AppState(
            database=database, env=env, volumes=volumes, runner=runner, last_reconcile=report
        )
        app.state.mediaferry = state

        worker = asyncio.create_task(runner.run_forever())
        try:
            yield
        finally:
            # 走っているジョブにキャンセルを要求し、**実際に終わるまで待つ**。
            #
            # ここで timeout を付けて worker を cancel してはいけない。
            # `to_thread` のハンドラはそれでは止まらないのに、coroutine 側の
            # finally だけが走って、まだ読み書きしている接続を閉じてしまう。
            # 猶予を超えた場合はコンテナの SIGKILL に委ねる（プロセスごと
            # 終わるので、中途半端に資源を剥がすより安全）。
            await runner.stop()
            await worker
            volumes.close_all()
            volumes_conn.close()

    app = FastAPI(title="mediaferry", lifespan=lifespan)
    app.include_router(system_router, prefix="/api")
    app.include_router(devices_router, prefix="/api")
    app.include_router(media_router, prefix="/api")
    return app
```

ジョブは 1 件ごとに自分の接続を開く。`JobRunner` にその作り方を渡す。

`app/src/mediaferry/api/jobs_wiring.py`:

```python
"""ジョブ 1 件ぶんの世界を組み立てる.

ジョブごとに DB 接続を開き、JobStore と ArtifactPublisher の両方をそれに
束ねる。手順 7（§9.3）でリースの確認と staged への遷移を 1 つの
BEGIN IMMEDIATE に入れる必要があり、別接続だと同じトランザクションに
できない。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from ..adapters.ffprobe import MediaProbe
from ..adapters.publisher import ArtifactPublisher
from ..db.connection import Database
from ..db.jobs import JobContext, JobStore
from ..db.profiles import ProfileRef, ProfileRegistry
from ..jobs.importer import Importer
from ..jobs.scan import Scanner
from ..jobs.volumes import VolumeSelection, VolumeService
from ..settings import SettingsService


class JobWorld:
    def __init__(self, database: Database, env: Mapping[str, str], volumes: VolumeService) -> None:
        self._database = database
        self._env = env
        self._volumes = volumes

    def store(self, conn: sqlite3.Connection) -> JobStore:
        return JobStore(conn)

    def connect(self) -> sqlite3.Connection:
        return self._database.connect()

    def run_scan(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        selection = VolumeSelection.from_params(ctx.params)
        profile = _fixed_profile(conn, selection)
        handle = self._volumes.open(selection)
        try:
            outcome = Scanner(conn).scan(ctx, handle.dirfd, selection.volume_instance_id, profile)
        finally:
            self._volumes.release(selection)
        ctx.emit(
            "info",
            f"スキャン完了: 新規 {outcome.new} 件 / 取込済 {outcome.already_imported} 件",
        )

    def run_import(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        selection = VolumeSelection.from_params(ctx.params)
        profile = _fixed_profile(conn, selection)
        # RUNTIME 層の設定はジョブの開始時に読み直す（画面での変更を待たせない）。
        settings = SettingsService(conn, self._env).snapshot()
        publisher = ArtifactPublisher(conn, settings.data_root, MediaProbe())
        importer = Importer(conn, publisher, settings.data_root, settings.default_timezone)
        handle = self._volumes.open(selection)
        try:
            outcome = importer.run(ctx, handle.dirfd, selection.volume_instance_id, profile)
        finally:
            self._volumes.release(selection)
        ctx.emit("info", f"取り込み完了: {outcome.published} 件")


def _fixed_profile(conn: sqlite3.Connection, selection: VolumeSelection) -> ProfileRef:
    """キュー投入時に固定したリビジョンを読む.

    現行リビジョンを読み直すと、キューで待っている間にプロファイルを
    編集しただけで、確認画面と違う規則で取り込まれる。
    """
    registry = ProfileRegistry(conn)
    definition = registry.definition_of(selection.profile_revision_id)
    return ProfileRef(
        profile_id=selection.profile_id,
        revision_id=selection.profile_revision_id,
        revision=0,
        definition=definition,
    )
```

`app/src/mediaferry/api/routes_system.py`:

```python
"""ヘルス・設定・プロファイル・ジョブ."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..db.jobs import JobStore
from ..db.profiles import ProfileRegistry
from ..settings import SettingInvalid, SettingLocked, SettingsService
from .deps import conn as get_conn
from .deps import state as get_state

router = APIRouter()


@router.get("/health")
def health(conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    version = conn.execute("SELECT MAX(version) AS v FROM schema_migration").fetchone()["v"]
    return {"status": "ok", "schema_version": version}


@router.get("/settings")
def list_settings(state=Depends(get_state), conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {
        "settings": [
            {
                "key": s.key,
                "value": s.value,
                "source": s.source,
                "locked": s.locked,
                "tier": s.tier.value,
                "writable": s.writable,
            }
            for s in SettingsService(conn, state.env).describe_all()
        ]
    }


@router.put("/settings")
def write_setting(
    body: dict[str, str],
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, str]:
    try:
        tier = SettingsService(conn, state.env).set(body["key"], body["value"])
    except SettingLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SettingInvalid, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # いつ効くかを返す。RESTART の値を変えて「反映されない」と見えるのを防ぐ。
    return {"status": "ok", "applies": tier.value}


@router.get("/profiles")
def list_profiles(conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {
        "profiles": [
            {"slug": ref.definition.slug, "name": ref.definition.name, "revision": ref.revision}
            for ref in ProfileRegistry(conn).active()
        ]
    }


@router.get("/jobs")
def list_jobs(conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {"jobs": [_job(row) for row in JobStore(conn).list_jobs()]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    row = JobStore(conn).get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="そのジョブは無い")
    return _job(row)


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, after_seq: int = 0, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {
        "events": [
            {"seq": e["seq"], "level": e["level"], "message": e["message"], "at": e["at"]}
            for e in JobStore(conn).events(job_id, after_seq)
        ]
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, conn=Depends(get_conn)) -> dict[str, str]:  # noqa: ANN001, B008
    if not JobStore(conn).request_cancel(job_id):
        raise HTTPException(status_code=409, detail="そのジョブはもう終わっている")
    return {"status": "cancelling"}


def _job(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "error": row["error"],
    }
```

`app/src/mediaferry/api/routes_devices.py`:

```python
"""接続中デバイスとボリュームの操作."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..db.jobs import JobStore
from ..jobs.volumes import StaleSelection, VolumeBusy
from .deps import conn as get_conn
from .deps import state as get_state

router = APIRouter()


@router.get("/devices")
def list_devices(state=Depends(get_state)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {
        "volumes": [
            {
                "volume_instance_id": view.volume_instance_id,
                "volume_key": view.volume_key,
                "fs_label": view.fs_label,
                "size_bytes": view.size_bytes,
                "profile_slug": view.profile_slug,
                "identity_confidence": view.identity_confidence,
                "provisional": view.provisional,
                "trusted": view.trusted,
                "reason": view.reason,
            }
            for view in state.volumes.refresh()
        ]
    }


@router.post("/volumes/{volume_instance_id}/trust")
def trust(volume_instance_id: str, state=Depends(get_state)) -> dict[str, str]:  # noqa: ANN001, B008
    state.volumes.trust(volume_instance_id)
    return {"status": "ok"}


@router.post("/volumes/{volume_instance_id}/scan")
def scan(
    volume_instance_id: str,
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, str]:
    return {"job_id": _enqueue(state, conn, "scan", volume_instance_id)}


@router.post("/volumes/{volume_instance_id}/import")
def start_import(
    volume_instance_id: str,
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, str]:
    return {"job_id": _enqueue(state, conn, "import", volume_instance_id)}


def _enqueue(state, conn, job_type: str, volume_instance_id: str) -> str:  # noqa: ANN001
    """選択した瞬間の presence とプロファイルリビジョンを params に固定する.

    volume_instance_id だけを渡すと、実行時に「最新の presence」を選ぶことに
    なり、抜き差しで別のカードが同じノードに来ていても、その現在値から
    正しい expect を組み立ててブローカーの TOCTOU 検証をすり抜ける（§9.2）。
    """
    try:
        selection = state.volumes.selection_for(volume_instance_id)
    except StaleSelection as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JobStore(conn).enqueue(job_type, selection.to_params())


@router.post("/volumes/{volume_instance_id}/close")
def close(volume_instance_id: str, state=Depends(get_state)) -> dict[str, str]:  # noqa: ANN001, B008
    try:
        state.volumes.close(volume_instance_id)
    except VolumeBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}
```

`app/src/mediaferry/api/routes_media.py`:

```python
"""ライブラリの一覧と、reconciliation が見つけた齟齬."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .deps import conn as get_conn
from .deps import state as get_state

router = APIRouter()


@router.get("/media")
def list_media(limit: int = 200, offset: int = 0, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    rows = conn.execute(
        "SELECT * FROM media_file ORDER BY captured_at DESC LIMIT ? OFFSET ?", (limit, offset)
    )
    return {"media": [_media(row) for row in rows]}


@router.get("/media/{media_id}")
def get_media(media_id: str, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="そのメディアは無い")
    return _media(row)


@router.get("/orphans")
def list_orphans(state=Depends(get_state), conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    report = state.last_reconcile
    missing = conn.execute(
        "SELECT id, rel_path, missing_at FROM media_file WHERE missing_at IS NOT NULL"
    )
    return {
        "orphans": [
            {"rel_path": o.rel_path, "size_bytes": o.size_bytes, "sha1": o.sha1}
            for o in report.orphans
        ],
        "unrecoverable": report.unrecoverable,
        "missing": [
            {"id": row["id"], "rel_path": row["rel_path"], "missing_at": row["missing_at"]}
            for row in missing
        ],
    }


def _media(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "role": row["role"],
        "rel_path": row["rel_path"],
        "size_bytes": row["size_bytes"],
        "kind": row["kind"],
        "captured_at": row["captured_at"],
        "captured_at_source": row["captured_at_source"],
        "duration_seconds": row["duration_seconds"],
        "probe_state": row["probe_state"],
        "missing_at": row["missing_at"],
    }
```

`app/src/mediaferry/__main__.py`:

```python
"""起動エントリ.

BIND_HOST の既定は loopback。Phase 4 で認証と CSRF が入るまで LAN へ公開しない。
"""

from __future__ import annotations

import logging
import os

import uvicorn

from .api.app import create_app
from .db.connection import Database
from .db.migrate import apply_migrations
from .settings import SettingsService, bootstrap_data_root


def main() -> None:
    env = dict(os.environ)
    # BIND_HOST / HTTP_PORT / LOG_LEVEL は RESTART 層で、DB にも保存できる。
    # env だけで決めると、画面で変えて再起動しても反映されない。
    database = Database(bootstrap_data_root(env) / "var" / "mediaferry.sqlite3")
    conn = database.connect()
    try:
        apply_migrations(conn)
        settings = SettingsService(conn, env).snapshot()
    finally:
        conn.close()

    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(create_app(env=env), host=settings.bind_host, port=settings.http_port)


if __name__ == "__main__":
    main()
```

`app/src/mediaferry/api/__init__.py` は空ファイル。

`app/Dockerfile` を Phase 1 の姿にする。ffprobe が無いと `probe_state` が
すべて `failed` になり、§9.7 の結合グループ検出（Phase 2）が全ファイルを
境界として扱ってしまう。

```dockerfile
FROM python:3.12-slim-bookworm

# ffprobe は公開前のメタデータ確定に、ffmpeg は Phase 2 の結合に使う。
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY protocol/ /srv/protocol/
COPY app/ /srv/app/
RUN pip install --no-cache-dir /srv/protocol \
    && pip install --no-cache-dir /srv/app

ENV MEDIAFERRY_BROKER_SOCKET=/run/mediaferry/broker.sock

CMD ["python", "-m", "mediaferry"]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest app/tests/test_api.py -v`
Expected: すべて PASS

- [ ] **Step 5: 変異試験**

`routes_system` の `SettingLocked` / `SettingInvalid` の except 節、
`_enqueue` の `selection.to_params()`、`app.py` の `sync_builtins()` をそれぞれ
削り、対応するテストが落ちることを確認してから戻す。

**次の 3 つは、素朴な筋書きでは検出できない。**

| 変異 | 検出できない理由 | 効くテスト |
| --- | --- | --- |
| 使用中の `close` を 409 にしない | ジョブが一瞬で終わるので、握っている最中に叩けない | `test_closing_a_volume_a_job_is_holding_is_a_conflict`（テストが自分で `open` する） |
| 固定リビジョンではなく現行を読む | リビジョンが 1 つしか無いと同じ定義になる | `test_a_queued_job_uses_the_profile_revision_it_was_queued_with`（`_fixed_profile` を直接見る） |
| 停止時に worker を待たない | ジョブが既に終わっていると差が出ない | `test_shutdown_waits_for_the_running_handler`（停止要求後に遅延を入れて順序を見る） |

- [ ] **Step 6: 全体を通す**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: すべて PASS

- [ ] **Step 7: コミット**

```bash
git add app/src/mediaferry/api app/src/mediaferry/__main__.py app/tests/test_api.py app/pyproject.toml app/Dockerfile uv.lock
git commit -m "feat(mediaferry): expose scan and import over a loopback api"
```

---

### Task 25: バックアップ手順と実 USB の確認手順

**Files:**
- Create: `docker/mediaferry/docs/phase1-backup.md`
- Create: `docker/mediaferry/docs/phase1-manual-checklist.md`
- Modify: `docker/mediaferry/README.md`
- Modify: `docker/mediaferry/docs/design.md`（§18-4 を解消済みにする）
- Modify: `docker/mediaferry/docs/HANDOFF.md`（現在地を Phase 2 へ進める）

**Interfaces:**
- Consumes: Task 1〜23 の成果
- Produces: ドキュメントのみ

§18-4 は「Phase 1 で、ライブラリからどこまで再構築できるかを明記し、定期
バックアップ手順を用意する」と決めてある。ここで閉じる。

- [ ] **Step 1: バックアップ手順を書く**

`docker/mediaferry/docs/phase1-backup.md` に次を含める。

- **DB が唯一の状態保持先である**こと（`failed_merges/` と `upload/` を廃止したため）
- ライブラリから再構築できるもの / できないもの:

  | 失うもの | 再構築 |
  | --- | --- |
  | `media_file`（実体・ハッシュ・撮影日時） | ライブラリを再スキャンすれば作り直せる（ffprobe と ファイル名から） |
  | `source_entry`（カード側の取込済判定） | カードを再スキャンすれば作り直せる。ただし取込済の判定は一度失われ、全件が新規に見える |
  | `merge_group`（グループの構成と採用の判断） | 自動検出はやり直せるが、**手動編集と採用の記録は失われる** |
  | `upload_record`（宛先ごとの送信済み状態） | **再構築できない。** 再送すると Immich 側の重複判定で弾かれるが、`origin` は `unknown` に落ちるので日時補正が承認待ちになる |
  | `destination_credential`（API キー） | **再構築できない。** 再登録が要る |

- バックアップ対象: `var/mediaferry.sqlite3` と `-wal` / `-shm`。
  **稼働中にファイルコピーしない。** `sqlite3 var/mediaferry.sqlite3 ".backup out.sqlite3"`
  または `VACUUM INTO` を使う（WAL と整合した 1 ファイルになる）
- バックアップファイルは 0600 で保存する（API キーの暗号文を含む）
- `MEDIAFERRY_SECRET_KEY` は **DB のバックアップと同じ場所に置かない**。
  同じ場所に置くと §12.3 の境界が消える
- TrueNAS のスナップショットは DB の整合を保証しないので、
  `.backup` を定期実行して**その出力をスナップショット対象のデータセットへ置く**
- リストア手順: アプリを停止 → `var/` に戻す → 起動（起動時の reconciliation が
  ファイルと DB の齟齬を回収する）

- [ ] **Step 2: 実 USB の確認手順を書く**

`docker/mediaferry/docs/phase1-manual-checklist.md`。実行場所は TrueNAS ホスト。
**zsh なので行内コメントを書かない、`tail -n 1` を使う**（HANDOFF §4）。

チェック項目:

1. カードを挿し `GET /api/devices` に現れ、`profile_slug` が `dji-osmo` になる
2. 内蔵ストレージ（空の DCIM）が `provisional: true` / `confidence: low` で出る
3. `POST /api/volumes/{id}/scan` が新規件数を返す
4. `POST /api/volumes/{id}/import` でライブラリにカードと同じ相対パスができる
5. 取り込み中にカードを抜く → ジョブが `failed` で終わり、`library/` に中途半端な
   ファイルが残らない
6. 再挿入して再取り込みすると、取込済のファイルはスキップされる
7. 取り込み中に `docker restart` → 起動時に回収され、`GET /api/orphans` が空
8. `POST /api/jobs/{id}/cancel` で止まり、`staging/` が残らない
9. 同名で内容の違うファイルを `library/` に置いてから取り込み、既存が
   上書きされず別名が付く
10. `POST /api/volumes/{id}/close` の後にカードを安全に抜ける
11. **mtime の解釈を実測する。** DJI のファイル名は壁時計そのものなので、
    同じファイルについて次の 2 つが一致するかを見る。

    ```bash
    ls /path/to/DCIM/DJI_001
    TZ=UTC stat -c '%y %n' /path/to/DCIM/DJI_001/DJI_20260817143000_0001_D.MP4
    ```

    一致すれば、カードの時刻欄に UTC オフセットが書かれていない（または 0）
    ことの確認になり、`timestamps.py` と `_collision_stamp` の前提が成り立つ。
    **一致しなければ**、その機種は `OffsetFromUtc` を書いているので、
    mtime の壁時計をプロファイルの timezone で描画する形へ変える。
    結果は `phase0-findings.md` に 1 件として残す

- [ ] **Step 3: README と設計仕様書を更新する**

- `README.md`: 「Phase 0 まで完了」→「Phase 1 完了」。開発コマンドに変更なし。
  `docs/phase1-plan.md` / `phase1-backup.md` / `phase1-manual-checklist.md` を表に足す
- `design.md` §12 の設定表に **`BROKER_SOCKET`**（既定 `/run/mediaferry/broker.sock`）を
  足す。`compose.yaml` は既にこのキーを app へ渡しているのに、仕様書の表に無い
- `design.md` §18 の 4 番（DB のバックアップとリストア）を
  「**Phase 1 で解消。** 手順は `phase1-backup.md`」に書き換える
- `design.md` §20 の Phase 1 行に完了の印を付ける
- `HANDOFF.md`: 「現在地」を Phase 2 に進め、Phase 1 で確定した契約
  （`ArtifactPublisher` の 11 手順、`artifact_staging` の状態、claim の作法）を
  「蒸し返さないこと」の表に追記する

- [ ] **Step 4: 検証**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: すべて PASS

`docs/` のリンク切れが無いことを目視で確認する。

- [ ] **Step 5: コミット**

```bash
git add docker/mediaferry/docs docker/mediaferry/README.md
git commit -m "docs(mediaferry): document backups and close phase 1"
```

---

## 実装順序と依存

```
1 (DB 基盤)
├─ 2 (job/setting) ─ 6 (settings)
├─ 3 (profile/source) ─ 13 (registry)
├─ 4 (artifact/merge)
└─ 5 (dest/upload)

7 (crypto)  8 (fingerprint)  9 (profile model) ─ 10 (matching)
11 (timestamps)  12 (naming)  15 (fs)  16 (ffprobe)  22 (usb product)

14 (jobs) ─ 17 (publisher) ─ 18 (scanner) ─ 19 (importer)
                  └─ 20 (reconciler) ─ 21 (crash tests)
                            └─ 23 (volumes) ─ 24 (api) ─ 25 (docs)
```

Task 7〜12・15・16・22 は互いに独立で、Task 1 の後ならいつでも着手できる
（22 は Task 1 にも依存しない）。並行して進める場合はこの 9 つを分けるのが安全。
23 は 10・13・15・22 を、24 は 14・19・20・23 を必要とする。

## Phase 1 の完了条件（§20）

- [ ] 実 USB で取り込める（`phase1-manual-checklist.md` の 10 項目すべて）
- [ ] §9.3 の**任意の手順**で落としても reconciliation が回収する
      （Task 21 の 22 ケース）
- [ ] `uv run pytest` / `ruff check` / `ruff format --check` がすべて通る
- [ ] API は loopback バインドのまま。認証と CSRF は Phase 4

## Phase 1 でやらないこと（意図的な除外）

| 項目 | いつ |
| --- | --- |
| 結合グループの検出・結合・検証 | Phase 2 |
| §10 の選択肢の提示規則 | Phase 2 |
| Immich の状態機械・転送先 CRUD・接続検証 | Phase 3（スキーマと暗号フォーマットだけ Phase 1 で固定済み） |
| SSE（`GET /events`） / React SPA / 認証 / CSRF | Phase 4 |
| `generic-dcim` / `canon-eos` プロファイル、プロファイル編集 UI | Phase 5 |
| `DeviceMonitor` の uevent 購読（自動検出） | mountd 側。Phase 1 は `list_volumes` のポーリングで足りる |
| サムネイル生成 | Phase 4（画面と一緒） |
| `recompute_timestamps` / `deep_verify` ジョブ | 種別は `job.type` の CHECK に入れてある。実装は必要になった時点 |

## レビュー記録

2026-08-17、codex にレビューを依頼した（blocker 8 / major 8 / minor 1）。

### 反映した blocker

| 指摘 | 反映先 |
| --- | --- |
| 手順 10 の後を再開できない。`_link` は staging を unlink した後も `staged` のままなので、再開時の `os.link` が必ず `FileNotFoundError` になる | Task 17 に `_adopt_published_final`（staging が無ければ final の大きさと SHA-1 だけで引き取る／一致しなければ `StagingLost`）。Task 20 で `unrecoverable` として報告 |
| `assert_lease` が `extend_lease` を呼んでいたため、`cancelling` でも成功し、期限切れも復活していた。確認と staged 遷移が別トランザクションで、その隙間に cancel が入れた | Task 14 で `assert_lease`（SELECT のみ・`running` かつ期限内）と `extend_lease` を分離。Task 17 で両者を 1 つの `BEGIN IMMEDIATE` に入れる。Task 19 でコピー中に heartbeat |
| 1 本の `sqlite3.Connection` を API スレッドとワーカースレッドで共有していた。トランザクションは接続に属するので、API の書き込みが publisher の `BEGIN IMMEDIATE` に混ざる | Task 1 を `Database` ファクトリに変更。API はリクエストごと、ジョブは 1 件ごと、`VolumeService` は専用の接続を持つ |
| ジョブが `volume_instance_id` しか持たず、実行時に「最新の presence」を選んでいた。§9.2 の TOCTOU 対策が外れていた | Task 23 に `VolumeSelection`（presence / broker_epoch / generation / volume_key / major:minor / fs_uuid / profile_revision）。Task 24 で params に固定し、実行時に照合 |
| `BrokerClient` を複数スレッドから同時に使っていた。1 本の SEQPACKET なので応答を取り違える。handle が presence に束縛されておらず、実行中に別スレッドから close できた | Task 23 で `_call` をロックで直列化。handle を selection に束縛し参照カウントを持たせ、使用中の close は 409 |
| 停止時に `to_thread` のハンドラを待たずに fd と DB を閉じていた | Task 24 で `stop()` → `run_forever()` の完了を待ってから資源を閉じる。猶予超過はプロセス終了に任せる |
| `SECRET_KEY` を DB に保存できてしまい、§12.3 の境界（暗号文と鍵を別の場所に置く）が消えていた。`AUTH_PASSWORD` も平文保存になっていた | Task 6 で設定を 3 層に分け、`BOOTSTRAP`（env のみ）は DB に書けも読めもしない |
| 取り込み中にカードを抜いても、全件失敗でジョブが `succeeded` になっていた | Task 19 で `ImportFailed` を送出。デバイス消失の errno なら残りを試さず降りる。staged 以降の失敗は `PublishInterrupted` として区別し、`source_entry` を failed に戻さない |

### 反映した major / minor

| 指摘 | 反映先 |
| --- | --- |
| `os.utime` が fsync の後で、staging の親を fsync していなかった（`os._exit` では検出できない） | Task 17 で順序を入れ替え、staging の親を staged commit の前に fsync。順序を spy で固定するテストを追加。Task 24 起動時に `assert_same_filesystem` |
| プロファイルの一致度を `identity_confidence` として保存し、`content_manifest_digest` を一度も計算していなかった。`UsbInfo` に `product` が無く常に空文字だった | Task 10 から `confidence` を削除。Task 22 を新設して wire に `product` を追加。Task 23 で manifest と既知ファイルの残存率から確度を出す |
| `upload_record` の複合 FK が NULL で迂回でき、存在しない `target_epoch` を作れた。state と claim/revision の整合が無かった | Task 5 に epoch 存在の trigger と 3 つの CHECK |
| `merge_member.active` の trigger が片方向だけで、superseded なグループに後から active な member を足せた | Task 4 に insert/update の trigger と supersede の不可逆性 |
| migration の失敗で rollback せず、版を記録しないファイルが DDL だけ commit されていた。既存 DB の権限を直していなかった | Task 1 で runner がトランザクションを所有（ファイルは DDL のみ）。checksum で改変を検出。権限は接続のたびに直す |
| queued の cancel が永久に `cancelling` で残った | Task 14 で queued は即 `cancelled` |
| `__main__` が DB の設定を無視し、`AppState.settings` が古いままだった | Task 24 で起動時に DB + env から解決。RUNTIME 層はジョブ開始時に読み直す |
| `missing_at` が一度立つと復元しても消えなかった | Task 20 で `_sync_missing` にして復帰も扱う |
| 衝突時の同内容判定が SHA-1 だけだった | Task 17 で大きさと SHA-1 の両方を比較 |

### 2 巡目（blocker 3 / major 7 / minor 1）で反映した指摘

1 巡目の修正で新しく入れた接続・presence・停止の実装に、実行時の破綻があった。

| 指摘 | 反映先 |
| --- | --- |
| **接続を分けたのに `check_same_thread` を戻さなかった。** `to_thread` はどの worker で走るか保証しないので、最初の `claim_next` で `ProgrammingError` になる | Task 1 で `check_same_thread=False` に戻し、**危険なのはフラグではなく共有そのもの**だとコメントに残した。別スレッドで作って使うテストを追加 |
| **`record_presence` が列挙のたびに行を増やしていた。** `selection_for` が内部で refresh するので、GET /devices → scan → import の間に presence_id が変わり、**同じカードが挿さったままでも必ず `StaleSelection`** になる | Task 3 に `UNIQUE(volume_instance_id, broker_epoch, generation, major, minor)`。Task 23 を `sync_presence` + `detach_absent` のスナップショット反映にし、参照 0 で選択の変わった handle を retire。refresh を挟んでも scan → import できるテストを追加 |
| **停止の timeout が worker を cancel し、使用中の接続を閉じていた。** `to_thread` のハンドラは止まらないのに coroutine 側の `finally` だけが走る。しかも `stop()` は走っているジョブに cancel を要求していなかった | Task 14 の `stop()` が現在のジョブへ cancel を要求。Task 24 は timeout を付けずに完了を待つ（猶予超過は SIGKILL に委ねる）。Task 19 はコピーの chunk 境界で cancel を見る |
| ハンドラ完了と cancel の間に TOCTOU。`get()` で読んでから `finish()` するので、間に入った cancel が succeeded で上書きされる | Task 14 に `finish_claimed`（`running→succeeded` / `cancelling→cancelled` を 1 文の CAS で決める） |
| `volume_presence` に `detached_at` を立てる経路が無く、抜いたポートの行が永久に live。同一 identity の同時接続を誤検出して確度が上がらない | Task 23 の `detach_absent`。別ポートへ挿し直しても `high` に戻るテストを追加 |
| `staging/<job-id>` を作ったとき、その名前を持つ `staging/` 側を fsync していない | Task 17 で、ジョブ用ディレクトリを新規作成したときだけ親も fsync |
| epoch の guard が INSERT だけで、UPDATE で迂回できた | Task 5 に同一性 3 欄の不変トリガ |
| `merge_member.active` の guard が INSERT と `UPDATE OF active` だけで、親の付け替えで迂回できた | Task 4 で `active` を親の状態の写しとして両方向に強制 |
| heartbeat をバイト数で決めると、低速なカードでは最初の 1 回の前にリースが切れる | Task 19 で経過時間（リースの 1/3）に変更 |
| `UsbInfo.product` を必須にすると既存の fixture が `TypeError` で壊れる | Task 22 の Files に既存 4 ファイルを明記（既定値は付けない） |

### 3 巡目（blocker 2 / major 2）で反映した指摘

2 巡目で入れた presence の反映順序に、判定との境界の問題が残っていた。

| 指摘 | 反映先 |
| --- | --- |
| **`refresh()` が detach より先に判定していた。** 別ポートへ挿し直した最初の refresh では旧 presence がまだ live なので「同一 identity の同時接続」と誤判定し、2 巡目で追加した再挿入テストが**提示コードのままでは落ちる**。同じ identity の 2 枚を初めて同時に列挙すると、先に判定した方だけ high になる | Task 23 の `refresh()` を 3 パスに分離（① スナップショットを 1 回取って device/volume/presence を反映 → ② `detach_absent` と handle の retire → ③ 確定した live 集合で判定）。同一 identity 2 枚が最初から両方 low になるテストを追加 |
| **`_probe()` が世代を確認せずに開いてある dirfd を再利用していた。** 差し替えられたカードを、旧カードの中身で判定した結果として記録する。retire も判定の後だった | `VolumeObservation`（epoch / generation / volume_key / major:minor / fs_uuid）を導入し、**完全一致のときだけ**再利用する。retire は判定の前。新旧で中身を変え、新世代が新しい中身で判定されることを固定するテストを追加 |
| `fs_uuid` が空だと `upsert_volume` が毎回新しい `volume_instance` を作り、同じ broker snapshot を列挙し直しただけで selection が無効になる | `resolve_volume_instance` に改名し、UUID が無いときは同じ観測の live presence から引く。世代が変われば継承しない規則を明記。UUID 無しの fixture で selection が生き続けるテストを追加 |
| `stop()` と `claim_next()` の間の race。claim 待ちの間に停止要求が来ると `_current` がまだ None なので cancel されず、掴んだ長いジョブの完走待ちになる | Task 14 で claim 直後に `_stopping` を再確認し、立っていれば `request_cancel` してから実行する |

### 4 巡目（blocker 4 / major 2）で反映した指摘

3 巡目で入れた「観測が完全一致すれば dirfd を再利用してよい」という前提が、
実装済みの mountd の契約と食い違っていた。テスト側の欠陥も 4 件。

| 指摘 | 反映先 |
| --- | --- |
| **完全一致でも cached dirfd が現在のカードを指すとは限らない。** `mountd/server.py::_observe` の `generation` は uevent の数ではなく、観測した集合の `(volume_key, fs_uuid, fs_type, size_bytes)` が変わったときだけ進む。Phase 1 は polling なので、同じ UUID・型・容量のカードが同じ major:minor で観測の合間に差し替わると **generation も epoch も据え置き**になる | Task 23 の `_probe` を**毎回 fresh open / close** に変更。ジョブ用 handle のキャッシュは「選択した時点のカードを読み続ける」用途として残す。代償（列挙のたびに mount / umount）と、避けるなら mountd 側に incarnation が要ることを明記 |
| `VolumeSelection` の構造変更後も 2 つのテストが旧フィールドを `replace` していて `TypeError` になる | `replace(selection, observation=replace(selection.observation, ...))` へ修正 |
| stop / claim の競合テストが、停止済みの状態で `run_forever` を呼ぶため loop に入らず、job を claim しない | `claim_next` を barrier で「claim 済み・戻る直前」に止め、そこで `stop()` する本物の競合に |
| `release()` は `_open` から消さないので、`opened() == []` を期待するテストは落ちる | 期待を `[volume_instance_id]` にし、`close()` まで通す形に |
| stale dirfd の回帰テストが、旧 fd と新 open が同じディレクトリを指すため修正を検出しない | `FakeMountManager.target` を差し替える fixture にし、既存 fd は旧カードを見続ける構成に。「generation 据え置きのまま差し替え」もこれで固定 |
| 同一 identity 2 本のテストが、最初から 2 本返して 2 回目を見るため旧実装でも通る | 1 本で high を作ってから 2 本目が現れた**最初の refresh** を見る形に |

### 5 巡目（blocker 1）で反映した指摘

4 巡目の修正（判定は毎回開き直す）の論理的帰結を追い切れていなかった。

| 指摘 | 反映先 |
| --- | --- |
| **判定は新カードを見るのに、その後の `open()` が同じ observation の古い handle を返す。** 差し替えでは generation も epoch も据え置きなので、`_retire_stale_handles` は古い handle を live と見なして残す。結果、**画面には新カードが見えるのに、取り込むのは取り外した旧カードの detached clone**になる | Task 23 で handle のジョブ間キャッシュを廃止。`release()` がその場で閉じ、`open()` は既に開いていれば `VolumeBusy`。`_retire_stale_handles` は不要になったので削除。差し替えテストの末尾に「次に開く dirfd も新カードである」ことの確認を追加 |

`VolumeObservation` は**接続の同一性であって媒体の同一性ではない**。この区別を
守れる場所（selection の照合）と守れない場所（開いた fd の使い回し）を分け、
後者を作らないことで解決している。

### 6 巡目（blocker 1 / minor 1）で反映した指摘

| 指摘 | 反映先 |
| --- | --- |
| 差し替えテストに旧契約の assertion（`release()` 後の dirfd で旧カードを確認する）が残っており、`EBADF` で判定まで到達しない | その行を「旧 fd がもう使えない」の確認に置き換えた。キャッシュ型へ戻す変異は前半で、stale 再利用は後半で落ちる |
| `VolumeObservation` の docstring に「完全一致なら dirfd を再利用してよい」が残っていた | 「接続の同一性であって媒体の同一性ではないので、dirfd 再利用の根拠にできない」に置換 |

### 退けた指摘

| 指摘 | 判断 |
| --- | --- |
| 衝突名の壁時計が「profile timezone ではなく UTC」なので Asia/Tokyo のカードで 9 時間ずれる | **条件付きで退けた。** オフセットの付与は瞬間を移動しないので、桁は変わらない。ただし再指摘のとおり、この主張が成り立つのは**カードの時刻欄に UTC オフセットが書かれていない場合に限る**。Linux の exfat ドライバは `OffsetFromUtc` の valid bit が立っていればそれで UTC へ変換する（`fs/exfat/misc.c`）。DJI のファイル名は壁時計そのものなので実機で確かめられる。実測を `phase1-manual-checklist.md` の 11 番に入れ、一致しない機種が出たら profile timezone で描画する形へ変えると明記した。「staged の metadata に永続化して再開時に再計算しない」という提案は採用済み |


