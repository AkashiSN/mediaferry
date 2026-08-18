"""リクエストからアプリの状態と、そのリクエスト専用の DB 接続を取り出す."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import TYPE_CHECKING

from fastapi import Depends, Request

from ..core.crypto import SecretBox
from ..settings import SettingsService
from .errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from .app import AppState


def state(request: Request) -> AppState:
    return request.app.state.mediaferry


def conn(app_state: AppState = Depends(state)) -> Iterator[sqlite3.Connection]:  # noqa: B008
    """リクエストごとに接続を開いて閉じる.

    トランザクションは接続に属するので、ワーカーと共有するとお互いの
    トランザクションに入り込む。
    """
    connection = app_state.database.connect()
    try:
        yield connection
    finally:
        connection.close()


def secret_box(
    app_state: AppState = Depends(state),  # noqa: B008
    connection: sqlite3.Connection = Depends(conn),  # noqa: B008
) -> SecretBox:
    """マスター鍵から `SecretBox` を作る. 未設定なら 400 で断る（§12.3）.

    引数名を `conn` にしないのは、このモジュールの `conn` を隠してしまい
    自己参照になるため。
    """
    settings = SettingsService(connection, app_state.env).snapshot()
    if settings.secret_key is None:
        raise ApiError(
            400,
            ErrorCode.SECRET_KEY_MISSING,
            "MEDIAFERRY_SECRET_KEY が未設定。転送先の API キーを保存できない",
        )
    return SecretBox(settings.secret_key)
