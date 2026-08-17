import anyio
import anyio.to_thread
import pytest

from mediaferry.db.jobs import JobStore
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
