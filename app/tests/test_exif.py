"""EXIF の `DateTimeOriginal` だけを読む（Task 3）.

**ソースは信頼できない入力**（§14 は RCE を脅威モデルに含む）。読むのは 1 タグだけ、
サムネイルと MakerNote は読まない、例外はすべて握って `fallback` へ落とす。
壊れた 1 枚で取り込み全体を止めない。
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from mediaferry.adapters.exif import read_datetime_original

from .exif_fixtures import a_jpeg_with


def a_file(tmp_path, name: str, payload: bytes):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_it_reads_the_wall_clock(tmp_path):
    path = a_file(tmp_path, "IMG_0001.JPG", a_jpeg_with(b"2026:08:19 14:30:05"))
    assert read_datetime_original(path) == datetime(2026, 8, 19, 14, 30, 5)  # noqa: DTZ001


def test_the_value_has_no_timezone(tmp_path):
    """**壁時計として返す。** EOS 70D は `OffsetTimeOriginal` を持たない世代で、
    オフセットは EXIF から得られない。付いていると装ってはいけない。
    """
    path = a_file(tmp_path, "IMG_0001.JPG", a_jpeg_with(b"2026:08:19 14:30:05"))
    assert read_datetime_original(path).tzinfo is None


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("タグが無い", a_jpeg_with(None)),
        ("JPEG ではない", b"not a jpeg at all"),
        ("空", b""),
        ("EXIF が途中で切れている", a_jpeg_with(b"2026:08:19 14:30:05")[:30]),
        ("日時の形が違う", a_jpeg_with(b"nonsense")),
    ],
)
def test_unreadable_input_returns_none_without_raising(tmp_path, name, payload):
    """**壊れた 1 枚で取り込み全体を止めない。** 例外は握って `fallback` へ落とす."""
    path = a_file(tmp_path, "X.JPG", payload)
    assert read_datetime_original(path) is None, name


def test_a_missing_file_returns_none(tmp_path):
    assert read_datetime_original(tmp_path / "does-not-exist.JPG") is None


def test_it_does_not_log_warnings_for_unreadable_input(tmp_path, caplog):
    """`exifread` は認識できない入力に WARNING を出す（実測）.

    Canon は MOV も `source: exif` のプロファイルを通るので、黙らせないと
    動画 1 本ごとに警告が並ぶ。呼ぶ側の振り分け（画像だけ）と二重の保険。
    """
    path = a_file(tmp_path, "MVI_0001.MOV", b"\x00\x00\x00\x18ftypqt  ")
    with caplog.at_level(logging.WARNING):
        assert read_datetime_original(path) is None
    assert not [r for r in caplog.records if r.name == "exifread"], "exifread の警告が漏れている"


def test_a_synthetic_cr2_yields_its_capture_time(tmp_path):
    """**推測で E2E を組まない。** 合成 CR2 が実装の読み取り経路を通るかを測る.

    通らなければ、E2E で「組が成立しない」のを仕様どおりと誤読する。
    """
    from .exif_fixtures import a_tiff_with

    path = tmp_path / "IMG_1234.CR2"
    path.write_bytes(a_tiff_with(b"2026:08:19 10:30:00"))

    assert read_datetime_original(path) == datetime(2026, 8, 19, 10, 30, 0)
