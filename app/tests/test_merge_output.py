from datetime import UTC, datetime, timedelta, timezone

import pytest

from mediaferry.core.merge.grouping import GIB, MergePart
from mediaferry.core.merge.output import MergeOutputUndefined, merged_rel_path
from mediaferry.core.profiles.model import KeepStreams, MergeRule


def a_rule(**overrides):
    values = {
        "enabled": True,
        "tolerance_seconds": 5,
        "min_part_size_gib": 15,
        "sequence_pattern": r"_(?P<seq>\d{4})_D$",
        "output_name": "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4",
        "keep_streams": KeepStreams(video="primary", audio="all", timecode=True, data=False),
    }
    values.update(overrides)
    return MergeRule(**values)


def a_part(name, *, captured_at=None, directory="DCIM/DJI_001"):
    return MergePart(
        media_file_id="id",
        rel_path=f"library/dji-osmo/{directory}/{name}",
        sha1="sha",
        captured_at=captured_at or datetime(2026, 8, 17, 14, 30, 0, tzinfo=UTC),
        duration_seconds=1500.0,
        size_bytes=16 * GIB,
        probe_state="ok",
    )


MEMBERS = [
    a_part("DJI_20260817143000_0001_D.MP4"),
    # 名前に使うのは先頭の時刻。末尾と取り違えたら分かるように別の時刻を持たせる。
    a_part(
        "DJI_20260817145500_0002_D.MP4",
        captured_at=datetime(2026, 8, 17, 14, 55, 0, tzinfo=UTC),
    ),
]


def test_the_output_name_follows_the_profile_template():
    got = merged_rel_path("dji-osmo", a_rule(), MEMBERS)
    assert got == "derived/dji-osmo/DCIM/DJI_001/DJI_20260817143000_0001-0002_MERGED.MP4"


def test_the_directory_layout_mirrors_the_card():
    members = [
        a_part("DJI_20260817143000_0001_D.MP4", directory="DCIM/DJI_002"),
        a_part("DJI_20260817145500_0002_D.MP4", directory="DCIM/DJI_002"),
    ]
    assert merged_rel_path("dji-osmo", a_rule(), members).startswith(
        "derived/dji-osmo/DCIM/DJI_002/"
    )


def test_the_timestamp_is_the_local_wall_clock_of_the_first_part():
    # captured_at はオフセット付きで保存されている。UTC へ直さず、そのままの
    # 壁時計を名前に使う（library 側の名前と読み比べられる形にする）。
    tokyo = timezone(timedelta(hours=9))
    members = [
        a_part(
            "DJI_20260817143000_0001_D.MP4",
            captured_at=datetime(2026, 8, 17, 14, 30, tzinfo=tokyo),
        ),
        a_part("DJI_20260817145500_0002_D.MP4"),
    ]
    assert "DJI_20260817143000_" in merged_rel_path("dji-osmo", a_rule(), members)


def test_an_unreadable_sequence_is_refused():
    members = [a_part("PANO_0001.JPG"), a_part("PANO_0002.JPG")]
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(), members)


def test_an_unknown_placeholder_is_refused():
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(output_name="{unknown}.MP4"), MEMBERS)


def test_a_path_outside_the_library_is_refused():
    # 連番が読める名前にする。読めない名前だと _sequence が先に落ちて、
    # 置き場所の判定を一度も通らない。
    outside = MergePart(
        "id",
        "derived/dji-osmo/DCIM/DJI_001/DJI_20260817143000_0001_D.MP4",
        "sha",
        MEMBERS[0].captured_at,
        1.0,
        1,
        "ok",
    )
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(), [outside, MEMBERS[1]])


def test_a_rendered_separator_is_refused():
    # プロファイルの検証をすり抜けた値でも、展開後にもう一度確かめる。
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(output_name="../{first_seq}.MP4"), MEMBERS)


def test_a_rendered_slash_is_refused():
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(output_name="{first_seq}/x.MP4"), MEMBERS)


def test_a_rendered_backslash_is_refused():
    # 「/」の判定だけをすり抜ける値。UnsafePath ではなく MergeOutputUndefined で返す。
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(output_name="DJI_{first_seq}\\x.MP4"), MEMBERS)
