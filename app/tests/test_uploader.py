import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.jobs.preflight import PreflightCache, PreflightFailed
from mediaferry.jobs.uploader import Uploader

from .fake_immich import API_KEY
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"
CAPTURED = "2026-08-17T14:30:00+09:00"


@pytest.fixture
def world(db, data_root, immich):
    import hashlib

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

    directory = data_root / "library" / "dji-osmo" / "DCIM"
    directory.mkdir(parents=True)
    (directory / "A.MP4").write_bytes(PAYLOAD)
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
        size_bytes=len(PAYLOAD),
        captured_at=CAPTURED,
        mtime_ns=1_700_000_000_000_000_000,
    )
    uploads.create_pairs([media_id], [destination_id])

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    uploader = Uploader(
        db,
        uploads,
        destinations,
        ProfileRegistry(db),
        data_root,
        open_client,
        preflight=PreflightCache(destinations, open_client),
    )
    return server, uploader, ctx, uploads, destinations, destination_id, media_id


def record_of(db):
    return db.execute("SELECT * FROM upload_record").fetchone()


def test_a_new_asset_is_uploaded_tagged_and_dated(world, db):
    server, uploader, ctx, _, _, destination_id, media_id = world

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 1
    row = record_of(db)
    assert row["state"] == "complete"
    assert row["origin"] == "created_by_us"
    assert row["remote_asset_id"] == "asset-1"
    assert row["first_check_result"] == "accept"
    assert server.uploads[0]["deviceAssetId"] == f"mediaferry:{media_id}"
    # 既定の DJI プロファイルはタグを持つ。自作なので付ける。
    assert server.tagged
    assert server.datetimes[row["remote_asset_id"]] == CAPTURED


def test_an_asset_that_already_exists_is_not_uploaded_again(world, db):
    import base64
    import hashlib

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"

    uploader.run(ctx, destination_id)

    row = record_of(db)
    assert server.uploads == []
    assert row["remote_asset_id"] == "asset-existing"
    assert row["origin"] == "pre_existing"
    assert row["first_check_result"] == "reject"
    # 自作と証明できないので、日時は自動で書き換えない。
    assert row["state"] == "awaiting_datetime_approval"
    assert server.datetimes == {}


def test_a_trashed_asset_is_recorded_as_trashed(world, db):
    import base64
    import hashlib

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"
    server.trashed.add("asset-existing")

    uploader.run(ctx, destination_id)

    assert record_of(db)["remote_is_trashed"] == 1


def _an_existing_asset(server):
    import base64
    import hashlib

    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"


def _tag_policy(monkeypatch, tag_pre_existing):
    """プロファイルの `tag_pre_existing` だけを差し替える."""
    from dataclasses import replace

    real = ProfileRegistry.by_id

    def by_id(self, profile_id):
        ref = real(self, profile_id)
        immich = replace(ref.definition.immich, tag_pre_existing=tag_pre_existing)
        return replace(ref, definition=replace(ref.definition, immich=immich))

    monkeypatch.setattr(ProfileRegistry, "by_id", by_id)


def test_a_pre_existing_asset_is_tagged_when_the_profile_says_so(world, db, monkeypatch):
    """既定の DJI プロファイルは `tag_pre_existing: true`（design §6）."""
    server, uploader, ctx, _, _, destination_id, _ = world
    _an_existing_asset(server)
    _tag_policy(monkeypatch, True)

    uploader.run(ctx, destination_id)

    assert server.tagged


def test_a_pre_existing_asset_is_not_tagged_when_the_profile_forbids_it(world, db, monkeypatch):
    """自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めた
    タグを付けない（§9.10）."""
    server, uploader, ctx, _, _, destination_id, _ = world
    _an_existing_asset(server)
    _tag_policy(monkeypatch, False)

    uploader.run(ctx, destination_id)

    assert server.tagged == {}


def test_the_preflight_stops_everything_before_a_byte_is_sent(world, db):
    """preflight は claim の後だが、**リモートに触る前**なので pending へ戻す."""
    server, uploader, ctx, _, _, destination_id, _ = world
    server.user_id = "someone-else"

    with pytest.raises(PreflightFailed):
        uploader.run(ctx, destination_id)

    assert server.uploads == []
    row = record_of(db)
    assert row["state"] == "pending"
    assert row["claim_job_id"] is None


def test_a_cancel_while_the_upload_is_in_flight_stops_the_commit(world, db, monkeypatch):
    """送信は成功しても、キャンセル後の commit は通さない（§8）.

    通すと、画面はキャンセル済みなのにタグと日時まで進む。
    """
    server, uploader, ctx, _, _, destination_id, _ = world
    real = ImmichClient.upload_asset

    def cancel_then_upload(self, *args, **kwargs):
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", cancel_then_upload)
    uploader.run(ctx, destination_id)

    row = record_of(db)
    # サーバには上がったかもしれないので needs_recheck。タグも日時も付けない。
    assert row["state"] == "needs_recheck"
    # **結果を commit させない。** 通すと「キャンセル済みなのに送信済みの記録」ができる。
    assert row["remote_asset_id"] is None
    assert server.tagged == {}
    assert server.datetimes == {}


def test_the_target_is_re_checked_before_the_tags_when_the_ttl_expired(world, db, monkeypatch):
    """送信が TTL を跨いだら、タグと日時の前に向き先を取り直す."""
    server, uploader, ctx, _, _, destination_id, _ = world
    uploader._preflight._ttl = 0  # noqa: SLF001 - TTL 切れを再現する
    real = ImmichClient.upload_asset

    def upload_then_move(self, *args, **kwargs):
        outcome = real(self, *args, **kwargs)
        # 送信中に別のライブラリへ差し替わった。
        server.user_id = "someone-else"
        return outcome

    monkeypatch.setattr(ImmichClient, "upload_asset", upload_then_move)
    with pytest.raises(PreflightFailed):
        uploader.run(ctx, destination_id)

    # 別ライブラリにタグも日時も書かない。
    assert server.tagged == {}
    assert server.datetimes == {}


def test_a_server_error_is_retried_and_then_failed(world, db, monkeypatch):
    server, uploader, ctx, _, _, destination_id, _ = world
    # **preflight の後で落とす。** `server.fail_next` にすると preflight が先に
    # 落ちて、再試行の分岐を一度も通らない。
    monkeypatch.setattr("mediaferry.jobs.uploader.BACKOFF_BASE_SECONDS", 0.01)
    from mediaferry.adapters.immich import ImmichUnavailable

    def unavailable(*args, **kwargs):
        raise ImmichUnavailable("POST /api/assets/bulk-upload-check が 503")

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", unavailable)

    outcome = uploader.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.failed == 1
    assert row["state"] == "failed"
    assert row["attempts"] == 3
    assert row["last_error"]
    # 秘密は残さない。
    assert API_KEY not in row["last_error"]


def test_an_auth_failure_is_not_retried(world, db, monkeypatch):
    """鍵が失効した場合、何度試しても変わらない. 再試行に回さず落とす.

    preflight を通った後に 401 になる筋書きを作る（鍵の失効は送信の途中でも
    起きる）。preflight の段で落とすと、この分岐を一度も通らない。
    """
    from mediaferry.adapters.immich import ImmichAuthFailed

    server, uploader, ctx, _, _, destination_id, _ = world

    def refuse(*args, **kwargs):
        raise ImmichAuthFailed("POST /api/assets が 401")

    monkeypatch.setattr(ImmichClient, "upload_asset", refuse)
    with pytest.raises(ImmichAuthFailed):
        uploader.run(ctx, destination_id)

    row = record_of(db)
    assert row["attempts"] == 0
    # 送信の成否が不明なまま降りるので、次回は checking から照合し直す。
    assert row["state"] == "needs_recheck"


def test_a_cancel_before_sending_leaves_it_pending(world, db):
    _, uploader, ctx, _, _, destination_id, _ = world
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 0
    assert record_of(db)["state"] == "pending"


def test_a_cancel_during_the_send_asks_for_a_recheck(world, db, monkeypatch):
    """サーバ側の成否が不明なので、次回 checking から照合し直す."""
    server, uploader, ctx, _, _, destination_id, _ = world

    def cancel_then_upload(*args, **kwargs):
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        raise KeyboardInterrupt

    monkeypatch.setattr(ImmichClient, "upload_asset", cancel_then_upload)
    with pytest.raises(KeyboardInterrupt):
        uploader.run(ctx, destination_id)

    assert record_of(db)["state"] == "needs_recheck"


def test_a_record_that_lost_its_grounds_is_refused_not_sent(world, db):
    server, uploader, ctx, _, _, destination_id, _ = world
    db.execute(
        "UPDATE media_file SET missing_at = '2026-08-17T00:00:00+00:00' WHERE role = 'original'"
    )

    outcome = uploader.run(ctx, destination_id)

    assert outcome.skipped == 1
    assert server.uploads == []
    row = record_of(db)
    assert row["invalidated_at"] is not None


def test_the_lease_is_extended_while_the_file_is_sent(world, db, monkeypatch):
    import time

    server, uploader, ctx, _, _, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    beats = []
    monkeypatch.setattr(ctx, "heartbeat", lambda: beats.append(1))
    real = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(0.3)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)
    uploader.run(ctx, destination_id)

    assert beats
    assert record_of(db)["state"] == "complete"


def test_the_job_stops_when_there_is_nothing_left(world, db):
    _, uploader, ctx, _, _, destination_id, _ = world
    uploader.run(ctx, destination_id)
    outcome = uploader.run(ctx, destination_id)
    assert (outcome.sent, outcome.failed, outcome.skipped) == (0, 0, 0)


def test_the_claim_is_extended_while_the_file_is_sent(world, db, monkeypatch):
    """リースだけ延ばしても、claim が切れれば結果を commit できない.

    **送信が claim の寿命より長い**状況を作る（短いと延長の有無で差が出ない）。
    """
    import time

    server, uploader, ctx, uploads, destinations, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    real = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(1.5)  # claim（1 秒）より長い
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)
    # claim を 1 秒で切れるようにしてから走らせる。
    monkeypatch.setattr(uploads, "claim_next", _claim_with(uploads, seconds=1))

    uploader.run(ctx, destination_id)

    assert record_of(db)["state"] == "complete"


def _claim_with(uploads, seconds):
    real = uploads.claim_next

    def claim(revision, job_id, token, lease_seconds=60):
        return real(revision, job_id, token, seconds)

    return claim


def test_a_duplicate_after_an_accept_is_unknown_not_ours(world, db, monkeypatch):
    """チェックとアップロードの間に別のクライアントが割り込んだ場合.

    自作の証明が無いので origin は unknown になり、日時は承認待ちになる。
    """
    import base64
    import hashlib

    from mediaferry.adapters.immich import CheckOutcome

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"  # 既にサーバにある
    # ただし初回の照合は accept を返した（割り込みの再現）。
    monkeypatch.setattr(
        ImmichClient,
        "bulk_upload_check",
        lambda self, items: {key: CheckOutcome("accept", None, False) for key, _ in items},
    )

    uploader.run(ctx, destination_id)

    row = record_of(db)
    assert row["origin"] == "unknown"
    assert row["state"] == "awaiting_datetime_approval"
    assert server.datetimes == {}


def test_a_rejected_request_fails_the_record_without_retrying(world, db, monkeypatch):
    """4xx は再試行しても変わらない. 理由を残して次のレコードへ進む."""
    from mediaferry.adapters.immich import ImmichRejected

    server, uploader, ctx, _, _, destination_id, _ = world

    def rejected(*args, **kwargs):
        raise ImmichRejected("POST /api/assets が 400")

    monkeypatch.setattr(ImmichClient, "upload_asset", rejected)

    outcome = uploader.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.failed == 1
    assert row["state"] == "failed"
    assert row["last_error"]
    assert row["attempts"] == 1
