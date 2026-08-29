"""同じ `DATA_ROOT` を 2 つのプロセスに持たせない（§12）.

**後から起動した側は、壊す前に止まる。** 錠が無いと、有効期限内の `running` な
ジョブがあっても、後から起動した側の reconciliation が
`running/lease あり → interrupted/lease NULL` にして作業ディレクトリを消す
（旧プロセスは次の心拍で `LeaseLost` になる）。移行も、2 接続が同時に
`apply_migrations` を走らせると片方が `UNIQUE constraint failed:
schema_migration.version` で落ちる。

**錠は `flock` で取り、待たずに断る。** 待って諦める形にすると、壊す側が
「起動が遅い」だけに見えて、何が起きているのかが読めない。

**ファイルの存在では見張らない。** `flock` は開いたファイル記述に紐づくので、
プロセスが落ちれば OS が解放する。存在で見張ると、電源断のあと二度と起動
できなくなる。
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: `DATA_ROOT` からの位置。DB と同じ `var/` に置く（どちらもこのアプリの状態）。
LOCK_REL_PATH = Path("var") / "mediaferry.lock"


class AlreadyRunning(RuntimeError):
    """同じ `DATA_ROOT` を別のプロセスが握っている."""


@contextmanager
def hold_data_root(data_root: Path | str) -> Iterator[Path]:
    """`DATA_ROOT` の所有権を握る。握れなければ `AlreadyRunning`.

    抜けるときに閉じて解放する。**握った fd はプロセスの寿命まで持つ**ので、
    呼び出し側はこのブロックの中で本体を動かす。
    """
    path = Path(data_root) / LOCK_REL_PATH
    # DB と同じ `var/`。最初の起動ではまだ無い。
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 中身は使わない。**書き込みで開くのは `flock` の要件ではなく**、
    # 読み取り専用の FS で黙って通さないため（そこは書けないと分かる方がよい）。
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(
                f"同じデータの置き場所（{data_root}）を別のプロセスが使っている。1 つだけ動かすこと"
            ) from exc
        yield path
    finally:
        # close が flock も外す。明示の LOCK_UN は要らない。
        os.close(fd)
