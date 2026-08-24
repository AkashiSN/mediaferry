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
            self._fail(poll_store, ctx, f"未登録のジョブ種別: {row['type']}")
            return

        conn = await asyncio.to_thread(self._database.connect)
        store = JobStore(conn)
        # ハンドラの中の JobStore と ArtifactPublisher を同じ接続に揃える。
        ctx = replace(ctx, _store=store)
        self._current = ctx
        # **決着はハンドラの接続を閉じてから、ワーカーの接続で付ける。** 落ちた
        # ハンドラは書き込みトランザクションを開いたままのことがあり、その中で
        # 決着を書くと下の `ROLLBACK` が巻き戻す。合図の `emit` も
        # `BEGIN IMMEDIATE` を要るので、開いたままの接続では書けない。
        failure: Exception | None = None
        try:
            await asyncio.to_thread(handler, ctx, conn)
        except Exception as exc:  # noqa: BLE001 - どのジョブが落ちてもワーカーは生かす
            logger.exception("ジョブ %s が失敗した", ctx.job_id)
            failure = exc
        finally:
            self._current = None
            if conn.in_transaction:
                logger.error("ジョブ %s がトランザクションを開いたまま終わった", ctx.job_id)
                conn.execute("ROLLBACK")
            conn.close()
        # **決着そのものでもワーカーを落とさない。** `finish` は rowcount≠1 で
        # `LeaseLost` を、`emit` は `BEGIN IMMEDIATE` の待ちきれで送出しうる。
        # ここから上がると `run_forever` を抜け、`api/app.py` の裸の
        # `create_task` の中で黙って死ぬ —— HTTP は生きたままジョブだけが
        # 二度と走らなくなる。
        try:
            if failure is not None:
                self._fail(poll_store, ctx, str(failure))
                return
            # 状態の読み出しと決着を分けると、その間の cancel が上書きされる。
            poll_store.finish_claimed(ctx.job_id, ctx.lease_token)
        except Exception:  # noqa: BLE001 - 決着が書けなくてもワーカーは生かす
            logger.exception("ジョブ %s の決着を書けなかった", ctx.job_id)

    def _fail(self, store: JobStore, ctx: JobContext, reason: str) -> None:
        """失敗の決着を付け、**終わったことを合図として残す**.

        `job_event` は進捗の配信（SSE）の唯一の出所なので、ここで書かないと
        画面はカードの写しを取り直さず、サーバでは既にカードを離しているのに
        「作業中です。終わるまで抜かないでください。」を出したまま止まる
        （§13「作業が終われば、押さなくても表示が切り替わる」）。

        **決着を先に書く。** 合図が先だと、それを受けて取り直した画面が、
        まだ `running` の一覧を読む。`emit` はリースを見ないので、`finish` が
        リースを外した後でも書ける。

        理由は `job.error` と同じ文字列。**画面は `job.error` を出さない**ので、
        ここに書かなければ失敗の理由がどこにも現れない。例外の文字列に秘密は
        含めない（`adapters/immich.py` は識別子に API キーが混ざる応答を
        `ImmichProtocolError` で弾き、値そのものは載せない）。
        """
        store.finish(ctx.job_id, ctx.lease_token, "failed", reason)
        store.emit(ctx.job_id, "error", f"作業が失敗した: {reason}")
