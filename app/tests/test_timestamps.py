from datetime import UTC, datetime

import pytest

from mediaferry.core.profiles.model import parse_definition
from mediaferry.core.timestamps import TimezoneUnresolved, resolve_captured_at

from .test_profile_model import a_definition


def defn(**timestamp_over):
    ts = a_definition()["timestamp"] | timestamp_over
    return parse_definition(a_definition(timestamp=ts))


def mtime_ns_of(wall_utc: str) -> int:
    dt = datetime.fromisoformat(wall_utc).replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def test_filename_wall_clock_gets_the_configured_offset():
    """DJI は creation_time を UTC で書きつつオフセットを書かないので、
    ファイル名の壁時計に profile の TZ を付けて撮影時刻とする."""
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"), "DCIM/DJI_20260817143000_0001_D.MP4", 0, None
    )
    assert got.at.isoformat() == "2026-08-17T14:30:00+09:00"
    assert got.source == "filename"
    assert got.tz == "Asia/Tokyo"


def test_the_default_timezone_is_used_when_the_profile_has_none():
    got = resolve_captured_at(defn(), "DCIM/DJI_20260817143000_0001_D.MP4", 0, "Europe/Berlin")
    assert got.at.utcoffset().total_seconds() == 2 * 3600
    assert got.tz == "Europe/Berlin"


def test_the_profile_timezone_wins_over_the_default():
    """既定値は「プロファイルが決めていないとき」の受け皿.

    逆順にすると、機種に固定した TZ が全体設定で黙って上書きされる。
    """
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"), "DCIM/DJI_20260817143000_0001_D.MP4", 0, "Europe/Berlin"
    )
    assert got.tz == "Asia/Tokyo"
    assert got.at.isoformat() == "2026-08-17T14:30:00+09:00"


def test_force_offset_without_any_timezone_is_an_error():
    """UTC を既定にすると補正にならないまま誤った時刻で確定する（§12.2）."""
    with pytest.raises(TimezoneUnresolved):
        resolve_captured_at(defn(), "DCIM/DJI_20260817143000_0001_D.MP4", 0, None)


def test_files_that_miss_the_pattern_fall_back_to_mtime():
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"),
        "PANORAMA/PANO_0001.JPG",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.source == "mtime"
    # exFAT の mtime はローカル時刻なので、UTC 表現の壁時計にオフセットを付ける
    assert got.at.isoformat() == "2026-08-17T05:00:00+09:00"


def test_policy_none_keeps_the_instant_as_recorded():
    """Canon は EXIF にローカル時刻を書くので介入しない."""
    got = resolve_captured_at(
        defn(timezone_policy="none", timezone=None),
        "DCIM/DJI_20260817143000_0001_D.MP4",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.at == datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
    assert got.tz is None


def test_an_ambiguous_wall_clock_takes_the_earlier_one_and_says_so():
    """DST の戻りで 1 時間が 2 回ある."""
    got = resolve_captured_at(
        defn(timezone="Europe/Berlin"), "DCIM/DJI_20261025023000_0001_D.MP4", 0, None
    )
    assert got.at.utcoffset().total_seconds() == 2 * 3600  # 先に来る CEST
    assert "曖昧" in got.note


def test_a_nonexistent_wall_clock_shifts_forward_and_says_so():
    """DST の進みで存在しない 1 時間がある."""
    got = resolve_captured_at(
        defn(timezone="Europe/Berlin"), "DCIM/DJI_20260329023000_0001_D.MP4", 0, None
    )
    assert got.at.isoformat() == "2026-03-29T03:30:00+02:00"
    assert "存在しない" in got.note


def test_an_unparsable_timestamp_in_the_name_falls_back():
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"),
        "DCIM/DJI_99999999999999_0001_D.MP4",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.source == "mtime"
