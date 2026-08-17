from pathlib import Path

from mountd.devices import enumerate_volumes


def make_sysfs(root: Path) -> None:
    """Osmo Pocket 4 の実測構成を模した sysfs ツリーを作る.

    内蔵ストレージ (sdj + パーティション sdj1) と microSD (sdk, superfloppy) を
    出す。加えて USB でない内蔵 SATA ディスク (sda) を混ぜ、除外されることを見る。
    """
    # subsystem シンボリックリンクの向き先。実機の /sys/bus/* に相当する。
    bus_usb = root / "bus/usb"
    bus_pci = root / "bus/pci"
    bus_usb.mkdir(parents=True)
    bus_pci.mkdir(parents=True)

    usb_base = root / "devices/pci0000:00/usb2/2-4"
    sata_base = root / "devices/pci0000:00/ata1/host0"
    # 名前に usb を含むが USB ではない PCI デバイス。パス文字列での判定なら
    # 誤って拾ってしまうケース。
    lookalike_base = root / "devices/pci0000:00/usbfake-controller/host9"

    # USB ディスク sdj (パーティションあり)
    sdj = usb_base / "2-4:1.8/host33/target33:0:0/33:0:0:0/block/sdj"
    sdj.mkdir(parents=True)
    (sdj / "dev").write_text("8:144\n")
    (sdj / "size").write_text(str(108 * 1024 * 1024 * 2) + "\n")
    sdj1 = sdj / "sdj1"
    sdj1.mkdir()
    (sdj1 / "dev").write_text("8:145\n")
    (sdj1 / "size").write_text(str(108 * 1024 * 1024 * 2) + "\n")
    (sdj1 / "partition").write_text("1\n")

    # USB ディスク sdk (superfloppy)
    sdk = usb_base / "2-4:1.2/host32/target32:0:0/32:0:0:0/block/sdk"
    sdk.mkdir(parents=True)
    (sdk / "dev").write_text("8:160\n")
    (sdk / "size").write_text(str(477 * 1024 * 1024 * 2) + "\n")

    # 内蔵 SATA
    sda = sata_base / "block/sda"
    sda.mkdir(parents=True)
    (sda / "dev").write_text("8:0\n")
    (sda / "size").write_text(str(5500 * 1024 * 1024 * 2) + "\n")
    (sata_base.parent / "subsystem").symlink_to(bus_pci)

    # 名前だけ usb っぽい非 USB デバイス
    sdz = lookalike_base / "block/sdz"
    sdz.mkdir(parents=True)
    (sdz / "dev").write_text("65:144\n")
    (sdz / "size").write_text(str(100 * 1024 * 1024 * 2) + "\n")
    (lookalike_base.parent / "subsystem").symlink_to(bus_pci)

    # USB デバイスの属性と subsystem (2-4 の階層に置く)
    usb_dev = root / "devices/pci0000:00/usb2/2-4"
    (usb_dev / "idVendor").write_text("2ca3\n")
    (usb_dev / "idProduct").write_text("0020\n")
    (usb_dev / "serial").write_text("ANGZP3K002QM4K\n")
    (usb_dev / "subsystem").symlink_to(bus_usb)

    # /sys/class/block のシンボリックリンク
    class_block = root / "class/block"
    class_block.mkdir(parents=True)
    for target in (sdj, sdj1, sdk, sda, sdz):
        (class_block / target.name).symlink_to(target)


def fake_probe(device_node: str) -> dict[str, str]:
    return {
        "/dev/sdj1": {"TYPE": "exfat", "UUID": "4356-50A7", "LABEL": "Pocket4"},
        "/dev/sdk": {"TYPE": "exfat", "UUID": "26B1-2FD6", "LABEL": "SD_Card"},
        "/dev/sda": {"TYPE": "zfs_member", "UUID": "3640321031764899222"},
        "/dev/sdz": {"TYPE": "exfat", "UUID": "AAAA-BBBB", "LABEL": "NotUsb"},
    }.get(device_node, {})


def test_only_usb_volumes_with_a_filesystem_are_listed(tmp_path):
    make_sysfs(tmp_path)
    vols = enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe, generation=3)
    nodes = sorted(v.device_node for v in vols)
    # sda は USB でないので除外。sdj はディスク自体に FS が無いので除外。
    # sdz はパス名に usb を含むが subsystem が pci なので除外。
    assert nodes == ["/dev/sdj1", "/dev/sdk"]


def test_a_non_usb_device_whose_path_contains_usb_is_excluded(tmp_path):
    """パス文字列での判定なら誤って拾ってしまうケースを弾けているか."""
    make_sysfs(tmp_path)
    vols = enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe)
    assert all("sdz" not in v.device_node for v in vols)


def test_superfloppy_and_partition_are_both_found(tmp_path):
    make_sysfs(tmp_path)
    vols = {v.device_node: v for v in enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe)}
    assert vols["/dev/sdk"].fs_label == "SD_Card"
    assert vols["/dev/sdj1"].fs_label == "Pocket4"


def test_volume_carries_major_minor_and_size(tmp_path):
    make_sysfs(tmp_path)
    vols = {v.device_node: v for v in enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe)}
    sdk = vols["/dev/sdk"]
    assert (sdk.major, sdk.minor) == (8, 160)
    assert sdk.size_bytes == 477 * 1024 * 1024 * 2 * 512
    assert sdk.volume_key == "8:160"


def test_usb_identity_is_resolved_from_ancestors(tmp_path):
    make_sysfs(tmp_path)
    vols = {v.device_node: v for v in enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe)}
    usb = vols["/dev/sdk"].usb
    assert usb is not None
    assert (usb.vendor_id, usb.product_id, usb.serial) == ("2ca3", "0020", "ANGZP3K002QM4K")


def test_generation_and_epoch_are_stamped(tmp_path):
    make_sysfs(tmp_path)
    vols = enumerate_volumes(
        sysfs_root=tmp_path, probe=fake_probe, broker_epoch="abc123", generation=42
    )
    assert all(v.generation == 42 for v in vols)
    assert all(v.broker_epoch == "abc123" for v in vols)


def test_missing_sysfs_yields_nothing(tmp_path):
    assert enumerate_volumes(sysfs_root=tmp_path, probe=fake_probe) == []
