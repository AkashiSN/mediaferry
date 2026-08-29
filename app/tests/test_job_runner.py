import asyncio
import sqlite3
import threading
from datetime import timedelta

import anyio
import anyio.to_thread
import pytest

from mediaferry.db.jobs import JobStore, LeaseLost
from mediaferry.jobs.runner import JobRunner


@pytest.mark.anyio
async def test_the_runner_executes_a_handler(db, database):
    store = JobStore(db)
    seen = []

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", lambda ctx, conn: seen.append(ctx.params["v"]))
    job_id = store.enqueue("scan", {"v": 7})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(job_id)["status"] == "queued":
                await anyio.sleep(0.01)
            while store.get(job_id)["status"] == "running":
                await anyio.sleep(0.01)
        await runner.stop()

    assert seen == [7]
    assert store.get(job_id)["status"] == "succeeded"


@pytest.mark.anyio
async def test_a_failing_handler_records_the_error_and_keeps_the_worker_alive(db, database):
    store = JobStore(db)

    def boom(ctx, conn):
        raise RuntimeError("ffprobe が見つからない")

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", boom)
    failing = store.enqueue("scan", {})
    later = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(later)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(failing)["status"] == "failed"
    assert "ffprobe" in store.get(failing)["error"]


@pytest.mark.anyio
async def test_a_cancelled_job_ends_as_cancelled(db, database):
    store = JobStore(db)
    started = anyio.Event()

    def slow(ctx, conn):
        started.set()
        while not ctx.cancelled():
            pass

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("import", slow)
    job_id = store.enqueue("import", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            await started.wait()
        store.request_cancel(job_id)
        with anyio.fail_after(5):
            while store.get(job_id)["status"] in {"running", "cancelling"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(job_id)["status"] == "cancelled"


@pytest.mark.anyio
async def test_each_job_gets_its_own_connection(db, database):
    """ハンドラの接続がワーカーの poller と同じだと、claim と publish の
    トランザクションが混ざる."""
    store = JobStore(db)
    seen = []

    def capture(ctx, conn):
        seen.append(conn)

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", capture)
    first = store.enqueue("scan", {})
    second = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(second)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert store.get(first)["status"] == "succeeded"


@pytest.mark.anyio
async def test_a_job_claimed_while_stopping_is_cancelled(db, database, monkeypatch):
    """claim を待っている間に停止要求が来ると、_current がまだ None なので
    stop() は cancel を打てない. 掴んだ後に確認しないと、停止が長いジョブの
    完走待ちになる.

    停止済みの状態で run_forever を呼んでも loop に入らないので、本当に
    「claim 済み・戻る直前」で止めて競合を作る。
    """
    import threading

    store = JobStore(db)
    job_id = store.enqueue("import", {})
    observed = []

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("import", lambda ctx, conn: observed.append(ctx.cancelled()))

    claimed = threading.Event()
    release = threading.Event()
    original = JobStore.claim_next

    def claim_then_wait(self):
        ctx = original(self)
        if ctx is not None:
            claimed.set()
            release.wait(timeout=5)
        return ctx

    monkeypatch.setattr(JobStore, "claim_next", claim_then_wait)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            assert await anyio.to_thread.run_sync(lambda: claimed.wait(5))
        await runner.stop()  # _current はまだ None
        release.set()
        with anyio.fail_after(5):
            while store.get(job_id)["status"] in {"queued", "running", "cancelling"}:
                await anyio.sleep(0.01)

    assert observed == [True]
    assert store.get(job_id)["status"] == "cancelled"


@pytest.mark.anyio
async def test_stop_waits_for_the_running_handler(db, database):
    """to_thread のハンドラは cancel では止まらない. 待たずに資源を閉じない."""
    store = JobStore(db)
    started = anyio.Event()
    finished = []

    def slow(ctx, conn):
        started.set()
        while not ctx.cancelled():
            pass
        finished.append(ctx.job_id)

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("import", slow)
    job_id = store.enqueue("import", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            await started.wait()
        store.request_cancel(job_id)
        await runner.stop()

    assert finished == [job_id]


@pytest.mark.anyio
async def test_stop_does_not_write_through_the_workers_connection(db, database, monkeypatch):
    """停止の cancel は、`run_forever` が閉じる接続の上に書いてはいけない.

    `stop()` は cancel を `to_thread` へ逃がすので、待っている間にイベント
    ループは `run_forever` を先へ進める。ジョブが同じ瞬間に終われば
    `run_forever` は降りて poller を閉じ、閉じ終わった接続の上に cancel の
    続きが落ちる —— 文の実行中に閉じられればプロセスごと落ちる（SIGSEGV）。
    ここでは「閉じ終わってから続きが走る」順に固定して、同じ共有を捕まえる。
    """
    store = JobStore(db)
    started = threading.Event()
    release = threading.Event()
    worker_done = threading.Event()
    real_request_cancel = JobStore.request_cancel

    def slow(ctx, conn):
        started.set()
        release.wait(5)

    def cancel_after_the_worker_is_gone(self, job_id):
        # ハンドラを降ろし、`run_forever` が poller を閉じ切るまで待ってから
        # 本物の cancel を走らせる。
        release.set()
        worker_done.wait(5)
        return real_request_cancel(self, job_id)

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("import", slow)
    store.enqueue("import", {})

    worker = asyncio.create_task(runner.run_forever())
    worker.add_done_callback(lambda _: worker_done.set())
    with anyio.fail_after(5):
        while not started.is_set():
            await anyio.sleep(0.01)

    monkeypatch.setattr(JobStore, "request_cancel", cancel_after_the_worker_is_gone)
    with anyio.fail_after(5):
        await runner.stop()
        await worker


@pytest.mark.anyio
async def test_a_failed_job_announces_that_it_finished(db, database, monkeypatch):
    """失敗にも決着の合図が要る.

    `job_event` は SSE の唯一の出所（`api/routes_events.py`）。失敗の経路で
    書かないと、画面はカードの写しを取り直さず、サーバでは既に離しているのに
    「作業中です。終わるまで抜かないでください。」を出したまま止まる。

    **順序も見る。** 外から `job_event` を眺めても、合図と決着のどちらが先かは
    競争になって読めない（10ms の隙間に入る保証が無い）ので、合図を出す瞬間の
    状態を、書いている接続そのものから控える。
    """
    store = JobStore(db)
    announced = []
    emit = JobStore.emit

    def spy(self, job_id, level, message, data=None):
        # **合図が先に出ると、それを受けて取り直した画面はまだ `running` を読む。**
        announced.append(self.get(job_id)["status"])
        emit(self, job_id, level, message, data)

    monkeypatch.setattr(JobStore, "emit", spy)

    def boom(ctx, conn):
        raise RuntimeError("ffprobe が見つからない")

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", boom)
    job_id = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while not announced:
                await anyio.sleep(0.01)
        await runner.stop()

    assert announced == ["failed"], "決着より先に合図を出している"
    events = store.events(job_id)
    assert [event["level"] for event in events] == ["error"]
    # 画面は `job.error` を出さないので、理由はここに書かないとどこにも現れない。
    assert "ffprobe" in events[-1]["message"]


@pytest.mark.anyio
async def test_an_unknown_job_type_also_announces_that_it_finished(db, database):
    """ハンドラの無い種別も同じ決着を通る（合図の無い終わり方を残さない）."""
    store = JobStore(db)
    runner = JobRunner(database, poll_interval=0.01)
    job_id = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(job_id)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(job_id)["status"] == "failed"
    assert [event["level"] for event in store.events(job_id)] == ["error"]


@pytest.mark.anyio
async def test_a_handler_that_dies_holding_a_transaction_still_gets_a_verdict(db, database):
    """決着は**ハンドラの接続を閉じてから**付ける.

    開いたままのトランザクションの中で書くと、取りこぼしを拾う `ROLLBACK` が
    決着ごと巻き戻し、失敗したジョブが `running` のまま残る（合図も書けない
    —— `emit` は `BEGIN IMMEDIATE` を要る）。
    """
    store = JobStore(db)

    def boom(ctx, conn):
        conn.execute("BEGIN IMMEDIATE")
        raise RuntimeError("開いたまま落ちた")

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", boom)
    job_id = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(job_id)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(job_id)["status"] == "failed"
    assert [event["level"] for event in store.events(job_id)] == ["error"]


@pytest.mark.anyio
async def test_a_success_verdict_that_cannot_be_written_does_not_kill_the_worker(
    db, database, monkeypatch
):
    """決着そのものが書けなくても、次のジョブは走る.

    `finish` は rowcount≠1 で `LeaseLost` を、`emit` は `BEGIN IMMEDIATE` の
    待ちきれで送出しうる。ここから上がると `run_forever` を抜け、`api/app.py` の
    裸の `create_task` の中で黙って死ぬ —— HTTP は生きたままジョブだけが二度と
    走らなくなる。
    """
    store = JobStore(db)
    settle = JobStore.finish_claimed
    attempts = []

    def break_the_first(self, job_id, token):
        attempts.append(job_id)
        if len(attempts) == 1:
            raise LeaseLost("決着を書けない")
        return settle(self, job_id, token)

    monkeypatch.setattr(JobStore, "finish_claimed", break_the_first)

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", lambda ctx, conn: None)
    store.enqueue("scan", {})
    later = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(later)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(later)["status"] == "succeeded", "決着が書けないとワーカーが死ぬ"


@pytest.mark.anyio
async def test_a_failure_verdict_that_cannot_be_written_does_not_kill_the_worker(
    db, database, monkeypatch
):
    """**失敗の決着こそ危ない。**

    `finish` の `LeaseLost` と `emit` の待ちきれが両方乗る唯一の経路で、しかも
    ハンドラが既に落ちている場面で通る。ここから例外が上がると `run_forever` を
    抜け、`api/app.py` の裸の `create_task` の中で黙って死ぬ —— HTTP は生きた
    ままジョブだけが二度と走らなくなる。
    """
    store = JobStore(db)
    announce = JobStore.emit
    attempts = []

    def break_the_first(self, job_id, level, message, data=None):
        attempts.append(job_id)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return announce(self, job_id, level, message, data)

    monkeypatch.setattr(JobStore, "emit", break_the_first)

    def boom(ctx, conn):
        raise RuntimeError("ffprobe が見つからない")

    runner = JobRunner(database, poll_interval=0.01)
    runner.register("scan", boom)
    store.enqueue("scan", {})
    later = store.enqueue("scan", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(later)["status"] in {"queued", "running"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(later)["status"] == "failed", "失敗の決着が書けないとワーカーが死ぬ"


@pytest.mark.anyio
async def test_the_runner_reaps_a_job_whose_lease_expired(db, database):
    """**決着を書けなかった行を、再起動を待たずに倒す.**

    `_settle` は「決着が書けなくてもワーカーを生かす」ので、そこを踏んだ行は
    `running` のまま残る。倒す経路が起動時の `sweep_interrupted` だけだと、
    その行は再起動するまで残り、リセットが `job_in_flight` で断られ続ける
    （案内は「終わってから」だが、終わる主体がもう居ない）。
    """
    stale = JobStore(db, lease_seconds=-1)  # 即座に失効する
    stale.enqueue("import", {})
    ctx = stale.claim_next()
    assert stale.get(ctx.job_id)["status"] == "running"

    store = JobStore(db)
    runner = JobRunner(database, poll_interval=0.01, reap_interval=0.0)
    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(5):
            while store.get(ctx.job_id)["status"] == "running":
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(ctx.job_id)["status"] == "interrupted"


@pytest.mark.anyio
async def test_the_runner_does_not_reap_a_job_whose_lease_is_alive(db, database):
    """**期限を見ずに倒さない.** 見ないと、走っている取り込みを毎周期で殺す."""
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()

    runner = JobRunner(database, poll_interval=0.01, reap_interval=0.0)
    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        await anyio.sleep(0.1)  # 回収の周回を何度も通す
        await runner.stop()

    assert store.get(ctx.job_id)["status"] == "running"
    ctx.assert_lease()  # リースも無傷


def a_running_job_with_an_expired_lease(db):
    """`_settle` が決着を書けなかった行. **`running` のまま、リースだけ切れている.**

    `enqueue` してから `claim_next` すると、走っているワーカーが先に掴みうる。
    1 文で入れて競合を無くす。
    """
    from mediaferry.clock import iso, utcnow
    from mediaferry.ids import new_id

    job_id = new_id()
    db.execute(
        "INSERT INTO job (id, type, status, params_json, lease_token, lease_expires_at,"
        " created_at) VALUES (?, 'import', 'running', '{}', 'stale', ?, ?)",
        (job_id, iso(utcnow() - timedelta(seconds=1)), iso(utcnow())),
    )
    return job_id


@pytest.mark.anyio
async def test_the_runner_does_not_reap_on_every_poll(db, database):
    """**回収は間引く.**

    poll は既定 0.5 秒ごとなので、毎周回 `reap_expired_leases` を呼ぶと、
    倒す行が 1 つも無くても UPDATE が書き込みロックを取り続ける。窓の中に
    現れた行は、次の窓まで回収されない。
    """
    store = JobStore(db)
    runner = JobRunner(database, poll_interval=0.01, reap_interval=30.0)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        await anyio.sleep(0.05)  # 最初の回収を通す
        job_id = a_running_job_with_an_expired_lease(db)
        await anyio.sleep(0.15)  # 何周回も回す
        await runner.stop()

    assert store.get(job_id)["status"] == "running", "窓の中では回収しない"
