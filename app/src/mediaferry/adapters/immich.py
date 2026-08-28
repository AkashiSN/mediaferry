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
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any
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


# 識別子として許す形（RFC 3986 の unreserved）と長さの上限。Immich は UUID を返す。
_UNRESERVED_RE = re.compile(r"[A-Za-z0-9._~-]+")
# 点だけの値（dot-segment）。unreserved の集合に入るが、経路の意味を持つ。
_DOTS_RE = re.compile(r"\.+")
IDENTIFIER_MAX_CHARS = 128


def to_base64_checksum(sha1_hex: str) -> str:
    """DB は hex で持ち、Immich へは base64 で送る."""
    return base64.b64encode(bytes.fromhex(sha1_hex)).decode("ascii")


@dataclass(frozen=True)
class RemoteAsset:
    """相手が持っている資産の姿（読み取り）."""

    asset_id: str
    date_time_original: str | None
    is_trashed: bool
    # スタックに入っていなければ両方 None。**旧版は `stack` キーを持たない。**
    stack_id: str | None = None
    stack_primary_asset_id: str | None = None


@dataclass(frozen=True)
class RemoteStack:
    """相手が持っているスタック（読み取り）."""

    stack_id: str
    primary_asset_id: str
    asset_ids: tuple[str, ...]


class _CountingReader:
    """読んだ量を数えながら渡すだけのラッパ.

    **数えるのは送信スレッド、書くのは待つ側**（`with_lease_pulse`）。ここから
    DB へは触らない。

    `fileno` を下の stream へそのまま通す。httpx はこれで本文の長さを測り
    `content-length` を付ける。**隠すと chunked になる** —— 数十 GiB の本文を
    途中の proxy が受け取れる形とは限らない。
    """

    def __init__(self, stream: IO[bytes], on_bytes: Callable[[int], None]) -> None:
        self._stream = stream
        self._on_bytes = on_bytes

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        self._on_bytes(len(chunk))
        return chunk

    def fileno(self) -> int:
        return self._stream.fileno()


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
        on_bytes: Callable[[int], None] | None = None,
    ) -> UploadOutcome:
        """multipart で送る. ファイルはストリーミングで読む.

        `on_bytes` を渡すと、読み出すたびにその量を呼び出し側へ知らせる
        （進捗の分子になる）。
        """
        data = {
            "deviceAssetId": device_asset_id,
            "deviceId": "mediaferry",
            "fileCreatedAt": file_created_at,
            "fileModifiedAt": file_modified_at,
            "isFavorite": "false",
        }
        with path.open("rb") as stream:
            asset_data = stream if on_bytes is None else _CountingReader(stream, on_bytes)
            response = self._request(
                "POST",
                "/api/assets",
                # **本文を伴うので redirect を一切追わない。**
                allow_redirect=False,
                data=data,
                files={"assetData": (path.name, asset_data, "application/octet-stream")},
                headers={"x-immich-checksum": to_base64_checksum(sha1_hex)},
            )
        body = _as_object(response, "POST /api/assets")
        status = body.get("status")
        if status not in UPLOAD_STATUSES:
            raise ImmichProtocolError("POST /api/assets の status が未知")
        asset_id = _required_str(body, "id", "POST /api/assets")
        return UploadOutcome(asset_id=self._identifier(asset_id, "POST /api/assets"), status=status)

    def _identifier(self, value: str, label: str) -> str:
        """識別子を、保存・URL へ使う前に検めた上で返す.

        **相手は「こちらが読む値」を選べる。** 侵害された Immich は、受け取った
        `x-api-key` を `assetId` やタグの id として返せる。形は仕様どおりなので
        型の検査では落ちない。これを保存すると、暗号化したはずの鍵の平文が
        `upload_record.remote_asset_id` と API 応答に現れる。URL へ入れれば
        `_checked` の例外文（`job.error` とログ）にも届く。

        **許す形を並べる（allowlist）。拒む形を並べない。** 拒否の列挙は
        fail-open になる: `%74%65%73%74` のように符号化した鍵は「使えない文字」を
        1 つも含まないが、httpx はそのまま path に載せるし、可逆なので経路の先で
        平文に戻る。RFC 3986 の unreserved だけを、長さの上限付きで通す
        （Immich の識別子は UUID。§12.3 の実測）。

        **受け取る値だけでなく、送る値も通す。** `remote_asset_id` とタグの id は
        DB から読んで次の要求の URL に入る。この検査が無かった版が保存した行が
        残っているので、境界の両方向で検める。

        経路ごとに塞ぐのではなく、**adapter の境界で**弾く。
        """
        if not isinstance(value, str) or not value:
            raise ImmichProtocolError(f"{label} の識別子が文字列でない")
        if self._api_key in value:
            raise ImmichProtocolError(f"{label} の識別子に API キーが含まれている")
        if len(value) > IDENTIFIER_MAX_CHARS or _UNRESERVED_RE.fullmatch(value) is None:
            raise ImmichProtocolError(f"{label} の識別子が識別子の形をしていない")
        if _DOTS_RE.fullmatch(value) is not None:
            # **unreserved だけでも経路は変えられる。** `.` と `..` は RFC 3986 の
            # dot-segment で、`/api/tags/../assets` は要求の組み立てで
            # `/api/assets` へ畳まれる（別のエンドポイントを叩く）。
            raise ImmichProtocolError(f"{label} の識別子が経路の記号になっている")
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
        # **保存済みの識別子も境界で検める**（`_identifier`）。id は URL と本文の
        # 両方に入り、検査の無かった版が書いた行が DB に残っている。
        checked = [self._identifier(asset_id, "tag_assets の asset id") for asset_id in asset_ids]
        tag = self._identifier(tag_id, "tag_assets の tag id")
        self._request("PUT", f"/api/tags/{tag}/assets", json={"ids": checked})

    def asset(self, asset_id: str) -> RemoteAsset:
        """資産の現在の姿を読む（承認の画面に出す「現在値」）.

        **相手が返さない値は埋めない。** 分からないことを 0 や現在時刻で埋めると、
        画面が「変更なし」と「分からない」を区別できなくなる。
        """
        checked = self._identifier(asset_id, "GET /api/assets の asset id")
        body = _as_object(self._request("GET", f"/api/assets/{checked}"), "GET /api/assets")
        returned = self._identifier(_required_str(body, "id", "GET /api/assets"), "GET /api/assets")
        # **要求した資産と応答が対応することまで見る。** ここを見ないと、相手が
        # 別の資産を返したときに、その資産のスタックをこちらの組と取り違える。
        if returned != checked:
            raise ImmichProtocolError("GET /api/assets が要求と違う資産を返した")
        exif = body.get("exifInfo")
        when = exif.get("dateTimeOriginal") if isinstance(exif, dict) else None
        stack_id, stack_primary = self._stack_field(body, "GET /api/assets")
        return RemoteAsset(
            asset_id=returned,
            date_time_original=when if isinstance(when, str) and when else None,
            is_trashed=bool(body.get("isTrashed")),
            stack_id=stack_id,
            stack_primary_asset_id=stack_primary,
        )

    def _stack_field(self, body: dict[str, Any], label: str) -> tuple[str | None, str | None]:
        """`AssetResponseDto.stack` を読む.

        **「無い」と「形が違う」を分ける。** キーが無い（`stack` を知らない版）のも
        `null`（スタックに入っていない、実 Immich の正規形）も `None` として扱う。
        **形が違うものを黙って `None` にはしない** —— スタック済みの資産を
        「入っていない」と読んで作り直すことになる。
        """
        stack = body.get("stack")
        if stack is None:
            return None, None
        if not isinstance(stack, dict):
            raise ImmichProtocolError(f"{label} の stack が object ではない")
        return (
            self._identifier(_required_str(stack, "id", label), label),
            self._identifier(_required_str(stack, "primaryAssetId", label), label),
        )

    def create_stack(self, asset_ids: Sequence[str]) -> RemoteStack:
        """スタックを作る.

        **既存スタックを吸収しうる。** 呼ぶ前に全員の `stack` を見ること（§9.11）。
        """
        checked = [self._identifier(a, "POST /api/stacks の asset id") for a in asset_ids]
        if len(set(checked)) != len(checked):
            # **入力の重複を先に閉じる。** 重複したまま送ると、相手が畳んで返しても
            # 集合の比較が通ってしまう（[A, A, B] を送って [A, B] が返る）。
            raise ValueError("create_stack に同じ asset id が複数ある")
        body = _as_object(
            self._request(
                "POST",
                "/api/stacks",
                # **非冪等で既存スタックを吸収するので、redirect を追わない。**
                # `_request` は 303 でも method を変えずに再送する。
                allow_redirect=False,
                json={"assetIds": checked},
            ),
            "POST /api/stacks",
        )
        created = self._stack_from(body, "POST /api/stacks")
        # **要求した集合と全単射であることを確かめる。** 吸収の仕様がある以上、
        # 返ってきた集合が違えば「別のものを作った」ので、その id を確定させない。
        if len(created.asset_ids) != len(checked) or set(created.asset_ids) != set(checked):
            raise ImmichProtocolError("POST /api/stacks が要求と違う集合を返した")
        return created

    def stacks(self) -> list[RemoteStack]:
        """相手が持っているスタックを全部読む（再確認の照合）.

        **絞り込まない。** こちらが持つ `remote_stack_id` の現存とメンバー集合を
        まとめて照合するので、1 要求で足りる。**件数の上限は置かない** ——
        打ち切ると「照合した」が嘘になる（`records_for_recheck` と同じ）。

        **形が違えば protocol error にする**（`_stack_from`）。黙って
        「スタックが無い」と読むと、在る組を解けていると判断して作り直す。
        """
        response = self._request("GET", "/api/stacks")
        return [
            self._stack_from(item, "GET /api/stacks")
            for item in _as_array(response, "GET /api/stacks")
        ]

    def stack_by_primary(self, primary_asset_id: str) -> RemoteStack | None:
        """主資産から引く. 見つからなければ `None`."""
        checked = self._identifier(primary_asset_id, "GET /api/stacks の primary asset id")
        response = self._request("GET", "/api/stacks", params={"primaryAssetId": checked})
        for item in _as_array(response, "GET /api/stacks"):
            stack = self._stack_from(item, "GET /api/stacks")
            if stack.primary_asset_id == checked:
                return stack
        return None

    def set_stack_primary(self, stack_id: str, asset_id: str) -> None:
        """代表を差し替える. **冪等**なので redirect の扱いは既定のまま."""
        stack = self._identifier(stack_id, "PUT /api/stacks の stack id")
        asset = self._identifier(asset_id, "PUT /api/stacks の primary asset id")
        self._request("PUT", f"/api/stacks/{stack}", json={"primaryAssetId": asset})

    def _stack_from(self, body: dict[str, Any], label: str) -> RemoteStack:
        """**壊れた応答を DB へ確定させない。** 形が違えば protocol error にする."""
        assets = body.get("assets")
        if not isinstance(assets, list) or not assets:
            raise ImmichProtocolError(f"{label} の応答に assets が無い")
        ids = tuple(
            self._identifier(_required_str(a, "id", label), label)
            for a in assets
            if isinstance(a, dict)
        )
        if len(ids) != len(assets):
            raise ImmichProtocolError(f"{label} の assets に object でない要素がある")
        if len(set(ids)) != len(ids):
            raise ImmichProtocolError(f"{label} の assets に重複がある")
        stack = RemoteStack(
            stack_id=self._identifier(_required_str(body, "id", label), label),
            primary_asset_id=self._identifier(_required_str(body, "primaryAssetId", label), label),
            asset_ids=ids,
        )
        # **primary は必ず member。** 外れていると、primary の検査が永久に一致せず
        # `PUT` を打ち続ける。
        if stack.primary_asset_id not in stack.asset_ids:
            raise ImmichProtocolError(f"{label} の primaryAssetId が assets に無い")
        return stack

    def set_date_time_original(self, asset_id: str, when: str) -> None:
        asset = self._identifier(asset_id, "set_date_time_original の asset id")
        self._request("PUT", f"/api/assets/{asset}", json={"dateTimeOriginal": when})

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


def _as_array(response: httpx.Response, label: str) -> list[dict[str, Any]]:
    """JSON を object の配列として読む. 壊れた応答も protocol error に正規化する."""
    try:
        body = response.json()
    except ValueError as exc:
        raise ImmichProtocolError(f"{label} の応答が JSON ではない") from exc
    if not isinstance(body, list):
        raise ImmichProtocolError(f"{label} の応答が配列ではない")
    for item in body:
        if not isinstance(item, dict):
            # **黙って読み飛ばさない**（「N 件見た」と言いながら見ていない状態を作る）。
            raise ImmichProtocolError(f"{label} の応答に object でない要素がある")
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
        if action == "reject" and (not isinstance(asset_id, str) or not asset_id):
            # **型もここで見る。** 数値を返されると `_identifier` の照合が
            # `TypeError` になり、プロトコルの違いではなく内部例外として落ちる。
            raise ImmichProtocolError("reject なのに文字列の assetId が無い")
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
