"""API のエラー形式（§13 / §14）.

**画面は例外の文字列をそのまま出さない。** 何が起きて次に何をすべきかを日本語で
示すのは画面の仕事で、そのためには**機械が読める `code`** が要る。`detail` だけを
返していると、画面は結局それを表示するしかなく、内部の文言や相手由来の値が
そのまま利用者へ出る。

封筒はこの形に統一する。

```json
{"error": {"code": "record_not_found", "detail": "そのレコードは無い", "meta": {}}}
```

- `code` は安定した語彙（下の `ErrorCode`）。増やすときはここに足す
- `detail` は**こちらが書いた日本語**だけ。相手の応答・例外の文字列・秘密を混ぜない
- `meta` は画面が使う構造化データ。**秘密を入れない**
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(StrEnum):
    """API が返す `code` の一覧. **画面はこれを見て日本語を決める。**"""

    # 400 系
    BAD_REQUEST = "bad_request"
    INVALID_ENDPOINT = "invalid_endpoint"
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"
    UNKNOWN_ACTION = "unknown_action"
    SECRET_KEY_MISSING = "secret_key_missing"  # noqa: S105 - 鍵ではなく code の名前
    VALIDATION_FAILED = "validation_failed"
    THUMBNAIL_FAILED = "thumbnail_failed"
    # 401 / 403 / 421
    NOT_AUTHENTICATED = "not_authenticated"
    CROSS_SITE_REQUEST = "cross_site_request"
    CSRF_FAILED = "csrf_failed"
    UNTRUSTED_HOST = "untrusted_host"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    TOO_MANY_STREAMS = "too_many_streams"
    # 404
    NOT_FOUND = "not_found"
    # 409
    CONFLICT = "conflict"
    JOB_ALREADY_FINISHED = "job_already_finished"
    NOT_RETRYABLE = "not_retryable"
    NOT_REQUEUEABLE = "not_requeueable"
    NOT_AWAITING_APPROVAL = "not_awaiting_approval"
    ALREADY_INVALIDATED = "already_invalidated"
    APPROVAL_ALREADY_QUEUED = "approval_already_queued"
    SETTING_LOCKED = "setting_locked"
    SAME_LIBRARY_UNDECIDED = "same_library_undecided"
    # 502
    DESTINATION_UNREACHABLE = "destination_unreachable"
    # 500
    INTERNAL = "internal"


class ApiError(Exception):
    """API が返す失敗. **`detail` はこちらが書いた文だけ。**"""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        detail: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.meta = meta or {}


def error_response(status_code: int, code: str, detail: str, meta: dict[str, Any]) -> JSONResponse:
    """封筒に入れた応答を作る（middleware など、例外を使えない場所から呼ぶ）."""
    return _envelope(status_code, code, detail, meta)


def _envelope(status_code: int, code: str, detail: str, meta: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "detail": detail, "meta": meta}},
    )


def install_error_handlers(app: FastAPI) -> None:
    """すべての失敗を同じ封筒に入れる. **未処理の例外の文言は外へ出さない。**"""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.detail, exc.meta)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # フレームワークが投げたもの（経路が無いときの 404、method が違うときの
        # 405 など）。code は状態から決める。
        #
        # **文言はこちらで書く。** 既定の `detail` は英語（"Method Not Allowed"）で、
        # 画面は `bad_request` に `detail` を添えて出すため、素通しにすると
        # そのまま利用者へ出る。
        if exc.status_code >= 500:
            return _envelope(exc.status_code, ErrorCode.INTERNAL, "内部エラー", {})
        if exc.status_code == 404:
            return _envelope(exc.status_code, ErrorCode.NOT_FOUND, "その経路は無い", {})
        return _envelope(exc.status_code, ErrorCode.BAD_REQUEST, "その要求は受け付けられない", {})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # **本文をそのまま返さない。** 受け取った値が応答に反射する経路になる。
        fields = sorted(
            {".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()}
        )
        return _envelope(
            422,
            ErrorCode.VALIDATION_FAILED,
            "要求の形が違う",
            {"fields": [field for field in fields if field]},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # **例外の文字列を外へ出さない**（秘密も相手由来の値も混ざりうる）。
        # 原因はログ（サーバ側）にだけ残す。
        import logging

        logging.getLogger("mediaferry.api").exception("未処理の例外", exc_info=exc)
        return _envelope(500, ErrorCode.INTERNAL, "内部エラー。ログを確認する", {})
