from datetime import UTC, datetime, timedelta

from mediaferry.core.merge.grouping import GIB, MergePart, detect_groups
from mediaferry.core.profiles.model import KeepStreams, MergeRule

BASE = datetime(2026, 8, 17, 14, 30, 0, tzinfo=UTC)


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


def a_part(index, *, offset_seconds, duration=1500.0, size=16 * GIB, probe_state="ok"):
    return MergePart(
        media_file_id=f"id-{index}",
        rel_path=f"library/dji-osmo/DCIM/DJI_001/DJI_{index:04d}_D.MP4",
        sha1=f"sha-{index}",
        captured_at=BASE + timedelta(seconds=offset_seconds),
        duration_seconds=duration,
        size_bytes=size,
        probe_state=probe_state,
    )


def test_two_parts_within_the_tolerance_form_one_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1502)]
    groups = detect_groups(parts, a_rule())
    assert len(groups) == 1
    assert [p.media_file_id for p in groups[0].members] == ["id-1", "id-2"]
    assert groups[0].gaps == (2.0,)


def test_a_gap_exactly_at_the_tolerance_stays_in_the_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1505)]
    assert len(detect_groups(parts, a_rule())) == 1


def test_a_gap_beyond_the_tolerance_splits_the_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1506)]
    assert detect_groups(parts, a_rule()) == []


def test_a_small_previous_part_splits_the_group():
    # 直前が min_part_size_gib 未満なら、時刻が続いていても別の録画。
    parts = [a_part(1, offset_seconds=0, size=1 * GIB), a_part(2, offset_seconds=1502)]
    assert detect_groups(parts, a_rule()) == []


def test_an_overlap_splits_the_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1499)]
    assert detect_groups(parts, a_rule()) == []


def test_a_failed_probe_is_a_boundary():
    # 失敗したパートを列から取り除くだけだと、その前後がつながって
    # 別の録画が 1 つのグループになる。差が tolerance に収まる並びで確かめる。
    parts = [
        a_part(1, offset_seconds=0),
        a_part(2, offset_seconds=1501, duration=None, probe_state="failed"),
        a_part(3, offset_seconds=1502),
    ]
    assert detect_groups(parts, a_rule()) == []


def test_a_boundary_does_not_stop_the_scan():
    # 切った後の並びからも候補が出る。切って終わりにしない。
    parts = [
        a_part(1, offset_seconds=0),
        a_part(2, offset_seconds=100_000),
        a_part(3, offset_seconds=101_502),
    ]
    groups = detect_groups(parts, a_rule())
    assert len(groups) == 1
    assert [p.media_file_id for p in groups[0].members] == ["id-2", "id-3"]


def test_three_parts_form_one_group_with_two_gaps():
    parts = [
        a_part(1, offset_seconds=0),
        a_part(2, offset_seconds=1502),
        a_part(3, offset_seconds=3003),
    ]
    groups = detect_groups(parts, a_rule())
    assert len(groups) == 1
    assert len(groups[0].members) == 3
    assert groups[0].gaps == (2.0, 1.0)


def test_a_single_part_is_not_a_candidate():
    assert detect_groups([a_part(1, offset_seconds=0)], a_rule()) == []


def test_a_disabled_rule_detects_nothing():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1502)]
    assert detect_groups(parts, a_rule(enabled=False)) == []
