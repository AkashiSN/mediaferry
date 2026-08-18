"""中断できない長い処理の間、リースを延ばし続ける.

`os.fsync`（30 GiB の直後は数十秒）、ffprobe（timeout がリースと同値）、
巨大ファイルの HTTP 送信（28 GiB で 84.5 秒）は、いずれも 1 回でリース
（60 秒）を超えうるのに途中で止められない。

**処理は別スレッドで走らせ、待つ側が heartbeat を打つ。** DB へ触るのは
待つ側だけなので、接続はスコープごとに 1 本のままで済む。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from ..db.jobs import LEASE_SECONDS, JobContext, LeaseLost

# リース (60 秒) の 1/3 ごとに延ばす。処理の長さは環境で桁が変わるので、
# 量ではなく時間で決める。
HEARTBEAT_INTERVAL = LEASE_SECONDS / 3


def with_lease_pulse[T](
    ctx: JobContext,
    work: Callable[[], T],
    also: Callable[[], None] | None = None,
    ownership_errors: tuple[type[BaseException], ...] = (LeaseLost,),
) -> T:
    """`work` を待ちながら heartbeat を打つ.

    `also` を渡すと、heartbeat のたびに一緒に呼ぶ（アップロードでは
    `upload_record.claim_expires_at` の延長に使う）。

    **`ownership_errors` には `also` が投げうる例外も含める。** アップロードは
    `ClaimLost` を投げるので、`(LeaseLost, ClaimLost)` を渡す。含め忘れると、
    claim の延長が失敗した瞬間に待つ側だけが例外で抜け、**走っているスレッドが
    後から 30 GiB を送り終える**（呼び出し側は失敗したと見ているのに副作用は進む）。
    `core` は `db.uploads` を知らないので、集合は呼び出し側から渡す。

    所有権を失っても、処理の完了を待ってから送出する。**走っているスレッドを
    残したまま抜けると、後から副作用が起きる。**
    """
    outcome: list[T] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome.append(work())
        except BaseException as exc:  # noqa: BLE001 - 呼び出し側へそのまま渡す
            failure.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    lost: BaseException | None = None
    while True:
        thread.join(timeout=HEARTBEAT_INTERVAL)
        if not thread.is_alive():
            break
        if lost is None:
            try:
                # **先に assert_lease を呼ぶ。** `extend_lease` は `cancelling` でも
                # 延ばすので、heartbeat だけでは 28 GiB の送信中のキャンセルに
                # 気づけない（`assert_lease` は `cancelling` を通さない）。
                ctx.assert_lease()
                ctx.heartbeat()
                if also is not None:
                    also()
            except ownership_errors as exc:
                # **打てなくなっても待ち続ける。** ここで抜けると、走っている
                # スレッドが後から 30 GiB を送り終える。呼び出し側は「失敗した」と
                # 見ているのに副作用だけが進む。
                lost = exc
    if lost is not None:
        raise lost
    if failure:
        raise failure[0]
    return outcome[0]
