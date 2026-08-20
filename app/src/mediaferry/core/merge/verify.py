"""結合結果の検証（§9.8）.

ffprobe の出力を受ける純粋関数。4 つの検査それぞれが pass / fail /
inconclusive を返し、**inconclusive は合否に使わない**。

サイズを「Σ パートのファイルサイズ」と比べてはならない。`-c copy` は宣言した
ストリームだけを引き継ぐので、正常な結合でもファイルサイズは大きく減る
（実測で 11.4%）。期待値は保持対象の `bit_rate × duration` から出す。
`bit_rate` は codec やコンテナによって取れず、可変ビットレートでは丸めた
平均でしかないので、一律に必須とすると正常な出力を不合格にしてしまう。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..profiles.model import KeepStreams
from .streams import selected_streams, stream_signature, stream_summary

# 検証器の版。閾値や判定を変えたら上げる。**input_digest には入れない**
# （入力の同一性の判定であって、検証器の同一性ではない）。
PIPELINE_VERSION = 1
# 継ぎ目ごとに 1 秒。パート数に比例させる。
DURATION_TOLERANCE_PER_PART = 1.0
# 継ぎ目で数フレーム落ちるのは正常。
FRAME_ALLOWANCE_PER_SEAM = 2
FRAME_ALLOWANCE_BASE = 2
# TS 経由は mux のオーバーヘッドが通常経路と異なる。
SIZE_TOLERANCE = {"concat": 0.02, "ts": 0.05}
# (max - min) / mean がこれを超えたら、平均ビットレートとして信用しない。
BITRATE_SPREAD_LIMIT = 0.1
# bit_rate が無いと期待サイズを組み立てられない種別。data はサイズへの寄与が
# 小さいので、取れなければ推定から外して先へ進む。
ESTIMABLE_TYPES = frozenset({"video", "audio"})

PASS = "pass"  # noqa: S105 （検査結果の名前。秘密ではない）
FAIL = "fail"
INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ProbedFile:
    duration_seconds: float | None
    size_bytes: int
    streams: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Check:
    name: str
    verdict: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class Verification:
    passed: bool
    route: str
    pipeline_version: int
    checks: tuple[Check, ...]
    # プロファイルが保持を宣言しなかったストリーム。
    dropped_streams: tuple[dict[str, Any], ...]
    # 保持を宣言したのに、経路のコンテナが運べずに外したストリーム。
    route_dropped_streams: tuple[dict[str, Any], ...]
    seam_offsets: tuple[float, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "passed": self.passed,
                "route": self.route,
                "pipeline_version": self.pipeline_version,
                "checks": [
                    {"name": c.name, "verdict": c.verdict, "detail": c.detail} for c in self.checks
                ],
                "dropped_streams": list(self.dropped_streams),
                "route_dropped_streams": list(self.route_dropped_streams),
                "seam_offsets": list(self.seam_offsets),
            },
            ensure_ascii=False,
        )


def verify(
    parts: Sequence[ProbedFile],
    merged: ProbedFile,
    keep: KeepStreams,
    route: str,
    route_dropped: Sequence[dict[str, Any]] = (),
) -> Verification:
    checks = (
        _duration_check(parts, merged),
        _streams_check(parts, merged, keep, route_dropped),
        _frames_check(parts, merged, keep),
        _size_check(parts, merged, keep, route),
    )
    return Verification(
        # inconclusive は合否に使わない。fail が 1 つも無ければ合格。
        passed=all(check.verdict != FAIL for check in checks),
        route=route,
        pipeline_version=PIPELINE_VERSION,
        checks=checks,
        dropped_streams=_dropped_streams(parts[0], keep),
        route_dropped_streams=tuple(stream_summary(stream) for stream in route_dropped),
        seam_offsets=_seam_offsets(parts),
    )


def _duration_check(parts: Sequence[ProbedFile], merged: ProbedFile) -> Check:
    if merged.duration_seconds is None:
        return Check("duration", FAIL, {"reason": "結合後の duration が取れない"})
    if any(part.duration_seconds is None for part in parts):
        return Check("duration", INCONCLUSIVE, {"reason": "duration が取れないパートがある"})
    expected = sum(part.duration_seconds for part in parts)
    difference = abs(merged.duration_seconds - expected)
    limit = DURATION_TOLERANCE_PER_PART * len(parts)
    return Check(
        "duration",
        PASS if difference <= limit else FAIL,
        {
            "expected_seconds": expected,
            "actual_seconds": merged.duration_seconds,
            "difference_seconds": difference,
            "limit_seconds": limit,
        },
    )


def _streams_check(
    parts: Sequence[ProbedFile],
    merged: ProbedFile,
    keep: KeepStreams,
    route_dropped: Sequence[dict[str, Any]] = (),
) -> Check:
    """**経路が運べなかったものは「消えた」に数えない。**

    TS 経路は `tmcd` を運べない。それは経路の性質であって結合の失敗ではないので、
    `size` 検査と同じように差し引く（差し引かないと、TS 経路は必ず不合格になる）。
    ただし**理由を言えるものだけ** —— `route_dropped` に無いストリームが消えたら
    失敗のまま。
    """
    signatures = {stream_signature(selected_streams(part.streams, keep)) for part in parts}
    if len(signatures) != 1:
        return Check(
            "streams",
            FAIL,
            {
                "reason": "パート間でストリーム構成が一致しない",
                "signatures": [[list(s) for s in signature] for signature in sorted(signatures)],
            },
        )
    expected = next(iter(signatures))
    if not expected:
        return Check("streams", FAIL, {"reason": "保持対象のストリームが 1 本も無い"})
    carried = tuple(
        signature
        for signature in expected
        if signature not in {stream_signature([stream])[0] for stream in route_dropped}
    )
    actual = stream_signature(selected_streams(merged.streams, keep))
    detail: dict[str, Any] = {
        "expected": [list(s) for s in carried],
        "actual": [list(s) for s in actual],
    }
    if carried != expected:
        detail["dropped_by_route"] = [list(s) for s in expected if s not in carried]
    return Check("streams", PASS if actual == carried else FAIL, detail)


def _frames_check(parts: Sequence[ProbedFile], merged: ProbedFile, keep: KeepStreams) -> Check:
    part_frames = [_video_frames(part, keep) for part in parts]
    merged_frames = _video_frames(merged, keep)
    if merged_frames is None or any(frames is None for frames in part_frames):
        return Check(
            "frames",
            INCONCLUSIVE,
            {"reason": "nb_frames が取れない映像ストリームがある"},
        )
    expected = sum(part_frames)
    allowance = FRAME_ALLOWANCE_PER_SEAM * (len(parts) - 1) + FRAME_ALLOWANCE_BASE
    lost = expected - merged_frames
    return Check(
        "frames",
        PASS if lost <= allowance else FAIL,
        {
            "expected_frames": expected,
            "actual_frames": merged_frames,
            "lost_frames": lost,
            "allowance_frames": allowance,
        },
    )


def _size_check(
    parts: Sequence[ProbedFile], merged: ProbedFile, keep: KeepStreams, route: str
) -> Check:
    """保持対象の `bit_rate × duration` から期待サイズを組み立てて比べる.

    **ばらつきは対応するストリームごとに見る。** 合計で見ると、支配的な映像が
    音声の大きな変動を隠す。`bit_rate` が取れないストリームは、映像・音声なら
    推定できないので `inconclusive`、data なら推定から外して先へ進む
    （`tmcd` は毎秒わずかで、許容誤差に埋もれる）。
    """
    if any(part.duration_seconds is None for part in parts):
        return Check("size", INCONCLUSIVE, {"reason": "duration が取れないパートがある"})
    selections = [selected_streams(part.streams, keep) for part in parts]
    if len({len(selection) for selection in selections}) != 1:
        return Check("size", INCONCLUSIVE, {"reason": "パート間で保持ストリームの本数が違う"})

    excluded: list[dict[str, Any]] = []
    part_rates = [0.0] * len(parts)
    expected_bits = 0.0
    # 位置で対応付ける。構成がずれている場合はストリーム検査が fail するので、
    # ここでは本数の一致だけを前提にする。
    for column in zip(*selections, strict=True):
        rates = [_bitrate_of(stream) for stream in column]
        if any(rate is None for rate in rates):
            if column[0].get("codec_type") in ESTIMABLE_TYPES:
                return Check(
                    "size",
                    INCONCLUSIVE,
                    {
                        "reason": "映像か音声の bit_rate が取れない",
                        "stream": stream_summary(column[0]),
                    },
                )
            excluded.append(stream_summary(column[0]))
            continue
        mean = sum(rates) / len(rates)
        if mean <= 0:
            excluded.append(stream_summary(column[0]))
            continue
        spread = (max(rates) - min(rates)) / mean
        if spread > BITRATE_SPREAD_LIMIT:
            return Check(
                "size",
                INCONCLUSIVE,
                {
                    "reason": "パート間の bit_rate のばらつきが大きく、平均として使えない",
                    "stream": stream_summary(column[0]),
                    "spread": spread,
                    "limit": BITRATE_SPREAD_LIMIT,
                },
            )
        for index, (rate, part) in enumerate(zip(rates, parts, strict=True)):
            expected_bits += rate * part.duration_seconds
            part_rates[index] += rate

    expected = expected_bits / 8
    if expected <= 0:
        return Check("size", INCONCLUSIVE, {"reason": "期待サイズを組み立てられない"})
    tolerance = SIZE_TOLERANCE[route]
    ratio = abs(merged.size_bytes - expected) / expected
    return Check(
        "size",
        PASS if ratio <= tolerance else FAIL,
        {
            "expected_bytes": expected,
            "actual_bytes": merged.size_bytes,
            "ratio": ratio,
            "tolerance": tolerance,
            "part_bit_rates": part_rates,
            "part_durations": [part.duration_seconds for part in parts],
            "excluded_streams": excluded,
        },
    )


def _video_frames(probed: ProbedFile, keep: KeepStreams) -> int | None:
    total = 0
    for stream in selected_streams(probed.streams, keep):
        if stream.get("codec_type") != "video":
            continue
        raw = stream.get("nb_frames")
        if raw is None:
            return None
        try:
            total += int(raw)
        except TypeError, ValueError:
            return None
    return total


def _bitrate_of(stream: dict[str, Any]) -> float | None:
    raw = stream.get("bit_rate")
    if raw is None:
        return None
    try:
        return float(raw)
    except TypeError, ValueError:
        return None


def _dropped_streams(part: ProbedFile, keep: KeepStreams) -> tuple[dict[str, Any], ...]:
    """脱落したストリームを記録する. 画面に出してユーザが把握できるようにする."""
    kept = {id(stream) for stream in selected_streams(part.streams, keep)}
    return tuple(stream_summary(stream) for stream in part.streams if id(stream) not in kept)


def _seam_offsets(parts: Sequence[ProbedFile]) -> tuple[float, ...]:
    """継ぎ目の秒数（各パートの累積境界）. 最後の終端は継ぎ目ではない."""
    offsets: list[float] = []
    total = 0.0
    for part in parts[:-1]:
        if part.duration_seconds is None:
            return ()
        total += part.duration_seconds
        offsets.append(total)
    return tuple(offsets)
