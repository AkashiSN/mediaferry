"""サムネイル（§13）.

**再生成できるキャッシュなので DB に入れない**（`DATA_ROOT/cache/thumbnails/`）。
置き場所と枚数に上限を置く —— `at` を自由に受けると、1 本の動画で何千枚も作れて
データ領域を埋められる（認証を切った LAN では誰でも）。
"""

from __future__ import annotations

import shutil
import subprocess
import threading

import pytest

from mediaferry.adapters.thumbnails import (
    MAX_FRAMES_PER_MEDIA,
    STEP_SECONDS,
    ThumbnailCache,
    ThumbnailFailed,
    quantise,
)


@pytest.fixture
def clip(data_root):
    """実 ffmpeg で作った短い動画（合成でも本物のフォーマット）."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    path = data_root / "library" / "clip.MP4"
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=40:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def cache(data_root):
    return ThumbnailCache(data_root)


# ---------------------------------------------------------------- 位置の丸め
@pytest.mark.parametrize(
    ("asked", "duration", "expected"),
    [
        (0, 40.0, 0),
        (7, 40.0, 0),
        (13, 40.0, 10),
        (39, 40.0, 30),
        # 長さを超えたら最後の刻みに寄せる（存在しない位置を作らない）。
        (999, 40.0, 30),
        (-5, 40.0, 0),
        # 長さが分からない写真などは 0 だけ。
        (30, None, 0),
    ],
)
def test_the_position_is_quantised(asked, duration, expected):
    assert quantise(asked, duration) == expected


def test_the_number_of_frames_per_media_is_capped():
    """**1 本あたりの枚数に上限。** 長い動画で無制限に作らせない."""
    very_long = STEP_SECONDS * 10_000.0
    assert quantise(10_000_000, very_long) == STEP_SECONDS * (MAX_FRAMES_PER_MEDIA - 1)


# ---------------------------------------------------------------- 生成
def test_a_frame_is_extracted_and_cached(cache, clip, data_root, monkeypatch):
    first = cache.get_or_create("m-1", clip, at=10)
    assert first.exists()
    assert first.read_bytes()[:3] == b"\xff\xd8\xff"  # JPEG
    assert first.relative_to(data_root / "cache" / "thumbnails")

    # **2 度目は ffmpeg を呼ばない**（出来上がりが同じかどうかでは分からない）。
    calls = []
    real = ThumbnailCache._extract
    monkeypatch.setattr(
        ThumbnailCache,
        "_extract",
        lambda self, source, at, destination: (
            calls.append(at),
            real(self, source, at, destination),
        )[1],
    )
    second = cache.get_or_create("m-1", clip, at=10)

    assert second == first
    assert calls == []


def test_a_failure_leaves_nothing_behind(cache, data_root):
    """**空ファイルを残さない。** 残すと以後ずっと壊れた絵を返す."""
    broken = data_root / "library" / "broken.MP4"
    broken.write_bytes(b"not a video")

    with pytest.raises(ThumbnailFailed):
        cache.get_or_create("m-2", broken, at=0)

    folder = data_root / "cache" / "thumbnails" / "m-2"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_half_written_file_is_removed(cache, data_root, monkeypatch):
    """**途中まで書けてから失敗する**のが本番で起きる形（時間切れ、容量不足）."""

    def half_then_fail(self, source, at, destination):  # noqa: ANN001, ANN202
        destination.write_bytes(b"\xff\xd8half")
        raise ThumbnailFailed("途中で落ちた")

    monkeypatch.setattr(ThumbnailCache, "_extract", half_then_fail)

    with pytest.raises(ThumbnailFailed):
        cache.get_or_create("m-6", data_root / "library" / "whatever.MP4", at=0)

    folder = data_root / "cache" / "thumbnails" / "m-6"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_zero_byte_output_is_not_taken_as_success(cache, data_root, monkeypatch):
    """**終了コードだけを信じない。**

    0 で返りながら**空のファイルを置いていく**ことがある。中身を見ないと、その
    空ファイルがキャッシュに座って以後ずっと壊れた絵を返す。
    """
    from pathlib import Path

    class _Ok:
        returncode = 0

    def writes_nothing(command, **kwargs):  # noqa: ANN001, ANN003, ANN202
        Path(command[-1]).write_bytes(b"")  # 出力先は引数の最後
        return _Ok()

    monkeypatch.setattr("mediaferry.adapters.thumbnails.subprocess.run", writes_nothing)

    with pytest.raises(ThumbnailFailed):
        cache.get_or_create("m-7", data_root / "library" / "whatever.MP4", at=0)
    assert not cache.path_for("m-7", 0).exists()


def test_two_readers_generate_only_once(cache, clip):
    """同じ絵を同時に求められても、作るのは 1 回（`.part` を奪い合わない）."""
    calls = []
    real = ThumbnailCache._extract

    def counted(self, source, at, destination):  # noqa: ANN001, ANN202
        calls.append(at)
        return real(self, source, at, destination)

    ThumbnailCache._extract = counted
    try:
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(cache.get_or_create("m-3", clip, at=0)))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        ThumbnailCache._extract = real

    assert len(calls) == 1
    assert len({str(path) for path in results}) == 1


def test_the_oldest_frames_are_dropped_when_the_cache_is_full(cache, clip, data_root):
    """**容量に上限。** 超えたら**古い順に**消す（データ領域を埋めさせない）."""
    import os

    first = cache.get_or_create("m-4", clip, at=0)
    second = cache.get_or_create("m-5", clip, at=0)
    # 触った時刻をはっきり分ける（同じ秒に並ぶと順序が決まらない）。
    os.utime(first, (1_000_000, 1_000_000))
    os.utime(second, (2_000_000, 2_000_000))
    # 1 枚だけ残る大きさにする。
    cache.max_bytes = max(first.stat().st_size, second.stat().st_size)

    cache._evict()  # noqa: SLF001 - 掃除そのものを試す

    assert not first.exists()
    assert second.exists()


# ---------------------------------------------------------------- API
def _a_media(db, data_root, clip):
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile

    rel = clip.relative_to(data_root)
    # ビルトインは client の起動時に同期されるので、別の slug を使う。
    return a_media_file(
        db, a_profile(db, slug="thumb-test"), rel_path=str(rel), duration_seconds=40.0
    )


def test_the_api_returns_a_frame_with_caching_headers(client, db, data_root, clip):
    media_id = _a_media(db, data_root, clip)

    response = client.get(f"/api/media/{media_id}/thumbnail?at=13")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=604800"
    # **位置を丸めた結果が ETag に入る**（同じ絵に同じ札）。
    assert response.headers["etag"].endswith('-10"')


def test_an_unchanged_frame_is_not_sent_again(client, db, data_root, clip):
    media_id = _a_media(db, data_root, clip)
    first = client.get(f"/api/media/{media_id}/thumbnail")

    again = client.get(
        f"/api/media/{media_id}/thumbnail", headers={"If-None-Match": first.headers["etag"]}
    )

    assert again.status_code == 304
    assert not again.content


def test_a_bad_position_is_refused(client, db, data_root, clip):
    media_id = _a_media(db, data_root, clip)
    assert client.get(f"/api/media/{media_id}/thumbnail?at=../etc").status_code == 422


def test_an_unknown_media_is_not_found(client):
    assert client.get("/api/media/nope/thumbnail").status_code == 404


def test_a_media_whose_file_is_gone_is_reported(client, db, data_root, clip):
    media_id = _a_media(db, data_root, clip)
    clip.unlink()

    response = client.get(f"/api/media/{media_id}/thumbnail")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "thumbnail_failed"
