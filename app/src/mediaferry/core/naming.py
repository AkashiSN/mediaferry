"""ライブラリ内のパスの決め方.

デバイス上の相対パスを保つ。この鏡写しの構造は意図的な設計価値で、ユーザが
NAS を直接開いて中身を辿れることを保証する。プロファイル slug で分けるのは、
複数機種のファイル名が衝突しうるため（IMG_0001.JPG は多くの機種で使われる）。

衝突時は**既存のファイルを絶対に動かさず**、新しく公開する側の名前を変える。
既存を動かすと media_file.rel_path と、それを参照する merge_member /
upload_record が壊れる。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath

SHA1_PREFIX_CHARS = 8


class UnsafePath(ValueError):
    """`..`・絶対パス・空の構成要素を含むパス."""


def safe_source_rel_path(rel_path: str) -> str:
    """カード上の相対パスを検証して正規形で返す."""
    if not rel_path or rel_path.startswith("/"):
        raise UnsafePath(f"相対パスではない: {rel_path!r}")
    parts = rel_path.split("/")
    for part in parts:
        if not part or part in {".", ".."} or "\0" in part or "\\" in part:
            raise UnsafePath(f"安全でない構成要素: {part!r}")
    return "/".join(parts)


def library_rel_path(role: str, profile_slug: str, source_rel_path: str) -> str:
    top = "library" if role == "original" else "derived"
    return f"{top}/{profile_slug}/{safe_source_rel_path(source_rel_path)}"


def staging_rel_path(job_id: str, artifact_id: str) -> str:
    return f"staging/{job_id}/{artifact_id}"


def work_rel_path(job_id: str) -> str:
    return f"work/{job_id}"


def candidate_paths(rel_path: str, stamp: str, sha1_hex: str) -> Iterator[str]:
    """公開先の候補を決定的な順序で無限に返す.

    途中で落ちて再実行しても同じ名前に落ち着くよう、乱数も現在時刻も使わない。
    `stamp` はソースの mtime 由来の壁時計（`YYYYMMDDHHMMSS`）で、staged の
    時点で永続化されたものをそのまま受け取る。
    """
    path = PurePosixPath(rel_path)
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    short = sha1_hex[:SHA1_PREFIX_CHARS]

    yield rel_path
    yield str(parent / f"{stem}_{stamp}{suffix}")
    yield str(parent / f"{stem}_{stamp}_{short}{suffix}")
    n = 2
    while True:
        yield str(parent / f"{stem}_{stamp}_{short}_{n}{suffix}")
        n += 1
