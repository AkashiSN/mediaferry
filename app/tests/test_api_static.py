"""ビルド済みフロントの配信（§16 / §14）.

**同一オリジンで配る。** 別ポートにすると CORS と Cookie の設定が増え、CSRF の
前提（同一オリジン）も崩れる。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mediaferry.api.app import create_app
from mediaferry.api.static import CSP


@pytest.fixture
def built(data_root, broker, monkeypatch, tmp_path):
    """ビルド済み資産があるときのアプリ."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>mediaferry</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MEDIAFERRY_WEB_ROOT", str(dist))
    app = create_app(broker_factory=lambda: broker)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        yield client, dist


def test_the_page_is_served_at_the_root(built):
    client, _ = built
    response = client.get("/")
    assert response.status_code == 200
    assert "mediaferry" in response.text


def test_the_assets_are_served(built):
    client, _ = built
    assert client.get("/assets/app.js").status_code == 200


def test_an_unknown_api_path_is_not_the_page(built):
    """**何でも `index.html` を返さない。** 消したはずの API が 200 になる."""
    client, _ = built
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_an_unknown_page_path_is_the_app(built):
    """SPA の経路（画面の URL を直に開く・再読み込み）."""
    client, _ = built
    response = client.get("/library/some-id")
    assert response.status_code == 200
    assert "mediaferry" in response.text


def test_the_page_declares_a_strict_policy(built):
    """外部からスクリプトも書体も読まない（§14 の攻撃面を増やさない）."""
    client, _ = built
    response = client.get("/")
    assert response.headers["content-security-policy"] == CSP
    assert "default-src 'self'" in CSP
    assert "frame-ancestors 'none'" in CSP


def test_the_page_hands_out_a_csrf_token(built):
    """画面が最初に受け取る場所（Task 3 の発行点）."""
    client, _ = built
    assert client.get("/").cookies.get("XSRF-TOKEN")


def test_nothing_outside_the_build_is_ever_returned(built):
    """**`dist` の外は返さない。**

    画面の経路は常に `index.html` を返すだけなので、任意のパスを開く経路が無い。
    資産（`/assets`）は `..` を含む要求を弾く。
    """
    client, dist = built
    (dist.parent / "secret.txt").write_text("秘密", encoding="utf-8")

    for path in ("/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt"):
        response = client.get(path)
        assert "秘密" not in response.text, path
    # 符号化して正規化を避けた形（クライアントが畳まないので、サーバ側が弾く）。
    assert client.get("/assets/%2e%2e/%2e%2e/secret.txt").status_code == 404


def test_without_a_build_the_api_still_works(client):
    """資産が無い環境（開発・テスト）でも API は動く."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 404


def test_an_empty_web_root_is_not_mounted(data_root, broker, monkeypatch, tmp_path):
    """**中身の無いディレクトリを「画面がある」と扱わない。**

    `index.html` が無いまま mount すると、起動そのものが落ちるか、画面の要求が
    500 になる（配るものが無いのに配ろうとする）。
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MEDIAFERRY_WEB_ROOT", str(empty))

    app = create_app(broker_factory=lambda: broker)

    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 404
