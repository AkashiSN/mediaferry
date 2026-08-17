import os
import socket
import threading
import time

import pytest

from mediaferry_protocol.messages import (
    REQ_CLOSE_VOLUME,
    REQ_LIST_VOLUMES,
    REQ_OPEN_VOLUME,
    UsbInfo,
    VolumeInfo,
)
from mediaferry_protocol.wire import recv_message, send_message
from mountd.server import BrokerServer


class FakeMountManager:
    """実際にはマウントせず、用意したディレクトリの dirfd を返す."""

    def __init__(self, target) -> None:
        self.target = target
        self.released: list[str] = []
        self.fail_release = False
        self._open: dict[str, int] = {}
        self._n = 0

    def mount(self, volume, expect, verify):
        self._n += 1
        handle = f"h{self._n}"
        verify()  # 実物と同じく post-mount 検証を呼ぶ
        fd = os.open(self.target, os.O_RDONLY | os.O_DIRECTORY)
        self._open[handle] = fd
        return handle, fd

    def release(self, handle):
        if self.fail_release:
            raise RuntimeError("cannot release")
        fd = self._open.pop(handle, None)
        if fd is not None:
            os.close(fd)
        self.released.append(handle)

    def release_all(self):
        for handle in list(self._open):
            self.release(handle)


def a_volume() -> VolumeInfo:
    """lister が返す素の観測値。epoch と generation はサーバが刻印する."""
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
        usb=UsbInfo(vendor_id="2ca3", product_id="0020", serial="X"),
        broker_epoch="",
        generation=0,
    )


def expect_from_listed(v: dict) -> dict:
    return {
        "major": v["major"],
        "minor": v["minor"],
        "fs_uuid": v["fs_uuid"],
        "fs_type": v["fs_type"],
        "broker_epoch": v["broker_epoch"],
        "generation": v["generation"],
    }


def make_server(tmp_path, **over):
    target = tmp_path / "mnt"
    target.mkdir(parents=True, exist_ok=True)
    (target / "DCIM").mkdir(exist_ok=True)
    (target / "DCIM" / "A.MP4").write_bytes(b"data")
    kwargs = {
        "socket_path": tmp_path / "broker.sock",
        "mount_manager": FakeMountManager(target),
        "lister": lambda: [a_volume()],
        "allowed_uids": None,
    }
    kwargs.update(over)
    return BrokerServer(**kwargs), kwargs["mount_manager"], target


def start(server):
    client, srv_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    thread = threading.Thread(target=server.handle_connection, args=(srv_side,), daemon=True)
    thread.start()
    return client, thread


@pytest.fixture
def served(tmp_path):
    server, mgr, target = make_server(tmp_path)
    client, thread = start(server)
    yield server, client, target
    client.close()
    thread.join(timeout=5)


def _open_first_volume(client):
    send_message(client, {"type": REQ_LIST_VOLUMES})
    v = recv_message(client)[0]["volumes"][0]
    send_message(
        client,
        {"type": REQ_OPEN_VOLUME, "volume_key": v["volume_key"], "expect": expect_from_listed(v)},
    )
    reply, fds = recv_message(client)
    for fd in fds:
        os.close(fd)
    return reply


def test_list_volumes_returns_the_volumes(served):
    _, client, _ = served
    send_message(client, {"type": REQ_LIST_VOLUMES})
    reply, fds = recv_message(client)
    assert reply["ok"] is True
    assert fds == []
    assert reply["volumes"][0]["device_node"] == "/dev/sdk"


def test_listed_volumes_carry_the_broker_epoch(served):
    server, client, _ = served
    send_message(client, {"type": REQ_LIST_VOLUMES})
    v = recv_message(client)[0]["volumes"][0]
    assert v["broker_epoch"] == server.broker_epoch
    assert v["broker_epoch"] != ""


def test_each_server_gets_a_distinct_epoch(tmp_path):
    """mountd 再起動をまたいだ古い expect を弾けるようにするため."""
    s1, _, _ = make_server(tmp_path / "a")
    s2, _, _ = make_server(tmp_path / "b")
    assert s1.broker_epoch != s2.broker_epoch


def test_open_volume_returns_a_usable_dirfd(served):
    _, client, _ = served
    send_message(client, {"type": REQ_LIST_VOLUMES})
    v = recv_message(client)[0]["volumes"][0]
    send_message(
        client,
        {"type": REQ_OPEN_VOLUME, "volume_key": v["volume_key"], "expect": expect_from_listed(v)},
    )
    reply, fds = recv_message(client)
    assert reply["ok"] is True
    assert len(fds) == 1
    try:
        assert os.listdir(fds[0]) == ["DCIM"]
    finally:
        os.close(fds[0])


def test_generation_advances_only_when_the_volume_set_changes(tmp_path):
    """世代が呼び出しごとに進むと同一性チェックが常に失敗し、
    クライアント任せだと常に成立してしまう。集合の変化に紐づくことを確かめる。"""
    current = [a_volume()]
    server, _, _ = make_server(tmp_path, lister=lambda: list(current))
    client, thread = start(server)
    try:
        send_message(client, {"type": REQ_LIST_VOLUMES})
        first = recv_message(client)[0]["volumes"][0]["generation"]
        send_message(client, {"type": REQ_LIST_VOLUMES})
        second = recv_message(client)[0]["volumes"][0]["generation"]
        assert first == second, "集合が変わっていないのに世代が進んだ"

        current.clear()  # デバイスが抜かれた
        send_message(client, {"type": REQ_LIST_VOLUMES})
        assert recv_message(client)[0]["volumes"] == []

        current.append(a_volume())  # 別のカードが挿された
        send_message(client, {"type": REQ_LIST_VOLUMES})
        third = recv_message(client)[0]["volumes"][0]["generation"]
        assert third > first, "抜き挿ししたのに世代が進んでいない"
    finally:
        client.close()
        thread.join(timeout=5)


def test_concurrent_observation_never_travels_back_in_time(tmp_path):
    """列挙をロックの外でやると、古い観測に新しい世代が刻印されうる.

    列挙の途中で別スレッドに追い越させ、返ってきた (集合, 世代) の対応が
    常に整合していることを確かめる。
    """
    import itertools

    states = [[a_volume()], [], [a_volume()], []]
    counter = itertools.count()
    barrier_hit = threading.Event()

    def slow_lister():
        i = next(counter)
        state = states[i % len(states)]
        if i == 0:
            barrier_hit.set()
            time.sleep(0.05)  # 追い越させる
        return list(state)

    server, _, _ = make_server(tmp_path, lister=slow_lister)

    seen: list[tuple[int, int]] = []
    lock = threading.Lock()

    def worker():
        client, thread = start(server)
        try:
            send_message(client, {"type": REQ_LIST_VOLUMES})
            reply = recv_message(client)[0]
            gen = reply["volumes"][0]["generation"] if reply["volumes"] else None
            with lock:
                seen.append((len(reply["volumes"]), gen))
        finally:
            client.close()
            thread.join(timeout=5)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # 同じ世代なら必ず同じ集合サイズでなければならない
    by_gen: dict[int, int] = {}
    for size, gen in seen:
        if gen is None:
            continue
        assert by_gen.setdefault(gen, size) == size, "同じ世代に異なる観測が刻印された"


def test_peer_with_a_disallowed_uid_is_refused(tmp_path):
    server, _, _ = make_server(tmp_path, allowed_uids=frozenset({os.getuid() + 1}))
    client, thread = start(server)
    try:
        reply, _ = recv_message(client)
        assert reply["ok"] is False
        assert reply["error"] == "unauthorized"
    finally:
        client.close()
        thread.join(timeout=5)


def test_holding_a_handle_disables_the_idle_timeout(tmp_path):
    """本番の app は dirfd を受け取ってから何十分も RPC を送らない。

    その間にタイムアウトして finally がハンドルを解放すると、大きいファイルの
    取り込みだけが途中で壊れる。短いプレビューしか流さないスパイクでは
    露見しないので、ここで固定する。
    """
    server, mgr, _ = make_server(tmp_path, idle_timeout=0.05)
    client, thread = start(server)
    try:
        reply = _open_first_volume(client)
        assert reply["ok"] is True
        time.sleep(0.4)  # idle_timeout の何倍も待つ
        assert mgr.released == [], "ハンドル保有中に解放された"
        send_message(client, {"type": REQ_LIST_VOLUMES})
        assert recv_message(client)[0]["ok"] is True, "接続が切れている"
    finally:
        client.close()
        thread.join(timeout=5)


def test_idle_connection_without_handles_is_dropped(tmp_path):
    server, _, _ = make_server(tmp_path, idle_timeout=0.05)
    client, thread = start(server)
    try:
        thread.join(timeout=5)
        assert not thread.is_alive(), "ハンドル未保有の接続が回収されていない"
    finally:
        client.close()


def test_requests_carrying_fds_are_rejected(served, tmp_path):
    _, client, _ = served
    dirfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        socket.send_fds(client, [b'{"type":"list_volumes"}'], [dirfd])
    finally:
        os.close(dirfd)
    reply, _ = recv_message(client)
    assert reply["ok"] is False
    assert reply["error"] == "bad_request"


def test_failed_release_is_reported(tmp_path):
    server, mgr, _ = make_server(tmp_path)
    client, thread = start(server)
    try:
        handle = _open_first_volume(client)["handle"]
        mgr.fail_release = True
        send_message(client, {"type": REQ_CLOSE_VOLUME, "handle": handle})
        reply, _ = recv_message(client)
        assert reply["error"] == "release_failed"
    finally:
        mgr.fail_release = False
        client.close()
        thread.join(timeout=5)


def test_subscribe_is_not_accepted_in_phase_0(served):
    """イベント配信は専用接続と決めたが Phase 0 では未実装."""
    _, client, _ = served
    send_message(client, {"type": "subscribe"})
    reply, _ = recv_message(client)
    assert reply["error"] == "bad_request"


def test_unknown_request_type_is_rejected(served):
    _, client, _ = served
    send_message(client, {"type": "rm_rf"})
    reply, _ = recv_message(client)
    assert reply["ok"] is False
    assert reply["error"] == "bad_request"


def test_missing_type_is_rejected(served):
    _, client, _ = served
    send_message(client, {})
    reply, _ = recv_message(client)
    assert reply["error"] == "bad_request"


def test_unknown_volume_key_is_rejected(served):
    _, client, _ = served
    send_message(
        client,
        {
            "type": REQ_OPEN_VOLUME,
            "volume_key": "99:99",
            "expect": {
                "major": 99,
                "minor": 99,
                "fs_uuid": None,
                "fs_type": "exfat",
                "broker_epoch": "whatever",
                "generation": 1,
            },
        },
    )
    reply, _ = recv_message(client)
    assert reply["error"] == "unknown_volume"


def test_close_volume_releases(served):
    server, client, _ = served
    handle = _open_first_volume(client)["handle"]
    send_message(client, {"type": REQ_CLOSE_VOLUME, "handle": handle})
    reply, _ = recv_message(client)
    assert reply["ok"] is True
    assert server.mount_manager.released == [handle]


def test_closing_a_handle_from_another_connection_is_refused(tmp_path):
    """handle は発行した接続に束縛される."""
    server, _, _ = make_server(tmp_path)
    c1, t1 = start(server)
    c2, t2 = start(server)
    try:
        handle = _open_first_volume(c1)["handle"]
        send_message(c2, {"type": REQ_CLOSE_VOLUME, "handle": handle})
        reply, _ = recv_message(c2)
        assert reply["error"] == "unknown_handle"
    finally:
        c1.close()
        c2.close()
        t1.join(timeout=5)
        t2.join(timeout=5)


def test_disconnect_releases_handles(tmp_path):
    server, mgr, _ = make_server(tmp_path)
    client, thread = start(server)
    _open_first_volume(client)
    client.close()
    thread.join(timeout=5)
    assert len(mgr.released) == 1
