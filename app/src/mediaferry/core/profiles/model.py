"""デバイスプロファイルの定義と検証.

機種差をコードの分岐ではなく設定の差分として表す。定義は DB のリビジョンに
JSON で保存され、取り込み・結合・アップロードの各レコードが使用したリビジョン
ID を持つ。

パスを含む項目は、マウントルートの外へ抜ける経路を作らせないため、単一の
安全な構成要素だけを許す（§14）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .patterns import compile_pattern

PROFILE_SCHEMA_VERSION = 1
BUILTIN_DIR = Path(__file__).parent / "builtin"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TIMESTAMP_SOURCES = ("filename", "exif", "mtime")
_TIMEZONE_POLICIES = ("none", "force_offset")
# mtime が何を表すか。exFAT の `OffsetFromUtc` を書く媒体なら真の瞬間、
# 書かない媒体（FAT32、valid bit の立たない exFAT）なら現地の壁時計を UTC と
# 見なした疑似 epoch になる。**媒体の性質なので、形からは推定できない。**
_MTIME_SEMANTICS = ("wall_clock", "instant")
_VIDEO_KEEP = ("primary", "all")
# 正規表現の長さの上限。書き間違いを早く教えるためのもので、
# catastrophic backtracking はこれでは防げない（patterns.py を見よ）。
MAX_PATTERN_LENGTH = 512
_AUDIO_KEEP = ("none", "primary", "all")


class ProfileInvalid(ValueError):
    """定義が仕様を満たさない."""


@dataclass(frozen=True)
class Hints:
    usb_ids: tuple[str, ...]
    volume_labels: tuple[str, ...]


@dataclass(frozen=True)
class Require:
    roots: tuple[str, ...]
    filename_pattern: str
    min_matching_files: int


@dataclass(frozen=True)
class ScanRule:
    roots: tuple[str, ...]
    extensions: tuple[str, ...]


@dataclass(frozen=True)
class TimestampRule:
    source: str
    pattern: str | None
    format: str | None
    fallback: str
    timezone_policy: str
    timezone: str | None
    # 既定は `wall_clock`。**宣言の無い定義に「瞬間」を仮定しない**（§6）。
    mtime_semantics: str = "wall_clock"


@dataclass(frozen=True)
class KeepStreams:
    video: str
    audio: str
    timecode: bool
    data: bool


@dataclass(frozen=True)
class MergeRule:
    enabled: bool
    tolerance_seconds: int
    min_part_size_gib: int
    sequence_pattern: str
    output_name: str
    keep_streams: KeepStreams


@dataclass(frozen=True)
class StackRule:
    """RAW+JPEG の組の規則（§6）.

    `extensions` は**先頭ほど primary**。**撮影時刻は見ない** —— 組の身元は
    カード上の原名と同席の証拠で決まる（`docs/history/phase10-design.md`）。
    """

    enabled: bool
    extensions: tuple[str, ...]


# **`stack` は省略できる。** 既存リビジョンの `definition_json` にこのキーは無く、
# 必須にすると適用済みの DB を開けなくなる。
STACK_DISABLED = StackRule(enabled=False, extensions=())


@dataclass(frozen=True)
class ImmichRule:
    tags: tuple[str, ...]
    tag_pre_existing: bool
    fix_datetime_after_upload: bool


@dataclass(frozen=True)
class ProfileDefinition:
    slug: str
    name: str
    hints: Hints
    require: Require
    scan: ScanRule
    timestamp: TimestampRule
    merge: MergeRule
    stack: StackRule
    immich: ImmichRule


def parse_definition(data: Mapping[str, Any]) -> ProfileDefinition:
    _reject_unknown(
        data,
        {"slug", "name", "hints", "require", "scan", "timestamp", "merge", "stack", "immich"},
        "profile",
    )
    slug = _string(data, "slug")
    if not _SLUG_RE.match(slug):
        raise ProfileInvalid(f"slug は英小文字・数字・ハイフンのみ: {slug}")
    scan = _parse_scan(_mapping(data, "scan"))
    return ProfileDefinition(
        slug=slug,
        name=_string(data, "name"),
        hints=_parse_hints(_mapping(data, "hints")),
        require=_parse_require(_mapping(data, "require")),
        scan=scan,
        timestamp=_parse_timestamp(_mapping(data, "timestamp")),
        merge=_parse_merge(_mapping(data, "merge")),
        stack=_parse_stack(_mapping(data, "stack"), scan) if "stack" in data else STACK_DISABLED,
        immich=_parse_immich(_mapping(data, "immich")),
    )


def definition_to_json(defn: ProfileDefinition) -> str:
    """DB へ入れる正規形. 差分検出に使うのでキー順を固定する."""
    return json.dumps(asdict(defn), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_builtin_definitions() -> list[ProfileDefinition]:
    out = []
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        out.append(parse_definition(yaml.safe_load(path.read_text(encoding="utf-8"))))
    return out


# ----------------------------------------------------------------------
def _reject_unknown(data: Mapping[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise ProfileInvalid(f"{where} に未知のキー: {', '.join(unknown)}")


def _mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ProfileInvalid(f"{key} はオブジェクトである必要がある")
    return value


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ProfileInvalid(f"{key} は空でない文字列である必要がある")
    return value


def _bool(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ProfileInvalid(f"{key} は真偽値である必要がある")
    return value


def _positive_int(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProfileInvalid(f"{key} は 0 以上の整数である必要がある")
    return value


def _strings(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ProfileInvalid(f"{key} は文字列の配列である必要がある")
    for item in value:
        if not isinstance(item, str):
            raise ProfileInvalid(f"{key} の要素は文字列である必要がある")
    return tuple(value)


def _safe_components(names: Sequence[str], key: str) -> tuple[str, ...]:
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
            raise ProfileInvalid(f"{key} は単一の安全なディレクトリ名である必要がある: {name!r}")
    return tuple(names)


def _regex(data: Mapping[str, Any], key: str) -> str:
    """正規表現として読めることと、長さの上限だけを見る.

    **固まらないことの保証はここでは作れない。** 保存時に敵対的な標本を試しても
    `(z+)+$` は `a` の標本を素通りする。実行時の上限（`patterns.py` の
    `timeout`）が保証を持つ。ここは「書き間違いを早く教える」ための検査。
    """
    pattern = _string(data, key)
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ProfileInvalid(f"{key} が長すぎる（{len(pattern)} > {MAX_PATTERN_LENGTH}）")
    try:
        compile_pattern(pattern)
    except Exception as exc:  # noqa: BLE001 - regex は独自の例外型を投げる
        raise ProfileInvalid(f"{key} が正規表現として不正: {exc}") from exc
    return pattern


def _parse_hints(data: Mapping[str, Any]) -> Hints:
    _reject_unknown(data, {"usb_ids", "volume_labels"}, "hints")
    return Hints(usb_ids=_strings(data, "usb_ids"), volume_labels=_strings(data, "volume_labels"))


def _parse_require(data: Mapping[str, Any]) -> Require:
    _reject_unknown(data, {"roots", "filename_pattern", "min_matching_files"}, "require")
    return Require(
        roots=_safe_components(_strings(data, "roots"), "require.roots"),
        filename_pattern=_regex(data, "filename_pattern"),
        min_matching_files=_positive_int(data, "min_matching_files"),
    )


def _parse_scan(data: Mapping[str, Any]) -> ScanRule:
    _reject_unknown(data, {"roots", "extensions"}, "scan")
    extensions = _strings(data, "extensions")
    for ext in extensions:
        if ext != ext.upper() or ext.startswith("."):
            raise ProfileInvalid(f"scan.extensions はドット無しの大文字で書く: {ext!r}")
    return ScanRule(
        roots=_safe_components(_strings(data, "roots"), "scan.roots"), extensions=extensions
    )


def _parse_timestamp(data: Mapping[str, Any]) -> TimestampRule:
    _reject_unknown(
        data,
        {
            "source",
            "pattern",
            "format",
            "fallback",
            "timezone_policy",
            "timezone",
            "mtime_semantics",
        },
        "timestamp",
    )
    source = _string(data, "source")
    if source not in _TIMESTAMP_SOURCES:
        raise ProfileInvalid(f"timestamp.source は {_TIMESTAMP_SOURCES} のいずれか")
    fallback = _string(data, "fallback")
    if fallback not in _TIMESTAMP_SOURCES:
        raise ProfileInvalid(f"timestamp.fallback は {_TIMESTAMP_SOURCES} のいずれか")
    policy = _string(data, "timezone_policy")
    if policy not in _TIMEZONE_POLICIES:
        raise ProfileInvalid(f"timestamp.timezone_policy は {_TIMEZONE_POLICIES} のいずれか")
    pattern = data.get("pattern")
    fmt = data.get("format")
    if source == "filename":
        if not isinstance(pattern, str):
            raise ProfileInvalid("source が filename なら timestamp.pattern が要る")
        _regex(data, "pattern")
        if "(?P<ts>" not in pattern:
            raise ProfileInvalid("timestamp.pattern は名前付きグループ ts を持つ必要がある")
        if not isinstance(fmt, str):
            raise ProfileInvalid("source が filename なら timestamp.format が要る")
    timezone = data.get("timezone")
    if timezone is not None and not isinstance(timezone, str):
        raise ProfileInvalid("timestamp.timezone は文字列か null")
    # **既定は `wall_clock`。** 書いていない定義（この欄より前に作られたもの）に
    # 「瞬間」を仮定すると、`OffsetFromUtc` を書かない媒体で黙ってずれる。
    semantics = data.get("mtime_semantics", "wall_clock")
    if semantics not in _MTIME_SEMANTICS:
        raise ProfileInvalid(f"timestamp.mtime_semantics は {_MTIME_SEMANTICS} のいずれか")
    return TimestampRule(
        source=source,
        pattern=pattern if isinstance(pattern, str) else None,
        format=fmt if isinstance(fmt, str) else None,
        fallback=fallback,
        timezone_policy=policy,
        timezone=timezone,
        mtime_semantics=semantics,
    )


def _parse_keep_streams(data: Mapping[str, Any]) -> KeepStreams:
    _reject_unknown(data, {"video", "audio", "timecode", "data"}, "merge.keep_streams")
    video, audio = _string(data, "video"), _string(data, "audio")
    if video not in _VIDEO_KEEP:
        raise ProfileInvalid(f"keep_streams.video は {_VIDEO_KEEP} のいずれか")
    if audio not in _AUDIO_KEEP:
        raise ProfileInvalid(f"keep_streams.audio は {_AUDIO_KEEP} のいずれか")
    return KeepStreams(
        video=video, audio=audio, timecode=_bool(data, "timecode"), data=_bool(data, "data")
    )


def _parse_merge(data: Mapping[str, Any]) -> MergeRule:
    _reject_unknown(
        data,
        {
            "enabled",
            "tolerance_seconds",
            "min_part_size_gib",
            "sequence_pattern",
            "output_name",
            "keep_streams",
        },
        "merge",
    )
    enabled = _bool(data, "enabled")
    if not enabled:
        # **無効なら連番の規則も出力名も要らない。** 書かせると、使われない値を
        # 発明することになる（canon-eos と generic-dcim は結合を持たない）。
        return MergeRule(
            enabled=False,
            tolerance_seconds=_positive_int(data, "tolerance_seconds"),
            min_part_size_gib=_positive_int(data, "min_part_size_gib"),
            sequence_pattern="",
            output_name="",
            keep_streams=_parse_keep_streams(_mapping(data, "keep_streams")),
        )
    output_name = _string(data, "output_name")
    _safe_components([output_name], "merge.output_name")
    return MergeRule(
        enabled=True,
        tolerance_seconds=_positive_int(data, "tolerance_seconds"),
        min_part_size_gib=_positive_int(data, "min_part_size_gib"),
        sequence_pattern=_regex(data, "sequence_pattern"),
        output_name=output_name,
        keep_streams=_parse_keep_streams(_mapping(data, "keep_streams")),
    )


def _parse_stack(data: Mapping[str, Any], scan: ScanRule) -> StackRule:
    # **`tolerance_seconds` は許すが読まない。** 既存リビジョンの
    # `definition_json` に入っているので、弾くと適用済みの DB を開けなくなる。
    _reject_unknown(data, {"enabled", "extensions", "tolerance_seconds"}, "stack")
    if not _bool(data, "enabled"):
        # 無効なら拡張子も要らない（`merge` と同じ扱い）。
        return STACK_DISABLED
    extensions = _strings(data, "extensions")
    for ext in extensions:
        if ext != ext.upper() or ext.startswith("."):
            raise ProfileInvalid(f"stack.extensions はドット無しの大文字で書く: {ext!r}")
        if ext not in scan.extensions:
            raise ProfileInvalid(f"stack.extensions が scan.extensions に無い: {ext}")
    if len(extensions) < 2:
        raise ProfileInvalid("stack.extensions は 2 つ以上必要（1 つでは組にならない）")
    if len(set(extensions)) != len(extensions):
        raise ProfileInvalid(f"stack.extensions に重複がある: {extensions}")
    return StackRule(enabled=True, extensions=extensions)


def _parse_immich(data: Mapping[str, Any]) -> ImmichRule:
    _reject_unknown(data, {"tags", "tag_pre_existing", "fix_datetime_after_upload"}, "immich")
    return ImmichRule(
        tags=_strings(data, "tags"),
        tag_pre_existing=_bool(data, "tag_pre_existing"),
        fix_datetime_after_upload=_bool(data, "fix_datetime_after_upload"),
    )
