"""mountd との通信. app でソケットを触るのはここだけ.

上位のコードは VolumeHandle の dirfd だけを見る。マウントのパスも
デバイスノードも知らない。
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from mediaferry_protocol.messages import (
    REQ_CLOSE_VOLUME,
    REQ_LIST_VOLUMES,
    REQ_OPEN_VOLUME,
    VolumeExpect,
    VolumeInfo,
    to_wire,
    volume_from_wire,
)
from mediaferry_protocol.wire import recv_message, send_message


class BrokerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class VolumeHandle:
    """開いたボリューム. dirfd を通してのみ中身へ到達できる.

    `SCM_RIGHTS` で受け取った fd は**サーバ側とは別の所有物**である。
    サーバ側の fd は接続が切れれば回収されるが、こちらは明示的に閉じない限り
    残り、detached mount をプロセス終了まで生かしてしまう。
    """

    handle: str
    dirfd: int
    volume: VolumeInfo
    _client: BrokerClient
    _closed: bool = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._client.close_volume(self)

    def close_local_fd(self) -> None:
        """ローカルの fd を閉じる。冪等.

        整数 fd を二度閉じると、その間に OS が同じ番号を別のファイルへ
        割り当てていた場合、無関係な fd を閉じてしまう。閉じたら -1 にして
        二度目を無効化する。
        """
        if self._closed:
            return
        self._closed = True
        fd, self.dirfd = self.dirfd, -1
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)

    @property
    def closed(self) -> bool:
        return self._closed


class BrokerClient:
    def __init__(self, socket_path: Path) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._sock.connect(str(socket_path))
        self._handles: dict[str, VolumeHandle] = {}
        # 要求と応答は 1 本のソケットで対応付ける。API のスレッドと
        # ワーカーのスレッドが同時に使うと、応答を取り違える。
        self._lock = threading.Lock()

    @classmethod
    def from_socket(cls, sock: socket.socket) -> BrokerClient:
        client = cls.__new__(cls)
        client._sock = sock
        client._handles = {}
        client._lock = threading.Lock()
        return client

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """ソケットと、開いたままのボリューム fd をすべて閉じる.

        ソケットだけ閉じると、例外で context manager を抜けなかった
        VolumeHandle の fd が残り、detached mount が生き続ける。
        """
        for handle in list(self._handles.values()):
            handle.close_local_fd()
        self._handles.clear()
        with contextlib.suppress(OSError):
            self._sock.close()

    # ------------------------------------------------------------------
    def _call(self, payload: dict, expect_fd: bool = False) -> tuple[dict, list[int]]:
        with self._lock:
            send_message(self._sock, payload)
            reply, fds = recv_message(self._sock, max_fds=1 if expect_fd else 0)
        if not reply.get("ok"):
            for fd in fds:
                os.close(fd)
            raise BrokerError(
                str(reply.get("error", "unknown")),
                str(reply.get("message", "")),
            )
        return reply, fds

    def list_volumes(self) -> list[VolumeInfo]:
        reply, _ = self._call({"type": REQ_LIST_VOLUMES})
        return [volume_from_wire(v) for v in reply.get("volumes", [])]

    def open_volume(self, volume: VolumeInfo) -> VolumeHandle:
        expect = VolumeExpect(
            major=volume.major,
            minor=volume.minor,
            fs_uuid=volume.fs_uuid,
            fs_type=volume.fs_type or "",
            broker_epoch=volume.broker_epoch,
            generation=volume.generation,
        )
        reply, fds = self._call(
            {
                "type": REQ_OPEN_VOLUME,
                "volume_key": volume.volume_key,
                "expect": to_wire(expect),
            },
            expect_fd=True,
        )
        if len(fds) != 1:
            for fd in fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
            raise BrokerError("bad_reply", "expected exactly one file descriptor")
        name = reply.get("handle")
        if not isinstance(name, str) or not name:
            with contextlib.suppress(OSError):
                os.close(fds[0])
            raise BrokerError("bad_reply", "handle is missing from the reply")
        vh = VolumeHandle(handle=name, dirfd=fds[0], volume=volume, _client=self)
        self._handles[name] = vh
        return vh

    def close_volume(self, handle: VolumeHandle) -> None:
        """ローカル fd を必ず閉じてから、サーバへ解放を通知する.

        サーバ側の応答が得られなくてもローカル fd は閉じる。サーバ側は
        接続断の後始末で回収されるので、こちらが握り続ける理由がない。
        二度呼んでも安全。
        """
        already_closed = handle.closed
        handle.close_local_fd()
        self._handles.pop(handle.handle, None)
        if already_closed:
            return
        self._call({"type": REQ_CLOSE_VOLUME, "handle": handle.handle})
