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
from mediaferry.jobs.preflight import PreflightCache
from mediaferry.jobs.reconcile import Reconciler
from mediaferry.jobs.uploader import Uploader

from .fake_immich import API_KEY
from .test_publisher import StubProbe
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"
CAPTURED = "2026-08-17T14:30:00+09:00"


@pytest.fixture
def world(db, data_root, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    home = destinations.create(
        name="home",
        base_url=server.url,
        public_url=None,
        secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    # 別名の宛先だが**同じ fake を見る**。宛先ごとに独立して記録が進むことと、
    # 同じリモートに 2 度目を送ると重複として扱われることを、1 つの世界で見る。
    family = destinations.create(
        name="family",
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
    )

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    def a_job(destination_id):
        store = JobStore(db)
        store.enqueue("upload", {"destination_id": destination_id})
        return store.claim_next()

    def an_uploader():
        return Uploader(
            db,
            uploads,
            destinations,
            ProfileRegistry(db),
            data_root,
            open_client,
            PreflightCache(destinations, open_client),
        )

    return server, destinations, uploads, home, family, media_id, a_job, an_uploader


def test_one_media_goes_to_two_destinations_independently(world, db):
    server, _, uploads, home, family, media_id, a_job, an_uploader = world

    pairs = uploads.create_pairs([media_id], [home, family])
    assert [pair.result for pair in pairs] == ["created", "created"]

    an_uploader().run(a_job(home), home)

    states = {
        row["destination_id"]: row["state"]
        for row in db.execute("SELECT destination_id, state FROM upload_record")
    }
    # 片方だけ送っても、もう片方は未送信のまま独立している。
    assert states[home] == "complete"
    assert states[family] == "pending"

    an_uploader().run(a_job(family), family)

    rows = {
        row["destination_id"]: row
        for row in db.execute("SELECT destination_id, state, remote_asset_id FROM upload_record")
    }
    # 2 つ目の宛先は同じ fake を見ているので、重複として扱われる。**送信は起きず**、
    # 既にある資産を引き受ける。自作と証明できないので日時の補正は承認待ちになる（§9.10）。
    assert len(server.uploads) == 1
    assert rows[family]["state"] == "awaiting_datetime_approval"
    assert rows[family]["remote_asset_id"] == rows[home]["remote_asset_id"]


def test_a_second_run_sends_nothing(world, db):
    _, _, uploads, home, _, media_id, a_job, an_uploader = world
    uploads.create_pairs([media_id], [home])
    an_uploader().run(a_job(home), home)

    outcome = an_uploader().run(a_job(home), home)

    assert (outcome.sent, outcome.failed) == (0, 0)


def test_an_interrupted_upload_is_recovered_at_startup(world, db, data_root, monkeypatch):
    server, destinations, uploads, home, _, media_id, a_job, an_uploader = world
    uploads.create_pairs([media_id], [home])
    ctx = a_job(home)

    # 送信の途中で落ちた（サーバ側の成否は不明）。
    def die(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(ImmichClient, "upload_asset", die)
    with pytest.raises(KeyboardInterrupt):
        an_uploader().run(ctx, home)
    assert db.execute("SELECT state FROM upload_record").fetchone()[0] == "needs_recheck"

    monkeypatch.undo()
    report = Reconciler(
        db,
        data_root,
        _publisher(db, data_root),
        JobStore(db),
        uploads=uploads,
        destinations=destinations,
    ).run()
    assert report.uploads_released == 0  # 既に needs_recheck へ落ちている

    an_uploader().run(a_job(home), home)

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["state"] == "complete"
    # 二重にアップロードしていない。
    assert len(server.uploads) == 1


def test_a_record_whose_group_changed_is_never_sent(world, db):
    server, _, uploads, home, _, _, a_job, an_uploader = world
    from .test_selection import a_derived, a_group, a_pair

    profile = ProfileRegistry(db).current("dji-osmo")
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id)
    uploads.create_pairs([output_id], [home])
    # 構成ファイルが差し替わって digest が合わなくなった。
    db.execute("UPDATE media_file SET sha1 = 'edited' WHERE id = ?", (members[0][0],))

    outcome = an_uploader().run(a_job(home), home)

    assert outcome.skipped == 1
    assert server.uploads == []
    assert (
        db.execute(
            "SELECT invalidated_at FROM upload_record WHERE media_file_id = ?", (output_id,)
        ).fetchone()[0]
        is not None
    )


def test_a_group_settled_at_startup_changes_what_can_be_sent(world, db, data_root):
    """`_settle_merges` → `_settle_uploads` の順序を、実際の効果で確かめる.

    `merging` のまま残ったグループは起動時に `detected` へ戻る（出力が無い場合）。
    その member を対象にした `default` の pair は、**根拠が成立しなくなる**ので
    無効化されなければならない。順序が逆だと、グループが `merging` のままの状態で
    評価してしまい、この判定に至らない。
    """
    from .test_selection import a_group, a_pair

    _, destinations, uploads, home, _, _, a_job, _ = world
    profile = ProfileRegistry(db).current("dji-osmo")
    members = a_pair(db, profile)
    group_id = a_group(db, profile, members, status="failed", verification=None)
    # failed のグループの member として送信を許可する。
    uploads.create_pairs([members[0][0]], [home])
    # 実際には結合の途中だった（起動時に detected へ戻る）。
    db.execute("UPDATE merge_group SET status = 'merging' WHERE id = ?", (group_id,))

    report = Reconciler(
        db,
        data_root,
        _publisher(db, data_root),
        JobStore(db),
        uploads=uploads,
        destinations=destinations,
    ).run()

    assert report.merges_released == 1
    row = db.execute(
        "SELECT * FROM upload_record WHERE media_file_id = ?", (members[0][0],)
    ).fetchone()
    # 「結合できなかったグループの member」という根拠は、もう成立しない。
    assert row["invalidated_at"] is not None


def _publisher(db, data_root):
    from mediaferry.adapters.publisher import ArtifactPublisher

    return ArtifactPublisher(db, data_root, StubProbe())
