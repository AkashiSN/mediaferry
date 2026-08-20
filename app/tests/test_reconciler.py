import pytest

from mediaferry.adapters.publisher import ArtifactPublisher, PublishInterrupted
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.reconcile import Reconciler

from .test_publisher import (
    StubProbe,
    _Crash,
    _die_after,
    a_merge_request,
    a_prepared,
    a_request,
    write_payload,
)
from .test_schema_artifacts import a_media_file, a_merge_group, a_source_entry, a_staging
from .test_schema_sources import a_volume


@pytest.fixture
def world(db, data_root):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    publisher = ArtifactPublisher(db, data_root, StubProbe())
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    return store, publisher, profile, volume_id, Reconciler(db, data_root, publisher, store)


def test_a_writing_row_is_discarded_with_its_temp_file(world, db, data_root):
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    staging = data_root / "staging" / ctx.job_id / "half-written"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"half")
    db.execute(
        "INSERT INTO artifact_staging (id, kind, job_id, lease_token, state, staging_rel_path,"
        " source_entry_id, created_at, updated_at)"
        " VALUES ('s1', 'import', ?, ?, 'writing', ?, ?, '2026-01-01T00:00:00+00:00',"
        " '2026-01-01T00:00:00+00:00')",
        (
            ctx.job_id,
            ctx.lease_token,
            f"staging/{ctx.job_id}/half-written",
            a_source_entry(db, volume_id),
        ),
    )
    report = reconciler.run()
    assert report.discarded == 1
    assert not staging.exists()
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 0


def test_a_staged_row_is_published_from_persisted_facts_alone(world, db, data_root):
    """パスを推測せず、final_rel_path と content_sha1 だけで再開する."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    entry_id = a_source_entry(db, volume_id)
    publisher._checkpoint = _die_after(7)  # noqa: SLF001
    with pytest.raises(_Crash):
        publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    report = reconciler.run()
    assert report.resumed == 1
    assert (data_root / "library/dji-osmo/DCIM/A.MP4").read_bytes() == b"payload"
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 1


def test_a_crash_between_link_and_commit_still_publishes(world, db, data_root):
    """手順 10 まで進んだ行は staged のまま残り、再開すると commit だけをやり直す."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    entry_id = a_source_entry(db, volume_id)
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    reconciler.run()
    assert db.execute("SELECT media_file_id FROM source_entry").fetchone()[0] is not None
    assert (data_root / "library/dji-osmo/DCIM/A.MP4").read_bytes() == b"payload"


def test_a_published_row_without_a_media_file_is_recommitted(world, db, data_root):
    """commit は 1 トランザクションなので通常は起きない. 手で DB を壊した場合の保険."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    db.execute("UPDATE source_entry SET media_file_id = NULL, state = 'importing'")
    db.execute("DELETE FROM media_file WHERE id = ?", (got.media_file_id,))

    report = reconciler.run()
    assert report.recommitted == 1
    assert db.execute("SELECT media_file_id FROM source_entry").fetchone()[0] is not None


def test_orphans_are_reported_and_never_deleted(world, data_root):
    """自動削除するとデータを失う経路になる. 画面に出してユーザの判断に委ねる."""
    *_, reconciler = world
    orphan = data_root / "library/dji-osmo/DCIM/UNKNOWN.MP4"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"who put this here")
    report = reconciler.run()
    assert [o.rel_path for o in report.orphans] == ["library/dji-osmo/DCIM/UNKNOWN.MP4"]
    assert orphan.exists()


def test_a_missing_file_marks_the_record_and_a_restored_one_clears_it(world, db, data_root):
    """一時的に dataset が見えなかっただけで永久に欠損扱いにしない."""
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    path = data_root / got.rel_path
    payload = path.read_bytes()

    path.unlink()
    assert reconciler.run().missing == 1
    missing_at = db.execute(
        "SELECT missing_at FROM media_file WHERE id = ?", (got.media_file_id,)
    ).fetchone()[0]
    assert missing_at is not None

    path.write_bytes(payload)
    assert reconciler.run().restored == 1
    missing_at = db.execute(
        "SELECT missing_at FROM media_file WHERE id = ?", (got.media_file_id,)
    ).fetchone()[0]
    assert missing_at is None


def test_an_unrecoverable_staging_row_is_reported_not_dropped(world, db, data_root):
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001
    (data_root / "library/dji-osmo/DCIM/A.MP4").unlink()

    report = reconciler.run()
    assert len(report.unrecoverable) == 1
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 1


def test_a_row_that_could_not_be_recovered_keeps_its_files_for_the_next_startup(
    world, db, data_root, monkeypatch
):
    """回収に失敗した行は次回も試す。その材料を同じ回で捨てない.

    手順 9（公開先の fsync）で落ちた行は、公開先の実体も staging の一時
    ファイルも残っている。ここで staging のディレクトリを消すと次回は
    回収できず、公開先を孤立ファイルとして報告すると、公開途中のファイルを
    ユーザに「素性の分からないファイル」として見せることになる。
    """
    store, publisher, profile, volume_id, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    publisher._checkpoint = _die_after(9)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    staging_dir = data_root / "staging" / ctx.job_id
    assert list(staging_dir.iterdir())  # 一時ファイルはまだある

    def unavailable(staging_id):
        raise OSError("データセットが一時的に見えない")

    monkeypatch.setattr(publisher, "resume", unavailable)
    report = reconciler.run()

    assert len(report.unrecoverable) == 1
    assert staging_dir.exists()
    assert [o.rel_path for o in report.orphans] == []


def test_stale_job_directories_are_cleaned_but_live_ones_are_kept(world, db, data_root):
    """使用中の可能性があるものを消さない. 所有者のジョブの状態を必ず確かめる."""
    store, publisher, profile, volume_id, reconciler = world
    dead = store.enqueue("import", {})
    ctx = store.claim_next()
    store.finish(dead, ctx.lease_token, "failed")
    (data_root / "staging" / dead).mkdir(parents=True)
    (data_root / "work" / dead).mkdir(parents=True)

    queued = store.enqueue("import", {})
    (data_root / "staging" / queued).mkdir(parents=True)

    report = reconciler.run()
    assert not (data_root / "staging" / dead).exists()
    assert not (data_root / "work" / dead).exists()
    assert (data_root / "staging" / queued).exists()
    assert report.cleaned_dirs == 2


def test_running_jobs_are_marked_interrupted_first(world, db):
    store, *_, reconciler = world
    store.enqueue("import", {})
    ctx = store.claim_next()
    reconciler.run()
    assert store.get(ctx.job_id)["status"] == "interrupted"


def _a_profile(db):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    return (profile.profile_id, profile.revision_id)


def _reconcile(db, data_root):
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository
    from mediaferry.db.uploads import UploadRepository

    publisher = ArtifactPublisher(db, data_root, StubProbe())
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    return Reconciler(
        db, data_root, publisher, JobStore(db), uploads=uploads, destinations=destinations
    ).run()


def test_a_merge_that_reached_the_publish_is_completed(db, data_root):
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="merging")
    output_id = a_media_file(
        db, profile, role="derived", rel_path="derived/dji-osmo/DCIM/MERGED.MP4"
    )
    (data_root / "derived/dji-osmo/DCIM").mkdir(parents=True)
    (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").write_bytes(b"x")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output_id, group_id)
    )

    report = _reconcile(db, data_root)

    assert report.merges_completed == 1
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "merged"
    )


def test_a_merge_that_never_published_is_released_for_a_retry(db, data_root):
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="merging")

    report = _reconcile(db, data_root)

    assert report.merges_released == 1
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "detected"
    )


def test_a_finished_group_is_left_alone(db, data_root):
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="skipped")
    _reconcile(db, data_root)
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "skipped"
    )


def test_a_group_with_an_unrecoverable_staging_is_not_released(db, data_root):
    """`StagingLost` を残したまま再試行できると、履歴が上書きされうる."""
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="merging")
    job_id = JobStore(db).enqueue("merge", {})
    # staged なのに実体が無い（手順 7〜10 の間で停止し、両方が失われた形）。
    a_staging(
        db,
        job_id,
        kind="merge",
        state="staged",
        merge_group_id=group_id,
        final_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
        expected_size=10,
        content_sha1="0" * 40,
        metadata_json="{}",
    )

    report = _reconcile(db, data_root)

    assert report.unrecoverable
    assert report.merges_blocked == 1
    assert report.merges_released == 0
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "merging"
    )


def test_a_merge_is_settled_after_its_staging_is_recovered(db, data_root):
    """決着は `_recover_staging` の後に置く.

    先に置くと、公開まで進んでいたグループが「出力がまだ無い」と見えて
    detected へ戻り、その直後に公開が完遂して出力だけが付く。
    """
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    group_id = a_merge_group(
        db, (profile.profile_id, profile.revision_id), "digest-1", status="merging"
    )
    store = JobStore(db)
    store.enqueue("merge", {})
    ctx = store.claim_next()
    publisher = ArtifactPublisher(db, data_root, StubProbe())
    publisher._checkpoint = _die_after(7)  # noqa: SLF001
    with pytest.raises(_Crash):
        publisher.publish_prepared(
            ctx, a_merge_request(profile, group_id), a_prepared(data_root, ctx)
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    report = Reconciler(db, data_root, publisher, store).run()

    assert report.resumed == 1
    assert report.merges_completed == 1
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "merged"
    )


def _an_upload_record(db, state, **over):
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
    from mediaferry.db.uploads import UploadRepository

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home",
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="k",
        identity=RemoteIdentity.observed("user-a"),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    uploads.create_pairs([media_id], [destination_id])
    # claim_job_id は job(id) への外部キー。中断したジョブの行を用意する。
    job_id = JobStore(db).enqueue("upload", {"destination_id": destination_id})
    fields = {
        "state": state,
        "claim_job_id": job_id,
        "claim_token": "tok-old",
        "claim_expires_at": "2999-01-01T00:00:00+00:00",
        "destination_revision_id": destinations.current(destination_id)["id"],
    }
    fields.update(over)
    assignment = ", ".join(f"{name} = ?" for name in fields)
    db.execute(f"UPDATE upload_record SET {assignment}", tuple(fields.values()))  # noqa: S608
    return db.execute("SELECT * FROM upload_record").fetchone()


def test_an_interrupted_upload_is_released_for_a_recheck(db, data_root):
    record = _an_upload_record(db, "uploading")

    report = _reconcile(db, data_root)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record["id"],)).fetchone()
    assert report.uploads_released == 1
    # サーバ側の成否が不明なので pending ではない。
    assert row["state"] == "needs_recheck"
    assert (row["claim_job_id"], row["claim_token"], row["claim_expires_at"]) == (None, None, None)


def test_a_finished_upload_is_left_alone(db, data_root):
    record = _an_upload_record(
        db, "complete", claim_job_id=None, claim_token=None, claim_expires_at=None
    )

    report = _reconcile(db, data_root)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record["id"],)).fetchone()
    assert report.uploads_released == 0
    assert row["state"] == "complete"


def test_a_waiting_upload_keeps_waiting(db, data_root):
    record = _an_upload_record(
        db,
        "awaiting_datetime_approval",
        claim_job_id=None,
        claim_token=None,
        claim_expires_at=None,
    )
    _reconcile(db, data_root)
    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record["id"],)).fetchone()
    assert row["state"] == "awaiting_datetime_approval"


def test_a_record_whose_grounds_are_gone_is_invalidated(db, data_root):
    """derived の生成元が現行と一致しなくなったレコードを止める."""
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
    from mediaferry.db.uploads import UploadRepository

    from .test_selection import a_derived, a_group, a_pair

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home",
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="k",
        identity=RemoteIdentity.observed("user-a"),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    uploads.create_pairs([output_id], [destination_id])
    # 構成ファイルが差し替わって digest が合わなくなった。
    db.execute("UPDATE media_file SET sha1 = 'edited' WHERE id = ?", (members[0][0],))

    report = _reconcile(db, data_root)

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert report.uploads_invalidated == 1
    assert row["invalidated_at"] is not None
    assert group_id in row["invalidated_reason"] or "グループ" in row["invalidated_reason"]


def test_a_healthy_record_is_not_invalidated(db, data_root):
    _an_upload_record(db, "pending", claim_job_id=None, claim_token=None, claim_expires_at=None)

    report = _reconcile(db, data_root)

    assert report.uploads_invalidated == 0
    assert db.execute("SELECT invalidated_at FROM upload_record").fetchone()[0] is None


def test_startup_purges_superseded_keys_and_sweeps_old_epochs(db, data_root):
    """編集の直後に落ちても、次の起動で均される."""
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
    from mediaferry.db.uploads import UploadRepository

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home",
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="k1",
        identity=RemoteIdentity.observed("user-a"),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    uploads.create_pairs([media_id], [destination_id])
    old_credential = destinations.current(destination_id)["credential_id"]
    # 編集はしたが、その後の後始末が走る前に落ちた状態を作る。
    destinations.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="k2",
        identity=RemoteIdentity.observed("user-b"),
    )
    db.execute("UPDATE upload_record SET invalidated_at = NULL, invalidated_reason = NULL")

    report = Reconciler(
        db,
        data_root,
        ArtifactPublisher(db, data_root, StubProbe()),
        JobStore(db),
        uploads=uploads,
        destinations=destinations,
    ).run()

    assert report.uploads_invalidated == 1
    assert report.credentials_purged == 1
    assert (
        db.execute(
            "SELECT secret_encrypted FROM destination_credential WHERE id = ?", (old_credential,)
        ).fetchone()[0]
        is None
    )


def test_the_reconciler_refuses_a_half_wired_pair(db, data_root):
    """片方だけ渡すと、回収がすべて黙って skip される（気づけない）."""
    import pytest

    from mediaferry.db.uploads import UploadRepository

    with pytest.raises(ValueError):
        Reconciler(
            db,
            data_root,
            ArtifactPublisher(db, data_root, StubProbe()),
            JobStore(db),
            uploads=UploadRepository(db, ProfileRegistry(db), None),
        )


def test_a_completed_record_keeps_its_history_even_if_the_group_changed(db, data_root):
    """送信済みの履歴は無効化しない（監査に要る）."""
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
    from mediaferry.db.uploads import UploadRepository

    from .test_selection import a_derived, a_group, a_pair

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home",
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="k",
        identity=RemoteIdentity.observed("user-a"),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id)
    uploads.create_pairs([output_id], [destination_id])
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ?",
        (destinations.current(destination_id)["id"],),
    )
    # 構成ファイルが差し替わって digest が合わなくなった。
    db.execute("UPDATE media_file SET sha1 = 'edited' WHERE id = ?", (members[0][0],))

    report = _reconcile(db, data_root)

    assert report.uploads_invalidated == 0
    assert db.execute("SELECT invalidated_at FROM upload_record").fetchone()[0] is None


def test_the_startup_reconciliation_leaves_a_record_in_the_log(
    db, data_root, broker_factory, monkeypatch, caplog
):
    """**黙って消さない.** 3 GiB を捨てた事実がどこにも残らないと、消えた容量の説明が付かない.

    `GET /orphans` に出るのは孤立だけで、破棄した staging の件数はどこにも出ない。
    """
    import logging

    from fastapi.testclient import TestClient

    from mediaferry.api.app import create_app

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    staging = data_root / "staging" / ctx.job_id / "half-written"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"half")
    db.execute(
        "INSERT INTO artifact_staging (id, kind, job_id, lease_token, state, staging_rel_path,"
        " source_entry_id, created_at, updated_at)"
        " VALUES ('s1', 'import', ?, ?, 'writing', ?, ?, '2026-01-01T00:00:00+00:00',"
        " '2026-01-01T00:00:00+00:00')",
        (
            ctx.job_id,
            ctx.lease_token,
            f"staging/{ctx.job_id}/half-written",
            a_source_entry(db, volume_id),
        ),
    )
    store.finish(ctx.job_id, ctx.lease_token, "failed")

    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    with (
        caplog.at_level(logging.INFO, logger="mediaferry.api.app"),
        TestClient(create_app(broker_factory=broker_factory), base_url="http://127.0.0.1:8080"),
    ):
        pass
    assert "discarded" in caplog.text
    assert not staging.exists()
