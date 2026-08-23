"""RAW/JPEG の組を決める規則（§6・§9.11）.

**判断だけを持つ。** DB も相手も触らないので、4 条件を 1 つずつ壊す試験が書ける。

組の同一性は**カード上の原名**（`source_entry.rel_path`）で取る。公開名
（`media_file.rel_path`）は衝突時に改名されるので、連番が一周した別カードの
ファイルと束ねうる。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from posixpath import splitext

from ..profiles.model import StackRule

# 範囲引きの上端（`source_entry.rel_path` を prefix で引くときに使う）。
# UTF-8 で最も大きい符号位置。
HIGH_SENTINEL = "\U0010ffff"


@dataclass(frozen=True)
class Candidate:
    """**観測 1 つぶん**の候補.

    同じ `media_file` が複数の `source_entry` を持つときは、この値が複数できる。
    **1 つに絞らない**（`observed_at` は再スキャンで動くので、順序で選ぶと同じ組が
    実行のたびに変わる）。**平坦化もしない** —— 「ボリュームの集合」と「1 つの
    stem」に潰すと、*別々の観測*でそれぞれが一致するだけの組が通る。
    """

    record_id: str
    media_file_id: str
    profile_id: str
    volume_instance_id: str
    rel_path: str  # **カード上の原名**
    captured_at: str
    captured_at_source: str
    origin: str
    state: str
    remote_asset_id: str | None
    invalidated: bool

    @property
    def source_key(self) -> tuple[str, str]:
        """組の鍵。**ボリュームと stem は必ず組で持つ。**"""
        return (self.volume_instance_id, stem_prefix(self.rel_path))


@dataclass(frozen=True)
class Group:
    """成立した組。**先頭が primary**（`extensions` の順）."""

    members: tuple[Candidate, ...]


@dataclass(frozen=True)
class Refusal:
    """組めなかった理由（画面に出す）."""

    reason: str


def stem_prefix(rel_path: str) -> str:
    """`DCIM/100CANON/IMG_1234.JPG` → `DCIM/100CANON/IMG_1234.`"""
    return splitext(rel_path)[0] + "."


def extension_of(rel_path: str) -> str:
    """ドット無しの大文字（`scan.extensions` の突き合わせと同じ形）."""
    return splitext(rel_path)[1].lstrip(".").upper()


def resolve_group(
    primary: Candidate, candidates: Sequence[Candidate], rule: StackRule
) -> Group | Refusal:
    """4 条件（§6）で組を決める. 同じ組はどの member から呼んでも同じになる."""
    if not rule.enabled:
        return Refusal("カメラの種類がスタックを使わない")
    if extension_of(primary.rel_path) not in rule.extensions:
        return Refusal("この拡張子は組の対象ではない")
    keys = {c.source_key for c in candidates if c.media_file_id == primary.media_file_id}
    matched = [
        c
        for c in candidates
        if c.media_file_id != primary.media_file_id
        # **鍵は組で比べる**（同じカードの、同じディレクトリの、同じ stem）。
        and c.source_key in keys
        and extension_of(c.rel_path) in rule.extensions
    ]
    # **同じ資産を 2 回送らない。** 1 つの media_file が複数の観測で候補に入る。
    # 集合を 1 つだけ持って O(n) にする（観測の数に上限は無い）。
    partners: list[Candidate] = []
    seen: set[str] = set()
    for candidate in matched:
        if candidate.media_file_id not in seen:
            seen.add(candidate.media_file_id)
            partners.append(candidate)
    if not partners:
        return Refusal("相方が見つからない")
    refusal = _refused(primary, partners, rule)
    if refusal is not None:
        return refusal
    members = sorted(
        [primary, *partners], key=lambda c: rule.extensions.index(extension_of(c.rel_path))
    )
    return Group(members=tuple(members))


def _refused(primary: Candidate, partners: Sequence[Candidate], rule: StackRule) -> Refusal | None:
    """組めない理由を探す. **見つからなければ None。**"""
    for member in (primary, *partners):
        if member.origin != "created_by_us":
            # `POST /stacks` は既存スタックを吸収する。タグ（追加のみ）と違って
            # 利用者が手で作った組を作り直しうるので、**証明できない相手には触らない**。
            return Refusal("自分が上げたと証明できない資産が含まれる")
    identifiers = [member.remote_asset_id for member in (primary, *partners)]
    if any(identifier is None for identifier in identifiers):
        # **「分からない」を「重なっている」と言わない。** 再確認で資産が消えると
        # `None` が並ぶが、それは相手が同じ ID を返した事象ではない。
        return Refusal("組の資産 ID が分からない")
    if len(set(identifiers)) != len(identifiers):
        # **相手が両方へ同じ資産 ID を返すことがある。** そのまま進むと、
        # 作成では「同じ id が複数ある」で落ち、回収では 1 資産のスタックを
        # 2 行へ `stacked` と記録してしまう。
        return Refusal("相方と資産 ID が重なっている")
    for partner in partners:
        if partner.profile_id != primary.profile_id:
            # 規則が 1 つに決まらない（§9.11）。
            return Refusal("相方が別のカメラの種類に属している")
        if partner.invalidated or partner.state != "complete":
            return Refusal("相方はまだ送信が終わっていない")
        if partner.captured_at_source != primary.captured_at_source:
            return Refusal("相方と時刻の根拠が違う（EXIF と mtime を突き合わせない）")
        if not _within(primary.captured_at, partner.captured_at, rule.tolerance_seconds):
            return Refusal("相方と撮影時刻が一致しない")
    return None


def _within(left: str, right: str, tolerance_seconds: int) -> bool:
    """**文字列ではなく瞬間で比べる**（オフセットが違っても同じ時刻でありうる）."""
    delta = datetime.fromisoformat(left) - datetime.fromisoformat(right)
    return abs(delta.total_seconds()) <= tolerance_seconds
