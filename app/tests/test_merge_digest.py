from mediaferry.core.merge.digest import input_digest
from mediaferry.core.profiles.model import KeepStreams, MergeRule

MEMBERS = [("id-1", "sha-1"), ("id-2", "sha-2")]


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


def test_the_digest_is_deterministic():
    assert input_digest(MEMBERS, a_rule(), "rev-1") == input_digest(MEMBERS, a_rule(), "rev-1")


def test_the_order_of_the_members_changes_the_digest():
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(
        list(reversed(MEMBERS)), a_rule(), "rev-1"
    )


def test_a_changed_content_hash_changes_the_digest():
    other = [("id-1", "sha-1"), ("id-2", "sha-2-edited")]
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(other, a_rule(), "rev-1")


def test_a_changed_merge_setting_changes_the_digest():
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(
        MEMBERS, a_rule(tolerance_seconds=6), "rev-1"
    )


def test_a_nested_keep_streams_change_changes_the_digest():
    changed = KeepStreams(video="primary", audio="all", timecode=True, data=True)
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(
        MEMBERS, a_rule(keep_streams=changed), "rev-1"
    )


def test_a_changed_profile_revision_changes_the_digest():
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(MEMBERS, a_rule(), "rev-2")


def test_the_digest_is_a_sha256_hex_string():
    digest = input_digest(MEMBERS, a_rule(), "rev-1")
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")
