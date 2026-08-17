"""マウントを名前空間から切り離して扱うための syscall ラッパ.

通常どおりマウントしたディレクトリの dirfd を渡すと、`openat(dirfd, "..")` が
マウントポイントの親へ抜ける。侵害された app はこれを使って mountd の
名前空間（ホストから bind した /dev を含む）へ到達できてしまう。

`open_tree(OPEN_TREE_CLONE)` で作った detached mount のルートでは、親を持たない
ため `..` がそこに固定される。クローンを作った後に元の取り付けを `MNT_DETACH`
すれば、そのファイルシステムはどの名前空間のパスにも現れなくなる。

Python は `open_tree(2)` を公開していないので `syscall(2)` を直接呼ぶ。
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# open_tree(2) のシステムコール番号。x86_64 と aarch64 で共通。
# 他アーキテクチャを追加するときは番号を確認すること。
NR_OPEN_TREE = 428

OPEN_TREE_CLONE = 0x1
AT_RECURSIVE = 0x8000
AT_FDCWD = -100
MNT_DETACH = 2

_libc = ctypes.CDLL(None, use_errno=True)


def _errno_error(context: str) -> OSError:
    errno = ctypes.get_errno()
    return OSError(errno, f"{context}: {os.strerror(errno)}")


def open_tree_clone(path: str) -> int:
    """`path` のマウントを複製し、どこにも接続されていないツリーの fd を返す."""
    result = _libc.syscall(
        ctypes.c_long(NR_OPEN_TREE),
        ctypes.c_int(AT_FDCWD),
        ctypes.c_char_p(path.encode()),
        ctypes.c_uint(OPEN_TREE_CLONE | os.O_CLOEXEC | AT_RECURSIVE),
    )
    if result < 0:
        raise _errno_error(f"open_tree({path})")
    return result


def dirfd_from_tree(tree_fd: int) -> int:
    """detached ツリーの fd から、列挙と openat に使える dirfd を作る.

    元の `tree_fd` は閉じてよい。dirfd 側がマウントへの参照を保持する。
    """
    return os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=tree_fd)


def umount_detach(path: str) -> None:
    """遅延アンマウント。開いている参照が残っていても失敗しない."""
    if _libc.umount2(path.encode(), ctypes.c_int(MNT_DETACH)) < 0:
        raise _errno_error(f"umount2({path}, MNT_DETACH)")


def dotdot_is_pinned(dirfd: int) -> bool:
    """`openat(dirfd, "..")` が同じディレクトリに留まるか.

    detached mount のルートなら True。通常のディレクトリなら False。
    マウント時にこれを確認することで、切り離しが効いていない状態で
    app に fd を渡してしまうことを防ぐ。

    **測定に失敗したら False を返す（fail-closed）。** 保証したいのは
    「`.` と `..` が同一の inode/dev だと実測できた」ことであって、
    「親を検査できなかった」ことは固定されている証拠にならない。一時的な
    I/O エラーで安全側に倒れてしまうと、最終ガードと実機試験が同時に
    偽陽性になる。detached mount では `..` の stat は必ず成功するので、
    fail-closed にしても正常系を妨げない。
    """
    try:
        here = os.stat(".", dir_fd=dirfd, follow_symlinks=False)
        up = os.stat("..", dir_fd=dirfd, follow_symlinks=False)
    except OSError:
        logger.exception("cannot verify that '..' is pinned; treating as not pinned")
        return False
    return (here.st_dev, here.st_ino) == (up.st_dev, up.st_ino)


def _unescape(field: str) -> str:
    # mountinfo は空白・タブ・改行・バックスラッシュを 8 進でエスケープする
    for code, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        field = field.replace(code, char)
    return field


def mountpoints_under(root: Path) -> list[str]:
    """`/proc/self/mountinfo` から `root` 配下のマウントポイントを列挙する.

    **読めなかったら例外を送出する。空リストに畳まない。** 「取り付いていない」と
    「確認できない」は違う。畳んでしまうと、runner がタイムアウトした後に
    実際には取り付いていたケースを見落とし、残骸が溜まる。
    """
    prefix = str(root)
    found: list[str] = []
    lines = Path("/proc/self/mountinfo").read_text().splitlines()
    for line in lines:
        fields = line.split(" ")
        if len(fields) < 5:
            continue
        target = _unescape(fields[4])
        # 兄弟ディレクトリ (/run/mediaferry-other) を配下と誤認しないよう、
        # 完全一致か "/" 区切りの前方一致だけを拾う
        if target == prefix or target.startswith(prefix.rstrip("/") + "/"):
            found.append(target)
    return found
