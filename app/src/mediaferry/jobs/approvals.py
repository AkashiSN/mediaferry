"""承認待ちの解消（§9.10「承認待ちの解消」）.

`pre_existing` / `unknown` の資産は、別経路で既にアップロードされ、ユーザが
手動で時刻を修正済みかもしれない。**承認を得てから書き戻す。**

却下も用意する。無いと、既に正しい日時が入っている資産について「補正不要」と
判断しても承認待ちを消せず、一覧に残り続ける。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ..adapters.immich import ImmichClient
from ..clock import now_iso
from ..core.lease_pulse import with_lease_pulse
from ..db.connection import immediate
from ..db.jobs import JobContext, LeaseLost
from ..db.profiles import ProfileRegistry
from ..db.uploads import ClaimLost, UploadRepository
from .preflight import PreflightCache

WAITING = "awaiting_datetime_approval"


class ApprovalNotPossible(RuntimeError):
    """承認・却下できる状態ではない."""


class ApprovalService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        uploads: UploadRepository,
        destinations,  # noqa: ANN001 - DestinationRepository
        registry: ProfileRegistry,
        open_client: Callable[[sqlite3.Row], ImmichClient],
        preflight: PreflightCache,
    ) -> None:
        self._conn = conn
        self._uploads = uploads
        self._destinations = destinations
        self._registry = registry
        self._open_client = open_client
        self._preflight = preflight

    def approve(self, ctx: JobContext, record_id: str) -> None:
        """撮影日時を書き戻してから `complete` にする.

        **claim を取ってから外部へ触る。** 取らないと、同時に走った却下が
        `complete` を commit した後にリモートを変更しうる。
        """
        row = self._waiting(record_id)
        if row["remote_asset_id"] is None:
            raise ApprovalNotPossible("リモートの資産 ID が分からない")
        media = self._conn.execute(
            "SELECT captured_at FROM media_file WHERE id = ?", (row["media_file_id"],)
        ).fetchone()
        revision = self._destinations.revision(row["destination_revision_id"])
        # awaiting → fixing_datetime を CAS で取る。ここで負けたら却下が先。
        self._uploads.claim_for_approval(record_id, ctx.job_id, ctx.lease_token)
        settled = False
        try:
            self._uploads.prepare_side_effect(ctx, record_id, "fixing_datetime")
            # **所有権を確かめてから向き先を見る。** 再確認も鍵を付けた要求なので、
            # キャンセル済みのジョブから出さない（§14）。別のライブラリの資産の
            # 日時を書き換えないための確認であることは変わらない。
            self._preflight.assert_target(
                revision["id"], wait=lambda work: with_lease_pulse(ctx, work)
            )
            # 再確認の間にキャンセルが commit されていないか、もう一度見る。
            self._uploads.prepare_side_effect(ctx, record_id, "fixing_datetime")
            with self._open_client(revision) as client:
                # **PUT も pulse で囲む。** 遅い相手だと 60 秒を超え、claim が
                # 切れて「リモートは変更済みなのに commit できない」状態になる。
                with_lease_pulse(
                    ctx,
                    lambda: client.set_date_time_original(
                        row["remote_asset_id"], media["captured_at"]
                    ),
                    also=lambda: self._uploads.extend_claim(record_id, ctx.lease_token),
                    ownership_errors=(LeaseLost, ClaimLost),
                )
            self._uploads.finish_owned(ctx, record_id, "complete", expect_state="fixing_datetime")
            settled = True
        finally:
            if not settled:
                # 書き換えたかどうか分からない。承認待ちへ戻して人に見せる。
                self._uploads.release_from_approval(record_id, ctx.lease_token)

    def reject(self, record_id: str) -> None:
        """**リモートを一切変更せずに** `complete` にする."""
        self._waiting(record_id)
        self._settle(record_id)

    # ------------------------------------------------------------------
    def _waiting(self, record_id: str) -> sqlite3.Row:
        row = self._uploads.get(record_id)
        if row is None:
            raise ApprovalNotPossible(f"レコード {record_id} が無い")
        if row["state"] != WAITING:
            raise ApprovalNotPossible(f"承認待ちではない（{row['state']}）")
        if row["invalidated_at"] is not None:
            # 無効化されたレコードの日時を書き換えない（§10 の多重防御）。
            raise ApprovalNotPossible(f"無効化されている: {row['invalidated_reason']}")
        return row

    def _settle(self, record_id: str) -> None:
        """claim を持たない行なので、状態だけを動かす."""
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'complete', updated_at = ?"
                " WHERE id = ? AND state = ?",
                (now_iso(), record_id, WAITING),
            )
            if updated.rowcount != 1:
                raise ApprovalNotPossible(f"レコード {record_id} は既に動いている")
