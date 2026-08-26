from mediaferry.core.profiles.matching import VolumeFacts, hint_score, resolve_profile
from mediaferry.core.profiles.model import parse_definition
from mediaferry.db.profiles import ProfileRegistry

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


# ----------------------------------------------------------------------
# catastrophic backtracking（Task 4）
#
# プロファイルはユーザが書き換える。長さの上限では防げない —— `(a+)+$` は
# 8 文字で、41 文字の入力に対して事実上停止しない。しかもマッチは
# `VolumeService` の `RLock` の中で最大 2000 件のファイル名に当たるので、
# 1 本の悪い式で `GET /devices` も watcher も固まる。
#
# **実測して `regex` + `timeout` に替えた**（2026-08-19）:
#   `re` は `(a+)+$` に `"a"*40+"!"` で 10 秒でも終わらない
#   `regex` は同じ式を 0.000 秒で処理する（自前の最適化で潰す）
#   `regex` でも潰しきれない `(a|a)+$` は `timeout=` が実測どおり発火する


def a_pathological_profile(pattern: str):
    from .test_profile_model import a_definition

    return parse_definition(
        a_definition(require={**a_definition()["require"], "filename_pattern": pattern})
    )


class ManyNames:
    def has_root(self, name):
        return name == "DCIM"

    def iter_names(self, root, limit):
        return ["a" * 40 + "!" for _ in range(5)]


def test_a_pathological_pattern_gives_up_instead_of_hanging():
    """**有限時間で降りる。** 固まらないことの保証は実行時が持つ.

    **別スレッドで上限付きに走らせる。** 直に呼ぶと、`re` へ戻す回帰のときに
    テストが「失敗」ではなく**ハング**する（この試験はまさにその状態を
    再現するために書かれている）。回帰は assert で落ちなければならない。
    """
    import threading

    outcome: list = []

    def run():
        outcome.append(
            resolve_profile(
                [a_pathological_profile(r"(a|a)+$")],
                VolumeFacts("2ca3", "0020", "SD_Card"),
                ManyNames(),
            )
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=15)
    assert not worker.is_alive(), "悪性の式で固まっている（timeout が効いていない）"
    assert outcome[0].slug is None, "打ち切ったのに一致したことにしている"
    assert "打ち切" in outcome[0].reason, f"理由が分からない: {outcome[0].reason}"


def test_a_normal_pattern_is_not_affected():
    """既存のビルトインの判定は変わらない（回帰）."""
    from mediaferry.core.profiles.model import load_builtin_definitions

    builtin_dji = next(d for d in load_builtin_definitions() if d.slug == "dji-osmo")
    tree = DictTree({"DCIM": ["DJI_20260817143000_0001_D.MP4"]})
    outcome = resolve_profile([builtin_dji], VolumeFacts("2ca3", "0020", "SD_Card"), tree)
    assert outcome.slug == "dji-osmo"
    assert outcome.provisional is False


# ----------------------------------------------------------------------
# ビルトイン 2 種（Task 4）


def builtins():
    from mediaferry.core.profiles.model import load_builtin_definitions

    return load_builtin_definitions()


def a_canon_card():
    """`DCIM/100CANON/` の下に置く（`iter_names` はサブディレクトリを辿る）."""
    return DictTree({"DCIM": ["IMG_0001.JPG", "IMG_0001.CR2", "MVI_0002.MOV"]})


def test_a_canon_card_resolves_to_canon_eos():
    outcome = resolve_profile(builtins(), VolumeFacts("", "", "EOS_DIGITAL"), a_canon_card())
    assert outcome.slug == "canon-eos"
    assert outcome.provisional is False


def test_a_canon_card_resolves_even_through_a_card_reader():
    """**USB ID は当てにならない。** カードリーダー経由が前提なので、見える
    ID はリーダーのもの。ラベルも付け替えられる。中身で確定する（§6）。
    """
    outcome = resolve_profile(builtins(), VolumeFacts("058f", "6366", ""), a_canon_card())
    assert outcome.slug == "canon-eos"


def test_an_unknown_camera_falls_back_to_generic_dcim():
    tree = DictTree({"DCIM": ["ABC_1234.JPG"]})
    outcome = resolve_profile(builtins(), VolumeFacts("", "", ""), tree)
    assert outcome.slug == "generic-dcim"


def test_a_volume_without_dcim_is_out_of_scope():
    outcome = resolve_profile(builtins(), VolumeFacts("", "", ""), DictTree({"Documents": ["a"]}))
    assert outcome.slug is None


def test_a_dji_card_does_not_fall_into_generic():
    """順位付けの回帰. `generic-dcim` は最後に回す（§6）."""
    tree = DictTree({"DCIM": ["DJI_20260817143000_0001_D.MP4"]})
    outcome = resolve_profile(builtins(), VolumeFacts("2ca3", "0020", "SD_Card"), tree)
    assert outcome.slug == "dji-osmo"


def test_a_canon_card_does_not_fall_into_generic():
    outcome = resolve_profile(builtins(), VolumeFacts("", "", ""), a_canon_card())
    assert outcome.slug == "canon-eos"


def test_generic_does_not_merge():
    """機種が分からないので分割の規則も分からない（generic-dcim は不変）."""
    by_slug = {d.slug: d for d in builtins()}
    assert by_slug["generic-dcim"].merge.enabled is False


def test_the_merging_builtins_are_canon_and_dji():
    """canon-eos は実カードで確かめた 4GB 分割の規則を持つ（Task 11）."""
    by_slug = {d.slug: d for d in builtins()}
    assert by_slug["canon-eos"].merge.enabled is True
    assert by_slug["dji-osmo"].merge.enabled is True


def test_generic_does_not_rewrite_the_remote_datetime():
    """機種が分からないので、日時の解釈に介入しない（generic-dcim は不変）."""
    by_slug = {d.slug: d for d in builtins()}
    assert by_slug["generic-dcim"].timestamp.timezone_policy == "none"
    assert by_slug["generic-dcim"].immich.fix_datetime_after_upload is False


def test_canon_writes_the_remote_datetime_back():
    """canon-eos は creation_time の Z を Immich が UTC と読むので、壁時計に
    オフセットを付けて書き戻す（Task 11）。
    """
    by_slug = {d.slug: d for d in builtins()}
    assert by_slug["canon-eos"].timestamp.timezone_policy == "force_offset"
    assert by_slug["canon-eos"].immich.fix_datetime_after_upload is True


def test_generic_dcim_does_not_claim_vendor_raw():
    """汎用が RAW を拾うと、機種プロファイルを作る動機が消える."""
    by_slug = {d.slug: d for d in builtins()}
    assert "CR2" not in by_slug["generic-dcim"].scan.extensions
    assert "CR2" in by_slug["canon-eos"].scan.extensions


# ----------------------------------------------------------------------
# canon-eos の仕上げ（Task 11）


def test_canon_reads_the_time_from_exif_then_the_container(db):
    """写真は EXIF、動画は器、どちらも無ければ mtime."""
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    defn = registry.current("canon-eos").definition
    assert defn.timestamp.source == ("exif", "container", "mtime")
    assert defn.timestamp.container_semantics == "wall_clock"


def test_canon_writes_the_datetime_back_to_immich(db):
    """**Immich は creation_time の Z を素直に UTC と読む.**

    書き戻さないと動画だけが 9 時間ずれる（実機で確認）。
    `fix_datetime_after_upload` は `timezone_policy: force_offset` と
    セットでないと効かない（`datetime_plan` が policy == "none" で降りる）。
    """
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    defn = registry.current("canon-eos").definition
    assert defn.timestamp.timezone_policy == "force_offset"
    assert defn.immich.fix_datetime_after_upload is True


def test_canon_merges_at_the_four_gibibyte_split_size(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    defn = registry.current("canon-eos").definition
    assert defn.merge.enabled is True
    assert defn.merge.min_part_size_gib == 3


def test_canon_sequence_pattern_does_not_match_a_different_prefix():
    """`sequence_pattern` の錨（`^` と `$`）を落とさない.

    `IMG_0007.JPG` は元々 `MVI_` を含まないので、`^` を落としても単独では
    当たらない。`^` の必要性を実際に示すのは埋め込まれた一致
    （例: `SUBMVI_0007`）。`$` の必要性を示すのは末尾に別の桁が続く一致
    （例: `MVI_00071`）——`\\d{4}` が先頭 4 桁を拾ってしまう。
    """
    from mediaferry.core.profiles.patterns import search

    pattern = {d.slug: d for d in builtins()}["canon-eos"].merge.sequence_pattern
    assert search(pattern, "IMG_0007") is None
    assert search(pattern, "SUBMVI_0007") is None
    assert search(pattern, "MVI_00071") is None
    assert search(pattern, "MVI_0007").group("seq") == "0007"
