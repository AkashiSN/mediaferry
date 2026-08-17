import os
import socket
import threading

import pytest

from mediaferry.adapters.broker_client import BrokerClient, BrokerError
from mediaferry_protocol.messages import UsbInfo, VolumeInfo
from mountd.server import BrokerServer


class FakeMountManager:
    """マウントはせず、用意したディレクトリの dirfd を返す."""

    def __init__(self, target):
        self.target = target
        self.released = []
        self._open = {}
        self._n = 0

    def mount(self, volume, expect, verify):
        self._n += 1
        handle = f"h{self._n}"
        verify()
        fd = os.open(self.target, os.O_RDONLY | os.O_DIRECTORY)
        self._open[handle] = fd
        return handle, fd

    def release(self, handle):
        fd = self._open.pop(handle, None)
        if fd is not None:
            os.close(fd)
        self.released.append(handle)

    def release_all(self):
        for handle in list(self._open):
            self.release(handle)


def a_volume() -> VolumeInfo:
    return VolumeInfo(
        volume_key="8:160",
        device_node="/dev/sdk",
        major=8,
        minor=160,
        sysfs_path="/sys/x",
        fs_type="exfat",
        fs_uuid="26B1-2FD6",
        fs_label="SD_Card",
        size_bytes=1024,
        usb=UsbInfo(vendor_id="2ca3", product_id="0020", product="OsmoPocket4-ABC", serial="X"),
        broker_epoch="",
        generation=0,
    )


@pytest.fixture
def connected(tmp_path):
    target = tmp_path / "mnt"
    target.mkdir()
    (target / "DCIM").mkdir()
    (target / "DCIM" / "A.MP4").write_bytes(b"payload")

    mgr = FakeMountManager(target)
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        mount_manager=mgr,
        lister=lambda: [a_volume()],
        allowed_uids=None,
    )
    client_sock, server_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    thread = threading.Thread(target=server.handle_connection, args=(server_sock,), daemon=True)
    thread.start()
    client = BrokerClient.from_socket(client_sock)
    yield client, mgr, server
    client.close()
    thread.join(timeout=5)


def test_list_volumes_parses_into_dataclasses(connected):
    client, _, server = connected
    volumes = client.list_volumes()
    assert len(volumes) == 1
    assert volumes[0].fs_label == "SD_Card"
    assert volumes[0].usb.vendor_id == "2ca3"
    assert volumes[0].broker_epoch == server.broker_epoch


def test_open_volume_yields_a_readable_dirfd(connected):
    client, _, _ = connected
    volume = client.list_volumes()[0]
    with client.open_volume(volume) as handle:
        assert os.listdir(handle.dirfd) == ["DCIM"]
        sub = os.open("DCIM", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handle.dirfd)
        try:
            fd = os.open("A.MP4", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sub)
            with os.fdopen(fd, "rb") as f:
                assert f.read() == b"payload"
        finally:
            os.close(sub)


def test_context_manager_closes_the_volume(connected):
    client, mgr, _ = connected
    volume = client.list_volumes()[0]
    with client.open_volume(volume) as handle:
        name = handle.handle
    assert mgr.released == [name]


def test_open_volume_sends_the_broker_epoch(connected):
    """epoch が欠けると mountd 側が bad_request で弾く."""
    client, _, _ = connected
    volume = client.list_volumes()[0]
    with client.open_volume(volume):
        pass  # 例外なく開けたことが epoch を送れている証拠


def test_broker_error_carries_the_code(connected):
    client, _, _ = connected
    bogus = a_volume()
    bogus = VolumeInfo(**{**bogus.__dict__, "volume_key": "99:99"})
    with pytest.raises(BrokerError) as exc:
        client.open_volume(bogus)
    assert exc.value.code == "unknown_volume"


def test_close_volume_is_idempotent(connected):
    """整数 fd を二度閉じると、その間に OS が同じ番号を別ファイルへ
    割り当てていた場合に無関係な fd を閉じてしまう."""
    client, mgr, _ = connected
    volume = client.list_volumes()[0]
    handle = client.open_volume(volume)
    client.close_volume(handle)
    assert handle.closed
    assert handle.dirfd == -1
    client.close_volume(handle)  # 二度目は何もしない
    assert mgr.released == [handle.handle]


def test_client_close_releases_open_volume_fds(connected):
    """例外で context manager を抜けなかった場合でも fd を残さない.

    残すと detached mount がプロセス終了まで生き続け、長寿命の app では
    USB デバイスとマウント資源が溜まる。
    """
    client, _, _ = connected
    volume = client.list_volumes()[0]
    handle = client.open_volume(volume)
    dirfd = handle.dirfd
    assert os.listdir(dirfd) == ["DCIM"]
    client.close()
    assert handle.closed
    with pytest.raises(OSError):
        os.listdir(dirfd)


def test_close_volume_closes_the_local_fd_even_if_the_server_call_fails(connected):
    """サーバへの通知が失敗してもローカル fd は必ず閉じる.

    ローカル fd を握ったままにすると detached mount が生き続ける。
    サーバ側は接続断の後始末で回収されるので、こちらが待つ理由がない。
    """
    client, _, _ = connected
    volume = client.list_volumes()[0]
    handle = client.open_volume(volume)
    dirfd = handle.dirfd

    def boom(*args, **kwargs):
        raise ConnectionResetError("broker went away")

    client._call = boom  # noqa: SLF001
    with pytest.raises(ConnectionResetError):
        client.close_volume(handle)
    assert handle.closed
    with pytest.raises(OSError):
        os.listdir(dirfd)
