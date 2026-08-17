from mediaferry.core.merge.streams import (
    map_arguments,
    selected_streams,
    stream_signature,
    stream_summary,
)
from mediaferry.core.profiles.model import KeepStreams

# Phase 0 の実測（DJI Osmo Pocket 4 の MP4 は 6 ストリーム）を写したもの。
DJI_STREAMS = [
    {
        "index": 0,
        "codec_type": "video",
        "codec_name": "hevc",
        "codec_tag_string": "hvc1",
        "bit_rate": "79924667",
        "nb_frames": "45540",
    },
    {
        "index": 1,
        "codec_type": "audio",
        "codec_name": "aac",
        "codec_tag_string": "mp4a",
        "bit_rate": "317374",
    },
    {
        "index": 2,
        "codec_type": "data",
        "codec_name": "bin_data",
        "codec_tag_string": "djmd",
        "bit_rate": "11300",
    },
    {
        "index": 3,
        "codec_type": "data",
        "codec_name": "bin_data",
        "codec_tag_string": "dbgi",
        "bit_rate": "10300000",
    },
    {"index": 4, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"},
    {
        "index": 5,
        "codec_type": "video",
        "codec_name": "mjpeg",
        "codec_tag_string": "",
        "disposition": {"attached_pic": 1},
    },
]

DJI_KEEP = KeepStreams(video="primary", audio="all", timecode=True, data=False)


def test_the_dji_profile_keeps_video_audio_and_timecode():
    kept = selected_streams(DJI_STREAMS, DJI_KEEP)
    assert [s["index"] for s in kept] == [0, 1, 4]


def test_data_tracks_are_kept_when_the_profile_asks_for_them():
    keep = KeepStreams(video="primary", audio="all", timecode=True, data=True)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0, 1, 2, 3, 4]


def test_the_timecode_track_is_dropped_independently_of_the_other_data_tracks():
    keep = KeepStreams(video="primary", audio="all", timecode=False, data=True)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0, 1, 2, 3]


def test_an_attached_thumbnail_is_not_counted_as_video():
    keep = KeepStreams(video="all", audio="none", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0]


def test_primary_video_keeps_only_the_first_real_video():
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
    ]
    keep = KeepStreams(video="primary", audio="none", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(streams, keep)] == [0]


def test_primary_audio_keeps_only_the_first_track():
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    keep = KeepStreams(video="primary", audio="primary", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(streams, keep)] == [0, 1]


def test_audio_can_be_dropped_entirely():
    keep = KeepStreams(video="primary", audio="none", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0]


def test_the_result_keeps_the_original_stream_order():
    keep = KeepStreams(video="primary", audio="all", timecode=True, data=True)
    indexes = [s["index"] for s in selected_streams(DJI_STREAMS, keep)]
    assert indexes == sorted(indexes)


def test_the_result_is_ordered_by_the_index_not_by_the_type():
    # 音声が映像より前にあるファイルでは、種別ごとに集めた順と index 順が食い違う。
    streams = [
        {"index": 0, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
        {"index": 1, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 2, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"},
    ]
    keep = KeepStreams(video="primary", audio="all", timecode=True, data=False)
    kept = selected_streams(streams, keep)
    assert [s["index"] for s in kept] == [0, 1, 2]
    assert map_arguments(kept) == ["-map", "0:0", "-map", "0:1", "-map", "0:2"]


def test_the_signature_covers_type_codec_and_tag():
    assert stream_signature(selected_streams(DJI_STREAMS, DJI_KEEP)) == (
        ("video", "hevc", "hvc1"),
        ("audio", "aac", "mp4a"),
        ("data", "none", "tmcd"),
    )


def test_map_arguments_use_the_absolute_index():
    assert map_arguments(selected_streams(DJI_STREAMS, DJI_KEEP)) == [
        "-map",
        "0:0",
        "-map",
        "0:1",
        "-map",
        "0:4",
    ]


def test_the_same_signature_can_have_different_absolute_indexes():
    """並びが違えば、同じ signature でも map は違う. 使い回してはいけない."""
    reordered = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "data", "codec_name": "bin_data", "codec_tag_string": "dbgi"},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    keep = KeepStreams(video="primary", audio="all", timecode=False, data=False)
    plain = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    assert stream_signature(selected_streams(reordered, keep)) == stream_signature(
        selected_streams(plain, keep)
    )
    assert map_arguments(selected_streams(reordered, keep)) != map_arguments(
        selected_streams(plain, keep)
    )


def test_the_summary_keeps_what_the_screen_needs():
    assert stream_summary(DJI_STREAMS[3]) == {
        "index": 3,
        "codec_type": "data",
        "codec_name": "bin_data",
        "codec_tag_string": "dbgi",
        "bit_rate": "10300000",
    }
