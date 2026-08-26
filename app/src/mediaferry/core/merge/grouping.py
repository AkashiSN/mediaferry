"""分割録画のグループ検出（§9.7）.

同一録画と判定する条件は 2 つ。直前ファイルの終端（開始時刻 + duration）と
次ファイルの開始時刻の差が `tolerance_seconds` 以内であること、かつ直前
ファイルのサイズが `min_part_size_gib` 以上であること。第 2 条件は、DJI が
~16GiB で自動分割することを利用して「分割」と「連続した別録画」を区別する。

**差の下限は 0 ではなく、`captured_at` の分解能**。ファイル名由来の時刻は秒まで
しか無いのに duration は小数なので、終端の推定は構造的に 1 秒ぶれる。0 で切ると、
**同じ録画の継ぎ目が丸めの符号で割れる**（実機の DJI で、5 パートが
+0.963 / +0.091 / −0.909 / +0.877 の並びになり 2 つに割れた）。

OS も DB も知らない。呼び出し側が並べた列を受け取り、境界で切るだけにする。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ..profiles.model import MergeRule

GIB = 1024**3

# `captured_at` の分解能（秒）。**これより小さい負の差は重なりの証拠にならない。**
# 秒に丸めた 2 つの開始時刻の差は、真の差から (-1, +1) の開区間ぶんだけずれうる
# ので、ちょうど 1 分解能ぶんの重なりは丸めでは作れない（本物の重なりを意味する）。
_RESOLUTION_SECONDS = {
    "filename": 1.0,  # プロファイルの format は秒までしか持たない
    "exif": 1.0,  # DateTimeOriginal は秒
    "container": 1.0,  # QuickTime の creation_time は秒
}
# mtime は秒未満まで持つ（`datetime.fromtimestamp(mtime_ns / 1e9)`）。丸めの
# 逃げ道を与える理由が無いので、既定は 0（負の差はそのまま重なりとして扱う）。
_DEFAULT_RESOLUTION_SECONDS = 0.0


@dataclass(frozen=True)
class MergePart:
    media_file_id: str
    rel_path: str
    sha1: str
    captured_at: datetime
    duration_seconds: float | None
    size_bytes: int
    probe_state: str
    # 分解能を決めるのに使う（filename / exif / mtime）。
    captured_at_source: str = "mtime"


@dataclass(frozen=True)
class GroupCandidate:
    """2 件以上のパートからなるグループ候補.

    `gaps` は継ぎ目ごとの差（秒）で、`members[i]` の終端と `members[i+1]` の
    開始の差が `gaps[i]` にあたる。画面で「なぜグループ化されたか」を示す。
    """

    members: tuple[MergePart, ...]
    gaps: tuple[float, ...]


def detect_groups(parts: Sequence[MergePart], rule: MergeRule) -> list[GroupCandidate]:
    """`parts` は開始時刻の昇順であること. 並べ替えは呼び出し側の責務."""
    if not rule.enabled:
        return []
    minimum = rule.min_part_size_gib * GIB
    groups: list[GroupCandidate] = []
    current: list[MergePart] = []
    gaps: list[float] = []

    for part in parts:
        if part.probe_state != "ok" or part.duration_seconds is None:
            # duration が無いファイルは境界。前後をつなぐ根拠が無い。
            groups.extend(_flush(current, gaps))
            current, gaps = [], []
            continue
        if not current:
            current = [part]
            continue
        previous = current[-1]
        gap = _gap_seconds(previous, part)
        overlap = -_resolution_seconds(previous, part)
        if previous.size_bytes < minimum or gap <= overlap or gap > rule.tolerance_seconds:
            # 分解能を超える重なりは、別の録画として扱う。
            groups.extend(_flush(current, gaps))
            current, gaps = [part], []
            continue
        current.append(part)
        gaps.append(gap)

    groups.extend(_flush(current, gaps))
    return groups


def _gap_seconds(previous: MergePart, following: MergePart) -> float:
    """直前の終端から次の開始までの差. `previous.duration_seconds` は非 None."""
    end = previous.captured_at.timestamp() + previous.duration_seconds
    return following.captured_at.timestamp() - end


def _resolution_seconds(previous: MergePart, following: MergePart) -> float:
    """2 つの時刻のうち粗い方の分解能. 差の誤差はこれを超えない."""
    return max(
        _RESOLUTION_SECONDS.get(previous.captured_at_source, _DEFAULT_RESOLUTION_SECONDS),
        _RESOLUTION_SECONDS.get(following.captured_at_source, _DEFAULT_RESOLUTION_SECONDS),
    )


def _flush(current: list[MergePart], gaps: list[float]) -> list[GroupCandidate]:
    if len(current) < 2:
        return []
    return [GroupCandidate(members=tuple(current), gaps=tuple(gaps))]
