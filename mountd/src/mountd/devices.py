"""sysfs と blkid から候補ボリュームを列挙する.

マウントはしない。この層は読み取りだけを行う。

USB かどうかは sysfs の実パスに `/usb` を含むかで判定する。udev に依存すると
コンテナ内で動かないため。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from mediaferry_protocol.messages import UsbInfo, VolumeInfo

BlkidProbe = Callable[[str], dict[str, str]]

SECTOR_BYTES = 512
BLKID_TIMEOUT_SECONDS = 5


def blkid_probe(device_node: str) -> dict[str, str]:
    """`blkid -o export` の出力を辞書にする. 判定できなければ空の辞書."""
    # S603/S607: 引数は必ずリストで組み立てシェルを介さない。device_node は
    # sysfs から得た名前で、`--` の後ろに置いてオプション解釈もさせない。
    # 実行ファイルはコンテナイメージが提供するものに限られる。
    argv = ["blkid", "-o", "export", "--", device_node]  # noqa: S607
    try:
        out = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=BLKID_TIMEOUT_SECONDS,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    result: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    return result


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_str(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value or None


def _resolve_usb(device_dir: Path) -> UsbInfo | None:
    """ブロックデバイスの祖先を遡って USB デバイスの属性を探す."""
    for ancestor in [device_dir, *device_dir.parents]:
        vendor = _read_str(ancestor / "idVendor")
        product = _read_str(ancestor / "idProduct")
        if vendor and product:
            return UsbInfo(
                vendor_id=vendor,
                product_id=product,
                serial=_read_str(ancestor / "serial"),
            )
    return None


def _is_usb(device_dir: Path) -> bool:
    """祖先に USB バスへ属するデバイスがあるかで判定する.

    sysfs のパス文字列に "usb" を含むかで判定すると、たまたま名前が usb で
    始まる非 USB デバイス (usbfake-controller のような PCI デバイス) を拾い、
    逆にカーネルのトポロジ表現が変わると取りこぼす。各祖先の subsystem
    シンボリックリンクの向き先で判定する。
    """
    for ancestor in [device_dir, *device_dir.parents]:
        subsystem = ancestor / "subsystem"
        try:
            if subsystem.is_symlink() and subsystem.resolve().name == "usb":
                return True
        except OSError:
            continue
    return False


def _make_volume(
    name: str,
    device_dir: Path,
    dev_root: Path,
    probe: BlkidProbe,
    broker_epoch: str,
    generation: int,
) -> VolumeInfo | None:
    dev = _read_str(device_dir / "dev")
    sectors = _read_int(device_dir / "size")
    if dev is None or sectors is None:
        return None
    major_s, _, minor_s = dev.partition(":")
    try:
        major, minor = int(major_s), int(minor_s)
    except ValueError:
        return None

    device_node = str(dev_root / name)
    info = probe(device_node)
    fs_type = info.get("TYPE")
    if not fs_type:
        # ファイルシステムを持たないデバイスは候補にしない
        return None

    return VolumeInfo(
        volume_key=f"{major}:{minor}",
        device_node=device_node,
        major=major,
        minor=minor,
        sysfs_path=str(device_dir),
        fs_type=fs_type,
        fs_uuid=info.get("UUID"),
        fs_label=info.get("LABEL"),
        size_bytes=sectors * SECTOR_BYTES,
        usb=_resolve_usb(device_dir),
        broker_epoch=broker_epoch,
        generation=generation,
    )


def enumerate_volumes(
    sysfs_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    probe: BlkidProbe = blkid_probe,
    broker_epoch: str = "",
    generation: int = 0,
) -> list[VolumeInfo]:
    """USB 由来でファイルシステムを持つボリュームを列挙する.

    ディスク直にファイルシステムがある superfloppy 構成と、パーティションに
    ある構成の両方を拾う。DJI Osmo Pocket 4 は前者 (microSD) と後者 (内蔵
    ストレージ) を同時に出すため、どちらか一方では足りない。
    """
    class_block = sysfs_root / "class" / "block"
    try:
        entries = sorted(class_block.iterdir())
    except OSError:
        return []

    volumes: list[VolumeInfo] = []
    for entry in entries:
        try:
            device_dir = entry.resolve()
        except OSError:
            continue
        if not _is_usb(device_dir):
            continue
        volume = _make_volume(entry.name, device_dir, dev_root, probe, broker_epoch, generation)
        if volume is not None:
            volumes.append(volume)
    return volumes
