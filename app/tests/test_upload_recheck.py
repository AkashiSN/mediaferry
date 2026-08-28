import base64
import hashlib
import os
from datetime import timedelta

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.clock import iso, now_iso, utcnow
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.ids import new_id
from mediaferry.jobs.preflight import PreflightCache
from mediaferry.jobs.recheck import Rechecker

from .fake_immich import API_KEY, FakeImmich
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"
CHECKSUM = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()

PAYLOAD_2 = b"video-bytes-2"
# `upload_record.checksum` は sha1 の 16 進。adapter が base64 へ直して送るので、
# fake の `assets` には base64 の方を鍵として入れる。
SHA1_2 = hashlib.sha1(PAYLOAD_2, usedforsecurity=False).hexdigest()
CHECKSUM_2 = base64.b64encode(hashlib.sha1(PAYLOAD_2, usedforsecurity=False).digest()).decode()


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


def test_a_vanished_asset_is_invalidated_so_it_returns_to_unsent(world):
    """消えた資産の記録は無効化する。**送り直し専用の状態を持たない。**

    無効化された記録は「この宛先の有効な記録」ではなくなるので、そのメディアは
    通常の「まだ送っていない」へ戻る（§9.10）。送信そのものは利用者の明示操作の
    ままなので、「意図的に消したものを黙って送り直さない」は保てる。
    """
    server, rechecker, ctx, destination_id, db = world
    server.assets.clear()  # 保持期限を過ぎて完全に消えた

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.vanished == 1
    assert row["remote_asset_id"] is None
    assert row["remote_checked_at"] is not None
    assert row["invalidated_at"] is not None
    assert row["invalidated_reason"] == "remote_missing"
    # **この場では送らない。** 送るのは利用者が通常経路で選んだとき。
    assert server.uploads == []


def test_an_asset_in_the_trash_is_not_invalidated(world):
    """ゴミ箱に在るのは「無い」の証明ではない。無効化すると二重に上がる."""
    server, rechecker, ctx, destination_id, db = world
    server.trashed.add("asset-1")

    rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert row["remote_is_trashed"] == 1
    assert row["invalidated_at"] is None


def test_a_row_that_moved_during_the_check_is_not_invalidated_either(world):
    """**照合したときの行にしか書かない**（§9.10）。無効化も同じ条件で守る.

    照合の最中に他の書き手が `remote_asset_id` を動かしていたら、こちらの
    「消えていた」は古い観測である。それで無効化すると、在る資産の記録を
    未送信へ戻して二重に上げる。
    """
    server, rechecker, ctx, destination_id, db = world
    server.assets.clear()
    original = server.route

    def hooked(method, path, body, headers):
        result = original(method, path, body, headers)
        if path == "/api/assets/bulk-upload-check":
            # 照合の応答を返した直後に、別の書き手が行を動かした。
            db.execute("UPDATE upload_record SET remote_asset_id = 'asset-moved'")
        return result

    server.route = hooked

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.checked == 0
    assert row["remote_asset_id"] == "asset-moved"
    assert row["invalidated_at"] is None


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


def _three_complete_records(world):
    """batch の分割を起こすために、`complete` の行を 3 件にする."""
    server, rechecker, ctx, destination_id, db = world
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
            db, ProfileRegistry(db), DestinationRepository(db, CredentialStore(db, _box_of(db)))
        ).create_pairs([extra], [destination_id])
    db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = 'asset-1',"
        " remote_is_trashed = 0, destination_revision_id = ? WHERE state = 'pending'",
        (revision_id,),
    )


def test_a_recheck_cancelled_while_the_target_is_checked_sends_no_check(world):
    """**preflight の後、最初の batch の前にも見る。**

    向き先の再確認は相手待ちなので、その間にキャンセルが commit されうる。
    最初の batch だけ確認を飛ばすと、キャンセル済みのジョブから鍵付きの
    照合要求が出る（§14）。
    """
    server, rechecker, ctx, destination_id, db = world
    real = FakeImmich.route

    def cancel_during_users_me(self, method, path, body, headers):  # noqa: ANN001, ANN202
        if path == "/api/users/me":
            db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real(self, method, path, body, headers)

    server.route = cancel_during_users_me.__get__(server, FakeImmich)

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    assert ("POST", "/api/assets/bulk-upload-check") not in server.requests


def test_a_recheck_that_lost_its_lease_between_batches_sends_no_more(world, monkeypatch):
    """**batch の合間はキャンセルだけでなくリースも見る。**

    `status` が `running` のままリースだけ失効した場合、キャンセルの確認は
    素通りする。失効した worker が残りの batch を送り続け、書けないと分かるのは
    最後の `stamp_many` になる。
    """
    from mediaferry.db.jobs import LeaseLost

    server, rechecker, ctx, destination_id, db = world
    _three_complete_records(world)
    monkeypatch.setattr("mediaferry.jobs.recheck.BULK_CHECK_BATCH", 1)
    monkeypatch.setattr("mediaferry.adapters.immich.BULK_CHECK_BATCH", 1)
    real = ImmichClient.bulk_upload_check

    def expire_the_lease(self, items):  # noqa: ANN001, ANN202
        db.execute(
            "UPDATE job SET lease_expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (ctx.job_id,),
        )
        return real(self, items)

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", expire_the_lease)

    with pytest.raises(LeaseLost):
        rechecker.run(ctx, destination_id)

    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 1


def test_a_record_that_moved_while_it_was_checked_is_not_stamped_with_the_old_result(world):
    """**照合の結果は、照合したときの行にしか書かない。**

    消滅と判定されて `remote_asset_id` が NULL の行へ、この照合が終わる前に
    **別の書き込みが新しい資産の観測を書いた**とする。そこへ古い結果
    （消滅＝NULL）を書くと、新しい観測を消してしまう。
    """
    server, rechecker, ctx, destination_id, db = world
    # サーバには無い（accept ＝ 消滅と判定される）。
    server.assets.clear()
    db.execute("UPDATE upload_record SET remote_asset_id = NULL, remote_checked_at = ?", ("t0",))
    real = FakeImmich.route

    def resend_during_check(self, method, path, body, headers):  # noqa: ANN001, ANN202
        if path == "/api/assets/bulk-upload-check":
            # 別の書き込みが、この行を新しい資産の観測で上書きした。
            db.execute(
                "UPDATE upload_record SET remote_asset_id = 'asset-new', remote_checked_at = ?,"
                " remote_is_trashed = 0",
                ("t1",),
            )
        return real(self, method, path, body, headers)

    server.route = resend_during_check.__get__(server, FakeImmich)

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert row["remote_asset_id"] == "asset-new"
    assert row["remote_checked_at"] == "t1"
    # 書けなかった行を「確認した」と数えない。
    assert outcome.checked == 0
    assert outcome.vanished == 0
    # 書けなかった行を「リモートに存在しない」と報せない（消えていない）。
    events = [row["message"] for row in db.execute("SELECT message FROM job_event")]
    assert "リモートに存在しない資産がある" not in events


def test_a_result_older_than_the_last_check_is_not_written(world):
    """**別の再確認が先に書いた行を、古い観測で上書きしない。**

    資産の id が同じでも、`remote_checked_at` が進んでいれば相手の観測の方が
    新しい。ゴミ箱の出入りは行の値だけでは見分けられないので、時刻も条件に
    入れて古い結果を落とす。
    """
    server, rechecker, ctx, destination_id, db = world
    db.execute("UPDATE upload_record SET remote_checked_at = ?", ("t0",))
    server.trashed.add("asset-1")  # こちらの観測は「ゴミ箱にある」
    real = FakeImmich.route

    def another_recheck_wins(self, method, path, body, headers):  # noqa: ANN001, ANN202
        if path == "/api/assets/bulk-upload-check":
            db.execute("UPDATE upload_record SET remote_checked_at = ?", ("t1",))
        return real(self, method, path, body, headers)

    server.route = another_recheck_wins.__get__(server, FakeImmich)

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert row["remote_checked_at"] == "t1"
    assert row["remote_is_trashed"] == 0
    assert outcome.checked == 0


def _uploads_of(db):
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository
    from mediaferry.db.profiles import ProfileRegistry as Registry
    from mediaferry.db.uploads import UploadRepository

    return UploadRepository(
        db, Registry(db), DestinationRepository(db, CredentialStore(db, _box_of(db)))
    )


def test_stamping_many_refuses_a_row_that_no_longer_matches_the_observation(world):
    """**このメソッドは公開されている。** 別の経路から呼ばれても、観測した姿と
    違う行は書き換えない（`stamp_many`）."""
    from mediaferry.db.uploads import Stamp

    server, rechecker, ctx, destination_id, db = world
    uploads = _uploads_of(db)
    record_id = record_of(db)["id"]
    db.execute(
        "UPDATE upload_record SET remote_asset_id = 'asset-new', remote_checked_at = ?", ("t0",)
    )

    written = uploads.stamp_many(
        ctx,
        [
            Stamp(
                record_id=record_id,
                asset_id=None,
                is_trashed=0,
                # 観測したときは別の資産を指していた（照合の後に動いた）。
                expect_asset_id="asset-1",
                expect_checked_at="t0",
            )
        ],
        checked_at="t1",
    )

    assert written == set()
    row = record_of(db)
    assert row["remote_asset_id"] == "asset-new"
    assert row["remote_checked_at"] == "t0"


def test_stamping_many_writes_nothing_when_the_lease_is_gone(world):
    """リースが切れていれば 1 行も書かない（取引の中で確かめる）."""
    from mediaferry.db.jobs import LeaseLost
    from mediaferry.db.uploads import Stamp

    server, rechecker, ctx, destination_id, db = world
    uploads = _uploads_of(db)
    record_id = record_of(db)["id"]
    db.execute("UPDATE upload_record SET remote_checked_at = ?", ("t0",))
    db.execute(
        "UPDATE job SET lease_expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (ctx.job_id,)
    )

    with pytest.raises(LeaseLost):
        uploads.stamp_many(
            ctx,
            [
                Stamp(
                    record_id=record_id,
                    asset_id="asset-2",
                    is_trashed=0,
                    expect_asset_id="asset-1",
                    expect_checked_at="t0",
                )
            ],
            checked_at="t1",
        )

    row = record_of(db)
    assert row["remote_asset_id"] == "asset-1"
    assert row["remote_checked_at"] == "t0"


def test_a_record_moved_out_of_complete_while_it_was_checked_is_not_stamped(world):
    """**`complete` は終端ではない。** 照合の最中に、別の書き込みが行の state だけを動かせる.

    そうした書き込みは `remote_asset_id` も `remote_checked_at` も変えない
    （状態だけを `pending` に戻す）ので、値の一致だけでは古い結果が通ってしまう。
    """
    server, rechecker, ctx, destination_id, db = world
    server.assets.clear()  # accept ＝ 消滅と判定される
    db.execute("UPDATE upload_record SET remote_asset_id = NULL, remote_checked_at = ?", ("t0",))
    real = FakeImmich.route

    def state_moves_during_check(self, method, path, body, headers):  # noqa: ANN001, ANN202
        if path == "/api/assets/bulk-upload-check":
            db.execute("UPDATE upload_record SET state = 'pending', remote_is_trashed = NULL")
        return real(self, method, path, body, headers)

    server.route = state_moves_during_check.__get__(server, FakeImmich)

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert row["state"] == "pending"
    assert row["remote_checked_at"] == "t0"
    assert outcome.checked == 0


def test_a_cancel_that_lands_just_before_the_write_is_not_a_failure(world, monkeypatch):
    """**キャンセルの確認と書き込みの間にも窓がある。**

    `ctx.cancelled()` を見た後に `cancelling` が commit されると、書き込みの
    取引の中の `assert_lease` が `LeaseLost` を投げる。そのまま外へ出すと
    `JobRunner` が**利用者の押したキャンセルをジョブの失敗として記録する**
    （§9.9。取り込み・結合・送信と同じ形で降りる）。
    """
    from mediaferry.clock import now_iso as real_now_iso

    server, rechecker, ctx, destination_id, db = world

    def cancel_then_now():
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real_now_iso()

    monkeypatch.setattr("mediaferry.jobs.recheck.now_iso", cancel_then_now)

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    assert record_of(db)["remote_checked_at"] is None


def test_a_recheck_whose_lease_expired_sends_no_preflight(world):
    """**最初のリモート要求は preflight。** その前にもリースを見る.

    `status` が `running` のままリースだけ失効した worker でも、
    `ctx.cancelled()` は素通りする。preflight の `GET /api/users/me` も鍵を
    付けた要求なので、所有権を失った worker から出してはいけない（§14）。
    """
    from mediaferry.db.jobs import LeaseLost

    server, rechecker, ctx, destination_id, db = world
    db.execute(
        "UPDATE job SET lease_expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (ctx.job_id,),
    )

    with pytest.raises(LeaseLost):
        rechecker.run(ctx, destination_id)

    assert server.requests == []


def test_a_slow_check_keeps_the_lease_alive(world, monkeypatch):
    """**照合の待ち時間はリースより長くなりうる。**

    `assert_lease` は見るだけで延ばさない。相手待ちの間に心拍を打たないと、
    遅い Immich では正常な再確認が必ずリース切れになり、`JobRunner` が
    failed として記録する（**遅いだけで壊れていないのに完了できない**）。

    リースの満了を 1 秒にした別のジョブで回す。1 本の照合がそれより長い。
    """
    import time

    server, rechecker, _, destination_id, db = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    # **満了も延長幅も 1 秒の store。** 心拍が効いていなければ照合中に切れる。
    store = JobStore(db, lease_seconds=1)
    store.enqueue("upload", {"destination_id": destination_id, "mode": "recheck"})
    ctx = store.claim_next()
    real = ImmichClient.bulk_upload_check

    def slow_check(self, items):  # noqa: ANN001, ANN202
        time.sleep(1.5)
        return real(self, items)

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", slow_check)

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 1
    assert record_of(db)["remote_checked_at"] is not None


def test_a_slow_preflight_does_not_eat_the_lease(world, monkeypatch):
    """**最初のリモート要求の前に満期にしておく。**

    `assert_lease` を通った時点で残りが僅かだと、preflight（相手待ち）の間に
    切れて、最初の照合に入れない。見るだけでなく、そこで延ばす。
    """
    import time

    server, rechecker, ctx, destination_id, db = world
    real_users_me = ImmichClient.users_me

    def slow_users_me(self):  # noqa: ANN001, ANN202
        time.sleep(0.6)
        return real_users_me(self)

    monkeypatch.setattr(ImmichClient, "users_me", slow_users_me)
    # 残り 0.3 秒。preflight の方が長いので、延ばさなければ照合の前に切れる。
    db.execute(
        "UPDATE job SET lease_expires_at = ? WHERE id = ?",
        (iso(utcnow() + timedelta(seconds=0.3)), ctx.job_id),
    )

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 1


def test_a_slow_preflight_keeps_the_lease_alive(world, monkeypatch):
    """**preflight の待ち時間にも心拍が要る。**

    直前に 1 回打つだけでは、`users/me` がリースより長くかかったときに、
    最初の照合へ入る前に切れる（クライアントの timeout は最大 86400 秒）。
    遅いだけで壊れていない Immich で、正常な再確認が failed になる。
    """
    import time

    server, rechecker, _, destination_id, db = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    store = JobStore(db, lease_seconds=1)
    store.enqueue("upload", {"destination_id": destination_id, "mode": "recheck"})
    ctx = store.claim_next()
    real_users_me = ImmichClient.users_me

    def slow_users_me(self):  # noqa: ANN001, ANN202
        time.sleep(1.5)
        return real_users_me(self)

    monkeypatch.setattr(ImmichClient, "users_me", slow_users_me)

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 1


def a_stacked_pair(world_tuple):
    """`world` の 1 件に相方を足して、両方を同じ組の `stacked` にする.

    資産はどちらも相手に在る状態にする（消滅とスタックの照合を混ぜない）。
    """
    server, _, _, destination_id, db = world_tuple
    profile = ProfileRegistry(db).current("dji-osmo")
    second = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/B.MP4",
        sha1=SHA1_2,
    )
    revision_id = db.execute("SELECT destination_revision_id FROM upload_record").fetchone()[
        "destination_revision_id"
    ]
    db.execute(
        "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
        " selection_rule, origin, checksum, remote_asset_id, remote_is_trashed,"
        " destination_revision_id, created_at, updated_at)"
        " VALUES (?, ?, 1, ?, 'complete', 'default', 'created_by_us', ?, 'asset-2', 0, ?, ?, ?)",
        (new_id(), destination_id, second, SHA1_2, revision_id, now_iso(), now_iso()),
    )
    server.assets[CHECKSUM_2] = "asset-2"
    # `0015` / `0016` の trigger が形を守っているので、3 列を一緒に書く。
    db.execute("UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = 'stack-1'")


def test_a_stack_that_is_gone_on_the_server_is_reopened(world):
    """**解けた組を `stacked` のまま残さない。** 設定 › 送り先の「N 組」が嘘になる."""
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    # 相手にはスタックが無い（利用者が解除した）。資産はどちらも在る。

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 1
    rows = db.execute("SELECT stack_state, remote_stack_id FROM upload_record").fetchall()
    assert [row["stack_state"] for row in rows] == [None, None]
    assert [row["remote_stack_id"] for row in rows] == [None, None]


def test_a_stack_whose_members_changed_is_reopened(world):
    """集合が一致しない組も戻す。§9.11 が「触らない」と決めている状態へ落とすため."""
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    server.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "someone-else"]}

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 1
    assert (
        db.execute("SELECT count(*) AS n FROM upload_record WHERE stack_state IS NULL").fetchone()[
            "n"
        ]
        == 2
    )


def test_a_stack_that_still_matches_is_not_touched(world):
    """一致している組には触らない."""
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    server.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "asset-2"]}

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 0
    rows = db.execute("SELECT stack_state, remote_stack_id FROM upload_record").fetchall()
    assert all(row["stack_state"] == "stacked" for row in rows)
    assert all(row["remote_stack_id"] == "stack-1" for row in rows)


def test_no_stacked_rows_means_no_request_for_stacks(world):
    """**空振りの要求を出さない。** `stacked` が 0 件なら相手に聞かない."""
    server, rechecker, ctx, destination_id, db = world

    rechecker.run(ctx, destination_id)

    assert ("GET", "/api/stacks") not in server.requests


def test_a_cancel_during_the_stack_check_writes_nothing(world):
    """照合の最中にキャンセルされたら、1 組も戻さない.

    「キャンセルした」と表示しながら組を開いていた、という状態を残さない
    （`stamp_many` の後のキャンセル確認と同じ考え方）。
    """
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    original = server.route

    def hooked(method, path, body, headers):
        result = original(method, path, body, headers)
        if path == "/api/stacks":
            # 一覧を返した直後に、利用者がキャンセルを commit した。
            db.execute("UPDATE job SET status = 'cancelling'")
        return result

    server.route = hooked

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 0
    rows = db.execute("SELECT stack_state FROM upload_record").fetchall()
    assert all(row["stack_state"] == "stacked" for row in rows)
    # **資産の照合の結果は既に書いてある。** ここで降りるのは組の段だけなので、
    # 「N 件確認した」の N は実際に書いた件数のまま（0 に化けない）。
    assert outcome.checked == 2


def test_a_group_whose_member_was_requeued_is_not_seen_as_broken(world):
    """**`stacked` はレコードの state と独立**（`0015`）. 差し戻しで組が崩れて見えない.

    再計算の差し戻しは `complete` → `needs_recheck` を動かすが、その資産を送った
    という事実は変わらない。数える集合から外すと、こちらの集合が相手の真部分集合に
    なって毎回「崩れている」と読み、戻した先の第 2 パスが「相方がまだ `complete` で
    ない」で見送りに落とす —— Immich では組んだままなのに画面が「見送り」と言う。
    """
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    server.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "asset-2"]}
    # `0004` の CHECK に当たらないよう、claim は NULL のままにする。
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE remote_asset_id = 'asset-2'")

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 0
    rows = db.execute("SELECT stack_state FROM upload_record").fetchall()
    assert all(row["stack_state"] == "stacked" for row in rows)


def a_vanished_record(world_tuple):
    """相手に無い資産を指す `complete` の行を 1 つ足す（組には入れない）."""
    server, _, _, destination_id, db = world_tuple
    profile = ProfileRegistry(db).current("dji-osmo")
    third = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/C.MP4",
        sha1=f"{3:040d}",
    )
    revision_id = db.execute("SELECT destination_revision_id FROM upload_record").fetchone()[
        "destination_revision_id"
    ]
    record_id = new_id()
    db.execute(
        "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
        " selection_rule, origin, checksum, remote_asset_id, remote_is_trashed,"
        " destination_revision_id, created_at, updated_at)"
        " VALUES (?, ?, 1, ?, 'complete', 'default', 'created_by_us', ?, 'asset-3', 0, ?, ?, ?)",
        (record_id, destination_id, third, f"{3:040d}", revision_id, now_iso(), now_iso()),
    )
    return record_id


def messages_of(db, job_id):
    return [
        row["message"]
        for row in db.execute(
            "SELECT message FROM job_event WHERE job_id = ? ORDER BY seq", (job_id,)
        )
    ]


def test_a_failing_stack_listing_still_reports_the_invalidation(world):
    """**組の照合が落ちても、無効化の報告は先に出る。**

    無効化は既に commit されている。相手側の失敗をそのまま外へ上げると、
    利用者に残るのは「再確認が失敗しました」だけで、**写真が黙って未送信へ
    戻ったこと**を作業の履歴から辿れない。
    """
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    server.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "asset-2"]}
    vanished = a_vanished_record(world)
    original = server.route

    def fail_the_stack_listing(method, path, body, headers):
        result = original(method, path, body, headers)
        if path == "/api/assets/bulk-upload-check":
            # 続く `GET /api/stacks` だけを落とす（照合そのものは通す）。
            server.fail_next = 1
        return result

    server.route = fail_the_stack_listing

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.vanished == 1
    assert outcome.checked == 3
    assert outcome.unstacked == 0
    assert (
        db.execute(
            "SELECT invalidated_reason FROM upload_record WHERE id = ?", (vanished,)
        ).fetchone()["invalidated_reason"]
        == "remote_missing"
    )
    messages = messages_of(db, ctx.job_id)
    # **順序も固定する。** 組の段を無効化の報告より前に置くと、その失敗で
    # 警告ごと消える形に戻ってしまう。
    assert messages.index(
        "リモートに存在しないので、まだ送っていないものに戻した"
    ) < messages.index("組の照合ができなかった")


def test_a_failing_stack_listing_does_not_leak_the_peers_words(world):
    """**相手由来の文言も、こちらの method / path / 状態コードも混ぜない**（§13）."""
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    original = server.route

    def fail_the_stack_listing(method, path, body, headers):
        result = original(method, path, body, headers)
        if path == "/api/assets/bulk-upload-check":
            server.fail_next = 1
        return result

    server.route = fail_the_stack_listing

    rechecker.run(ctx, destination_id)

    joined = " ".join(messages_of(db, ctx.job_id))
    assert "unavailable" not in joined
    assert "503" not in joined
    assert "/api/stacks" not in joined


def test_a_lease_lost_after_the_write_still_reports_what_was_written(world, monkeypatch):
    """**書いた後にリースを失っても、`checked` は実際に書いた件数のまま。**

    `stamp_many` は commit 済みなので、ここで 0 と報告すると
    「何も書いていない」と読める記録が残る（実際は無効化まで済んでいる）。
    """
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    server.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "asset-2"]}
    real = UploadRepository.stamp_many

    def expire_after_the_write(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        written = real(self, *args, **kwargs)
        db.execute(
            "UPDATE job SET lease_expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (ctx.job_id,),
        )
        return written

    monkeypatch.setattr(UploadRepository, "stamp_many", expire_after_the_write)

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 2
    assert outcome.unstacked == 0
