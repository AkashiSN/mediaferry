"""ボリュームの中身の軽い要約.

「前回と同じカードか」を推測するために使う。フォーマット直後や別カードへの
差し替えを検出することが目的で、完全な保証ではない（§8）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

DOMAIN = b"mfm"
VERSION = 1


def content_manifest_digest(names: Iterable[str]) -> str:
    """名前の集合から決定的なダイジェストを作る.

    順序に依存しないよう並べ替える。ディレクトリを走査する順序は
    ファイルシステムによって変わる。
    """
    digest = hashlib.sha256()
    digest.update(DOMAIN)
    digest.update(bytes([VERSION]))
    for name in sorted(set(names)):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
