import os

import pytest

from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import ClaimLost, UploadRepository

from .test_schema_artifacts import a_media_file
from .test_selection import a_derived, a_group, a_pair

IDENTITY = RemoteIdentity.observed("user-a")


def a_job_row(db, job_id):
    """`upload_record.claim_job_id` は job(id) への外部キー.

    このタスクのテストは job の中身を使わないので、行だけ用意する。
    """
    from mediaferry.clock import now_iso

    db.execute(
        "INSERT INTO job (id, type, status, params_json, created_at)"
        " VALUES (?, 'upload', 'running', '{}', ?)",
        (job_id, now_iso()),
    )
    return job_id


@pytest.fixture
def world(db):
    for job_id in ("job-1", "job-2"):
        a_job_row(db, job_id)
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home",
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-1",
        identity=IDENTITY,
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    return profile, destinations, destination_id, uploads


def a_pending(db, world, **over):
    profile, _, destination_id, uploads = world
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id), **over)
    uploads.create_pairs([media_id], [destination_id])
    return db.execute("SELECT * FROM upload_record WHERE media_file_id = ?", (media_id,)).fetchone()


def test_claiming_takes_ownership_and_records_the_revision(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    revision = destinations.current(destination_id)

    row = uploads.claim_next(destination_id, job_id="job-1", token="tok-1")

    assert row["state"] == "checking"
    assert row["claim_job_id"] == "job-1"
    assert row["destination_revision_id"] == revision["id"]
    assert row["claim_expires_at"] is not None


def test_a_second_claim_gets_nothing(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    assert uploads.claim_next(destination_id, "job-1", "tok-1") is not None
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is None


def test_an_abandoned_claim_is_recovered_by_the_reconciler_not_by_a_takeover(db, world):
    """**期限切れの横取りは起こらない契約にする。**

    `0004` の CHECK は「claim の 3 欄はすべて NULL かすべて非 NULL」かつ
    「進行中の状態なら claim を持つ」と定めている。つまり `pending` /
    `needs_recheck` の行に期限だけを残すことはできず、進行中の行は
    `claim_next` の対象外。**放置された claim を回収するのは起動時の
    reconciliation だけ**（Task 12）。`claim_next` の
    `claim_expires_at < now` は §8 の SQL をそのまま写した保険で、
    この CHECK が生きている限り到達しない。
    """
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    uploads.claim_next(destination_id, "job-1", "tok-1")
    # 期限を過去にしても、進行中の行は claim_next の対象にならない。
    db.execute(
        "UPDATE upload_record SET claim_expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", record["id"]),
    )
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is None

    assert uploads.release_interrupted() == 1
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is not None


def test_a_record_from_another_epoch_is_not_claimed(db, world):
    """epoch が違う記録は、無効化されていなくても claim しない.

    `add_revision` は旧 epoch の未完了レコードを無効化する（§8）ので、
    **その無効化を外してから**確かめる。外さないと `invalidated_at` の条件で
    弾かれて、epoch の条件を一度も通らない。
    """
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    # 宛先を別アカウントへ向け替える（epoch が進む）。
    destinations.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-2",
        identity=RemoteIdentity.observed("user-b"),
    )
    db.execute("UPDATE upload_record SET invalidated_at = NULL, invalidated_reason = NULL")

    assert uploads.claim_next(destination_id, "job-1", "tok-1") is None


def test_an_invalidated_record_is_not_claimed(db, world):
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'x' WHERE id = ?",
        ("2026-08-17T00:00:00+00:00", record["id"]),
    )
    assert uploads.claim_next(destination_id, "job-1", "tok-1") is None


def test_needs_recheck_is_claimable(db, world):
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE id = ?", (record["id"],))
    assert uploads.claim_next(destination_id, "job-1", "tok-1") is not None


def test_a_missing_file_fails_the_eligibility_check(db, world):
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute(
        "UPDATE media_file SET missing_at = ? WHERE id = ?",
        ("2026-08-17T00:00:00+00:00", record["media_file_id"]),
    )
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    assert uploads.check_eligibility(claimed) is not None


def test_a_group_that_stopped_failing_invalidates_its_member(db, world):
    """`failed_group_member` の根拠は claim 時に「今も」成立している必要がある."""
    profile, destinations, destination_id, uploads = world
    members = a_pair(db, profile)
    group_id = a_group(db, profile, members, status="failed", verification=None)
    uploads.create_pairs([members[0][0]], [destination_id])
    db.execute("UPDATE merge_group SET status = 'merged' WHERE id = ?", (group_id,))

    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    assert uploads.check_eligibility(claimed) is not None


def test_an_adopted_derived_stays_eligible(db, world):
    profile, destinations, destination_id, uploads = world
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id, verification='{"passed": false}')
    uploads.create_pairs([output_id], [destination_id])

    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    assert uploads.check_eligibility(claimed) is None


def test_a_disabled_destination_fails_the_eligibility_check(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    destinations.set_enabled(destination_id, False)
    assert uploads.check_eligibility(claimed) is not None


def test_refusing_invalidates_and_releases(db, world):
    _, destinations, destination_id, uploads = world
    claimed = None
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.refuse(claimed["id"], "tok-1", "生成元のグループが変わった")

    row = uploads.get(claimed["id"])
    assert row["invalidated_at"] is not None
    assert row["invalidated_reason"] == "生成元のグループが変わった"
    assert row["claim_job_id"] is None


def test_refusing_survives_the_state_check(db, world):
    """`refuse` は state も pending へ戻す. 進行中のまま claim を外せない."""
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.refuse(claimed["id"], "tok-1", "ファイルが見つからない")

    row = uploads.get(claimed["id"])
    assert row["state"] == "pending"
    assert row["invalidated_at"] is not None
    # 無効化されているので、次の claim では拾われない。
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is None


def test_a_cancelled_job_cannot_perform_a_side_effect(db, world):
    """`extend_lease` は cancelling でも延ばす. `assert_lease` は通さない."""
    from mediaferry.db.jobs import JobStore

    _, destinations, destination_id, uploads = world
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    from mediaferry.db.jobs import LeaseLost

    with pytest.raises(LeaseLost):
        uploads.prepare_side_effect(ctx, claimed["id"], "checking")


def test_an_invalidated_record_cannot_perform_a_side_effect(db, world):
    from mediaferry.db.jobs import JobStore

    _, destinations, destination_id, uploads = world
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'グループが変わった'"
        " WHERE id = ?",
        ("2026-08-17T00:00:00+00:00", claimed["id"]),
    )

    with pytest.raises(ClaimLost):
        uploads.prepare_side_effect(ctx, claimed["id"], "checking")


def test_an_expired_claim_cannot_commit_a_side_effect(db, world):
    from mediaferry.db.jobs import JobStore

    _, destinations, destination_id, uploads = world
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
    db.execute(
        "UPDATE upload_record SET claim_expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", claimed["id"]),
    )

    # 送信は終わったが、書き戻す資格はもう無い。
    with pytest.raises(ClaimLost):
        uploads.advance(
            claimed["id"],
            ctx.lease_token,
            "asset_known",
            expect_state="checking",
            remote_asset_id="asset-1",
        )


def test_a_state_that_moved_under_us_stops_the_commit(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    uploads.advance(claimed["id"], "tok-1", "uploading", expect_state="checking")

    # 期待した状態ではない（誰かが動かした）。
    with pytest.raises(ClaimLost):
        uploads.advance(claimed["id"], "tok-1", "asset_known", expect_state="checking")


def test_advancing_keeps_the_claim(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.advance(claimed["id"], "tok-1", "uploading", expect_state="checking")

    row = uploads.get(claimed["id"])
    assert row["state"] == "uploading"
    assert row["claim_job_id"] == "job-1"


def test_a_stale_token_cannot_move_the_record(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    with pytest.raises(ClaimLost):
        uploads.advance(claimed["id"], "tok-old", "uploading", expect_state="checking")
    assert uploads.get(claimed["id"])["state"] == "checking"


def test_finishing_clears_the_claim(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.advance(
        claimed["id"], "tok-1", "asset_known", expect_state="checking", remote_asset_id="asset-1"
    )
    uploads.finish(claimed["id"], "tok-1", "complete", expect_state="asset_known")

    row = uploads.get(claimed["id"])
    assert row["state"] == "complete"
    assert (row["claim_job_id"], row["claim_token"], row["claim_expires_at"]) == (None, None, None)
    # どの設定へ送ったかは残す。
    assert row["destination_revision_id"] is not None


def test_releasing_puts_it_back_for_a_recheck(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.release_to(claimed["id"], "tok-1", "needs_recheck")

    row = uploads.get(claimed["id"])
    assert row["state"] == "needs_recheck"
    assert row["claim_job_id"] is None


def test_the_claim_can_be_extended(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1", 1)
    before = uploads.get(claimed["id"])["claim_expires_at"]

    uploads.extend_claim(claimed["id"], "tok-1", 3600)

    assert uploads.get(claimed["id"])["claim_expires_at"] > before


def test_invalidating_a_group_hits_only_unfinished_records(db, world):
    """**同じグループの `complete` は残す。** 送信済みの履歴を無効化しない."""
    profile, destinations, destination_id, uploads = world
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    uploads.create_pairs([output_id], [destination_id])
    sent = db.execute("SELECT id FROM upload_record").fetchone()["id"]
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ? WHERE id = ?",
        (destinations.current(destination_id)["id"], sent),
    )
    # 同じグループに、まだ送っていない pair をもう 1 つ作る（別の宛先へ）。
    other = destinations.create(
        name="family",
        base_url="http://family.invalid:2283",
        public_url=None,
        secret="key-1",
        identity=IDENTITY,
    )
    uploads.create_pairs([output_id], [other])

    assert uploads.invalidate_for_group(group_id, "グループが変わった") == 1

    assert uploads.get(sent)["invalidated_at"] is None


def test_advancing_the_epoch_invalidates_the_queued_records(db, world):
    """**`add_revision` が同じトランザクションで破棄する**（§8）."""
    _, destinations, destination_id, uploads = world
    a_pending(db, world)

    destinations.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-2",
        identity=RemoteIdentity.observed("user-b"),
    )

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["invalidated_at"] is not None
    assert row["invalidated_reason"]


def test_the_startup_sweep_catches_what_the_edit_missed(db, world):
    """編集の直後に落ちた場合に備えて、起動時にも同じ掃除をする（Task 12）."""
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    destinations.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-2",
        identity=RemoteIdentity.observed("user-b"),
    )
    epoch = destinations.current(destination_id)["target_epoch"]
    # 破棄が走る前に落ちた状態を作る。
    db.execute("UPDATE upload_record SET invalidated_at = NULL, invalidated_reason = NULL")

    assert uploads.invalidate_old_epoch(destination_id, epoch, "向き先が変わった") == 1

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["invalidated_reason"] == "向き先が変わった"


def test_records_of_the_current_epoch_are_left_alone(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    epoch = destinations.current(destination_id)["target_epoch"]
    assert uploads.invalidate_old_epoch(destination_id, epoch, "x") == 0


def test_a_completed_record_from_an_old_epoch_stays_as_history(db, world):
    """旧 epoch の記録は監査履歴として残す（§8）."""
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ? WHERE id = ?",
        (destinations.current(destination_id)["id"], record["id"]),
    )
    destinations.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-2",
        identity=RemoteIdentity.observed("user-b"),
    )
    epoch = destinations.current(destination_id)["target_epoch"]

    assert uploads.invalidate_old_epoch(destination_id, epoch, "向き先が変わった") == 0
    assert uploads.get(record["id"])["invalidated_at"] is None


def test_refusing_an_already_invalidated_record_keeps_the_first_reason(db, world):
    """**最初の無効化の理由と時刻を残す。**

    上書きすると、監査で見えるのが「無効化されている: 元の理由」という
    二次的な文言になり、いつ何が起きたのかが読めなくなる。
    """
    _, _, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    # claim を持ったまま、別の経路（グループの構成変更）で無効化された状態。
    db.execute(
        "UPDATE upload_record SET invalidated_at = '2026-08-18T00:00:00+00:00',"
        " invalidated_reason = 'グループの構成が変わった' WHERE id = ?",
        (claimed["id"],),
    )
    first = db.execute("SELECT invalidated_at, invalidated_reason FROM upload_record").fetchone()

    uploads.refuse(claimed["id"], "tok-1", "無効化されている: グループの構成が変わった")

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["invalidated_reason"] == first["invalidated_reason"]
    assert row["invalidated_at"] == first["invalidated_at"]
    # claim は落ちている（所有したまま残さない）。
    assert row["claim_token"] is None
    assert row["state"] == "pending"
