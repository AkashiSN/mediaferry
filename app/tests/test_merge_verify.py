import json

from mediaferry.core.merge.verify import ProbedFile, verify
from mediaferry.core.profiles.model import KeepStreams

KEEP = KeepStreams(video="primary", audio="all", timecode=True, data=False)


def a_part(
    duration=1500.0,
    size=16_000_000_000,
    *,
    video_rate="79924667",
    frames="45000",
    audio_rate="317374",
    extra=(),
):
    video = {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"}
    if video_rate is not None:
        video["bit_rate"] = video_rate
    if frames is not None:
        video["nb_frames"] = frames
    audio = {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"}
    if audio_rate is not None:
        audio["bit_rate"] = audio_rate
    streams = [
        video,
        audio,
        {
            "index": 2,
            "codec_type": "data",
            "codec_name": "bin_data",
            "codec_tag_string": "dbgi",
            "bit_rate": "10300000",
        },
    ]
    streams.extend(extra)
    return ProbedFile(duration_seconds=duration, size_bytes=size, streams=tuple(streams))


def a_merged(duration=3000.0, size=None, *, frames="89999", streams=None):
    if streams is None:
        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "codec_tag_string": "hvc1",
                "bit_rate": "79924667",
                "nb_frames": frames,
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "codec_tag_string": "mp4a",
                "bit_rate": "317374",
            },
        ]
    if size is None:
        size = int((79924667 + 317374) * duration / 8)
    return ProbedFile(duration_seconds=duration, size_bytes=size, streams=tuple(streams))


def verdicts(result):
    return {check.name: check.verdict for check in result.checks}


def test_a_clean_merge_passes_every_check():
    result = verify([a_part(), a_part()], a_merged(), KEEP, "concat")
    assert result.passed
    assert verdicts(result) == {
        "duration": "pass",
        "streams": "pass",
        "frames": "pass",
        "size": "pass",
    }


def test_a_duration_beyond_the_tolerance_fails():
    result = verify([a_part(), a_part()], a_merged(duration=3010.0), KEEP, "concat")
    assert verdicts(result)["duration"] == "fail"
    assert not result.passed


def test_the_duration_tolerance_scales_with_the_part_count():
    parts = [a_part(), a_part(), a_part()]
    merged = a_merged(duration=4500.0 + 2.5)
    assert verdicts(verify(parts, merged, KEEP, "concat"))["duration"] == "pass"


def test_a_missing_kept_stream_fails():
    merged = a_merged(
        streams=[
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "codec_tag_string": "hvc1",
                "bit_rate": "79924667",
                "nb_frames": "89999",
            },
        ]
    )
    result = verify([a_part(), a_part()], merged, KEEP, "concat")
    assert verdicts(result)["streams"] == "fail"
    assert not result.passed


def test_a_recoded_stream_fails_even_with_the_same_count():
    merged = a_merged(
        streams=[
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "codec_tag_string": "avc1",
                "bit_rate": "79924667",
                "nb_frames": "89999",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "codec_tag_string": "mp4a",
                "bit_rate": "317374",
            },
        ]
    )
    result = verify([a_part(), a_part()], merged, KEEP, "concat")
    assert verdicts(result)["streams"] == "fail"


def test_parts_that_disagree_with_each_other_fail():
    other = a_part()
    changed = list(other.streams)
    changed[0] = {**changed[0], "codec_name": "h264"}
    result = verify(
        [a_part(), ProbedFile(1500.0, 16_000_000_000, tuple(changed))],
        a_merged(),
        KEEP,
        "concat",
    )
    assert verdicts(result)["streams"] == "fail"


def test_dropped_streams_are_recorded():
    result = verify([a_part(), a_part()], a_merged(), KEEP, "concat")
    assert [s["codec_tag_string"] for s in result.dropped_streams] == ["dbgi"]


def test_seam_offsets_are_the_cumulative_boundaries():
    result = verify([a_part(), a_part(duration=1200.0)], a_merged(duration=2700.0), KEEP, "concat")
    assert result.seam_offsets == (1500.0,)


def test_lost_frames_within_the_allowance_pass():
    # 2 パートなら許容は 2 * (2 - 1) + 2 = 4 フレーム。
    result = verify(
        [a_part(frames="45000"), a_part(frames="45000")],
        a_merged(frames="89996"),
        KEEP,
        "concat",
    )
    assert verdicts(result)["frames"] == "pass"


def test_lost_frames_beyond_the_allowance_fail():
    result = verify(
        [a_part(frames="45000"), a_part(frames="45000")],
        a_merged(frames="89995"),
        KEEP,
        "concat",
    )
    assert verdicts(result)["frames"] == "fail"


def test_extra_frames_do_not_fail_the_check():
    # §9.8 の条件は「Σ パート − 結合後 ≤ 許容」の片側。多い側は判定しない。
    result = verify(
        [a_part(frames="45000"), a_part(frames="45000")],
        a_merged(frames="90100"),
        KEEP,
        "concat",
    )
    assert verdicts(result)["frames"] == "pass"


def test_a_merged_file_without_frame_counts_is_inconclusive():
    merged = a_merged(frames=None)
    result = verify([a_part(), a_part()], merged, KEEP, "concat")
    assert verdicts(result)["frames"] == "inconclusive"
    assert result.passed


def test_a_merged_file_without_a_duration_fails():
    # duration が取れない出力を「判定不能」で通すと、壊れた結合物が合格になる。
    merged = ProbedFile(None, a_merged().size_bytes, a_merged().streams)
    result = verify([a_part(), a_part()], merged, KEEP, "concat")
    assert verdicts(result)["duration"] == "fail"
    assert not result.passed


def test_parts_with_different_kept_stream_counts_are_inconclusive():
    # 本数が違うまま列で対応付けると、別のストリーム同士を比べるか例外になる。
    second_audio = {
        "index": 3,
        "codec_type": "audio",
        "codec_name": "aac",
        "codec_tag_string": "mp4a",
        "bit_rate": "317374",
    }
    parts = [a_part(), a_part(extra=[second_audio])]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "inconclusive"


def test_missing_frame_counts_are_inconclusive_not_failed():
    parts = [a_part(frames=None), a_part()]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["frames"] == "inconclusive"
    assert result.passed


def test_a_missing_video_bit_rate_makes_the_size_check_inconclusive():
    parts = [a_part(video_rate=None), a_part()]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "inconclusive"
    assert result.passed


def test_a_timecode_without_a_bit_rate_does_not_disable_the_size_check():
    """既定の DJI プロファイルは timecode を保持する. tmcd に bit_rate は無い.

    ここで全体を inconclusive にすると、Phase 0 で直したサイズ検査が既定で
    毎回死ぬ。推定から外して、外したことを detail に残す。
    """
    tmcd = {"index": 3, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"}
    parts = [a_part(extra=[tmcd]), a_part(extra=[tmcd])]
    merged_streams = [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "codec_tag_string": "hvc1",
            "bit_rate": "79924667",
            "nb_frames": "89999",
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "codec_tag_string": "mp4a",
            "bit_rate": "317374",
        },
        {"index": 2, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"},
    ]
    result = verify(parts, a_merged(streams=merged_streams), KEEP, "concat")
    assert verdicts(result)["size"] == "pass"
    excluded = result.checks[3].detail["excluded_streams"]
    assert [s["codec_tag_string"] for s in excluded] == ["tmcd"]


def test_a_wide_bit_rate_spread_makes_the_size_check_inconclusive():
    parts = [a_part(video_rate="79924667"), a_part(video_rate="40000000")]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "inconclusive"


def test_a_spread_hidden_by_the_dominant_stream_is_still_caught():
    """合計で見ると、80 Mbps の映像が音声の 2 倍の変動を隠す."""
    parts = [a_part(audio_rate="317374"), a_part(audio_rate="634748")]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "inconclusive"


def test_the_size_is_compared_against_the_kept_streams_only():
    # データトラック（dbgi 10.3 Mbps）を足しても期待サイズは変わらない。
    parts = [a_part(), a_part()]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "pass"


def test_a_size_beyond_the_tolerance_fails():
    parts = [a_part(), a_part()]
    merged = a_merged(size=int((79924667 + 317374) * 3000.0 / 8 * 1.05))
    assert verdicts(verify(parts, merged, KEEP, "concat"))["size"] == "fail"


def test_the_ts_route_allows_a_wider_size_difference():
    parts = [a_part(), a_part()]
    merged = a_merged(size=int((79924667 + 317374) * 3000.0 / 8 * 1.04))
    assert verdicts(verify(parts, merged, KEEP, "concat"))["size"] == "fail"
    assert verdicts(verify(parts, merged, KEEP, "ts"))["size"] == "pass"


def test_streams_dropped_by_the_route_are_recorded():
    """TS 経路が運べずに外したストリームは、脱落の理由が違うので分けて残す."""
    dropped = [{"index": 4, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"}]
    result = verify([a_part(), a_part()], a_merged(), KEEP, "ts", route_dropped=dropped)
    assert [s["codec_tag_string"] for s in result.route_dropped_streams] == ["tmcd"]


def test_the_result_serialises_to_json():
    result = verify([a_part(), a_part()], a_merged(), KEEP, "concat")
    payload = json.loads(result.to_json())
    assert payload["passed"] is True
    assert payload["route"] == "concat"
    assert payload["pipeline_version"] == 1
    assert payload["seam_offsets"] == [1500.0]
    assert {c["name"] for c in payload["checks"]} == {"duration", "streams", "frames", "size"}
    assert payload["checks"][3]["detail"]["part_bit_rates"] == [80242041.0, 80242041.0]


TMCD = {"index": 4, "codec_type": "data", "codec_name": None, "codec_tag_string": "tmcd"}


def test_a_stream_the_route_could_not_carry_is_not_counted_as_missing():
    """**実機の DJI はここで必ず落ちていた.**

    TS 経路は `tmcd` を運べない。実装はそれを `route_dropped` に記録し、`size`
    検査も差し引いている（`excluded_streams`）のに、`streams` 検査だけが
    差し引かずに「期待に無い」と判定していた。**TS 経路を通ると必ず不合格になる。**
    """
    parts = [a_part(extra=[TMCD]), a_part(extra=[TMCD])]
    merged = a_merged()
    assert verdicts(verify(parts, merged, KEEP, "ts", route_dropped=[TMCD]))["streams"] == "pass"


def test_a_stream_that_vanished_without_a_reason_still_fails():
    """経路のせいだと言えないなら、消えたことは失敗のまま."""
    parts = [a_part(extra=[TMCD]), a_part(extra=[TMCD])]
    assert verdicts(verify(parts, a_merged(), KEEP, "concat"))["streams"] == "fail"
