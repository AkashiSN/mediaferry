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


# **持ち主のグループを 1 つに決める並び.** 1 つの出力を複数の `merge_group` が
# 指すことがある —— 構成を変えない組み直し（`merges.supersede` の「やり直し」）は
# 旧グループが `output_media_file_id` を持ったまま superseded になり、新グループが
# 結合すると出力の `rel_path` が同じなので `publisher._commit` が同じ `media_file`
# 行を再利用する。**先頭は必ず現行**（置き換えられていない、かつ破棄していない）に
# なり、同点は `id` で決まる —— 問い合わせるたびに違うグループを拾わないため。
_OWNER_PICK = " ORDER BY (o.superseded_by_id IS NOT NULL), (o.status = 'skipped'), o.id LIMIT 1"


def owner_group(conn: sqlite3.Connection, media_file_id: str) -> sqlite3.Row | None:
    """この派生物の**持ち主のグループ**を 1 つ返す（無ければ `None`）.

    **「この出力を持っているグループ」を引く問い合わせはここだけ。** 削除・予告・
    一覧・くわしくの元ファイル欄が別々に書くと、同じ出力を 2 つのグループが
    指している状態で、判定と予告と表示がそれぞれ違うグループを見る。
    """
    return conn.execute(
        "SELECT o.id, o.status, o.superseded_by_id FROM merge_group o"  # noqa: S608
        " WHERE o.output_media_file_id = ?" + _OWNER_PICK,
        (media_file_id,),
    ).fetchone()


def _is_current_group(group_row: sqlite3.Row | None) -> bool:
    """このグループが現行か（`delete_derived` が `discard_locked` を呼ぶ条件.

    `delete_derived`・`delete_frees_sources`・`deletion_blocker` の 3 つがここを
    呼ぶ。**条件を書き写さない** —— 片方だけ変えたら、消える予告と実際の挙動が
    食い違う。グループが無い（出所不明）なら現行ではない。

    **`merged` に限らない。** 現行かどうかを分けるのは「member をまだ握っているか」
    であって、結合が終わったかではない。`detected` / `merging` / `failed` の
    グループも member を `active = 1` のまま握っている（`publisher` は `merging` の
    うちに `output_media_file_id` を入れるので、`mark_merged` の前に落ちれば
    出力を持ったまま `merging` / `failed` で残る）。それを「現行ではない」と見ると、
    出力だけ外して member が握られたままになり、二度とつなげなくなる。
    握りを手放しているのは、trigger が member を落とす `skipped` と、
    置き換えられた（superseded）グループだけ。
    """
    return (
        group_row is not None
        and group_row["superseded_by_id"] is None
        and group_row["status"] != "skipped"
    )


class MediaRepository:
    def __init__(self, conn: sqlite3.Connection, data_root: Path) -> None:
        self._conn = conn
        self._data_root = data_root

    def list_stale_derived(self) -> list[dict[str, object]]:
        """もう使われていない派生物を並べる. **削除の前提より狭い.**

        **消せるのに画面から辿れなければ、無いのと同じ。** `list_groups` は
        `superseded_by_id` を持つ行をどの場合も返さない（置き換えられた構成を
        一覧に並べる意味が無いため）ので、そのグループの「できたファイル」は
        結合画面に出ない —— 実機で 66 GiB がそこに残っていた。**出すのは構成
        ではなく、残っているファイル**。

        ここに出るのは「持ち主のグループが置き換えられたか破棄されていて、送信の
        記録が 1 つも無い」ものだけで、`deletion_blocker` が許すものより狭い
        （送った記録があっても Immich に生きていなければ消せる）。**狭くてよい**
        のは、そちらは写真タブの一覧とくわしくから辿れるから。ここが引き受けるのは
        **どの画面からも辿れない**出力の回収だけで、押しても 409 で断られる
        ボタンは並べない（この条件は `deletion_blocker` が許す範囲の内側にある）。
        """
        rows = self._conn.execute(
            "SELECT m.id, m.rel_path, m.size_bytes, m.captured_at,"  # noqa: S608
            "       CASE WHEN g.superseded_by_id IS NOT NULL THEN 'superseded'"
            "            ELSE 'skipped' END AS reason"
            # **持ち主のグループ 1 つだけを見る。** 同じ出力を複数のグループが
            # 指しうるので、素直に join すると 1 つのファイルが何行にも増え、
            # 現行のグループが握っている出力まで「もう使われていない」欄に出る。
            " FROM media_file m JOIN merge_group g ON g.id = ("
            "     SELECT o.id FROM merge_group o WHERE o.output_media_file_id = m.id"
            + _OWNER_PICK
            + " )"
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
            # 出所が分からないもの（グループ不在）は `deletion_blocker` が既に断って
            # いるので、ここまで来た派生物には必ず持ち主がいる。
            group = owner_group(self._conn, media_file_id)
            if _is_current_group(group):
                MergeRepository(self._conn).discard_locked(group["id"])
            # **実体が無いのに指し続けない。同じ出力を指すグループを全部外す。**
            # 持ち主 1 つだけを外すと、残った側が `media_file` を指したままになり、
            # 削除が `ON DELETE RESTRICT` に当たって 500 になる。
            self._conn.execute(
                "UPDATE merge_group SET output_media_file_id = NULL WHERE output_media_file_id = ?",
                (media_file_id,),
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
        return _is_current_group(owner_group(self._conn, media_file_id))

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
        group = owner_group(self._conn, media_file_id)
        if group is None:
            # **出所が分からない派生物は消さない**（design.md の裁定 7。孤立ファイルと
            # 同じ扱いで、判断はユーザに委ねる）。**判定と削除で規則をそろえる** ——
            # ここに無いと、画面が「消せます」と言った直後に `delete_derived` が断る。
            return "元になったファイルが分からない"
        # **出力自体が決着していても、元になった構成ファイルがまだ送信中なら消さない.**
        # 削除の実装は既存の `MergeRepository._assert_editable` を通り、そこは
        # 現行グループの active な member が送信中だと断る。ここで見ておかないと、
        # 画面が「消せます」と言った直後に 409 になる。
        if _is_current_group(group):
            member_in_flight = self._conn.execute(
                "SELECT 1 FROM merge_member mm"  # noqa: S608
                " JOIN upload_record u ON u.media_file_id = mm.media_file_id"
                " WHERE mm.merge_group_id = ? AND mm.active = 1"
                f"   AND u.invalidated_at IS NULL AND u.state IN ({_IN_FLIGHT})",
                (group["id"],),
            ).fetchone()
            if member_in_flight is not None:
                return "元になったファイルを送信中か、確認を待っている"
        return None
