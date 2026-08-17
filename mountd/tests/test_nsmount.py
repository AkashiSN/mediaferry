import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mountd import nsmount


def test_root_directory_has_pinned_dotdot():
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert nsmount.dotdot_is_pinned(fd) is True
    finally:
        os.close(fd)


def test_ordinary_directory_does_not_have_pinned_dotdot(tmp_path):
    """通常のディレクトリは親へ抜けられる。これが塞ぐべき穴。"""
    sub = tmp_path / "vol"
    sub.mkdir()
    fd = os.open(sub, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert nsmount.dotdot_is_pinned(fd) is False
    finally:
        os.close(fd)


def test_dotdot_check_fails_closed_when_stat_errors(monkeypatch):
    """検証できなかったことを「固定されている」と解釈しない.

    fail-open だと、一時的な I/O エラーで最終ガードと実機試験が同時に
    偽陽性になる。ここでは本来 pinned な `/` を対象にして、stat が失敗すれば
    False になることを確かめる。
    """
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if path == "..":
            raise OSError(errno.EIO, "simulated I/O error")
        return real_stat(path, *args, **kwargs)

    try:
        assert nsmount.dotdot_is_pinned(fd) is True  # 素の状態では pinned
        monkeypatch.setattr(os, "stat", fake_stat)
        assert nsmount.dotdot_is_pinned(fd) is False
    finally:
        os.close(fd)


def test_dotdot_check_fails_closed_on_a_bad_fd():
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    os.close(fd)
    assert nsmount.dotdot_is_pinned(fd) is False


def test_mountpoints_under_propagates_read_failures(monkeypatch):
    """読めなかったことを「取り付いていない」と解釈しない."""

    def boom(self, *args, **kwargs):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(OSError):
        nsmount.mountpoints_under(Path("/"))


def test_mountpoints_under_root_finds_the_root_mount():
    found = nsmount.mountpoints_under(Path("/"))
    assert "/" in found


def test_mountpoints_under_an_unmounted_path_is_empty(tmp_path):
    assert nsmount.mountpoints_under(tmp_path) == []


def test_mountpoints_under_does_not_match_sibling_prefixes(tmp_path):
    """/run/mediaferry-other が /run/mediaferry の配下と誤認されないこと."""
    assert nsmount.mountpoints_under(Path("/proc-not-a-real-prefix")) == []


DETACH_SCRIPT = r"""
import os, sys, ctypes
sys.path.insert(0, sys.argv[1])
from mountd import nsmount

src, mnt = sys.argv[2], sys.argv[3]
libc = ctypes.CDLL(None, use_errno=True)
assert libc.mount(src.encode(), mnt.encode(), None, ctypes.c_ulong(0x1000), None) == 0

tree = nsmount.open_tree_clone(mnt)
dirfd = nsmount.dirfd_from_tree(tree)
os.close(tree)
nsmount.umount_detach(mnt)

print("PINNED", nsmount.dotdot_is_pinned(dirfd))
print("ENTRIES", sorted(os.listdir(dirfd)))
sub = os.open("DCIM", os.O_RDONLY | os.O_DIRECTORY, dir_fd=dirfd)
f = os.open("A.MP4", os.O_RDONLY, dir_fd=sub)
print("CONTENT", os.read(f, 16).decode())
"""


@pytest.mark.needs_root
def test_detached_clone_pins_dotdot_and_survives_detach(tmp_path):
    """実際にマウントして、切り離したクローンで `..` が固定されることを確かめる.

    ユーザ名前空間を切って動かすので、root でなくても実行できる環境が多い。
    できない環境では skip する。
    """
    src = tmp_path / "src"
    (src / "DCIM").mkdir(parents=True)
    (src / "DCIM" / "A.MP4").write_bytes(b"payload-16bytes!")
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    (tmp_path / "SECRET").write_bytes(b"secret")

    pkg_root = str(Path(nsmount.__file__).parent.parent)
    argv = [  # noqa: S607
        "unshare",
        "-Urm",
        "--propagation",
        "private",
        sys.executable,
        "-c",
        DETACH_SCRIPT,
        pkg_root,
        str(src),
        str(mnt),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True)  # noqa: S603
    if proc.returncode != 0:
        pytest.skip(f"ユーザ名前空間でマウントできない環境: {proc.stderr.strip()[:200]}")
    assert "PINNED True" in proc.stdout
    assert "ENTRIES ['DCIM']" in proc.stdout
    assert "CONTENT payload-16bytes!" in proc.stdout
