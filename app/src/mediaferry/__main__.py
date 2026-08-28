"""起動エントリ.

BIND_HOST の既定は loopback。LAN へ出すなら AUTH_PASSWORD を設定する。
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
