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

from ..adapters.immich import BULK_CHECK_BATCH, ImmichClient
from ..clock import now_iso
from ..db.jobs import JobContext, LeaseLost
from ..db.uploads import Stamp, UploadRepository
from .preflight import PreflightCache

# **分割はこちらで行う。** adapter に全件を渡すと、その内部ループの合間に
# キャンセルを見る隙が無く、最初の batch の途中で中止しても残りを全部送る。
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

        outcomes = {}
        try:
            with self._open_client(revision) as client:
                for start in range(0, len(records), BULK_CHECK_BATCH):
                    # **batch ごとに、キャンセルとリースの両方を見る。**
                    # 最初の batch も飛ばさない（preflight は相手待ちで、その間に
                    # キャンセルが commit されうる）。`ctx.cancelled()` はジョブの
                    # 状態しか見ないので、失効したリースのまま鍵付きの要求を
                    # 送り続けるのを止められない（§14）。
                    if ctx.cancelled():
                        # 続きは送らない。ここまでの結果も書かずに降りる。
                        return RecheckOutcome(0, 0, 0, 0)
                    ctx.assert_lease()
                    batch = records[start : start + BULK_CHECK_BATCH]
                    outcomes.update(
                        client.bulk_upload_check([(row["id"], row["checksum"]) for row in batch])
                    )

            # **照合の最中にキャンセルされていたら、結果を書かずに降りる。** 書くと
            # 「キャンセルした」と表示しながら、リモートの観測を反映したことになる。
            if ctx.cancelled():
                return RecheckOutcome(0, 0, 0, 0)

            vanished = [row for row in records if _action_of(outcomes, row) == "accept"]
            present = [row for row in records if _action_of(outcomes, row) == "reject"]
            # **結果は 1 つのトランザクションで書く。** 1 行ずつ commit すると、
            # 途中でキャンセルやリースの失効が起きたときに「全件か 0 件か」ではなく
            # 中途半端な状態が残る。リースも同じ取引の中で確かめる。
            written = self._uploads.stamp_many(
                ctx,
                [_stamp_of(row, None, 0) for row in vanished]
                + [
                    _stamp_of(
                        row,
                        outcomes[row["id"]].asset_id,
                        int(outcomes[row["id"]].is_trashed),
                    )
                    for row in present
                ],
                checked_at=now_iso(),
            )
        except LeaseLost:
            # **利用者が押したキャンセルを失敗として記録しない**（§9.9）。
            # `assert_lease` は `cancelling` を通さないので、確認の直後に
            # キャンセルが commit されるとここへ来る。
            if ctx.cancelled():
                ctx.emit("info", "キャンセルを観測したので再確認を中止した")
                return RecheckOutcome(0, 0, 0, 0)
            raise

        for row in vanished:
            if row["id"] not in written:
                continue
            # **送り直さない。** 見えるようにするだけ。
            ctx.emit("warning", "リモートに存在しない資産がある", {"upload_record_id": row["id"]})
        skipped = len(records) - len(written)
        if skipped:
            # **黙って飛ばさない。** 「N 件確認した」の N と実際が食い違う。
            ctx.emit("info", f"再確認の最中に変わった行を飛ばした: {skipped} 件")
        trashed = sum(
            1
            for row in present
            if row["id"] in written
            and outcomes[row["id"]].is_trashed
            and not row["remote_is_trashed"]
        )
        restored = sum(
            1
            for row in present
            if row["id"] in written
            and not outcomes[row["id"]].is_trashed
            and row["remote_is_trashed"]
        )
        return RecheckOutcome(
            checked=len(written),
            trashed=trashed,
            vanished=sum(1 for row in vanished if row["id"] in written),
            restored=restored,
        )


def _stamp_of(row: sqlite3.Row, asset_id: str | None, is_trashed: int) -> Stamp:
    """結果に、**照合したときの行の姿**を添える（`stamp_many`）."""
    return Stamp(
        record_id=row["id"],
        asset_id=asset_id,
        is_trashed=is_trashed,
        expect_asset_id=row["remote_asset_id"],
        expect_checked_at=row["remote_checked_at"],
    )


def _action_of(outcomes: dict, record_id) -> str | None:  # noqa: ANN001, ANN401
    """応答に無い行は触らない（`_parsed_check` が全単射を保証するので通常は無い）."""
    outcome = outcomes.get(record_id["id"])
    return None if outcome is None else outcome.action
