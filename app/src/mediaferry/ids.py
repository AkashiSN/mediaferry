"""ID の採番.

uuid4 の hex を使う。ハイフン無しなのは、パスやログに出したときに
選択・コピーしやすいため。
"""

from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    return uuid4().hex
