#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Immich API の実測プローブ (Phase 0 検証 ②).

仕様書が前提にしている次の 4 点を確かめ、**判定を終了ステータスに反映する**。
「実行した」だけで成功扱いにすると、Task 11 の記録を都合よく埋められてしまう。

1. 転送先の安定した同定手段 (サーバインスタンス ID と認証ユーザ ID) が取れるか
   → 取れないと upload_destination を API キー由来にせざるを得ず、
     キーローテートで全件再アップロードになる (仕様書 §8)
2. bulk-upload-check が、アップロード済み資産に対して **その資産 ID を返すか**
   → 返らないと「サーバ成功・ローカル未記録」からの再開ができない (仕様書 §9.10)
3. アップロードした資産の deviceAssetId を後から読めるか
   → 読めないと「自分が上げた資産」と「以前から存在した無関係な重複」を
     区別できず、既存資産の撮影日時を壊す (仕様書 §9.10)
4. チェックサムの wire encoding (hex か base64 か)
   → Phase 1 が bulk body と x-immich-checksum の双方で必要とする

毎回ユニークな有効画像を生成するので、前回の後片付けに失敗していても
判定が汚染されない。

実行:
  export IMMICH_URL=<url>
  export IMMICH_API_KEY=<key>
  ./immich_probe.py --write --cleanup

--write なしでは 1 と 4 の一部しか確認できない。Task 11 の記録には
--write --cleanup の結果を使う。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import struct
import sys
import uuid
import zlib
from pathlib import Path
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(120.0)


def make_unique_png(width: int = 2, height: int = 2) -> bytes:
    """毎回内容の異なる有効な PNG を返す (8bit truecolor, フィルタ無し).

    固定の画像を使うと、前回の後片付けに失敗していた場合に既存資産が返り、
    その deviceAssetId を読んで「永続する」と誤判定してしまう。
    """
    noise = hashlib.sha256(uuid.uuid4().bytes).digest()
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # フィルタタイプ 0 (None)
        for x in range(width):
            base = (y * width + x) * 3
            rows += bytes(noise[(base + c) % len(noise)] for c in range(3))

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def show(title: str, value: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2)[:4000])


def check(results: dict[str, bool], name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def bulk_check(client: httpx.Client, asset_id: str, checksum: str) -> dict[str, Any]:
    """`bulk-upload-check` を 1 件で呼び、結果を厳密に解釈する.

    HTTP エラー・空応答・未知のスキーマを「重複ではない」と解釈しないこと。
    そうすると encoding が受理されていないだけなのに命題が通ってしまう。
    戻り値の `outcome` は accept / reject / unknown のいずれか。
    """
    try:
        r = client.post(
            "/api/assets/bulk-upload-check",
            json={"assets": [{"id": asset_id, "checksum": checksum}]},
        )
    except httpx.HTTPError as exc:
        return {"outcome": "unknown", "reason": f"transport error: {exc}"}
    if r.status_code >= 300:
        return {"outcome": "unknown", "reason": f"HTTP {r.status_code}: {r.text[:300]}"}
    try:
        body = r.json()
    except ValueError:
        return {"outcome": "unknown", "reason": "JSON ではない応答"}
    items = body.get("results") if isinstance(body, dict) else body
    if not isinstance(items, list) or len(items) != 1:
        return {"outcome": "unknown", "reason": f"results が 1 件ではない: {body!r:.300}"}
    item = items[0]
    if not isinstance(item, dict):
        return {"outcome": "unknown", "reason": f"result が object ではない: {item!r:.200}"}
    if item.get("id") != asset_id:
        return {"outcome": "unknown", "reason": f"id が一致しない: {item.get('id')!r}"}
    action = str(item.get("action", "")).lower()
    if action in ("accept", "upload"):
        outcome = "accept"
    elif action == "reject":
        outcome = "reject"
    else:
        return {"outcome": "unknown", "reason": f"未知の action: {action!r}", "raw": item}
    return {"outcome": outcome, "assetId": item.get("assetId"), "raw": item}


# サーバ「インスタンス」の同定候補にする (endpoint, field) の allowlist。
#
# 名前だけで拾うと、version 系エンドポイントの汎用的な `id`（ビルド ID や
# 起動 ID かもしれない）まで合格してしまう。エンドポイントごとに明示する。
# バージョンやライセンス状態は同じ版の全サーバで一致するので入れない。
IDENTITY_CANDIDATES = (
    ("/api/server/about", "instanceId"),
    ("/api/server/about", "serverId"),
    ("/api/server/config", "instanceId"),
    ("/api/server/storage", "instanceId"),
)

IDENTITY_ENDPOINTS = ("/api/server/about", "/api/server/config", "/api/server/version")


def collect_identity(client: httpx.Client) -> dict[str, str]:
    """allowlist した (endpoint, field) の値を集める."""
    bodies: dict[str, Any] = {}
    for path in IDENTITY_ENDPOINTS:
        r = client.get(path)
        print(f"GET {path} -> {r.status_code}")
        if r.status_code == 200:
            bodies[path] = r.json()
            show(f"server info: {path}", bodies[path])
    found: dict[str, str] = {}
    for path, field in IDENTITY_CANDIDATES:
        value = bodies.get(path, {}).get(field)
        if isinstance(value, str) and value:
            found[f"{path}#{field}"] = value
    return found


def probe_identity(
    client: httpx.Client,
    results: dict[str, bool],
    baseline: Path | None,
    identity_out: Path | None,
    accept_unverified: bool,
) -> None:
    """命題 1: 転送先を「安定して」同定できるか.

    値が存在するだけでは足りない。再起動・更新をまたいで同じ値であることまで
    確かめないと、更新のたびに別の転送先と誤認して全件を再アップロードする。
    そのため 2 回目の実行と付き合わせる。
    """
    print("\n--- 1. 転送先の同定 ---")
    found = collect_identity(client)
    print(f"\n候補: {json.dumps(found, ensure_ascii=False, indent=2)}")

    r = client.get("/api/users/me")
    print(f"GET /api/users/me -> {r.status_code}")
    user_id = r.json().get("id") if r.status_code == 200 else None
    check(results, "認証ユーザ ID が取れる", bool(user_id), f"user_id={user_id!r}")

    if identity_out is not None:
        identity_out.write_text(json.dumps(found, ensure_ascii=False, indent=2))
        print(f"候補を {identity_out} に保存しました")

    if not found:
        check(
            results,
            "サーバインスタンス固有の識別子がある",
            False,
            f"{[f'{p}#{f}' for p, f in IDENTITY_CANDIDATES]} のいずれも取れない",
        )
        return
    check(results, "サーバインスタンス固有の識別子がある", True, ", ".join(found))

    if baseline is None:
        if accept_unverified:
            print(
                "\n注意: --accept-unverified-identity が指定されました。安定性は未検証です。\n"
                "  Task 11 に「明示登録した転送先 UUID を使う」代替を記録すること。"
            )
            check(results, "識別子が再起動をまたいで安定している", True, "未検証を明示的に許容")
        else:
            check(
                results,
                "識別子が再起動をまたいで安定している",
                False,
                "--identity-baseline が未指定。Immich を再起動してから "
                "--identity-baseline <前回の出力> で再実行するか、"
                "--accept-unverified-identity で代替設計を採る",
            )
        return

    previous = json.loads(baseline.read_text())
    stable = [k for k, v in found.items() if previous.get(k) == v]
    check(
        results,
        "識別子が再起動をまたいで安定している",
        bool(stable),
        f"一致={stable} "
        f"変化={ {k: (previous.get(k), v) for k, v in found.items() if k not in stable} }",
    )


def probe_upload_cycle(
    client: httpx.Client, results: dict[str, bool], cleanup: bool, sample: Path | None
) -> None:
    """命題 2〜4: bulk-upload-check の往復、deviceAssetId、checksum encoding."""
    run_id = uuid.uuid4().hex[:12]
    if sample is not None:
        blob = sample.read_bytes()
        filename, content_type = sample.name, "application/octet-stream"
    else:
        blob = make_unique_png()
        filename, content_type = f"mediaferry-probe-{run_id}.png", "image/png"

    # S324: Immich のプロトコルが SHA-1 を要求する。暗号用途ではない。
    digest = hashlib.sha1(blob).digest()  # noqa: S324
    hex_sum = digest.hex()
    b64_sum = base64.b64encode(digest).decode()
    device_asset_id = f"mediaferry:probe:{run_id}"
    print(f"\n--- 2〜4. アップロード往復 (run={run_id}, {len(blob)} bytes) ---")
    print(f"sha1 hex   = {hex_sum}")
    print(f"sha1 base64= {b64_sum}")

    # アップロード前は存在しないはず。unknown を PASS にしない。
    before = {
        enc: bulk_check(client, f"pre-{run_id}", val)
        for enc, val in (("hex", hex_sum), ("base64", b64_sum))
    }
    show("bulk-upload-check (アップロード前)", before)
    bulk_encodings = [enc for enc, v in before.items() if v["outcome"] == "accept"]
    check(
        results,
        "bulk-upload-check が受理する checksum encoding がある",
        bool(bulk_encodings),
        f"accept={bulk_encodings} 結果={ {k: v['outcome'] for k, v in before.items()} }",
    )
    check(
        results,
        "未アップロードの checksum が reject されない",
        all(v["outcome"] != "reject" for v in before.values()),
        json.dumps({k: v["outcome"] for k, v in before.items()}),
    )

    # x-immich-checksum の encoding を探る。base64 を先に試す。
    asset_id = None
    used_header_encoding = None
    for enc, value in (("base64", b64_sum), ("hex", hex_sum)):
        r = client.post(
            "/api/assets",
            data={
                "deviceAssetId": device_asset_id,
                "deviceId": f"mediaferry-probe-{run_id}",
                "fileCreatedAt": "2026-01-01T00:00:00.000+00:00",
                "fileModifiedAt": "2026-01-01T00:00:00.000+00:00",
                "isFavorite": "false",
            },
            files={"assetData": (filename, io.BytesIO(blob), content_type)},
            headers={"x-immich-checksum": value},
        )
        print(f"POST /api/assets (x-immich-checksum={enc}) -> {r.status_code}")
        if r.status_code < 300:
            created = r.json()
            show("upload response", created)
            asset_id = created.get("id")
            used_header_encoding = enc
            check(
                results,
                "アップロードが created として成立した",
                str(created.get("status", "created")).lower() != "duplicate",
                f"status={created.get('status')!r}",
            )
            break
        print(r.text[:500])
    check(
        results,
        "x-immich-checksum の encoding を確定できた",
        used_header_encoding is not None,
        f"encoding={used_header_encoding}",
    )
    if asset_id is None:
        check(results, "bulk-upload-check が既存資産 ID を返す", False, "アップロード失敗")
        check(results, "deviceAssetId を読み戻せる", False, "アップロード失敗")
        return

    # アップロード後は reject として、その資産 ID が返るはず。
    # これが返らないと「サーバ成功・ローカル未記録」から再開できない。
    after = {
        enc: bulk_check(client, f"post-{run_id}", val)
        for enc, val in (("hex", hex_sum), ("base64", b64_sum))
    }
    show("bulk-upload-check (アップロード後)", after)
    matched = [
        enc for enc, v in after.items() if v["outcome"] == "reject" and v.get("assetId") == asset_id
    ]
    check(
        results,
        "bulk-upload-check が既存資産 ID を返す",
        bool(matched),
        f"一致した encoding={matched or 'なし'} / 期待 assetId={asset_id} / "
        f"結果={ {k: (v['outcome'], v.get('assetId')) for k, v in after.items()} }",
    )
    print(f"\n>>> Task 11 に記録する: bulk encoding = {matched or bulk_encodings}")
    print(f">>> Task 11 に記録する: header encoding = {used_header_encoding}")

    r = client.get(f"/api/assets/{asset_id}")
    print(f"GET /api/assets/{{id}} -> {r.status_code}")
    got = r.json().get("deviceAssetId") if r.status_code == 200 else None
    check(
        results,
        "deviceAssetId を読み戻せる",
        got == device_asset_id,
        f"got={got!r} sent={device_asset_id!r}",
    )

    if not cleanup:
        print(f"\n注意: 資産 {asset_id} が残っています。--cleanup で削除できます")
        return
    # httpx の .delete() は body を取らないので request() を使う
    r = client.request("DELETE", "/api/assets", json={"ids": [asset_id], "force": True})
    print(f"DELETE /api/assets -> {r.status_code}")
    check(results, "後片付けが成功した", r.status_code < 300, f"status={r.status_code}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="小さな画像を実際にアップロードする")
    parser.add_argument("--cleanup", action="store_true", help="作成した資産を削除する")
    parser.add_argument(
        "--sample",
        type=Path,
        default=None,
        help="内蔵の PNG が受理されない場合に使う任意の画像ファイル",
    )
    parser.add_argument(
        "--identity-out",
        type=Path,
        default=None,
        help="サーバ識別子の候補を保存する。2 回目の実行で --identity-baseline に渡す",
    )
    parser.add_argument(
        "--identity-baseline",
        type=Path,
        default=None,
        help="前回保存した候補と付き合わせ、再起動をまたいで安定しているか確かめる",
    )
    parser.add_argument(
        "--accept-unverified-identity",
        action="store_true",
        help="安定性を検証せず、明示登録した転送先 UUID を使う代替を採ることを宣言する",
    )
    args = parser.parse_args()

    url = os.environ.get("IMMICH_URL")
    key = os.environ.get("IMMICH_API_KEY")
    if not url or not key:
        print("IMMICH_URL と IMMICH_API_KEY を設定してください", file=sys.stderr)
        return 1

    results: dict[str, bool] = {}
    with httpx.Client(
        base_url=url.rstrip("/"),
        headers={"x-api-key": key, "Accept": "application/json"},
        timeout=TIMEOUT,
        follow_redirects=True,
    ) as client:
        probe_identity(
            client,
            results,
            baseline=args.identity_baseline,
            identity_out=args.identity_out,
            accept_unverified=args.accept_unverified_identity,
        )
        if args.write:
            probe_upload_cycle(client, results, cleanup=args.cleanup, sample=args.sample)
        else:
            print("\n(--write 未指定。命題 2〜4 は未検証のまま)")

    print("\n=== 判定 ===")
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not args.write:
        print("\nRESULT: INCOMPLETE — Task 11 の記録には --write --cleanup が必要")
        return 3
    if all(results.values()):
        print("\nRESULT: PASS — 仕様書 §18-3 は解消")
        return 0
    print("\nRESULT: FAIL — 失敗した命題は代替設計を Task 11 で決めること", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
