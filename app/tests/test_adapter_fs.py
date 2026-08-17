import os
from pathlib import Path

import pytest

from mediaferry.adapters.fs import (
    CrossDeviceLayout,
    DirfdTree,
    EscapeAttempt,
    assert_same_filesystem,
    iter_media_files,
    open_beneath,
)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "DCIM" / "DJI_001").mkdir(parents=True)
    (tmp_path / "DCIM" / "DJI_001" / "A.MP4").write_bytes(b"payload")
    (tmp_path / "DCIM" / "DJI_001" / "A.LRF").write_bytes(b"low res")
    (tmp_path / "DCIM" / "DJI_001" / "._A.MP4").write_bytes(b"apple double")
    (tmp_path / "DCIM" / ".hidden").mkdir()
    (tmp_path / "DCIM" / ".hidden" / "B.MP4").write_bytes(b"x")
    (tmp_path / "PANORAMA").mkdir()
    (tmp_path / "PANORAMA" / "PANO_0001.JPG").write_bytes(b"jpg")
    (tmp_path / "MISC").mkdir()
    (tmp_path / "MISC" / "C.MP4").write_bytes(b"outside the roots")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    yield fd
    os.close(fd)


def test_only_configured_roots_and_extensions_are_listed(tree):
    found = {f.rel_path for f in iter_media_files(tree, ("DCIM", "PANORAMA"), ("MP4", "JPG"))}
    assert found == {"DCIM/DJI_001/A.MP4", "PANORAMA/PANO_0001.JPG"}


def test_dot_directories_and_apple_doubles_are_skipped(tree):
    found = {f.rel_path for f in iter_media_files(tree, ("DCIM",), ("MP4",))}
    assert found == {"DCIM/DJI_001/A.MP4"}


@pytest.mark.parametrize("configured", ["MP4", "mp4", "Mp4"])
def test_extension_matching_is_case_insensitive(tmp_path, configured):
    """カード上の名前と、呼び出し側が渡す拡張子の両方で大小文字を問わない."""
    (tmp_path / "DCIM").mkdir()
    (tmp_path / "DCIM" / "a.mp4").write_bytes(b"x")
    (tmp_path / "DCIM" / "B.MP4").write_bytes(b"x")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        found = {f.rel_path for f in iter_media_files(fd, ("DCIM",), (configured,))}
        assert found == {"DCIM/a.mp4", "DCIM/B.MP4"}
    finally:
        os.close(fd)


def test_found_files_carry_size_and_mtime(tree):
    found = next(iter(iter_media_files(tree, ("PANORAMA",), ("JPG",))))
    assert found.size_bytes == 3
    assert found.mtime_ns > 0


def test_open_beneath_reads_through_the_dirfd(tree):
    fd = open_beneath(tree, "DCIM/DJI_001/A.MP4")
    with os.fdopen(fd, "rb") as f:
        assert f.read() == b"payload"


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "DCIM/../../x"])
def test_open_beneath_refuses_to_escape(tree, path):
    with pytest.raises(EscapeAttempt):
        open_beneath(tree, path)


def test_symlinks_are_not_followed(tmp_path):
    (tmp_path / "DCIM").mkdir()
    (tmp_path / "DCIM" / "link.MP4").symlink_to("/etc/passwd")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert list(iter_media_files(fd, ("DCIM",), ("MP4",))) == []
        with pytest.raises(OSError):
            open_beneath(fd, "DCIM/link.MP4")
    finally:
        os.close(fd)


def test_the_tree_view_answers_the_matching_questions(tree):
    view = DirfdTree(tree)
    assert view.has_root("DCIM") is True
    assert view.has_root("NOPE") is False
    assert "PANO_0001.JPG" in list(view.iter_names("PANORAMA", 100))


def test_the_tree_view_walks_into_subdirectories(tree):
    """DJI は DCIM/DJI_001/ の下にファイルを置く. 直下だけ見ると 0 件になる."""
    assert "A.MP4" in list(DirfdTree(tree).iter_names("DCIM", 100))


def test_same_filesystem_check_passes_for_one_dataset(data_root):
    assert_same_filesystem(data_root / "staging", data_root / "library", data_root / "derived")


def test_same_filesystem_check_reports_a_split_layout(data_root, monkeypatch):
    """別デバイスだと os.link が EXDEV で必ず失敗する. 起動時に気づく."""
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self.name == "staging":
            return os.stat_result(tuple(result)[:2] + (result.st_dev + 1,) + tuple(result)[3:])
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    with pytest.raises(CrossDeviceLayout):
        assert_same_filesystem(data_root / "staging", data_root / "library")
