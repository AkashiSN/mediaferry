"""スキャン時の同一性判定に使う軽量な指紋.

(rel_path, size, mtime) だけだと、SD を再フォーマットして連番が再利用され、
たまたま同じサイズ・同じ mtime の別ファイルが同じパスに来る場合を取りこぼす。
16GiB に毎回フル SHA-1 を掛けるのは実用に耐えないので、決定的な位置の
16 窓（合計 1MiB）だけを読む。

**これは同一性の確率的キャッシュキーであって、完全性検査ではない。**
サンプリング対象外だけが変化・破損したファイルは検出できない。ビットロットの
検出には media_file.sha1 とソースのフルハッシュを突き合わせる deep_verify を使う。
"""

from __future__ import annotations

import hashlib
from typing import BinaryIO

FINGERPRINT_VERSION = 1
WINDOW_BYTES = 64 * 1024
WINDOW_COUNT = 16
DOMAIN = b"mfq"


def window_offsets(size: int) -> list[int]:
    """読む窓の先頭オフセットを決定的に算出する.

    1MiB 以下ならファイル全体を 1 窓として読む。範囲が重なる場合は
    重複を除いて昇順で返す。
    """
    if size <= WINDOW_BYTES * WINDOW_COUNT:
        return [0]
    span = size - WINDOW_BYTES
    offsets = [round(i * span / (WINDOW_COUNT - 1)) for i in range(WINDOW_COUNT)]
    return sorted(set(offsets))


def quick_fingerprint(fileobj: BinaryIO, size: int) -> str:
    """ドメイン分離子と固定幅のサイズを含めて連結の曖昧さを排除する."""
    digest = hashlib.sha1(usedforsecurity=False)  # noqa: S324
    digest.update(DOMAIN)
    digest.update(bytes([FINGERPRINT_VERSION]))
    digest.update(size.to_bytes(8, "little"))
    for offset in window_offsets(size):
        fileobj.seek(offset)
        remaining = WINDOW_BYTES if size > WINDOW_BYTES * WINDOW_COUNT else size
        while remaining > 0:
            chunk = fileobj.read(remaining)
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()
