"""SOCK_SEQPACKET 上で JSON メッセージと fd をやり取りする.

SOCK_SEQPACKET を使うのは、カーネルがメッセージ境界を保つため。自前の長さ
プレフィックスが不要になり、受信バッファを固定にするだけでサイズ上限を
強制できる（超過分は MSG_TRUNC で検出する）。SCM_RIGHTS は 1 回の sendmsg に
紐づくので、境界が保たれることは fd の受け渡しでも都合が良い。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from collections.abc import Sequence
from typing import Any

from .errors import ConnectionClosed, MessageTooLarge, ProtocolError

MAX_MESSAGE_BYTES = 1 << 20


def send_message(
    sock: socket.socket,
    payload: dict[str, Any],
    fds: Sequence[int] = (),
) -> None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise MessageTooLarge(f"payload is {len(body)} bytes (limit {MAX_MESSAGE_BYTES})")
    if fds:
        socket.send_fds(sock, [body], list(fds))
    else:
        sock.sendall(body)


def recv_message(
    sock: socket.socket,
    max_fds: int = 1,
) -> tuple[dict[str, Any], list[int]]:
    # バッファを上限ちょうどにしておくと、超過メッセージは MSG_TRUNC が立つ。
    body, fds, flags, _ = socket.recv_fds(sock, MAX_MESSAGE_BYTES, max_fds)
    if not body and not fds:
        raise ConnectionClosed("peer closed the connection")
    if flags & socket.MSG_TRUNC:
        for fd in fds:
            _close_quietly(fd)
        raise MessageTooLarge("incoming message exceeded the size limit")
    if flags & socket.MSG_CTRUNC:
        for fd in fds:
            _close_quietly(fd)
        raise ProtocolError("ancillary data was truncated")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # json.loads は bytes を受け取ると、不正な UTF-8 に対して
        # JSONDecodeError ではなく UnicodeDecodeError を投げる。両方を
        # ProtocolError に正規化しないと、呼び出し側の except を素通りする。
        for fd in fds:
            _close_quietly(fd)
        raise ProtocolError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        for fd in fds:
            _close_quietly(fd)
        raise ProtocolError("payload must be a JSON object")
    return payload, fds


def _close_quietly(fd: int) -> None:
    with contextlib.suppress(OSError):
        os.close(fd)
