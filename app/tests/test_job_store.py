import json
import sqlite3
import threading

import pytest

from mediaferry.db.jobs import JobStore, LeaseLost


def test_enqueue_then_claim(db):
    store = JobStore(db)
    job_id = store.enqueue("scan", {"volume_instance_id": "v1"})
    ctx = store.claim_next()
    assert ctx is not None
    assert ctx.job_id == job_id
    assert ctx.params == {"volume_instance_id": "v1"}
    assert store.get(job_id)["status"] == "running"


def test_only_one_worker_wins_a_job(db, database):
    """SQLite に行ロックは無い。BEGIN IMMEDIATE の中の条件付き UPDATE で所有権を取る."""
    second = database.connect()
    JobStore(db).enqueue("scan", {})
    claims = [JobStore(db).claim_next(), JobStore(second).claim_next()]
    assert sum(1 for c in claims if c is not None) == 1
    second.close()


def test_concurrent_claimers_do_not_both_win(db, database):
    """逐次に呼ぶと 2 人目の SELECT が既に running を見るだけで、競合にならない.

    本当に確かめたいのは「同時に取りに来ても 1 人しか勝たない」なので、
    スレッドで同時に走らせる。
    """
    JobStore(db).enqueue("scan", {})
    connections = [database.connect() for _ in range(4)]
    start = threading.Barrier(len(connections))
    won: list[object] = []
    lock = threading.Lock()

    def claim(conn):
        start.wait(timeout=5)
        ctx = JobStore(conn).claim_next()
        if ctx is not None:
            with lock:
                won.append(ctx.job_id)

    threads = [threading.Thread(target=claim, args=(conn,)) for conn in connections]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    for conn in connections:
        conn.close()
    assert len(won) == 1


def test_claiming_sets_a_lease_and_heartbeat_extends_it(db):
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    before = store.get(ctx.job_id)["lease_expires_at"]
    ctx.heartbeat()
    assert store.get(ctx.job_id)["lease_expires_at"] >= before


def test_a_stale_token_cannot_touch_the_job(db):
    """キャンセルされた古いジョブが新しいジョブの状態を上書きしないため."""
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    with pytest.raises(LeaseLost):
        store.finish(ctx.job_id, "someone-elses-token", "succeeded")
    store.finish(ctx.job_id, ctx.lease_token, "succeeded")


def test_finishing_clears_the_lease(db):
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    store.finish(ctx.job_id, ctx.lease_token, "succeeded")
    row = store.get(ctx.job_id)
    assert row["status"] == "succeeded"
    assert row["lease_token"] is None and row["lease_expires_at"] is None
    assert row["finished_at"] is not None


def test_cancel_is_cooperative(db):
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert ctx.cancelled() is False
    assert store.request_cancel(ctx.job_id) is True
    assert ctx.cancelled() is True


def test_cancelling_a_queued_job_finishes_it_immediately(db):
    """queued を cancelling にすると、claim_next が拾わないので誰も終わらせられない."""
    store = JobStore(db)
    job_id = store.enqueue("import", {})
    assert store.request_cancel(job_id) is True
    row = store.get(job_id)
    assert row["status"] == "cancelled"
    assert row["finished_at"] is not None
    assert store.claim_next() is None


def test_assert_lease_refuses_a_cancelling_job(db):
    """「キャンセル済み」と表示した後に公開されることを防ぐ境界."""
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    ctx.assert_lease()
    store.request_cancel(ctx.job_id)
    with pytest.raises(LeaseLost):
        ctx.assert_lease()


def test_assert_lease_refuses_an_expired_lease(db):
    store = JobStore(db, lease_seconds=-1)
    store.enqueue("import", {})
    ctx = store.claim_next()
    with pytest.raises(LeaseLost):
        ctx.assert_lease()


def test_heartbeat_cannot_revive_an_expired_lease(db):
    """失効したジョブは reap されて interrupted になる. 復活させない."""
    store = JobStore(db, lease_seconds=-1)
    store.enqueue("import", {})
    ctx = store.claim_next()
    with pytest.raises(LeaseLost):
        ctx.heartbeat()


def test_a_cancel_cannot_slip_between_the_lease_check_and_the_transition(db, database):
    """BEGIN IMMEDIATE の中で確認してから同じ transaction で進める."""
    from mediaferry.db.connection import immediate

    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    other_conn = database.connect()
    other = JobStore(other_conn)
    try:
        with immediate(db):
            ctx.assert_lease()
            # 別接続の cancel は書き込みロックを取れないので待たされる
            with pytest.raises(sqlite3.OperationalError):
                other_conn.execute("PRAGMA busy_timeout = 0")
                other.request_cancel(ctx.job_id)
    finally:
        other_conn.close()


def test_cancelling_a_finished_job_does_nothing(db):
    store = JobStore(db)
    job_id = store.enqueue("import", {})
    ctx = store.claim_next()
    store.finish(job_id, ctx.lease_token, "succeeded")
    assert store.request_cancel(job_id) is False


def test_startup_marks_running_jobs_interrupted(db):
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert store.sweep_interrupted() == 1
    assert store.get(ctx.job_id)["status"] == "interrupted"
    assert store.get(ctx.job_id)["lease_token"] is None


def test_events_are_numbered_per_job_and_readable_from_a_cursor(db):
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    ctx.emit("info", "1 件目")
    ctx.emit("info", "2 件目", {"index": 2})
    assert [e["seq"] for e in store.events(ctx.job_id, after_seq=0)] == [1, 2]
    assert [e["message"] for e in store.events(ctx.job_id, after_seq=1)] == ["2 件目"]


def test_expired_leases_are_reaped(db):
    store = JobStore(db, lease_seconds=-1)  # 即座に失効する
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert store.reap_expired_leases() == 1
    assert store.get(ctx.job_id)["status"] == "interrupted"


def test_a_live_lease_is_not_reaped(db):
    """期限を見ずに reap すると、正常に走っているジョブを毎周期で殺す."""
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    assert store.reap_expired_leases() == 0
    assert store.get(ctx.job_id)["status"] == "running"
    ctx.assert_lease()  # リースも無傷


def test_progress_rides_along_with_the_heartbeat(db):
    """**進捗は心拍と同じ 1 回の UPDATE に乗せる。** 書き込みを増やさない.

    `job_event` には入れない —— あれは監査の記録で、心拍のたびに行を足すと
    際限なく増える。進捗は「いまの値」だけあればよいので上書きが正しい。
    """
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    ctx.heartbeat({"phase": "copy", "bytes_done": 5, "bytes_total": 10})
    assert json.loads(store.get(ctx.job_id)["progress_json"])["bytes_done"] == 5


def test_a_heartbeat_without_progress_keeps_the_last_value(db):
    """脈動は進捗を持たない場所からも打たれる. そこで消すと表示が点滅する."""
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    ctx.heartbeat({"phase": "copy", "bytes_done": 5})
    ctx.heartbeat()
    assert json.loads(store.get(ctx.job_id)["progress_json"])["bytes_done"] == 5


def test_finishing_clears_the_progress(db):
    """終わったジョブの「いま何をしているか」は無い."""
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    ctx.heartbeat({"phase": "copy", "bytes_done": 5})
    store.finish(ctx.job_id, ctx.lease_token, "succeeded")
    assert store.get(ctx.job_id)["progress_json"] is None
