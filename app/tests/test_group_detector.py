from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mediaferry.core.merge.digest import input_digest
from mediaferry.core.merge.grouping import GIB
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.detect_groups import GroupDetector

from .test_schema_artifacts import a_media_file, a_merge_group

BASE = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


@pytest.fixture
def ctx(db):
    store = JobStore(db)
    store.enqueue("detect_groups", {})
    return store.claim_next()


def a_part(
    db,
    profile,
    index,
    *,
    offset_seconds,
    duration=1500.0,
    size=16 * GIB,
    probe_state="ok",
    role="original",
    kind="video",
    missing_at=None,
):
    return a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role=role,
        kind=kind,
        rel_path=f"library/dji-osmo/DCIM/DJI_001/DJI_20260817143000_{index:04d}_D.MP4",
        sha1=f"{index:040d}",
        size_bytes=size,
        duration_seconds=duration,
        probe_state=probe_state,
        captured_at=(BASE + timedelta(seconds=offset_seconds)).isoformat(),
        missing_at=missing_at,
    )


def test_two_consecutive_parts_become_a_group(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    outcome = GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    assert outcome.created == 1
    groups = MergeRepository(db).list_groups()
    assert len(groups) == 1
    assert [row["position"] for row in MergeRepository(db).members(groups[0]["id"])] == [0, 1]


def test_running_twice_does_not_duplicate_the_group(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    detector = GroupDetector(db, MergeRepository(db))
    detector.run(ctx, profile)
    outcome = detector.run(ctx, profile)
    assert outcome.created == 0
    assert len(MergeRepository(db).list_groups()) == 1


def test_a_file_already_in_a_group_is_a_boundary(db, profile, ctx):
    # 1-2 が既にグループ化されている状態で 3 が来ても、2 と 3 はつながらない。
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    detector = GroupDetector(db, MergeRepository(db))
    detector.run(ctx, profile)
    a_part(db, profile, 3, offset_seconds=3004)
    outcome = detector.run(ctx, profile)
    assert outcome.created == 0
    assert len(MergeRepository(db).list_groups()) == 1


def test_a_taken_file_between_two_free_ones_does_not_join_them(db, profile, ctx):
    """境界は「列から取り除く」ではない.

    取り除くだけだと、その前後がつながって別の録画が 1 つのグループになる。
    """
    a_part(db, profile, 1, offset_seconds=0)
    taken = a_part(db, profile, 2, offset_seconds=1501)
    a_part(db, profile, 3, offset_seconds=1502)
    other = a_part(db, profile, 9, offset_seconds=100_000)
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-taken")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group_id, taken))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group_id, other))

    outcome = GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    assert outcome.created == 0


def test_a_photo_between_two_parts_does_not_break_the_group(db, profile, ctx):
    """写真は候補の列に入れない.

    入れると duration を持たないので境界になり、その前後の分割録画が
    検出されなくなる。
    """
    a_part(db, profile, 1, offset_seconds=0)
    a_part(
        db,
        profile,
        2,
        offset_seconds=1501,
        kind="photo",
        duration=None,
        probe_state="not_applicable",
    )
    a_part(db, profile, 3, offset_seconds=1502)
    assert GroupDetector(db, MergeRepository(db)).run(ctx, profile).created == 1


def test_derived_files_are_never_parts(db, profile, ctx):
    # 結合物どうしを結合しない。derived を候補に入れると連鎖する。
    a_part(db, profile, 1, offset_seconds=0, role="derived")
    a_part(db, profile, 2, offset_seconds=1502, role="derived")
    assert GroupDetector(db, MergeRepository(db)).run(ctx, profile).created == 0


def test_the_stored_digest_covers_the_profile_revision(db, profile, ctx):
    # digest はプロファイルリビジョンまで含めて作る（§8）。
    first = a_part(db, profile, 1, offset_seconds=0)
    second = a_part(db, profile, 2, offset_seconds=1502)
    GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    group = MergeRepository(db).list_groups()[0]
    assert group["input_digest"] == input_digest(
        [(first, f"{1:040d}"), (second, f"{2:040d}")],
        profile.definition.merge,
        profile.revision_id,
    )


def test_a_disabled_profile_says_why(db, profile, ctx):
    # 何も起きない理由を画面へ出す。黙って 0 件を返さない。
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    definition = replace(profile.definition, merge=replace(profile.definition.merge, enabled=False))
    GroupDetector(db, MergeRepository(db)).run(ctx, replace(profile, definition=definition))
    messages = [row["message"] for row in JobStore(db).events(ctx.job_id)]
    assert any("結合しない" in message for message in messages)


def test_photos_and_derived_files_are_not_parts(db, profile, ctx):
    a_part(
        db,
        profile,
        1,
        offset_seconds=0,
        kind="photo",
        duration=None,
        probe_state="not_applicable",
    )
    a_part(db, profile, 2, offset_seconds=1502, role="derived")
    assert GroupDetector(db, MergeRepository(db)).run(ctx, profile).created == 0


def test_a_missing_file_is_not_a_part(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0, missing_at=BASE.isoformat())
    a_part(db, profile, 2, offset_seconds=1502)
    assert GroupDetector(db, MergeRepository(db)).run(ctx, profile).created == 0


def test_the_parts_are_ordered_by_the_capture_time_not_the_name(db, profile, ctx):
    # 名前と時刻の順が逆になっているデータで、時刻の順に並ぶことを確かめる。
    a_part(db, profile, 2, offset_seconds=0)
    a_part(db, profile, 1, offset_seconds=1502)
    GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    repo = MergeRepository(db)
    group = repo.list_groups()[0]
    assert [row["rel_path"].split("_")[-2] for row in repo.members(group["id"])] == [
        "0002",
        "0001",
    ]


def test_a_candidate_without_a_readable_sequence_is_reported_not_stored(db, profile, ctx):
    for index in (1, 2):
        a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/PANO_{index:04d}.MP4",
            sha1=f"{index:040d}",
            size_bytes=16 * GIB,
            duration_seconds=1500.0,
            captured_at=(BASE + timedelta(seconds=1502 * (index - 1))).isoformat(),
        )
    outcome = GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    assert outcome.created == 0
    assert outcome.undefined == 1
    assert MergeRepository(db).list_groups() == []


def test_a_disabled_profile_detects_nothing(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    definition = replace(profile.definition, merge=replace(profile.definition.merge, enabled=False))
    disabled = replace(profile, definition=definition)
    assert GroupDetector(db, MergeRepository(db)).run(ctx, disabled).created == 0


def test_the_preview_does_not_store_anything(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    candidates = GroupDetector(db, MergeRepository(db)).preview(profile, profile.definition.merge)
    assert len(candidates) == 1
    assert MergeRepository(db).list_groups() == []


def test_the_preview_uses_the_thresholds_it_is_given(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    strict = replace(profile.definition.merge, tolerance_seconds=1)
    assert GroupDetector(db, MergeRepository(db)).preview(profile, strict) == []
