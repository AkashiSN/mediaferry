from dataclasses import MISSING, fields

import pytest

from mediaferry_protocol.errors import ProtocolError
from mediaferry_protocol.messages import (
    REQ_LIST_VOLUMES,
    UsbInfo,
    VolumeExpect,
    VolumeInfo,
    expect_from_wire,
    to_wire,
    usb_from_wire,
    volume_from_wire,
)


def sample_volume() -> VolumeInfo:
    return VolumeInfo(
        volume_key="8:160",
        device_node="/dev/sdk",
        major=8,
        minor=160,
        sysfs_path="/sys/devices/pci0000:00/usb2/2-4/block/sdk",
        fs_type="exfat",
        fs_uuid="26B1-2FD6",
        fs_label="SD_Card",
        size_bytes=512_110_190_592,
        usb=UsbInfo(
            vendor_id="2ca3",
            product_id="0020",
            product="OsmoPocket4-ABC123",
            serial="TESTSERIAL0001",
        ),
        broker_epoch="c0ffee",
        generation=7,
    )


def test_volume_roundtrip():
    v = sample_volume()
    assert volume_from_wire(to_wire(v)) == v


def test_volume_without_usb_roundtrip():
    v = sample_volume()
    v = VolumeInfo(**{**v.__dict__, "usb": None, "fs_uuid": None, "fs_label": None})
    assert volume_from_wire(to_wire(v)) == v


def test_expect_roundtrip():
    e = VolumeExpect(
        major=8,
        minor=160,
        fs_uuid="26B1-2FD6",
        fs_type="exfat",
        broker_epoch="c0ffee",
        generation=7,
    )
    assert expect_from_wire(to_wire(e)) == e


def test_expect_missing_broker_epoch_is_rejected():
    """mountd 再起動をまたいだ古い expect を弾くための必須欄."""
    d = to_wire(
        VolumeExpect(
            major=8,
            minor=160,
            fs_uuid="26B1-2FD6",
            fs_type="exfat",
            broker_epoch="c0ffee",
            generation=7,
        )
    )
    del d["broker_epoch"]
    with pytest.raises(ProtocolError):
        expect_from_wire(d)


def test_missing_field_raises():
    d = to_wire(sample_volume())
    del d["major"]
    with pytest.raises(ProtocolError):
        volume_from_wire(d)


def test_wrong_type_raises():
    d = to_wire(sample_volume())
    d["major"] = "8"
    with pytest.raises(ProtocolError):
        volume_from_wire(d)


def test_request_constants_are_stable():
    assert REQ_LIST_VOLUMES == "list_volumes"


def test_usb_info_carries_the_product_string():
    """serial は機種の既定値でありうる. 機体固有の文字列は product 側にある."""
    usb = usb_from_wire(
        {
            "vendor_id": "2ca3",
            "product_id": "0020",
            "product": "OsmoPocket4-ABC123",
            "serial": "123456789ABCDEF",
        }
    )
    assert usb.product == "OsmoPocket4-ABC123"


def test_usb_product_may_be_absent():
    """product を公開しないデバイスもある. null を受け付ける."""
    usb = usb_from_wire(
        {"vendor_id": "2ca3", "product_id": "0020", "product": None, "serial": None}
    )
    assert usb.product is None


def test_a_wire_message_without_the_product_field_is_rejected():
    """「値が無い」と「欄ごと無い」を分ける.

    product を送らない mountd を黙って通すと、serial が機種の既定値である
    機体を 2 台挿したときに同一デバイスと誤認する。
    """
    with pytest.raises(ProtocolError, match="product"):
        usb_from_wire({"vendor_id": "2ca3", "product_id": "0020", "serial": None})


def test_usb_info_has_no_default_values():
    """既定値を付けると、product を埋め忘れた組み立てが黙って通る."""
    assert [f.name for f in fields(UsbInfo) if f.default is not MISSING] == []
