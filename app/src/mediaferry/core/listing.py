"""一覧の共通の判断（§11 / §13）.

**ページの境目で行が重複・欠落しないこと**と、**1 回の要求で全件を引かせないこと**を
ここに閉じる。並びの tie-break は SQL 側（`captured_at DESC, id DESC`）。
"""

from __future__ import annotations

from collections.abc import Iterable

from ..db.profiles import ProfileRef

# 1 ページの既定と上限。上限を置かないと、1 回の要求で全件を引ける。
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def page_bounds(page: int, page_size: int) -> tuple[int, int]:
    """`(limit, offset)` にする. 範囲の外は内側へ寄せる."""
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    index = max(1, page)
    return size, (index - 1) * size


def escape_like(raw: str) -> str:
    """`LIKE` の記号を文字として扱う.

    `%` を打った利用者に全件を返さない。`\\` 自身を先に置き換える
    （後にすると、直前に足した `\\` をもう一度置き換えてしまう）。
    """
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def stack_extension_ranks(profiles: Iterable[ProfileRef]) -> list[tuple[str, str, int]]:
    """`(profile_id, 拡張子, 順位)` の一覧. **順位は現行リビジョンから読む。**

    取り込んだ版ではなく現行版を使うのは、組が「取り込みの記録」ではなく
    「いま適用する操作」だから（`docs/decisions.md`）。`stack` が無効なプロファイルは
    1 行も出さない —— 出さなければ、その行は決して従にならない。
    """
    ranks: list[tuple[str, str, int]] = []
    for profile in profiles:
        rule = profile.definition.stack
        if not rule.enabled:
            continue
        for position, extension in enumerate(rule.extensions):
            ranks.append((profile.profile_id, extension, position))
    return ranks
