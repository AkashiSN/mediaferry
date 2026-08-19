"""E2E のためにアプリ一式を立ち上げ、URL を出して待つ（Playwright から使う）.

    uv run python -m tests.system.serve <状態ディレクトリ> [パスワード] [--timezone-from-db]

**mock ではなく実物を立てる。** ブローカーは実ソケット（マウント部分だけ fake）、
Immich は `fake_immich.py` を 2 台、アプリはサブプロセスの本物。
"""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

from .harness import system_app


def main() -> int:
    target = Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    rest = sys.argv[2:]
    # `--timezone-from-db` を付けると DEFAULT_TIMEZONE を env に置かない。env にあると
    # `locked` になって画面から変えられず、再計算の受け入れが経路として試せない。
    from_db = "--timezone-from-db" in rest
    password = next((arg for arg in rest if not arg.startswith("--")), None)
    with system_app(
        target, password=password, default_timezone=None if from_db else "Asia/Tokyo"
    ) as app:
        # Playwright 側はこの 1 行を読んで接続先を知る。
        print(
            json.dumps(
                {"url": app.url, "immich": app.immich_urls, "data_root": str(app.data_root)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
