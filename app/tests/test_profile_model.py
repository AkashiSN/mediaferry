import json

import pytest

from mediaferry.core.profiles.model import (
    ProfileInvalid,
    definition_to_json,
    load_builtin_definitions,
    parse_definition,
)


def a_definition(**over):
    data = {
        "slug": "dji-osmo",
        "name": "DJI Osmo Pocket",
        "hints": {"usb_ids": ["2ca3:*"], "volume_labels": ["SD_Card"]},
        "require": {
            "roots": ["DCIM", "PANORAMA"],
            "filename_pattern": r"^DJI_\d{14}_\d{4}_D\.(MP4|JPG)$",
            "min_matching_files": 1,
        },
        "scan": {"roots": ["DCIM", "PANORAMA"], "extensions": ["MP4", "JPG"]},
        "timestamp": {
            "source": "filename",
            "pattern": r"^DJI_(?P<ts>\d{14})_",
            "format": "%Y%m%d%H%M%S",
            "fallback": "mtime",
            "timezone_policy": "force_offset",
            "timezone": None,
        },
        "merge": {
            "enabled": True,
            "tolerance_seconds": 5,
            "min_part_size_gib": 15,
            "sequence_pattern": r"_(?P<seq>\d{4})_D$",
            "output_name": "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4",
            "keep_streams": {"video": "primary", "audio": "all", "timecode": True, "data": False},
        },
        "immich": {
            "tags": ["DJI Osmo Pocket 4"],
            "tag_pre_existing": True,
            "fix_datetime_after_upload": True,
        },
    }
    data.update(over)
    return data


def test_a_complete_definition_parses():
    defn = parse_definition(a_definition())
    assert defn.slug == "dji-osmo"
    assert defn.scan.extensions == ("MP4", "JPG")
    assert defn.merge.keep_streams.data is False
    assert defn.timestamp.timezone is None


def test_lrf_is_excluded_because_it_is_not_in_the_extensions():
    assert "LRF" not in parse_definition(a_definition()).scan.extensions


@pytest.mark.parametrize("root", ["..", "/DCIM", "DCIM/../..", "a/b", ""])
def test_roots_must_be_single_safe_components(root):
    """マウントルートの外へ抜ける経路を定義から作らせない."""
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(scan={"roots": [root], "extensions": ["MP4"]}))


def test_output_name_cannot_contain_a_path_separator():
    merged = a_definition()["merge"] | {"output_name": "../evil.MP4"}
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(merge=merged))


def test_broken_regexes_are_rejected_at_parse_time():
    require = a_definition()["require"] | {"filename_pattern": "^DJI_(unclosed"}
    with pytest.raises(ProfileInvalid, match="filename_pattern"):
        parse_definition(a_definition(require=require))


def test_slug_is_restricted_to_path_safe_characters():
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(slug="../etc"))
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(slug="DJI Osmo"))


def test_timezone_policy_is_constrained():
    ts = a_definition()["timestamp"] | {"timezone_policy": "guess"}
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(timestamp=ts))


def test_a_filename_source_needs_a_pattern_and_a_format():
    ts = a_definition()["timestamp"] | {"pattern": None}
    with pytest.raises(ProfileInvalid, match="pattern"):
        parse_definition(a_definition(timestamp=ts))
    # format が無いと、取り出した ts をどう読むかが決まらない
    ts = a_definition()["timestamp"] | {"format": None}
    with pytest.raises(ProfileInvalid, match="format"):
        parse_definition(a_definition(timestamp=ts))


def test_the_pattern_must_capture_ts():
    ts = a_definition()["timestamp"] | {"pattern": r"^DJI_(\d{14})_"}
    with pytest.raises(ProfileInvalid, match="ts"):
        parse_definition(a_definition(timestamp=ts))


def test_unknown_keys_are_rejected():
    """綴りを間違えた設定が黙って無視されると、効いていない設定に気づけない."""
    with pytest.raises(ProfileInvalid, match="tolerance_second"):
        parse_definition(a_definition(merge=a_definition()["merge"] | {"tolerance_second": 5}))


def test_json_round_trip_is_stable():
    defn = parse_definition(a_definition())
    once = definition_to_json(defn)
    assert parse_definition(json.loads(once)) == defn
    assert definition_to_json(parse_definition(json.loads(once))) == once


def test_the_json_keys_are_sorted_at_every_level():
    """リビジョンの差分検出に使うので、順序は内容だけで決まる必要がある.

    dataclass のフィールドを並べ替えただけで JSON が変わると、中身が同じ
    プロファイルが変更扱いになって無意味なリビジョンが増える。
    """
    loaded = json.loads(definition_to_json(parse_definition(a_definition())))

    def assert_sorted(node):
        if isinstance(node, dict):
            assert list(node) == sorted(node)
            for value in node.values():
                assert_sorted(value)

    assert_sorted(loaded)


def test_the_builtin_dji_profile_is_valid_and_has_no_local_timezone():
    """地域固定の値をリポジトリに含めない。TZ は設定で与える（§12.2）."""
    builtins = {d.slug: d for d in load_builtin_definitions()}
    assert "dji-osmo" in builtins
    assert builtins["dji-osmo"].timestamp.timezone is None
    assert builtins["dji-osmo"].timestamp.timezone_policy == "force_offset"


# ----------------------------------------------------------------------
# 正規表現（Task 4）


def test_a_broken_regex_is_rejected():
    with pytest.raises(ProfileInvalid, match="正規表現"):
        parse_definition(
            a_definition(require={**a_definition()["require"], "filename_pattern": "("})
        )


def test_a_very_long_regex_is_rejected():
    """長さの上限は残す. 上限だけでは足りないが、無いよりはよい."""
    with pytest.raises(ProfileInvalid, match="長すぎ"):
        parse_definition(
            a_definition(require={**a_definition()["require"], "filename_pattern": "a" * 5000})
        )


def test_merge_can_omit_the_sequence_pattern_when_it_is_disabled():
    """`canon-eos` と `generic-dcim` は結合を持たない.

    無効なのに連番の規則と出力名を書かせると、意味の無い値を発明することになる。
    """
    merge = {
        "enabled": False,
        "tolerance_seconds": 5,
        "min_part_size_gib": 15,
        "keep_streams": {"video": "primary", "audio": "all", "timecode": True, "data": False},
    }
    defn = parse_definition(a_definition(merge=merge))
    assert defn.merge.enabled is False
    assert defn.merge.sequence_pattern == ""
    assert defn.merge.output_name == ""


def test_merge_still_requires_the_sequence_pattern_when_it_is_enabled():
    merge = {
        "enabled": True,
        "tolerance_seconds": 5,
        "min_part_size_gib": 15,
        "keep_streams": {"video": "primary", "audio": "all", "timecode": True, "data": False},
    }
    with pytest.raises(ProfileInvalid):
        parse_definition(a_definition(merge=merge))


# --- stack（Phase 6 / §6） ---------------------------------------------


def test_stack_is_optional_so_old_revisions_still_parse():
    """**既存リビジョンの JSON には `stack` が無い。** 必須にすると DB が開けない。"""
    defn = parse_definition(a_definition())
    assert defn.stack.enabled is False
    assert defn.stack.extensions == ()
    assert defn.stack.tolerance_seconds == 0


def test_stack_extensions_must_be_upper_and_dotless():
    with pytest.raises(ProfileInvalid, match="ドット無しの大文字"):
        parse_definition(
            a_definition(
                scan={"roots": ["DCIM"], "extensions": ["JPG", "CR2"]},
                stack={"enabled": True, "extensions": [".jpg", "CR2"], "tolerance_seconds": 0},
            )
        )


def test_stack_needs_at_least_two_extensions():
    """1 つでは組にならない（自分としか当たらない）。"""
    with pytest.raises(ProfileInvalid, match="2 つ以上"):
        parse_definition(
            a_definition(
                scan={"roots": ["DCIM"], "extensions": ["JPG"]},
                stack={"enabled": True, "extensions": ["JPG"], "tolerance_seconds": 0},
            )
        )


def test_stack_extensions_must_not_repeat():
    with pytest.raises(ProfileInvalid, match="重複"):
        parse_definition(
            a_definition(
                scan={"roots": ["DCIM"], "extensions": ["JPG"]},
                stack={"enabled": True, "extensions": ["JPG", "JPG"], "tolerance_seconds": 0},
            )
        )


def test_stack_extensions_must_be_scanned():
    """**取り込まない拡張子は組にならない。** 書き間違いを早く教える。"""
    with pytest.raises(ProfileInvalid, match="scan.extensions に無い"):
        parse_definition(
            a_definition(
                scan={"roots": ["DCIM"], "extensions": ["JPG"]},
                stack={"enabled": True, "extensions": ["JPG", "CR2"], "tolerance_seconds": 0},
            )
        )


def test_the_tolerance_must_not_be_negative():
    with pytest.raises(ProfileInvalid, match="0 以上"):
        parse_definition(
            a_definition(
                scan={"roots": ["DCIM"], "extensions": ["JPG", "CR2"]},
                stack={"enabled": True, "extensions": ["JPG", "CR2"], "tolerance_seconds": -1},
            )
        )


def test_a_disabled_stack_does_not_require_the_rest():
    """`merge.enabled: false` と同じ扱い（使われない値を発明させない）。"""
    assert parse_definition(a_definition(stack={"enabled": False})).stack.enabled is False


def test_an_unknown_key_in_stack_is_rejected():
    with pytest.raises(ProfileInvalid, match="stack に未知のキー"):
        parse_definition(a_definition(stack={"enabled": False, "pattern": "x"}))


def test_an_enabled_stack_keeps_the_order_of_the_extensions():
    """**先頭が primary。** 順序は規則の一部なので落とさない。"""
    defn = parse_definition(
        a_definition(
            scan={"roots": ["DCIM"], "extensions": ["JPG", "CR2"]},
            stack={"enabled": True, "extensions": ["CR2", "JPG"], "tolerance_seconds": 0},
        )
    )
    assert defn.stack.extensions == ("CR2", "JPG")


def test_canon_eos_stacks_jpg_and_cr2():
    canon = {d.slug: d for d in load_builtin_definitions()}["canon-eos"]
    assert canon.stack.enabled is True
    assert canon.stack.extensions == ("JPG", "CR2")
    assert canon.stack.tolerance_seconds == 0


def test_the_other_builtins_do_not_stack():
    builtins = {d.slug: d for d in load_builtin_definitions()}
    assert builtins["dji-osmo"].stack.enabled is False
    assert builtins["generic-dcim"].stack.enabled is False


def test_the_stack_rule_is_part_of_the_normal_form():
    """`definition_to_json` は差分検出に使う。**規則が入っていなければ版が進まない。**"""
    body = json.loads(
        definition_to_json(
            parse_definition(
                a_definition(
                    scan={"roots": ["DCIM"], "extensions": ["JPG", "CR2"]},
                    stack={"enabled": True, "extensions": ["JPG", "CR2"], "tolerance_seconds": 0},
                )
            )
        )
    )
    assert body["stack"] == {"enabled": True, "extensions": ["JPG", "CR2"], "tolerance_seconds": 0}
