"""入口の防御（§14）.

**認証の有無に関わらず掛ける。** 認証を切っていても、罠サイトを開いたブラウザから
`127.0.0.1` を叩ける（drive-by CSRF）。ホスト名を LAN の IP へ向け直す DNS rebinding も
同じ経路で、**Origin と Host の一致だけでは防げない**（どちらも攻撃者のホスト名になる）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mediaferry.api.app import create_app
from mediaferry.api.errors import ErrorCode

CSRF = "test-csrf-token"  # noqa: S105 - テスト用の見せかけの値


def _error(response):
    return response.json()["error"]


@pytest.fixture
def app(data_root, broker, monkeypatch):
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    return create_app(broker_factory=lambda: broker)


@pytest.fixture
def secured(data_root, broker, monkeypatch):
    """認証を有効にしたアプリ."""
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MEDIAFERRY_AUTH_PASSWORD", "correct horse")
    app = create_app(broker_factory=lambda: broker)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        yield client


# ---------------------------------------------------------------- Host
def test_an_unknown_host_name_is_refused(app):
    """**DNS rebinding は Host で止める。** Origin と Host の一致では止まらない.

    攻撃者のドメインを LAN の IP へ向け直すと、ブラウザが送る Origin も Host も
    攻撃者のホスト名になるので「一致するか」は通ってしまう。
    """
    with TestClient(app, base_url="http://evil.example") as client:
        response = client.get("/api/health")
    assert response.status_code == 421
    assert _error(response)["code"] == ErrorCode.UNTRUSTED_HOST


def test_an_address_typed_by_the_user_is_accepted(app):
    """IP を直に打つのは正当な使い方（rebinding はホスト名を要る）."""
    for base in ("http://127.0.0.1:8080", "http://192.168.0.10:8080", "http://localhost:8080"):
        with TestClient(app, base_url=base) as client:
            assert client.get("/api/health").status_code == 200


def test_a_configured_host_name_is_accepted(data_root, broker, monkeypatch):
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MEDIAFERRY_TRUSTED_HOSTS", "mediaferry.example, nas.example")
    app = create_app(broker_factory=lambda: broker)
    with TestClient(app, base_url="http://mediaferry.example") as client:
        assert client.get("/api/health").status_code == 200
    with TestClient(app, base_url="http://other.example") as client:
        assert client.get("/api/health").status_code == 421


# ---------------------------------------------------------------- Origin / CSRF
def test_a_cross_site_post_is_refused(client):
    response = client.post(
        "/api/merge-groups/detect",
        json={},
        headers={"Origin": "http://evil.example", "X-CSRF-Token": CSRF},
        cookies={"XSRF-TOKEN": CSRF},
    )
    assert response.status_code == 403
    assert _error(response)["code"] == ErrorCode.CROSS_SITE_REQUEST


def test_another_port_on_the_same_host_is_still_another_site(client):
    """**オリジンはスキームとポートまで含む。** ホスト名だけで見ない.

    同じ端末で走る別のアプリ（`http://127.0.0.1:9999`）は別のオリジンで、
    そこからの要求を通す理由は無い。
    """
    response = client.post(
        "/api/merge-groups/detect", json={}, headers={"Origin": "http://127.0.0.1:9999"}
    )
    assert response.status_code == 403
    assert _error(response)["code"] == ErrorCode.CROSS_SITE_REQUEST


def test_a_cross_site_get_is_allowed(client):
    """GET には掛けない（`curl` は Origin を送らないし、読み取りは変えない）."""
    response = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert response.status_code == 200


def test_a_post_without_a_csrf_token_is_refused(app):
    """共有の client は対を付けているので、**素のブラウザ**を作って確かめる."""
    with TestClient(app, base_url="http://127.0.0.1:8080") as bare:
        response = bare.post("/api/merge-groups/detect", json={})
    assert response.status_code == 403
    assert _error(response)["code"] == ErrorCode.CSRF_FAILED


def test_a_post_with_a_mismatched_csrf_token_is_refused(client):
    response = client.post(
        "/api/merge-groups/detect",
        json={},
        headers={"X-CSRF-Token": "not-the-cookie"},
        cookies={"XSRF-TOKEN": CSRF},
    )
    assert response.status_code == 403
    assert _error(response)["code"] == ErrorCode.CSRF_FAILED


def test_the_token_is_handed_out_and_kept_stable(app):
    """発行点を固定する（画面が最初に叩くところで必ず受け取れる）."""
    with TestClient(app, base_url="http://127.0.0.1:8080") as bare:
        first = bare.get("/api/auth/session")
        token = first.cookies.get("XSRF-TOKEN")
        assert token
        second = bare.get("/api/auth/session")
        # 既に有効な値があれば作り直さない（開いている別タブの値を無効にしない）。
        assert second.cookies.get("XSRF-TOKEN") in (None, token)
        assert bare.cookies.get("XSRF-TOKEN") == token


# ---------------------------------------------------------------- 認証
def test_without_a_password_everything_is_open(client):
    body = client.get("/api/auth/session").json()
    assert body == {"required": False, "authenticated": False}
    assert client.get("/api/jobs").status_code == 200


def test_with_a_password_reads_need_a_session(secured):
    assert secured.get("/api/auth/session").json()["required"] is True
    assert secured.get("/api/jobs").status_code == 401
    assert _error(secured.get("/api/jobs"))["code"] == ErrorCode.NOT_AUTHENTICATED


def test_logging_in_and_out(secured):
    token = secured.get("/api/auth/session").cookies["XSRF-TOKEN"]
    headers = {"X-CSRF-Token": token}

    wrong = secured.post("/api/auth/login", json={"password": "wrong"}, headers=headers)
    assert wrong.status_code == 401
    assert secured.get("/api/jobs").status_code == 401

    login = secured.post("/api/auth/login", json={"password": "correct horse"}, headers=headers)
    assert login.status_code == 200
    assert secured.get("/api/jobs").status_code == 200
    assert secured.get("/api/auth/session").json() == {"required": True, "authenticated": True}

    session_cookie = secured.cookies.get("mediaferry_session")
    assert secured.post("/api/auth/logout", headers=headers).status_code == 200
    assert secured.get("/api/jobs").status_code == 401
    # **サーバ側でも失効している。** Cookie を消すだけだと、盗まれた値がまだ通る。
    replayed = secured.get("/api/jobs", cookies={"mediaferry_session": session_cookie})
    assert replayed.status_code == 401


def test_the_login_endpoint_is_still_checked_for_its_origin(secured):
    """**login を丸ごと例外にしない。** 罠サイトからログインを試させない."""
    response = secured.post(
        "/api/auth/login",
        json={"password": "correct horse"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_repeated_failures_are_rate_limited(secured):
    token = secured.get("/api/auth/session").cookies["XSRF-TOKEN"]
    headers = {"X-CSRF-Token": token}
    codes = [
        secured.post("/api/auth/login", json={"password": "wrong"}, headers=headers).status_code
        for _ in range(11)
    ]
    assert codes[-1] == 429
    assert codes.count(401) >= 5


def test_the_password_never_appears_in_a_response(secured, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        token = secured.get("/api/auth/session").cookies["XSRF-TOKEN"]
        responses = [
            secured.post(
                "/api/auth/login",
                json={"password": password},
                headers={"X-CSRF-Token": token},
            )
            for password in ("correct horse", "wrong")
        ]
    assert all("correct horse" not in response.text for response in responses)
    assert "correct horse" not in caplog.text


def test_the_session_cookie_is_not_readable_by_scripts(secured):
    token = secured.get("/api/auth/session").cookies["XSRF-TOKEN"]
    login = secured.post(
        "/api/auth/login", json={"password": "correct horse"}, headers={"X-CSRF-Token": token}
    )
    cookies = login.headers.get_list("set-cookie")
    session = [value for value in cookies if value.startswith("mediaferry_session=")]
    assert session and "httponly" in session[0].lower()
    assert "samesite=lax" in session[0].lower()
    # XSRF は JS が読むので HttpOnly を付けない。
    xsrf = [value for value in cookies if value.startswith("XSRF-TOKEN=")]
    assert all("httponly" not in value.lower() for value in xsrf)


# ---------------------------------------------------------------- 公開の警告
def test_an_unauthenticated_exposure_is_reported(data_root, broker, monkeypatch):
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MEDIAFERRY_BIND_HOST", "0.0.0.0")  # noqa: S104 - 公開の再現
    app = create_app(broker_factory=lambda: broker)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        warnings = client.get("/api/settings").json()["warnings"]
    assert [warning["code"] for warning in warnings] == ["unauthenticated_exposure"]


def test_the_session_cookie_outlives_the_browser_window(secured):
    """**閉じて開いてもログインしたまま。**

    DB のセッションは 14 日もつのに Cookie がブラウザセッションだと、窓を閉じた
    だけでログアウトする（保存している意味が無い）。
    """
    from mediaferry.db.sessions import SESSION_TTL_SECONDS

    token = secured.get("/api/auth/session").cookies["XSRF-TOKEN"]
    login = secured.post(
        "/api/auth/login", json={"password": "correct horse"}, headers={"X-CSRF-Token": token}
    )

    [session] = [
        value
        for value in login.headers.get_list("set-cookie")
        if value.startswith("mediaferry_session=")
    ]
    assert f"max-age={SESSION_TTL_SECONDS}" in session.lower()
