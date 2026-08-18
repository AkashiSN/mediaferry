"""送信前の向き先の再確認（§10）.

`destination_revision.remote_user_id` は登録・編集の時点の観測値にすぎない。
宛先を編集しなくても、DNS・リバースプロキシ・Immich 本体の差し替えで同じ
`base_url` の先が別のライブラリに変わる。**あるリビジョンの最初の pair を
送る前に 1 回、`/api/users/me` を取り直して突き合わせる。**

結果は 1 ジョブ内で共有する。1000 件のアップロードで 1000 回叩かない。
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import Any

from ..adapters.immich import ImmichClient, ImmichError
from ..core.destinations.identity import fingerprint
from ..db.destinations import DestinationNotFound, DestinationRepository

# 成功の判定が有効な時間。20 時間級のジョブでも、この間隔で取り直す。
PREFLIGHT_TTL_SECONDS = 900.0


class PreflightFailed(RuntimeError):
    """向き先が変わっている、または確認できない.

    **そのリビジョンの pair は 1 バイトも送らない。**
    """


class PreflightCache:
    def __init__(
        self,
        repo: DestinationRepository,
        open_client: Callable[[sqlite3.Row], ImmichClient],
        ttl_seconds: float = PREFLIGHT_TTL_SECONDS,
    ) -> None:
        self._repo = repo
        self._open_client = open_client
        self._ttl = ttl_seconds
        self._failed: dict[str, PreflightFailed] = {}
        self._verified_at: dict[str, float] = {}

    def assert_target(
        self,
        revision_id: str,
        wait: Callable[[Callable[[], Any]], Any] | None = None,
    ) -> None:
        """向き先を確かめる. `wait` を渡すと**相手待ちだけ**をそれで囲む.

        `users/me` の応答はクライアントの timeout（既定 86400 秒）まで待ちうる
        ので、リース（60 秒）を跨ぐ。呼び出し側が `with_lease_pulse` を渡せば、
        待っている間も心拍が打てる。**DB へ触る部分（リビジョンの解決と資格
        情報の復号）は呼び出し側のスレッドに残す**（接続はスコープごとに 1 本）。
        """
        failure = self._failed.get(revision_id)
        if failure is not None:
            # 一度失敗したリビジョンへは、同じジョブ内でもう試さない。
            raise failure
        checked = self._verified_at.get(revision_id)
        if checked is not None and time.monotonic() - checked < self._ttl:
            return
        try:
            self._check(revision_id, wait)
        except PreflightFailed as exc:
            self._failed[revision_id] = exc
            raise
        self._verified_at[revision_id] = time.monotonic()

    def _check(
        self, revision_id: str, wait: Callable[[Callable[[], Any]], Any] | None = None
    ) -> None:
        try:
            revision = self._repo.revision(revision_id)
        except DestinationNotFound as exc:
            raise PreflightFailed(str(exc)) from exc
        expected = revision["remote_user_id"]
        if expected is None:
            raise PreflightFailed(
                f"リビジョン {revision_id} には向き先の記録が無い。接続を検証し直す"
            )
        try:
            with self._open_client(revision) as client:
                # **囲むのは相手待ちだけ。** クライアントの構築（資格情報の復号）は
                # DB を読むので、呼び出し側のスレッドで済ませてある。
                body = client.users_me() if wait is None else wait(client.users_me)
                # 記録側と同じ指紋にしてから比べる（`core.destinations.identity`）。
                raw = body.get("id")
                observed = fingerprint(raw if isinstance(raw, str) else None)
        except ImmichError as exc:
            raise PreflightFailed(f"向き先を確認できない: {exc}") from exc
        if observed != expected:
            raise PreflightFailed(
                f"向き先が変わっている（記録 {expected} / 現在 {observed}）。"
                "転送先の設定を確認し直す"
            )
