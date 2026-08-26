import json
import shutil
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from mediaferry.adapters.ffmpeg import MergeCancelled, MergeRunner
from mediaferry.adapters.ffprobe import MediaProbe
from mediaferry.adapters.publisher import ArtifactPublisher, PublishInterrupted
from mediaferry.core.merge.grouping import GroupCandidate, MergePart
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import GroupNotClaimable, MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.merger import (
    FREE_SPACE_MARGIN,
    MergeInputsChanged,
    Merger,
    NotEnoughSpace,
    _recording_end_ns,
)

from .test_schema_artifacts import a_media_file

BASE = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
NAMES = ["DJI_20260817143000_0001_D.MP4", "DJI_20260817143000_0002_D.MP4"]


def make_clip(path, seconds=2):
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def world(db, data_root):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)

    members = []
    for index, name in enumerate(NAMES):
        path = make_clip(directory / name)
        rel = f"library/dji-osmo/DCIM/DJI_001/{name}"
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=rel,
            sha1=f"{index:040d}",
            size_bytes=path.stat().st_size,
            duration_seconds=2.0,
            captured_at=(BASE + timedelta(seconds=2 * index)).isoformat(),
        )
        members.append(
            MergePart(
                media_file_id=media_id,
                rel_path=rel,
                sha1=f"{index:040d}",
                captured_at=BASE + timedelta(seconds=2 * index),
                duration_seconds=2.0,
                size_bytes=path.stat().st_size,
                probe_state="ok",
            )
        )

    repo = MergeRepository(db)
    group_id = repo.save_detected(
        profile, GroupCandidate(members=tuple(members), gaps=(0.0,)), "digest-1"
    )
    store = JobStore(db)
    store.enqueue("merge", {})
    ctx = store.claim_next()
    merger = Merger(
        db,
        repo,
        ArtifactPublisher(db, data_root, MediaProbe()),
        MergeRunner(),
        MediaProbe(),
        data_root,
    )
    return merger, ctx, profile, repo, group_id


def test_a_group_is_merged_verified_and_published(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    result = merger.run(ctx, group_id, "digest-1", profile)

    assert result.route == "concat"
    assert result.rel_path == (
        "derived/dji-osmo/DCIM/DJI_001/DJI_20260817143000_0001-0002_MERGED.MP4"
    )
    assert (data_root / result.rel_path).exists()
    row = repo.get(group_id)
    assert row["status"] == "merged"
    assert row["output_media_file_id"] == result.media_file_id
    assert row["verification_json"] is not None
    assert row["tool_version"].startswith("ffmpeg version")
    media = db.execute("SELECT * FROM media_file WHERE id = ?", (result.media_file_id,)).fetchone()
    assert media["role"] == "derived"
    assert 3.6 < media["duration_seconds"] < 4.4


def test_the_output_mtime_is_the_recording_end_in_wall_clock(world, data_root):
    merger, ctx, profile, _, group_id = world
    result = merger.run(ctx, group_id, "digest-1", profile)
    # 最後のパートの開始（壁時計 14:30:02）+ duration 2 秒 = 14:30:04。
    expected = datetime(2026, 8, 17, 14, 30, 4, tzinfo=UTC).timestamp()
    assert (data_root / result.rel_path).stat().st_mtime == pytest.approx(expected, abs=1)


def test_the_offset_in_captured_at_decides_the_output_mtime(world, data_root, db):
    """`captured_at` は真の瞬間なので、オフセットを解釈して epoch にする.

    UTC と読み替えると、取り込んだファイル（mtime が真の瞬間）と派生物とで
    9 時間ずれる（`docs/history/hardware-verification.md` の 11 番）。
    """
    merger, ctx, profile, _, group_id = world
    db.execute(
        "UPDATE media_file SET captured_at = replace(captured_at, '+00:00', '+09:00')"
        " WHERE role = 'original'"
    )
    result = merger.run(ctx, group_id, "digest-1", profile)
    expected = datetime(2026, 8, 17, 14, 30, 4, tzinfo=timezone(timedelta(hours=9))).timestamp()
    assert (data_root / result.rel_path).stat().st_mtime == pytest.approx(expected, abs=1)


def test_a_wall_clock_profile_keeps_the_output_mtime_in_the_old_representation(
    world, data_root, db
):
    """`wall_clock` の媒体では、取り込みの mtime が「壁時計を UTC と読んだ epoch」.

    派生物だけ真の瞬間にすると、同じライブラリの中で `library/` と `derived/` の
    epoch がオフセットぶんずれる（この案件がまさにそれで壊れていた）。
    """
    merger, ctx, profile, _, group_id = world
    # ビルトインは編集できない（§6）ので、同じ版を指したまま定義だけ差し替える。
    wall_clock_profile = replace(
        profile,
        definition=replace(
            profile.definition,
            timestamp=replace(profile.definition.timestamp, mtime_semantics="wall_clock"),
        ),
    )
    db.execute(
        "UPDATE media_file SET captured_at = replace(captured_at, '+00:00', '+09:00')"
        " WHERE role = 'original'"
    )
    result = merger.run(ctx, group_id, "digest-1", wall_clock_profile)
    # 壁時計 14:30:04 を UTC として読んだ epoch。オフセットで 9 時間ずらさない。
    expected = datetime(2026, 8, 17, 14, 30, 4, tzinfo=UTC).timestamp()
    assert (data_root / result.rel_path).stat().st_mtime == pytest.approx(expected, abs=1)


def test_a_captured_at_without_an_offset_is_read_as_utc(monkeypatch):
    """オフセットの無い値を実行環境の TZ で解釈しない.

    `datetime.timestamp()` は naive な値をシステムの TZ で読むので、放っておくと
    ホストの設定で派生物の mtime が変わる。
    """
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        got = _recording_end_ns(
            [{"captured_at": "2026-08-17T14:30:02", "duration_seconds": 2.0}], "instant"
        )
    finally:
        monkeypatch.undo()
        time.tzset()
    assert got == int(datetime(2026, 8, 17, 14, 30, 4, tzinfo=UTC).timestamp() * 1e9)


def test_the_work_directory_is_cleaned(world, data_root):
    merger, ctx, profile, _, group_id = world
    merger.run(ctx, group_id, "digest-1", profile)
    assert not (data_root / "work" / ctx.job_id).exists()


def test_a_changed_digest_is_refused_before_anything_runs(world, data_root):
    merger, ctx, profile, repo, group_id = world
    with pytest.raises(GroupNotClaimable):
        merger.run(ctx, group_id, "digest-2", profile)
    assert repo.get(group_id)["status"] == "detected"
    assert not (data_root / "work" / ctx.job_id).exists()


def test_a_missing_input_stops_the_job_and_fails_the_group(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    (data_root / "library/dji-osmo/DCIM/DJI_001" / NAMES[0]).unlink()
    with pytest.raises(MergeInputsChanged):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "failed"
    assert repo.get(group_id)["error"]


def test_a_cancelled_merge_releases_the_group(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
    with pytest.raises(MergeCancelled):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "detected"
    assert not (data_root / "work" / ctx.job_id).exists()


def test_the_verification_is_recorded_before_the_publish(world, data_root, db, monkeypatch):
    merger, ctx, profile, repo, group_id = world

    def explode(*args, **kwargs):
        raise PublishInterrupted("公開の途中で落ちた")

    monkeypatch.setattr(merger._publisher, "publish_prepared", explode)
    with pytest.raises(PublishInterrupted):
        merger.run(ctx, group_id, "digest-1", profile)
    row = repo.get(group_id)
    # 検証結果は残る。**merging のままにする**（reconciliation が決着させる）。
    assert row["verification_json"] is not None
    assert row["status"] == "merging"


def test_the_space_check_covers_the_ts_peak(world, data_root, db, monkeypatch):
    """入力合計は入るが、TS の中間物と出力を同時に置けない空きでは始めない."""
    import os as os_module

    merger, ctx, profile, repo, group_id = world
    total = sum(row["size_bytes"] for row in repo.members(group_id))
    real = os_module.statvfs(data_root)

    class Tight:
        f_frsize = 1
        f_bavail = total + FREE_SPACE_MARGIN + 1  # 入力 1 本ぶんは足りる

    monkeypatch.setattr(
        os_module,
        "statvfs",
        lambda path: Tight() if str(path) == str(data_root) else real,
    )
    with pytest.raises(NotEnoughSpace):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "failed"


def test_a_failed_verification_is_still_published(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    # duration をずらして不合格にする。公開は行われ、採用はされない。
    db.execute("UPDATE media_file SET duration_seconds = 60.0 WHERE role = 'original'")
    result = merger.run(ctx, group_id, "digest-1", profile)
    assert not result.passed
    assert (data_root / result.rel_path).exists()
    row = repo.get(group_id)
    assert row["status"] == "merged"
    assert row["adopted_at"] is None


def test_a_member_marked_missing_stops_the_job(world, data_root, db):
    # 実体が残っていても、欠損として記録されていれば信用しない
    # （別の内容に置き換わっている可能性がある）。
    merger, ctx, profile, repo, group_id = world
    db.execute("UPDATE media_file SET missing_at = ? WHERE role = 'original'", (BASE.isoformat(),))
    with pytest.raises(MergeInputsChanged):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "failed"


def test_the_derived_file_keeps_the_first_part_capture_time(world, db):
    merger, ctx, profile, repo, group_id = world
    result = merger.run(ctx, group_id, "digest-1", profile)
    first = repo.members(group_id)[0]
    media = db.execute("SELECT * FROM media_file WHERE id = ?", (result.media_file_id,)).fetchone()
    assert media["captured_at"] == first["captured_at"]


def test_a_cancel_between_the_verification_and_the_publish_is_observed(world, data_root, db):
    """検証の後・公開の前にもキャンセルを見る.

    見ないと、公開の走査に入ってから気づくことになり、例外の種類が変わる。
    """
    merger, ctx, profile, repo, group_id = world
    real = merger._repo.record_verification

    def cancel_then_record(*args, **kwargs):
        real(*args, **kwargs)
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    merger._repo.record_verification = cancel_then_record
    with pytest.raises(MergeCancelled):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "detected"
    assert db.execute("SELECT count(*) FROM media_file WHERE role = 'derived'").fetchone()[0] == 0


def test_parts_with_a_different_stream_order_go_through_the_ts_route(db, data_root):
    """パートごとに ffprobe した結果から map を作る.

    先頭パートの構成を全体に当てはめると、preflight が並びの違いに気づかず
    concat demuxer を使ってしまう。TS 経路が運べずに外したストリームも
    verification_json に残す。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)

    members = []
    for index, name in enumerate(NAMES):
        path = _clip_with_timecode(directory / name, audio_first=index == 1)
        rel = f"library/dji-osmo/DCIM/DJI_001/{name}"
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=rel,
            sha1=f"{index:040d}",
            size_bytes=path.stat().st_size,
            duration_seconds=2.0,
            captured_at=(BASE + timedelta(seconds=2 * index)).isoformat(),
        )
        members.append(
            MergePart(
                media_file_id=media_id,
                rel_path=rel,
                sha1=f"{index:040d}",
                captured_at=BASE + timedelta(seconds=2 * index),
                duration_seconds=2.0,
                size_bytes=path.stat().st_size,
                probe_state="ok",
            )
        )

    repo = MergeRepository(db)
    group_id = repo.save_detected(
        profile, GroupCandidate(members=tuple(members), gaps=(0.0,)), "digest-1"
    )
    store = JobStore(db)
    store.enqueue("merge", {})
    ctx = store.claim_next()
    merger = Merger(
        db,
        repo,
        ArtifactPublisher(db, data_root, MediaProbe()),
        MergeRunner(),
        MediaProbe(),
        data_root,
    )

    result = merger.run(ctx, group_id, "digest-1", profile)
    assert result.route == "ts"
    verification = json.loads(repo.get(group_id)["verification_json"])
    assert [s["codec_tag_string"] for s in verification["route_dropped_streams"]] == ["tmcd"]


def _clip_with_timecode(path, *, audio_first, seconds=2):
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size=64x64:rate=10",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
    ]
    command += ["-map", "1:a", "-map", "0:v"] if audio_first else ["-map", "0:v", "-map", "1:a"]
    command += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-timecode",
        "00:00:00:00",
        "-y",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    return path


def test_the_fallback_reason_lands_in_the_job_record(world, db, monkeypatch):
    """**なぜこの出力がこの形なのか**は、コンテナのログではなくジョブに残す.

    実機では `tmcd` が原因で毎回 TS へ落ちていたのに、画面からは経路名しか
    見えず、理由は `docker logs` を読むまで分からなかった。
    """
    from mediaferry.adapters.ffmpeg import MergeFailed

    merger, ctx, profile, _, group_id = world

    def broken(self, *args, **kwargs):  # noqa: ANN001, ANN202, ARG001
        raise MergeFailed("ffmpeg が 1 で終了した: Cannot map stream #0:4")

    monkeypatch.setattr("mediaferry.adapters.ffmpeg.MergeRunner._concat_command", broken)
    result = merger.run(ctx, group_id, "digest-1", profile)

    assert result.route == "ts"
    messages = [
        row["message"]
        for row in db.execute(
            "SELECT message FROM job_event WHERE job_id = ? AND level = 'warning'", (ctx.job_id,)
        )
    ]
    assert any("TS 経由へ落とす" in message for message in messages)
    assert any("Cannot map stream" in message for message in messages)


def test_the_merge_reports_how_far_it_has_written(world, db, monkeypatch):
    """結合は 1 時間かかることがある. 無言だと止まっているのか分からない."""
    import json

    merger, ctx, profile, _, group_id = world
    seen = []
    real = ctx.heartbeat
    monkeypatch.setattr(
        ctx, "heartbeat", lambda progress=None: (seen.append(progress), real(progress))[1]
    )
    merger.run(ctx, group_id, "digest-1", profile)

    merging = [p for p in seen if p and p.get("phase") == "merge"]
    assert merging, "結合の進捗が 1 度も報告されていない"
    assert merging[0]["route"] == "concat"
    assert merging[0]["parts"] == 2
    assert merging[0]["bytes_total"] > 0
    assert json.dumps(merging[0])


def test_the_progress_counts_a_quicktime_output(tmp_path):
    """**出力の器はプロファイルが決める.**

    `.mp4` と `.ts` しか数えないと、Canon の結合中は 4 GB のあいだ
    進捗が 0 のままになる。
    """
    from mediaferry.jobs.merger import MERGE_ARTIFACT_SUFFIXES, merge_bytes_written

    (tmp_path / "MVI_20260826123713_0007-0008_MERGED.MOV").write_bytes(b"x" * 300)
    (tmp_path / "concat.log").write_bytes(b"y" * 999)
    assert ".mov" in MERGE_ARTIFACT_SUFFIXES
    assert merge_bytes_written(tmp_path) == 300


def test_the_progress_ignores_case_in_the_suffix(tmp_path):
    """カメラは大文字の拡張子を書く. `.MOV` も数える."""
    from mediaferry.jobs.merger import merge_bytes_written

    (tmp_path / "A.MOV").write_bytes(b"x" * 10)
    assert merge_bytes_written(tmp_path) == 10


def test_the_progress_counts_the_ts_parts_too(tmp_path):
    """TS 経路は各パートの .ts と出力を両方置く. 分母が倍になる前提を壊さない."""
    from mediaferry.jobs.merger import merge_bytes_written

    (tmp_path / "0.ts").write_bytes(b"x" * 5)
    (tmp_path / "out.mp4").write_bytes(b"y" * 7)
    assert merge_bytes_written(tmp_path) == 12
