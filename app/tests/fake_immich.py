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
# 同じ鍵をパーセント符号化した形。生の一致では見つからないが、可逆に戻せる。
ENCODED_API_KEY = "".join(f"%{ord(character):02x}" for character in API_KEY)
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
        # スタック。`stack_id -> {"primary": asset_id, "assets": [asset_id, ...]}`
        self.stacks: dict[str, dict[str, Any]] = {}
        # 応答を壊すつまみ（fail-closed の検査用）。
        self.stack_response_without_assets: bool = False
        self.drop_one_asset_from_the_stack_response: bool = False
        self.primary_outside_the_stack: bool = False
        self.stack_list_is_not_json: bool = False
        self.stack_list_is_not_even_json: bool = False
        self.stack_list_ignores_the_primary_filter: bool = False
        self.duplicate_asset_in_the_stack_response: bool = False
        # **スタック id だけに鍵を混ぜる。** 資産の集合は正しいままなので、
        # 全単射の検査では落ちない（識別子の検査だけが守っている経路）。
        self.key_as_stack_id: bool = False
        self.empty_assets_in_the_stack_response: bool = False
        self.stack_list_has_a_scalar: bool = False
        self.malformed_stack_field: bool = False
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
        # **「正常な応答」の識別子として鍵を返す。** 形は仕様どおりなので
        # 型の検査では落ちない。保存・URL への組み立てまで到達しうる値。
        self.echo_key_as_ids: bool = False
        # タグの id に、次の要求の経路を変えられる値を返す。
        self.path_in_tag_id: bool = False
        # **鍵をパーセント符号化して識別子に混ぜる。** 生の一致だけを見る検査は
        # ここを通してしまう。可逆なので、経路の先で復号すれば平文に戻る。
        self.encoded_key_as_ids: bool = False
        # `assetId` を文字列以外で返す（型の検査が無いと内部例外になる）。
        self.numeric_asset_id: bool = False
        # タグの id に dot-segment を返す（unreserved だけの検査は通ってしまう）。
        self.dot_segment_in_tag_id: bool = False
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
        if method == "GET" and path.startswith("/api/assets/"):
            asset_id = path.split("/")[3]
            if self.echo_key_as_ids:
                return 200, {"id": API_KEY, "isTrashed": False}
            body = {"id": asset_id, "isTrashed": asset_id in self.trashed}
            if asset_id in self.datetimes:
                body["exifInfo"] = {"dateTimeOriginal": self.datetimes[asset_id]}
            if self.malformed_stack_field:
                # キーはあるが object ではない（旧版の「キーが無い」とは別物）。
                body["stack"] = "stack-1"
            else:
                found = self._stack_of(asset_id)
                if found is not None:
                    stack_id, stack = found
                    body["stack"] = {
                        "id": stack_id,
                        "primaryAssetId": stack["primary"],
                        "assetCount": len(stack["assets"]),
                    }
            return 200, body
        if method == "GET" and path == "/api/tags":
            if self.echo_key_as_ids:
                return 200, [{"id": API_KEY, "name": name} for name in self.tags or ["dji"]]
            if self.encoded_key_as_ids:
                return 200, [{"id": ENCODED_API_KEY, "name": name} for name in ["dji"]]
            if self.dot_segment_in_tag_id:
                return 200, [{"id": "..", "name": name} for name in ["dji"]]
            if self.path_in_tag_id:
                return 200, [{"id": "../../users/me", "name": name} for name in ["dji"]]
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
        if method == "POST" and path == "/api/stacks":
            return self._create_stack(json.loads(body))
        if method == "GET" and path.startswith("/api/stacks?"):
            return self._stacks_by_primary(path)
        if method == "PUT" and path.startswith("/api/stacks/"):
            stack_id = path.split("/")[3]
            stack = self.stacks.get(stack_id)
            if stack is None:
                return 404, {"message": "no such stack"}
            stack["primary"] = json.loads(body)["primaryAssetId"]
            return 200, self._stack_view(stack_id)
        if method == "PUT" and path.startswith("/api/assets/"):
            asset_id = path.split("/")[3]
            self.datetimes[asset_id] = json.loads(body)["dateTimeOriginal"]
            return 200, {"id": asset_id}
        return 404, {"message": f"no route for {method} {path}"}

    def _bulk_check(self, payload):  # noqa: ANN001, ANN202
        if self.encoded_key_as_ids:
            return {
                "results": [
                    {
                        "id": item["id"],
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": ENCODED_API_KEY,
                        "isTrashed": False,
                    }
                    for item in payload["assets"]
                ]
            }
        if self.numeric_asset_id:
            return {
                "results": [
                    {
                        "id": item["id"],
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": 1,
                        "isTrashed": False,
                    }
                    for item in payload["assets"]
                ]
            }
        if self.echo_key_as_ids:
            return {
                "results": [
                    {
                        "id": item["id"],
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": API_KEY,
                        "isTrashed": False,
                    }
                    for item in payload["assets"]
                ]
            }
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
        if self.echo_key_as_ids:
            return 201, {"id": API_KEY, "status": "created"}
        if self.echo_key_in_scalars:
            return 200, {"id": "asset-echo", "status": API_KEY}
        existing = self.assets.get(checksum)
        if existing is not None:
            return 200, {"id": existing, "status": "duplicate"}
        asset_id = f"asset-{len(self.assets) + 1}"
        self.assets[checksum] = asset_id
        return 201, {"id": asset_id, "status": "created"}

    def _stack_of(self, asset_id: str):  # noqa: ANN202
        for stack_id, stack in self.stacks.items():
            if asset_id in stack["assets"]:
                return stack_id, stack
        return None

    def _stack_view(self, stack_id: str):  # noqa: ANN202
        stack = self.stacks[stack_id]
        assets = list(stack["assets"])
        if self.drop_one_asset_from_the_stack_response:
            assets = assets[:-1]
        if self.duplicate_asset_in_the_stack_response:
            # **要求と件数が違うのに集合は同じ**（[A, B] を送って [A, A, B] が返る）。
            assets = [assets[0], *assets]
        if self.empty_assets_in_the_stack_response:
            assets = []
        primary = API_KEY if self.echo_key_as_ids else stack["primary"]
        if self.primary_outside_the_stack:
            primary = "someone-else"
        body = {
            "id": API_KEY if (self.echo_key_as_ids or self.key_as_stack_id) else stack_id,
            "primaryAssetId": primary,
            "assets": [{"id": API_KEY if self.echo_key_as_ids else a} for a in assets],
        }
        if self.stack_response_without_assets:
            del body["assets"]
        return body

    def _create_stack(self, payload):  # noqa: ANN001, ANN202
        """**実物の地雷を再現する。**

        渡した資産のどれかが既存スタックの primary なら、その既存スタックを
        新しいスタックへ畳み込む（Immich v3.1.0 の仕様）。
        """
        ids = list(payload["assetIds"])
        absorbed = [stack_id for stack_id, stack in self.stacks.items() if stack["primary"] in ids]
        for stack_id in absorbed:
            for asset_id in self.stacks.pop(stack_id)["assets"]:
                if asset_id not in ids:
                    ids.append(asset_id)
        stack_id = f"stack-{len(self.stacks) + 1}"
        self.stacks[stack_id] = {"primary": ids[0], "assets": ids}
        return 201, self._stack_view(stack_id)

    def _stacks_by_primary(self, path: str):  # noqa: ANN202
        if self.stack_list_is_not_even_json:
            return 200, b"<html>proxy body</html>"
        if self.stack_list_is_not_json:
            return 200, "これは JSON の配列ではない"
        primary = path.split("primaryAssetId=")[1]
        if self.stack_list_has_a_scalar:
            return 200, ["scalar"]
        if self.stack_list_ignores_the_primary_filter:
            # **絞り込みを無視して全部返す相手。** こちらが primary の一致を
            # 確かめていなければ、無関係なスタックを掴む。
            return 200, [self._stack_view(stack_id) for stack_id in self.stacks]
        return 200, [
            self._stack_view(stack_id)
            for stack_id, stack in self.stacks.items()
            if stack["primary"] == primary
        ]


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
            # **本物の非 JSON を返せるようにする。** `json.dumps` を通すと
            # 「JSON の文字列」になってしまい、`.json()` が成功する。
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
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
