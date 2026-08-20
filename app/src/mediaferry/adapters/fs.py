"""dirfd を起点にした読み取り.

パス解決には常に単一のパス構成要素だけを使い、`..`・絶対パス・シンボリック
リンクを辿らない（`O_NOFOLLOW`）。これで `openat2(RESOLVE_BENEATH)` と同等の
閉じ込めを構成的に実現する。mountd が渡す dirfd は detached mount 由来なので、
その `..` はボリュームルートに固定されている。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# macOS が書く AppleDouble の残骸。本体と同じ拡張子を持つので、名前で弾く。
APPLE_DOUBLE_PREFIX = "._"


class EscapeAttempt(ValueError):
    """マウントルートの外へ出ようとするパス."""


class CrossDeviceLayout(RuntimeError):
    """staging と公開先が別のファイルシステムにある."""


@dataclass(frozen=True)
class FoundFile:
    rel_path: str
    size_bytes: int
    mtime_ns: int


def open_beneath(dirfd: int, rel_path: str) -> int:
    """dirfd の下のファイルを開く. 中間ディレクトリも 1 段ずつ辿る."""
    parts = rel_path.split("/")
    for part in parts:
        if not part or part in {".", ".."} or "\\" in part or "\0" in part:
            raise EscapeAttempt(f"安全でない構成要素: {part!r}")
    if rel_path.startswith("/"):
        raise EscapeAttempt("絶対パスは受け付けない")

    current = dirfd
    opened: list[int] = []
    try:
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            opened.append(current)
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
    finally:
        for fd in opened:
            os.close(fd)


def exists_beneath(dirfd: int, rel_path: str) -> bool:
    """dirfd の下にそのパスがあるか. 開けるかどうかで判定する."""
    try:
        fd = open_beneath(dirfd, rel_path)
    except OSError, EscapeAttempt:
        return False
    os.close(fd)
    return True


def iter_media_files(
    dirfd: int, roots: Iterable[str], extensions: Iterable[str]
) -> Iterator[FoundFile]:
    """scan.roots の下から scan.extensions に一致するファイルを列挙する."""
    wanted = {ext.upper() for ext in extensions}
    for root in roots:
        yield from _walk(dirfd, root, root, wanted)


def _walk(dirfd: int, root: str, rel_prefix: str, wanted: set[str]) -> Iterator[FoundFile]:
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dirfd)
    except OSError:
        return
    try:
        for entry in sorted(os.scandir(fd), key=lambda e: e.name):
            name = entry.name
            if name.startswith(".") or name.startswith(APPLE_DOUBLE_PREFIX):
                continue
            rel = f"{rel_prefix}/{name}"
            if entry.is_dir(follow_symlinks=False):
                yield from _walk(fd, name, rel, wanted)
            elif entry.is_file(follow_symlinks=False) and _extension(name) in wanted:
                stat = entry.stat(follow_symlinks=False)
                yield FoundFile(rel_path=rel, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
    finally:
        os.close(fd)


def _extension(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return ext.upper()


class DirfdTree:
    """`resolve_profile` に渡す読み取り専用の窓."""

    def __init__(self, dirfd: int) -> None:
        self._dirfd = dirfd

    def has_root(self, name: str) -> bool:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._dirfd)
        except OSError:
            return False
        os.close(fd)
        return True

    def iter_names(self, root: str, limit: int) -> list[str]:
        """root 配下のファイル名を（サブディレクトリも辿って）返す.

        DJI は DCIM/DJI_001/ の下に置くので、直下だけを見ると 0 件になる。
        """
        names: list[str] = []
        self._collect(self._dirfd, root, names, limit)
        return names

    def _collect(self, parent_fd: int, name: str, names: list[str], limit: int) -> None:
        if len(names) >= limit:
            return
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError:
            return
        try:
            for entry in sorted(os.scandir(fd), key=lambda e: e.name):
                if len(names) >= limit:
                    return
                if entry.name.startswith("."):
                    continue
                if entry.is_file(follow_symlinks=False):
                    names.append(entry.name)
                elif entry.is_dir(follow_symlinks=False):
                    self._collect(fd, entry.name, names, limit)
        finally:
            os.close(fd)


def fsync_dir(path: Path) -> None:
    """ディレクトリエントリを永続化する. これを怠ると電源断で公開が失われる."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def assert_same_filesystem(*paths: Path) -> None:
    """staging と公開先が同じファイルシステムにあることを起動時に確かめる.

    公開は `os.link` による原子的操作である必要がある。別デバイスにあると
    `EXDEV` で必ず失敗し、それが分かるのは最初の取り込みの最中になる。
    """
    devices = {path: path.stat().st_dev for path in paths}
    if len(set(devices.values())) > 1:
        detail = ", ".join(f"{path}={dev}" for path, dev in devices.items())
        raise CrossDeviceLayout(
            f"staging と公開先が別のファイルシステムにある（{detail}）。"
            "DATA_ROOT の下に 1 つのデータセットとして置くこと"
        )
