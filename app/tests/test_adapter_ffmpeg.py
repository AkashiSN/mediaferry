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


def make_pcm_clip(path, seconds=2, *, audio_first=False):
    """音声が PCM のクリップ. Canon の MOV と同じ形（器も QuickTime）."""
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
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"]
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


def test_the_concat_route_does_not_map_streams_it_cannot_carry(tmp_path, work_dir):
    """**実機の DJI はこれで毎回 TS へ落ちていた.**

    concat demuxer は data ストリームを運べず、`-map` に残すと
    `Cannot map stream #0:4 - unsupported type` で即座に落ちる。TS 経路も同じ
    ものを落とすので、最初から選ばなければ失うものは無く、**往復を丸ごと省ける**
    （実機では 74.87 GiB で 14 分かかっていた）。TS 経路は `hvc1` を `hev1` に
    変えてしまうので、通らずに済むこと自体に価値がある。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    parts = [
        make_clip(tmp_path / "a.MP4", timecode=True),
        make_clip(tmp_path / "b.MP4", timecode=True),
    ]
    outcome = MergeRunner().merge(
        parts, streams_for(parts), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "concat"
    assert [s["codec_tag_string"] for s in outcome.dropped_by_route] == ["tmcd"]
    assert MediaProbe().describe(outcome.output_path, "MP4").probe_state == "ok"


def test_the_merge_refuses_the_ts_route_when_it_would_lose_audio(tmp_path, work_dir):
    """**4 GB を再 mux してから駄目だと分かる経路を残さない.**

    concat が失敗しても、運べないと分かっているなら TS を試さない。

    concat demuxer にファイル一覧の中で存在しないパートを混ぜても、
    `mpegts` と同じ「読めるところまでで終了コード 0」を返すことがあり、
    狙った失敗を再現しない（実測: 2 パートのみの `concat` 成功として通る）。
    確実に concat を失敗させるには、`FailingConcat`（既存のテストが使う形）
    のように concat コマンドそのものを不正な入力に差し替える。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    probe = MediaProbe()
    parts = [make_pcm_clip(tmp_path / f"{i}.mov") for i in range(2)]
    streams = [probe.describe(path, "MOV").streams for path in parts]
    with pytest.raises(MergeFailed, match="pcm_s16le"):
        FailingConcat().merge(
            parts, streams, KEEP, work_dir, "out.mov", lambda: None, lambda: False
        )


def test_a_topology_mismatch_with_pcm_also_refuses(tmp_path):
    """並びが違うときも同じ. **TS へ落ちる道は 2 つある.**"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    probe = MediaProbe()
    first = make_pcm_clip(tmp_path / "a.mov")
    second = make_pcm_clip(tmp_path / "b.mov", audio_first=True)
    streams = [probe.describe(path, "MOV").streams for path in (first, second)]
    with pytest.raises(MergeFailed, match="pcm_s16le"):
        MergeRunner().merge(
            [first, second],
            streams,
            KEEP,
            tmp_path,
            "out.mov",
            lambda: None,
            lambda: False,
        )


def test_a_topology_mismatch_with_pcm_only_in_a_later_part_also_refuses(tmp_path):
    """先頭パートに PCM が無く、後続にだけある構成でも塞がれる（M4）.

    **この枝は「パートごとにストリームの並びが違う」ことが前提の場所。**
    先頭だけを見ると、先頭が AAC で後続が PCM という構成（並びが違うので
    concat demuxer は使えない）で運べないと分からず、TS 経路が黙って音を
    落とす。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    probe = MediaProbe()
    first = make_clip(tmp_path / "a.mov")
    second = make_pcm_clip(tmp_path / "b.mov")
    streams = [probe.describe(path, "MOV").streams for path in (first, second)]
    with pytest.raises(MergeFailed, match="pcm_s16le"):
        MergeRunner().merge(
            [first, second],
            streams,
            KEEP,
            tmp_path,
            "out.mov",
            lambda: None,
            lambda: False,
        )


def test_aac_still_falls_back_to_the_ts_route(tmp_path):
    """**塞ぐのは PCM だけ.** 運べるものまで諦めない."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    probe = MediaProbe()
    first = make_clip(tmp_path / "a.mp4")
    second = make_clip(tmp_path / "b.mp4", audio_first=True)
    streams = [probe.describe(path, "MP4").streams for path in (first, second)]
    outcome = MergeRunner().merge(
        [first, second], streams, KEEP, tmp_path, "out.mp4", lambda: None, lambda: False
    )
    assert outcome.route == "ts"
