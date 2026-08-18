"""ログインとログアウト（§11 / §14）.

**認証は必須にしない。** `AUTH_PASSWORD` が無ければ `required: false` を返し、
どの経路も素通りする（LAN 内で無設定で使えることを優先する。§12）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ..core.auth import verify_password
from ..db.sessions import SessionStore
from .deps import conn
from .errors import ApiError, ErrorCode
from .security import (
    SESSION_COOKIE,
    authentication_required,
    issue_csrf,
    require_session,
)

router = APIRouter()


@router.get("/auth/session")
def read_session(
    request: Request,
    response: Response,
    connection: sqlite3.Connection = Depends(conn),  # noqa: B008
) -> dict[str, bool]:
    """認証の要否と、いまログインしているかを返す.

    **CSRF トークンの発行点でもある。** 画面が最初に叩くところで必ず受け取れる
    ようにしておく（`index.html` の応答でも配る）。
    """
    issue_csrf(request, response)
    required = authentication_required(request)
    session_id = request.cookies.get(SESSION_COOKIE)
    authenticated = bool(
        required and session_id is not None and SessionStore(connection).verify(session_id)
    )
    return {"required": required, "authenticated": authenticated}


@router.post("/auth/login")
def login(
    request: Request,
    response: Response,
    body: dict[str, Any],
    connection: sqlite3.Connection = Depends(conn),  # noqa: B008
) -> dict[str, str]:
    """パスワードを確かめてセッションを配る.

    **総当たりを遅らせる。** 同じ相手からの失敗が続いたら 429 で断る。
    **応答にもログにもパスワードを出さない。**
    """
    state = request.app.state.mediaferry
    if state.password_hash is None:
        raise ApiError(409, ErrorCode.CONFLICT, "認証は無効になっている")
    client = request.client.host if request.client else "unknown"
    if state.login_attempts.too_many(client):
        raise ApiError(429, ErrorCode.TOO_MANY_ATTEMPTS, "試行が多すぎる。しばらく待つ")
    password = body.get("password")
    if not isinstance(password, str) or not verify_password(state.password_hash, password):
        state.login_attempts.record_failure(client)
        raise ApiError(401, ErrorCode.NOT_AUTHENTICATED, "パスワードが違う")
    state.login_attempts.forget(client)
    session_id, expires_at = SessionStore(connection).create()
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
        path="/",
        secure=request.url.scheme == "https",
    )
    issue_csrf(request, response)
    return {"status": "ok", "expires_at": expires_at}


@router.post("/auth/logout", dependencies=[Depends(require_session)])
def logout(
    request: Request,
    response: Response,
    connection: sqlite3.Connection = Depends(conn),  # noqa: B008
) -> dict[str, str]:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id is not None:
        SessionStore(connection).revoke(session_id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}
