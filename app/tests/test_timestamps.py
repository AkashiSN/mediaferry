from datetime import UTC, datetime

import pytest

from mediaferry.core.profiles.model import parse_definition
from mediaferry.core.timestamps import (
    TimezoneUnresolved,
    container_wall_clock,
    resolve_captured_at,
)

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
        defn(source=["exif", "mtime"], pattern=None, format=None, timezone_policy="none"),
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
        defn(source=["exif", "mtime"], pattern=None, format=None, timezone_policy="none"),
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
        defn(source=["exif", "mtime"], pattern=None, format=None, timezone="Asia/Tokyo"),
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
        defn(source=["exif", "mtime"], pattern=None, format=None, timezone="Asia/Tokyo"),
        "DCIM/100CANON/IMG_0001.JPG",
        0,
        None,
        exif_wall=datetime(2026, 8, 19, 14, 30, tzinfo=UTC),
    )
    assert got.source == "exif"
    assert got.at.isoformat() == "2026-08-19T14:30:00+09:00"
    assert got.at.utcoffset() == got.at.tzinfo.utcoffset(got.at), "at と tz が食い違っている"


# ----------------------------------------------------------------------
# source: container（Task 4） —— QuickTime の creation_time
#
# Canon は現地の壁時計を書きながら `Z`（UTC）を付ける（実測）。真に受けると
# 9 時間ずれるので、既定では `Z` を無視して桁をそのまま壁時計として読む。


def test_the_container_time_is_read_as_a_wall_clock_by_default():
    """**`Z` を真に受けない.**"""
    got = resolve_captured_at(
        defn(source=["container", "mtime"], pattern=None, format=None, timezone_policy="none"),
        "DCIM/100CANON/MVI_0006.MOV",
        0,
        None,
        container_wall="2026-08-26T12:35:08.000000Z",
    )
    assert got.source == "container"
    assert got.at.isoformat() == "2026-08-26T12:35:08+00:00"


def test_the_container_time_can_be_declared_as_an_instant():
    """真の UTC を書く器もある. 宣言されたときだけ瞬間として扱う."""
    got = resolve_captured_at(
        defn(
            source=["container", "mtime"],
            pattern=None,
            format=None,
            timezone_policy="force_offset",
            timezone="Etc/GMT-9",
            container_semantics="instant",
        ),
        "a.MOV",
        0,
        None,
        container_wall="2026-08-26T12:35:08.000000Z",
    )
    assert got.source == "container"
    assert got.at.isoformat() == "2026-08-26T21:35:08+09:00"


def test_the_chain_falls_through_to_mtime_when_the_container_has_no_time():
    """器が時刻を持たないファイルは mtime へ落ちる."""
    got = resolve_captured_at(
        defn(source=["container", "mtime"], pattern=None, format=None, timezone_policy="none"),
        "a.MOV",
        1_787_747_586_000_000_000,
        None,
    )
    assert got.source == "mtime"


def test_a_naive_container_time_falls_through_when_declared_as_an_instant():
    """`instant` を宣言していても、naive な値（オフセット無しの `creation_time`）
    が来る筋書きを確かめる.

    この分岐は `None` を返して連鎖が次（`mtime`）へ落ちる。
    """
    got = resolve_captured_at(
        defn(
            source=["container", "mtime"],
            pattern=None,
            format=None,
            timezone_policy="none",
            container_semantics="instant",
        ),
        "a.MOV",
        1_787_747_586_000_000_000,
        None,
        container_wall="2026-08-26T12:35:08.000000",
    )
    assert got.source == "mtime"


def test_a_container_time_is_ignored_when_the_chain_does_not_declare_it():
    """**宣言と実際の解釈をずらさない.** 連鎖に無い出所の値が来ても使わない."""
    got = resolve_captured_at(
        defn(source=["exif", "mtime"], pattern=None, format=None, timezone_policy="none"),
        "a.MOV",
        1_787_747_586_000_000_000,
        None,
        container_wall="2026-08-26T12:35:08.000000Z",
    )
    assert got.source == "mtime"


def test_exif_wins_over_the_container_when_it_comes_first():
    """写真は EXIF、動画は器 —— 1 本の連鎖で両方をまかなう."""
    got = resolve_captured_at(
        defn(
            source=["exif", "container", "mtime"],
            pattern=None,
            format=None,
            timezone_policy="none",
        ),
        "IMG_0001.CR2",
        0,
        None,
        exif_wall=datetime(2026, 8, 26, 12, 33, 5),  # noqa: DTZ001
        container_wall="2026-08-26T12:35:08.000000Z",
    )
    assert got.source == "exif"


def test_the_quicktime_epoch_is_treated_as_absent():
    """**日時を設定していない器は 1904-01-01 を書く.**

    そのまま採ると、撮影日時が 1904 年に飛んで一覧の先頭も末尾も壊れる。
    ちょうどこの値だけを「無い」として扱い、次の出所へ落とす。
    """
    got = resolve_captured_at(
        defn(source=["container", "mtime"], pattern=None, format=None, timezone_policy="none"),
        "a.MOV",
        1_787_747_586_000_000_000,
        None,
        container_wall="1904-01-01T00:00:00.000000Z",
    )
    assert got.source == "mtime"


def test_an_unparsable_container_time_falls_through():
    """読めない値で取り込み全体を止めない."""
    got = resolve_captured_at(
        defn(source=["container", "mtime"], pattern=None, format=None, timezone_policy="none"),
        "a.MOV",
        1_787_747_586_000_000_000,
        None,
        container_wall="なんだこれ",
    )
    assert got.source == "mtime"


def test_container_wall_clock_returns_a_naive_value_for_wall_clock_semantics():
    """壁時計として読んだ値は naive で返す契約を直接見る.

    **`resolve_captured_at` 越しでは見えない.** `name is None` の経路も
    `_attach_offset` の経路も、最後に必ず `wall.replace(tzinfo=...)` を呼んで
    上書きするため、`container_wall_clock` が aware のまま返しても
    `resolve_captured_at` の出力（`isoformat()`）は変わらない。この関数を
    直接呼んで初めて、aware のまま返す変異を検出できる。
    """
    got = container_wall_clock("2026-08-26T12:35:08.000000Z", UTC, "wall_clock")
    assert got.tzinfo is None


def test_wall_clock_ignores_an_explicit_offset_that_is_not_z():
    """`wall_clock` は桁だけを採る. `Z` 以外の明示オフセットでも変わらない.

    実装は `.replace(tzinfo=None)` で桁だけを落とすので、`+09:00` が付いて
    いても `Z` と同じ挙動になる（実装の穴ではなくテストの空白）。
    """
    got = container_wall_clock("2026-08-26T12:35:08+09:00", UTC, "wall_clock")
    assert got.tzinfo is None
    assert got.isoformat() == "2026-08-26T12:35:08"


def test_instant_with_a_naive_value_falls_through_to_the_next_source():
    """`instant` を宣言していても、naive な値（オフセット無し）は使わない.

    `container_wall_clock` はここで `None` を返し、連鎖が次（`mtime`）へ
    落ちる。**結果が変わる分岐なので固定する.**
    """
    got = container_wall_clock("2026-08-26T12:35:08", UTC, "instant")
    assert got is None


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
