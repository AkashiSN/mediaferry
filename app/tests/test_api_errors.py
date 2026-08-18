"""API のエラー形式（§13 / §14）.

画面は `code` を見て日本語を決める。`detail` をそのまま出す作りにすると、内部の
文言や相手由来の値が利用者へ流れる。**すべての失敗を同じ封筒に入れる。**
"""

from __future__ import annotations

import pytest

from mediaferry.api.errors import ErrorCode


def _error(response):
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "detail", "meta"}, body
    return body["error"]


def test_a_missing_record_carries_a_code(client):
    response = client.get("/api/media/does-not-exist")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == ErrorCode.NOT_FOUND
    assert error["detail"]


def test_a_malformed_query_does_not_echo_what_was_sent(client):
    """**受け取った値を応答に反射させない。** 反射は XSS と情報漏れの経路."""
    response = client.get("/api/media?limit=not-a-number")
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == ErrorCode.VALIDATION_FAILED
    assert "not-a-number" not in str(error)
    assert error["meta"]["fields"] == ["limit"]


def test_an_unhandled_exception_does_not_leak_its_message(client, monkeypatch):
    """**例外の文字列を外へ出さない**（秘密も相手由来の値も混ざりうる）.

    `TestClient` は既定でサーバ側の例外を送出し直すので、**本番と同じ
    「500 を返す」経路**を見るために `raise_server_exceptions=False` で開き直す。
    """
    from fastapi.testclient import TestClient

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("secret-value-in-message")

    monkeypatch.setattr("mediaferry.db.jobs.JobStore.list_jobs", explode)

    with TestClient(client.app, raise_server_exceptions=False) as raw:
        response = raw.get("/api/jobs")

    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == ErrorCode.INTERNAL
    assert "secret-value-in-message" not in str(error)


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/api/media/nope", 404, ErrorCode.NOT_FOUND),
        ("GET", "/api/merge-groups/nope", 404, ErrorCode.NOT_FOUND),
        ("GET", "/api/jobs/nope", 404, ErrorCode.NOT_FOUND),
        ("POST", "/api/jobs/nope/cancel", 409, ErrorCode.JOB_ALREADY_FINISHED),
        # 鍵が無いと転送先は扱えない。**画面が「設定を直す」と案内できる code を返す。**
        ("POST", "/api/destinations/nope/verify", 400, ErrorCode.SECRET_KEY_MISSING),
    ],
)
def test_known_failures_carry_a_stable_code(client, method, path, status, code):
    response = client.request(method, path)
    assert response.status_code == status
    assert _error(response)["code"] == code


def test_an_unknown_path_is_also_enveloped(client):
    """**ルータが無い経路の 404 も同じ封筒に入れる。**

    ここは FastAPI が投げる `HTTPException` を通る唯一の経路で、`code` を
    状態から決める分岐がここでしか働かない。
    """
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert _error(response)["code"] == ErrorCode.NOT_FOUND


def test_a_server_side_http_error_is_internal_not_bad_request(client, monkeypatch):
    """5xx を `bad_request` として返さない（画面が「入力を直せ」と誤案内する）."""
    from fastapi import HTTPException

    def unavailable(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise HTTPException(status_code=503, detail="いま受けられない")

    monkeypatch.setattr("mediaferry.db.jobs.JobStore.list_jobs", unavailable)

    response = client.get("/api/jobs")

    assert response.status_code == 503
    assert _error(response)["code"] == ErrorCode.INTERNAL
