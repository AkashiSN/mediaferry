"""送信済みレコードの状態を確かめ直す（§9.10「ゴミ箱と消滅の追跡」）.

`remote_is_trashed` は `checking` 時点のスナップショットにすぎない。ゴミ箱の
保持期限を過ぎて資産が消えても「送信済み」のまま残るので、宛先ごとの明示操作で
照合し直す。

**消えていた資産の記録は無効化する。** 無効化された記録は「この宛先の有効な
記録」ではなくなるので、そのメディアは通常の「まだ送っていない」へ戻る。
**この場では送らない** —— 送るのは利用者が通常経路で選んだときだけ。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ..adapters.immich import BULK_CHECK_BATCH, ImmichClient
from ..clock import now_iso
from ..core.lease_pulse import with_lease_pulse
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
    # 相手側で解けていた／崩れていたので未評価へ戻した組の数（§9.11）。
    unstacked: int = 0


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

    def _cancelled_or_raise(self, ctx: JobContext) -> RecheckOutcome:
        """**利用者が押したキャンセルを失敗として記録しない**（§9.9）.

        `assert_lease` は `cancelling` を通さないので、確認の直後にキャンセルが
        commit されるとリースを失った形で降りてくる。キャンセルでないリースの
        喪失はそのまま上げる（取り込み・結合・送信と同じ形）。
        """
        if ctx.cancelled():
            ctx.emit("info", "キャンセルを観測したので再確認を中止した")
            return RecheckOutcome(0, 0, 0, 0)
        raise

    def run(self, ctx: JobContext, destination_id: str) -> RecheckOutcome:
        # **キャンセルの確認はリモートへ触る前。** preflight も鍵を付けた要求
        # なので、キャンセル済みのジョブから出してよいものではない（§14）。
        if ctx.cancelled():
            return RecheckOutcome(0, 0, 0, 0)
        revision = self._destinations.current(destination_id)
        try:
            # **リースも preflight より先に見る。** `cancelled()` はジョブの状態
            # しか見ないので、`running` のままリースだけ失効した worker を止め
            # られない。最初のリモート要求はこの preflight なので、その前に置く。
            ctx.assert_lease()
            # 直後の照合まで満期で入る（`assert_lease` は見るだけで延ばさない）。
            ctx.heartbeat()
            # 向き先が変わっていたら、別ライブラリの照合結果で上書きしてしまう。
            # **`users/me` の待ち時間もリースを跨ぐ**ので、そこも心拍で守る
            # （囲まれるのは相手待ちだけ。DB へ触るのは待つ側だけのまま）。
            self._preflight.assert_target(
                revision["id"], wait=lambda work: with_lease_pulse(ctx, work)
            )
        except LeaseLost:
            return self._cancelled_or_raise(ctx)

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
                    # **待っている間もリースを延ばす。** `assert_lease` は見る
                    # だけなので、1 本の照合が 60 秒を超えると正常な再確認でも
                    # 必ずリースを失う（`JobRunner` はそれを failed にする）。
                    # 相手待ちの間 DB へ触るのは待つ側だけ（接続は 1 本のまま）。
                    outcomes.update(
                        with_lease_pulse(
                            ctx,
                            lambda batch=batch: client.bulk_upload_check(
                                [(row["id"], row["checksum"]) for row in batch]
                            ),
                        )
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
            unstacked = self._reconcile_stacks(ctx, revision)
        except LeaseLost:
            return self._cancelled_or_raise(ctx)

        for row in vanished:
            if row["id"] not in written:
                continue
            # **無効化して「まだ送っていない」へ戻す。** 送るのは利用者の明示操作。
            ctx.emit(
                "warning",
                "リモートに存在しないので、まだ送っていないものに戻した",
                {"upload_record_id": row["id"]},
            )
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
            unstacked=unstacked,
        )

    def _reconcile_stacks(self, ctx: JobContext, revision: sqlite3.Row) -> int:
        """スタックの現存とメンバー集合を照合し、崩れた組を未評価へ戻す（§9.11）.

        **資産の照合の後に走らせる。** 消滅した資産は `stamp_many` の
        `_reopen_stack_of` で既に組を開いており、開いた行は `stack_state` が
        NULL なのでここの対象から外れる。逆順だと同じ組を 2 度開く。

        **「無い」と「集合が違う」を 1 つの条件で見る。** どちらも
        「こちらが記録している組は現在の姿ではない」であり、戻した先の第 2 パスが
        相手を読み直して、作り直すか見送るかを決める（§9.11 の表）。
        """
        destination_id = revision["destination_id"]
        epoch = revision["target_epoch"]
        groups = self._uploads.stacked_groups(destination_id, epoch)
        if not groups:
            # **空振りの要求を出さない。**
            return 0
        # 相手へ触る前に、キャンセルとリースの両方を見る（batch の照合と同じ）。
        if ctx.cancelled():
            return 0
        ctx.assert_lease()
        ctx.heartbeat()
        with self._open_client(revision) as client:
            live = {
                stack.stack_id: frozenset(stack.asset_ids)
                for stack in with_lease_pulse(ctx, client.stacks)
            }
        # **照合の最中にキャンセルされていたら、結果を書かずに降りる。**
        if ctx.cancelled():
            return 0
        broken = [stack_id for stack_id, members in groups.items() if live.get(stack_id) != members]
        if not broken:
            return 0
        return self._uploads.reopen_stacks(ctx, destination_id, epoch, broken)


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
