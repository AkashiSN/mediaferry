import base64
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
from mediaferry.jobs.recheck import Rechecker

from .fake_immich import API_KEY, FakeImmich
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"
CHECKSUM = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()


_BOXES: dict[int, SecretBox] = {}


@pytest.fixture
def world(db, data_root, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    box = SecretBox(os.urandom(32))
    _BOXES[id(db)] = box
    destinations = DestinationRepository(db, CredentialStore(db, box))
    destination_id = destinations.create(
        name="home",
        base_url=server.url,
        public_url=None,
        secret=API_KEY,
        identity=RemoteIdentity.observed(server.user_id),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
    )
    uploads.create_pairs([media_id], [destination_id])
    db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = 'asset-1',"
        " remote_is_trashed = 0, destination_revision_id = ?",
        (destinations.current(destination_id)["id"],),
    )
    server.assets[CHECKSUM] = "asset-1"

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id, "mode": "recheck"})
    ctx = store.claim_next()
    rechecker = Rechecker(
        uploads, destinations, open_client, PreflightCache(destinations, open_client)
    )
    return server, rechecker, ctx, destination_id, db


def record_of(db):
    return db.execute("SELECT * FROM upload_record").fetchone()


def _box_of(db):
    """テスト内で作り直しても同じ鍵になるよう、fixture が使った箱を再利用する."""
    return _BOXES[id(db)]


def test_an_asset_that_is_still_there_is_just_stamped(world):
    server, rechecker, ctx, destination_id, db = world

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.checked == 1
    assert row["remote_checked_at"] is not None
    assert row["remote_asset_id"] == "asset-1"
    assert row["remote_is_trashed"] == 0


def test_an_asset_in_the_trash_is_flagged(world):
    server, rechecker, ctx, destination_id, db = world
    server.trashed.add("asset-1")

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.trashed == 1
    assert record_of(db)["remote_is_trashed"] == 1


def test_an_asset_restored_from_the_trash_clears_the_flag(world):
    server, rechecker, ctx, destination_id, db = world
    db.execute("UPDATE upload_record SET remote_is_trashed = 1")

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.restored == 1
    assert record_of(db)["remote_is_trashed"] == 0


def test_a_vanished_asset_is_shown_as_missing_not_resent(world):
    server, rechecker, ctx, destination_id, db = world
    server.assets.clear()  # 保持期限を過ぎて完全に消えた

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.vanished == 1
    # 自動では送り直さない。利用者が意図的に消したものを黙って戻さない。
    assert row["state"] == "complete"
    assert row["remote_asset_id"] is None
    assert row["remote_checked_at"] is not None
    assert server.uploads == []


def test_records_from_an_old_epoch_are_not_touched(world):
    """旧 epoch は別ライブラリへ送った履歴. 現行の資格情報で照合しない."""
    server, rechecker, ctx, destination_id, db = world
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity

    before = record_of(db)["remote_asset_id"]
    destinations = DestinationRepository(db, CredentialStore(db, _box_of(db)))
    # **別アカウントへ向け替える**（ホストが同じままだと epoch は進まない）。
    destinations.add_revision(
        destination_id,
        base_url=server.url,
        public_url=None,
        secret=API_KEY,
        identity=RemoteIdentity.observed("another-user"),
    )
    assert destinations.current(destination_id)["target_epoch"] == 2
    server.user_id = "another-user"  # preflight を通す
    server.assets.clear()  # 新しいライブラリには何も無い

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    assert record_of(db)["remote_asset_id"] == before


def test_only_complete_records_are_rechecked(world):
    server, rechecker, ctx, destination_id, db = world
    db.execute("UPDATE upload_record SET state = 'pending', destination_revision_id = NULL")

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 0


def test_the_preflight_runs_before_the_recheck(world):
    server, rechecker, ctx, destination_id, db = world
    server.user_id = "someone-else"
    from mediaferry.jobs.preflight import PreflightFailed

    with pytest.raises(PreflightFailed):
        rechecker.run(ctx, destination_id)
    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 0


def test_records_are_checked_in_one_batch(world):
    server, rechecker, ctx, destination_id, db = world
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    profile = ProfileRegistry(db).current("dji-osmo")
    revision_id = record_of(db)["destination_revision_id"]
    for index in range(3):
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/B{index}.MP4",
            sha1=f"{index:040d}",
        )
        db.execute(
            "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
            " selection_rule, origin, remote_asset_id, checksum, destination_revision_id,"
            " created_at, updated_at)"
            " VALUES (?, ?, 1, ?, 'complete', 'default', 'created_by_us', ?, ?, ?, ?, ?)",
            (
                new_id(),
                destination_id,
                media_id,
                f"asset-b{index}",
                f"{index:040d}",
                revision_id,
                now_iso(),
                now_iso(),
            ),
        )

    rechecker.run(ctx, destination_id)

    # 4 件を 1 回で照合する（1 件ずつ叩かない）。
    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 1


def test_a_cancelled_recheck_stops_early(world):
    """**キャンセル済みなら 1 要求も出さない。**

    件数だけを見ていると、`users/me` を投げてから止まる実装を見逃す。鍵付きの
    要求はキャンセルの後に出してよいものではない（§14）。
    """
    server, rechecker, ctx, destination_id, db = world
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    assert server.requests == []


def test_a_recheck_cancelled_during_the_check_writes_nothing(world):
    """照合の最中にキャンセルされたら、結果を書かずに降りる.

    書くと「キャンセルした」と表示しながらリモートの観測を反映したことになる。
    """
    server, rechecker, ctx, destination_id, db = world
    before = db.execute("SELECT remote_checked_at FROM upload_record").fetchall()

    real = FakeImmich.route

    def cancel_during_check(self, method, path, body, headers):  # noqa: ANN001, ANN202
        if path == "/api/assets/bulk-upload-check":
            db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real(self, method, path, body, headers)

    server.route = cancel_during_check.__get__(server, FakeImmich)

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    after = db.execute("SELECT remote_checked_at FROM upload_record").fetchall()
    assert [row["remote_checked_at"] for row in after] == [
        row["remote_checked_at"] for row in before
    ]


def test_a_recheck_whose_lease_expired_writes_nothing(world):
    """**キャンセルだけでなくリースの失効も見る。**

    `ctx.cancelled()` はジョブの `status` しか見ない。リースが切れた
    （＝起動時の回収が同じジョブを別の worker へ渡しうる）状態のまま書くと、
    2 つの書き手が同じ行を触る。
    """
    from mediaferry.db.jobs import LeaseLost

    server, rechecker, ctx, destination_id, db = world
    before = [row["remote_checked_at"] for row in db.execute("SELECT * FROM upload_record")]
    db.execute(
        "UPDATE job SET lease_expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (ctx.job_id,),
    )

    with pytest.raises(LeaseLost):
        rechecker.run(ctx, destination_id)

    after = [row["remote_checked_at"] for row in db.execute("SELECT * FROM upload_record")]
    assert after == before


def test_a_recheck_cancelled_between_batches_sends_no_more(world, monkeypatch):
    """**batch の合間にもキャンセルを見る。**

    1 回の照合が 500 件ずつに割れるので、adapter に任せきりにすると、最初の
    batch の途中でキャンセルしても残りを全部送ってしまう。
    """
    server, rechecker, ctx, destination_id, db = world
    # **1 件だけだと分割が起きない。** 3 件にして、batch を 1 に絞る。
    profile = ProfileRegistry(db).current("dji-osmo")
    revision_id = db.execute("SELECT destination_revision_id FROM upload_record").fetchone()[0]
    for index in (2, 3):
        extra = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/{index}.MP4",
            sha1=f"{index:040d}",
        )
        UploadRepository(
            db, ProfileRegistry(db), DestinationRepository(db, CredentialStore(db, _BOXES[id(db)]))
        ).create_pairs([extra], [destination_id])
    db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = 'asset-1',"
        " remote_is_trashed = 0, destination_revision_id = ? WHERE state = 'pending'",
        (revision_id,),
    )
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 3
    # **adapter 側も 1 件ずつにする。** ここを絞らないと、全件を adapter へ
    # 渡す実装（＝直したかった形）でも要求は 1 本に見え、区別が付かない。
    monkeypatch.setattr("mediaferry.jobs.recheck.BULK_CHECK_BATCH", 1)
    monkeypatch.setattr("mediaferry.adapters.immich.BULK_CHECK_BATCH", 1)
    real = ImmichClient.bulk_upload_check

    def cancel_after_first(self, items):  # noqa: ANN001, ANN202
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real(self, items)

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", cancel_after_first)

    rechecker.run(ctx, destination_id)

    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 1


def test_a_record_in_flight_is_not_stamped_by_a_recheck(world):
    """進行中の行には所有者がいる. claim を持たない経路では触らない."""
    from mediaferry.db.jobs import JobStore

    server, rechecker, ctx, destination_id, db = world
    # claim_job_id は job(id) への外部キー。実在するジョブでないと入らない。
    other_job = JobStore(db).enqueue("upload", {"destination_id": destination_id})
    db.execute(
        "UPDATE upload_record SET state = 'uploading', claim_job_id = ?,"
        " claim_token = 'other-token', claim_expires_at = '2999-01-01T00:00:00+00:00'",
        (other_job,),
    )
    rechecker.run(ctx, destination_id)
    assert record_of(db)["remote_checked_at"] is None


def test_stamping_refuses_a_record_that_is_not_complete(world):
    """`stamp_remote` は `complete` の行だけを触る.

    再確認の選択側でも絞っているが、**このメソッドは公開されている**ので、
    別の経路から呼ばれても進行中の行を書き換えないことを固定する。
    """
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository
    from mediaferry.db.profiles import ProfileRegistry as Registry
    from mediaferry.db.uploads import UploadRepository

    server, rechecker, ctx, destination_id, db = world
    uploads = UploadRepository(
        db, Registry(db), DestinationRepository(db, CredentialStore(db, _box_of(db)))
    )
    record_id = record_of(db)["id"]
    db.execute("UPDATE upload_record SET state = 'needs_recheck', remote_asset_id = NULL")

    uploads.stamp_remote(record_id, asset_id="asset-9", is_trashed=1, checked_at="2026-01-01")

    row = record_of(db)
    assert row["remote_asset_id"] is None
    assert row["remote_checked_at"] is None
