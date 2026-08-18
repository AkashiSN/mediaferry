"""サムネイルの生成とキャッシュ（§13）.

**DB に入れない。** 再生成できるキャッシュであって派生物ではない（`media_file` や
`artifact` と同じ扱いにすると、回収の対象にも backup の対象にもなってしまう）。

**上限を 2 つ置く。** 位置は刻みに丸めて 1 本あたりの枚数を抑え、全体の容量にも
上限を置く。`at` を自由に受けると、1 本の長い動画で何千枚も作れてデータ領域を
埋められる —— 認証を切った LAN では誰でもできる（§14 の「残る攻撃面」を増やさない）。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

# 位置の刻み（秒）と、1 本あたりの枚数の上限。
STEP_SECONDS = 10
MAX_FRAMES_PER_MEDIA = 32
# 長辺。一覧に並べるのに足りる大きさ。
LONG_EDGE = 512
# 生成に掛ける時間の上限。壊れた入力で居座らせない。
TIMEOUT_SECONDS = 30
# キャッシュ全体の容量。
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024


class ThumbnailFailed(RuntimeError):
    """作れなかった（入力が壊れている、消えている、時間切れ）."""


def quantise(asked: int, duration_seconds: float | None) -> int:
    """求められた位置を、実際に作る位置へ丸める.

    **存在しない位置を作らない。** 長さを超えた要求は最後の刻みへ寄せ、長さが
    分からないもの（写真）は先頭だけにする。
    """
    if duration_seconds is None or duration_seconds <= 0:
        return 0
    last_by_duration = int(max(duration_seconds - 1, 0)) // STEP_SECONDS
    last = min(last_by_duration, MAX_FRAMES_PER_MEDIA - 1)
    index = max(asked, 0) // STEP_SECONDS
    return min(index, last) * STEP_SECONDS


class ThumbnailCache:
    def __init__(self, data_root: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._root = data_root / "cache" / "thumbnails"
        self.max_bytes = max_bytes
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def path_for(self, media_id: str, at: int) -> Path:
        return self._root / media_id / f"{at}.jpg"

    def get_or_create(self, media_id: str, source: Path, at: int) -> Path:
        """あれば返し、無ければ作る. **同じ絵を 2 度作らない。**"""
        target = self.path_for(media_id, at)
        if target.exists():
            return target
        with self._lock_for(f"{media_id}/{at}"):
            # 待っている間に他方が作り終えているかもしれない。
            if target.exists():
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            # **一時ファイルは要求ごとに別名にする。** 共通の名前だと、並行する
            # 要求が互いの途中の出力を壊す。
            work = target.with_suffix(f".jpg.{os.getpid()}.{threading.get_ident()}.part")
            try:
                self._extract(source, at, work)
                os.replace(work, target)
            except Exception as exc:
                work.unlink(missing_ok=True)
                self._drop_empty(target.parent)
                raise ThumbnailFailed(str(exc)) from exc
        self._evict()
        return target

    # ------------------------------------------------------------------
    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _extract(self, source: Path, at: int, destination: Path) -> None:
        """**写真も ffmpeg で読む。** 画像ライブラリを足さない（既にテストの前提）."""
        if shutil.which("ffmpeg") is None:
            raise ThumbnailFailed("ffmpeg が無い")
        command = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            *(["-ss", str(at)] if at else []),
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({LONG_EDGE},iw)':'min({LONG_EDGE},ih)':force_original_aspect_ratio=decrease",
            "-f",
            "image2",
            "-y",
            str(destination),
        ]
        result = subprocess.run(  # noqa: S603
            command, capture_output=True, timeout=TIMEOUT_SECONDS, check=False
        )
        if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
            raise ThumbnailFailed(f"ffmpeg が失敗した（{result.returncode}）")

    def _drop_empty(self, folder: Path) -> None:
        with contextlib.suppress(OSError):
            folder.rmdir()

    def _evict(self) -> None:
        """容量を超えたら古い順に消す."""
        files = sorted(self._root.rglob("*.jpg"), key=lambda path: path.stat().st_mtime)
        total = sum(path.stat().st_size for path in files)
        while files and total > self.max_bytes:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
