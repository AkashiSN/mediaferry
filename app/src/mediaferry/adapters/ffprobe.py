"""メディアの種別と duration の確定.

公開前にメタデータを確定させるため（§9.3 手順 5）、ここで得た結果が
そのまま media_file に入る。ffprobe が正当に失敗した場合と、そもそも実行して
いない場合を probe_state で区別する。

duration は §9.7 の結合グループ検出が境界判定に使うので、失敗を
「0 秒」に丸めない。
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PHOTO_EXTENSIONS = frozenset({"JPG", "JPEG", "PNG", "HEIC", "DNG", "CR2", "CR3", "RAW"})


@dataclass(frozen=True)
class ProbeResult:
    kind: str  # photo / video
    duration_seconds: float | None
    probe_state: str  # ok / failed / not_applicable
    streams: list[dict[str, Any]] = field(default_factory=list)
    # 器が申告した撮影時刻（`format.tags.creation_time`）。**解釈しない。**
    # 現地の壁時計に `Z` を付ける機種があるので、ここで UTC として読むと
    # ずれが固定される。意味は `core/timestamps.py` が決める。
    container_wall: str | None = None


class MediaProbe:
    def __init__(self, ffprobe_path: str = "ffprobe", timeout_seconds: int = 60) -> None:
        self._ffprobe = ffprobe_path
        self._timeout = timeout_seconds

    def describe(self, path: Path, extension: str) -> ProbeResult:
        if extension.upper() in PHOTO_EXTENSIONS:
            return ProbeResult(kind="photo", duration_seconds=None, probe_state="not_applicable")
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    self._ffprobe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            payload = json.loads(completed.stdout)
            duration = float(payload["format"]["duration"])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            logger.warning("ffprobe に失敗した: %s (%s)", path.name, exc)
            return ProbeResult(kind="video", duration_seconds=None, probe_state="failed")
        return ProbeResult(
            kind="video",
            duration_seconds=duration,
            probe_state="ok",
            streams=payload.get("streams", []),
            container_wall=_container_wall(payload),
        )


def _container_wall(payload: dict[str, Any]) -> str | None:
    """`format.tags.creation_time` を文字列のまま返す. 無ければ `None`."""
    raw = payload.get("format", {}).get("tags", {}).get("creation_time")
    return raw if isinstance(raw, str) else None
