import hashlib
import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.jobs.approvals import ApprovalNotPossible, ApprovalService
from mediaferry.jobs.preflight import PreflightCache, PreflightFailed

from .fake_immich import API_KEY
from .test_schema_artifacts import a_media_file

CAPTURED = "2026-08-17T14:30:00+09:00"


@pytest.fixture
def world(db, data_root, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home",
        base_url=server.url,
        public_url=None,
        secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(b"x", usedforsecurity=False).hexdigest(),
        captured_at=CAPTURED,
    )
    uploads.create_pairs([media_id], [destination_id])
    db.execute(
        "UPDATE upload_record SET state = 'awaiting_datetime_approval', origin = 'pre_existing',"
        " remote_asset_id = 'asset-1', destination_revision_id = ?",
        (destinations.current(destination_id)["id"],),
    )

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    service = ApprovalService(
        db,
        uploads,
        destinations,
        ProfileRegistry(db),
        open_client,
        PreflightCache(destinations, open_client),
    )
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id, "mode": "approve"})
    ctx = store.claim_next()
    return server, service, db, uploads, ctx


def record_of(db):
    return db.execute("SELECT * FROM upload_record").fetchone()


def test_approving_writes_the_capture_time_and_completes(world):
    server, service, db, _, ctx = world

    service.approve(ctx, record_of(db)["id"])

    assert server.datetimes["asset-1"] == CAPTURED
    assert record_of(db)["state"] == "complete"


def test_rejecting_changes_nothing_remote(world):
    server, service, db, _, ctx = world

    service.reject(record_of(db)["id"])

    assert server.datetimes == {}
    assert record_of(db)["state"] == "complete"


def test_a_record_that_is_not_waiting_cannot_be_approved(world):
    server, service, db, _, ctx = world
    db.execute("UPDATE upload_record SET state = 'pending', destination_revision_id = NULL")
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, record_of(db)["id"])


def test_an_unknown_record_is_refused(world):
    _, service, _, _, ctx = world
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, "no-such-record")


def test_approving_re_checks_where_the_revision_points(world):
    server, service, db, _, ctx = world
    server.user_id = "someone-else"
    with pytest.raises(PreflightFailed):
        service.approve(ctx, record_of(db)["id"])
    assert server.datetimes == {}
    assert record_of(db)["state"] == "awaiting_datetime_approval"


def test_rejecting_does_not_need_the_remote(world):
    """却下はリモートに触らないので、向き先が変わっていても消せる."""
    server, service, db, _, ctx = world
    server.user_id = "someone-else"

    service.reject(record_of(db)["id"])

    assert record_of(db)["state"] == "complete"


def test_a_rejection_that_won_the_race_stops_the_approval(world):
    """却下が先に complete を commit したら、承認はリモートに触らない."""
    server, service, db, _, ctx = world
    record_id = record_of(db)["id"]

    service.reject(record_id)

    # 却下が先に complete へ倒しているので、承認は「承認待ちではない」で断られる。
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, record_id)
    assert server.datetimes == {}
    assert record_of(db)["state"] == "complete"


def test_an_invalidated_record_cannot_be_approved(world):
    server, service, db, _, ctx = world
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'グループが変わった'",
        ("2026-08-17T00:00:00+00:00",),
    )
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, record_of(db)["id"])
    assert server.datetimes == {}


def test_a_failed_approval_goes_back_to_waiting(world, monkeypatch):
    """書き換えたか分からないまま complete にしない."""
    server, service, db, _, ctx = world
    from mediaferry.adapters.immich import ImmichClient, ImmichUnavailable

    def boom(*args, **kwargs):
        raise ImmichUnavailable("PUT /api/assets/asset-1 が 503")

    monkeypatch.setattr(ImmichClient, "set_date_time_original", boom)
    with pytest.raises(ImmichUnavailable):
        service.approve(ctx, record_of(db)["id"])

    row = record_of(db)
    assert row["state"] == "awaiting_datetime_approval"
    assert row["claim_job_id"] is None


def test_approving_without_an_asset_id_is_refused(world):
    server, service, db, _, ctx = world
    db.execute("UPDATE upload_record SET remote_asset_id = NULL")
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, record_of(db)["id"])


def test_a_cancel_during_the_approval_stops_the_commit(world, monkeypatch):
    """PUT 中にキャンセルされたら、complete を書かずに承認待ちへ戻す.

    リモートは変わったかもしれない（そこは止められない）が、**「承認済み」と
    記録しない**。人がもう一度確かめられる状態に戻す。
    """
    from mediaferry.adapters.immich import ImmichClient
    from mediaferry.db.jobs import LeaseLost

    server, service, db, _, ctx = world
    real = ImmichClient.set_date_time_original

    def cancel_then_put(self, *args, **kwargs):
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "set_date_time_original", cancel_then_put)
    with pytest.raises(LeaseLost):
        service.approve(ctx, record_of(db)["id"])

    row = record_of(db)
    assert row["state"] == "awaiting_datetime_approval"
    assert row["claim_job_id"] is None
