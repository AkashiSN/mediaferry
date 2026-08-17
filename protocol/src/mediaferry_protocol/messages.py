"""ブローカープロトコルの要求・応答の型.

サーバとクライアントの双方がこのモジュールだけを見る。ここに無い形の
メッセージは受け付けない。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from .errors import ProtocolError

REQ_LIST_VOLUMES = "list_volumes"
REQ_OPEN_VOLUME = "open_volume"
REQ_CLOSE_VOLUME = "close_volume"

REQUEST_TYPES = frozenset({REQ_LIST_VOLUMES, REQ_OPEN_VOLUME, REQ_CLOSE_VOLUME})


@dataclass(frozen=True)
class UsbInfo:
    vendor_id: str
    product_id: str
    serial: str | None


@dataclass(frozen=True)
class VolumeInfo:
    volume_key: str
    device_node: str
    major: int
    minor: int
    sysfs_path: str
    fs_type: str | None
    fs_uuid: str | None
    fs_label: str | None
    size_bytes: int
    usb: UsbInfo | None
    broker_epoch: str
    generation: int


@dataclass(frozen=True)
class VolumeExpect:
    """open_volume 時にクライアントが「これのはずだ」と主張する内容.

    サーバはマウントの直前と直後にこれを検証する。デバイスノードは
    抜き挿しで再利用されるため、ノード名だけを信用してはならない。

    `broker_epoch` は mountd の起動ごとに生成される乱数。世代番号は
    再起動で 0 に戻るため、これが無いと「mountd 再起動をまたいで残った
    古い expect」が偶然一致してしまう。Phase 1 のジョブは app と mountd の
    再起動をまたぐので、この欄は Phase 0 で wire schema に入れておく。
    """

    major: int
    minor: int
    fs_uuid: str | None
    fs_type: str
    broker_epoch: str
    generation: int


def to_wire(obj: UsbInfo | VolumeInfo | VolumeExpect) -> dict[str, Any]:
    return asdict(obj)


def _require(d: dict[str, Any], key: str, typ: type | tuple[type, ...]) -> Any:
    if key not in d:
        raise ProtocolError(f"missing field: {key}")
    value = d[key]
    if not isinstance(value, typ):
        raise ProtocolError(f"field {key} must be {typ}, got {type(value).__name__}")
    return value


def _optional(d: dict[str, Any], key: str, typ: type) -> Any:
    if key not in d:
        raise ProtocolError(f"missing field: {key}")
    value = d[key]
    if value is not None and not isinstance(value, typ):
        raise ProtocolError(f"field {key} must be {typ} or null")
    return value


def usb_from_wire(d: dict[str, Any] | None) -> UsbInfo | None:
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ProtocolError("usb must be an object or null")
    return UsbInfo(
        vendor_id=_require(d, "vendor_id", str),
        product_id=_require(d, "product_id", str),
        serial=_optional(d, "serial", str),
    )


def volume_from_wire(d: dict[str, Any]) -> VolumeInfo:
    if not isinstance(d, dict):
        raise ProtocolError("volume must be an object")
    # bool は int の派生なので明示的に弾く
    for int_field in ("major", "minor", "size_bytes", "generation"):
        if isinstance(d.get(int_field), bool):
            raise ProtocolError(f"field {int_field} must be int, got bool")
    return VolumeInfo(
        volume_key=_require(d, "volume_key", str),
        device_node=_require(d, "device_node", str),
        major=_require(d, "major", int),
        minor=_require(d, "minor", int),
        sysfs_path=_require(d, "sysfs_path", str),
        fs_type=_optional(d, "fs_type", str),
        fs_uuid=_optional(d, "fs_uuid", str),
        fs_label=_optional(d, "fs_label", str),
        size_bytes=_require(d, "size_bytes", int),
        usb=usb_from_wire(d.get("usb")),
        broker_epoch=_require(d, "broker_epoch", str),
        generation=_require(d, "generation", int),
    )


def expect_from_wire(d: dict[str, Any]) -> VolumeExpect:
    if not isinstance(d, dict):
        raise ProtocolError("expect must be an object")
    for int_field in ("major", "minor", "generation"):
        if isinstance(d.get(int_field), bool):
            raise ProtocolError(f"field {int_field} must be int, got bool")
    known = {f.name for f in fields(VolumeExpect)}
    unknown = set(d) - known
    if unknown:
        raise ProtocolError(f"unknown fields in expect: {sorted(unknown)}")
    return VolumeExpect(
        major=_require(d, "major", int),
        minor=_require(d, "minor", int),
        fs_uuid=_optional(d, "fs_uuid", str),
        fs_type=_require(d, "fs_type", str),
        broker_epoch=_require(d, "broker_epoch", str),
        generation=_require(d, "generation", int),
    )
