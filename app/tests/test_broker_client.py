import contextlib
import errno
import os
import socket
import threading

import pytest

from mediaferry.adapters.broker_client import BrokerClient, BrokerError
from mediaferry_protocol.errors import ProtocolError
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
    with pytest.raises((OSError, ProtocolError)):
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
    with pytest.raises((OSError, ProtocolError)):
        os.listdir(dirfd)


class BrokerHarness:
    """実ソケットで待ち受けて、mountd の `handle_connection` へ渡す土台.

    再接続の試験には「同じパスへ繋ぎ直せること」と「既存の接続を落とせること」が
    要る。`socketpair` の fixture では前者を、`serve_forever` では後者を試せない
    （待ち受けソケットを閉じても、accept 済みの接続は生き続ける）。
    """

    def __init__(self, path, server):
        self._server = server
        self._accepted: list[socket.socket] = []
        self._dropped: list[socket.socket] = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._sock.bind(str(path))
        self._sock.listen(8)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._accepted.append(conn)
            threading.Thread(
                target=self._server.handle_connection, args=(conn,), daemon=True
            ).start()

    def drop_connections(self):
        """mountd の再起動に相当する. 受け付け済みの接続を落とす.

        `shutdown` だけにして `close` はしない。閉じてしまうと、まだ
        `handle_connection` が握っている fd が消えて EBADF になる（試験の
        土台が出すノイズであって、被試験側の挙動ではない）。
        """
        for conn in self._accepted:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
        self._dropped.extend(self._accepted)
        self._accepted.clear()

    def close(self):
        self.drop_connections()
        with contextlib.suppress(OSError):
            self._sock.close()
        self._thread.join(timeout=5)
        for conn in self._dropped:
            with contextlib.suppress(OSError):
                conn.close()


@pytest.fixture
def harness(tmp_path):
    target = tmp_path / "mnt"
    target.mkdir()
    server = BrokerServer(
        socket_path=tmp_path / "unused.sock",
        mount_manager=FakeMountManager(target),
        lister=lambda: [a_volume()],
        allowed_uids=None,
    )
    h = BrokerHarness(tmp_path / "broker.sock", server)
    yield h, tmp_path / "broker.sock"
    h.close()


def test_list_volumes_reconnects_after_the_broker_drops_the_connection(harness):
    """mountd を再起動しても、アプリを再起動せずに戻る.

    常時ポーリングする VolumeWatcher を足すと、この穴は 1 回の失敗ではなく
    恒久的な故障になる。
    """
    h, path = harness
    client = BrokerClient(path)
    try:
        assert len(client.list_volumes()) == 1
        h.drop_connections()
        assert len(client.list_volumes()) == 1
    finally:
        client.close()


def test_open_volume_is_not_retried_after_the_connection_breaks(harness):
    """fd を伴う要求は再送しない.

    再送すると mountd 側で 2 度目のマウントが起き、1 つ目の handle が誰にも
    閉じられずに残る。
    """
    h, path = harness
    client = BrokerClient(path)
    try:
        volume = client.list_volumes()[0]
        h.drop_connections()
        with pytest.raises((OSError, ProtocolError)):
            client.open_volume(volume)
    finally:
        client.close()


def test_a_client_made_from_a_socket_does_not_reconnect(harness):
    """`from_socket` は接続先のパスを知らないので繋ぎ直せない.

    **「例外が出た」だけでは足りない。** 繋ぎ直そうとしても、知らないパスへの
    connect が ENOENT になって同じ型の例外が出る。壊れた接続そのものの
    エラーが上がってくること（＝試みてすらいないこと）を見る。
    """
    h, path = harness
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    sock.connect(str(path))
    client = BrokerClient.from_socket(sock)
    try:
        assert len(client.list_volumes()) == 1
        h.drop_connections()
        with pytest.raises((OSError, ProtocolError)) as caught:
            client.list_volumes()
        assert getattr(caught.value, "errno", None) != errno.ENOENT, (
            "存在しないパスへ繋ぎ直そうとしている"
        )
    finally:
        client.close()


def test_reconnecting_many_times_does_not_leak_file_descriptors(harness):
    """旧 socket を必ず閉じる. 閉じ忘れると tick ごとに fd が増える."""
    h, path = harness
    client = BrokerClient(path)
    try:
        client.list_volumes()
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(20):
            h.drop_connections()
            client.list_volumes()
        after = len(os.listdir("/proc/self/fd"))
        assert after - before <= 2, f"fd が {before} から {after} へ増えた"
    finally:
        client.close()


def test_a_closed_client_does_not_reconnect(harness):
    """停止は接続を閉じて解く. その OSError を再接続が拾うと、停止が効かない."""
    h, path = harness
    client = BrokerClient(path)
    client.list_volumes()
    client.close()
    with pytest.raises((OSError, ProtocolError)):
        client.list_volumes()


def test_closing_unblocks_a_call_that_is_waiting_for_a_reply(tmp_path):
    """応答を返さないブローカー相手でも、close() が待っている呼び出しを解く.

    停止はこれで成立する。`close` だけで `shutdown` を省くと、待っている
    `recv` が解ける保証が弱い。
    """
    path = tmp_path / "silent.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.bind(str(path))
    listener.listen(1)
    accepted = []

    def accept_and_stay_silent():
        conn, _ = listener.accept()
        accepted.append(conn)  # 応答を返さずに握ったままにする

    threading.Thread(target=accept_and_stay_silent, daemon=True).start()
    client = BrokerClient(path)
    outcome = []

    def call():
        try:
            client.list_volumes()
            outcome.append("returned")
        except BaseException as exc:  # noqa: BLE001 - 型ではなく「降りたこと」を見る
            outcome.append(type(exc).__name__)

    caller = threading.Thread(target=call, daemon=True)
    caller.start()
    # 呼び出しが recv で待ちに入るのを待つ
    caller.join(timeout=0.5)
    assert caller.is_alive(), "応答が無いのに戻ってきた（試験の前提が崩れている）"

    client.close()
    caller.join(timeout=5)
    assert not caller.is_alive(), "close() しても待ち続けている"
    assert outcome and outcome[0] != "returned"

    for conn in accepted:
        with contextlib.suppress(OSError):
            conn.close()
    with contextlib.suppress(OSError):
        listener.close()
