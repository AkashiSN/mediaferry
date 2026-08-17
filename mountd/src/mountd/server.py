"""ブローカーのソケットサーバ.

接続ごとにハンドルを持ち、切断時に必ず解放する。ハンドルは発行した接続に
束縛され、別の接続からは操作できない。
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import socket
import struct
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from mediaferry_protocol.errors import ConnectionClosed, ProtocolError
from mediaferry_protocol.messages import (
    REQ_CLOSE_VOLUME,
    REQ_LIST_VOLUMES,
    REQ_OPEN_VOLUME,
    VolumeInfo,
    expect_from_wire,
    to_wire,
)
from mediaferry_protocol.wire import recv_message, send_message
from mountd.mounts import MountRejected

logger = logging.getLogger(__name__)

# ハンドルを持たない接続だけに適用する idle timeout。
#
# ハンドル保有中にタイムアウトを掛けてはならない。app は dirfd を受け取った後、
# 16GiB のコピーを何十分も続ける間ブローカーへ一切 RPC を送らない。そこで
# タイムアウトすると finally がマウントを剥がし、取り込みが途中で壊れる。
# 短いプレビューしか流さないスパイクでは露見せず、大きなファイルでだけ落ちる。
DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0


class MountManagerLike(Protocol):
    def mount(self, volume: VolumeInfo, expect: Any, verify: Any) -> tuple[str, int]: ...
    def release(self, handle: str) -> None: ...
    def release_all(self) -> None: ...


Lister = Callable[[], list[VolumeInfo]]


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _peer_uid(conn: socket.socket) -> int | None:
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


class BrokerServer:
    def __init__(
        self,
        socket_path: Path,
        mount_manager: MountManagerLike,
        lister: Lister,
        allowed_uids: frozenset[int] | None,
        socket_gid: int | None = None,
        idle_timeout: float | None = DEFAULT_IDLE_TIMEOUT_SECONDS,
        broker_epoch: str | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.mount_manager = mount_manager
        self.lister = lister
        self.allowed_uids = allowed_uids
        self.socket_gid = socket_gid
        self.idle_timeout = idle_timeout
        # 世代番号はプロセス起動で 0 に戻る。再起動をまたいだ古い expect が
        # 偶然一致しないよう、プロセスごとの乱数を併せて持つ。
        self.broker_epoch = broker_epoch or secrets.token_hex(8)
        self._current_generation = 0
        self._last_fingerprint: tuple | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        server.bind(str(self.socket_path))
        # 0o660 にするだけでは所有者が root:root のままなので、非 root の app は
        # 接続できない。app と共有するグループへ明示的に付け替える。
        if self.socket_gid is not None:
            os.chown(self.socket_path, -1, self.socket_gid)
        os.chmod(self.socket_path, 0o660)
        server.listen(8)
        logger.info("listening on %s", self.socket_path)
        try:
            while True:
                conn, _ = server.accept()
                threading.Thread(target=self.handle_connection, args=(conn,), daemon=True).start()
        finally:
            server.close()
            self.mount_manager.release_all()

    # ------------------------------------------------------------------
    def handle_connection(self, conn: socket.socket) -> None:
        handles: set[str] = set()
        try:
            if self.allowed_uids is not None:
                uid = _peer_uid(conn)
                if uid is None or uid not in self.allowed_uids:
                    send_message(conn, _error("unauthorized", "peer uid is not allowed"))
                    return
            while True:
                # ハンドルを保有している間はタイムアウトを外す。詳細は
                # DEFAULT_IDLE_TIMEOUT_SECONDS のコメントを参照。
                conn.settimeout(None if handles else self.idle_timeout)
                try:
                    # 要求に fd を付ける用途は無い。取りこぼすと fd が漏れるので
                    # 1 つまで受け取り、付いていたら拒否する。
                    request, fds = recv_message(conn, max_fds=1)
                except ConnectionClosed:
                    return
                except (ProtocolError, TimeoutError, OSError):
                    with contextlib.suppress(OSError):
                        send_message(conn, _error("bad_request", "malformed request"))
                    return
                if fds:
                    for fd in fds:
                        os.close(fd)
                    send_message(conn, _error("bad_request", "requests must not carry fds"))
                    continue
                self._dispatch(conn, request, handles)
        finally:
            for handle in list(handles):
                try:
                    self.mount_manager.release(handle)
                except Exception:
                    logger.exception("failed to release %s on disconnect", handle)
            with contextlib.suppress(OSError):
                conn.close()

    # ------------------------------------------------------------------
    def _dispatch(self, conn: socket.socket, request: dict, handles: set[str]) -> None:
        kind = request.get("type")
        if kind == REQ_LIST_VOLUMES:
            self._do_list(conn)
        elif kind == REQ_OPEN_VOLUME:
            self._do_open(conn, request, handles)
        elif kind == REQ_CLOSE_VOLUME:
            self._do_close(conn, request, handles)
        else:
            send_message(conn, _error("bad_request", f"unknown request type: {kind!r}"))

    def _observe(self) -> list[VolumeInfo]:
        """現在のボリューム一覧を、epoch と世代番号を刻印して返す.

        世代は「観測されたボリューム集合が変わったとき」だけ進める。呼び出しの
        たびに進めると、クライアントが list_volumes で得た世代が open_volume の
        時点で必ず古くなり、同一性チェックが常に失敗する。逆にクライアントが
        送ってきた世代をそのまま刻印すると、チェックが常に成立して無意味になる。
        集合の変化に紐づけることで、抜き挿しがあったときだけ不一致になる。

        **列挙から刻印までを丸ごとロックの中で行う。** ロックの外で列挙すると、
        並行接続で観測が時間逆行する。スレッド A が状態 B を列挙した直後に
        止まり、スレッド B が状態 C を観測して世代を進め、そのあと A が
        「古い状態 B に新しい世代」を刻印して返す、という順序が起こりうる。
        列挙は sysfs と blkid の読み取りだけで短いので、直列化して構わない。
        """
        with self._lock:
            volumes = self.lister()
            fingerprint = tuple(
                sorted((v.volume_key, v.fs_uuid, v.fs_type, v.size_bytes) for v in volumes)
            )
            if fingerprint != self._last_fingerprint:
                self._last_fingerprint = fingerprint
                self._current_generation += 1
            generation = self._current_generation
            return [
                replace(v, broker_epoch=self.broker_epoch, generation=generation) for v in volumes
            ]

    def _do_list(self, conn: socket.socket) -> None:
        volumes = self._observe()
        send_message(conn, {"ok": True, "volumes": [to_wire(v) for v in volumes]})

    def _do_open(self, conn: socket.socket, request: dict, handles: set[str]) -> None:
        key = request.get("volume_key")
        if not isinstance(key, str):
            send_message(conn, _error("bad_request", "volume_key must be a string"))
            return
        try:
            expect = expect_from_wire(request.get("expect", {}))
        except ProtocolError as exc:
            send_message(conn, _error("bad_request", str(exc)))
            return

        volume = {v.volume_key: v for v in self._observe()}.get(key)
        if volume is None:
            send_message(conn, _error("unknown_volume", f"no volume with key {key!r}"))
            return

        def verify() -> VolumeInfo | None:
            return {v.volume_key: v for v in self._observe()}.get(key)

        try:
            handle, dirfd = self.mount_manager.mount(volume, expect, verify)
        except MountRejected as exc:
            send_message(conn, _error("rejected", str(exc)))
            return
        except Exception as exc:
            logger.exception("mount failed for %s", key)
            send_message(conn, _error("mount_failed", str(exc)))
            return

        handles.add(handle)
        # dirfd は MountManager が保持している。ここでは複製せず送るだけで、
        # 解放は release() に任せる。
        try:
            send_message(conn, {"ok": True, "handle": handle}, fds=[dirfd])
        except Exception:
            self.mount_manager.release(handle)
            handles.discard(handle)
            raise

    def _do_close(self, conn: socket.socket, request: dict, handles: set[str]) -> None:
        handle = request.get("handle")
        if not isinstance(handle, str) or handle not in handles:
            send_message(conn, _error("unknown_handle", "handle is not owned by this connection"))
            return
        try:
            self.mount_manager.release(handle)
        except Exception as exc:
            # 実物の release() は fd を閉じるだけなので失敗しない。
            # 差し替え実装が例外を出した場合の保険。
            send_message(conn, _error("release_failed", str(exc)))
            return
        handles.discard(handle)
        send_message(conn, {"ok": True})
