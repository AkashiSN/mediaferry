import pytest

from mediaferry.adapters.publisher import ArtifactPublisher, PublishInterrupted
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.reconcile import Reconciler

from .test_publisher import StubProbe, _Crash, _die_after, a_request, write_payload
from .test_schema_artifacts import a_source_entry
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
