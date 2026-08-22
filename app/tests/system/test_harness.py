"""土台そのものの smoke（E2E を書く前に、立ち上がることを確かめる）."""

from __future__ import annotations

import httpx
import pytest

from .harness import system_app

pytestmark = pytest.mark.needs_system


def test_the_real_process_serves_health(tmp_path):
    with system_app(tmp_path) as app:
        response = httpx.get(f"{app.url}/api/health", timeout=10.0)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_two_fake_immich_servers_are_reachable(tmp_path):
    """**宛先を 2 つ使う受け入れ**（§20）のために 2 台立てる."""
    with system_app(tmp_path) as app:
        assert len(set(app.immich_urls)) == 2
        for url in app.immich_urls:
            body = httpx.get(f"{url}/api/users/me", headers={"x-api-key": "test-api-key"}).json()
            assert body["id"]


def test_the_guard_is_on_in_the_real_process(tmp_path):
    """**本番と同じ経路**で入口の防御が効いていることを確かめる（§14）."""
    with system_app(tmp_path) as app, app.client() as client:
        # 信頼しないホスト名では名乗らない。
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 421
        # 状態を変える要求には二重送信 Cookie の対が要る。
        assert client.post("/api/merge-groups/detect", json={}).status_code == 403


def test_the_app_keeps_answering_after_filling_the_pipe(tmp_path):
    """**出力を汲み出さないとアプリが止まる。**

    アプリの stdout はパイプで、既定のバッファは 64 KiB。誰も読まないまま埋まると、
    書き手は `pipe_write` で永久にブロックし、**listen はしているのに応答しない**
    状態になる。要求 1 件につきアクセスログが 1 行（60 バイト前後）出るので、
    2000 件でバッファの倍以上を吐かせ、そのあとも答えることを確かめる。

    止まっているときは要求が返らないので、待ちを短く切って失敗させる。
    """
    with system_app(tmp_path) as app:
        with httpx.Client(base_url=app.url, timeout=10.0) as client:
            for _ in range(2000):
                assert client.get("/api/health").status_code == 200
        # 溜めた出力は、失敗の報告に使えるよう残っている。
        assert "/api/health" in app.output()


def test_a_failed_start_is_reported_with_the_output(tmp_path, monkeypatch):
    """**落ちたときは出力を添える。** 汲み出しに回しても、この性質は残る."""
    monkeypatch.setenv("MEDIAFERRY_LOG_LEVEL", "そんな段階はない")
    with pytest.raises(RuntimeError) as caught, system_app(tmp_path):
        pass
    assert "起動に失敗した" in str(caught.value)
    assert "Traceback" in str(caught.value)
    assert "そんな段階はない" in str(caught.value)


def test_authentication_can_be_turned_on(tmp_path):
    with system_app(tmp_path, password="correct horse") as app, app.client() as client:
        assert client.get("/api/jobs").status_code == 401
        token = client.get("/api/auth/session").cookies["XSRF-TOKEN"]
        login = client.post(
            "/api/auth/login",
            json={"password": "correct horse"},
            headers={"X-CSRF-Token": token},
        )
        assert login.status_code == 200
        assert client.get("/api/jobs").status_code == 200
