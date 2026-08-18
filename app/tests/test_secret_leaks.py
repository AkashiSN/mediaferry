"""侵害された転送先が**こちらの読む値に鍵を混ぜてきた**ときの漏れを見る（§12.3 / §14）.

脅威は「本文をまるごとログに出す」だけではない。相手は `users/me` の `id`、
`POST /api/assets` の `status`、`bulk-upload-check` の `action` / `assetId` に
受け取った `x-api-key` をそのまま入れて返せる。それを DB の列・API 応答・
例外の文言へ通すと、`SecretBox` で暗号化して保存している意味が消える。
"""

from __future__ import annotations

import base64
import logging
import os

import pytest

from mediaferry.adapters.immich import ImmichClient, ImmichProtocolError
from mediaferry.db.connection import Database

from .fake_immich import API_KEY


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("MEDIAFERRY_SECRET_KEY", base64.b64encode(os.urandom(32)).decode())


@pytest.fixture
def api_db(client, data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


def test_an_echoed_key_never_reaches_the_response_or_the_database(
    secret_env, immich, client, api_db
):
    """`users/me` が鍵を id として返しても、平文はどこにも残らない."""
    immich.echo_key_in_scalars = True

    body = client.post(
        "/api/destinations", json={"name": "home", "base_url": immich.url, "api_key": API_KEY}
    ).json()

    assert API_KEY not in str(body)
    listed = client.get("/api/destinations").json()
    assert API_KEY not in str(listed)
    stored = api_db.execute("SELECT remote_user_id FROM destination_revision").fetchall()
    assert stored and all(API_KEY not in str(row["remote_user_id"]) for row in stored)


def test_the_verify_response_does_not_echo_the_key(secret_env, immich, client, api_db):
    destination_id = client.post(
        "/api/destinations", json={"name": "home", "base_url": immich.url, "api_key": API_KEY}
    ).json()["id"]
    immich.echo_key_in_scalars = True

    body = client.post(f"/api/destinations/{destination_id}/verify").json()

    assert API_KEY not in str(body)
    # 向き先が変わったことは分かる（指紋が一致しない）。
    assert body["matches"] is False


def test_a_protocol_error_carries_no_value_from_the_response(immich, caplog):
    """未知の `status` / `action` を**引用しない**.

    引用すると、例外文がそのままジョブの `last_error` とログへ入る。
    """
    immich.echo_key_in_scalars = True
    with (
        caplog.at_level(logging.DEBUG),
        ImmichClient(immich.url, API_KEY) as client,
        pytest.raises(ImmichProtocolError) as caught,
    ):
        client.bulk_upload_check([("record-1", "0" * 40)])
    assert API_KEY not in str(caught.value)
    assert API_KEY not in caplog.text
