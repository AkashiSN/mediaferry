"""撮影日時の解決.

`force_offset` は、ファイル名または EXIF から得た**壁時計**にプロファイルの
オフセットを付与する。DJI が MP4 の creation_time を UTC で書きつつオフセットも
GPS も書かないため、Immich が撮影地の TZ を判定できず、UTC の壁時計をそのまま
localDateTime として採用してしまう問題への対処である。

**mtime が何を表すかはプロファイルが宣言する**（`timestamp.mtime_semantics`）。
Linux の exfat ドライバは、`OffsetFromUtc` の valid bit が立っていればその
オフセットで UTC へ変換し、立っていなければマウントの `time_offset`（既定 0）を
使う（`fs/exfat/misc.c` の `exfat_get_entry_time`）。前者なら epoch は真の瞬間
（`instant`）、後者と FAT32 なら現地の壁時計を UTC と見なした疑似 epoch
（`wall_clock`）になる。**媒体の性質なので値の形からは見分けられない。**

`instant` のときは解決した timezone を付けた値をそのまま採り、**オフセットを
付け直さない** —— naive の壁時計へ落とすと、DST の戻りでどちらの 1 時間かを失う。
曖昧さの解決（下の `_attach_offset`）は、**壁時計から始めた値**にだけ当たる。

`timezone_policy: none` のときは描画に使う timezone が無いので、UTC 表現の壁時計を
そのまま採る（介入しない方針そのもの）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from .profiles.model import ProfileDefinition
from .profiles.patterns import PatternTimeout, search


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
    exif_wall: datetime | None = None,
) -> CapturedAt:
    """`exif_wall` は呼び出し側が読んだ EXIF の壁時計.

    **この層はファイルを読まない。** 読むのは `adapters/exif.py` の仕事で、
    読む対象（ステージ済みのファイル）はここからは見えない（§9.3 手順 5）。
    値を注入する形にすることで、判断は純粋関数のままにできる。
    """
    if defn.timestamp.timezone_policy == "none":
        name, zone = None, UTC
    else:
        # **TZ の解決を壁時計より先に行う。** mtime の fallback がこの TZ で描画する。
        name = defn.timestamp.timezone or default_timezone
        if name is None:
            raise TimezoneUnresolved(
                f"プロファイル {defn.slug} は force_offset だが timezone が未設定"
            )
        zone = ZoneInfo(name)

    wall, source = _wall_clock(defn, rel_path, mtime_ns, exif_wall, zone)
    if source == "mtime" and defn.timestamp.mtime_semantics == "instant":
        # **瞬間から始めた値は fold まで決まっている**ので付け直さない。
        # **見分けるのは source。** 「aware かどうか」で見ると、オフセット付きの
        # EXIF がそのまま通り、`at` と `tz` が食い違う `CapturedAt` ができる。
        return CapturedAt(at=wall, source=source, tz=name, note=None)
    if name is None:
        return CapturedAt(at=wall.replace(tzinfo=UTC), source=source, tz=None, note=None)
    at, note = _attach_offset(wall, zone)
    return CapturedAt(at=at, source=source, tz=name, note=note)


def _wall_clock(
    defn: ProfileDefinition,
    rel_path: str,
    mtime_ns: int,
    exif_wall: datetime | None,
    zone: tzinfo,
) -> tuple[datetime, str]:
    """`zone` は mtime の epoch に付ける TZ.

    **返す値は `source` が `mtime` のときだけ aware。** ファイル名と EXIF は
    壁時計で、オフセットの付与は呼び出し側が行う（`_attach_offset`）。
    """
    rule = defn.timestamp
    if rule.source == "filename" and rule.pattern is not None and rule.format is not None:
        try:
            found = search(rule.pattern, PurePosixPath(rel_path).name)
        except PatternTimeout:
            # 悪性の式で取り込み全体を止めない。fallback へ落とす。
            found = None
        if found is not None:
            try:
                return datetime.strptime(found.group("ts"), rule.format), "filename"  # noqa: DTZ007
            except ValueError:
                pass
    # **プロファイルが exif を宣言しているときだけ使う。** 宣言していない
    # プロファイルに値が渡っても無視する（宣言と実際の解釈をずらさない）。
    if rule.source == "exif" and exif_wall is not None:
        return exif_wall, "exif"
    # fallback は mtime のみを想定する。EXIF を持たないファイル（Canon の MOV、
    # タグの無い JPEG）はここへ落ちる。
    return mtime_wall_clock(mtime_ns, zone, defn.timestamp.mtime_semantics), "mtime"


def mtime_wall_clock(mtime_ns: int, zone: tzinfo, semantics: str) -> datetime:
    """mtime が指すカード上の時刻. **意味の解釈はここ 1 か所に置く.**

    - `instant`: 真の瞬間なので `zone` を付けた aware な値をそのまま返す。
      naive へ落として付け直すと、DST の戻りでどちらの 1 時間かを失う
    - `wall_clock`: UTC 表現の桁がそのまま壁時計。naive で返し、オフセットの
      付与は呼び出し側（`_attach_offset`）に任せる

    `publisher._collision_stamp` も同じ規則で桁を作る。ずれると `library/` と
    `derived/` で衝突接尾辞の壁時計が食い違う。
    """
    if semantics == "instant":
        return datetime.fromtimestamp(mtime_ns / 1e9, tz=zone)
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=UTC).replace(tzinfo=None)


def mtime_ns_of(at: datetime, semantics: str) -> int:
    """`captured_at` を、そのプロファイルの mtime 表現へ戻す（`merger` が使う）.

    取り込んだファイルの mtime と同じ表現にしないと、`library/` と `derived/` で
    epoch がオフセットぶんずれる。
    """
    if semantics == "instant":
        return int(at.timestamp() * 1e9)
    # 壁時計を UTC として読んだ疑似 epoch。オフセットの無い値はそのまま UTC。
    return int(at.replace(tzinfo=UTC).timestamp() * 1e9)


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
