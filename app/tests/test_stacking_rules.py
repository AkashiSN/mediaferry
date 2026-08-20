"""組を決める規則（§6・§9.11）.

**判断だけを持つ純関数**なので、4 条件を 1 つずつ壊す筋書きが書ける。
"""

from dataclasses import replace

import pytest

from mediaferry.core.profiles.model import StackRule
from mediaferry.core.uploads.stacking import (
    Candidate,
    Group,
    Refusal,
    extension_of,
    resolve_group,
    stem_prefix,
)

RULE = StackRule(enabled=True, extensions=("JPG", "CR2"), tolerance_seconds=0)


def a_candidate(rel_path, **over):
    """既定は「送り終わった、自分が上げた、同じカードの同じ時刻」の観測.

    **既定と一致するだけで通るテストを書かない。** 壊す条件を 1 つずつ渡す。
    """
    base = {
        "record_id": f"rec-{rel_path}",
        "media_file_id": f"media-{rel_path}",
        "profile_id": "profile-1",
        "volume_instance_id": "volume-1",
        "rel_path": rel_path,
        "captured_at": "2026-08-19T10:30:00+09:00",
        "captured_at_source": "exif",
        "origin": "created_by_us",
        "state": "complete",
        "remote_asset_id": f"asset-{rel_path}",
        "invalidated": False,
    }
    base.update(over)
    return Candidate(**base)


def a_jpg(**over):
    return a_candidate("DCIM/100CANON/IMG_1234.JPG", media_file_id="media-jpg", **over)


def a_cr2(**over):
    rel_path = over.pop("rel_path", "DCIM/100CANON/IMG_1234.CR2")
    return a_candidate(rel_path, media_file_id="media-cr2", **over)


def test_a_pair_on_the_same_card_forms_a_group():
    group = resolve_group(a_jpg(), [a_jpg(), a_cr2()], RULE)
    assert isinstance(group, Group)
    # **先頭の拡張子が primary**（`extensions` の順序が規則の一部）。
    assert [m.rel_path for m in group.members] == [
        "DCIM/100CANON/IMG_1234.JPG",
        "DCIM/100CANON/IMG_1234.CR2",
    ]


def test_the_primary_follows_the_rule_not_the_caller():
    """どの member から評価しても同じ組・同じ primary になる."""
    rule = replace(RULE, extensions=("CR2", "JPG"))
    group = resolve_group(a_cr2(), [a_jpg(), a_cr2()], rule)
    assert group.members[0].rel_path.endswith(".CR2")


def test_a_disabled_rule_refuses_everything():
    refusal = resolve_group(a_jpg(), [a_jpg(), a_cr2()], replace(RULE, enabled=False))
    assert "スタックを使わない" in refusal.reason


def test_an_extension_outside_the_rule_is_not_a_target():
    movie = a_candidate("DCIM/100CANON/MVI_1234.MOV", media_file_id="media-mov")
    assert "対象ではない" in resolve_group(movie, [movie], RULE).reason


def test_a_lonely_file_is_refused():
    assert "相方が見つからない" in resolve_group(a_jpg(), [a_jpg()], RULE).reason


def test_a_different_stem_is_not_a_partner():
    other = a_cr2(rel_path="DCIM/100CANON/IMG_9999.CR2")
    assert isinstance(resolve_group(a_jpg(), [a_jpg(), other], RULE), Refusal)


def test_a_partner_in_another_directory_is_not_a_partner():
    other = a_cr2(rel_path="DCIM/101CANON/IMG_1234.CR2")
    assert isinstance(resolve_group(a_jpg(), [a_jpg(), other], RULE), Refusal)


def test_a_partner_observed_only_on_another_card_is_refused():
    """連番が一周した別カードとの誤結合を閉じる（§6）."""
    other_card = a_cr2(volume_instance_id="volume-2")
    assert isinstance(resolve_group(a_jpg(), [a_jpg(), other_card], RULE), Refusal)


def test_the_volume_and_the_stem_must_match_in_the_same_observation():
    """**平坦化した集合では通ってしまう組を閉じる。**

    JPG は (volume-1, A.) と (volume-2, B.)、CR2 は (volume-1, C.) と (volume-3, B.)。
    ボリュームの集合は volume-1 で交わり、stem の集合は B. で交わるが、
    **同じ観測では一度も一致しない**。
    """
    jpg = [
        a_candidate("DCIM/100CANON/A.JPG", media_file_id="m-jpg", volume_instance_id="volume-1"),
        a_candidate("DCIM/100CANON/B.JPG", media_file_id="m-jpg", volume_instance_id="volume-2"),
    ]
    cr2 = [
        a_candidate("DCIM/100CANON/C.CR2", media_file_id="m-cr2", volume_instance_id="volume-1"),
        a_candidate("DCIM/100CANON/B.CR2", media_file_id="m-cr2", volume_instance_id="volume-3"),
    ]
    assert isinstance(resolve_group(jpg[0], [*jpg, *cr2], RULE), Refusal)


def test_the_same_observation_matching_both_does_pair():
    """**上の裏。** 同じ観測でボリュームと stem が揃えば、それは同じシャッター。"""
    jpg = [
        a_candidate("DCIM/100CANON/A.JPG", media_file_id="m-jpg", volume_instance_id="volume-1"),
        a_candidate("DCIM/100CANON/B.JPG", media_file_id="m-jpg", volume_instance_id="volume-2"),
    ]
    cr2 = [a_candidate("DCIM/100CANON/B.CR2", media_file_id="m-cr2", volume_instance_id="volume-2")]
    assert isinstance(resolve_group(jpg[0], [*jpg, *cr2], RULE), Group)


def test_a_media_observed_twice_appears_once_in_the_group():
    """**同じ資産を 2 回送らない**（2 回記録もしない）."""
    twice = [a_cr2(), a_cr2(record_id="rec-dup")]
    group = resolve_group(a_jpg(), [a_jpg(), *twice], RULE)
    assert len(group.members) == 2


def test_a_different_capture_time_is_not_a_partner():
    """**同じシャッターであることの直接の証拠**（§6）."""
    late = a_cr2(captured_at="2026-08-19T10:30:01+09:00")
    assert "撮影時刻" in resolve_group(a_jpg(), [a_jpg(), late], RULE).reason


def test_the_tolerance_is_honoured():
    late = a_cr2(captured_at="2026-08-19T10:30:01+09:00")
    group = resolve_group(a_jpg(), [a_jpg(), late], replace(RULE, tolerance_seconds=2))
    assert isinstance(group, Group)


def test_the_tolerance_is_inclusive_at_the_boundary():
    late = a_cr2(captured_at="2026-08-19T10:30:02+09:00")
    group = resolve_group(a_jpg(), [a_jpg(), late], replace(RULE, tolerance_seconds=2))
    assert isinstance(group, Group)


def test_a_different_time_source_is_not_a_partner():
    """EXIF の時刻と mtime の時刻という**別々の時計**を突き合わせない（§6）."""
    fallen_back = a_cr2(captured_at_source="mtime")
    assert "時刻の根拠" in resolve_group(a_jpg(), [a_jpg(), fallen_back], RULE).reason


def test_offsets_are_compared_as_instants_not_strings():
    """`captured_at` はオフセット付きで保存される（§8 の唯一の例外）."""
    same_instant = a_cr2(captured_at="2026-08-19T01:30:00+00:00")
    assert isinstance(resolve_group(a_jpg(), [a_jpg(), same_instant], RULE), Group)


def test_a_partner_that_is_not_ours_refuses_the_group():
    """`POST /stacks` は既存スタックを吸収する。**証明できない相手には触らない**."""
    theirs = a_cr2(origin="pre_existing")
    assert "証明できない" in resolve_group(a_jpg(), [a_jpg(), theirs], RULE).reason


def test_a_primary_that_is_not_ours_refuses_the_group():
    """**片側だけ見ない。** 自分側が pre_existing でも束ねない."""
    assert (
        "証明できない"
        in resolve_group(a_jpg(origin="unknown"), [a_jpg(origin="unknown"), a_cr2()], RULE).reason
    )


def test_a_partner_that_is_not_complete_refuses_the_group():
    refusal = resolve_group(a_jpg(), [a_jpg(), a_cr2(state="pending")], RULE)
    assert "送信が終わっていない" in refusal.reason


def test_an_invalidated_partner_refuses_the_group():
    assert isinstance(resolve_group(a_jpg(), [a_jpg(), a_cr2(invalidated=True)], RULE), Refusal)


def test_a_partner_without_a_remote_asset_id_refuses_the_group():
    refusal = resolve_group(a_jpg(), [a_jpg(), a_cr2(remote_asset_id=None)], RULE)
    assert "資産 ID が分からない" in refusal.reason


def test_a_partner_of_another_profile_is_refused():
    """規則が 1 つに決まらない組は作らない（§9.11）."""
    assert (
        "別のプロファイル"
        in resolve_group(a_jpg(), [a_jpg(), a_cr2(profile_id="profile-2")], RULE).reason
    )


def test_the_stem_prefix_keeps_the_directory():
    assert stem_prefix("DCIM/100CANON/IMG_1234.JPG") == "DCIM/100CANON/IMG_1234."


def test_the_extension_is_upper_cased():
    """カード上の名前は小文字のこともある（`scan` の突き合わせと同じ扱い）."""
    assert extension_of("DCIM/100CANON/img_1234.jpg") == "JPG"


def test_a_lower_case_partner_still_pairs():
    lower = a_cr2(rel_path="DCIM/100CANON/IMG_1234.cr2")
    assert isinstance(resolve_group(a_jpg(), [a_jpg(), lower], RULE), Group)


@pytest.mark.parametrize("rel_path", ["DCIM/100CANON/IMG_1234", "IMG_1234.JPG"])
def test_paths_without_a_directory_or_extension_do_not_crash(rel_path):
    lonely = a_candidate(rel_path, media_file_id="m-x")
    assert isinstance(resolve_group(lonely, [lonely], RULE), Refusal)


def test_a_sibling_with_an_extension_outside_the_rule_is_not_a_partner():
    """**同じカード・同じ stem でも、規則に無い拡張子は組に入れない。**

    Canon はサムネイル（`.THM`）を同じ名前で書く。取り込みの対象にしていなくても、
    カード上の観測としては同じ stem に並ぶ。
    """
    thumbnail = a_candidate(
        "DCIM/100CANON/IMG_1234.THM", media_file_id="media-thm", volume_instance_id="volume-1"
    )
    refusal = resolve_group(a_jpg(), [a_jpg(), thumbnail], RULE)
    assert "相方が見つからない" in refusal.reason


def test_two_missing_asset_ids_are_not_reported_as_a_duplicate():
    """**「分からない」を「重なっている」と言わない。**

    再確認で両方の資産が消えると `[None, None]` になる。これは相手が同じ ID を
    返した事象ではないので、理由を取り違えない。
    """
    gone = [a_jpg(remote_asset_id=None), a_cr2(remote_asset_id=None)]
    refusal = resolve_group(gone[0], gone, RULE)
    assert "資産 ID が分からない" in refusal.reason
