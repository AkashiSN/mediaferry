"""進捗の配信を**本物のプロセス**で確かめる（SSE、§11）.

`TestClient` は終わらない応答を最後まで受け取ろうとして進まないので、線の上の
挙動（実際に流れるか、`id:` が付くか、再接続で続くか）はここで見る。
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from mediaferry.db.connection import Database
from mediaferry.db.jobs import JobStore

from .harness import system_app

pytestmark = pytest.mark.needs_system


def _emit_soon(store, job_id, message, delay=0.5):
    def later() -> None:
        time.sleep(delay)
        store.emit(job_id, "info", message)

    thread = threading.Thread(target=later, daemon=True)
    thread.start()
    return thread


def _frames(response, count, timeout=10.0):
    """`id:` と `data:` の対を `count` 個集める."""
    out: list[tuple[str | None, dict]] = []
    pending_id: str | None = None
    deadline = time.monotonic() + timeout
    for line in response.iter_lines():
        if time.monotonic() > deadline:
            break
        if line.startswith("id:"):
            pending_id = line.removeprefix("id:").strip()
        elif line.startswith("data:"):
            out.append((pending_id, json.loads(line.removeprefix("data:").strip())))
            pending_id = None
            if len(out) == count:
                return out
    raise AssertionError(f"{count} 本届かなかった（{len(out)} 本）")


def test_progress_reaches_an_open_page(tmp_path):
    with system_app(tmp_path) as app:
        conn = Database(app.data_root / "var" / "mediaferry.sqlite3").connect()
        store = JobStore(conn)
        job_id = store.enqueue("scan", {})
        with (
            httpx.Client(base_url=app.url, timeout=15.0) as client,
            client.stream("GET", "/api/events") as response,
        ):
            assert response.status_code == 200
            _emit_soon(store, job_id, "動いている")
            [(event_id, event)] = _frames(response, 1)
        conn.close()

    assert event["message"] == "動いている"
    # **`id:` が付く。** ブラウザはこれを Last-Event-ID として送り返す。
    assert event_id is not None and event_id.isdigit()


def test_a_reconnecting_page_does_not_miss_what_happened_meanwhile(tmp_path):
    """**取りこぼさない。** 切れている間に起きたことは、次の接続で届く."""
    with system_app(tmp_path) as app:
        conn = Database(app.data_root / "var" / "mediaferry.sqlite3").connect()
        store = JobStore(conn)
        job_id = store.enqueue("scan", {})
        with httpx.Client(base_url=app.url, timeout=15.0) as client:
            with client.stream("GET", "/api/events") as response:
                _emit_soon(store, job_id, "1 本目")
                [(first_id, first)] = _frames(response, 1)
            # ここは「切れている」時間。
            store.emit(job_id, "info", "切れている間")
            store.emit(job_id, "info", "そのあと")
            with client.stream(
                "GET", "/api/events", headers={"Last-Event-ID": first_id}
            ) as response:
                events = [event for _, event in _frames(response, 2)]
        conn.close()

    assert first["message"] == "1 本目"
    assert [event["message"] for event in events] == ["切れている間", "そのあと"]
