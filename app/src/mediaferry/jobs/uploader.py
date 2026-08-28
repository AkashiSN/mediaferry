"""Immich へのアップロード（§9.10）.

1 ジョブが 1 宛先を担当し、claim できるレコードが無くなるまで 1 件ずつ進める。
**逐次実行**である。ジョブ内で並行させると状態遷移の commit を別スレッドから
行うことになり、接続をスコープごとに 1 本に保てない。**並列度の設定は持たない**
—— 律速はネットワークと相手側の取り込みで、増やせるのは失敗の同時多発だけ。

各段階は冪等で、どこで落ちても `checking` からやり直せる。**送信中の中断は
サーバ側の成否が不明なので `needs_recheck` に落とす。** チェックサム照合で
既存が見つかれば `asset_known` へ進むため、二重アップロードにはならない。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..adapters.immich import (
    ImmichAuthFailed,
    ImmichClient,
    ImmichError,
    ImmichProtocolError,
    ImmichRedirected,
    ImmichUnavailable,
)
from ..clock import now_iso
from ..core.lease_pulse import with_lease_pulse
from ..core.uploads.decisions import datetime_plan, origin_after_upload, tags_to_apply
from ..db.jobs import JobContext, LeaseLost
from ..db.profiles import ProfileRegistry
from ..db.uploads import CLAIMABLE_STATES, ClaimLost, NoLongerEligible, UploadRepository
from .preflight import PreflightCache

logger = logging.getLogger(__name__)

# 再試行の間隔（秒）。指数バックオフ。キャンセルを見るために刻んで待つ。
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 30.0
CANCEL_POLL_SECONDS = 0.2
# 失敗の理由に残す長さ。**秘密は含めない**（例外の文字列に URL は入るが鍵は入らない）。
ERROR_CHARS = 500


@dataclass(frozen=True)
class UploadOutcome:
    sent: int
    skipped: int
    failed: int
    awaiting: int


@dataclass
class _Reported:
    """画面に出す進み具合. **`_Progress` とは別物**（あちらは解放先を決める内部フラグ）.

    合計はジョブの開始時に 1 回だけ数えた値で、送っている間にも対象は増減しうる。
    **数えるのは送信スレッド、DB へ書くのは待つ側**（`with_lease_pulse` の心拍と
    `run` のループ）。この分業のおかげで、接続はスコープごとに 1 本のままで済む。
    """

    file_count: int
    bytes_total_all: int
    file_index: int = 0
    rel_path: str = ""
    bytes_total: int = 0
    bytes_done: int = 0
    bytes_done_all: int = 0

    def begin(self, rel_path: str, bytes_total: int) -> None:
        """次の 1 件へ移る. `rel_path` は `DATA_ROOT` からの相対パス."""
        self.file_index += 1
        self.rel_path = rel_path
        self.bytes_total = bytes_total
        self.bytes_done = 0

    def add(self, count: int) -> None:
        """送信スレッドから呼ばれる. **DB へは触らない。**"""
        self.bytes_done += count
        self.bytes_done_all += count

    def settle(self) -> None:
        """1 件が決着した. **送らずに済んだぶんも分子に入れる。**

        既にリモートにある件（`bulk_upload_check` の `reject`）や見送った件は
        1 バイトも送らないのに、大きさは分母に入っている。拾わないと、重複の
        多い回に「ほとんど終わっているのにバーが数 % のまま」になる。

        足りない分だけを足す（引かない）。送った量が大きさを超えている件は、
        そのまま残して `snapshot` に合計を伸ばさせる。
        """
        missing = self.bytes_total - self.bytes_done
        if missing > 0:
            self.bytes_done += missing
            self.bytes_done_all += missing

    def snapshot(self) -> dict[str, Any]:
        # **合計を追い越したら合計を伸ばす。** 走っている間に対象が増減しうる
        # ので、`12 / 10 件` のような嘘を画面に出さない。
        return {
            "phase": "upload",
            "rel_path": self.rel_path,
            "file_index": self.file_index,
            "file_count": max(self.file_count, self.file_index),
            "bytes_done": self.bytes_done,
            "bytes_total": max(self.bytes_total, self.bytes_done),
            "bytes_done_all": self.bytes_done_all,
            "bytes_total_all": max(self.bytes_total_all, self.bytes_done_all),
        }


@dataclass
class _Progress:
    """1 レコードの進み具合.

    `touched_remote` は「リモートに触った可能性があるか」。降りるときの
    戻し先（`pending` か `needs_recheck` か）を決める。
    """

    settled: bool = False
    touched_remote: bool = False


class Uploader:
    def __init__(
        self,
        conn: sqlite3.Connection,
        uploads: UploadRepository,
        destinations,  # noqa: ANN001 - DestinationRepository
        registry: ProfileRegistry,
        data_root: Path,
        open_client: Callable[[sqlite3.Row], ImmichClient],
        preflight: PreflightCache,
        max_attempts: int = 3,
    ) -> None:
        self._conn = conn
        self._uploads = uploads
        self._destinations = destinations
        self._registry = registry
        self._data_root = data_root
        self._open_client = open_client
        self._preflight = preflight
        self._max_attempts = max_attempts

    def run(self, ctx: JobContext, destination_id: str) -> UploadOutcome:
        sent = skipped = failed = awaiting = 0
        # **分母は開始時に 1 回だけ数える。** 1 件ごとに数え直すと、送るたびに
        # 全件の走査が増える。ずれは `snapshot` が合計を伸ばして吸収する。
        file_count, bytes_total_all = self._uploads.sendable_totals(destination_id)
        reported = _Reported(file_count=file_count, bytes_total_all=bytes_total_all)
        while True:
            if ctx.cancelled():
                break
            # **1 件ごとに自分でリースを延ばす。** `with_lease_pulse` が心拍を打つ
            # のは「1 回の送信が `HEARTBEAT_INTERVAL` より長かったとき」だけなので、
            # 写真のように 1 件ずつが速いと**一度も打たれない**。ループが延ばさない
            # と、何十枚も送る道がリースの満期（60 秒）で落ちる。
            #
            # **`assert_lease` は要らない。** キャンセルは上の `cancelled()` が見て
            # おり、失効と横取りは `heartbeat` 自身が `LeaseLost` で捕まえる
            # （`extend_lease` は満期前の行しか更新しない）。
            #
            # **進捗も同じ UPDATE に乗せる**（書き込みは増えない）。1 件ずつが速い
            # 道では、ここが唯一の進捗の書き手になる。
            ctx.heartbeat(reported.snapshot())
            # **リビジョンは pair ごとに、claim と同じトランザクションで決まる**（§8）。
            record = self._uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
            if record is None:
                break
            reported.begin(*self._extent(record))
            try:
                state = self._guarded(ctx, record, reported)
            except LeaseLost:
                if ctx.cancelled():
                    # **利用者が押したキャンセルを失敗として記録しない**（§9.9）。
                    # 所有権の確認（`assert_lease`）は `cancelling` を通さないので、
                    # 送信の途中でキャンセルすると必ずここへ来る。レコードは
                    # `_guarded` の finally が `needs_recheck` へ落としている。
                    ctx.emit("info", "キャンセルを観測したので送信を中止した")
                    break
                raise
            if state not in CLAIMABLE_STATES:
                # **決着した件は必ず分子に入れる。** 送らずに済んだ件（既に
                # リモートにある・見送った）も分母には入っている。`claim_next` が
                # また拾う状態で降りたときだけ足さない —— 次に取り直したときに
                # もう一度数えるので、二重に足さないため。
                reported.settle()
            sent += state == "complete"
            failed += state == "failed"
            awaiting += state == "awaiting_datetime_approval"
            skipped += state in ("pending", "needs_recheck", "refused")
        return UploadOutcome(sent=sent, skipped=skipped, failed=failed, awaiting=awaiting)

    def _extent(self, record: sqlite3.Row) -> tuple[str, int]:
        """いま送る 1 件の相対パスと大きさ. **絶対パスは進捗に載せない。**"""
        media = self._conn.execute(
            "SELECT rel_path, size_bytes FROM media_file WHERE id = ?",
            (record["media_file_id"],),
        ).fetchone()
        return (media["rel_path"], media["size_bytes"])

    # ------------------------------------------------------------------
    def _guarded(self, ctx: JobContext, record: sqlite3.Row, reported: _Reported) -> str:
        """**claim を取った後の全経路をここで囲む。**

        資格情報の復号、クライアントの構築、プロファイルの解決まで try の外に
        置くと、そこで落ちたときにレコードが `checking` + claim のまま残る。
        `claim_next` は進行中の状態を拾わないので、**次の起動まで誰も触れなく
        なる**。決着が付かずに抜ける経路はすべて解放してから送出する。
        """
        progress = _Progress()
        try:
            reason = self._uploads.check_eligibility(record)
            if reason is not None:
                # 送らずに無効化して理由を残す（§10）。
                self._uploads.refuse(record["id"], ctx.lease_token, reason)
                ctx.emit("warning", f"送信を見送った: {reason}", {"upload_record_id": record["id"]})
                progress.settled = True
                return "refused"
            state = self._one(ctx, record, progress, reported)
            progress.settled = True
            return state
        except NoLongerEligible as exc:
            # 送る直前に根拠が崩れていた。**まだ 1 バイトも送っていない。**
            self._uploads.refuse(record["id"], ctx.lease_token, str(exc))
            ctx.emit("warning", f"送信を見送った: {exc}", {"upload_record_id": record["id"]})
            progress.settled = True
            return "refused"
        finally:
            if not progress.settled:
                # **副作用の境界を越えたかで戻し先を変える。** 越える前の失敗
                # （preflight、資格情報の復号、クライアントの構築）は確定的なので
                # `pending` へ。越えた後だけ「サーバ側の成否が不明」＝
                # `needs_recheck` にする。
                if progress.touched_remote:
                    self._release_unknown(ctx, record)
                else:
                    self._release_pending(ctx, record)

    def _one(
        self, ctx: JobContext, record: sqlite3.Row, progress: _Progress, reported: _Reported
    ) -> str:
        revision = self._destinations.revision(record["destination_revision_id"])
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (record["media_file_id"],)
        ).fetchone()
        profile = self._registry.by_id(media["profile_id"])
        with self._open_client(revision) as client:
            try:
                return self._steps(
                    ctx, client, record, media, profile, revision["id"], progress, reported
                )
            except ImmichUnavailable as exc:
                return self._retry_or_fail(ctx, record, exc)
            except ImmichAuthFailed, ImmichRedirected, ImmichProtocolError:
                # 鍵が違う・向き先がおかしい・応答の形が違う。再試行しても
                # 変わらないので、ジョブごと止めて人に見せる。送信の途中かも
                # しれないので、claim は `_guarded` の finally が外す。
                raise
            except ImmichError as exc:
                self._uploads.finish(
                    record["id"],
                    ctx.lease_token,
                    "failed",
                    expect_state=self._uploads.get(record["id"])["state"],
                    last_error=str(exc)[:ERROR_CHARS],
                    attempts=record["attempts"] + 1,
                )
                return "failed"

    def _guard(
        self,
        ctx: JobContext,
        record: sqlite3.Row,
        revision_id: str,
        state: str,
        progress: _Progress | None = None,
        verify_eligibility: bool = False,
    ) -> None:
        """**ネットワークへ触る直前に必ず通す。**

        向き先の再確認（TTL 内ならキャッシュが返るので通信は増えない）と、
        リース・claim の確認をまとめる。レコードの先頭で 1 回だけにすると、
        70 GiB の送信が TTL を跨いだ後の tag / 日時 PUT が**別のライブラリへ
        飛ぶ**（asset ID が偶然存在すれば他人の資産を書き換える）。
        """
        # **prepare → preflight → prepare の 2 段。** 向き先の再確認も鍵を付けた
        # 要求なので、所有権を確かめる前に出さない（§14）。そして再確認は相手待ち
        # なので、リース（60 秒）より長くなりうる。前だけに置くと「直前に確かめた」
        # という保証が消え、確認している間に commit されたキャンセルを見落とす。
        #
        # **後段も同じ強さで確かめる。** §10 の根拠まで見直す呼び出しで後段だけ
        # 弱くすると、相手を待っている間に結合をやり直されたとき、いま結合中の
        # グループの構成ファイルを送る（保証が消えるのはキャンセルだけではない）。
        self._uploads.prepare_side_effect(
            ctx, record["id"], state, verify_eligibility=verify_eligibility
        )
        # **相手待ちも心拍で守る。** `users/me` はクライアントの timeout まで
        # 待ちうるので、リース（60 秒）を跨ぐ。囲まれるのは相手待ちだけで、
        # DB へ触るのは待つ側のまま（接続はスコープごとに 1 本）。
        self._preflight.assert_target(revision_id, wait=lambda work: with_lease_pulse(ctx, work))
        self._uploads.prepare_side_effect(
            ctx, record["id"], state, verify_eligibility=verify_eligibility
        )
        if progress is not None:
            # ここを抜けた後は、リモートに触った可能性がある。
            progress.touched_remote = True

    def _steps(
        self,
        ctx: JobContext,
        client: ImmichClient,
        record: sqlite3.Row,
        media: sqlite3.Row,
        profile,  # noqa: ANN001 - ProfileRef
        revision_id: str,
        progress: _Progress,
        reported: _Reported,
    ) -> str:
        # 1. checking —— 外部への要求なので、直前に所有権と向き先を確かめる。
        self._guard(ctx, record, revision_id, "checking", progress)
        outcome = client.bulk_upload_check([(record["id"], media["sha1"])])[record["id"]]
        first_check = record["first_check_result"] or outcome.action
        if outcome.action == "reject":
            # **一度確定した `created_by_us` は降格させない。** 後処理の一時障害で
            # 再開したとき、自分が上げた資産は当然 `reject` で返ってくる。ここで
            # 付け直すと自作の資産が `unknown` になり、タグと日時の扱いが
            # 「他人が上げたもの」に変わる（§9.10）。
            if record["origin"] == "created_by_us":
                origin = "created_by_us"
            else:
                origin = "pre_existing" if first_check == "reject" else "unknown"
                # **こちらはまだ何も変えていない。** 既存資産へのタグ付けが
                # 最初の変更になるので、その前に §10 の根拠を見直す（§8）。
                # 自分が作った資産（`created_by_us`）は既に置いてあるので、
                # ここで見送っても取り消せない。
                self._guard(ctx, record, revision_id, "checking", progress, verify_eligibility=True)
            self._uploads.advance_owned(
                ctx,
                record["id"],
                "asset_known",
                expect_state="checking",
                first_check_result=first_check,
                remote_asset_id=outcome.asset_id,
                remote_is_trashed=1 if outcome.is_trashed else 0,
                remote_checked_at=now_iso(),
                origin=origin,
            )
        else:
            self._uploads.advance_owned(
                ctx,
                record["id"],
                "uploading",
                expect_state="checking",
                first_check_result=first_check,
                remote_checked_at=now_iso(),
            )
            # 送信の直前にもう一度。ここを通ってから初めて 1 バイトを送る。
            # **§10 の根拠もここで見直す**（`verify_eligibility`）。
            self._guard(ctx, record, revision_id, "uploading", progress, verify_eligibility=True)
            uploaded = self._send(ctx, client, record, media, reported)
            origin = origin_after_upload(first_check, uploaded.status)
            if origin != "created_by_us":
                # **`duplicate` で返った資産は他人のものかもしれない。**
                # こちらはまだ何も変えていない（既にあったので作ってもいない）。
                # タグ付けが最初の変更になるので、その前に §10 を見直す（§8）。
                self._guard(
                    ctx, record, revision_id, "uploading", progress, verify_eligibility=True
                )
            # 2〜3. asset_known。ここで初めて「サーバ側に存在する」が確定する。
            # **commit も所有権付きで行う。** 送信中にキャンセルされていたら
            # ここで止まり、`_guarded` が needs_recheck へ落とす。
            self._uploads.advance_owned(
                ctx,
                record["id"],
                "asset_known",
                expect_state="uploading",
                remote_asset_id=uploaded.asset_id,
                origin=origin,
            )

        row = self._uploads.get(record["id"])
        # 4. tagging —— **変更を伴う呼び出しごとに guard する。**
        self._uploads.advance_owned(ctx, record["id"], "tagging", expect_state="asset_known")
        for name in tags_to_apply(profile.definition.immich, row["origin"]):
            self._guard(ctx, record, revision_id, "tagging", progress)
            tag_id = client.find_tag(name)
            if tag_id is None:
                self._guard(ctx, record, revision_id, "tagging", progress)
                tag_id = client.create_tag(name)
            self._guard(ctx, record, revision_id, "tagging", progress)
            client.tag_assets(tag_id, [row["remote_asset_id"]])

        # 5. fixing_datetime
        plan = datetime_plan(
            profile.definition.immich,
            profile.definition.timestamp.timezone_policy,
            media["captured_at"],
            row["origin"],
        )
        if plan.proposed is None:
            self._uploads.finish_owned(ctx, record["id"], "complete", expect_state="tagging")
            return "complete"
        if not plan.automatic:
            ctx.emit(
                "info",
                f"日時の補正に承認が要る: {plan.reason}",
                {"upload_record_id": record["id"]},
            )
            # **承認を求める時点の「現在値」を控える**（§13 の差分表示）。画面を
            # 開くたびに N 件ぶんの HTTP を出さないために、ここで 1 度だけ読む。
            self._uploads.finish_owned(
                ctx,
                record["id"],
                "awaiting_datetime_approval",
                expect_state="tagging",
                remote_datetime_original=self._observed_datetime(
                    ctx, client, record, revision_id, progress, row["remote_asset_id"]
                ),
                remote_checked_at=now_iso(),
            )
            return "awaiting_datetime_approval"
        self._uploads.advance_owned(ctx, record["id"], "fixing_datetime", expect_state="tagging")
        # 既存資産の日時を書き換える。**最も取り返しがつかない副作用**なので、
        # 直前に必ず確かめる。
        self._guard(ctx, record, revision_id, "fixing_datetime", progress)
        client.set_date_time_original(row["remote_asset_id"], plan.proposed)
        self._uploads.finish_owned(ctx, record["id"], "complete", expect_state="fixing_datetime")
        return "complete"

    def _observed_datetime(  # noqa: PLR0913
        self,
        ctx: JobContext,
        client: ImmichClient,
        record: sqlite3.Row,
        revision_id: str,
        progress: _Progress,
        asset_id: str,
    ) -> str | None:
        """リモートの現在の日時を読む. **読めなくても送信の結果は変えない。**

        承認の画面に出すための値なので、相手が答えられなくても「分からない」まま
        承認待ちにする（ここで失敗にすると、送信そのものが失敗として記録される）。
        """
        try:
            self._guard(ctx, record, revision_id, "tagging", progress)
            return client.asset(asset_id).date_time_original
        except ImmichError:
            return None

    def _send(  # noqa: ANN202
        self,
        ctx: JobContext,
        client: ImmichClient,
        record: sqlite3.Row,
        media: sqlite3.Row,
        reported: _Reported,
    ):
        """**送信中もリースと claim を延ばす。** 28 GiB は 84.5 秒（実測）.

        **送信スレッドは数えるだけ**（`reported.add`）で、その値を DB へ書くのは
        心拍を打つ待つ側（`progress`）。接続はスコープごとに 1 本のまま。
        """
        path = self._data_root / media["rel_path"]
        device_asset_id = f"mediaferry:{media['id']}"
        modified = datetime.fromtimestamp(media["mtime_ns"] / 1e9, tz=UTC).isoformat()
        return with_lease_pulse(
            ctx,
            lambda: client.upload_asset(
                path,
                sha1_hex=media["sha1"],
                device_asset_id=device_asset_id,
                file_created_at=media["captured_at"],
                file_modified_at=modified,
                on_bytes=reported.add,
            ),
            also=lambda: self._uploads.extend_claim(record["id"], ctx.lease_token),
            progress=reported.snapshot,
            # claim の延長が失敗しても、送信スレッドを残したまま抜けない。
            ownership_errors=(LeaseLost, ClaimLost),
        )

    def _retry_or_fail(self, ctx: JobContext, record: sqlite3.Row, exc: Exception) -> str:
        attempts = record["attempts"] + 1
        current = self._uploads.get(record["id"])["state"]
        if attempts >= self._max_attempts:
            self._uploads.finish(
                record["id"],
                ctx.lease_token,
                "failed",
                expect_state=current,
                attempts=attempts,
                last_error=str(exc)[:ERROR_CHARS],
            )
            ctx.emit(
                "error",
                "アップロードに失敗した（上限まで再試行）",
                {"upload_record_id": record["id"]},
            )
            return "failed"
        self._uploads.release_to(
            record["id"],
            ctx.lease_token,
            "pending",
            attempts=attempts,
            last_error=str(exc)[:ERROR_CHARS],
        )
        self._sleep(ctx, min(BACKOFF_BASE_SECONDS**attempts, BACKOFF_MAX_SECONDS))
        return "pending"

    def _release_pending(self, ctx: JobContext, record: sqlite3.Row) -> None:
        """リモートに触る前に降りた. **確定的な失敗**なので `pending` へ戻す."""
        try:
            self._uploads.release_to(record["id"], ctx.lease_token, "pending")
        except ClaimLost:
            logger.warning("claim を失った状態で降りた: %s", record["id"])

    def _release_unknown(self, ctx: JobContext, record: sqlite3.Row) -> None:
        """サーバ側の成否が不明なまま降りる. 次回 `checking` から照合し直す.

        **冪等にする。** すでに終端まで進んでいたり、claim を失っていたりする
        場合は何もしない（`ClaimLost` を握りつぶす）。ここで送出すると、
        本来の失敗理由が隠れる。
        """
        try:
            self._uploads.release_to(record["id"], ctx.lease_token, "needs_recheck")
        except ClaimLost:
            logger.warning("claim を失った状態で降りた: %s", record["id"])

    def _sleep(self, ctx: JobContext, seconds: float) -> None:
        """待つ間もキャンセルを見る."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if ctx.cancelled():
                return
            time.sleep(CANCEL_POLL_SECONDS)
