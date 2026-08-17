"""単一の asyncio ワーカー.

SQLite の書き込みを 1 本に絞るため、同時に走るジョブは 1 つだけにする。
実処理は同期関数として書き、`asyncio.to_thread` へ逃がしてイベントループを
塞がないようにする。キャンセルは協調的で、ハンドラが `ctx.cancelled()` を
見て自分で降りる。

**ジョブごとに DB 接続を開く。** ハンドラにはその接続を渡し、`JobContext` も
同じ接続に束ね直す。§9.3 の手順 7 は「リースの確認」と「staged への遷移」を
1 つの `BEGIN IMMEDIATE` に入れるので、両者が別接続だと成立しない。

`stop()` は「今のジョブが終わったら降りる」を意味する。`to_thread` で
走っているハンドラは task の cancel では止まらないので、呼び出し側は
`run_forever()` の完了を待つ。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import replace

from ..db.connection import Database
from ..db.jobs import JobContext, JobStore

logger = logging.getLogger(__name__)

Handler = Callable[[JobContext, sqlite3.Connection], None]


class JobRunner:
    def __init__(self, database: Database, poll_interval: float = 0.5) -> None:
        self._database = database
        self._poll_interval = poll_interval
        self._handlers: dict[str, Handler] = {}
        self._stopping = asyncio.Event()
        self._poll_store: JobStore | None = None
        self._current: JobContext | None = None

    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    async def stop(self) -> None:
        """降りるよう伝える. 実際に終わるのは `run_forever()` の完了時.

        走っているジョブにはキャンセルを要求する。要求しないと、ハンドラは
        `ctx.cancelled()` が偽のまま最後まで走り、停止が「待つだけ」になる。
        """
        self._stopping.set()
        current, store = self._current, self._poll_store
        if current is not None and store is not None:
            await asyncio.to_thread(store.request_cancel, current.job_id)

    async def run_forever(self) -> None:
        poller = self._database.connect()
        self._poll_store = poll_store = JobStore(poller)
        try:
            while not self._stopping.is_set():
                ctx = await asyncio.to_thread(poll_store.claim_next)
                if ctx is None:
                    # 停止要求が来るまで待つ。来なければ次の周回で claim を試す。
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                    continue
                if self._stopping.is_set():
                    # claim を待っている間に停止要求が来た。この時点では
                    # `_current` がまだ None なので stop() は cancel を打てない。
                    # 掴んでしまったジョブは、ハンドラが最初のキャンセル確認で
                    # 降りるように cancel を立ててから渡す。
                    await asyncio.to_thread(poll_store.request_cancel, ctx.job_id)
                await self._run_one(ctx, poll_store)
        finally:
            self._poll_store = None
            poller.close()

    async def _run_one(self, ctx: JobContext, poll_store: JobStore) -> None:
        row = poll_store.get(ctx.job_id)
        handler = self._handlers.get(row["type"])
        if handler is None:
            poll_store.finish(
                ctx.job_id, ctx.lease_token, "failed", f"未登録のジョブ種別: {row['type']}"
            )
            return

        conn = await asyncio.to_thread(self._database.connect)
        store = JobStore(conn)
        # ハンドラの中の JobStore と ArtifactPublisher を同じ接続に揃える。
        ctx = replace(ctx, _store=store)
        self._current = ctx
        try:
            await asyncio.to_thread(handler, ctx, conn)
        except Exception as exc:  # noqa: BLE001 - どのジョブが落ちてもワーカーは生かす
            logger.exception("ジョブ %s が失敗した", ctx.job_id)
            store.finish(ctx.job_id, ctx.lease_token, "failed", str(exc))
            return
        finally:
            self._current = None
            if conn.in_transaction:  # pragma: no cover - 取りこぼしの検出用
                logger.error("ジョブ %s がトランザクションを開いたまま終わった", ctx.job_id)
                conn.execute("ROLLBACK")
            conn.close()
        # 状態の読み出しと決着を分けると、その間の cancel が上書きされる。
        poll_store.finish_claimed(ctx.job_id, ctx.lease_token)
