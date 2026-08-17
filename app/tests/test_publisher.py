import hashlib
import json
import sqlite3
from datetime import datetime

import pytest

from mediaferry.adapters.ffprobe import MediaProbe, ProbeResult
from mediaferry.adapters.publisher import (
    ArtifactPublisher,
    ArtifactRequest,
    HashingWriter,
    PublishAborted,
    PublishInterrupted,
    StagingLost,
)
from mediaferry.core.timestamps import CapturedAt
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_source_entry
from .test_schema_sources import a_volume


class StubProbe(MediaProbe):
    def __init__(self, result=None):
        self.result = result or ProbeResult("video", 2.0, "ok")

    def describe(self, path, extension):
        return self.result


@pytest.fixture
def setup(db, data_root):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    publisher = ArtifactPublisher(db, data_root, StubProbe())
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    return publisher, ctx, profile, volume_id


def a_request(profile, entry_id, **over):
    fields = {
        "kind": "import",
        "role": "original",
        "profile_id": profile.profile_id,
        "profile_revision_id": profile.revision_id,
        "desired_rel_path": "library/dji-osmo/DCIM/A.MP4",
        "source_rel_path": "DCIM/A.MP4",
        "extension": "MP4",
        "captured": CapturedAt(
            at=datetime.fromisoformat("2026-08-17T14:30:00+09:00"),
            source="filename",
            tz="Asia/Tokyo",
            note=None,
        ),
        "mtime_ns": 1_700_000_000_000_000_000,
        "source_entry_id": entry_id,
        "merge_group_id": None,
    }
    fields.update(over)
    return ArtifactRequest(**fields)


def write_payload(payload):
    def write(writer):
        writer.write(payload)

    return write


def test_publish_puts_the_file_in_the_library_and_records_it(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    got = publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))

    assert (data_root / "library/dji-osmo/DCIM/A.MP4").read_bytes() == b"payload"
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (got.media_file_id,)).fetchone()
    assert row["rel_path"] == "library/dji-osmo/DCIM/A.MP4"
    assert row["sha1"] == hashlib.sha1(b"payload", usedforsecurity=False).hexdigest()
    assert row["size_bytes"] == 7
    assert row["duration_seconds"] == 2.0
    assert row["probe_state"] == "ok"
    assert row["captured_at"].startswith("2026-08-17T14:30:00")


def test_the_source_entry_is_linked_and_marked_published(setup, db):
    publisher, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    got = publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"x"))
    row = db.execute("SELECT * FROM source_entry WHERE id = ?", (entry_id,)).fetchone()
    assert row["media_file_id"] == got.media_file_id
    assert row["state"] == "published"


def test_the_staging_file_is_gone_and_the_row_is_published(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x"))
    assert list((data_root / "staging").rglob("*")) == [data_root / "staging" / ctx.job_id]
    assert db.execute("SELECT state FROM artifact_staging").fetchone()["state"] == "published"


def test_an_existing_different_file_is_never_overwritten(setup, db, data_root):
    """SD をフォーマットして連番が再利用されたケース. 既存は絶対に動かさない."""
    publisher, ctx, profile, volume_id = setup
    target = data_root / "library/dji-osmo/DCIM/A.MP4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"an older recording")

    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"new payload")
    )

    assert target.read_bytes() == b"an older recording"
    assert got.rel_path != "library/dji-osmo/DCIM/A.MP4"
    assert got.rel_path.startswith("library/dji-osmo/DCIM/A_")
    assert (data_root / got.rel_path).read_bytes() == b"new payload"


def test_the_alternate_name_is_deterministic(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    target = data_root / "library/dji-osmo/DCIM/A.MP4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"older")
    first = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    # 同じ入力を別の source_entry で再公開すると、同じ内容なので同じ行に落ちる
    second = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
    )
    assert first.rel_path == second.rel_path
    assert second.reused_existing is True
    assert first.media_file_id == second.media_file_id


def test_publishing_the_same_content_twice_does_not_duplicate(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    first = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"same")
    )
    second = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"same")
    )
    assert first.media_file_id == second.media_file_id
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 1


def test_a_lost_lease_stops_the_publish_before_it_touches_the_library(setup, db, data_root):
    """キャンセル済みと表示した後に公開されることを防ぐ."""
    publisher, ctx, profile, volume_id = setup
    JobStore(db).finish(ctx.job_id, ctx.lease_token, "cancelled")
    with pytest.raises(PublishAborted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x")
        )
    assert not (data_root / "library/dji-osmo/DCIM/A.MP4").exists()


def test_a_cancel_requested_during_the_write_stops_the_publish(setup, db, data_root):
    """cancelling でも extend_lease が通ってしまうと、この境界が破れる."""
    publisher, ctx, profile, volume_id = setup

    def write(writer):
        writer.write(b"payload")
        JobStore(db).request_cancel(ctx.job_id)

    with pytest.raises(PublishAborted):
        publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write)
    assert not (data_root / "library/dji-osmo/DCIM/A.MP4").exists()
    # writing のまま残るので、次回起動の reconciliation が破棄する
    assert db.execute("SELECT state FROM artifact_staging").fetchone()["state"] == "writing"


def test_a_cancel_cannot_land_between_the_lease_check_and_the_staged_transition(
    setup, db, database, data_root
):
    """確認と遷移が同じ BEGIN IMMEDIATE の中にあることを、書き込みロックで確かめる.

    分けると、その隙間に別接続の cancel が commit でき、「キャンセル済みと
    表示した後に公開される」経路が残る。ここでは確認の直後に別接続から
    cancel を試み、書き込みロックに阻まれることを見る。
    """
    publisher, ctx, profile, volume_id = setup
    other_conn = database.connect()
    other_conn.execute("PRAGMA busy_timeout = 0")
    other = JobStore(other_conn)
    outcome = []

    real_assert = ctx.assert_lease

    def assert_then_try_to_cancel():
        real_assert()
        try:
            other.request_cancel(ctx.job_id)
        except sqlite3.OperationalError:
            outcome.append("blocked")
        else:
            outcome.append("slipped in")

    ctx.assert_lease = assert_then_try_to_cancel
    try:
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    finally:
        other_conn.close()

    assert outcome == ["blocked"]


def test_a_failure_after_staging_is_not_reported_as_an_import_failure(setup, db, data_root):
    """staged 以降は reconciliation が完遂する. 呼び出し元が failed に倒すと二重取り込みになる."""
    publisher, ctx, profile, volume_id = setup
    publisher._checkpoint = _die_after(8)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )


def test_resume_after_the_staging_file_is_gone(setup, db, data_root):
    """手順 10 まで進んで落ちた行は staged のまま. os.link を試すと必ず失敗する."""
    publisher, ctx, profile, volume_id = setup
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    staging_id = db.execute("SELECT id FROM artifact_staging").fetchone()["id"]
    got = publisher.resume(staging_id)
    assert got.rel_path == "library/dji-osmo/DCIM/A.MP4"
    assert (data_root / got.rel_path).read_bytes() == b"payload"
    assert db.execute("SELECT state FROM artifact_staging").fetchone()["state"] == "published"


def test_resume_does_not_retry_names_it_already_rejected(setup, db, data_root, monkeypatch):
    """再開は現在の final_rel_path から続ける.

    先頭へ戻しても落ち着く名前は同じだが、棄却済みの名前を試すたびに
    その既存ファイルの SHA-1 を読み直す。16GiB のカードでは実費になる。
    """
    from mediaferry.adapters import publisher as publisher_module

    publisher, ctx, profile, volume_id = setup
    # A.MP4 を別内容で占有させ、1 本目を別名へ追いやる
    publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"XX"))
    publisher._checkpoint = _die_after(8)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"YY")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001

    row = db.execute("SELECT * FROM artifact_staging WHERE state = 'staged'").fetchone()
    (data_root / row["final_rel_path"]).write_bytes(b"ZZZ")  # 第三者が別内容で占有

    attempts = []
    real_link = publisher_module.os.link
    monkeypatch.setattr(
        publisher_module.os,
        "link",
        lambda src, dst: (attempts.append(str(dst)), real_link(src, dst))[1],
    )
    publisher.resume(row["id"])

    assert not any(a.endswith("/A.MP4") for a in attempts), (
        f"棄却済みの名前を試し直している: {attempts}"
    )


def test_a_staged_row_whose_files_are_all_gone_is_not_silently_dropped(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    publisher._checkpoint = _die_after(10)  # noqa: SLF001
    with pytest.raises(PublishInterrupted):
        publisher.publish(
            ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"payload")
        )
    publisher._checkpoint = lambda step: None  # noqa: SLF001
    (data_root / "library/dji-osmo/DCIM/A.MP4").unlink()

    staging_id = db.execute("SELECT id FROM artifact_staging").fetchone()["id"]
    with pytest.raises(StagingLost):
        publisher.resume(staging_id)
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 1


class _Crash(RuntimeError):
    pass


def _die_after(step):
    def checkpoint(current):
        if current == step:
            raise _Crash(f"step {step}")

    return checkpoint


def test_the_staged_row_carries_everything_needed_to_resume(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    seen = {}

    original = publisher._checkpoint  # noqa: SLF001

    def spy(step):
        if step == 7:
            seen["row"] = dict(db.execute("SELECT * FROM artifact_staging").fetchone())
        original(step)

    publisher._checkpoint = spy  # noqa: SLF001
    publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))

    row = seen["row"]
    assert row["state"] == "staged"
    assert row["final_rel_path"] == "library/dji-osmo/DCIM/A.MP4"
    assert row["expected_size"] == 7
    assert row["content_sha1"]
    assert json.loads(row["metadata_json"])["kind"] == "video"


def test_the_published_file_keeps_the_source_mtime(setup, db, data_root):
    publisher, ctx, profile, volume_id = setup
    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x")
    )
    assert (data_root / got.rel_path).stat().st_mtime_ns == 1_700_000_000_000_000_000


def test_merge_artifacts_use_the_same_protocol(setup, db, data_root):
    from .test_schema_artifacts import a_merge_group

    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    got = publisher.publish(
        ctx,
        a_request(
            profile,
            None,
            kind="merge",
            role="derived",
            desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
            source_rel_path="DCIM/MERGED.MP4",
            source_entry_id=None,
            merge_group_id=group_id,
        ),
        write_payload(b"merged"),
    )
    assert (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").read_bytes() == b"merged"
    output_id = db.execute(
        "SELECT output_media_file_id FROM merge_group WHERE id = ?", (group_id,)
    ).fetchone()[0]
    assert output_id == got.media_file_id


def test_durability_order_is_utime_then_fsync_then_dir_then_staged(setup, db, monkeypatch):
    """os._exit のテストは page cache を失わないので、この順序は測れない.

    mtime を fsync の後に付けると metadata が永続化されず、staging の親を
    fsync しないと「DB は staged、ファイルは無い」になる。
    """
    from mediaferry.adapters import publisher as publisher_module

    calls = []
    monkeypatch.setattr(publisher_module.os, "utime", lambda *a, **k: calls.append("utime"))
    real_fsync = publisher_module.os.fsync
    monkeypatch.setattr(
        publisher_module.os,
        "fsync",
        lambda fd: (calls.append("fsync"), real_fsync(fd))[1],
    )
    monkeypatch.setattr(
        publisher_module, "fsync_dir", lambda path: calls.append(f"fsync_dir:{path.name}")
    )

    publisher, ctx, profile, volume_id = setup
    original = publisher._checkpoint  # noqa: SLF001
    monkeypatch.setattr(
        publisher,
        "_checkpoint",
        lambda step: (calls.append(f"step{step}"), original(step))[1],
    )
    publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"x"))

    upto_staged = calls[: calls.index("step7") + 1]
    assert upto_staged.index("utime") < upto_staged.index("fsync")
    assert upto_staged.index("fsync") < upto_staged.index(f"fsync_dir:{ctx.job_id}")
    assert upto_staged.index(f"fsync_dir:{ctx.job_id}") < upto_staged.index("step7")


def test_the_hashing_writer_matches_hashlib(tmp_path):
    with (tmp_path / "f").open("wb") as f:
        writer = HashingWriter(f)
        writer.write(b"ab")
        writer.write(b"cd")
    assert writer.size == 4
    assert writer.sha1 == hashlib.sha1(b"abcd", usedforsecurity=False).hexdigest()
