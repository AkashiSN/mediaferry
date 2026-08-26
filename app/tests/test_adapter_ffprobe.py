import json
import shutil
import subprocess

import pytest

from mediaferry.adapters.ffprobe import MediaProbe


@pytest.fixture
def a_video(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    path = tmp_path / "clip.mp4"
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.parametrize("extension", ["JPG", "jpg", "Jpg"])
def test_photos_are_not_probed(tmp_path, extension):
    path = tmp_path / "a.JPG"
    path.write_bytes(b"not really a jpeg")
    got = MediaProbe(ffprobe_path="/nonexistent").describe(path, extension)
    assert got.kind == "photo"
    assert got.probe_state == "not_applicable"
    assert got.duration_seconds is None


def test_a_video_gets_a_duration(a_video):
    got = MediaProbe().describe(a_video, "MP4")
    assert got.kind == "video"
    assert got.probe_state == "ok"
    assert 1.8 < got.duration_seconds < 2.2
    assert any(s["codec_type"] == "video" for s in got.streams)


def test_a_broken_video_fails_without_raising(tmp_path):
    path = tmp_path / "broken.MP4"
    path.write_bytes(b"\x00" * 128)
    got = MediaProbe().describe(path, "MP4")
    assert got.kind == "video"
    assert got.probe_state == "failed"
    assert got.duration_seconds is None


def test_a_missing_ffprobe_is_reported_as_failed_not_a_crash(tmp_path):
    path = tmp_path / "a.MP4"
    path.write_bytes(b"\x00")
    got = MediaProbe(ffprobe_path="/nonexistent/ffprobe").describe(path, "MP4")
    assert got.probe_state == "failed"


def test_the_command_is_an_argument_array(monkeypatch, tmp_path):
    """シェル文字列を組み立てない（§14）."""
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"format": {"duration": "1.0"}, "streams": []}), ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = tmp_path / "a.MP4"
    path.write_bytes(b"\x00")
    MediaProbe().describe(path, "MP4")
    assert isinstance(seen["args"], list)
    assert str(path) in seen["args"]
    # 終了ステータスを見る。壊れた入力でも JSON らしきものを出しうるので、
    # パースが通ったことを成功の判定に使わない。
    assert seen["kwargs"]["check"] is True
    # 16GiB のファイルで ffprobe が固まったままワーカーを止めない。
    assert seen["kwargs"]["timeout"] > 0


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["ffprobe"]),
        subprocess.TimeoutExpired(["ffprobe"], 60),
    ],
)
def test_subprocess_failures_are_reported_as_failed(monkeypatch, tmp_path, error):
    """非ゼロ終了もタイムアウトも failed にする。ワーカーごと落とさない."""

    def fake_run(args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = tmp_path / "a.MP4"
    path.write_bytes(b"\x00")
    got = MediaProbe().describe(path, "MP4")
    assert got.probe_state == "failed"
    assert got.duration_seconds is None


def _probe_returning(monkeypatch, payload):
    """ffprobe を呼ばずに、決めた JSON を返させる."""

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return MediaProbe()


def test_the_container_creation_time_is_carried_verbatim(tmp_path, monkeypatch):
    """**器が申告した撮影時刻は、解釈せずそのまま運ぶ.**

    Canon は現地の壁時計を書きながら `Z` を付ける。ここで UTC として読むと
    9 時間ずれた値が固定されてしまう。意味の解釈は `core/timestamps.py` の
    1 か所に置く。
    """
    payload = {
        "format": {"duration": "12.5", "tags": {"creation_time": "2026-08-26T12:35:08.000000Z"}},
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
    }
    probe = _probe_returning(monkeypatch, payload)
    result = probe.describe(tmp_path / "MVI_0006.MOV", "MOV")
    assert result.container_wall == "2026-08-26T12:35:08.000000Z"


def test_a_container_without_a_creation_time_reports_none(tmp_path, monkeypatch):
    """タグを持たない器もある. 欠けていることと 0 を混ぜない."""
    payload = {"format": {"duration": "12.5"}, "streams": []}
    probe = _probe_returning(monkeypatch, payload)
    assert probe.describe(tmp_path / "a.MOV", "MOV").container_wall is None


def test_a_photo_reports_no_container_time(tmp_path):
    """写真では ffprobe を走らせない. 値も持たない."""
    assert MediaProbe().describe(tmp_path / "IMG_0001.JPG", "JPG").container_wall is None


def test_a_non_string_creation_time_reports_none(tmp_path, monkeypatch):
    """`creation_time` が文字列でない値（数値等）で来た場合は取り込まない."""
    payload = {"format": {"duration": "12.5", "tags": {"creation_time": 0}}, "streams": []}
    probe = _probe_returning(monkeypatch, payload)
    assert probe.describe(tmp_path / "a.MOV", "MOV").container_wall is None
