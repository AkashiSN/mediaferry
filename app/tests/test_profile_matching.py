from mediaferry.core.profiles.matching import VolumeFacts, hint_score, resolve_profile
from mediaferry.core.profiles.model import parse_definition

from .test_profile_model import a_definition


class DictTree:
    """dirfd の代わり. core は OS を知らない."""

    def __init__(self, contents):
        self._contents = contents

    def has_root(self, name):
        return name in self._contents

    def iter_names(self, root, limit):
        return list(self._contents.get(root, []))[:limit]


def dji():
    return parse_definition(a_definition())


def generic():
    data = a_definition(
        slug="generic-dcim",
        name="Generic DCIM",
        hints={"usb_ids": [], "volume_labels": []},
        require={
            "roots": ["DCIM"],
            "filename_pattern": r".*\.(MP4|JPG|JPEG|MOV)$",
            "min_matching_files": 1,
        },
    )
    return parse_definition(data)


def specific(slug, usb_ids=(), labels=()):
    """generic と同じ中身に一致する専用プロファイル.

    slug は順位規則の検証用に指定する。アルファベット順で偶然正解になると、
    規則が効いているかが分からない。
    """
    data = a_definition(
        slug=slug,
        name=slug,
        hints={"usb_ids": list(usb_ids), "volume_labels": list(labels)},
        require={
            "roots": ["DCIM"],
            "filename_pattern": r".*\.JPG$",
            "min_matching_files": 1,
        },
    )
    return parse_definition(data)


def dji_facts(**over):
    fields = {"usb_vendor_id": "2ca3", "usb_product_id": "0020", "fs_label": "SD_Card"}
    fields.update(over)
    return VolumeFacts(**fields)


def test_hints_rank_candidates_but_do_not_confirm():
    assert hint_score(dji(), dji_facts()) > hint_score(generic(), dji_facts())
    # hints だけ一致しても中身が空なら確定しない
    outcome = resolve_profile([dji(), generic()], dji_facts(), DictTree({}))
    assert outcome.slug is None


def test_content_confirms_the_profile():
    tree = DictTree({"DCIM": ["DJI_20260817120000_0001_D.MP4"]})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree)
    assert outcome.slug == "dji-osmo"
    assert outcome.provisional is False


def test_usb_ids_alone_never_confirm():
    """USB ID だけで確定させる経路を塞ぐ。中身は他機種のもの."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree)
    assert outcome.slug == "generic-dcim"


def test_an_empty_dcim_is_a_provisional_match_with_low_confidence():
    """Osmo の内蔵ストレージは DCIM を持つが空だった（Phase 0 実測）."""
    tree = DictTree({"DCIM": [], "PANORAMA": []})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree)
    assert outcome.slug == "dji-osmo"
    assert outcome.provisional is True
    assert "空" in outcome.reason


def test_an_empty_tree_without_matching_hints_is_out_of_scope():
    tree = DictTree({"DCIM": []})
    outcome = resolve_profile(
        [dji(), generic()], dji_facts(usb_vendor_id="abcd", fs_label="BACKUP"), tree
    )
    assert outcome.slug is None
    assert outcome.provisional is False


def test_falls_back_to_generic_dcim():
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    outcome = resolve_profile([dji(), generic()], VolumeFacts("1234", "5678", "PHONE"), tree)
    assert outcome.slug == "generic-dcim"


def test_a_remembered_profile_is_still_revalidated():
    """記憶を無条件に信用しない。中身が変わっていれば別のプロファイルになる."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    outcome = resolve_profile([dji(), generic()], dji_facts(), tree, remembered_slug="dji-osmo")
    assert outcome.slug == "generic-dcim"


def test_a_remembered_profile_wins_ties():
    """hints も中身も同じなら、前回と同じ判定を続ける.

    slug 順で決まってしまうと、プロファイルを 1 つ増やしただけで既存カードの
    判定が入れ替わる。
    """
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    both = [specific("aa-first"), specific("zz-second")]
    facts = VolumeFacts("1234", "5678", "PHONE")
    assert resolve_profile(both, facts, tree, remembered_slug="zz-second").slug == "zz-second"
    assert resolve_profile(both, facts, tree, remembered_slug="aa-first").slug == "aa-first"


def test_hints_break_a_tie_between_two_matching_profiles():
    """hints は確定させないが、どちらも一致するときの順位は決める."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    both = [specific("aa-first"), specific("zz-second", labels=["PHONE"])]
    assert resolve_profile(both, VolumeFacts("1234", "5678", "PHONE"), tree).slug == "zz-second"


def test_the_generic_fallback_loses_to_a_specific_profile():
    """generic は最後の手段。slug 順で先に来ても専用プロファイルを押しのけない."""
    tree = DictTree({"DCIM": ["IMG_0001.JPG"]})
    both = [generic(), specific("zz-second")]
    assert resolve_profile(both, VolumeFacts("1234", "5678", "PHONE"), tree).slug == "zz-second"


def test_usb_id_globs_match_any_product():
    # ラベルを外し、スコアが USB の glob だけから来ることを確かめる
    assert hint_score(dji(), dji_facts(usb_product_id="9999", fs_label="OTHER")) > 0
    assert hint_score(dji(), dji_facts(usb_vendor_id="ffff", fs_label="OTHER")) == 0
