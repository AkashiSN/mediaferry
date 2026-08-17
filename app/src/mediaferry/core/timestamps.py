"""撮影日時の解決.

`force_offset` は、ファイル名または mtime から得た**壁時計**にプロファイルの
オフセットを付与する。DJI が MP4 の creation_time を UTC で書きつつオフセットも
GPS も書かないため、Immich が撮影地の TZ を判定できず、UTC の壁時計をそのまま
localDateTime として採用してしまう問題への対処である。

mtime の壁時計は UTC 表現から取る。**これは「カードの時刻欄に UTC オフセットが
書かれていない」ことを前提にしている。** Linux の exfat ドライバは、
`OffsetFromUtc` の valid bit が立っていればそのオフセットで UTC へ変換し、
立っていないときだけマウントの `time_offset`（既定 0）を使う
（`fs/exfat/misc.c` の `exfat_get_entry_time`）。DJI はファイル名に壁時計を
埋めるので、両者が一致するかを実機で確かめられる。手順は
`phase1-manual-checklist.md` にあり、**一致しない機種が出たらここを
プロファイルの timezone で描画する形へ変える**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from .profiles.model import ProfileDefinition


class TimezoneUnresolved(RuntimeError):
    """force_offset なのに TZ がプロファイルにも既定値にも無い."""


@dataclass(frozen=True)
class CapturedAt:
    at: datetime
    source: str  # filename / exif / mtime
    tz: str | None
    note: str | None


def resolve_captured_at(
    defn: ProfileDefinition,
    rel_path: str,
    mtime_ns: int,
    default_timezone: str | None,
) -> CapturedAt:
    wall, source = _wall_clock(defn, rel_path, mtime_ns)
    if defn.timestamp.timezone_policy == "none":
        return CapturedAt(at=wall.replace(tzinfo=UTC), source=source, tz=None, note=None)

    name = defn.timestamp.timezone or default_timezone
    if name is None:
        raise TimezoneUnresolved(f"プロファイル {defn.slug} は force_offset だが timezone が未設定")
    at, note = _attach_offset(wall, ZoneInfo(name))
    return CapturedAt(at=at, source=source, tz=name, note=note)


def _wall_clock(defn: ProfileDefinition, rel_path: str, mtime_ns: int) -> tuple[datetime, str]:
    rule = defn.timestamp
    if rule.source == "filename" and rule.pattern is not None and rule.format is not None:
        match = re.search(rule.pattern, PurePosixPath(rel_path).name)
        if match is not None:
            try:
                return datetime.strptime(match.group("ts"), rule.format), "filename"  # noqa: DTZ007
            except ValueError:
                pass
    # fallback は mtime のみを想定する（exif は Phase 5 の canon-eos で足す）。
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=UTC).replace(tzinfo=None), "mtime"


def _attach_offset(wall: datetime, zone: ZoneInfo) -> tuple[datetime, str | None]:
    """壁時計にオフセットを付ける. DST の境界は決め打ちで解決し、記録する."""
    earlier = wall.replace(tzinfo=zone, fold=0)
    later = wall.replace(tzinfo=zone, fold=1)
    if earlier.utcoffset() != later.utcoffset():
        # 同じ壁時計が 2 回あるか、1 回も無い。offset の大小で見分ける。
        if earlier.utcoffset() > later.utcoffset():
            return earlier, "壁時計が曖昧（DST の戻り）。先に来る方を採用した"
        shifted = wall + timedelta(hours=1)
        return (
            shifted.replace(tzinfo=zone),
            "壁時計が存在しない（DST の進み）。1 時間後ろへずらした",
        )
    return earlier, None
