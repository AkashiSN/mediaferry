import json

import pytest

from mediaferry.core.merge.digest import input_digest
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.selection import (
    INCLUDE_FAILED_GROUP_MEMBERS,
    INCLUDE_UNADOPTED_DERIVED,
    SelectionService,
)

from .test_schema_artifacts import a_media_file, a_merge_group

PASSED = json.dumps({"passed": True})
NOT_PASSED = json.dumps({"passed": False})


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


def a_group(
    db,
    profile,
    members,
    *,
    status="merged",
    verification=PASSED,
    adopted_at=None,
    output_id=None,
    digest=None,
):
    if digest is None:
        digest = input_digest(
            [(media_id, sha1) for media_id, sha1 in members],
            profile.definition.merge,
            profile.revision_id,
        )
    group_id = a_merge_group(
        db,
        (profile.profile_id, profile.revision_id),
        digest,
        status=status,
        verification_json=verification,
        adopted_at=adopted_at,
        output_media_file_id=output_id,
    )
    for position, (media_id, _) in enumerate(members):
        db.execute(
            "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
            " VALUES (?, ?, ?, 1)",
            (group_id, media_id, position),
        )
    return group_id


def a_pair(db, profile, prefix="P"):
    """rel_path は UNIQUE なので、複数のグループを作るときは prefix を変える."""
    return [
        (
            a_media_file(
                db,
                (profile.profile_id, profile.revision_id),
                rel_path=f"library/dji-osmo/DCIM/{prefix}{index}.MP4",
                sha1=f"{prefix}{index:039d}",
            ),
            f"{prefix}{index:039d}",
        )
        for index in (1, 2)
    ]


def a_derived(db, profile, name="MERGED"):
    return a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path=f"derived/dji-osmo/DCIM/{name}.MP4",
    )


def ids(result):
    return {item.media_file_id for item in result}


def test_a_plain_original_is_selectable(db, profile):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    result = SelectionService(db, ProfileRegistry(db)).selectable()
    assert ids(result) == {media_id}
    assert result[0].reason == "default"


def test_a_missing_file_is_not_selectable(db, profile):
    a_media_file(
        db, (profile.profile_id, profile.revision_id), missing_at="2026-08-17T00:00:00+00:00"
    )
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_a_member_of_an_active_group_is_not_selectable(db, profile):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id)
    assert ids(SelectionService(db, ProfileRegistry(db)).selectable()) == {output_id}


def test_a_verified_derived_output_is_selectable(db, profile):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    result = SelectionService(db, ProfileRegistry(db)).selectable()
    assert [item.merge_group_id for item in result] == [group_id]


def test_an_unadopted_failed_verification_is_not_selectable_by_default(db, profile):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id, verification=NOT_PASSED)
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_an_adopted_failed_verification_is_selectable(db, profile):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(
        db,
        profile,
        members,
        output_id=output_id,
        verification=NOT_PASSED,
        adopted_at="2026-08-17T00:00:00+00:00",
    )
    assert ids(SelectionService(db, ProfileRegistry(db)).selectable()) == {output_id}


def test_a_stale_digest_takes_the_derived_output_out_of_the_list(db, profile):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id, digest="stale-digest")
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_a_superseded_group_takes_its_output_out_of_the_list(db, profile):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    old = a_group(db, profile, members, output_id=output_id)
    newer = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-new")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, old))
    # 旧派生物は消え、構成ファイルは個別に選べるようになる。
    assert ids(SelectionService(db, ProfileRegistry(db)).selectable()) == {
        media_id for media_id, _ in members
    }


def test_a_group_that_is_not_merged_yet_hides_both_sides(db, profile):
    # 出力の実体があっても、merged になるまでは出さない（結合の途中で
    # 落ちて output_media_file_id だけが入っている状態がありうる）。
    members = a_pair(db, profile)
    a_group(db, profile, members, status="merging", output_id=a_derived(db, profile))
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_a_superseded_group_is_hidden_even_when_its_digest_matches(db, profile):
    """supersede の判定は digest の一致とは別に要る.

    member を持つグループなら trigger が active を落とすので digest 側でも
    弾けるが、それに依存すると「supersede されても出力は出さない」という
    規則が偶然成り立っているだけになる。
    """
    output_id = a_derived(db, profile)
    old = a_group(db, profile, [], output_id=output_id)
    newer = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-new")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, old))
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_failed_group_members_can_be_shown_with_a_filter(db, profile):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="failed", verification=None)
    service = SelectionService(db, ProfileRegistry(db))
    assert service.selectable() == []
    shown = service.selectable(include=[INCLUDE_FAILED_GROUP_MEMBERS])
    assert ids(shown) == {media_id for media_id, _ in members}
    assert {item.reason for item in shown} == {"failed_group_member"}


def test_skipped_group_members_can_be_shown_with_the_same_filter(db, profile):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="skipped", verification=None)
    shown = SelectionService(db, ProfileRegistry(db)).selectable(
        include=[INCLUDE_FAILED_GROUP_MEMBERS]
    )
    assert ids(shown) == {media_id for media_id, _ in members}


def test_a_string_false_is_not_a_pass(db, profile):
    """`bool("false")` は真になる. `passed` は真の bool のときだけ合格."""
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id, verification=json.dumps({"passed": "false"}))
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_the_list_is_capped(db, profile):
    for index in range(5):
        a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/C{index}.MP4",
        )
    assert len(SelectionService(db, ProfileRegistry(db)).selectable(limit=3)) == 3


def test_the_members_are_read_in_one_query(db, profile):
    """derived 1 件ごとに問い合わせない（グループが増えても query 数が伸びない）."""
    for index in range(3):
        members = a_pair(db, profile, prefix=f"G{index}")
        output_id = a_derived(db, profile, name=f"M{index}")
        a_group(db, profile, members, output_id=output_id)

    # sqlite3.Connection.execute は読み取り専用なので差し替えられない。
    # 実際に発行された SQL を trace で数える。
    calls = []
    db.set_trace_callback(calls.append)
    try:
        SelectionService(db, ProfileRegistry(db)).selectable()
    finally:
        db.set_trace_callback(None)

    assert len([sql for sql in calls if "merge_member mm" in sql and "JOIN media_file" in sql]) == 1


def test_unadopted_derived_outputs_can_be_shown_with_a_filter(db, profile):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id, verification=NOT_PASSED)
    shown = SelectionService(db, ProfileRegistry(db)).selectable(
        include=[INCLUDE_UNADOPTED_DERIVED]
    )
    assert ids(shown) == {output_id}
    assert {item.reason for item in shown} == {"unadopted_derived"}


def test_a_current_group_reports_itself_as_current(db, profile):
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    assert group_is_current(db, ProfileRegistry(db), group_id, output_id) is True


def test_a_stale_digest_is_not_current(db, profile):
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id, digest="stale-digest")
    assert group_is_current(db, ProfileRegistry(db), group_id, output_id) is False


def test_another_media_file_is_not_the_output_of_this_group(db, profile):
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    other_id = a_derived(db, profile, name="OTHER")
    group_id = a_group(db, profile, members, output_id=output_id)
    assert group_is_current(db, ProfileRegistry(db), group_id, other_id) is False


def test_a_superseded_group_is_not_current(db, profile):
    from mediaferry.db.selection import group_is_current

    output_id = a_derived(db, profile)
    old = a_group(db, profile, [], output_id=output_id)
    newer = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-new")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, old))
    assert group_is_current(db, ProfileRegistry(db), old, output_id) is False


def test_an_unknown_group_is_not_current(db, profile):
    from mediaferry.db.selection import group_is_current

    assert group_is_current(db, ProfileRegistry(db), "no-such-group", "no-such-media") is False


def test_the_list_and_the_single_check_agree(db, profile):
    """一覧の判定と claim 側の判定が食い違わない."""
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    listed = SelectionService(db, ProfileRegistry(db)).selectable()
    assert [item.media_file_id for item in listed] == [output_id]
    assert group_is_current(db, ProfileRegistry(db), group_id, output_id) is True


def test_the_digest_follows_the_member_position_not_the_insert_order(db, profile):
    from mediaferry.core.merge.digest import input_digest
    from mediaferry.db.selection import expected_digest

    first, second = a_pair(db, profile)
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "d")
    # position とは逆の順で挿入する。
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 1, 1)",
        (group_id, second[0]),
    )
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group_id, first[0]),
    )
    assert expected_digest(db, ProfileRegistry(db), group_id) == input_digest(
        [first, second], profile.definition.merge, profile.revision_id
    )
