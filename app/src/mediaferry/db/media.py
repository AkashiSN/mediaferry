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
from .merges import GroupNotEditable, MergeRepository

# **「決着していない」送信状態の集合.** `deletion_blocker` の出力側チェックと
# member 側チェック、そして Python 側（`_presence` など）が同じ定義を使う。
IN_FLIGHT_STATES = frozenset(
    {
        "pending",
        "checking",
        "uploading",
        "asset_known",
        "tagging",
        "fixing_datetime",
        "awaiting_datetime_approval",
        "needs_recheck",
    }
)

_IN_FLIGHT = ", ".join(f"'{state}'" for state in sorted(IN_FLIGHT_STATES))


def _is_current_group(group_row: sqlite3.Row | None) -> bool:
    """このグループが現行か（`delete_derived` が `discard_locked` を呼ぶ条件.

    `delete_derived` と `MediaRepository.delete_frees_sources` の両方がここを
    呼ぶ。**条件を書き写さない** —— 片方だけ変えたら、消える予告と実際の挙動が
    食い違う。グループが無い（出所不明）なら現行ではない。
    """
    return (
        group_row is not None
        and group_row["superseded_by_id"] is None
        and group_row["status"] == "merged"
    )


class MediaRepository:
    def __init__(self, conn: sqlite3.Connection, data_root: Path) -> None:
        self._conn = conn
        self._data_root = data_root

    def list_stale_derived(self) -> list[dict[str, object]]:
        """もう使われていない派生物を並べる. **条件は削除の前提と同じ.**

        **消せるのに画面から辿れなければ、無いのと同じ。** `list_groups` は
        `superseded_by_id` を持つ行をどの場合も返さない（置き換えられた構成を
        一覧に並べる意味が無いため）ので、そのグループの「できたファイル」は
        結合画面に出ない —— 実機で 66 GiB がそこに残っていた。**出すのは構成
        ではなく、残っているファイル**。

        条件を `delete_derived` と揃えるのは、**押しても 409 で断られる
        ボタンを並べない**ため。片方だけ変えたら、もう片方も変える。
        """
        rows = self._conn.execute(
            "SELECT m.id, m.rel_path, m.size_bytes, m.captured_at,"
            "       CASE WHEN g.superseded_by_id IS NOT NULL THEN 'superseded'"
            "            ELSE 'skipped' END AS reason"
            " FROM media_file m JOIN merge_group g ON g.output_media_file_id = m.id"
            " WHERE m.role = 'derived'"
            "   AND (g.superseded_by_id IS NOT NULL OR g.status = 'skipped')"
            "   AND NOT EXISTS (SELECT 1 FROM upload_record u WHERE u.media_file_id = m.id)"
            " ORDER BY m.rel_path"
        )
        return [dict(row) for row in rows]

    def delete_derived(self, media_file_id: str) -> str:
        """つないだ動画を消す（写真タブの「消す」）.

        **消してよいのは、Immich に生きていない `derived` だけ**
        （規則は `deletion_blocker`）。元ファイルは対象外。

        **持ち主が現行のグループなら、一緒に「別々にした」にする。** `merged` の
        まま出力だけ外すと `merge_member` が active に残り、再検出も組み直しも
        塞がって二度とつなげなくなる。同じトランザクションで行うので、
        `MergeRepository` の**開いている前提の版**を呼ぶ。

        **DB を先に消し、実体は後で消す。** 逆にすると、途中で落ちたときに
        「レコードはあるのに実体が無い」状態になり、失ったように見える。
        この順なら、実体だけが残っても孤立として画面に出る（回収できる）。
        """
        with immediate(self._conn):
            # **トランザクションの中で見直す。** 判定と削除の間に送信が始まりうる。
            blocker = self.deletion_blocker(media_file_id)
            if blocker is not None:
                raise GroupNotEditable(blocker)
            row = self._conn.execute(
                "SELECT rel_path FROM media_file WHERE id = ?", (media_file_id,)
            ).fetchone()
            group = self._conn.execute(
                "SELECT id, status, superseded_by_id FROM merge_group"
                " WHERE output_media_file_id = ?",
                (media_file_id,),
            ).fetchone()
            if group is None:
                # 出所が分からない。孤立と同じ扱いで、判断はユーザに委ねる。
                raise GroupNotEditable("この派生物の出所が分からない")
            if _is_current_group(group):
                MergeRepository(self._conn).discard_locked(group["id"])
            # 実体が無いのに指し続けない。
            self._conn.execute(
                "UPDATE merge_group SET output_media_file_id = NULL WHERE id = ?", (group["id"],)
            )
            # **記録を先に消す。** `media_file_id` は ON DELETE RESTRICT。
            self._conn.execute(
                "DELETE FROM upload_record WHERE media_file_id = ?", (media_file_id,)
            )
            self._conn.execute("DELETE FROM media_file WHERE id = ?", (media_file_id,))
            rel_path = row["rel_path"]
        with contextlib.suppress(OSError):
            (self._data_root / rel_path).unlink()
        return rel_path

    def delete_frees_sources(self, media_file_id: str) -> bool:
        """**この 1 件を消すと、元になったファイルが「まだ送っていない」に戻るか.**

        真なのは、持ち主のグループが現行（`_is_current_group`）のときだけ。
        既に「別々にした」／組み直しで置き換わったグループの出力は、member が
        既に解放済みなので消しても何も変わらない。くわしく画面の確認ダイアログは
        この値で文言を出し分ける —— 起きないことを予告しないため。
        """
        group = self._conn.execute(
            "SELECT status, superseded_by_id FROM merge_group WHERE output_media_file_id = ?",
            (media_file_id,),
        ).fetchone()
        return _is_current_group(group)

    def deletion_blocker(self, media_file_id: str) -> str | None:
        """消せない理由を返す（消せるなら `None`）.

        **画面にそのまま出せる日本語を返す。** 押しても 409 で断られるボタンを
        並べないため、一覧・詳細・DELETE がこの 1 つの判定を使う。

        理由を 1 つに絞れるよう、当てはまりの強い順に見る。
        """
        row = self._conn.execute(
            "SELECT role FROM media_file WHERE id = ?", (media_file_id,)
        ).fetchone()
        if row is None:
            return "そのファイルは無い"
        if row["role"] != "derived":
            return "取り込んだ元ファイルは消せない"
        for clause, reason in (
            (
                f"u.invalidated_at IS NULL AND u.state IN ({_IN_FLIGHT})",
                "送信中か、確認を待っている記録がある",
            ),
            (
                "u.invalidated_at IS NULL AND u.remote_asset_id IS NOT NULL"
                " AND coalesce(u.remote_is_trashed, 0) = 0",
                "Immich に入っている",
            ),
            (
                "u.invalidated_at IS NULL AND u.state = 'complete'"
                " AND u.remote_asset_id IS NULL AND u.remote_checked_at IS NULL",
                "Immich にあるかどうかを確かめていない",
            ),
        ):
            found = self._conn.execute(
                f"SELECT 1 FROM upload_record u WHERE u.media_file_id = ? AND {clause}",  # noqa: S608
                (media_file_id,),
            ).fetchone()
            if found is not None:
                return reason
        # **出力自体が決着していても、元になった構成ファイルがまだ送信中なら消さない.**
        # 削除の実装は既存の `MergeRepository._assert_editable` を通り、そこは
        # 現行グループの active な member が送信中だと断る。ここで見ておかないと、
        # 画面が「消せます」と言った直後に 409 になる。
        current_group = self._conn.execute(
            "SELECT id FROM merge_group"
            " WHERE output_media_file_id = ? AND superseded_by_id IS NULL AND status != 'skipped'",
            (media_file_id,),
        ).fetchone()
        if current_group is not None:
            member_in_flight = self._conn.execute(
                "SELECT 1 FROM merge_member mm"  # noqa: S608
                " JOIN upload_record u ON u.media_file_id = mm.media_file_id"
                " WHERE mm.merge_group_id = ? AND mm.active = 1"
                f"   AND u.invalidated_at IS NULL AND u.state IN ({_IN_FLIGHT})",
                (current_group["id"],),
            ).fetchone()
            if member_in_flight is not None:
                return "元になったファイルを送信中か、確認を待っている"
        return None
