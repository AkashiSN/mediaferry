"""送信済みレコードの状態を確かめ直す（§9.10「ゴミ箱と消滅の追跡」）.

`remote_is_trashed` は `checking` 時点のスナップショットにすぎない。ゴミ箱の
保持期限を過ぎて資産が消えても「送信済み」のまま残るので、宛先ごとの明示操作で
照合し直す。

**自動で再アップロードはしない。** 消えていた資産は `remote_asset_id` を外して
「リモートに存在しない」と分かる形にし、ユーザが明示的に `pending` へ戻す。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ..adapters.immich import ImmichClient
from ..clock import now_iso
from ..db.jobs import JobContext
from ..db.uploads import UploadRepository
from .preflight import PreflightCache

# 1 回の照合に載せる件数は ImmichClient が分割する。ここでは全件を渡す。
COMPLETE = "complete"


@dataclass(frozen=True)
class RecheckOutcome:
    checked: int
    trashed: int
    vanished: int
    restored: int


class Rechecker:
    def __init__(
        self,
        uploads: UploadRepository,
        destinations,  # noqa: ANN001 - DestinationRepository
        open_client: Callable[[sqlite3.Row], ImmichClient],
        preflight: PreflightCache,
    ) -> None:
        self._uploads = uploads
        self._destinations = destinations
        self._open_client = open_client
        self._preflight = preflight

    def run(self, ctx: JobContext, destination_id: str) -> RecheckOutcome:
        # **キャンセルの確認はリモートへ触る前。** preflight も鍵を付けた要求
        # なので、キャンセル済みのジョブから出してよいものではない（§14）。
        if ctx.cancelled():
            return RecheckOutcome(0, 0, 0, 0)
        revision = self._destinations.current(destination_id)
        # 向き先が変わっていたら、別ライブラリの照合結果で上書きしてしまう。
        self._preflight.assert_target(revision["id"])

        # **現行 epoch だけを照合する。** 旧 epoch は別ライブラリへの履歴。
        # **黙って打ち切らない。** 上限で切ると「N 件確認した」の N が実際の
        # 件数と食い違い、消滅を見落とす。
        records = [
            row
            for row in self._uploads.records_for_recheck(destination_id, revision["target_epoch"])
            if row["checksum"] is not None
        ]
        if not records:
            return RecheckOutcome(0, 0, 0, 0)

        with self._open_client(revision) as client:
            outcomes = client.bulk_upload_check([(row["id"], row["checksum"]) for row in records])

        # **照合の最中にキャンセルされていたら、結果を書かずに降りる。** 書くと
        # 「キャンセルした」と表示しながら、リモートの観測を反映したことになる。
        if ctx.cancelled():
            return RecheckOutcome(0, 0, 0, 0)

        trashed = vanished = restored = 0
        for row in records:
            outcome = outcomes.get(row["id"])
            if outcome is None:
                continue
            if outcome.action == "accept":
                # サーバに無い。**送り直さない。** 見えるようにするだけ。
                self._stamp(row, asset_id=None, is_trashed=0)
                vanished += 1
                ctx.emit(
                    "warning",
                    "リモートに存在しない資産がある",
                    {"upload_record_id": row["id"]},
                )
                continue
            self._stamp(row, asset_id=outcome.asset_id, is_trashed=1 if outcome.is_trashed else 0)
            if outcome.is_trashed and not row["remote_is_trashed"]:
                trashed += 1
            if not outcome.is_trashed and row["remote_is_trashed"]:
                restored += 1
        return RecheckOutcome(
            checked=len(records), trashed=trashed, vanished=vanished, restored=restored
        )

    def _stamp(self, row: sqlite3.Row, asset_id: str | None, is_trashed: int) -> None:
        """claim を取らずに更新する. `complete` の行は誰も所有していない."""
        self._uploads.stamp_remote(
            row["id"], asset_id=asset_id, is_trashed=is_trashed, checked_at=now_iso()
        )
