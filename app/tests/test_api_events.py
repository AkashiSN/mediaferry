"""進捗の配信（SSE、§11 / §13）—— 位置の決め方と枠組み.

**再開の cursor は `job_event.id`（全ジョブ横断の自動採番）。** `seq` はジョブ内の
連番なので、ジョブをまたぐ再開位置にならない。

**線の上の挙動（実際に流れるか、再接続で続くか）は実プロセスで見る**
（`app/tests/system/test_events.py`）。`TestClient` は終わらない応答を最後まで
受け取ろうとして進まないので、ここでは生成器と純粋な判断だけを直接呼ぶ。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from mediaferry.api import routes_events
from mediaferry.api.routes_events import _event, _frame, _starting_point, _stream
from mediaferry.db.connection import Database
from mediaferry.db.jobs import JobStore


class _Request:
    """`_stream` が使うのは query とヘッダだけ."""

    def __init__(self, query: dict[str, str] | None = None, headers: dict[str, str] | None = None):
        self.query_params = query or {}
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}


class _State:
    def __init__(self) -> None:
        self.event_streams = 1


@pytest.fixture
def store(db):
    return JobStore(db)


async def _collect(connection, request, count, timeout=5.0):
    """`data:` を `count` 本集める（届かなければ時間切れで落とす）."""
    frames: list[str] = []
    stream = _stream(request, connection)

    async def pump() -> None:
        async for frame in stream:
            frames.append(frame)
            if sum(1 for f in frames if f.startswith(("id:", "event:"))) >= count:
                return

    await asyncio.wait_for(pump(), timeout)
    await stream.aclose()
    return frames


def _payloads(frames):
    out = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line.removeprefix("data:").strip()))
    return out


# ---------------------------------------------------------------- 位置の決め方
def test_the_first_connection_starts_at_the_end(db, store):
    """**初回は履歴を流さない。** 長く運用した後の新しいタブで全件が流れない."""
    job_id = store.enqueue("scan", {})
    for index in range(3):
        store.emit(job_id, "info", f"古い {index}")
    high = db.execute("SELECT MAX(id) AS id FROM job_event").fetchone()["id"]

    assert _starting_point(db, None) == (high, None)


def test_a_cursor_inside_the_range_is_used_as_is(db, store):
    job_id = store.enqueue("scan", {})
    store.emit(job_id, "info", "1 本目")
    store.emit(job_id, "info", "2 本目")

    assert _starting_point(db, 1) == (1, None)


def test_a_cursor_from_the_future_restarts_and_says_so(db, store):
    """DB を作り直した後の古いタブ. **黙って何も流さないのが一番困る。**"""
    job_id = store.enqueue("scan", {})
    store.emit(job_id, "info", "1 本目")
    high = db.execute("SELECT MAX(id) AS id FROM job_event").fetchone()["id"]

    assert _starting_point(db, 999_999) == (high, "cursor_out_of_range")


def test_a_cursor_older_than_what_is_left_restarts_from_the_oldest(db, store):
    """掃除された後。**残っている分は取りこぼさない。**"""
    job_id = store.enqueue("scan", {})
    for index in range(3):
        store.emit(job_id, "info", f"残る {index}")
    db.execute("DELETE FROM job_event WHERE id < 3")
    low = db.execute("SELECT MIN(id) AS id FROM job_event").fetchone()["id"]

    assert _starting_point(db, 1) == (low - 1, "cursor_out_of_range")


def test_an_empty_database_starts_at_zero(db):
    assert _starting_point(db, None) == (0, None)


# ---------------------------------------------------------------- 枠組み
def test_a_frame_carries_the_id_so_the_browser_can_resume():
    frame = _frame(42, "job", {"message": "動いている"})
    assert frame.startswith("id: 42\nevent: job\ndata: ")
    assert frame.endswith("\n\n")


def test_a_frame_without_an_id_is_not_a_resume_point():
    """`cursor_reset` は `job_event` ではないので、位置として憶えさせない."""
    assert _frame(None, "cursor_reset", {"reason": "x"}).startswith("event: cursor_reset")


def test_an_event_carries_the_structured_data(db, store):
    job_id = store.enqueue("scan", {})
    store.emit(job_id, "warning", "気をつける", {"count": 3})
    row = db.execute("SELECT * FROM job_event ORDER BY id DESC LIMIT 1").fetchone()

    event = _event(row)

    assert event["job_id"] == job_id
    assert event["level"] == "warning"
    assert event["data"] == {"count": 3}


# ---------------------------------------------------------------- 流れるところ
@pytest.mark.anyio
async def test_events_after_the_cursor_are_streamed(db, data_root, store, monkeypatch):
    monkeypatch.setattr(routes_events, "POLL_SECONDS", 0.01)
    job_id = store.enqueue("scan", {})
    store.emit(job_id, "info", "1 本目")
    store.emit(job_id, "info", "2 本目")
    reader = Database(data_root / "var" / "mediaferry.sqlite3").connect()

    frames = await _collect(reader, _Request(query={"after_event_id": "1"}), count=1)

    assert [event["message"] for event in _payloads(frames)] == ["2 本目"]
    reader.close()


@pytest.mark.anyio
async def test_each_event_is_sent_once_and_in_order(db, data_root, store, monkeypatch):
    """**位置を進めながら流す。** 進めないと同じ行を流し続ける（画面が同じ行で埋まる）."""
    monkeypatch.setattr(routes_events, "POLL_SECONDS", 0.01)
    job_id = store.enqueue("scan", {})
    for message in ("1 本目", "2 本目", "3 本目"):
        store.emit(job_id, "info", message)
    reader = Database(data_root / "var" / "mediaferry.sqlite3").connect()

    async def emit_later() -> None:
        await asyncio.sleep(0.05)
        store.emit(job_id, "info", "4 本目")

    later = asyncio.create_task(emit_later())
    # **3 本目のあとに来るのは「4 本目」。** 位置を進めていなければ、次の poll で
    # 同じ行がもう一度流れて「2 本目」が来る。
    frames = await _collect(reader, _Request(query={"after_event_id": "1"}), count=3)
    await later

    assert [event["message"] for event in _payloads(frames)] == ["2 本目", "3 本目", "4 本目"]
    reader.close()


@pytest.mark.anyio
async def test_the_reconnect_header_is_used_when_there_is_no_query(
    db, data_root, store, monkeypatch
):
    """再接続でブラウザが送るのは `Last-Event-ID`."""
    monkeypatch.setattr(routes_events, "POLL_SECONDS", 0.01)
    job_id = store.enqueue("scan", {})
    store.emit(job_id, "info", "1 本目")
    store.emit(job_id, "info", "2 本目")
    reader = Database(data_root / "var" / "mediaferry.sqlite3").connect()

    frames = await _collect(reader, _Request(headers={"Last-Event-ID": "1"}), count=1)

    assert [event["message"] for event in _payloads(frames)] == ["2 本目"]
    reader.close()


@pytest.mark.anyio
async def test_a_quiet_stream_sends_a_keep_alive(db, data_root, monkeypatch):
    """途中のリバースプロキシが無通信で切る."""
    monkeypatch.setattr(routes_events, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(routes_events, "KEEP_ALIVE_SECONDS", 0.02)
    reader = Database(data_root / "var" / "mediaferry.sqlite3").connect()

    stream = _stream(_Request(), reader)
    frame = await asyncio.wait_for(anext(stream), 5.0)
    await stream.aclose()

    assert frame.startswith(":")
    reader.close()


@pytest.mark.anyio
async def test_the_resources_are_returned_however_the_stream_ends(db, data_root, monkeypatch):
    """**開きっぱなしで DB 接続が増え続けない。**

    返す場所は 2 つある —— 流し終えた（切られた）ときと、**一度も始まらないまま
    閉じられた**とき。どちらでも 1 度だけ返す。
    """
    monkeypatch.setattr(routes_events, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(routes_events, "KEEP_ALIVE_SECONDS", 0.02)
    reader = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    state = _State()
    reservation = routes_events._Reservation(state, reader)  # noqa: SLF001

    body = routes_events._Body(_stream(_Request(), reader), reservation)  # noqa: SLF001
    await asyncio.wait_for(anext(body), 5.0)  # keep-alive まで進める
    await body.aclose()

    assert state.event_streams == 0
    with pytest.raises(Exception, match="closed"):
        reader.execute("SELECT 1")


@pytest.mark.anyio
async def test_the_reservation_is_returned_even_if_nothing_was_read(db, data_root):
    """**始まらないまま閉じられても返す。** 落とすと上限に当たったまま戻らない."""
    reader = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    state = _State()
    reservation = routes_events._Reservation(state, reader)  # noqa: SLF001

    body = routes_events._Body(_stream(_Request(), reader), reservation)  # noqa: SLF001
    await body.aclose()

    assert state.event_streams == 0


# ---------------------------------------------------------------- 入口
@pytest.mark.anyio
async def test_too_many_streams_are_refused(data_root, monkeypatch):
    """**上限は入口で見る。** `TestClient` では終わらない応答を待ってしまうので、
    ルータの関数を直接呼んで、返るものの型と状態だけを見る."""
    from starlette.responses import StreamingResponse

    from mediaferry.db.connection import Database

    class _AppState:
        def __init__(self, streams: int) -> None:
            self.event_streams = streams
            self.database = Database(data_root / "var" / "mediaferry.sqlite3")

    monkeypatch.setattr(routes_events, "MAX_CONNECTIONS", 1)

    full = await routes_events.events(_Request(), _AppState(1))
    assert full.status_code == 503
    assert json.loads(bytes(full.body))["error"]["code"] == "too_many_streams"

    state = _AppState(0)
    allowed = await routes_events.events(_Request(), state)
    assert isinstance(allowed, StreamingResponse)
    # 開いた分は数える（返し忘れると、上限に当たったまま戻らなくなる）。
    assert state.event_streams == 1
    await allowed.body_iterator.aclose()
    assert state.event_streams == 0


def test_events_need_a_session_when_authentication_is_on(secured_app):
    """`EventSource` はヘッダを付けられないが Cookie は送る."""
    client, _ = secured_app
    assert client.get("/api/events").status_code == 401


@pytest.mark.anyio
async def test_the_reservation_is_returned_when_the_reader_is_cancelled(db, data_root, monkeypatch):
    """**切断は取り消しとして来る。**

    Starlette は本体の `aclose()` を呼ばずにタスクを取り消す。取り消しの経路で
    返さないと、切断のたびに DB 接続と数えが残り、上限に当たったまま戻らなくなる
    （8 回切れば `/events` が恒久的に 503）。
    """
    monkeypatch.setattr(routes_events, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(routes_events, "KEEP_ALIVE_SECONDS", 100.0)
    reader = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    state = _State()
    reservation = routes_events._Reservation(state, reader)  # noqa: SLF001
    body = routes_events._Body(_stream(_Request(), reader), reservation)  # noqa: SLF001

    task = asyncio.create_task(anext(body))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.event_streams == 0
    with pytest.raises(Exception, match="closed"):
        reader.execute("SELECT 1")
