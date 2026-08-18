"""入口の防御（§14）.

**認証の有無に関わらず掛ける。** 認証を切っていても、罠サイトを開いたブラウザから
`127.0.0.1` を叩ける（drive-by CSRF）。

**Origin と Host の一致は DNS rebinding を防がない。** 攻撃者のドメインを LAN の IP へ
向け直すと、ブラウザが送る `Origin` も `Host` も攻撃者のホスト名になり、「一致するか」は
通ってしまう。そこで **`Host` を信頼できる集合と突き合わせる**。

信頼するのは (1) `localhost`、(2) **IP アドレスそのもの**、(3) `TRUSTED_HOSTS` に
書かれた名前。(2) を入れるのは、利用者が LAN の IP を直に打つのが正当な使い方だから。
rebinding は「ホスト名が LAN の IP を指す」形なので、名前を明示的な許可制にすれば
その経路は閉じる。

検証の順序は **Host → Origin → CSRF → セッション**。相手に一番情報を与えない順に並べる。
"""

from __future__ import annotations

import ipaddress
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import Depends, Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ..db.sessions import SessionStore
from .deps import conn
from .errors import ApiError, ErrorCode, error_response

# 状態を変えないメソッド。Origin も CSRF も掛けない（`curl` は Origin を送らない）。
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# CSRF だけを免除する経路。**Host と Origin の検証は掛かる。**
CSRF_EXEMPT_PATHS = frozenset({"/api/auth/login"})
SESSION_COOKIE = "mediaferry_session"  # noqa: S105 - Cookie の名前
CSRF_COOKIE = "XSRF-TOKEN"  # noqa: S105 - Cookie の名前
CSRF_HEADER = "X-CSRF-Token"  # noqa: S105 - ヘッダの名前
# ログインの失敗を数える窓と上限（同一の相手から総当たりさせない）。
LOGIN_WINDOW_SECONDS = 60.0
LOGIN_MAX_ATTEMPTS = 10


def is_trusted_host(host_header: str | None, configured: frozenset[str]) -> bool:
    """`Host` が信頼できるか. **名前は明示的な許可制。**"""
    if not host_header:
        return False
    hostname = urlsplit(f"//{host_header}").hostname
    if hostname is None:
        return False
    if hostname in configured or hostname == "localhost":
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def _same_origin(candidate: str, request: Request) -> bool:
    parts = urlsplit(candidate)
    return (parts.scheme, parts.hostname, parts.port) == (
        request.url.scheme,
        request.url.hostname,
        request.url.port,
    )


class SecurityMiddleware:
    """`Host` を確かめ、状態を変える要求に Origin と CSRF を要求する.

    **素の ASGI ミドルウェアにする。** `BaseHTTPMiddleware` は応答を一旦受け止めて
    から流すので、終わらない応答（SSE）が相手に届かなくなる。ここは要求を通すか
    断るかだけなので、scope を見て早く決める。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        # **信頼する名前は起動時に決まる**（`TRUSTED_HOSTS` は RESTART の階層）。
        trusted = request.app.state.mediaferry.trusted_hosts
        if not is_trusted_host(request.headers.get("host"), trusted):
            # 421。「この名前ではこのサーバを名乗らない」という意味で返す。
            refusal = error_response(421, ErrorCode.UNTRUSTED_HOST, "この名前では受け付けない", {})
            await refusal(scope, receive, send)
            return
        if request.method not in SAFE_METHODS:
            refused = self._refuse_cross_site(request)
            if refused is not None:
                await refused(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _refuse_cross_site(self, request: Request) -> Response | None:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin is not None and not _same_origin(origin, request):
            return error_response(
                403, ErrorCode.CROSS_SITE_REQUEST, "別のサイトからの要求は受け付けない", {}
            )
        if request.url.path in CSRF_EXEMPT_PATHS:
            return None
        sent = request.headers.get(CSRF_HEADER)
        stored = request.cookies.get(CSRF_COOKIE)
        if not sent or not stored or not secrets.compare_digest(sent, stored):
            return error_response(
                403, ErrorCode.CSRF_FAILED, "画面を再読み込みしてから操作する", {}
            )
        return None


def issue_csrf(request: Request, response: Response) -> str:
    """CSRF トークンを配る. **既に有効な値があれば作り直さない**（別タブを壊さない）."""
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,  # JS が読んでヘッダに載せる（二重送信 Cookie）
        samesite="lax",
        path="/",
        secure=request.url.scheme == "https",
    )
    return token


class LoginAttempts:
    """ログインの失敗を相手ごとに数える（総当たりを遅らせる）."""

    def __init__(self) -> None:
        self._seen: dict[str, list[float]] = {}

    def too_many(self, client: str) -> bool:
        now = time.monotonic()
        recent = [at for at in self._seen.get(client, []) if now - at < LOGIN_WINDOW_SECONDS]
        self._seen[client] = recent
        return len(recent) >= LOGIN_MAX_ATTEMPTS

    def record_failure(self, client: str) -> None:
        self._seen.setdefault(client, []).append(time.monotonic())

    def forget(self, client: str) -> None:
        self._seen.pop(client, None)


def authentication_required(request: Request) -> bool:
    """`AUTH_PASSWORD` が設定されているか（起動時に決まる）."""
    return request.app.state.mediaferry.password_hash is not None


def require_session(
    request: Request,
    connection: sqlite3.Connection = Depends(conn),  # noqa: B008
) -> None:
    """認証が有効なら、セッションを持っていない要求を拒む.

    **接続はリクエスト専用のものを使う**（`deps.conn`）。ワーカーや他の要求と
    共有すると、お互いのトランザクションに入り込む（§3）。
    """
    if not authentication_required(request):
        return
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id is None or not SessionStore(connection).verify(session_id):
        raise ApiError(401, ErrorCode.NOT_AUTHENTICATED, "ログインが要る")
