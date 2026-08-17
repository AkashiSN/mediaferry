"""結合結果の名前と置き場所.

`output_name` はプロファイルの値で、置換するのは `{ts}` `{first_seq}`
`{last_seq}` の 3 つだけにする。`str.format` を使わないのは、テンプレートから
値の属性を辿れてしまうため。置換できるキーをここで閉じる。

置き場所はカード上の階層を保つ（§7）。ユーザが NAS を直接開いて
`library/` と `derived/` を読み比べられることを保証する。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from ..naming import UnsafePath, library_rel_path, safe_source_rel_path
from ..profiles.model import MergeRule
from .grouping import MergePart

TS_FORMAT = "%Y%m%d%H%M%S"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class MergeOutputUndefined(ValueError):
    """出力名を決められない（連番が読めない、未知のプレースホルダ、範囲外のパス）."""


def merged_rel_path(profile_slug: str, rule: MergeRule, members: Sequence[MergePart]) -> str:
    first, last = members[0], members[-1]
    name = _render(
        rule.output_name,
        {
            # captured_at はオフセット付き。UTC へ直さず、そのままの壁時計を使う。
            "ts": first.captured_at.strftime(TS_FORMAT),
            "first_seq": _sequence(rule, first.rel_path),
            "last_seq": _sequence(rule, last.rel_path),
        },
    )
    parent = _source_parent(profile_slug, first.rel_path)
    return library_rel_path("derived", profile_slug, str(parent / name))


def _sequence(rule: MergeRule, rel_path: str) -> str:
    match = re.search(rule.sequence_pattern, PurePosixPath(rel_path).stem)
    if match is None:
        raise MergeOutputUndefined(f"連番が読めない: {rel_path}")
    try:
        return match.group("seq")
    except IndexError as exc:
        raise MergeOutputUndefined("sequence_pattern に seq グループが無い") from exc


def _source_parent(profile_slug: str, rel_path: str) -> PurePosixPath:
    path = PurePosixPath(rel_path)
    prefix = PurePosixPath("library") / profile_slug
    if prefix not in path.parents:
        raise MergeOutputUndefined(f"ライブラリの外のパス: {rel_path}")
    return path.parent.relative_to(prefix)


def _render(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise MergeOutputUndefined(f"未知のプレースホルダ: {{{key}}}")
        return values[key]

    rendered = _PLACEHOLDER.sub(replace, template)
    try:
        checked = safe_source_rel_path(rendered)
    except UnsafePath as exc:
        raise MergeOutputUndefined(str(exc)) from exc
    if "/" in checked:
        raise MergeOutputUndefined(f"出力名が単一の構成要素ではない: {rendered}")
    return checked
