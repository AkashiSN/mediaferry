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

from mediaferry_protocol.messages import UsbInfo, VolumeInfo
from mountd.server import BrokerServer

from ..conftest import FakeMountManager
from ..exif_fixtures import a_jpeg_with
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
    """取り込みの対象になるカード.

    **2 本入れる。** 1 本だと結合の候補が出ず、画面から結合を試せない
    （§9.7 の検出は連続した分割録画を探す）。
    """
    card = root / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    for index, name in enumerate(
        ("DJI_20260817143000_0001_D.MP4", "DJI_20260817143100_0002_D.MP4")
    ):
        (card / "DCIM" / "DJI_001" / name).write_bytes(bytes([65 + index]) * 100)
    return card


def _a_canon_card(root: Path) -> Path:
    """Canon EOS 風の合成カード.

    **実カードは手元に無い**（§1 の「残っていること」3）ので、仕様と
    `canon-eos` の `require` から組み立てる。EXIF を持たせるのは、
    `timestamp.source: exif` が公開済みファイルから読めることを通すため。
    """
    card = root / "canon"
    (card / "DCIM" / "100CANON").mkdir(parents=True)
    for index, name in enumerate(("IMG_0001.JPG", "IMG_0002.JPG")):
        (card / "DCIM" / "100CANON" / name).write_bytes(
            a_jpeg_with(f"2026:02:0{index + 3} 04:05:06".encode())
        )
    return card


class _Cards(FakeMountManager):
    """`volume_key` ごとに別のディレクトリを見せる（2 枚同時に挿してある状態）."""

    def __init__(self, by_key: dict[str, Path]) -> None:
        super().__init__(next(iter(by_key.values())))
        self._by_key = by_key

    def mount(self, volume, expect, verify):  # noqa: ANN001, ANN201
        self.target = self._by_key[volume.volume_key]
        return super().mount(volume, expect, verify)


@contextmanager
def system_app(
    tmp_path: Path,
    *,
    password: str | None = None,
    immich_count: int = 2,
    default_timezone: str | None = "Asia/Tokyo",
) -> Iterator[SystemApp]:
    """一式を立ち上げて、終わったら必ず片付ける."""
    data_root = tmp_path / "data"
    # §7 のレイアウト。staging は library と同じファイルシステムに要る。
    for name in ("library", "derived", "staging", "work", "var"):
        (data_root / name).mkdir(parents=True)
    card = _a_card(tmp_path)
    canon = _a_canon_card(tmp_path)

    servers = [FakeImmich() for _ in range(immich_count)]
    for server in servers:
        server.start()

    socket_path = tmp_path / "run" / "broker.sock"
    # **カードを 2 枚差してある状態にする。** 列挙が空だとデバイスの画面から
    # 先へ進めず（取り込みも結合も送信も始まらない）、1 枚だと「複数デバイスを
    # 独立に扱える」（Phase 5 §20）を受け入れの経路に載せられない。
    broker = BrokerServer(
        socket_path=socket_path,
        mount_manager=_Cards({"8:160": card, "8:176": canon}),
        lister=lambda: [_a_volume(), _a_canon_volume()],
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
        # 転送先を作るのに要る（§12.3）。テスト用の使い捨ての鍵。
        "MEDIAFERRY_SECRET_KEY": "0" * 43 + "=",
    }
    # **ビルド済みの画面があれば一緒に配る。** E2E はブラウザから操作するので、
    # 資産が無いと画面が出ない（API だけのテストでは無くても困らない）。
    built = Path(__file__).resolve().parents[3] / "web" / "dist"
    if (built / "index.html").is_file():
        env["MEDIAFERRY_WEB_ROOT"] = str(built)
    if default_timezone is not None:
        # **`None` を渡すと env に置かない。** env にあると `locked` になり、
        # 画面から変えられない —— 再計算の受け入れは「設定を変えてから直す」
        # 筋書きなので、DB 側の設定として持てる状態が要る（§12.2）。
        env["MEDIAFERRY_DEFAULT_TIMEZONE"] = default_timezone
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


def _a_volume() -> VolumeInfo:
    """DJI のカードに見えるボリューム（判定はプロファイルが行う）."""
    return VolumeInfo(
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


def _a_canon_volume() -> VolumeInfo:
    """カードリーダー越しの Canon（**USB ID はリーダーのもの**なので手がかりにしない）."""
    return VolumeInfo(
        volume_key="8:176",
        device_node="/dev/sdl",
        major=8,
        minor=176,
        sysfs_path="/sys/y",
        fs_type="exfat",
        fs_uuid="1234-ABCD",
        fs_label="EOS_DIGITAL",
        size_bytes=32_000_000_000,
        usb=UsbInfo(
            vendor_id="05e3",
            product_id="0749",
            product="USB Card Reader",
            serial="0000000000",
        ),
        broker_epoch="",
        generation=1,
    )


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
