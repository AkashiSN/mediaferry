"""起動エントリ.

BIND_HOST の既定は loopback。LAN へ出すなら AUTH_PASSWORD を設定する。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn

from .api.app import create_app
from .db.connection import Database
from .db.migrate import apply_migrations
from .settings import SettingsService, bootstrap_data_root
from .single_instance import AlreadyRunning, hold_data_root


def main() -> int:
    """所有権を取ってから本体を動かす. 握れなければ **1 を返して終わる.**

    移行も reconciliation も、握れてから走らせる —— 後から起動した側が、
    有効期限内の `running` を倒して作業ディレクトリを消す。
    """
    env = dict(os.environ)
    data_root = bootstrap_data_root(env)
    try:
        with hold_data_root(data_root):
            _serve(env, data_root)
    except AlreadyRunning as exc:
        # ログの設定より前に落ちうるので、標準エラーへ直接書く。
        print(f"起動しない: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


def _serve(env: dict[str, str], data_root: Path) -> None:
    # BIND_HOST / HTTP_PORT / LOG_LEVEL は RESTART 層で、DB にも保存できる。
    # env だけで決めると、画面で変えて再起動しても反映されない。
    database = Database(data_root / "var" / "mediaferry.sqlite3")
    conn = database.connect()
    try:
        apply_migrations(conn)
        settings = SettingsService(conn, env).snapshot()
    finally:
        conn.close()

    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(create_app(env=env), host=settings.bind_host, port=settings.http_port)


if __name__ == "__main__":
    sys.exit(main())
