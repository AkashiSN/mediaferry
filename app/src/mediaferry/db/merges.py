"""結合グループの保存と状態遷移.

`status` は detected → merging → merged / failed、および detected / failed →
skipped と動く。**遷移は `BEGIN IMMEDIATE` の中の条件付き UPDATE（CAS）で取る。**
SQLite に行ロックは無い。

claim の条件に `input_digest` を含めるのは、キューで待っている間に構成や
プロファイルが変わった場合に、確認画面と違う入力で結合しないため。
"""

from __future__ import annotations

import sqlite3

from ..clock import now_iso
from ..core.merge.grouping import GroupCandidate
from ..ids import new_id
from .connection import immediate
from .profiles import ProfileRef

CLAIMABLE = ("detected", "failed")


class GroupNotClaimable(RuntimeError):
    """求めた遷移ができない（状態が違う、構成が変わっている、出力が無い）."""


class GroupNotEditable(RuntimeError):
    """いま送られている（これから送られる）根拠なので、動かせない."""


class MergeRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_detected(
        self, profile: ProfileRef, candidate: GroupCandidate, digest: str
    ) -> str | None:
        """作れたら group_id、既に同じものがあれば None."""
        media_ids = [part.media_file_id for part in candidate.members]
        marks = ", ".join("?" * len(media_ids))
        with immediate(self._conn):
            existing = self._conn.execute(
                "SELECT 1 FROM merge_group WHERE input_digest = ? AND superseded_by_id IS NULL",
                (digest,),
            ).fetchone()
            if existing is not None:
                return None
            taken = self._conn.execute(
                f"SELECT 1 FROM merge_member WHERE active = 1 AND media_file_id IN ({marks})",  # noqa: S608
                media_ids,
            ).fetchone()
            if taken is not None:
                # 1 つの media_file が同時に属せるアクティブグループは 1 つまで。
                return None
            group_id = new_id()
            self._conn.execute(
                "INSERT INTO merge_group (id, profile_id, profile_revision_id, status,"
                " input_digest, detected_by, created_at, updated_at)"
                " VALUES (?, ?, ?, 'detected', ?, 'auto', ?, ?)",
                (
                    group_id,
                    profile.profile_id,
                    profile.revision_id,
                    digest,
                    now_iso(),
                    now_iso(),
                ),
            )
            for position, media_id in enumerate(media_ids):
                self._conn.execute(
                    "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
                    " VALUES (?, ?, ?, 1)",
                    (group_id, media_id, position),
                )
        return group_id

    def claim_for_merge(self, group_id: str, expected_digest: str) -> None:
        """detected / failed → merging. 再試行でも同じ条件を使う."""
        marks = ", ".join("?" * len(CLAIMABLE))
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET status = 'merging', error = NULL, updated_at = ?"  # noqa: S608
                " WHERE id = ? AND input_digest = ? AND superseded_by_id IS NULL"
                f" AND status IN ({marks})",
                (now_iso(), group_id, expected_digest, *CLAIMABLE),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(
                    f"グループ {group_id} は結合を始められない（状態か構成が変わっている）"
                )

    def record_verification(self, group_id: str, verification_json: str, tool_version: str) -> None:
        """**公開の前に呼ぶ。** 公開の途中で落ちても検証をやり直さずに済む.

        成立条件を DB 側でも確かめる。呼び出し順のバグ 1 つで、結合していない
        グループに検証結果が付くのを防ぐ。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET verification_json = ?, tool_version = ?, updated_at = ?"
                " WHERE id = ? AND status = 'merging' AND superseded_by_id IS NULL",
                (verification_json, tool_version, now_iso(), group_id),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(
                    f"グループ {group_id} は結合中ではないので検証結果を書けない"
                )

    def mark_merged(self, group_id: str) -> None:
        """**出力と検証結果が揃っていることを DB 側で確かめる。**

        揃っていない `merged` 行を作ると、選択肢の側が黙って隠すので
        異常が静かに残る。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET status = 'merged', error = NULL, updated_at = ?"
                " WHERE id = ? AND status = 'merging' AND superseded_by_id IS NULL"
                "   AND output_media_file_id IS NOT NULL AND verification_json IS NOT NULL",
                (now_iso(), group_id),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(
                    f"グループ {group_id} には merged にできる出力と検証結果が無い"
                )

    def mark_failed(self, group_id: str, error: str) -> None:
        self._transition(group_id, "failed", ("merging",), error=error)

    def release(self, group_id: str) -> None:
        """merging → detected. キャンセルと中断の後始末に使う."""
        self._transition(group_id, "detected", ("merging",))

    def adopt(self, group_id: str) -> None:
        """検証不合格の派生物を、中身を見た上で採用する（§10）."""
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT status, adopted_at, output_media_file_id, superseded_by_id"
                " FROM merge_group WHERE id = ?",
                (group_id,),
            ).fetchone()
            if row is None or row["superseded_by_id"] is not None:
                raise GroupNotClaimable(f"グループ {group_id} は採用できない")
            if row["status"] != "merged" or row["output_media_file_id"] is None:
                raise GroupNotClaimable(f"グループ {group_id} には採用できる出力が無い")
            if row["adopted_at"] is not None:
                return
            self._conn.execute(
                "UPDATE merge_group SET adopted_at = ?, updated_at = ? WHERE id = ?",
                (now_iso(), now_iso(), group_id),
            )

    def create_manual(self, media_ids: list[str], digest: str) -> str:
        """手でグループを作る（検出が拾えなかった並びを人が組む）.

        **既に他のグループの構成になっているファイルは受け取らない** —— 1 つの
        ファイルが active な member でいられるのは 1 グループだけ（部分索引）。
        先に古い方を破棄するか、組み直す。
        """
        with immediate(self._conn):
            first = self._conn.execute(
                "SELECT profile_id, profile_revision_id FROM media_file WHERE id = ?",
                (media_ids[0],),
            ).fetchone()
            if first is None:
                raise GroupNotEditable("そのメディアは無い")
            group_id = new_id()
            now = now_iso()
            try:
                self._conn.execute(
                    "INSERT INTO merge_group (id, profile_id, profile_revision_id, status,"
                    " input_digest, detected_by, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'detected', ?, 'manual', ?, ?)",
                    (group_id, first["profile_id"], first["profile_revision_id"], digest, now, now),
                )
                for position, media_id in enumerate(media_ids):
                    self._conn.execute(
                        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
                        " VALUES (?, ?, ?, 1)",
                        (group_id, media_id, position),
                    )
            except sqlite3.IntegrityError as exc:
                # 同じ構成の現行グループが既にある（部分索引）、または構成ファイルが
                # 既に他のグループの member になっている。**どちらも作り直しではなく、
                # 既にあるものを直す場面**なので、理由を添えて断る。
                raise GroupNotEditable("その構成のグループは既にある") from exc
        return group_id

    def discard(self, group_id: str) -> None:
        """グループを捨てる（`skipped` にする）.

        **公開済みの派生物は消さない。** 削除はデータを失う経路なので、選択肢
        （§10）から外れるだけにする。取り残された派生物は孤立として報告される。
        """
        with immediate(self._conn):
            self._assert_editable(group_id)
            self._conn.execute(
                "UPDATE merge_group SET status = 'skipped', updated_at = ? WHERE id = ?",
                (now_iso(), group_id),
            )
            self._invalidate_pending(group_id, "結合グループを破棄した")

    def supersede(self, group_id: str, media_ids: list[str], digest: str) -> str:
        """構成を変えた新しいグループを作り、旧グループをそこへ向け直す.

        **1 つの `BEGIN IMMEDIATE` で行う。** 「新しいグループの作成」「旧グループの
        `superseded_by_id`」「member の付け替え」が割れると、`input_digest` の
        部分索引（`WHERE superseded_by_id IS NULL`）が一時的に 2 行を許して
        UNIQUE 違反になる。
        """
        with immediate(self._conn):
            old = self._assert_editable(group_id)
            # **無効化は向け直す前に行う。** `superseded_by_id` を立てた trigger が
            # 旧 member を `active = 0` にするので、後だと「active な member」を
            # 条件にした無効化が 1 件も当たらない。
            self._invalidate_pending(group_id, "結合グループを組み直した")
            new_id_value = new_id()
            now = now_iso()
            self._conn.execute(
                "INSERT INTO merge_group (id, profile_id, profile_revision_id, status,"
                " input_digest, detected_by, created_at, updated_at)"
                " VALUES (?, ?, ?, 'detected', ?, 'manual', ?, ?)",
                (
                    new_id_value,
                    old["profile_id"],
                    old["profile_revision_id"],
                    digest,
                    now,
                    now,
                ),
            )
            # **順序が意味を持つ。** 1 つのファイルが active な member でいられるのは
            # 1 グループだけ（部分索引 `merge_member_one_active_group`）。旧グループの
            # member を外すのは `superseded_by_id` を立てた trigger なので、
            # 向け直してから新しい member を入れる。
            self._point_at(group_id, new_id_value)
            for position, media_id in enumerate(media_ids):
                self._conn.execute(
                    "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
                    " VALUES (?, ?, ?, 1)",
                    (new_id_value, media_id, position),
                )
        return new_id_value

    def _point_at(self, old_id: str, new_id_value: str) -> None:
        self._conn.execute(
            "UPDATE merge_group SET superseded_by_id = ?, updated_at = ? WHERE id = ?",
            (new_id_value, now_iso(), old_id),
        )

    def _assert_editable(self, group_id: str) -> sqlite3.Row:
        """**これから送られる根拠になっている間は動かさない**（§10）.

        拒むのは (a) 構成ファイルを指す記録が進行中、または (b) `pending` /
        `needs_recheck` の記録があり、その宛先の送信ジョブが待っているか走って
        いるとき。**`complete` / `failed` は妨げない** —— 「一度送ったら二度と
        直せない」では、破棄と再結合の目的が果たせない。
        """
        row = self._conn.execute("SELECT * FROM merge_group WHERE id = ?", (group_id,)).fetchone()
        if row is None:
            raise GroupNotEditable("そのグループは無い")
        in_flight = self._conn.execute(
            "SELECT count(*) AS n FROM upload_record u"
            " JOIN merge_member m ON m.media_file_id = u.media_file_id"
            " WHERE m.merge_group_id = ? AND m.active = 1 AND u.invalidated_at IS NULL"
            "   AND u.state IN ('checking', 'uploading', 'asset_known', 'tagging',"
            "                   'fixing_datetime')",
            (group_id,),
        ).fetchone()["n"]
        if in_flight:
            raise GroupNotEditable("送信中の記録がある")
        queued = self._conn.execute(
            "SELECT count(*) AS n FROM upload_record u"
            " JOIN merge_member m ON m.media_file_id = u.media_file_id"
            " WHERE m.merge_group_id = ? AND m.active = 1 AND u.invalidated_at IS NULL"
            "   AND u.state IN ('pending', 'needs_recheck')"
            "   AND EXISTS (SELECT 1 FROM job j WHERE j.type = 'upload'"
            "               AND j.status IN ('queued', 'running')"
            "               AND j.params_json LIKE '%' || u.destination_id || '%')",
            (group_id,),
        ).fetchone()["n"]
        if queued:
            raise GroupNotEditable("送信ジョブが待っている")
        return row

    def _invalidate_pending(self, group_id: str, reason: str) -> None:
        """**編集と同じ取引で無効化する。**

        残すと、編集の直後に既存のジョブが claim して「根拠が消えた」で無効化され、
        理由の分かりにくい失敗が並ぶ。
        """
        # **まだ無効化されていない行だけを触る。** 既に無効化されている行の理由と
        # 時刻は最初のものを残す（上書きすると、監査で見えるのが二次的な文言に
        # 変わる。Phase 3 の 4 巡目で確定した扱い）。
        self._conn.execute(
            "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?, updated_at = ?"
            " WHERE invalidated_at IS NULL AND state IN ('pending', 'needs_recheck')"
            "   AND media_file_id IN (SELECT media_file_id FROM merge_member"
            "                         WHERE merge_group_id = ? AND active = 1)",
            (now_iso(), reason, now_iso(), group_id),
        )

    def get(self, group_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM merge_group WHERE id = ?", (group_id,)).fetchone()

    def members(self, group_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT mm.position, mm.media_file_id, m.rel_path, m.sha1, m.size_bytes,"
                " m.duration_seconds, m.probe_state, m.captured_at, m.captured_at_source,"
                " m.captured_at_tz, m.captured_at_note, m.missing_at"
                " FROM merge_member mm JOIN media_file m ON m.id = mm.media_file_id"
                " WHERE mm.merge_group_id = ? ORDER BY mm.position",
                (group_id,),
            )
        )

    def list_groups(
        self, status: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[sqlite3.Row]:
        if status is None:
            return list(
                self._conn.execute(
                    "SELECT * FROM merge_group ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            )
        return list(
            self._conn.execute(
                "SELECT * FROM merge_group WHERE status = ?"
                " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        )

    def _transition(
        self, group_id: str, target: str, allowed: tuple[str, ...], error: str | None = None
    ) -> None:
        marks = ", ".join("?" * len(allowed))
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET status = ?, error = ?, updated_at = ?"  # noqa: S608
                f" WHERE id = ? AND superseded_by_id IS NULL AND status IN ({marks})",
                (target, error, now_iso(), group_id, *allowed),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(f"グループ {group_id} は {target} へ動かせない")
