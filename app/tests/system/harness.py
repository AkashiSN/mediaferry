"""実プロセスの mediaferry を立ち上げる（E2E の土台）.

使い方::

    with system_app() as app:
        httpx.get(f"{app.url}/api/health")

**サブプロセスで起動する。** 同一プロセスの `TestClient` では、静的配信・Cookie・
SSE・ジョブの worker が本番と同じ経路を通らない。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from mountd.server import BrokerServer

from ..conftest import FakeMountManager
from ..fake_immich import FakeImmich

# 起動を待つ上限。CI の遅い環境でも足りる長さにする。
STARTUP_TIMEOUT_SECONDS = 30.0


@dataclass
class SystemApp:
    """立ち上げた一式への入り口."""

    url: str
    data_root: Path
    immich_urls: list[str]
    password: str | None
    process: subprocess.Popen = field(repr=False)

    def client(self) -> httpx.Client:
        """ブラウザと同じ形で叩くクライアント（Host はループバック）."""
        return httpx.Client(base_url=self.url, timeout=30.0, follow_redirects=False)


def _free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _a_card(root: Path) -> Path:
    """取り込みの対象になる最小のカード（実 ffmpeg は使わない）."""
    card = root / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").write_bytes(b"a" * 100)
    return card


@contextmanager
def system_app(
    tmp_path: Path,
    *,
    password: str | None = None,
    immich_count: int = 2,
) -> Iterator[SystemApp]:
    """一式を立ち上げて、終わったら必ず片付ける."""
    data_root = tmp_path / "data"
    # §7 のレイアウト。staging は library と同じファイルシステムに要る。
    for name in ("library", "derived", "staging", "work", "var"):
        (data_root / name).mkdir(parents=True)
    card = _a_card(tmp_path)

    servers = [FakeImmich() for _ in range(immich_count)]
    for server in servers:
        server.start()

    socket_path = tmp_path / "run" / "broker.sock"
    broker = BrokerServer(
        socket_path=socket_path,
        mount_manager=FakeMountManager(card),
        lister=lambda: [],
        allowed_uids=None,
        idle_timeout=None,
    )
    broker_thread = threading.Thread(target=broker.serve_forever, daemon=True)
    broker_thread.start()

    port = _free_port()
    env = {
        **os.environ,
        "MEDIAFERRY_DATA_ROOT": str(data_root),
        "MEDIAFERRY_BROKER_SOCKET": str(socket_path),
        "MEDIAFERRY_BIND_HOST": "127.0.0.1",
        "MEDIAFERRY_HTTP_PORT": str(port),
        "MEDIAFERRY_DEFAULT_TIMEZONE": "Asia/Tokyo",
        # 転送先を作るのに要る（§12.3）。テスト用の使い捨ての鍵。
        "MEDIAFERRY_SECRET_KEY": "0" * 43 + "=",
    }
    if password is not None:
        env["MEDIAFERRY_AUTH_PASSWORD"] = password

    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "mediaferry"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_ready(url, process)
        yield SystemApp(
            url=url,
            data_root=data_root,
            immich_urls=[server.url for server in servers],
            password=password,
            process=process,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        for server in servers:
            server.stop()


def _wait_until_ready(url: str, process: subprocess.Popen) -> None:
    """`/health` が返るまで待つ. **落ちていたら出力を添えて失敗させる。**"""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"起動に失敗した（終了コード {process.returncode}）\n{output}")
        try:
            response = httpx.get(f"{url}/api/health", timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.1)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.1)
    raise RuntimeError("起動を待ち切れなかった")
