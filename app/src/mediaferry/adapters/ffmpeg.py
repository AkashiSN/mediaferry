"""ffmpeg による結合（§9.8）.

concat demuxer を試し、失敗したら TS 経由へ落とす。**保持するストリームの選択は
パートごとに、そのパート自身の ffprobe 結果から作る。** 先頭パートの絶対 index を
使い回すと、保持しない data track の挿入位置が違うパートで別のストリームを選ぶ。

concat demuxer は最初のファイルの構成を全体に適用するので、全パートの構成が
一致するときだけ試す。一致しなければ preflight で弾いて TS 経路へ送る。

TS 経路では、選択を各パートの mpegts 化の段で適用する。mpegts の中では
ストリーム index が振り直されるので、結合の段で MP4 の絶対 index を使うと
別のストリームを指す。**mpegts は QuickTime の data track（tmcd / djmd）を
運べない**ので、保持を宣言されていても外し、外したことを呼び出し元へ返す。
map に残したままだと mux が拒否して、検証できる出力そのものが作られない。

外部プロセスはプロセスグループとして起動し、キャンセル時は SIGTERM → 猶予 →
SIGKILL の順に送って必ず刈り取る（§9.9）。
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.merge.streams import (
    map_arguments,
    selected_streams,
    stream_signature,
    stream_summary,
    ts_route_blockers,
)
from ..core.profiles.model import KeepStreams

logger = logging.getLogger(__name__)

# キャンセル要求に気づくまでの間隔。
POLL_INTERVAL = 0.5
# リースを延ばす間隔。リース (60 秒) の 1/3。poll のたびに書くと、30 分の結合で
# 数千回 WAL へ書き、API とキャンセルの書き込みロックに不要に競合する。
PULSE_INTERVAL = 20.0
TERM_GRACE_SECONDS = 5.0
VERSION_TIMEOUT_SECONDS = 30
# 失敗の理由を伝えるのに要る分だけ。ログ全体は work/ に残る。
LOG_TAIL_CHARS = 2000

# mpegts が運べない種別。tmcd（タイムコード）と djmd / dbgi がここに入る。
UNSUPPORTED_BY_TS = frozenset({"data"})
# **concat demuxer も data を運べない。** `-map` に残すと
# `Cannot map stream #0:N - unsupported type` で即座に落ちる（実機の DJI は
# `tmcd` を持つので毎回これで TS 経路へ落ちていた）。TS 経路も同じものを落とす
# ので、最初から選ばなければ失うものは無く、往復を丸ごと省ける。
UNSUPPORTED_BY_CONCAT = frozenset({"data"})

# MP4 の中の H.264 / H.265 を mpegts へ入れるには Annex B へ直す。
_ANNEXB = {"h264": "h264_mp4toannexb", "hevc": "hevc_mp4toannexb"}

# TS 片の中でのストリームの並び。ここに無い種別は後ろへ回す。
_TS_TYPE_ORDER = {"video": 0, "audio": 1}


class MergeFailed(RuntimeError):
    """ffmpeg が非 0 で終了した."""


class MergeCancelled(RuntimeError):
    """キャンセル要求を観測して外部プロセスを刈った."""


@dataclass(frozen=True)
class MergeOutcome:
    route: str  # concat / ts
    output_path: Path
    tool_version: str
    # 保持を宣言されていたのに、経路のコンテナが運べずに外したストリーム。
    dropped_by_route: tuple[dict[str, Any], ...]


class MergeRunner:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        poll_interval: float = POLL_INTERVAL,
        pulse_interval: float = PULSE_INTERVAL,
        term_grace_seconds: float = TERM_GRACE_SECONDS,
    ) -> None:
        self._ffmpeg = ffmpeg_path
        self._poll_interval = poll_interval
        self._pulse_interval = pulse_interval
        self._term_grace = term_grace_seconds

    def merge(
        self,
        parts: Sequence[Path],
        part_streams: Sequence[Sequence[dict[str, Any]]],
        keep: KeepStreams,
        work_dir: Path,
        output_name: str,
        on_progress: Callable[[], None],
        cancelled: Callable[[], bool],
        on_note: Callable[[str], None] | None = None,
    ) -> MergeOutcome:
        """`on_note` は経路の判断を呼び出し側へ伝える.

        **どの経路を通ったかは、その出力がなぜその形なのかの説明**なので、
        コンテナのログではなくジョブの記録に残す（`job_event`）。ログにも
        残すのは、ジョブの外（起動時など）から追う場合のため。
        """

        def note(message: str) -> None:
            logger.warning("%s", message)
            if on_note is not None:
                on_note(message)

        selections = [selected_streams(streams, keep) for streams in part_streams]
        output = work_dir / output_name
        if _topology_matches(part_streams, selections):
            carried, dropped = _split_unsupported(selections[0], UNSUPPORTED_BY_CONCAT)
            try:
                self._run(
                    self._concat_command(parts, map_arguments(carried), work_dir, output),
                    work_dir / "concat.log",
                    on_progress,
                    cancelled,
                )
                return MergeOutcome("concat", output, self.tool_version(), dropped)
            except MergeFailed as exc:
                _refuse_ts_if_lossy(part_streams[0], keep)
                note(f"concat demuxer に失敗した。TS 経由へ落とす: {exc}")
        else:
            _refuse_ts_if_lossy(part_streams[0], keep)
            note("パート間でストリームの並びが違うので concat demuxer を使わない")
        output.unlink(missing_ok=True)
        dropped = self._ts_merge(
            parts, part_streams, selections, work_dir, output, on_progress, cancelled
        )
        return MergeOutcome("ts", output, self.tool_version(), dropped)

    def tool_version(self) -> str:
        completed = subprocess.run(  # noqa: S603
            [self._ffmpeg, "-version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
        return completed.stdout.splitlines()[0].strip()

    # ------------------------------------------------------------------
    def _concat_command(
        self, parts: Sequence[Path], maps: list[str], work_dir: Path, output: Path
    ) -> list[str]:
        listing = work_dir / "concat.txt"
        listing.write_text("".join(f"file '{_escape(part)}'\n" for part in parts), encoding="utf-8")
        return [
            self._ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-fflags",
            "+genpts",
            "-i",
            str(listing),
            *maps,
            "-c",
            "copy",
            "-y",
            str(output),
        ]

    def _ts_merge(
        self,
        parts: Sequence[Path],
        part_streams: Sequence[Sequence[dict[str, Any]]],
        selections: Sequence[Sequence[dict[str, Any]]],
        work_dir: Path,
        output: Path,
        on_progress: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> tuple[dict[str, Any], ...]:
        """各パートを mpegts にしてから `concat:` で結合する.

        map と bitstream filter は**そのパート自身の**構成から作る。
        """
        dropped: dict[tuple[Any, ...], dict[str, Any]] = {}
        pieces: list[Path] = []
        for index, part in enumerate(parts):
            carried = []
            for stream in selections[index]:
                if stream.get("codec_type") in UNSUPPORTED_BY_TS:
                    summary = stream_summary(stream)
                    dropped[(summary["codec_type"], summary["codec_tag_string"])] = summary
                    continue
                carried.append(stream)
            piece = work_dir / f"part-{index:04d}.ts"
            self._run(
                [
                    self._ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(part),
                    *map_arguments(_ts_layout(carried)),
                    "-c",
                    "copy",
                    *_video_bitstream(part_streams[index]),
                    "-f",
                    "mpegts",
                    "-y",
                    str(piece),
                ],
                work_dir / f"ts-{index:04d}.log",
                on_progress,
                cancelled,
            )
            pieces.append(piece)
        self._run(
            [
                self._ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                "concat:" + "|".join(str(piece) for piece in pieces),
                "-map",
                "0",
                "-c",
                "copy",
                *_audio_bitstream(part_streams[0]),
                "-y",
                str(output),
            ],
            work_dir / "ts-join.log",
            on_progress,
            cancelled,
        )
        return tuple(dropped.values())

    def _run(
        self,
        command: list[str],
        log_path: Path,
        on_progress: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> None:
        # 引数配列で起動する。シェル文字列は組み立てない（§14）。
        with log_path.open("wb") as log:
            process = subprocess.Popen(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                # プロセスグループを分けて、子を取り残さずに刈れるようにする。
                start_new_session=True,
            )
            try:
                # 起動直後に 1 回打つ。短い結合でも heartbeat が 0 回にならない。
                on_progress()
                last_pulse = time.monotonic()
                while process.poll() is None:
                    # キャンセルは細かく見る。応答を待たせない。
                    if cancelled():
                        self._kill(process)
                        raise MergeCancelled("キャンセル要求を観測した")
                    # **リースの延長は throttle する。** poll のたびに打つと、
                    # 30 分の結合で数千回 WAL へ書き、API とキャンセルの
                    # 書き込みロックに不要に競合する。
                    if time.monotonic() - last_pulse >= self._pulse_interval:
                        on_progress()
                        last_pulse = time.monotonic()
                    time.sleep(self._poll_interval)
            finally:
                if process.poll() is None:
                    self._kill(process)
        if process.returncode != 0:
            raise MergeFailed(f"ffmpeg が {process.returncode} で終了した: {_tail(log_path)}")

    def _kill(self, process: subprocess.Popen[bytes]) -> None:
        """プロセスグループ単位で送り、子プロセスを取り残さない."""
        try:
            group = os.getpgid(process.pid)
        except ProcessLookupError:
            process.wait()
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGTERM)
        deadline = time.monotonic() + self._term_grace
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(self._poll_interval)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
        process.wait()


def _split_unsupported(
    streams: Sequence[dict[str, Any]], unsupported: frozenset[str]
) -> tuple[list[dict[str, Any]], tuple[dict[str, Any], ...]]:
    """運べる型と、経路が運べない型に分ける. 落としたものは記録して返す."""
    carried: list[dict[str, Any]] = []
    dropped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for stream in streams:
        if stream.get("codec_type") in unsupported:
            summary = stream_summary(stream)
            dropped[(summary["codec_type"], summary["codec_tag_string"])] = summary
            continue
        carried.append(stream)
    return carried, tuple(dropped.values())


def _topology_matches(
    part_streams: Sequence[Sequence[dict[str, Any]]],
    selections: Sequence[Sequence[dict[str, Any]]],
) -> bool:
    """concat demuxer を使ってよいか.

    demuxer は最初のファイルの構成を全体に適用するので、**全ストリームの
    構成**と**保持対象の絶対 index の並び**の両方が一致していることを求める。
    """
    if len({stream_signature(streams) for streams in part_streams}) != 1:
        return False
    return len({tuple(s["index"] for s in selection) for selection in selections}) == 1


def _ts_layout(streams: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """TS 片のストリームの並びを、パートによらず種別順に揃える.

    `concat:` は mpegts の生バイトを継ぐので、パートごとに並びが違うと後続の
    パートを読めない（`No start code is found` で mux が落ちる）。**map に使う
    index はそのパート自身のもの**のままにして、並びだけを揃える。
    """
    return sorted(
        streams, key=lambda s: _TS_TYPE_ORDER.get(str(s.get("codec_type")), len(_TS_TYPE_ORDER))
    )


def _escape(path: Path) -> str:
    """concat demuxer の単一引用符の中で使える形にする."""
    return str(path).replace("'", "'\\''")


def _video_bitstream(streams: Sequence[dict[str, Any]]) -> list[str]:
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        name = _ANNEXB.get(str(stream.get("codec_name")))
        return [] if name is None else ["-bsf:v", name]
    return []


def _audio_bitstream(streams: Sequence[dict[str, Any]]) -> list[str]:
    """TS から MP4 へ戻す段で ADTS の AAC を ASC へ直す."""
    if any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in streams):
        return ["-bsf:a", "aac_adtstoasc"]
    return []


def _tail(log_path: Path) -> str:
    text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    return text[-LOG_TAIL_CHARS:]


def _refuse_ts_if_lossy(streams: Sequence[dict[str, Any]], keep: KeepStreams) -> None:
    """TS 経路が音を捨てるなら、走らせる前に諦める.

    **運べないと分かっているものを、運べるか試してから諦める理由が無い。**
    4 GB のパートを mpegts へ書き直すだけで数分かかる。
    """
    blockers = ts_route_blockers(streams, keep)
    if blockers:
        names = "・".join(str(b["codec_name"]) for b in blockers)
        raise MergeFailed(f"TS 経路は {names} を運べないので結合できない")
