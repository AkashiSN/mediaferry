from datetime import timedelta

from mediaferry.core.profiles.model import ImmichRule
from mediaferry.core.uploads.decisions import (
    datetime_plan,
    instant,
    origin_after_upload,
    same_instant,
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


# ------------------------------------------------------- 同じ瞬間かどうか
#
# **Immich は日時を UTC へ正規化して返す。** `+09:00` で書いた値は `+00:00` の表記で
# 戻るので、文字列で比べると同じ瞬間が常に「違う」になる。承認を待つかどうかも、
# 画面に出す `identical` も、この 1 つの判定で決める（2 か所に書くと片方だけ直したとき
# 画面と状態機械が食い違う）。


def test_the_same_instant_written_in_two_offsets_is_the_same():
    assert same_instant("2026-08-31T20:06:12+09:00", "2026-08-31T11:06:12+00:00") is True


def test_one_second_apart_is_not_the_same():
    assert same_instant("2026-08-31T20:06:12+09:00", "2026-08-31T20:06:13+09:00") is False


def test_an_unknown_current_value_is_not_the_same():
    """**「分からない」は「変更なし」ではない**（承認を飛ばさせない）."""
    assert same_instant(None, "2026-08-31T20:06:12+09:00") is False


def test_a_missing_proposal_is_not_the_same():
    assert same_instant("2026-08-31T20:06:12+09:00", None) is False


def test_two_unknowns_are_not_the_same():
    assert same_instant(None, None) is False


def test_an_unreadable_value_is_not_the_same():
    assert same_instant("いつか", "2026-08-31T20:06:12+09:00") is False


def test_a_value_without_an_offset_is_not_the_same():
    """オフセットが無いと瞬間が決まらない（どの地の 20:06 か分からない）."""
    assert same_instant("2026-08-31T20:06:12", "2026-08-31T20:06:12+09:00") is False


def test_the_instant_of_an_unreadable_value_is_none():
    assert instant("いつか") is None
    assert instant(None) is None
    assert instant("2026-08-31T20:06:12") is None


def test_the_instant_keeps_the_offset_it_was_written_with():
    """**画面が壁時計を切り出せるように、書かれたオフセットを保つ.**"""
    parsed = instant("2026-08-31T20:06:12+09:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=9)
