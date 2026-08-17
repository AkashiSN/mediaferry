import json
import os
import socket

import pytest

from mediaferry_protocol.errors import ConnectionClosed, MessageTooLarge, ProtocolError
from mediaferry_protocol.wire import MAX_MESSAGE_BYTES, recv_message, send_message


@pytest.fixture
def pair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    yield a, b
    a.close()
    b.close()


def test_roundtrip_payload(pair):
    a, b = pair
    send_message(a, {"type": "ping", "n": 1})
    payload, fds = recv_message(b)
    assert payload == {"type": "ping", "n": 1}
    assert fds == []


def test_message_boundaries_are_preserved(pair):
    a, b = pair
    send_message(a, {"i": 1})
    send_message(a, {"i": 2})
    assert recv_message(b)[0] == {"i": 1}
    assert recv_message(b)[0] == {"i": 2}


def test_sending_oversized_payload_raises(pair):
    a, _ = pair
    with pytest.raises(MessageTooLarge):
        send_message(a, {"blob": "x" * (MAX_MESSAGE_BYTES + 1)})


def test_closed_peer_raises(pair):
    a, b = pair
    a.close()
    with pytest.raises(ConnectionClosed):
        recv_message(b)


def test_dirfd_survives_transfer_and_is_usable(pair, tmp_path):
    """dirfd を渡した先で os.listdir / os.open(dir_fd=) が使えることを確かめる。

    これが Phase 0 の中心的な前提。fd 経由でボリュームを読む設計が
    成立するかどうかがここで決まる。
    """
    a, b = pair
    (tmp_path / "DCIM").mkdir()
    (tmp_path / "DCIM" / "A.MP4").write_bytes(b"hello")

    dirfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        send_message(a, {"type": "opened"}, fds=[dirfd])
    finally:
        os.close(dirfd)

    payload, fds = recv_message(b)
    assert payload == {"type": "opened"}
    assert len(fds) == 1
    received = fds[0]
    try:
        assert os.listdir(received) == ["DCIM"]
        sub = os.open("DCIM", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=received)
        try:
            assert os.listdir(sub) == ["A.MP4"]
            fd = os.open("A.MP4", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sub)
            with os.fdopen(fd, "rb") as f:
                assert f.read() == b"hello"
        finally:
            os.close(sub)
    finally:
        os.close(received)


def test_listdir_does_not_consume_the_dirfd(pair, tmp_path):
    """同じ dirfd で 2 回列挙できること。Scanner が繰り返し走査するため。"""
    a, b = pair
    (tmp_path / "X").mkdir()
    dirfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        send_message(a, {}, fds=[dirfd])
    finally:
        os.close(dirfd)
    _, fds = recv_message(b)
    received = fds[0]
    try:
        assert os.listdir(received) == ["X"]
        assert os.listdir(received) == ["X"]
    finally:
        os.close(received)


def test_payload_is_utf8_json(pair):
    a, b = pair
    send_message(a, {"label": "日本語ラベル"})
    payload, _ = recv_message(b)
    assert payload["label"] == "日本語ラベル"
    assert json.dumps(payload)


def test_invalid_utf8_is_normalised_to_protocol_error(pair):
    """json.loads は bytes の不正 UTF-8 に UnicodeDecodeError を投げる。

    JSONDecodeError だけを catch していると、呼び出し側の except を素通りして
    サーバのループを落とす。ProtocolError に正規化されることを確かめる。
    """
    a, b = pair
    a.sendall(b'{"a":"\xff\xfe"}')
    with pytest.raises(ProtocolError):
        recv_message(b)


def test_non_object_payload_is_rejected(pair):
    a, b = pair
    a.sendall(b"[1,2,3]")
    with pytest.raises(ProtocolError):
        recv_message(b)


def test_unexpected_fds_are_closed_when_the_payload_is_bad(pair, tmp_path):
    """壊れたメッセージに fd が付いていても漏らさない."""
    a, b = pair
    dirfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        socket.send_fds(a, [b"not json"], [dirfd])
    finally:
        os.close(dirfd)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(ProtocolError):
        recv_message(b)
    assert len(os.listdir("/proc/self/fd")) <= before
