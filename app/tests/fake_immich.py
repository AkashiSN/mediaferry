"""テスト用の Immich（ループバックで実際に listen する HTTP サーバ）.

**実物の httpx で、実物のソケットに対して叩く。** クライアントを差し替えて
「呼んだつもり」を確かめるテストは、ヘッダ名や encoding の取り違えを見逃す。

ASGI ではなく素の HTTP サーバにしているのは、httpx 0.28 の `ASGITransport` が
非同期用（`handle_async_request` しか持たない）で、**同期の `httpx.Client` から
使えない**ため。multipart の wire と redirect の扱いをそのまま確かめる意味でも、
実際に listen させる方が確実である。

応答の形は `docs/phase0-findings.md` ② の実測に合わせる。
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

API_KEY = "test-api-key"  # noqa: S105
USER_ID = "user-uuid-1"


class FakeImmich:
    """状態を持つ最小の Immich.

    `assets` はチェックサム（base64）から資産 ID への写像。
    """

    def __init__(self, user_id: str = USER_ID) -> None:
        self.user_id = user_id
        self.assets: dict[str, str] = {}
        self.trashed: set[str] = set()
        self.tags: dict[str, str] = {}  # name -> id
        self.tagged: dict[str, list[str]] = {}  # tag_id -> asset_ids
        self.datetimes: dict[str, str] = {}
        self.uploads: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []
        self.fail_next: int = 0  # 次の N 回を 503 にする
        self.redirect_to: str | None = None
        # 400 の本文に、受け取った API キーをそのまま返す（秘密の漏れを見る）。
        self.echo_key_in_error: bool = False
        # **2xx の応答の scalar に鍵を echo する。** 侵害された転送先が
        # 「こちらが読む値」に秘密を混ぜてきたとき、それが DB・API 応答・
        # 例外・ログのどこにも出ないことを確かめるためのつまみ。
        self.echo_key_in_scalars: bool = False
        self._server: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._server = server

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        assert self._server is not None, "start() を先に呼ぶ"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # ------------------------------------------------------------------
    def route(self, method: str, path: str, body: bytes, headers: dict[str, str]):  # noqa: ANN201
        self.requests.append((method, path))
        if self.redirect_to is not None:
            # **1 回だけ返す。** 毎回 301 にすると、追従した先で何が起きても
            # 「redirect が多すぎる」に化けて、追従の可否を見分けられない。
            target, self.redirect_to = self.redirect_to, None
            return 301, {"location": target}
        if headers.get("x-api-key") != API_KEY:
            return 401, {"message": "Invalid API key"}
        if self.fail_next > 0:
            self.fail_next -= 1
            return 503, {"message": "unavailable"}
        if self.echo_key_in_error:
            return 400, {"message": f"bad request from key {headers.get('x-api-key')}"}

        if method == "GET" and path == "/api/users/me":
            observed = headers.get("x-api-key") if self.echo_key_in_scalars else self.user_id
            return 200, {"id": observed, "email": "someone@example.invalid"}
        if method == "POST" and path == "/api/assets/bulk-upload-check":
            return 200, self._bulk_check(json.loads(body))
        if method == "POST" and path == "/api/assets":
            return self._upload(body, headers)
        if method == "GET" and path == "/api/tags":
            return 200, [{"id": tag_id, "name": name} for name, tag_id in self.tags.items()]
        if method == "POST" and path == "/api/tags":
            name = json.loads(body)["name"]
            tag_id = self.tags.setdefault(name, f"tag-{len(self.tags) + 1}")
            return 201, {"id": tag_id, "name": name}
        if method == "PUT" and path.startswith("/api/tags/") and path.endswith("/assets"):
            tag_id = path.split("/")[3]
            ids = json.loads(body)["ids"]
            self.tagged.setdefault(tag_id, []).extend(ids)
            return 200, [{"id": asset_id, "success": True} for asset_id in ids]
        if method == "PUT" and path.startswith("/api/assets/"):
            asset_id = path.split("/")[3]
            self.datetimes[asset_id] = json.loads(body)["dateTimeOriginal"]
            return 200, {"id": asset_id}
        return 404, {"message": f"no route for {method} {path}"}

    def _bulk_check(self, payload):  # noqa: ANN001, ANN202
        if self.echo_key_in_scalars:
            # 未知の action として鍵を返す。クライアントは protocol error にする。
            return {
                "results": [{"id": item["id"], "action": API_KEY} for item in payload["assets"]]
            }
        results = []
        for item in payload["assets"]:
            asset_id = self.assets.get(item["checksum"])
            if asset_id is None:
                results.append({"id": item["id"], "action": "accept"})
            else:
                results.append(
                    {
                        "id": item["id"],
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": asset_id,
                        "isTrashed": asset_id in self.trashed,
                    }
                )
        return {"results": results}

    def _upload(self, body, headers):  # noqa: ANN001, ANN202
        fields = _parse_multipart(body, headers["content-type"])
        data = fields["assetData"]
        checksum = base64.b64encode(hashlib.sha1(data, usedforsecurity=False).digest()).decode()
        if headers.get("x-immich-checksum") != checksum:
            return 400, {"message": "checksum header mismatch"}
        self.uploads.append(
            {**{k: v for k, v in fields.items() if k != "assetData"}, "size": len(data)}
        )
        if self.echo_key_in_scalars:
            return 200, {"id": "asset-echo", "status": API_KEY}
        existing = self.assets.get(checksum)
        if existing is not None:
            return 200, {"id": existing, "status": "duplicate"}
        asset_id = f"asset-{len(self.assets) + 1}"
        self.assets[checksum] = asset_id
        return 201, {"id": asset_id, "status": "created"}


def _handler_for(fake: FakeImmich):  # noqa: ANN202
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            """テストの出力を汚さない."""

        def _respond(self, method: str) -> None:
            length = int(self.headers.get("content-length", 0))
            body = self.rfile.read(length) if length else b""
            headers = {key.lower(): value for key, value in self.headers.items()}
            status, payload = fake.route(method, self.path, body, headers)
            if status == 301:
                self.send_response(301)
                self.send_header("location", payload["location"])
                self.send_header("content-length", "0")
                self.end_headers()
                return
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._respond("PUT")

    return Handler


def _parse_multipart(body: bytes, content_type: str) -> dict[str, Any]:
    """multipart/form-data の最小パーサ. 名前と中身だけを取り出す."""
    boundary = content_type.split("boundary=")[1].encode()
    fields: dict[str, Any] = {}
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        if b'name="' not in head:
            continue
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        content = value.rsplit(b"\r\n", 1)[0]
        fields[name] = content if name == "assetData" else content.decode()
    return fields
