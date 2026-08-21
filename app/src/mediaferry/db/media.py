"""メディアの読み書きのうち、削除に関わるもの.

**`design.md` は「孤立ファイルは削除しない。画面に出してユーザの判断に委ねる」と
決めている。** ここが例外なのは、対象が「**もう誰も参照していない、我々が作った
派生物**」に限られるから —— 出所の分からない孤立ファイルとは性質が違う。

やり直しの経路（§13 の組み直し）を足したことで、古い出力が残る場面が現実に
なった（実機で 74 GiB）。それを片付けるための経路。
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from .connection import immediate
from .merges import GroupNotEditable


class MediaRepository:
    def __init__(self, conn: sqlite3.Connection, data_root: Path) -> None:
        self._conn = conn
        self._data_root = data_root

    def delete_stale_derived(self, media_file_id: str) -> str:
        """古くなった派生物を消す. **元ファイルは対象外.**

        消してよい条件は 3 つとも満たすこと。

        * `role = derived`（我々が作ったもの）
        * 持ち主のグループが**もう現行でない**（`skipped` か superseded）
        * **送信の記録が指していない**（何を送ったのか分からなくなる）

        **DB を先に消し、実体は後で消す。** 逆にすると、途中で落ちたときに
        「レコードはあるのに実体が無い」状態になり、失ったように見える。
        この順なら、実体だけが残っても孤立として画面に出る（回収できる）。
        """
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT id, role, rel_path FROM media_file WHERE id = ?", (media_file_id,)
            ).fetchone()
            if row is None:
                raise GroupNotEditable("そのファイルは無い")
            if row["role"] != "derived":
                raise GroupNotEditable("取り込んだ元ファイルは消せない")
            group = self._conn.execute(
                "SELECT id, status, superseded_by_id FROM merge_group"
                " WHERE output_media_file_id = ?",
                (media_file_id,),
            ).fetchone()
            if group is None:
                # 出所が分からない。孤立と同じ扱いで、判断はユーザに委ねる。
                raise GroupNotEditable("この派生物の出所が分からない")
            if group["superseded_by_id"] is None and group["status"] != "skipped":
                raise GroupNotEditable("現行のグループの結合結果は消せない")
            sent = self._conn.execute(
                "SELECT 1 FROM upload_record WHERE media_file_id = ?", (media_file_id,)
            ).fetchone()
            if sent is not None:
                raise GroupNotEditable("送信の記録が指している")
            # 実体が無いのに指し続けない。
            self._conn.execute(
                "UPDATE merge_group SET output_media_file_id = NULL WHERE id = ?", (group["id"],)
            )
            self._conn.execute("DELETE FROM media_file WHERE id = ?", (media_file_id,))
            rel_path = row["rel_path"]
        with contextlib.suppress(OSError):
            (self._data_root / rel_path).unlink()
        return rel_path
