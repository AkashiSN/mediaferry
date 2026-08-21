from datetime import UTC, datetime

import pytest

from mediaferry.core.profiles.model import parse_definition
from mediaferry.core.timestamps import TimezoneUnresolved, resolve_captured_at

from .test_profile_model import a_definition


def defn(**timestamp_over):
    """既定は `mtime_semantics: instant`（実測した DJI と同じ）.

    **`wall_clock` を見る試験は明示的に渡す。** 既定に頼ると、既定を変える回帰で
    どちらの意味を試していたのか読めなくなる。
    """
    ts = a_definition()["timestamp"] | {"mtime_semantics": "instant"} | timestamp_over
    return parse_definition(a_definition(timestamp=ts))


def mtime_ns_of(instant_utc: str) -> int:
    """mtime は**真の瞬間**。UTC で書いた時刻の epoch を返す."""
    dt = datetime.fromisoformat(instant_utc).replace(tzinfo=UTC)
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
    """mtime は真の瞬間なので、壁時計はプロファイルの TZ で描画する.

    DJI は exFAT の `OffsetFromUtc` を書いており、ドライバはそれで UTC へ
    変換する（`docs/history/hardware-verification.md` の 11 番）。UTC 表現を
    壁時計として使うと、オフセットぶんずれた `captured_at` が入る。
    """
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"),
        "PANORAMA/PANO_0001.JPG",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.source == "mtime"
    assert got.at.isoformat() == "2026-08-17T14:00:00+09:00"
    assert got.at.timestamp() == mtime_ns_of("2026-08-17T05:00:00") / 1e9


def test_a_wall_clock_profile_reads_the_mtime_as_a_local_wall_clock():
    """`OffsetFromUtc` を書かない媒体（FAT32 等）はこちら.

    その epoch は「現地の壁時計を UTC と見なした疑似 epoch」なので、UTC 表現の
    桁を壁時計として読み、プロファイルのオフセットを付ける。
    """
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo", mtime_semantics="wall_clock"),
        "PANORAMA/PANO_0001.JPG",
        mtime_ns_of("2026-08-17T05:00:00"),
        None,
    )
    assert got.source == "mtime"
    assert got.at.isoformat() == "2026-08-17T05:00:00+09:00"


def test_the_mtime_fallback_uses_the_default_timezone_too():
    """プロファイルが TZ を持たないときは既定値で描画する.

    ここが UTC のままだと、既定値を設定した意味が fallback だけ消える。
    """
    got = resolve_captured_at(
        defn(),
        "PANORAMA/PANO_0001.JPG",
        mtime_ns_of("2026-08-17T05:00:00"),
        "Europe/Berlin",
    )
    assert got.source == "mtime"
    assert got.at.isoformat() == "2026-08-17T07:00:00+02:00"


def test_the_mtime_fallback_keeps_the_instant_across_the_dst_fold():
    """**mtime は瞬間なので fold まで決まっている。** 壁時計へ落として付け直さない.

    Europe/Berlin の 2026-10-25T01:30:00Z は 02:30+01:00（DST の戻りの 2 回目）。
    naive の壁時計に落として `_attach_offset` に通すと「先に来る方」が選ばれ、
    epoch が 1 時間ずれる。曖昧なのは**壁時計から始めたとき**の話で、瞬間から
    始めた値に当ててはいけない。
    """
    instant = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    got = resolve_captured_at(
        defn(timezone="Europe/Berlin"),
        "PANORAMA/PANO_0001.JPG",
        int(instant.timestamp() * 1_000_000_000),
        None,
    )
    assert got.source == "mtime"
    assert got.at.isoformat() == "2026-10-25T02:30:00+01:00"
    assert got.at.timestamp() == instant.timestamp()
    assert got.note is None, "瞬間から決めた値に「曖昧」の断りは要らない"


def test_the_same_wall_clock_before_the_fold_keeps_its_own_instant():
    """1 時間前（fold の 1 回目）は同じ壁時計で別の瞬間. どちらも動かさない."""
    instant = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    got = resolve_captured_at(
        defn(timezone="Europe/Berlin"),
        "PANORAMA/PANO_0001.JPG",
        int(instant.timestamp() * 1_000_000_000),
        None,
    )
    assert got.at.isoformat() == "2026-10-25T02:30:00+02:00"
    assert got.at.timestamp() == instant.timestamp()


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


# ----------------------------------------------------------------------
# source: exif（Task 3）
#
# **値は呼び出し側が注入する。** この層は純粋関数のままにする —— ファイルを
# 読むのは adapters の仕事で、読む対象（ステージ済みのファイル）はここからは
# 見えない（§9.3 手順 5）。


def test_exif_source_uses_the_injected_wall_clock():
    got = resolve_captured_at(
        defn(source="exif", pattern=None, format=None, timezone_policy="none"),
        "DCIM/100CANON/IMG_0001.JPG",
        mtime_ns_of("2020-01-01T00:00:00"),
        None,
        exif_wall=datetime(2026, 8, 19, 14, 30, 5),  # noqa: DTZ001
    )
    assert got.source == "exif"
    assert got.at == datetime(2026, 8, 19, 14, 30, 5, tzinfo=UTC)  # noqa: DTZ001


def test_exif_source_falls_back_when_the_value_is_missing():
    """EXIF が無いファイル（Canon の MOV、タグの無い JPEG）は fallback へ."""
    got = resolve_captured_at(
        defn(source="exif", pattern=None, format=None, timezone_policy="none"),
        "DCIM/100CANON/MVI_0001.MOV",
        mtime_ns_of("2026-05-05T10:00:00"),
        None,
        exif_wall=None,
    )
    assert got.source == "mtime"
    assert got.at == datetime(2026, 5, 5, 10, 0, 0, tzinfo=UTC)  # noqa: DTZ001


def test_an_injected_value_is_ignored_when_the_profile_does_not_ask_for_exif():
    """`source: filename` のプロファイルに値が渡っても使わない.

    使うと、プロファイルの宣言と実際の解釈がずれる。

    **ファイル名が当たらない筋書きで見る。** 当たる筋書きだと `filename` の枝が
    先に返ってしまい、`exif` の枝を一度も通らない —— 宣言を見ているかどうかを
    検証したことにならない。
    """
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"),
        "PANORAMA/PANO_0001.JPG",  # pattern に当たらない → fallback へ
        mtime_ns_of("2026-05-05T10:00:00"),
        None,
        exif_wall=datetime(1999, 1, 1, 0, 0, 0),  # noqa: DTZ001
    )
    assert got.source == "mtime", "宣言していない exif の値を使っている"
    assert got.at.year == 2026


def test_a_matching_filename_wins_over_an_injected_value():
    """`source: filename` で名前が当たれば、注入された値より優先する."""
    got = resolve_captured_at(
        defn(timezone="Asia/Tokyo"),
        "DCIM/DJI_20260817143000_0001_D.MP4",
        0,
        None,
        exif_wall=datetime(1999, 1, 1, 0, 0, 0),  # noqa: DTZ001
    )
    assert got.source == "filename"
    assert got.at.year == 2026


def test_exif_wall_clock_gets_the_configured_offset():
    """`force_offset` のプロファイルなら EXIF の壁時計にもオフセットを付ける."""
    got = resolve_captured_at(
        defn(source="exif", pattern=None, format=None, timezone="Asia/Tokyo"),
        "DCIM/100CANON/IMG_0001.JPG",
        0,
        None,
        exif_wall=datetime(2026, 8, 19, 14, 30, 5),  # noqa: DTZ001
    )
    assert got.source == "exif"
    assert got.at.isoformat() == "2026-08-19T14:30:05+09:00"


def test_an_aware_exif_value_is_still_read_as_a_wall_clock():
    """`exif_wall` は**壁時計**という契約. オフセットが付いていても桁を採る.

    「aware かどうか」で mtime を見分けると、オフセット付きの EXIF がその値の
    まま通り、`at` は +00:00 なのに `tz` は Asia/Tokyo という矛盾した
    `CapturedAt` ができる（`adapters/exif.py` は naive しか返さないので現行の
    経路では起きないが、判別の根拠としては脆い）。
    """
    got = resolve_captured_at(
        defn(source="exif", pattern=None, format=None, timezone="Asia/Tokyo"),
        "DCIM/100CANON/IMG_0001.JPG",
        0,
        None,
        exif_wall=datetime(2026, 8, 19, 14, 30, tzinfo=UTC),
    )
    assert got.source == "exif"
    assert got.at.isoformat() == "2026-08-19T14:30:00+09:00"
    assert got.at.utcoffset() == got.at.tzinfo.utcoffset(got.at), "at と tz が食い違っている"


def test_a_pathological_timestamp_pattern_falls_back_instead_of_hanging():
    """`timestamp.pattern` もユーザが書く. 悪性の式で取り込みを止めない.

    **別スレッドで上限付きに走らせる。** 直に呼ぶと、`timeout` を外す回帰の
    ときにテストが「失敗」ではなくハングする。

    ここは `matching` と違って `fallback` に落とす —— 判定と違い、取り込みは
    1 ファイルごとの処理で、日時が決まらないことを理由に全体を止める必要が無い。
    """
    import threading

    got: list = []

    def run():
        try:
            got.append(
                resolve_captured_at(
                    # `ts` の名前付きグループは既存の検証が要求する。
                    # **式が本当に破綻することを測ってから使う**（`\d{4}` を頭に
                    # 置くと即座に失敗して破綻しない —— 変異試験が素通りする）。
                    defn(pattern=r"(?P<ts>(a|a)+)$", format="%Y", timezone="Asia/Tokyo"),
                    "a" * 40 + "!",
                    mtime_ns_of("2026-05-05T10:00:00"),
                    None,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - 何が出たかを表に出す
            got.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=15)
    assert not worker.is_alive(), "悪性の式で固まっている（timeout が効いていない）"
    assert not isinstance(got[0], BaseException), f"例外が出た: {got[0]!r}"
    assert got[0].source == "mtime"
    assert got[0].at.year == 2026
