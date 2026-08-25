"""アップロードの第 2 パス —— RAW/JPEG のスタッキング（§9.11）.

**状態機械には状態を足さない。** 送り終わったレコードのうち未評価のものを拾い、
組が成立すれば相手のスタックを作る。組めない場合も見送りとして決着させる
（未評価のまま残すと、送信のたびにライブラリ全体を舐めることになる）。

**開始時に宛先の現行リビジョンと `target_epoch` を固定する。** 旧 epoch の
`complete` は監査履歴として残る（無効化されない）ので、絞らないと別ライブラリへ
送った資産 ID を現行の資格情報で送ることになる。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..adapters.immich import (
    ImmichAuthFailed,
    ImmichClient,
    ImmichProtocolError,
    ImmichRedirected,
    ImmichRejected,
    ImmichUnavailable,
    RemoteStack,
)
from ..core import lease_pulse
from ..core.lease_pulse import with_lease_pulse
from ..core.uploads.stacking import Candidate, Group, resolve_group, stem_prefix
from ..db.jobs import JobContext, LeaseLost
from ..db.profiles import ProfileRegistry
from ..db.uploads import DestinationChanged, StackGroupChanged, UploadRepository
from .preflight import PreflightFailed

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


class DestinationUnusable(RuntimeError):
    """組ではなく**宛先の障害**. 第 2 パスごと打ち切る.

    認証失敗・redirect・5xx・接続不能は組固有ではないので、次の組へ進んでも同じ
    結果になる。未評価が N 件あれば、失効した鍵や停止したサーバへ N 組ぶんの
    要求を投げ続けることになる（timeout は既定 86400 秒）。
    """


@dataclass(frozen=True)
class StackOutcome:
    stacked: int
    skipped: int
    deferred: int  # 宛先の障害で未評価のまま残した数


class Stacker:
    def __init__(
        self,
        conn: sqlite3.Connection,
        uploads: UploadRepository,
        destinations,  # noqa: ANN001 - DestinationRepository
        registry: ProfileRegistry,
        open_client: Callable[[sqlite3.Row], ImmichClient],
        preflight,  # noqa: ANN001 - PreflightCache
    ) -> None:
        self._conn = conn
        self._uploads = uploads
        self._destinations = destinations
        self._registry = registry
        self._open_client = open_client
        self._preflight = preflight

    def run(self, ctx: JobContext, destination_id: str) -> StackOutcome:
        revision = self._destinations.current(destination_id)
        epoch, cursor = revision["target_epoch"], ""
        stacked = skipped = deferred = 0
        # 行をまたいだ経過時間でも心拍を打つ。`with_lease_pulse` は処理が間隔より
        # 短く終わると 1 度も打たないので、短い見送りが続くと積もる。
        last_beat = time.monotonic()
        with self._open_client(revision) as client:
            while not ctx.cancelled():
                batch = self._uploads.unstacked_batch(destination_id, epoch, cursor, BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    cursor = row["id"]
                    if ctx.cancelled():
                        return StackOutcome(stacked, skipped, deferred)
                    try:
                        # **受け口の中で打つ。** 行頭の確認が偽を返した直後に
                        # キャンセルが commit されると、ここが `LeaseLost` を投げる。
                        # 外に置くと `JobRunner` の汎用経路がジョブを失敗にする。
                        last_beat = self._beat(ctx, last_beat)
                        result = self._one(ctx, client, revision, epoch, row)
                    except DestinationUnusable as exc:
                        ctx.emit("warning", f"宛先の障害でスタックを中断した: {exc}")
                        return StackOutcome(stacked, skipped, deferred + 1)
                    except DestinationChanged as exc:
                        # 固定した epoch 全体が無効。続けても同じ失敗を繰り返す。
                        ctx.emit("info", f"宛先の向き先が変わったのでスタックを中断した: {exc}")
                        return StackOutcome(stacked, skipped, deferred)
                    except StackGroupChanged as exc:
                        # 前提が変わった。**記録もしない**（次の送信で組み直す）。
                        # 次の行では現行のプロファイル版を読み直すので、進んでよい。
                        ctx.emit("info", f"組が変わったのでスタックを見送った: {exc}")
                        continue
                    except LeaseLost:
                        # **利用者が押したキャンセルを失敗として記録しない**（§9.9）。
                        if ctx.cancelled():
                            ctx.emit("info", "キャンセルを観測してスタックを中止した")
                            return StackOutcome(stacked, skipped, deferred)
                        raise
                    stacked += result == "stacked"
                    skipped += result == "skipped"
        return StackOutcome(stacked, skipped, deferred)

    # ------------------------------------------------------------------
    def _beat(self, ctx: JobContext, last_beat: float) -> float:
        """行をまたいだ心拍. **`assert_lease` を先に呼ぶ。**

        `extend_lease` は `cancelling` でも延ばすので、heartbeat だけだと
        キャンセル済みのリースを延ばし続ける。
        """
        if time.monotonic() - last_beat < lease_pulse.HEARTBEAT_INTERVAL:
            return last_beat
        ctx.assert_lease()
        ctx.heartbeat()
        return time.monotonic()

    def _one(
        self,
        ctx: JobContext,
        client: ImmichClient,
        revision: sqlite3.Row,
        epoch: int,
        row: sqlite3.Row,
    ) -> str:
        # **バッチは snapshot。** 組を記録すると相方の行もその場で決着するが、
        # 取り出し済みのバッチにはまだ残っている。読み直さずに進むと、**組ごとに
        # 資産の読み取りを 2 度**出したうえで guard が弾くことになる。
        current = self._uploads.get(row["id"])
        if current is None or current["stack_state"] is not None:
            return "settled"
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (row["media_file_id"],)
        ).fetchone()
        if media is None:  # pragma: no cover - FK が消させない
            raise StackGroupChanged("メディアが消えている")
        # **プロファイルの現行リビジョン**を使う（`immich.tags` と同じ層の判断）。
        # 読んだ版は組ごとに固定し、guard と記録で「まだ現行か」を確かめる。
        profile = self._registry.by_id(media["profile_id"])
        rule = profile.definition.stack
        destination_id = row["destination_id"]

        def refuse(reason: str) -> str:
            self._uploads.mark_skipped(ctx, row, destination_id, epoch, profile.revision_id, reason)
            return "skipped"

        if not rule.enabled:
            return refuse("カメラの種類がスタックを使わない")
        candidates = self._candidates(row, media, destination_id, epoch)
        if candidates is None:
            return refuse("カード上の観測が残っていない")
        mine = next(c for c in candidates if c.record_id == row["id"])
        decision = resolve_group(mine, candidates, rule)
        if not isinstance(decision, Group):
            return refuse(decision.reason)

        members = [
            self._uploads.record_for(destination_id, epoch, member.media_file_id)
            for member in decision.members
        ]
        if any(member is None for member in members):  # pragma: no cover - 直前に読んでいる
            raise StackGroupChanged("組のレコードが消えている")
        try:
            return self._settle(ctx, client, revision, epoch, decision, members, profile, row)
        except (
            ImmichUnavailable,
            ImmichAuthFailed,
            ImmichRedirected,
            PreflightFailed,
        ) as exc:
            # **未評価のまま残す。** 宛先が落ちている・鍵が失効しただけなら、
            # 直したあとの送信で自然に再試行される。`PreflightFailed` も同じ層で、
            # 向き先の再確認が通らない状態そのもの。
            raise DestinationUnusable(str(exc)) from exc
        except (ImmichRejected, ImmichProtocolError) as exc:
            # 再試行しても直らない。**理由を残して画面に出す。**
            #
            # **相手由来の文言も、こちらの method / path / 状態コードも混ぜない**
            # （§13）。`stack_reason` は 設定 › 送り先に、作業の履歴の文言は
            # 設定 › 作業の履歴に、どちらもそのまま並ぶ。詳しい中身は運用ログへ回す。
            logger.warning("送り先が組を受け付けなかった: %s", exc)
            ctx.emit("warning", "送り先が組を受け付けなかった")
            return refuse("送り先が組を受け付けなかった")

    def _candidates(
        self, row: sqlite3.Row, media: sqlite3.Row, destination_id: str, epoch: int
    ) -> list[Candidate] | None:
        """組の候補を、**観測 1 つにつき 1 つ**作る（§9.11）."""
        observations = self._uploads.sources_of(media["id"])
        if not observations:
            return None
        candidates: list[Candidate] = []
        for observation in observations:
            volume = observation["volume_instance_id"]
            prefix = stem_prefix(observation["rel_path"])
            for sibling in self._uploads.siblings_on_card(volume, prefix):
                candidate = self._candidate_of(sibling, volume, destination_id, epoch, row)
                if candidate is not None:
                    candidates.append(candidate)
        return candidates or None

    def _candidate_of(
        self,
        sibling: sqlite3.Row,
        volume: str,
        destination_id: str,
        epoch: int,
        row: sqlite3.Row,
    ) -> Candidate | None:
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (sibling["media_file_id"],)
        ).fetchone()
        if media is None:
            return None
        record = (
            row
            if sibling["media_file_id"] == row["media_file_id"]
            else self._uploads.record_for(destination_id, epoch, sibling["media_file_id"])
        )
        if record is None:
            # この宛先へ送っていない。組は成立しない（相方として数えない）。
            return None
        return Candidate(
            record_id=record["id"],
            media_file_id=media["id"],
            profile_id=media["profile_id"],
            volume_instance_id=volume,
            rel_path=sibling["rel_path"],
            copresent_key=sibling["copresent_key"],
            captured_at=media["captured_at"],
            captured_at_source=media["captured_at_source"],
            origin=record["origin"],
            state=record["state"],
            remote_asset_id=record["remote_asset_id"],
            invalidated=record["invalidated_at"] is not None,
        )

    def _settle(
        self,
        ctx: JobContext,
        client: ImmichClient,
        revision: sqlite3.Row,
        epoch: int,
        decision: Group,
        members: list[sqlite3.Row],
        profile,  # noqa: ANN001 - ProfileRef
        row: sqlite3.Row,
    ) -> str:
        destination_id = row["destination_id"]
        wanted_primary = decision.members[0].remote_asset_id
        self._guard(ctx, members, destination_id, epoch, revision, profile.revision_id)
        assets = [
            with_lease_pulse(ctx, lambda asset_id=member["remote_asset_id"]: client.asset(asset_id))
            for member in members
        ]
        existing = {asset.stack_id for asset in assets}
        if existing == {None}:
            stack = self._create(ctx, client, members, decision, revision, epoch, profile)
        else:
            stack = self._adopted(ctx, client, assets, decision)
            if stack is None:
                # **理由は「いま処理している行」に付ける。** 組の先頭に付けると、
                # 相方を処理したときに自分ではない行が決着してしまう。
                self._uploads.mark_skipped(
                    ctx,
                    row,
                    destination_id,
                    epoch,
                    profile.revision_id,
                    "相手側に別のスタックがある",
                )
                return "skipped"
        if stack.primary_asset_id != wanted_primary:
            self._guard(ctx, members, destination_id, epoch, revision, profile.revision_id)
            with_lease_pulse(ctx, lambda: client.set_stack_primary(stack.stack_id, wanted_primary))
            # **読み直して確かめる。** 応答を信じるのではなく、相手の現在の姿を見る。
            # `PUT` と読み直しの間に member を差し替えられることもあるので、
            # **id・primary・集合の 3 つ**を見る。
            moved = with_lease_pulse(ctx, lambda: client.stack_by_primary(wanted_primary))
            ours = {member.remote_asset_id for member in decision.members}
            if (
                moved is None
                or moved.stack_id != stack.stack_id
                or moved.primary_asset_id != wanted_primary
                or set(moved.asset_ids) != ours
            ):
                raise ImmichProtocolError("primary の差し替えが反映されていない")
        self._uploads.mark_stacked(
            ctx, members, destination_id, epoch, profile.revision_id, stack.stack_id
        )
        return "stacked"

    def _create(
        self,
        ctx: JobContext,
        client: ImmichClient,
        members: list[sqlite3.Row],
        decision: Group,
        revision: sqlite3.Row,
        epoch: int,
        profile,  # noqa: ANN001 - ProfileRef
    ) -> RemoteStack:
        # **`POST` の直前にもう一度通す**（GET を待っている間に前提は変わりうる）。
        self._guard(
            ctx, members, members[0]["destination_id"], epoch, revision, profile.revision_id
        )
        asset_ids = [member.remote_asset_id for member in decision.members]
        return with_lease_pulse(ctx, lambda: client.create_stack(asset_ids))

    def _adopted(
        self,
        ctx: JobContext,
        client: ImmichClient,
        assets,  # noqa: ANN001 - Sequence[RemoteAsset]
        decision: Group,
    ) -> RemoteStack | None:
        """既にスタックがある場合に、**それが我々の組そのものか**を確かめる.

        一致すれば「既にできている」として記録する（**中断からの回収経路**）。
        一致しなければ触らない —— 利用者が手で作った組を作り直さない。

        **見るのはローカルの組（`decision`）との一致。** 相手の応答から作った
        集合どうしを比べると、相手が値を選べる場面で取り違える。
        """
        ours = {member.remote_asset_id for member in decision.members}
        stack_ids = {asset.stack_id for asset in assets}
        if len(stack_ids) != 1 or None in stack_ids:
            # 一部だけスタック済み、または別々のスタック。
            return None
        primaries = {asset.stack_primary_asset_id for asset in assets}
        if len(primaries) != 1:
            # **資産ごとに別々の primary を名乗る相手には触らない。**
            return None
        primary = next(iter(primaries))
        if primary not in ours:
            # 主資産が組の外にある。**引く手がかりが無いので触らない。**
            return None
        found = with_lease_pulse(ctx, lambda: client.stack_by_primary(primary))
        if found is None or found.stack_id != next(iter(stack_ids)):
            # 引けた id が、資産が名乗っていた id と違う。
            return None
        if set(found.asset_ids) != ours:
            return None
        return found

    def _guard(
        self,
        ctx: JobContext,
        members: list[sqlite3.Row],
        destination_id: str,
        epoch: int,
        revision: sqlite3.Row,
        profile_revision_id: str,
    ) -> None:
        """**外部へ触る直前に必ず通す**（§9.10 の `_guard` と同じ作法）.

        prepare → preflight（相手待ちなので `with_lease_pulse` で囲む）→ prepare。
        前だけに置くと、向き先の再確認を待っている間に commit されたキャンセルを
        見落とす。**`POST` と `PUT` のそれぞれの直前で通す。**
        """
        self._uploads.guard_stack_group(ctx, members, destination_id, epoch, profile_revision_id)
        self._preflight.assert_target(revision["id"], wait=lambda work: with_lease_pulse(ctx, work))
        self._uploads.guard_stack_group(ctx, members, destination_id, epoch, profile_revision_id)
