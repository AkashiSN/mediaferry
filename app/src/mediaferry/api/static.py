"""ビルド済みフロントの配信（§16 / §14）.

**同一オリジンで配る。** 別ポートにすると CORS と Cookie の設定が増え、CSRF の
前提（同一オリジン）も崩れる。

**何でも `index.html` を返さない。** `/api` の下は必ず API として扱う —— 消した
はずの API が 200 を返すようになると、画面もテストも気づけない。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .errors import ErrorCode, error_response
from .security import issue_csrf

# 外部からスクリプトも書体も画像も読まない（§14 の攻撃面を増やさない）。
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)
# API として扱う接頭辞。ここに落ちた要求は画面に化けない。
API_PREFIXES = ("/api",)


def web_root(env) -> Path | None:  # noqa: ANN001
    """ビルド済み資産の場所. 無ければ `None`（開発とテストでは普通に無い）."""
    raw = env.get("MEDIAFERRY_WEB_ROOT", "/srv/web")
    root = Path(raw)
    return root if (root / "index.html").is_file() else None


def install_web(app: FastAPI, root: Path) -> None:
    """`/` 以下で画面を配る. `/api` は API のまま."""
    app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def page(full_path: str, request: Request) -> Response:
        if any(("/" + full_path).startswith(prefix) for prefix in API_PREFIXES):
            # ここへ来るのはルータが拾わなかった `/api` の要求だけ。**画面に化けさせない**
            # （消したはずの API が 200 を返すと、画面もテストも気づけない）。
            return error_response(404, ErrorCode.NOT_FOUND, "その API は無い", {})
        response = FileResponse(
            root / "index.html",
            headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store"},
        )
        # **画面が最初に受け取る場所**（二重送信 Cookie の片割れ。§14）。
        issue_csrf(request, response)
        return response



