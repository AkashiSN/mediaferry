from itertools import islice

import pytest

from mediaferry.core.naming import (
    UnsafePath,
    candidate_paths,
    library_rel_path,
    safe_source_rel_path,
    staging_rel_path,
)


def test_library_mirrors_the_path_on_the_card():
    """ユーザが NAS を直接開いて辿れることを保証する."""
    assert (
        library_rel_path("original", "dji-osmo", "DCIM/DJI_001/A.MP4")
        == "library/dji-osmo/DCIM/DJI_001/A.MP4"
    )


def test_derived_files_live_under_their_own_tree():
    assert library_rel_path("derived", "dji-osmo", "DCIM/A.MP4") == "derived/dji-osmo/DCIM/A.MP4"


@pytest.mark.parametrize(
    "path", ["../etc/passwd", "/etc/passwd", "DCIM/../../x", "", "DCIM//A.MP4", "DCIM/./A.MP4"]
)
def test_unsafe_source_paths_are_refused(path):
    with pytest.raises(UnsafePath):
        safe_source_rel_path(path)


@pytest.mark.parametrize("path", ["/etc/passwd", ""])
def test_absolute_and_empty_paths_say_which_rule_they_broke(path):
    """構成要素の検査でも弾けるが、そのメッセージでは原因が分からない.

    API とログに出るのはこの文言なので、先頭で相対パスかどうかを見て分ける。
    """
    with pytest.raises(UnsafePath, match="相対パス"):
        safe_source_rel_path(path)


def test_the_first_candidate_is_the_plain_path():
    stamp = "20260817143005"
    first = next(candidate_paths("library/x/DCIM/A.MP4", stamp, "abcdef1234"))
    assert first == "library/x/DCIM/A.MP4"


def test_the_series_is_deterministic():
    stamp = "20260817143005"
    got = list(islice(candidate_paths("library/x/DCIM/A.MP4", stamp, "abcdef1234"), 5))
    assert got == [
        "library/x/DCIM/A.MP4",
        "library/x/DCIM/A_20260817143005.MP4",
        "library/x/DCIM/A_20260817143005_abcdef12.MP4",
        "library/x/DCIM/A_20260817143005_abcdef12_2.MP4",
        "library/x/DCIM/A_20260817143005_abcdef12_3.MP4",
    ]


def test_the_series_keeps_the_extension_and_the_directory():
    series = candidate_paths("derived/x/DCIM/B.tar.gz", "20260102030405", "0" * 40)
    second = list(islice(series, 2))[1]
    assert second == "derived/x/DCIM/B.tar_20260102030405.gz"


def test_staging_paths_are_scoped_to_the_job():
    """起動時の掃除がジョブ単位でできるように、job-id でディレクトリを分ける."""
    assert staging_rel_path("job-1", "art-1") == "staging/job-1/art-1"
