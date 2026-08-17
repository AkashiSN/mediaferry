import errno
import os
import subprocess
from dataclasses import replace

import pytest

from mediaferry_protocol.messages import UsbInfo, VolumeExpect, VolumeInfo
from mountd.mounts import (
    MOUNT_OPTIONS,
    MountFailed,
    MountManager,
    MountRejected,
)


def volume(**over) -> VolumeInfo:
    base = VolumeInfo(
        volume_key="8:160",
        device_node="/dev/sdk",
        major=8,
        minor=160,
        sysfs_path="/sys/devices/pci0000:00/usb2/2-4/block/sdk",
        fs_type="exfat",
        fs_uuid="26B1-2FD6",
        fs_label="SD_Card",
        size_bytes=512_110_190_592,
        usb=UsbInfo(vendor_id="2ca3", product_id="0020", serial="X"),
        broker_epoch="epoch-1",
        generation=7,
    )
    return replace(base, **over)


def expect(**over) -> VolumeExpect:
    base = VolumeExpect(
        major=8,
        minor=160,
        fs_uuid="26B1-2FD6",
        fs_type="exfat",
        broker_epoch="epoch-1",
        generation=7,
    )
    return replace(base, **over)


class FakeSystem:
    """mount / open_tree / umount の状態を一貫して模す.

    `_discard_target` は「本当に取り付けられているか」を mountinfo で確かめる
    実装なので、fake 側も取り付け状態を持っていないと噛み合わない。
    """

    def __init__(self, content_dir) -> None:
        self.content_dir = content_dir
        self.mounted: set[str] = set()
        self.calls: list[list[str]] = []
        self.cloned: list[str] = []
        self.detached: list[str] = []
        self.mount_returncode = 0
        self.mount_stderr = ""
        self.mount_exception: BaseException | None = None
        self.mount_happens_before_exception = False

    def runner(self, argv):
        self.calls.append(argv)
        if argv[0] == "mount":
            if self.mount_exception is not None:
                if self.mount_happens_before_exception:
                    self.mounted.add(argv[-1])
                raise self.mount_exception
            if self.mount_returncode == 0:
                self.mounted.add(argv[-1])
            return subprocess.CompletedProcess(argv, self.mount_returncode, "", self.mount_stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def cloner(self, path):
        self.cloned.append(path)
        return os.open(self.content_dir, os.O_RDONLY | os.O_DIRECTORY)

    def detacher(self, path):
        self.detached.append(path)
        self.mounted.discard(path)

    def lister(self, root):
        prefix = str(root).rstrip("/")
        return sorted(m for m in self.mounted if m == prefix or m.startswith(prefix + "/"))


def build(tmp_path, pinned=True, sys_over=None):
    content = tmp_path / "content"
    (content / "DCIM").mkdir(parents=True, exist_ok=True)
    system = FakeSystem(content)
    if sys_over:
        for key, value in sys_over.items():
            setattr(system, key, value)
    root = tmp_path / "mnt"
    mgr = MountManager(
        mount_root=root,
        runner=system.runner,
        cloner=system.cloner,
        detacher=system.detacher,
        mount_lister=system.lister,
        pin_check=lambda fd: pinned,
    )
    return mgr, system, root


def test_mount_uses_fixed_readonly_options(tmp_path):
    mgr, system, _ = build(tmp_path)
    handle, _ = mgr.mount(volume(), expect(), verify=lambda: volume())
    try:
        argv = system.calls[0]
        assert argv[0] == "mount"
        assert argv[1:3] == ["-t", "exfat"]
        assert argv[3:5] == ["-o", MOUNT_OPTIONS]
        for opt in ("ro", "nosuid", "nodev", "noexec"):
            assert opt in MOUNT_OPTIONS.split(",")
        assert argv[-2] == "/dev/sdk"
    finally:
        mgr.release(handle)


def test_mount_returns_a_usable_dirfd(tmp_path):
    mgr, _, _ = build(tmp_path)
    handle, dirfd = mgr.mount(volume(), expect(), verify=lambda: volume())
    try:
        assert os.listdir(dirfd) == ["DCIM"]
    finally:
        mgr.release(handle)


def test_mount_detaches_the_attached_copy(tmp_path):
    """クローンを作ったら元の取り付けは即座に外す。名前空間に残さない."""
    mgr, system, root = build(tmp_path)
    handle, _ = mgr.mount(volume(), expect(), verify=lambda: volume())
    try:
        assert len(system.cloned) == 1
        assert system.detached == system.cloned
        assert system.mounted == set()
        assert list(root.iterdir()) == []
    finally:
        mgr.release(handle)


def test_mount_target_does_not_use_the_fs_uuid(tmp_path):
    mgr, system, _ = build(tmp_path)
    handle, _ = mgr.mount(volume(), expect(), verify=lambda: volume())
    try:
        assert "26B1-2FD6" not in system.cloned[0]
    finally:
        mgr.release(handle)


def test_unpinned_dotdot_is_rejected(tmp_path):
    """切り離しが効いていない dirfd を app に渡さない."""
    mgr, system, root = build(tmp_path, pinned=False)
    with pytest.raises(MountRejected, match="not detached"):
        mgr.mount(volume(), expect(), verify=lambda: volume())
    assert system.detached, "拒否する前に取り付けを外していない"
    assert list(root.iterdir()) == []


def test_disallowed_fs_type_is_rejected_before_running_mount(tmp_path):
    mgr, system, _ = build(tmp_path)
    with pytest.raises(MountRejected, match="fs type"):
        mgr.mount(
            volume(fs_type="ntfs"),
            expect(fs_type="ntfs"),
            verify=lambda: volume(fs_type="ntfs"),
        )
    assert system.calls == []


def test_expect_mismatch_before_mount_is_rejected(tmp_path):
    mgr, system, _ = build(tmp_path)
    with pytest.raises(MountRejected, match="does not match"):
        mgr.mount(volume(), expect(minor=161), verify=lambda: volume())
    assert system.calls == []


def test_broker_epoch_mismatch_is_rejected(tmp_path):
    """mountd 再起動をまたいだ古い expect を弾く."""
    mgr, system, _ = build(tmp_path)
    with pytest.raises(MountRejected, match="does not match"):
        mgr.mount(volume(), expect(broker_epoch="epoch-2"), verify=lambda: volume())
    assert system.calls == []


def test_device_disappearing_after_mount_is_rejected(tmp_path):
    mgr, system, root = build(tmp_path)
    with pytest.raises(MountRejected, match="changed during mount"):
        mgr.mount(volume(), expect(), verify=lambda: None)
    assert system.mounted == set()
    assert list(root.iterdir()) == []


def test_failed_mount_command_removes_the_directory(tmp_path):
    mgr, system, root = build(
        tmp_path, sys_over={"mount_returncode": 32, "mount_stderr": "no such device"}
    )
    with pytest.raises(MountFailed, match="no such device"):
        mgr.mount(volume(), expect(), verify=lambda: volume())
    assert list(root.iterdir()) == []
    assert system.cloned == []


def test_runner_exception_detaches_when_the_mount_did_happen(tmp_path):
    """mount が成立したか不明なときは mountinfo で確かめて後始末する."""
    mgr, system, root = build(
        tmp_path,
        sys_over={
            "mount_exception": subprocess.TimeoutExpired("mount", 30),
            "mount_happens_before_exception": True,
        },
    )
    with pytest.raises(subprocess.TimeoutExpired):
        mgr.mount(volume(), expect(), verify=lambda: volume())
    assert system.detached, "不明な取り付けを回収していない"
    assert system.mounted == set()
    assert list(root.iterdir()) == []


def test_runner_exception_without_a_mount_just_removes_the_directory(tmp_path):
    mgr, system, root = build(
        tmp_path,
        sys_over={
            "mount_exception": subprocess.TimeoutExpired("mount", 30),
            "mount_happens_before_exception": False,
        },
    )
    with pytest.raises(subprocess.TimeoutExpired):
        mgr.mount(volume(), expect(), verify=lambda: volume())
    assert system.detached == [], "取り付いていないものを外そうとした"
    assert list(root.iterdir()) == []


def test_reap_stale_detaches_leftovers_from_a_previous_process(tmp_path):
    content = tmp_path / "content"
    content.mkdir()
    root = tmp_path / "mnt"
    (root / "leftover").mkdir(parents=True)
    system = FakeSystem(content)
    system.mounted.add(str(root / "leftover"))
    mgr = MountManager(
        mount_root=root,
        runner=system.runner,
        cloner=system.cloner,
        detacher=system.detacher,
        mount_lister=system.lister,
        pin_check=lambda fd: True,
    )
    assert system.detached == [str(root / "leftover")]
    assert list(root.iterdir()) == []
    assert mgr.reap_stale() == []


def test_detach_failure_on_the_success_path_fails_the_mount(tmp_path):
    """外し損ねた取り付けを抱えたまま成功を返さない.

    クローンが固定されていても、特権側の名前空間に名前付きマウントが残る。
    要求のたびに残骸が増え、デバイスを解放できなくなる。
    """
    mgr, system, _ = build(tmp_path)

    def failing_detach(path):
        raise OSError(errno.EPERM, "cannot detach")

    system.detacher = failing_detach
    mgr._detacher = failing_detach  # noqa: SLF001
    with pytest.raises(MountFailed, match="could not be detached"):
        mgr.mount(volume(), expect(), verify=lambda: volume())


def test_mountinfo_read_failure_is_not_treated_as_not_mounted(tmp_path):
    """列挙できないことを「取り付いていない」と解釈しない.

    後条件（元の取り付けを外した）を確認できないまま成功を返さない。
    """
    mgr, system, _ = build(tmp_path)

    def failing_lister(root):
        raise OSError(errno.EACCES, "cannot read mountinfo")

    mgr._mount_lister = failing_lister  # noqa: SLF001
    with pytest.raises(MountFailed, match="cannot read mountinfo"):
        mgr.mount(volume(), expect(), verify=lambda: volume())


def test_reap_stale_propagates_detach_failure(tmp_path):
    """回収できないまま起動しない."""
    content = tmp_path / "content"
    content.mkdir()
    root = tmp_path / "mnt"
    (root / "leftover").mkdir(parents=True)
    system = FakeSystem(content)
    system.mounted.add(str(root / "leftover"))

    def failing_detach(path):
        raise OSError(errno.EPERM, "cannot detach")

    with pytest.raises(OSError, match="cannot detach"):
        MountManager(
            mount_root=root,
            runner=system.runner,
            cloner=system.cloner,
            detacher=failing_detach,
            mount_lister=system.lister,
            pin_check=lambda fd: True,
        )


def test_release_is_infallible_and_closes_the_fd(tmp_path):
    mgr, _, _ = build(tmp_path)
    handle, dirfd = mgr.mount(volume(), expect(), verify=lambda: volume())
    mgr.release(handle)
    with pytest.raises(OSError):
        os.listdir(dirfd)
    mgr.release(handle)  # 二重解放でも落ちない


def test_release_all_closes_every_handle(tmp_path):
    mgr, _, _ = build(tmp_path)
    _, fd1 = mgr.mount(volume(), expect(), verify=lambda: volume())
    _, fd2 = mgr.mount(volume(minor=145), expect(minor=145), verify=lambda: volume(minor=145))
    mgr.release_all()
    for fd in (fd1, fd2):
        with pytest.raises(OSError):
            os.listdir(fd)


def test_verify_raising_still_releases_resources(tmp_path):
    mgr, system, root = build(tmp_path)

    def boom():
        raise RuntimeError("sysfs disappeared")

    with pytest.raises(RuntimeError, match="sysfs disappeared"):
        mgr.mount(volume(), expect(), verify=boom)
    assert system.detached, "取り付けが残っている"
    assert system.mounted == set()
    assert list(root.iterdir()) == []
