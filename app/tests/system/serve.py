"""E2E のためにアプリ一式を立ち上げ、URL を出して待つ（Playwright から使う）.

    uv run python -m tests.system.serve <状態ディレクトリ>

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
    password = sys.argv[2] if len(sys.argv) > 2 else None
    with system_app(target, password=password) as app:
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
