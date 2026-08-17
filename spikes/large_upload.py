#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""巨大ファイルの Immich アップロード疎通試験 (Phase 0 検証 ③).

結合後の MP4 は 32GiB に達しうる。ストリーミング multipart で送って

  - 実際にファイル全体を転送できること
  - クライアント側のメモリがファイルサイズに比例しないこと
  - リバースプロキシの body size 上限に当たらないこと
  - アップロード後にサーバ側で資産として成立すること

を確かめる。判定は終了ステータスに反映する。

checksum の encoding は Task 9 (immich_probe.py) で確定した値を必ず渡す。
ヘッダと bulk body で異なる場合があるので別々の引数にしてある。

実行:
  export IMMICH_URL=<url>
  export IMMICH_API_KEY=<key>
  ./large_upload.py --file <32GiB の MP4> \
      --header-checksum-encoding <base64|hex> \
      --bulk-checksum-encoding <base64|hex> \
      --cleanup
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import resource
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import httpx

CHUNK = 4 * 1024 * 1024
# ストリーミング送信ができていれば、ファイルサイズによらず数百 MiB に収まる。
# 「サイズに比例していない」だけを条件にすると 32GiB に対して 3GiB の
# バッファリングでも通ってしまうので、絶対値の上限も併せて課す。
MEMORY_CAP_BYTES = 512 * 1024 * 1024
DEFAULT_MIN_SIZE_GIB = 31.0


def sha1_of(path: Path) -> bytes:
    # S324: Immich のプロトコルが SHA-1 を要求する。暗号用途ではない。
    h = hashlib.sha1()  # noqa: S324
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.digest()


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PiB"


def encode(digest: bytes, encoding: str) -> str:
    return base64.b64encode(digest).decode() if encoding == "base64" else digest.hex()


class CountingReader:
    """読み出したバイト数を数えるラッパ.

    重複判定でサーバが即座に応答すると、32GiB を一度も送っていないのに
    「成功・高スループット・低メモリ」に見える。実際に送ったバイト数を
    数えて、ファイルサイズと一致することを必須条件にする。
    """

    def __init__(self, fh: IO[bytes]) -> None:
        self._fh = fh
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        self.bytes_read += len(chunk)
        return chunk

    def __iter__(self) -> Iterator[bytes]:
        while chunk := self.read(CHUNK):
            yield chunk


def rss_bytes() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def bulk_outcome(client: httpx.Client, asset_id: str, checksum: str) -> dict[str, Any]:
    """Task 9 と同じ厳密さで bulk-upload-check を解釈する."""
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
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        return {"outcome": "unknown", "reason": f"results が 1 件の object ではない: {body!r:.300}"}
    item = items[0]
    if item.get("id") != asset_id:
        return {"outcome": "unknown", "reason": f"id が一致しない: {item.get('id')!r}"}
    action = str(item.get("action", "")).lower()
    if action in ("accept", "upload"):
        return {"outcome": "accept", "raw": item}
    if action == "reject":
        return {"outcome": "reject", "assetId": item.get("assetId"), "raw": item}
    return {"outcome": "unknown", "reason": f"未知の action: {action!r}", "raw": item}


def remote_size_of(asset: dict[str, Any]) -> int | None:
    exif = asset.get("exifInfo") or {}
    for key in ("fileSizeInByte", "fileSizeInBytes"):
        value = exif.get(key) or asset.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument(
        "--header-checksum-encoding",
        required=True,
        choices=("base64", "hex"),
        help="Task 9 で確定した x-immich-checksum の encoding",
    )
    parser.add_argument(
        "--bulk-checksum-encoding",
        required=True,
        choices=("base64", "hex"),
        help="Task 9 で確定した bulk-upload-check の encoding",
    )
    parser.add_argument("--cleanup", action="store_true", help="アップロード後に削除する")
    parser.add_argument("--timeout", type=float, default=86400.0)
    parser.add_argument(
        "--min-size-gib",
        type=float,
        default=DEFAULT_MIN_SIZE_GIB,
        help="この大きさ未満のファイルでは試験にならないので中止する",
    )
    args = parser.parse_args()

    url = os.environ.get("IMMICH_URL")
    key = os.environ.get("IMMICH_API_KEY")
    if not url or not key:
        print("IMMICH_URL と IMMICH_API_KEY を設定してください", file=sys.stderr)
        return 1

    path: Path = args.file
    size = path.stat().st_size
    minimum = int(args.min_size_gib * 1024**3)
    print(f"対象: {path} ({human(size)})")
    if size < minimum:
        print(
            f"中止: {human(size)} は下限 {human(minimum)} 未満。"
            "結合後の実サイズで測らないと §18-2 の判定にならない。\n"
            "  意図的に小さいファイルで試すなら --min-size-gib を明示的に下げ、"
            "その旨を findings に記録すること。",
            file=sys.stderr,
        )
        return 3

    t0 = time.monotonic()
    digest = sha1_of(path)
    t_hash = time.monotonic() - t0
    header_sum = encode(digest, args.header_checksum_encoding)
    bulk_sum = encode(digest, args.bulk_checksum_encoding)
    print(f"SHA-1: {digest.hex()} ({t_hash:.1f}s, {human(size / max(t_hash, 1e-9))}/s)")

    stamp = "2026-01-01T00:00:00.000+00:00"
    baseline_rss = rss_bytes()
    checks: list[tuple[str, bool, str]] = []

    with httpx.Client(
        base_url=url.rstrip("/"),
        headers={"x-api-key": key, "Accept": "application/json"},
        timeout=httpx.Timeout(args.timeout, connect=30.0),
        follow_redirects=True,
    ) as client:
        # 既に同じ内容が存在すると、サーバが body を読まずに応答して
        # 「送れた」ように見えてしまう。その場合は測定にならないので中止する。
        # 既存資産をこのスクリプトが勝手に消すことはしない。
        pre = bulk_outcome(client, str(path), bulk_sum)
        print(f"bulk-upload-check (事前) -> {pre}")
        if pre["outcome"] != "accept":
            print(
                f"\n中止: 事前チェックが accept ではない ({pre['outcome']})。\n"
                "  reject なら同じ内容が既に Immich にある。別のユニークな MP4 を用意するか、\n"
                "  既存資産を手動で削除してから再実行する。\n"
                "  unknown なら --bulk-checksum-encoding が違う可能性がある。",
                file=sys.stderr,
            )
            return 3

        t0 = time.monotonic()
        with path.open("rb") as fh:
            reader = CountingReader(fh)
            r = client.post(
                "/api/assets",
                data={
                    "deviceAssetId": f"mediaferry:spike:{digest.hex()[:16]}",
                    "deviceId": "mediaferry-spike",
                    "fileCreatedAt": stamp,
                    "fileModifiedAt": stamp,
                    "isFavorite": "false",
                },
                # ファイルライクを渡すと httpx はチャンク送信する。全体を
                # メモリに載せない。
                files={"assetData": (path.name, reader, "video/mp4")},
                headers={"x-immich-checksum": header_sum},
            )
        elapsed = time.monotonic() - t0
        peak_rss = rss_bytes()
        delta_rss = peak_rss - baseline_rss

        print(f"\nPOST /api/assets -> {r.status_code} ({elapsed:.1f}s)")
        print(f"送信バイト数: {reader.bytes_read} / {size}")
        print(f"スループット: {human(size / max(elapsed, 1e-9))}/s")
        print(f"RSS: baseline={human(baseline_rss)} peak={human(peak_rss)} 増分={human(delta_rss)}")
        print(f"応答: {r.text[:1000]}")

        cap = min(MEMORY_CAP_BYTES, size // 10)
        checks += [
            ("HTTP が成功した", r.status_code < 300, f"status={r.status_code}"),
            ("ファイル全体を送信した", reader.bytes_read == size, f"{reader.bytes_read}/{size}"),
            ("RSS 増分が上限内", delta_rss < cap, f"{human(delta_rss)} < {human(cap)}"),
        ]

        asset_id = r.json().get("id") if r.status_code < 300 else None
        if asset_id:
            g = client.get(f"/api/assets/{asset_id}")
            asset = g.json() if g.status_code == 200 else {}
            got_size = remote_size_of(asset)
            print(
                f"GET /api/assets/{{id}} -> {g.status_code} "
                f"type={asset.get('type')} remote_size={got_size}"
            )
            checks.append(
                (
                    "アップロード後に資産を取得できた",
                    g.status_code == 200,
                    f"status={g.status_code}",
                )
            )
            checks.append(
                (
                    "サーバ側のサイズが入力と一致する",
                    got_size == size,
                    f"remote={got_size} local={size}"
                    + (
                        ""
                        if got_size is not None
                        else " (サイズ欄が無い。対象版のフィールド名を要確認)"
                    ),
                )
            )
        else:
            checks.append(("アップロード後に資産を取得できた", False, "asset id 不明"))
            checks.append(("サーバ側のサイズが入力と一致する", False, "asset id 不明"))

        if args.cleanup and asset_id:
            d = client.request("DELETE", "/api/assets", json={"ids": [asset_id], "force": True})
            print(f"DELETE /api/assets -> {d.status_code}")
            post = bulk_outcome(client, f"post-delete-{digest.hex()[:8]}", bulk_sum)
            print(f"bulk-upload-check (削除後) -> {post}")
            checks.append(("後片付けが成功した", d.status_code < 300, f"status={d.status_code}"))
            checks.append(
                (
                    "削除後は再び accept になる",
                    post["outcome"] == "accept",
                    str(post["outcome"]),
                )
            )
        elif asset_id:
            print(f"注意: 資産 {asset_id} が残っています。--cleanup で削除できます")

        print("\n=== 判定 ===")
        for name, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    if all(ok for _, ok, _ in checks):
        print("\nRESULT: PASS — 仕様書 §18-2 は解消")
        return 0
    print(
        "\nRESULT: FAIL — リバースプロキシの body size 上限やタイムアウトを疑う。\n"
        "  解消できない場合は §18-2 の代替 (結合物は NAS のみに保持し、Immich には\n"
        "  元パートを上げる) を採用し、§10 の eligibility をどう変えるか Task 11 で決める。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
