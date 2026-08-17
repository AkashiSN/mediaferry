"""allowlist と expect 検証を伴う detached read-only マウント.

マウントオプションはこのモジュールが固定する。クライアントからは指定できない。

保持するのは取り付けではなく dirfd である（詳細は nsmount のドキュメント）。
そのため解放は fd を閉じるだけで済み、EBUSY で外せないマウントが溜まらない。
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mediaferry_protocol.messages import VolumeExpect, VolumeInfo
from mountd import nsmount

logger = logging.getLogger(__name__)

# カーネルにマウントさせるファイルシステムを絞る。blkid が返した任意の型を
# そのまま渡すと、攻撃者が用意したメディアで未知のドライバを叩けてしまう。
ALLOWED_FS_TYPES = frozenset({"exfat", "vfat"})

MOUNT_OPTIONS = "ro,nosuid,nodev,noexec,noatime"
MOUNT_TIMEOUT_SECONDS = 30

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]
Verifier = Callable[[], VolumeInfo | None]


class MountRejected(Exception):
    """要求が方針に反するか、デバイスの同一性・切り離しを確認できない."""


class MountFailed(Exception):
    """mount コマンド自体が失敗した."""


def subprocess_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    # S603: 呼び出し元が argv をリストで組み立てる。シェルを介さず、
    # fs_type は allowlist 済み、デバイスノードとマウント先は `--` の後ろに置く。
    return subprocess.run(  # noqa: S603
        argv,
        capture_output=True,
        text=True,
        timeout=MOUNT_TIMEOUT_SECONDS,
        check=False,
    )


@dataclass
class _Mounted:
    handle: str
    dirfd: int
    device_node: str


def _matches(volume: VolumeInfo, expect: VolumeExpect) -> bool:
    return (
        volume.major == expect.major
        and volume.minor == expect.minor
        and volume.fs_uuid == expect.fs_uuid
        and volume.fs_type == expect.fs_type
        and volume.broker_epoch == expect.broker_epoch
        and volume.generation == expect.generation
    )


class MountManager:
    def __init__(
        self,
        mount_root: Path,
        runner: Runner = subprocess_runner,
        cloner: Callable[[str], int] = nsmount.open_tree_clone,
        detacher: Callable[[str], None] = nsmount.umount_detach,
        mount_lister: Callable[[Path], list[str]] = nsmount.mountpoints_under,
        pin_check: Callable[[int], bool] = nsmount.dotdot_is_pinned,
    ) -> None:
        self._mount_root = mount_root
        self._runner = runner
        self._cloner = cloner
        self._detacher = detacher
        self._mount_lister = mount_lister
        self._pin_check = pin_check
        self._mounted: dict[str, _Mounted] = {}
        self._mount_root.mkdir(parents=True, exist_ok=True)
        self.reap_stale()

    # ------------------------------------------------------------------
    def reap_stale(self) -> list[str]:
        """前のプロセスが残した取り付けとディレクトリを回収する.

        列挙も detach も**失敗したら例外を送出する**。回収できないまま
        起動してしまうと、以後の要求のたびに残骸が増え、デバイスを解放できなく
        なる。ここで落ちれば mountd は ready にならず、異常が表に出る。
        """
        reaped: list[str] = []
        for target in self._mount_lister(self._mount_root):
            if target == str(self._mount_root):
                continue
            self._detacher(target)
            reaped.append(target)
        with contextlib.suppress(OSError):
            for child in self._mount_root.iterdir():
                with contextlib.suppress(OSError):
                    child.rmdir()
        if reaped:
            logger.warning("reaped %d leftover mount(s)", len(reaped))
        return reaped

    # ------------------------------------------------------------------
    def mount(
        self,
        volume: VolumeInfo,
        expect: VolumeExpect,
        verify: Verifier,
    ) -> tuple[str, int]:
        """検証してからマウントし、(handle, dirfd) を返す.

        `verify` はマウント直後にもう一度ボリュームを観測して返す呼び出し可能物。
        デバイスノードは抜き挿しで再利用されるため、直前の検証だけでは
        「マウントしている最中に別のカードにすり替わった」ケースを弾けない。
        """
        if expect.fs_type not in ALLOWED_FS_TYPES:
            raise MountRejected(f"fs type {expect.fs_type!r} is not allowed")
        if volume.fs_type not in ALLOWED_FS_TYPES:
            raise MountRejected(f"fs type {volume.fs_type!r} is not allowed")
        if not _matches(volume, expect):
            raise MountRejected("volume does not match the expected identity")

        handle = uuid.uuid4().hex
        target = self._mount_root / handle
        target.mkdir(parents=True, exist_ok=False)

        argv = [
            "mount",
            "-t",
            expect.fs_type,
            "-o",
            MOUNT_OPTIONS,
            "--",
            volume.device_node,
            str(target),
        ]
        try:
            result = self._runner(argv)
        except Exception:
            # マウントが成立したかは分からない。mountinfo で確かめて後始末する。
            self._discard_quietly(target)
            raise
        if result.returncode != 0:
            self._discard_quietly(target)
            raise MountFailed(f"mount failed ({result.returncode}): {result.stderr.strip()}")

        try:
            tree_fd = self._cloner(str(target))
        except Exception:
            self._discard_quietly(target)
            raise
        try:
            dirfd = nsmount.dirfd_from_tree(tree_fd)
        except Exception:
            self._discard_quietly(target)
            raise
        finally:
            with contextlib.suppress(OSError):
                os.close(tree_fd)

        # クローンを作ったら即座に取り付けを外す。以後このファイルシステムは
        # どの名前空間のパスにも現れない。
        #
        # これは best-effort ではなく**必須の後条件**である。外し損ねると、
        # app に渡すクローンが固定されていても、特権側の名前空間に名前付きの
        # マウントが残る。要求のたびに残骸が増え、デバイスを解放できなくなる。
        try:
            self._detach_target(target)
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.close(dirfd)
            raise MountFailed(
                f"clone succeeded but the attached copy could not be detached: {exc}"
            ) from exc

        try:
            if not self._pin_check(dirfd):
                raise MountRejected("mount is not detached: '..' escapes the volume root")
            observed = verify()
            if observed is None or not _matches(observed, expect):
                raise MountRejected("device changed during mount")
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(dirfd)
            raise

        self._mounted[handle] = _Mounted(handle, dirfd, volume.device_node)
        return handle, dirfd

    # ------------------------------------------------------------------
    def release(self, handle: str) -> None:
        """dirfd を閉じる。取り付けは既に外れているので失敗しない."""
        entry = self._mounted.pop(handle, None)
        if entry is None:
            return
        with contextlib.suppress(OSError):
            os.close(entry.dirfd)

    def release_all(self) -> None:
        for handle in list(self._mounted):
            self.release(handle)

    # ------------------------------------------------------------------
    def _detach_target(self, target: Path) -> None:
        """target が取り付けられていれば外し、ディレクトリを消す.

        列挙も detach も失敗を伝播する。成功経路ではこれを使う。
        """
        if str(target) in self._mount_lister(self._mount_root):
            self._detacher(str(target))
        with contextlib.suppress(OSError):
            target.rmdir()

    def _discard_quietly(self, target: Path) -> None:
        """例外処理の途中で呼ぶ後始末。元の例外を隠さないよう握り潰す.

        握り潰した場合も残骸として記録し、次回起動の reap_stale で回収する。
        """
        try:
            self._detach_target(target)
        except OSError:
            logger.exception("failed to clean up %s; it will be reaped on next start", target)
