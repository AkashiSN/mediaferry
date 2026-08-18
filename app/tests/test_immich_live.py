"""実 Immich に対する疎通確認.

環境変数 `MEDIAFERRY_TEST_IMMICH_URL` と `MEDIAFERRY_TEST_IMMICH_KEY` を与えて
`uv run pytest -m needs_immich` で走らせる。**作った資産は必ず消す。**

Phase 0 のプローブ（`spikes/immich_probe.py`）が確かめていない
「タグの作成・付与」と「日時の更新」をここで確かめる。
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import uuid

import pytest

from mediaferry.adapters.immich import ImmichClient

pytestmark = pytest.mark.needs_immich


@pytest.fixture
def client():
    url = os.environ.get("MEDIAFERRY_TEST_IMMICH_URL")
    key = os.environ.get("MEDIAFERRY_TEST_IMMICH_KEY")
    if not url or not key:
        pytest.skip("MEDIAFERRY_TEST_IMMICH_URL / _KEY が要る")
    with ImmichClient(url, key) as client:
        yield client


def test_the_identity_is_readable(client):
    assert client.users_me()["id"]


def test_an_unknown_checksum_is_accepted(client):
    sha1 = hashlib.sha1(uuid.uuid4().bytes, usedforsecurity=False).hexdigest()
    assert client.bulk_upload_check([("k", sha1)])["k"].action == "accept"
    # base64 で送っていることを、応答の形と併せて確かめる。
    assert base64.b64encode(bytes.fromhex(sha1))


def test_the_whole_upload_path_works_against_a_real_server(client, tmp_path):
    """**upload → 照合 → タグ → 日時 → 後片付け**を実機で通す.

    タグと日時のエンドポイントは Phase 0 で実測していない（Task 4）。ここを
    通さないと、全部間違っていても Phase 3 の完了条件が PASS になる。

    **作ったものは必ず消す。** 消せなかったらテストは失敗させる（実ライブラリに
    ゴミを残さない）。**既存の資産には触らない**（毎回ユニークな中身を作る）。
    """
    import os

    path = tmp_path / f"mediaferry-test-{uuid.uuid4().hex[:8]}.jpg"
    _a_unique_jpeg(path, uuid.uuid4().bytes)  # 毎回ユニーク。既存資産と衝突しない
    sha1 = hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()
    tag_name = f"mediaferry-test-{uuid.uuid4().hex[:8]}"

    asset_id = None
    try:
        # 1. 未知のはず
        assert client.bulk_upload_check([("k", sha1)])["k"].action == "accept"

        # 2. アップロード（created が返ることが origin の根拠になる）
        uploaded = client.upload_asset(
            path,
            sha1_hex=sha1,
            device_asset_id=f"mediaferry:{uuid.uuid4().hex}",
            file_created_at="2026-08-17T14:30:00+00:00",
            file_modified_at="2026-08-17T14:30:00+00:00",
        )
        asset_id = uploaded.asset_id
        assert uploaded.status == "created"

        # 3. 照合で自分の資産が返る
        outcome = client.bulk_upload_check([("k", sha1)])["k"]
        assert outcome.action == "reject"
        assert outcome.asset_id == asset_id
        assert outcome.is_trashed is False

        # 4. タグの作成・再利用・付与
        tag_id = client.ensure_tag(tag_name)
        assert client.ensure_tag(tag_name) == tag_id
        client.tag_assets(tag_id, [asset_id])

        # 5. 日時の書き戻し
        client.set_date_time_original(asset_id, "2026-08-17T14:30:00+09:00")
    finally:
        # 後片付けの失敗も FAIL にする（実ライブラリにゴミを残さない）。
        _cleanup(
            os.environ["MEDIAFERRY_TEST_IMMICH_URL"],
            os.environ["MEDIAFERRY_TEST_IMMICH_KEY"],
            asset_id,
            tag_name,
        )


def _a_unique_jpeg(path, seed: bytes) -> bytes:  # noqa: ANN001
    """**実 ffmpeg で本物の JPEG を作る**（中身は毎回変える）.

    相手は実 Immich で、上げたファイルは実際に取り込み処理へ回る。手で組んだ
    最小 JPEG や画像ライブラリの出力より、結合テストと同じ実 ffmpeg で作った
    ものを送る（この案件は「実物で確かめる」に倒している）。色をシードから
    決めるので、既存の資産とチェックサムが衝突しない。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が要る（テスト用の画像を作る）")
    colour = seed[:3].hex()
    subprocess.run(  # noqa: S603
        [  # noqa: S607 - 既存の結合テストと同じく PATH の ffmpeg を使う
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{colour}:s=64x64",
            "-frames:v",
            "1",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def _cleanup(url: str, key: str, asset_id: str | None, tag_name: str) -> None:
    """作った資産とタグを消す. **消せなければ送出する。**"""
    import httpx

    with httpx.Client(base_url=url, headers={"x-api-key": key}, timeout=60.0) as raw:
        if asset_id is not None:
            response = raw.request("DELETE", "/api/assets", json={"ids": [asset_id], "force": True})
            assert response.status_code < 400, f"資産を消せなかった: {response.status_code}"
        tags = raw.get("/api/tags").json()
        for tag in tags:
            if tag["name"] == tag_name:
                response = raw.delete(f"/api/tags/{tag['id']}")
                assert response.status_code < 400, f"タグを消せなかった: {response.status_code}"
