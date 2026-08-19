"""試験用の最小 JPEG（EXIF 付き）.

**符号化器を試験の依存にしない。** Pillow や piexif を入れると、1 タグを読む
ためだけに画像コンテナのデコーダを引き込むことになる（実装が避けているのと
同じ理由）。ここで組み立てるのは、まさに実装が読む構造そのもの。

`crash_child.py` は素のスクリプトとして起動されるので、同じディレクトリに
置いてどちらからも読めるようにしてある。
"""

from __future__ import annotations

import struct


def a_jpeg_with(datetime_original: bytes | None) -> bytes:
    """最小の JPEG. EXIF の `DateTimeOriginal` だけを持たせる."""
    if datetime_original is None:
        return b"\xff\xd8\xff\xd9"
    value = datetime_original + b"\x00"
    # TIFF ヘッダ: バイト順 II、マジック 42、IFD0 は offset 8
    tiff = b"II" + struct.pack("<HI", 42, 8)
    exif_ifd_offset = 8 + 2 + 12 + 4  # IFD0（1 件）の直後
    tiff += struct.pack("<H", 1)
    tiff += struct.pack("<HHII", 0x8769, 4, 1, exif_ifd_offset)  # ExifIFDPointer
    tiff += struct.pack("<I", 0)  # 次の IFD は無い
    value_offset = exif_ifd_offset + 2 + 12 + 4
    tiff += struct.pack("<H", 1)
    tiff += struct.pack("<HHII", 0x9003, 2, len(value), value_offset)  # DateTimeOriginal
    tiff += struct.pack("<I", 0)
    tiff += value
    app1 = b"Exif\x00\x00" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"
