"""Immich の HTTP クライアント（§9.10 / §12.4）.

**redirect を追わない。** `x-api-key` はカスタムヘッダなので、cross-origin の
redirect でもクライアントは剥がさない。誤設定や侵害されたエンドポイントが
外部へ 301 を返すと、API キーがそのまま渡る。同一 origin のときだけ 1 回追い、
**本文を伴う要求では一切追わない**（ファイルは 1 回目の送信で EOF に達している
ので、追うと空か途中までの本文を送る）。

チェックサムは **base64 に統一**する（Phase 0 の実測。`x-immich-checksum` は
base64、`bulk-upload-check` は両方を受理する）。片方に揃えないと取り違えが起きる。

ファイルは**ストリーミングで送る**。数十 GiB をメモリへ載せない。

**例外に相手の応答本文を入れない。** 本文は相手が決める値で、受け取った
`x-api-key` を返す実装がありうる。載せると `last_error` として DB に永続化され、
API と画面にも出る（§12.3）。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# 1 回の bulk-upload-check に載せる件数。応答が大きくなりすぎない範囲で減らす。
BULK_CHECK_BATCH = 500
# 同一 origin の redirect だけを、この回数まで手動で追う（本文の無い要求のみ）。
MAX_SAME_ORIGIN_REDIRECTS = 3
CHECK_ACTIONS = frozenset({"accept", "reject"})
UPLOAD_STATUSES = frozenset({"created", "duplicate"})


class ImmichError(RuntimeError):
    """Immich とのやり取りが期待どおりに終わらなかった."""


class ImmichAuthFailed(ImmichError):
    """401 / 403。API キーが違うか失効している."""


class ImmichRejected(ImmichError):
    """4xx。要求そのものが受理されない（再試行しても変わらない）."""


class ImmichUnavailable(ImmichError):
    """5xx・接続不能・タイムアウト。再試行の余地がある."""


class ImmichRedirected(ImmichError):
    """別の origin へ飛ばされた、または本文を伴う要求が redirect された.

    **秘密も本文も送らずに止める。**
    """


class ImmichProtocolError(ImmichError):
    """応答の形が契約と違う.

    黙って読み飛ばすと、「N 件確認した」と表示しながら実際には何も見ていない
    状態になる。
    """


@dataclass(frozen=True)
class CheckOutcome:
    action: str  # accept / reject
    asset_id: str | None
    is_trashed: bool


@dataclass(frozen=True)
class UploadOutcome:
    asset_id: str
    status: str  # created / duplicate


def to_base64_checksum(sha1_hex: str) -> str:
    """DB は hex で持ち、Immich へは base64 で送る."""
    return base64.b64encode(bytes.fromhex(sha1_hex)).decode("ascii")


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 86400) -> None:
        self._base_url = base_url.rstrip("/")
        # 相手から受け取った識別子と突き合わせるためだけに持つ（`_identifier`）。
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"x-api-key": api_key, "accept": "application/json"},
            timeout=httpx.Timeout(timeout_seconds, connect=30.0),
            # **既定で追わない。** 同一 origin のときだけ手動で追う。
            follow_redirects=False,
        )

    def __enter__(self) -> ImmichClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    def users_me(self) -> dict[str, Any]:
        """向き先の同定に使う（§8）. preflight もこれを叩く."""
        return self._request("GET", "/api/users/me").json()

    def bulk_upload_check(self, items: Sequence[tuple[str, str]]) -> dict[str, CheckOutcome]:
        """`(key, sha1_hex)` の列を照合する. 戻り値は key ごとの結果.

        **要求した key と応答の key が全単射であることを確かめる。**
        """
        outcomes: dict[str, CheckOutcome] = {}
        for start in range(0, len(items), BULK_CHECK_BATCH):
            batch = items[start : start + BULK_CHECK_BATCH]
            payload = {
                "assets": [{"id": key, "checksum": to_base64_checksum(sha1)} for key, sha1 in batch]
            }
            body = self._request("POST", "/api/assets/bulk-upload-check", json=payload).json()
            parsed = _parsed_check(body, [key for key, _ in batch])
            outcomes.update(
                {
                    key: outcome
                    if outcome.asset_id is None
                    else replace(
                        outcome,
                        asset_id=self._identifier(outcome.asset_id, "bulk-upload-check"),
                    )
                    for key, outcome in parsed.items()
                }
            )
        return outcomes

    def upload_asset(
        self,
        path: Path,
        *,
        sha1_hex: str,
        device_asset_id: str,
        file_created_at: str,
        file_modified_at: str,
    ) -> UploadOutcome:
        """multipart で送る. ファイルはストリーミングで読む."""
        data = {
            "deviceAssetId": device_asset_id,
            "deviceId": "mediaferry",
            "fileCreatedAt": file_created_at,
            "fileModifiedAt": file_modified_at,
            "isFavorite": "false",
        }
        with path.open("rb") as stream:
            response = self._request(
                "POST",
                "/api/assets",
                # **本文を伴うので redirect を一切追わない。**
                allow_redirect=False,
                data=data,
                files={"assetData": (path.name, stream, "application/octet-stream")},
                headers={"x-immich-checksum": to_base64_checksum(sha1_hex)},
            )
        body = _as_object(response, "POST /api/assets")
        status = body.get("status")
        if status not in UPLOAD_STATUSES:
            raise ImmichProtocolError("POST /api/assets の status が未知")
        asset_id = _required_str(body, "id", "POST /api/assets")
        return UploadOutcome(asset_id=self._identifier(asset_id, "POST /api/assets"), status=status)

    def _identifier(self, value: str, label: str) -> str:
        """相手から受け取った識別子を、保存・URL へ使う前に検めた上で返す.

        **相手は「こちらが読む値」を選べる。** 侵害された Immich は、受け取った
        `x-api-key` を `assetId` やタグの id として返せる。形は仕様どおりなので
        型の検査では落ちない。これを保存すると、暗号化したはずの鍵の平文が
        `upload_record.remote_asset_id` と API 応答に現れる。URL へ入れれば
        `_checked` の例外文（`job.error` とログ）にも届く。

        経路ごとに塞ぐのではなく、**adapter の境界で 1 度だけ**弾く。
        """
        if self._api_key in value:
            raise ImmichProtocolError(f"{label} の識別子に API キーが含まれている")
        if any(character in value for character in "/?#") or value.strip() != value:
            # 次の要求の path に入る値なので、経路を変えられる形を受け取らない。
            raise ImmichProtocolError(f"{label} の識別子に使えない文字がある")
        return value

    def find_tag(self, name: str) -> str | None:
        """既存のタグを探す（読み取りのみ）."""
        tags = self._request("GET", "/api/tags").json()
        if not isinstance(tags, list):
            raise ImmichProtocolError("GET /api/tags の応答が配列ではない")
        for tag in tags:
            if not isinstance(tag, dict) or not isinstance(tag.get("name"), str):
                raise ImmichProtocolError("GET /api/tags の要素の形が違う")
            if tag["name"] == name:
                return self._identifier(_required_str(tag, "id", "GET /api/tags"), "GET /api/tags")
        return None

    def create_tag(self, name: str) -> str:  # noqa: D401
        """タグを作る（変更を伴う）.

        **`find_tag` と分けてある。** 呼び出し側は「変更を伴う呼び出しの直前」
        ごとに所有権と向き先を確かめる必要があり、探索と作成が 1 メソッドに
        まとまっていると、その間に guard を挟めない。
        """
        body = _as_object(self._request("POST", "/api/tags", json={"name": name}), "POST /api/tags")
        return self._identifier(_required_str(body, "id", "POST /api/tags"), "POST /api/tags")

    def ensure_tag(self, name: str) -> str:
        """探して無ければ作る. **guard を挟めないので、ジョブからは使わない。**

        `needs_immich` の疎通確認のような、所有権の要らない場面向け。
        """
        return self.find_tag(name) or self.create_tag(name)

    def tag_assets(self, tag_id: str, asset_ids: Sequence[str]) -> None:
        self._request("PUT", f"/api/tags/{tag_id}/assets", json={"ids": list(asset_ids)})

    def set_date_time_original(self, asset_id: str, when: str) -> None:
        self._request("PUT", f"/api/assets/{asset_id}", json={"dateTimeOriginal": when})

    # ------------------------------------------------------------------
    def _request(
        self, method: str, path: str, allow_redirect: bool = True, **kwargs: Any
    ) -> httpx.Response:
        url = path
        for _ in range(MAX_SAME_ORIGIN_REDIRECTS + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                # 例外の型と URL だけ。ヘッダ（API キー）も応答本文も含めない。
                raise ImmichUnavailable(
                    f"{method} {path} に失敗した: {type(exc).__name__}"
                ) from exc
            if not response.is_redirect:
                return self._checked(method, path, response)
            if not allow_redirect:
                raise ImmichRedirected(
                    f"{method} {path} が redirect された。本文を伴う要求は追わない"
                )
            url = self._same_origin_target(response)
        raise ImmichRedirected(f"{method} {path} の redirect が多すぎる")

    def _same_origin_target(self, response: httpx.Response) -> str:
        """scheme・host・port が同じときだけ追う. それ以外は秘密を送らない."""
        location = response.headers.get("location", "")
        target = urlsplit(str(response.url.join(location)))
        base = urlsplit(self._base_url)
        if (target.scheme, target.hostname, target.port) != (
            base.scheme,
            base.hostname,
            base.port,
        ):
            raise ImmichRedirected("別の origin へ redirect された")
        return str(response.url.join(location))

    def _checked(self, method: str, path: str, response: httpx.Response) -> httpx.Response:
        """**応答本文を例外へ載せない。** 相手が API キーを echo しうる."""
        if response.status_code in (401, 403):
            raise ImmichAuthFailed(f"{method} {path} が {response.status_code}")
        if response.status_code >= 500:
            raise ImmichUnavailable(f"{method} {path} が {response.status_code}")
        if response.status_code >= 400:
            logger.debug("%s %s が %s", method, path, response.status_code)
            raise ImmichRejected(f"{method} {path} が {response.status_code}")
        return response


def _as_object(response: httpx.Response, label: str) -> dict[str, Any]:
    """JSON を object として読む. 壊れた応答も protocol error に正規化する."""
    try:
        body = response.json()
    except ValueError as exc:
        raise ImmichProtocolError(f"{label} の応答が JSON ではない") from exc
    if not isinstance(body, dict):
        raise ImmichProtocolError(f"{label} の応答が object ではない")
    return body


def _required_str(body: dict[str, Any], key: str, label: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise ImmichProtocolError(f"{label} の応答に {key} が無い")
    return value


def _parsed_check(body: Any, expected: Sequence[str]) -> dict[str, CheckOutcome]:
    """応答を検証して写像にする. 欠落・重複・未知の値は protocol error.

    **型も見る。** proxy や別バージョンが scalar や list を返したとき、
    `AttributeError` ではなく「プロトコルが違う」として分類・表示できるようにする。
    """
    if not isinstance(body, dict):
        raise ImmichProtocolError("bulk-upload-check の応答が object ではない")
    results = body.get("results")
    if not isinstance(results, list):
        raise ImmichProtocolError("bulk-upload-check の応答に results が無い")
    outcomes: dict[str, CheckOutcome] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ImmichProtocolError("bulk-upload-check の results の要素が object ではない")
        key = result.get("id")
        if not isinstance(key, str):
            raise ImmichProtocolError("bulk-upload-check の結果に文字列の id が無い")
        if key in outcomes:
            raise ImmichProtocolError("bulk-upload-check の応答に同じ id が 2 度現れた")
        action = result.get("action")
        if action not in CHECK_ACTIONS:
            raise ImmichProtocolError("bulk-upload-check の action が未知")
        asset_id = result.get("assetId")
        if action == "reject" and not asset_id:
            raise ImmichProtocolError("reject なのに assetId が無い")
        trashed = result.get("isTrashed")
        if action == "reject" and not isinstance(trashed, bool):
            # **既定を False にしない。** 欄が無い応答を「ゴミ箱に無い」と
            # 決めつけると、消された資産を送信済みとして扱う根拠が消える。
            raise ImmichProtocolError("reject なのに isTrashed が bool でない")
        outcomes[key] = CheckOutcome(action=action, asset_id=asset_id, is_trashed=bool(trashed))
    missing = [key for key in expected if key not in outcomes]
    extra = [key for key in outcomes if key not in expected]
    if missing or extra:
        raise ImmichProtocolError(
            f"bulk-upload-check の応答が要求と一致しない（欠落 {len(missing)} 件 /"
            f" 余分 {len(extra)} 件）"
        )
    return outcomes
