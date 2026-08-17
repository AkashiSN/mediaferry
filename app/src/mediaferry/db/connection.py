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
