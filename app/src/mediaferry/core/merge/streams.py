"""保持するストリームの決定（§9.8）.

**ffmpeg の暗黙の選択に任せない。** 任せると「何が保持されたか」が出力を
見るまで分からず、誤ったストリームを選んだ出力を、その出力自身を基準に
合格させてしまう。ここで決めた集合を `-map` にも検証にも使う。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..profiles.model import KeepStreams

TIMECODE_TAG = "tmcd"

# mpegts が無損失で運べない codec の接頭辞。**種別ではなく codec の軸**なので、
# `UNSUPPORTED_BY_TS`（data を落とす）とは混ぜない。
_TS_LOSSY_CODEC_PREFIXES = ("pcm_",)


def selected_streams(streams: Sequence[dict[str, Any]], keep: KeepStreams) -> list[dict[str, Any]]:
    """保持対象を、入力のストリーム順で返す."""
    kept: list[dict[str, Any]] = []

    videos = [s for s in streams if s.get("codec_type") == "video" and not _is_thumbnail(s)]
    kept.extend(videos[:1] if keep.video == "primary" else videos)

    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if keep.audio == "primary":
        audios = audios[:1]
    elif keep.audio == "none":
        audios = []
    kept.extend(audios)

    for stream in streams:
        if stream.get("codec_type") != "data":
            continue
        is_timecode = stream.get("codec_tag_string") == TIMECODE_TAG
        if keep.timecode if is_timecode else keep.data:
            kept.append(stream)

    return sorted(kept, key=lambda s: s["index"])


def stream_signature(streams: Sequence[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    """本数と codec の一致を比べるための署名.

    **欠けた値と `None` を同じに扱う。** ffprobe は持たない欄を落とし、
    `stream_summary` は `None` で埋める。片方を `"None"` にすると、要約と生の
    ストリームで署名が食い違い、経路が落としたものの差し引きが黙って効かなくなる。
    """
    return tuple(
        (
            _text(s.get("codec_type")),
            _text(s.get("codec_name")),
            _text(s.get("codec_tag_string")),
        )
        for s in streams
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def map_arguments(streams: Sequence[dict[str, Any]]) -> list[str]:
    """`-map 0:<index>` の列. 絶対 index で指定し、選択を曖昧にしない.

    **index はそのストリームが属するファイルのもの。** 別のパートへ使い回すと、
    保持対象の並びが違うファイルで別のストリームを選ぶ。
    """
    args: list[str] = []
    for stream in streams:
        args.extend(["-map", f"0:{stream['index']}"])
    return args


def stream_summary(stream: dict[str, Any]) -> dict[str, Any]:
    """記録・表示用の要約. 検証と ffmpeg アダプタが同じ形で残す."""
    return {
        "index": stream.get("index"),
        "codec_type": stream.get("codec_type"),
        "codec_name": stream.get("codec_name"),
        "codec_tag_string": stream.get("codec_tag_string"),
        "bit_rate": stream.get("bit_rate"),
    }


def _is_thumbnail(stream: dict[str, Any]) -> bool:
    """埋め込みサムネイル（`attached_pic`）を映像として数えない."""
    return bool(stream.get("disposition", {}).get("attached_pic"))


def ts_route_blockers(
    streams: Sequence[dict[str, Any]], keep: KeepStreams
) -> tuple[dict[str, Any], ...]:
    """TS 経路が無損失で運べない、保持対象のストリームを返す.

    mpegts は PCM を private data として詰め、**警告だけ出して終了コード 0 で
    成功する**。読み直すと `bin_data` の data ストリームになり、音声が消える。
    ffmpeg が失敗しない以上、こちらで運べないと判断するしかない。

    **捨てるストリームは数えない。** `keep` が落とすものは出力に影響しない。
    """
    return tuple(
        stream_summary(stream)
        for stream in selected_streams(streams, keep)
        if str(stream.get("codec_name", "")).startswith(_TS_LOSSY_CODEC_PREFIXES)
    )
