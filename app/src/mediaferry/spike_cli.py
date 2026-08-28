"""前提を実機で確かめるための CLI.

別コンテナの mountd に繋ぎ、渡された dirfd 越しに実際のボリュームを読めるかを
確かめる。判定は終了ステータスに反映する。**本体の経路では使わない。**
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import sys
from pathlib import Path

from mediaferry.adapters.broker_client import BrokerClient, BrokerError

MAX_ENTRIES = 20
PREVIEW_BYTES = 16
SOCKET_ENV = "MEDIAFERRY_BROKER_SOCKET"
EXPECTED_UID_ENV = "MEDIAFERRY_EXPECT_UID"

# 書き込みが拒否されたと認めてよい errno。これ以外は「判定不能」として FAIL。
WRITE_DENIED_ERRNOS = {errno.EROFS, errno.EACCES, errno.EPERM}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    return ok


def _proc_status_field(name: str) -> str | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(f"{name}:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def check_dotdot_is_pinned(dirfd: int) -> bool:
    """`openat(dirfd, "..")` がボリュームルートに留まるか.

    これは INFO ではなく合否である。侵害された app は「`..` を使わない」という
    規約を無視できるので、抜けられる時点で仕様書 §14 の境界が成立しない。
    mountd 側が detached mount で渡していれば固定される。

    **測れなかったら FAIL とする（fail-closed）。** 「親を検査できなかった」ことは
    固定されている証拠にならない。ここを安全側に倒すと、通常マウント由来で
    実際には抜けられる状態なのに、最終ガードと実機試験が同時に偽陽性になる。
    """
    try:
        here = os.stat(".", dir_fd=dirfd, follow_symlinks=False)
        up = os.stat("..", dir_fd=dirfd, follow_symlinks=False)
    except OSError as exc:
        return _check("'..' がボリュームルートに固定される", False, f"検証できなかった: {exc}")
    pinned = (here.st_dev, here.st_ino) == (up.st_dev, up.st_ino)
    detail = ""
    if not pinned:
        try:
            parent = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=dirfd)
            try:
                detail = f"親の中身: {sorted(os.listdir(parent))[:8]}"
            finally:
                os.close(parent)
        except OSError as exc:
            detail = f"親を列挙できず: {exc}"
    return _check("'..' がボリュームルートに固定される", pinned, detail)


def check_source_is_read_only(dirfd: int) -> bool:
    """ソースへの書き込みが拒否されるか.

    open に成功した時点で無条件に FAIL とする。後片付けの unlink 失敗を
    「書き込み拒否」と取り違えないよう、生成と削除の例外を分けて扱う。
    """
    name = "mediaferry-write-probe"
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in WRITE_DENIED_ERRNOS:
            return _check("ソースへの新規作成が拒否される", True, f"errno={exc.errno}")
        return _check("ソースへの新規作成が拒否される", False, f"想定外の errno={exc.errno}")
    os.close(fd)
    with contextlib.suppress(OSError):
        os.unlink(name, dir_fd=dirfd)
    return _check("ソースへの新規作成が拒否される", False, "作成できてしまった")


def check_existing_file_is_read_only(dirfd: int, rel_dir: str, name: str) -> bool:
    """既存ファイルを書き込みモードで開けないか."""
    try:
        if rel_dir:
            sub = os.open(rel_dir, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dirfd)
        else:
            sub = os.dup(dirfd)
    except OSError as exc:
        return _check("既存ファイルの O_WRONLY が拒否される", False, f"親を開けない: {exc}")
    try:
        fd = os.open(name, os.O_WRONLY, dir_fd=sub)
    except OSError as exc:
        ok = exc.errno in WRITE_DENIED_ERRNOS
        return _check(
            "既存ファイルの O_WRONLY が拒否される",
            ok,
            f"errno={exc.errno}" if ok else f"想定外の errno={exc.errno}",
        )
    finally:
        os.close(sub)
    os.close(fd)
    return _check("既存ファイルの O_WRONLY が拒否される", False, "開けてしまった")


def check_socket_cannot_be_replaced(socket_path: Path) -> bool:
    try:
        os.unlink(socket_path)
    except OSError as exc:
        ok = exc.errno in WRITE_DENIED_ERRNOS
        return _check(
            "ソケットを unlink できない",
            ok,
            f"errno={exc.errno}" if ok else f"想定外の errno={exc.errno}",
        )
    return _check("ソケットを unlink できない", False, "消せてしまった")


def check_process_privileges() -> bool:
    ok = True

    caps = _proc_status_field("CapEff")
    if caps is None:
        ok &= _check("実効ケーパビリティが空", False, "CapEff を読めない")
    else:
        try:
            ok &= _check("実効ケーパビリティが空", int(caps, 16) == 0, f"CapEff={caps}")
        except ValueError:
            ok &= _check("実効ケーパビリティが空", False, f"CapEff を解釈できない: {caps!r}")

    nnp = _proc_status_field("NoNewPrivs")
    ok &= _check("no-new-privileges が有効", nnp == "1", f"NoNewPrivs={nnp}")

    expected_uid = os.environ.get(EXPECTED_UID_ENV)
    if expected_uid:
        ok &= _check(
            "想定した非 root UID で動いている",
            os.geteuid() == int(expected_uid),
            f"euid={os.geteuid()} expected={expected_uid}",
        )
    else:
        ok &= _check("root 以外で動いている", os.geteuid() != 0, f"euid={os.geteuid()}")

    return ok


def security_checks(dirfd: int, socket_path: Path, sample: tuple[str, str] | None) -> bool:
    """仕様書 §14 の境界が実際に効いているかを確かめる負のテスト.

    読み出しが成功しただけでは境界を実証したことにならない。マウントが実は
    書き込み可能だった、app に CAP_SYS_ADMIN が残っていた、ソケットを
    差し替えられた、`..` で mountd の名前空間へ抜けられた、といった構成ミスでも
    プレビューは成功してしまう。
    """
    print("\n--- セキュリティ境界の確認 ---")
    ok = True
    ok &= check_dotdot_is_pinned(dirfd)
    ok &= check_source_is_read_only(dirfd)
    if sample is not None:
        ok &= check_existing_file_is_read_only(dirfd, sample[0], sample[1])
    ok &= check_socket_cannot_be_replaced(socket_path)
    ok &= check_process_privileges()
    return ok


def walk_preview(
    dirfd: int, prefix: str = "", depth: int = 0, rel: str = ""
) -> tuple[int, tuple[str, str] | None]:
    """先頭数件を表示し、(読めたファイル数, 最初の通常ファイルの位置) を返す."""
    if depth > 3:
        return 0, None
    try:
        names = sorted(os.listdir(dirfd))
    except OSError as exc:
        print(f"  {prefix}<listdir 失敗: {exc}>")
        return 0, None
    read = 0
    sample: tuple[str, str] | None = None
    for name in names[:MAX_ENTRIES]:
        try:
            st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        except OSError as exc:
            print(f"  {prefix}{name} <stat 失敗: {exc}>")
            continue
        if stat.S_ISDIR(st.st_mode):
            print(f"  {prefix}{name}/")
            sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dirfd)
            try:
                sub_read, sub_sample = walk_preview(
                    sub, prefix + "  ", depth + 1, f"{rel}/{name}" if rel else name
                )
            finally:
                os.close(sub)
            read += sub_read
            sample = sample or sub_sample
        elif stat.S_ISREG(st.st_mode):
            print(f"  {prefix}{name}  ({st.st_size} bytes)")
            if read == 0:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
                try:
                    head = os.read(fd, PREVIEW_BYTES)
                    print(f"  {prefix}  → 先頭 {len(head)} バイト: {head.hex()}")
                finally:
                    os.close(fd)
                read += 1
                sample = sample or (rel, name)
    return read, sample


def main() -> int:
    """判定を終了ステータスに反映する.

    「実行した」だけで成功扱いにすると スパイクの中心命題に偽陽性が入る。
    必須条件を満たさなければ非ゼロで終わる。
    """
    socket_path = Path(os.environ.get(SOCKET_ENV, "/run/mediaferry/broker.sock"))
    expected = int(os.environ.get("MEDIAFERRY_EXPECT_VOLUMES", "1"))
    print(f"broker socket: {socket_path}")
    print(f"期待ボリューム数 (MEDIAFERRY_EXPECT_VOLUMES): {expected}")

    try:
        client = BrokerClient(socket_path)
    except OSError as exc:
        # ソケットに connect できない。ソケットのパーミッション (DAC) で
        # 弾かれた場合もここに来る。SO_PEERCRED による拒否とは別物なので、
        # 終了コードを分けて区別できるようにする。
        print(f"FAIL: ブローカーに接続できません: {exc}", file=sys.stderr)
        return 1

    listed = opened = readable = 0
    security_ok = True
    security_checked = 0
    with client:
        try:
            volumes = client.list_volumes()
        except BrokerError as exc:
            # connect は通ったがブローカーに拒否された。許可外 UID の確認は
            # ここに来ることを期待する。
            print(f"FAIL: ブローカーに拒否されました: {exc.code}: {exc.message}", file=sys.stderr)
            return 4
        listed = len(volumes)
        for v in volumes:
            print(
                f"\n=== {v.device_node} "
                f"fs={v.fs_type} uuid={v.fs_uuid} label={v.fs_label} "
                f"size={v.size_bytes} epoch={v.broker_epoch} gen={v.generation} usb={v.usb}"
            )
            try:
                with client.open_volume(v) as handle:
                    opened += 1
                    print(f"  handle={handle.handle} dirfd={handle.dirfd}")
                    read, sample = walk_preview(handle.dirfd)
                    if read > 0:
                        readable += 1
                    else:
                        print("  FAIL: このボリュームでは実ファイルを読めなかった")
                    security_ok &= security_checks(handle.dirfd, socket_path, sample)
                    security_checked += 1
            except BrokerError as exc:
                print(f"  FAIL: open_volume code={exc.code} {exc.message}")

    print("\n=== 判定 ===")
    results = [
        _check(f"ボリュームを {expected} 件以上列挙できた", listed >= expected, f"listed={listed}"),
        _check(
            "列挙した全ボリュームを open できた",
            listed > 0 and opened == listed,
            f"opened={opened}/{listed}",
        ),
        _check(
            "全ボリュームで dirfd 越しに実ファイルを読めた",
            listed > 0 and readable == listed,
            f"readable={readable}/{listed}",
        ),
        # 1 件も検査していないのに PASS にしない。security_ok は初期値が True
        # なので、ボリュームが 0 件だとループを通らずそのまま通過してしまう。
        _check(
            "セキュリティ境界の確認が全て通った",
            security_checked > 0 and security_checked == opened and security_ok,
            f"checked={security_checked}/{opened}",
        ),
    ]
    if all(results):
        print("\nRESULT: PASS — 仕様書 §18-1 は解消")
        return 0
    print("\nRESULT: FAIL — 上の FAIL 項目を findings に記録すること", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
