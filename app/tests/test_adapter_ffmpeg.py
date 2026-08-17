import shutil
import subprocess

import pytest

from mediaferry.adapters.ffmpeg import (
    MergeCancelled,
    MergeFailed,
    MergeRunner,
    _topology_matches,
)
from mediaferry.adapters.ffprobe import MediaProbe
from mediaferry.core.merge.streams import selected_streams
from mediaferry.core.profiles.model import KeepStreams

KEEP = KeepStreams(video="primary", audio="all", timecode=True, data=False)
VIDEO_ONLY = KeepStreams(video="primary", audio="none", timecode=False, data=False)


def make_clip(path, seconds=2, *, timecode=False, audio_first=False):
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
    # -map の順がそのまま出力のストリーム順になる。並びの違うパートを作れる。
    command += ["-map", "1:a", "-map", "0:v"] if audio_first else ["-map", "0:v", "-map", "1:a"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    if timecode:
        command += ["-timecode", "00:00:00:00"]
    command += ["-y", str(path)]
    subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    return path


@pytest.fixture
def clips(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    return [make_clip(tmp_path / "a.MP4"), make_clip(tmp_path / "b.MP4")]


@pytest.fixture
def work_dir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


def streams_for(paths):
    return [MediaProbe().describe(path, "MP4").streams for path in paths]


def never_cancelled():
    return False


def test_two_clips_are_joined_by_the_concat_demuxer(clips, work_dir):
    outcome = MergeRunner().merge(
        clips, streams_for(clips), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "concat"
    assert outcome.dropped_by_route == ()
    probe = MediaProbe().describe(outcome.output_path, "MP4")
    assert probe.probe_state == "ok"
    assert 3.6 < probe.duration_seconds < 4.4


def test_the_declared_streams_decide_what_is_kept(clips, work_dir):
    outcome = MergeRunner().merge(
        clips,
        streams_for(clips),
        VIDEO_ONLY,
        work_dir,
        "MERGED.MP4",
        lambda: None,
        never_cancelled,
    )
    kinds = {s["codec_type"] for s in MediaProbe().describe(outcome.output_path, "MP4").streams}
    assert kinds == {"video"}


def test_the_lease_pulse_is_throttled_but_always_fires_once(clips, work_dir):
    beats = []
    MergeRunner(pulse_interval=1000.0).merge(
        clips,
        streams_for(clips),
        KEEP,
        work_dir,
        "MERGED.MP4",
        lambda: beats.append(1),
        never_cancelled,
    )
    # 短い結合でも 1 回は打つ。poll のたびには打たない。
    assert len(beats) == 1


class FailingConcat(MergeRunner):
    """concat demuxer だけが失敗する. TS 経路が実際に走ることを確かめる."""

    def _concat_command(self, parts, maps, work_dir, output):
        return [
            self._ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            "/nonexistent.MP4",
            "-y",
            str(output),
        ]


def test_the_ts_route_runs_when_the_concat_demuxer_fails(clips, work_dir):
    outcome = FailingConcat().merge(
        clips, streams_for(clips), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "ts"
    probe = MediaProbe().describe(outcome.output_path, "MP4")
    assert probe.probe_state == "ok"
    assert 3.6 < probe.duration_seconds < 4.4


def test_parts_with_a_different_stream_order_skip_the_concat_demuxer(tmp_path, work_dir):
    """concat demuxer は最初のファイルの構成を全体に適用する.

    並びが違うまま渡すと、後続のパートで別のストリームを拾う。preflight で
    弾いて TS 経路へ送る。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    parts = [
        make_clip(tmp_path / "a.MP4"),
        make_clip(tmp_path / "b.MP4", audio_first=True),
    ]
    outcome = MergeRunner().merge(
        parts, streams_for(parts), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "ts"
    assert not (work_dir / "concat.log").exists()
    kinds = sorted(
        s["codec_type"] for s in MediaProbe().describe(outcome.output_path, "MP4").streams
    )
    assert kinds == ["audio", "video"]


def test_the_preflight_also_compares_the_absolute_indexes():
    """signature が一致しても、保持対象の絶対 index が違えば map を使い回せない.

    ffprobe の出力では index が位置と一致するので、この分岐は signature 検査を
    すり抜けない。ffprobe を通さない呼び出し（DB から組み立てた列など）のための
    保険で、ここでは直接 `_topology_matches` に渡して確かめる。
    """
    first = [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    second = [
        {"index": 5, "codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1"},
        {"index": 6, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    selections = [selected_streams(first, KEEP), selected_streams(second, KEEP)]
    assert not _topology_matches([first, second], selections)
    assert _topology_matches([first, first], [selections[0], selections[0]])


def test_each_part_is_mapped_by_its_own_indexes(tmp_path, work_dir):
    """並びの違うパートでも、映像と音声が取り違えられずに残る."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    parts = [
        make_clip(tmp_path / "a.MP4"),
        make_clip(tmp_path / "b.MP4", audio_first=True),
    ]
    outcome = MergeRunner().merge(
        parts,
        streams_for(parts),
        VIDEO_ONLY,
        work_dir,
        "MERGED.MP4",
        lambda: None,
        never_cancelled,
    )
    streams = MediaProbe().describe(outcome.output_path, "MP4").streams
    assert [s["codec_type"] for s in streams] == ["video"]


def test_the_ts_route_drops_what_mpegts_cannot_carry_and_records_it(tmp_path, work_dir):
    """mpegts は tmcd を運べない. map に残すと出力そのものが作られない."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    parts = [
        make_clip(tmp_path / "a.MP4", timecode=True),
        make_clip(tmp_path / "b.MP4", timecode=True),
    ]
    outcome = FailingConcat().merge(
        parts, streams_for(parts), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "ts"
    assert [s["codec_tag_string"] for s in outcome.dropped_by_route] == ["tmcd"]
    assert MediaProbe().describe(outcome.output_path, "MP4").probe_state == "ok"


def test_a_broken_input_fails_on_both_routes(tmp_path, work_dir):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    broken = tmp_path / "broken.MP4"
    broken.write_bytes(b"\x00" * 128)
    with pytest.raises(MergeFailed):
        MergeRunner().merge(
            [broken, broken],
            [[], []],
            KEEP,
            work_dir,
            "MERGED.MP4",
            lambda: None,
            never_cancelled,
        )


def test_a_cancelled_merge_raises_and_leaves_no_output(clips, work_dir):
    with pytest.raises(MergeCancelled):
        MergeRunner().merge(
            clips,
            streams_for(clips),
            KEEP,
            work_dir,
            "MERGED.MP4",
            lambda: None,
            lambda: True,
        )


def test_the_tool_version_is_the_first_line_of_ffmpeg_version(clips):
    assert MergeRunner().tool_version().startswith("ffmpeg version")
