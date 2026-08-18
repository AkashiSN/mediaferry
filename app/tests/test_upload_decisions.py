from mediaferry.core.profiles.model import ImmichRule
from mediaferry.core.uploads.decisions import (
    datetime_plan,
    origin_after_upload,
    tags_to_apply,
)

CAPTURED = "2026-08-17T14:30:00+09:00"


def a_rule(**over):
    values = {
        "tags": ("mediaferry", "dji"),
        "tag_pre_existing": False,
        "fix_datetime_after_upload": True,
    }
    values.update(over)
    return ImmichRule(**values)


def test_a_created_asset_gets_the_tags():
    assert tags_to_apply(a_rule(), "created_by_us") == ("mediaferry", "dji")


def test_a_pre_existing_asset_is_not_tagged_by_default():
    assert tags_to_apply(a_rule(), "pre_existing") == ()


def test_a_pre_existing_asset_is_tagged_when_the_profile_says_so():
    assert tags_to_apply(a_rule(tag_pre_existing=True), "pre_existing") == ("mediaferry", "dji")


def test_an_unknown_origin_is_treated_like_pre_existing():
    assert tags_to_apply(a_rule(), "unknown") == ()
    assert tags_to_apply(a_rule(tag_pre_existing=True), "unknown") == ("mediaferry", "dji")


def test_a_created_status_proves_we_made_it():
    assert origin_after_upload("accept", "created") == "created_by_us"


def test_a_duplicate_after_a_reject_is_pre_existing():
    assert origin_after_upload("reject", "duplicate") == "pre_existing"


def test_a_duplicate_after_an_accept_is_unknown():
    """チェックとアップロードの間に別のクライアントが割り込みうる."""
    assert origin_after_upload("accept", "duplicate") == "unknown"


def test_a_missing_first_check_is_unknown():
    assert origin_after_upload(None, "duplicate") == "unknown"


def test_the_capture_time_is_written_back_automatically_when_we_made_it():
    plan = datetime_plan(a_rule(), "force_offset", CAPTURED, "created_by_us")
    assert plan.proposed == CAPTURED
    assert plan.automatic is True


def test_a_pre_existing_asset_needs_approval():
    plan = datetime_plan(a_rule(), "force_offset", CAPTURED, "pre_existing")
    assert plan.proposed == CAPTURED
    assert plan.automatic is False


def test_an_unknown_origin_needs_approval():
    assert datetime_plan(a_rule(), "force_offset", CAPTURED, "unknown").automatic is False


def test_no_timezone_policy_means_no_proposal():
    plan = datetime_plan(a_rule(), "none", CAPTURED, "pre_existing")
    assert plan.proposed is None
    assert plan.automatic is False


def test_a_profile_can_turn_the_correction_off():
    plan = datetime_plan(
        a_rule(fix_datetime_after_upload=False), "force_offset", CAPTURED, "created_by_us"
    )
    assert plan.proposed is None
    assert plan.reason
