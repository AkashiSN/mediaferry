"""ボリュームごとのプロファイル判定.

`hints` は候補の順位付けにのみ使い、単独では確定させない。確定は必ず
マウント先の中身が `require` を満たすことで行う。USB ID だけで確定すると、
同じ ID の別機種や、他人のカードを誤って取り込む経路になる。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Protocol

from .model import ProfileDefinition
from .patterns import PatternTimeout, compile_pattern, match

# require の確認で読むファイル名の上限。数万件のカードで全件読まない。
NAME_SCAN_LIMIT = 2000

GENERIC_SLUG = "generic-dcim"


@dataclass(frozen=True)
class VolumeFacts:
    usb_vendor_id: str
    usb_product_id: str
    fs_label: str


class SourceTree(Protocol):
    """ボリュームの中身への読み取り専用の窓."""

    def has_root(self, name: str) -> bool: ...

    def iter_names(self, root: str, limit: int) -> Iterable[str]: ...


@dataclass(frozen=True)
class MatchOutcome:
    """プロファイル判定の結果.

    **ボリュームの同定確度 (`identity_confidence`) はここに含めない。**
    「中身がプロファイルに一致したか」と「前回と同じカードか」は別の問いで、
    混ぜると、同じ UUID の別カードが DJI のファイルを持っているだけで
    信頼を引き継いでしまう。
    """

    slug: str | None
    provisional: bool
    reason: str


def hint_score(defn: ProfileDefinition, facts: VolumeFacts) -> int:
    """一致した hint の数. 0 なら順位付けに寄与しない."""
    score = 0
    usb = f"{facts.usb_vendor_id}:{facts.usb_product_id}".lower()
    if any(fnmatch(usb, pattern.lower()) for pattern in defn.hints.usb_ids):
        score += 1
    if any(facts.fs_label == label for label in defn.hints.volume_labels):
        score += 1
    return score


def resolve_profile(
    definitions: Sequence[ProfileDefinition],
    facts: VolumeFacts,
    tree: SourceTree,
    remembered_slug: str | None = None,
) -> MatchOutcome:
    """中身の検証を通った最初のプロファイルを採用する."""
    ordered = sorted(
        definitions,
        key=lambda d: (
            -hint_score(d, facts),
            0 if d.slug == remembered_slug else 1,
            1 if d.slug == GENERIC_SLUG else 0,
            d.slug,
        ),
    )
    provisional: MatchOutcome | None = None
    for defn in ordered:
        roots = [root for root in defn.require.roots if tree.has_root(root)]
        if not roots:
            continue
        try:
            matches = _count_matching_files(defn, tree, roots)
        except PatternTimeout as exc:
            # **黙って不一致にしない。** 原因が画面から分からなくなる。
            # このプロファイルは候補から外し、次を試す。
            return MatchOutcome(slug=None, provisional=False, reason=f"照合を打ち切った: {exc}")
        if matches >= defn.require.min_matching_files:
            return MatchOutcome(
                slug=defn.slug,
                provisional=False,
                reason=f"{', '.join(roots)} に一致するファイルが {matches} 件",
            )
        if provisional is None and hint_score(defn, facts) > 0:
            # 中身が空でも正当なボリュームがある（まだ撮影していない内蔵ストレージ）。
            # 対象だと分かるように残すが、自動取り込みの対象にはしない。
            provisional = MatchOutcome(
                slug=defn.slug,
                provisional=True,
                reason=f"{', '.join(roots)} はあるが一致するファイルが無い（空）",
            )
    if provisional is not None:
        return provisional
    return MatchOutcome(slug=None, provisional=False, reason="対象外")


def _count_matching_files(defn: ProfileDefinition, tree: SourceTree, roots: Sequence[str]) -> int:
    pattern = compile_pattern(defn.require.filename_pattern)
    found = 0
    for root in roots:
        for name in tree.iter_names(root, NAME_SCAN_LIMIT):
            if match(pattern, name):
                found += 1
                if found >= defn.require.min_matching_files:
                    return found
    return found
