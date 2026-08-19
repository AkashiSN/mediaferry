import os
import socket
import threading

import pytest
from fastapi.testclient import TestClient

from mediaferry.adapters.broker_client import BrokerClient
from mediaferry.api.app import create_app
from mediaferry.db.connection import Database
from mediaferry.db.migrate import apply_migrations
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

    @property
    def mounts(self) -> int:
        """これまでに開いた回数. 「判定のたびにマウントする」代償を測る."""
        return self._n

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

    テストはこのリストを書き換えて抜き差しを表す。クライアント側だけを
    差し替えると、サーバが知らないボリュームを開こうとして
    `unknown_volume` になる（実機では起きない状態）。
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
def broker_factory(mount_manager, tmp_path, volumes):
    """**呼ぶたびに新しい接続を作る。** サーバは 1 つで、接続だけを増やす。

    実物と同じく、handle は発行した接続に束縛される（§11）。同じ client を
    使い回すと「VolumeWatcher は専用のブローカー接続を持つ」という性質を
    テストで確かめられない —— watcher の停止が取り込みの相手を切る経路が
    そのまま素通りする。
    """
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        mount_manager=mount_manager,
        lister=lambda: list(volumes),
        allowed_uids=None,
    )
    made = []

    def make() -> BrokerClient:
        client_sock, server_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        thread = threading.Thread(target=server.handle_connection, args=(server_sock,), daemon=True)
        thread.start()
        client = BrokerClient.from_socket(client_sock)
        made.append((client, thread))
        return client

    yield make
    for client, thread in made:
        client.close()
        thread.join(timeout=5)


@pytest.fixture
def broker(broker_factory):
    return broker_factory()


@pytest.fixture
def anyio_backend():
    """JobRunner は asyncio ワーカーなので、anyio の trio 側は使わない."""
    return "asyncio"


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


@pytest.fixture
def client(data_root, broker_factory, monkeypatch):
    """起動時に migration とビルトインの同期、reconciliation が走る."""
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    app = create_app(broker_factory=broker_factory)
    # **ブラウザと同じ形で叩く。** Host はループバック（rebinding 対策で名前は
    # 許可制。§14）、状態を変える要求には二重送信 Cookie の対を付ける。
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        token = "test-csrf-token"  # noqa: S105 - テスト用の見せかけの値
        client.cookies.set("XSRF-TOKEN", token)
        client.headers["X-CSRF-Token"] = token
        yield client


@pytest.fixture
def immich():
    """ループバックで listen する fake Immich. テストごとに新しいポート."""
    from .fake_immich import FakeImmich

    server = FakeImmich()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def secured_app(data_root, broker_factory, monkeypatch):
    """認証を有効にしたアプリと、その CSRF トークン."""
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MEDIAFERRY_AUTH_PASSWORD", "correct horse")
    app = create_app(broker_factory=broker_factory)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        token = client.get("/api/auth/session").cookies["XSRF-TOKEN"]
        client.headers["X-CSRF-Token"] = token
        yield client, token
