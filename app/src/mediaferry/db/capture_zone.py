"""撮影地のタイムゾーンの上書きと、撮影日時を動かした後始末."""

from __future__ import annotations

import sqlite3

from ..clock import now_iso


def requeue_sent(conn: sqlite3.Connection, media_id: str, *, fixes_datetime: bool) -> int:
    """送信済みを `needs_recheck` へ戻す（`CLAIMABLE_STATES` に入っている）.

    **戻すのは「プロファイルが日時を書き戻す」ものだけ。**
    `fix_datetime_after_upload` が偽なら、こちらがリモートの日時を書いたことが
    無いので、ローカルが変わってもリモートに差は生じない。戻すと、何も
    変わらない再送を全件に強いる。

    条件は `state = 'complete'` の CAS。進行中のレコードを踏むと、所有者の
    いる行を横から動かすことになる。

    **無効化された行は触らない。** 無効化された `complete` は「なぜ送信を
    許可したか」を残すための監査履歴で、送信済みの記録ではない（§2.3）。
    claim も一覧も数え上げも無効化を除くので、戻しても誰も拾わない。
    """
    if not fixes_datetime:
        return 0
    return conn.execute(
        "UPDATE upload_record SET state = 'needs_recheck', updated_at = ?"
        " WHERE media_file_id = ? AND state = 'complete' AND invalidated_at IS NULL",
        (now_iso(), media_id),
    ).rowcount


def reopen_skipped_stack(conn: sqlite3.Connection, media_id: str) -> int:
    """スタックの見送りを未評価へ戻す（§6）.

    抽出は未評価しか拾わないので、戻さないと二度と再評価されない。
    **`stacked` は戻さない**（相手側に既にあるものを作り直さない）。

    **組の判定は撮影時刻を見ない**（§6）ので、この戻しで結論が変わることは無い。
    見送りを未評価へ戻すのが `captured_at` を動かす側の責任だ、という形だけを残す。

    **無効化された行は触らない。** 監査履歴なので書き換えない。第 2 パスの
    抽出も無効化を除くので、戻しても拾われない。
    """
    return conn.execute(
        "UPDATE upload_record SET stack_state = NULL, stack_reason = NULL, updated_at = ?"
        " WHERE media_file_id = ? AND stack_state = 'skipped' AND invalidated_at IS NULL",
        (now_iso(), media_id),
    ).rowcount
