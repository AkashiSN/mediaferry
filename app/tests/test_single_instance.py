"""同じ `DATA_ROOT` で 2 つ起動させない（§12）.

**後から起動した側は、壊す前に止まる。** 単一起動の錠が無いと、有効期限内の
`running` なジョブがあっても、後から起動した側の reconciliation が
`running/lease あり → interrupted/lease NULL` にして作業ディレクトリを消す
（旧プロセスは次の心拍で `LeaseLost`）。移行も 2 接続で同時に走ると
`UNIQUE constraint failed: schema_migration.version` になりうる。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time

import pytest

from mediaferry.single_instance import AlreadyRunning, hold_data_root


def test_the_first_holder_takes_the_lock(tmp_path):
    with hold_data_root(tmp_path) as lock_path:
        assert lock_path.exists()


def test_a_second_holder_is_refused_without_waiting(tmp_path):
    """**待たずに断る。** 待つと、壊す側が「起動が遅い」だけに見える.

    **待ちに上限を置いて別スレッドで見る。** `LOCK_NB` を外すと `flock` は
    握られている間ずっと返らないので、素直に書くと失敗ではなく無限待ちになる
    （`docs/development.md`「回帰でテストが『ハング』する形を書かない」）。
    """
    outcome: list[object] = []

    def second() -> None:
        try:
            with hold_data_root(tmp_path):
                outcome.append("取れてしまった")
        except AlreadyRunning as exc:
            outcome.append(exc)

    with hold_data_root(tmp_path):
        thread = threading.Thread(target=second, daemon=True)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive(), "待っている（LOCK_NB が効いていない）"

    assert len(outcome) == 1
    assert isinstance(outcome[0], AlreadyRunning), outcome[0]


def test_the_lock_is_released_when_the_holder_exits(tmp_path):
    with hold_data_root(tmp_path):
        pass
    with hold_data_root(tmp_path):
        pass  # 2 度目が取れる


def test_different_data_roots_do_not_block_each_other(tmp_path):
    """**錠は `DATA_ROOT` ごと。** 1 台で 2 つのライブラリを動かす道を塞がない."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with hold_data_root(tmp_path / "a"), hold_data_root(tmp_path / "b"):
        pass


def test_a_holder_that_died_does_not_leave_the_lock_held(tmp_path):
    """**stale lock を残さない。** 落ちたら OS が解放する.

    ファイルの存在で見張ると、電源断のあと二度と起動できなくなる。`flock` は
    開いたファイル記述に紐づくので、プロセスが消えれば解放される。
    """
    script = textwrap.dedent(
        f"""
        import time
        from mediaferry.single_instance import hold_data_root
        with hold_data_root({str(tmp_path)!r}):
            print("held", flush=True)
            time.sleep(60)
        """
    )
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(AlreadyRunning), hold_data_root(tmp_path):
            pass
        child.kill()
        child.wait(timeout=10)
        # 解放は OS がやるので、観測できるまで少し待つ。
        deadline = time.monotonic() + 5
        while True:
            try:
                with hold_data_root(tmp_path):
                    break
            except AlreadyRunning:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_the_entry_point_refuses_while_another_process_holds_the_data_root(tmp_path, monkeypatch):
    """**握れてから走らせる.**

    移行も reconciliation も、所有権を取る前に走らせてはいけない ——
    後から起動した側が、有効期限内の `running` を倒して作業ディレクトリを消す。
    """
    from mediaferry import __main__ as entry

    served: list[object] = []
    monkeypatch.setattr(entry, "_serve", lambda env, data_root: served.append(data_root))
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(tmp_path))

    with hold_data_root(tmp_path):
        assert entry.main() == 1

    assert served == [], "握れていないのに本体を動かした"


def test_the_entry_point_serves_when_it_holds_the_data_root(tmp_path, monkeypatch):
    from mediaferry import __main__ as entry

    served: list[object] = []
    monkeypatch.setattr(entry, "_serve", lambda env, data_root: served.append(data_root))
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(tmp_path))

    assert entry.main() == 0
    assert served == [tmp_path]
