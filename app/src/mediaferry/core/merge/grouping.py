"""分割録画のグループ検出（§9.7）.

同一録画と判定する条件は 2 つ。直前ファイルの終端（開始時刻 + duration）と
次ファイルの開始時刻の差が `tolerance_seconds` 以内であること、かつ直前
ファイルのサイズが `min_part_size_gib` 以上であること。第 2 条件は、DJI が
~16GiB で自動分割することを利用して「分割」と「連続した別録画」を区別する。

OS も DB も知らない。呼び出し側が並べた列を受け取り、境界で切るだけにする。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ..profiles.model import MergeRule

GIB = 1024**3


@dataclass(frozen=True)
class MergePart:
    media_file_id: str
    rel_path: str
    sha1: str
    captured_at: datetime
    duration_seconds: float | None
    size_bytes: int
    probe_state: str


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
        if previous.size_bytes < minimum or gap < 0 or gap > rule.tolerance_seconds:
            # オーバーラップ（差が負）も別の録画として扱う。
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


def _flush(current: list[MergePart], gaps: list[float]) -> list[GroupCandidate]:
    if len(current) < 2:
        return []
    return [GroupCandidate(members=tuple(current), gaps=tuple(gaps))]
