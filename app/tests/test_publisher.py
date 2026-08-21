import errno
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import time
from datetime import datetime

import pytest

from mediaferry.adapters.ffprobe import MediaProbe, ProbeResult
from mediaferry.adapters.publisher import (
    ArtifactPublisher,
    ArtifactRequest,
    HashingWriter,
    PublishAborted,
    PublishCancelled,
    PublishInterrupted,
    StagingLost,
    _with_lease_pulse,
)
from mediaferry.core.timestamps import CapturedAt
from mediaferry.db.jobs import JobStore, LeaseLost
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_merge_group, a_source_entry
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
    # 取り込み時は「算出に使った版」と「取り込みに使った版」が一致する（`0011`）。
    # ずれるのは再計算を通った後だけで、画面はそれを手がかりにする。
    assert row["captured_at_revision_id"] == row["profile_revision_id"]


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
    # staged より手前なので、書きかけはその場で捨てる
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 0
    assert list((data_root / "staging").rglob("*")) == [data_root / "staging" / ctx.job_id]


def test_a_failed_copy_discards_its_staging_file(setup, db, data_root):
    """カードが抜けた場合（手動チェックリスト #5）.

    起動時の reconciliation は同じものを捨てるが、**起動するまで消えない**。
    アプリを動かし続ける限り、最大で分割 1 本ぶん（DJI なら 16 GiB）が
    利用者から見えないまま残る（`GET /orphans` にも出ない）。
    """
    publisher, ctx, profile, volume_id = setup

    def write(writer):
        writer.write(b"half")
        raise OSError(errno.EIO, "Input/output error")

    with pytest.raises(OSError, match="Input/output"):
        publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write)
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 0
    assert [p for p in (data_root / "staging").rglob("*") if p.is_file()] == []


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
    # **staged 以降は捨てない。** 実体は検証済みで、reconciliation が公開を完遂する。
    row = db.execute("SELECT state, staging_rel_path FROM artifact_staging").fetchone()
    assert row["state"] == "staged"
    assert (data_root / row["staging_rel_path"]).exists()


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


def test_the_collision_suffix_is_the_wall_clock_of_the_resolved_timezone(setup, db, data_root):
    """接尾辞は「カード上の壁時計」. mtime は真の瞬間なので TZ で描画する.

    UTC で描画すると、`captured_at` の壁時計と接尾辞がオフセットぶんずれる
    （`docs/history/hardware-verification.md` の 11 番）。
    """
    publisher, ctx, profile, volume_id = setup
    # A.MP4 を別内容で占有させ、2 本目を別名へ追いやる
    publisher.publish(ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"XX"))
    got = publisher.publish(
        ctx, a_request(profile, a_source_entry(db, volume_id)), write_payload(b"YY")
    )
    # mtime 1_700_000_000 秒 = 2023-11-14T22:13:20+00:00 = 東京の 11/15 07:13:20
    assert got.rel_path == "library/dji-osmo/DCIM/A_20231115071320.MP4"


def test_a_captured_at_without_a_timezone_stamps_in_utc(setup, db, data_root):
    """`timezone_policy: none` は壁時計を UTC として持つので、接尾辞もそれに合わせる."""
    publisher, ctx, profile, volume_id = setup
    captured = CapturedAt(
        at=datetime.fromisoformat("2023-11-14T22:13:20+00:00"),
        source="mtime",
        tz=None,
        note=None,
    )
    publisher.publish(
        ctx,
        a_request(profile, a_source_entry(db, volume_id), captured=captured),
        write_payload(b"XX"),
    )
    got = publisher.publish(
        ctx,
        a_request(profile, a_source_entry(db, volume_id), captured=captured),
        write_payload(b"YY"),
    )
    assert got.rel_path == "library/dji-osmo/DCIM/A_20231114221320.MP4"


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


def a_prepared(data_root, ctx, payload=b"merged-bytes"):
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True, exist_ok=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(payload)
    return prepared


def a_merge_request(profile, group_id, **over):
    return a_request(
        profile,
        None,
        kind="merge",
        role="derived",
        desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
        merge_group_id=group_id,
        **over,
    )


def test_a_prepared_file_is_published_without_being_rewritten(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    prepared = a_prepared(data_root, ctx)

    published = publisher.publish_prepared(ctx, a_merge_request(profile, group_id), prepared)

    final = data_root / "derived/dji-osmo/DCIM/MERGED.MP4"
    assert final.read_bytes() == b"merged-bytes"
    assert published.sha1 == hashlib.sha1(b"merged-bytes", usedforsecurity=False).hexdigest()
    assert published.size_bytes == len(b"merged-bytes")
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (published.media_file_id,)).fetchone()
    assert row["role"] == "derived"
    assert (
        db.execute(
            "SELECT output_media_file_id FROM merge_group WHERE id = ?", (group_id,)
        ).fetchone()[0]
        == published.media_file_id
    )


def test_the_published_file_survives_the_work_directory_being_cleaned(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    prepared = a_prepared(data_root, ctx)
    publisher.publish_prepared(ctx, a_merge_request(profile, group_id), prepared)

    shutil.rmtree(data_root / "work" / ctx.job_id)

    assert (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").read_bytes() == b"merged-bytes"


def test_the_prepared_file_is_linked_not_copied(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    prepared = a_prepared(data_root, ctx)
    publisher.publish_prepared(ctx, a_merge_request(profile, group_id), prepared)
    final = data_root / "derived/dji-osmo/DCIM/MERGED.MP4"
    assert final.stat().st_ino == prepared.stat().st_ino


def test_publishing_a_prepared_file_leaves_no_staging_file(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    publisher.publish_prepared(ctx, a_merge_request(profile, group_id), a_prepared(data_root, ctx))
    assert [p for p in (data_root / "staging").rglob("*") if p.is_file()] == []


def test_the_prepared_file_gets_the_requested_mtime(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    publisher.publish_prepared(
        ctx,
        a_merge_request(profile, group_id, mtime_ns=1_600_000_000_000_000_000),
        a_prepared(data_root, ctx),
    )
    stat = (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").stat()
    assert stat.st_mtime_ns == 1_600_000_000_000_000_000


def test_a_missing_prepared_file_leaves_nothing_durable(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    with pytest.raises(OSError):
        publisher.publish_prepared(
            ctx,
            a_merge_request(profile, group_id),
            data_root / "work" / ctx.job_id / "missing.MP4",
        )
    # 行も実体も残さない。次の起動を待たずにその場で捨てる。
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0


def test_the_hash_scan_pulses_the_lease(setup, data_root, db, monkeypatch):
    """リースより長い走査でも、手順 7 で失効しない."""
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    # 2 chunk 以上にして、走査の途中で打つ機会を作る。
    prepared = a_prepared(data_root, ctx, b"x" * (4 * 1024 * 1024 + 1))
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0)
    beats = []
    monkeypatch.setattr(ctx, "heartbeat", lambda: beats.append(1))

    publisher.publish_prepared(ctx, a_merge_request(profile, group_id), prepared)
    assert beats


def test_a_slow_probe_does_not_lose_the_lease(db, data_root, monkeypatch):
    """ffprobe の timeout はリースと同値. 囲まないと手順 7 で失効する."""
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db, lease_seconds=1)
    store.enqueue("merge", {})
    ctx = store.claim_next()
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")

    class SlowProbe(StubProbe):
        def describe(self, path, extension):
            time.sleep(1.5)  # リース (1 秒) より長い
            return super().describe(path, extension)

    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.2)
    publisher = ArtifactPublisher(db, data_root, SlowProbe())

    published = publisher.publish_prepared(
        ctx, a_merge_request(profile, group_id), a_prepared(data_root, ctx)
    )
    assert (data_root / published.rel_path).exists()


def test_the_lease_pulse_propagates_the_failure(setup):
    """囲んだ処理の例外は、そのまま呼び出し側へ渡す."""
    _, ctx, _, _ = setup

    def boom():
        raise RuntimeError("fsync に失敗した")

    with pytest.raises(RuntimeError, match="fsync"):
        _with_lease_pulse(ctx, boom)


def test_a_cancelled_hash_scan_leaves_nothing_durable(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    prepared = a_prepared(data_root, ctx)
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    with pytest.raises(PublishCancelled):
        publisher.publish_prepared(ctx, a_merge_request(profile, group_id), prepared)
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 0
    assert not (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").exists()
    # **結合の入力は消さない。** staging は work への link なので、取り違えると
    # 中間生成物ごと落ちる。
    assert prepared.exists()


def test_the_lease_pulse_waits_for_the_work_before_raising(setup, monkeypatch):
    """リースを失っても、走っている処理の完了を待ってから送出する.

    待たずに抜けると、残ったスレッドが後から staging へ書き込む。
    """
    _, ctx, _, _ = setup
    finished = []

    def slow():
        time.sleep(0.3)
        finished.append(1)

    def lost():
        raise LeaseLost("リースを失った")

    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(ctx, "heartbeat", lost)
    with pytest.raises(LeaseLost):
        _with_lease_pulse(ctx, slow)
    assert finished


def test_a_slow_fsync_does_not_lose_the_lease(db, data_root, monkeypatch):
    """16 GiB を書いた直後の fsync はリースより長くなりうる（取り込み側の穴）."""
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    entry_id = a_source_entry(db, volume_id)
    store = JobStore(db, lease_seconds=1)
    store.enqueue("import", {})
    ctx = store.claim_next()

    real_fsync = os.fsync

    def slow_fsync(fd):
        # 遅くするのはファイルの fsync だけ。ディレクトリの fsync は
        # メタデータだけなので実際も一瞬で終わる。
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            time.sleep(1.5)  # リース (1 秒) より長い
        real_fsync(fd)

    from mediaferry.adapters import publisher as publisher_module

    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.2)
    monkeypatch.setattr(publisher_module.os, "fsync", slow_fsync)
    publisher = ArtifactPublisher(db, data_root, StubProbe())

    published = publisher.publish(ctx, a_request(profile, entry_id), write_payload(b"payload"))
    assert (data_root / published.rel_path).exists()


def test_a_size_that_disagrees_with_the_disk_is_aborted(setup, data_root, db, monkeypatch):
    """手順 4。実体と記録が食い違ったまま staged へ進ませない."""
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    prepared = a_prepared(data_root, ctx)
    real = publisher._materialise_link  # noqa: SLF001

    def lying(*args, **kwargs):
        size, sha1 = real(*args, **kwargs)
        return size + 1, sha1

    monkeypatch.setattr(publisher, "_materialise_link", lying)
    with pytest.raises(PublishAborted):
        publisher.publish_prepared(ctx, a_merge_request(profile, group_id), prepared)
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM artifact_staging").fetchone()[0] == 0
    assert [f for f in (data_root / "staging").rglob("*") if f.is_file()] == []
    assert prepared.exists()


# ----------------------------------------------------------------------
# 遅延解決（source: exif）—— §9.3 手順 5
#
# `Importer` は `captured` を `publish` の**前**に作るので、その時点で staging は
# 存在しない。ステージ済みのファイルから読むには publisher 側に口が要る。


def a_resolver(recorder, at="2026-01-02T03:04:05+09:00"):
    def resolve(staging_abs):
        recorder.append(staging_abs)
        return CapturedAt(at=datetime.fromisoformat(at), source="exif", tz="Asia/Tokyo", note=None)

    return resolve


def test_a_request_rejects_having_both_captured_and_a_resolver(setup, db):
    """**どちらか一方**をコメントではなく構築時に強制する.

    書いておかないと、後から caller（merge 等）を足したときに、どちらかが
    静かに優先される。
    """
    publisher, ctx, profile, volume_id = setup
    with pytest.raises(ValueError, match="どちらか一方"):
        a_request(profile, a_source_entry(db, volume_id), resolve_captured=a_resolver([]))


def test_a_request_rejects_having_neither(setup, db):
    publisher, ctx, profile, volume_id = setup
    with pytest.raises(ValueError, match="どちらか一方"):
        a_request(profile, a_source_entry(db, volume_id), captured=None)


def test_the_resolver_sees_the_staged_file_and_its_value_is_recorded(setup, db, data_root):
    """解決はステージ済みのファイルに対して行い、結果が `media_file` に載る."""
    publisher, ctx, profile, volume_id = setup
    seen: list = []
    got = publisher.publish(
        ctx,
        a_request(
            profile, a_source_entry(db, volume_id), captured=None, resolve_captured=a_resolver(seen)
        ),
        write_payload(b"payload"),
    )
    assert len(seen) == 1, "解決が呼ばれていない"
    assert seen[0].parent.parent == data_root / "staging", f"ステージ以外を読んでいる: {seen[0]}"
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (got.media_file_id,)).fetchone()
    assert row["captured_at_source"] == "exif"
    assert row["captured_at"].startswith("2026-01-02T03:04:05")


def test_the_resolver_runs_between_verification_and_metadata(setup, db, monkeypatch):
    """**手順 4 の後・手順 5 の中。**

    **「完成したファイルを読んでいる」だけでは足りない。** 実体は手順 2〜3 で
    書き終わっているので、手順 4 の前でも後でも同じサイズが見える。位置を
    確かめるには、手順の記録そのものと突き合わせる。
    """
    from mediaferry.adapters.publisher import STEP_METADATA, STEP_VERIFIED, ArtifactPublisher

    publisher, ctx, profile, volume_id = setup
    trace: list = []
    original = ArtifactPublisher._checkpoint

    def recording(self, step):  # noqa: ANN001, ANN202
        trace.append(step)
        return original(self, step)

    monkeypatch.setattr(ArtifactPublisher, "_checkpoint", recording)

    def resolve(staging_abs):
        trace.append("resolve")
        assert staging_abs.stat().st_size == 10, "書き終わる前に読んでいる"
        return CapturedAt(
            at=datetime.fromisoformat("2026-01-02T03:04:05+09:00"),
            source="exif",
            tz="Asia/Tokyo",
            note=None,
        )

    publisher.publish(
        ctx,
        a_request(profile, a_source_entry(db, volume_id), captured=None, resolve_captured=resolve),
        write_payload(b"0123456789"),
    )
    assert trace.count("resolve") == 1
    at = trace.index("resolve")
    assert trace.index(STEP_VERIFIED) < at < trace.index(STEP_METADATA), (
        f"解決の位置が手順 4〜5 の外にある: {trace}"
    )


def test_the_resolved_value_is_persisted_before_the_file_is_published(setup, db):
    """手順 7 の commit に載る（手順 5 で確定するので載らないほうがおかしい）.

    ここが崩れると、公開済みなのに `captured_at` が既定値のままの行ができる。
    """
    publisher, ctx, profile, volume_id = setup
    got = publisher.publish(
        ctx,
        a_request(
            profile, a_source_entry(db, volume_id), captured=None, resolve_captured=a_resolver([])
        ),
        write_payload(b"x"),
    )
    row = db.execute(
        "SELECT metadata_json, state FROM artifact_staging WHERE source_entry_id IS NOT NULL"
    ).fetchone()
    assert row is not None and row["state"] == "published"
    metadata = json.loads(row["metadata_json"])
    assert metadata["captured_at_source"] == "exif"
    assert metadata["captured_at"].startswith("2026-01-02T03:04:05")
    assert got.media_file_id
