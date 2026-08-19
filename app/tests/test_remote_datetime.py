"""リモートの日時の観測（§9.10 / §13）.

承認の画面は「現在値と変更案を並べて表示」する。**現在値はどこかに保存されて
いなければ出せない** —— 画面を開くたびに N 件ぶんの HTTP を出すわけにはいかない。

**古い観測で新しい値を上書きしない。** 書き込みは再確認と同じ形（観測したときの
姿を条件に入れる CAS）。
"""

from __future__ import annotations

import pytest

from mediaferry.adapters.immich import ImmichClient, ImmichProtocolError
from mediaferry.db.uploads import UploadRepository

from .fake_immich import API_KEY
from .test_schema_artifacts import a_media_file
from .test_schema_sources import a_profile
from .test_schema_uploads import a_destination, an_upload


# ---------------------------------------------------------------- adapter
def test_the_asset_can_be_read_back(immich):
    immich.assets["some-checksum"] = "asset-1"
    immich.datetimes["asset-1"] = "2026-08-17T14:30:00+09:00"
    with ImmichClient(immich.url, API_KEY) as client:
        asset = client.asset("asset-1")
    assert asset.asset_id == "asset-1"
    assert asset.date_time_original == "2026-08-17T14:30:00+09:00"


def test_an_identifier_from_the_asset_response_is_checked(immich):
    """相手が返す id も境界で検める（§12.3。他の経路と同じ）."""
    immich.echo_key_as_ids = True
    with ImmichClient(immich.url, API_KEY) as client, pytest.raises(ImmichProtocolError):
        client.asset("asset-1")


def test_a_missing_datetime_is_not_invented(immich):
    """相手が返さないなら「分からない」まま持つ（0 や現在時刻で埋めない）."""
    immich.assets["some-checksum"] = "asset-2"
    with ImmichClient(immich.url, API_KEY) as client:
        asset = client.asset("asset-2")
    assert asset.date_time_original is None


# ---------------------------------------------------------------- 保存
@pytest.fixture
def world(db):
    profile = a_profile(db, slug="remote-dt")
    media = a_media_file(db, profile, rel_path="library/remote-dt/A.MP4")
    destination = a_destination(db, name="remote-dt")
    _, revision_id, _ = destination
    record = an_upload(
        db,
        destination,
        media,
        state="complete",
        origin="pre_existing",
        destination_revision_id=revision_id,
        remote_asset_id="asset-1",
        remote_checked_at="t0",
    )
    return UploadRepository(db, None, None), record


def test_the_observed_datetime_is_stored_with_its_time(db, world):
    uploads, record = world

    uploads.stamp_remote_datetime(
        record, "2026-08-17T14:30:00+09:00", checked_at="t1", expect_checked_at="t0"
    )

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["remote_datetime_original"] == "2026-08-17T14:30:00+09:00"
    # **いつ時点の観測かを一緒に持つ**（画面がそう表示する）。
    assert row["remote_checked_at"] == "t1"


def test_an_older_observation_does_not_overwrite_a_newer_one(db, world):
    uploads, record = world
    uploads.stamp_remote_datetime(record, "新しい", checked_at="t1", expect_checked_at="t0")

    uploads.stamp_remote_datetime(record, "古い", checked_at="t2", expect_checked_at="t0")

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["remote_datetime_original"] == "新しい"


def test_only_complete_records_are_stamped(db, world):
    uploads, record = world
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE id = ?", (record,))

    uploads.stamp_remote_datetime(record, "書けない", checked_at="t1", expect_checked_at="t0")

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["remote_datetime_original"] is None


# ---------------------------------------------------------------- 承認の差分
@pytest.fixture
def secret_env(monkeypatch):
    """転送先を扱う経路はマスター鍵を要る（§12.3）."""
    import base64
    import os

    monkeypatch.setenv("MEDIAFERRY_SECRET_KEY", base64.b64encode(os.urandom(32)).decode())


def _awaiting(db, *, proposed_source="2026-08-17T14:30:00+09:00", current=None):
    from mediaferry.db.profiles import ProfileRegistry

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    media = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        captured_at=proposed_source,
    )
    destination = a_destination(db, name="diff-dest")
    _, revision_id, _ = destination
    return an_upload(
        db,
        destination,
        media,
        state="awaiting_datetime_approval",
        origin="pre_existing",
        destination_revision_id=revision_id,
        remote_asset_id="asset-1",
        remote_checked_at="2026-08-18T00:00:00+00:00",
        remote_datetime_original=current,
    )


def test_the_pending_approval_carries_both_sides(secret_env, client, db):
    """画面は**現在値と変更案を並べて**出す（§13）."""
    record_id = _awaiting(db, current="2020-01-01T00:00:00+00:00")

    body = client.get("/api/uploads?state=awaiting_datetime_approval").json()
    assert "records" in body, body
    [row] = body["records"]

    assert row["id"] == record_id
    assert row["remote_current"] == "2020-01-01T00:00:00+00:00"
    assert row["proposed"] == "2026-08-17T14:30:00+09:00"
    # **いつ時点の値かを一緒に返す**（最新が要るなら再確認を回す導線を出す）。
    assert row["remote_checked_at"] == "2026-08-18T00:00:00+00:00"
    assert row["identical"] is False


def test_an_identical_datetime_is_marked_so_the_page_can_say_no_change(secret_env, client, db):
    """同じなら「変更なし」と出して、承認を促さない."""
    _awaiting(db, current="2026-08-17T14:30:00+09:00")

    body = client.get("/api/uploads?state=awaiting_datetime_approval").json()
    assert "records" in body, body
    [row] = body["records"]

    assert row["identical"] is True


def test_an_unknown_current_value_is_not_called_identical(secret_env, client, db):
    """読めなかったものを「変更なし」にしない（承認を飛ばさせない）."""
    _awaiting(db, current=None)

    body = client.get("/api/uploads?state=awaiting_datetime_approval").json()
    assert "records" in body, body
    [row] = body["records"]

    assert row["remote_current"] is None
    assert row["identical"] is False
