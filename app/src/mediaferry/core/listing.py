"""一覧の共通の判断（§11 / §13）.

**ページの境目で行が重複・欠落しないこと**と、**1 回の要求で全件を引かせないこと**を
ここに閉じる。並びの tie-break は SQL 側（`captured_at DESC, id DESC`）。
"""

from __future__ import annotations

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
