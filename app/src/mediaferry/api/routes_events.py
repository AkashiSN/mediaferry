"""進捗の配信（SSE、§11）.

**再開の cursor は `job_event.id`。** ジョブ内の連番（`seq`）はジョブをまたぐ位置に
ならないので、全体で単調増加する主キーを使う。ブラウザは再接続で `Last-Event-ID` を
自動で送るので、`id:` 行にその値を載せる。

**初回は履歴を流さない。** 長く運用した後に新しいタブを開くだけで全 `job_event` が
流れると、画面も回線も詰まる。「開いてから起きたこと」だけを流す。

**接続ごとに DB 接続を 1 本開く**（§3）。タブを開きっぱなしにするだけで増えるので、
同時接続に上限を置く。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..db.connection import Database
from .deps import state as get_state
from .errors import ErrorCode, error_response

router = APIRouter()

# 新しい `job_event` を見に行く間隔。SQLite に通知は無いので polling で読む。
# 取りこぼさないことが要件で、遅延はこの程度で足りる（§13 の進捗表示）。
POLL_SECONDS = 0.5
# 無通信で切られないよう、コメント行を流す間隔。
KEEP_ALIVE_SECONDS = 15.0
# 同時接続の上限。1 本につき DB 接続を 1 本使う。
MAX_CONNECTIONS = 8


@router.get("/events")
async def events(request: Request, app_state=Depends(get_state)):  # noqa: ANN001, ANN201, B008
    """`job_event` を流し続ける."""
    if app_state.event_streams >= MAX_CONNECTIONS:
        return error_response(
            503, ErrorCode.TOO_MANY_STREAMS, "同時に開ける進捗の数を超えている", {}
        )
    app_state.event_streams += 1
    reservation = _Reservation(app_state, app_state.database.connect())
    return StreamingResponse(
        _Body(_stream(request, reservation.connection), reservation),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class _Reservation:
    """開いた 1 本ぶんの資源（DB 接続と数え）を、**一度だけ**返す.

    返す場所が 2 つある —— 流し終えた（または切られた）ときと、**一度も始まらない
    まま閉じられた**とき。後者を落とすと、数えだけが残って上限に当たったまま
    戻らなくなる。
    """

    def __init__(self, app_state: Any, connection: sqlite3.Connection) -> None:
        self._app_state = app_state
        self.connection = connection
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.connection.close()
        self._app_state.event_streams -= 1


class _Body:
    """`StreamingResponse` へ渡す本体. 終わり方に関わらず資源を返す."""

    def __init__(self, stream: AsyncIterator[str], reservation: _Reservation) -> None:
        self._stream = stream
        self._reservation = reservation

    def __aiter__(self) -> _Body:
        return self

    async def __anext__(self) -> str:
        try:
            return await self._stream.__anext__()
        except StopAsyncIteration:
            self._reservation.release()
            raise

    async def aclose(self) -> None:
        await self._stream.aclose()
        self._reservation.release()


async def _stream(request: Request, connection: sqlite3.Connection) -> AsyncIterator[str]:
    try:
        cursor, reset = _starting_point(connection, _requested_cursor(request))
        if reset is not None:
            yield _frame(None, "cursor_reset", {"reason": reset})
        quiet = 0.0
        # **切断は取り消しで受け取る。** `is_disconnected()` を待つと、受信側に
        # 何も来ない経路（テストの client など）でそこから進まなくなる。
        # 相手が切れれば Starlette がこのタスクを取り消し、`finally` が走る。
        while True:
            rows = _after(connection, cursor)
            for row in rows:
                cursor = row["id"]
                yield _frame(row["id"], "job", _event(row))
            if rows:
                quiet = 0.0
            else:
                quiet += POLL_SECONDS
                if quiet >= KEEP_ALIVE_SECONDS:
                    quiet = 0.0
                    yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_SECONDS)
    finally:
        # 資源は `_Reservation` が返す（ここでは何も持たない）。
        pass


def _requested_cursor(request: Request) -> int | None:
    """query を優先し、無ければブラウザが再接続で送るヘッダを見る."""
    for raw in (request.query_params.get("after_event_id"), request.headers.get("last-event-id")):
        if raw is not None and raw.isdigit():
            return int(raw)
    return None


def _starting_point(
    connection: sqlite3.Connection, requested: int | None
) -> tuple[int, str | None]:
    """流し始める位置と、位置を作り直した理由を返す.

    **範囲の外を黙って無視しない。** DB を作り直した後の古いタブは、黙っていると
    「何も起きていない」ように見える。理由を 1 本流してから今の位置で再開する。
    """
    bounds = connection.execute(
        "SELECT COALESCE(MIN(id), 0) AS low, COALESCE(MAX(id), 0) AS high FROM job_event"
    ).fetchone()
    if requested is None:
        return bounds["high"], None
    if requested > bounds["high"]:
        return bounds["high"], "cursor_out_of_range"
    if requested < bounds["low"] - 1:
        # 掃除された後。残っている分から流し直す。
        return bounds["low"] - 1, "cursor_out_of_range"
    return requested, None


def _after(connection: sqlite3.Connection, cursor: int) -> list[sqlite3.Row]:
    return list(
        connection.execute("SELECT * FROM job_event WHERE id > ? ORDER BY id LIMIT 500", (cursor,))
    )


def _event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "seq": row["seq"],
        "level": row["level"],
        "message": row["message"],
        "data": json.loads(row["data_json"]) if row["data_json"] else None,
        "at": row["at"],
    }


def _frame(event_id: int | None, name: str, payload: dict[str, Any]) -> str:
    head = "" if event_id is None else f"id: {event_id}\n"
    body = json.dumps(payload, ensure_ascii=False)
    return f"{head}event: {name}\ndata: {body}\n\n"


def _database(path) -> Database:  # noqa: ANN001 - 型は Database だけ
    return Database(path)
