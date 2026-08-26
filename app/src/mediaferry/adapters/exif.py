"""EXIF から撮影日時（壁時計）を 1 つだけ読む.

**ソースは信頼できない入力**（§14 は RCE を脅威モデルに含む）。だからこの層は
次を守る。

- **読むのは `EXIF DateTimeOriginal` の 1 タグだけ。** `stop_tag` でそこまでしか
  解析させず、`details=False` でサムネイルと MakerNote を読まない
- **例外はすべて握る。** 壊れた 1 枚で取り込み全体を止めない。読めなければ
  `None` を返し、呼び出し側が `timestamp.source` の連鎖を次へ進める
- **オフセットは読まない。** EOS 70D は EXIF 2.31 の `OffsetTimeOriginal` より前の
  機種で持っていない。付いていると装わず、壁時計として返す。TZ の扱いは
  プロファイルの `timezone_policy` が決める（§6）

読む対象は**ステージ済みのファイル**であってソースではない（§9.3 手順 5）。
SHA-1 で検証済みのバイト同一なコピーなので、dirfd 起点の単一構成要素という
ソース側の規約をここへ持ち込まなくて済む。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import exifread

TAG = "EXIF DateTimeOriginal"
# EXIF 2.x の日時の形。区切りはコロンで、TZ は含まない。
FORMAT = "%Y:%m:%d %H:%M:%S"

# **認識できない入力に対して exifread は例外ではなく WARNING を出す。**
# Canon は MOV も source: exif のプロファイルを通るので、黙らせないと動画
# 1 本ごとに警告が並ぶ。呼ぶ側の振り分け（画像だけ渡す）と二重の保険。
logging.getLogger("exifread").setLevel(logging.ERROR)


def read_datetime_original(path: Path) -> datetime | None:
    """`DateTimeOriginal` を壁時計として返す. 読めなければ `None`."""
    try:
        with path.open("rb") as handle:
            tags = exifread.process_file(handle, stop_tag=TAG, details=False)
        raw = tags.get(TAG)
        if raw is None:
            return None
        return datetime.strptime(str(raw).strip(), FORMAT)  # noqa: DTZ007
    except Exception:  # noqa: BLE001 - 読めないことは失敗ではない
        return None
