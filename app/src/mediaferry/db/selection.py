"""アップロードの選択肢（§10）.

「既定で選択肢に出す」条件をここ 1 か所に置く。画面・API・ワーカーが同じ
定義を使うためで、写しを作らない。**唯一の例外が `SENDABLE_CLAUSE`。**
`_ORIGINALS` / `_DERIVED` と同じ §10 の条件を、`SelectionService` を経由しない
軽い集計（`/media?status=unsent`・`/dashboard` の件数）向けに SQL の断片として
並べて置く。片方を変えたらもう片方も変える。

`input_digest` の一致は SQL だけでは判定できない（現行の構成・設定・
プロファイルリビジョンから計算し直す必要がある）ので、SQL で絞ってから
Python で確かめる。この一致を見ないと、**グループを編集した後に旧派生物が
選択肢へ戻る**（旧グループは `status = merged` のまま残るため）。

`SENDABLE_CLAUSE` は digest そのものは比べられないが、**digest がずれる原因の
うち SQL で見えるもの——プロファイルのリビジョン——は見る**。カメラの種類を
保存すると版が上がり、その版で作った結合物は `POST /uploads` が必ず断る
（`group_is_current`）。数え続けると、ホームの「N 件をまだ送っていません」が
押しても消せないまま残る。

安全条件 (a) と `selection_rule` ごとの条件 (c) は claim のときに評価する
もので、`upload_record` と一緒に足す。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core.merge.digest import input_digest
from .profiles import ProfileRegistry

INCLUDE_FAILED_GROUP_MEMBERS = "failed_group_members"
INCLUDE_UNADOPTED_DERIVED = "unadopted_derived"
# 1 応答で返す上限。
DEFAULT_LIMIT = 500

_ORIGINALS = (
    "SELECT m.id, m.rel_path, m.role FROM media_file m"
    " WHERE m.missing_at IS NULL AND m.role = 'original'"
    "   AND NOT EXISTS (SELECT 1 FROM merge_member mm"
    "                   WHERE mm.media_file_id = m.id AND mm.active = 1)"
    " ORDER BY m.captured_at DESC, m.rel_path DESC"
)

_DERIVED = (
    "SELECT m.id, m.rel_path, m.role, g.id AS merge_group_id, g.profile_id,"
    " g.input_digest, g.verification_json, g.adopted_at"
    " FROM media_file m JOIN merge_group g ON g.output_media_file_id = m.id"
    " WHERE m.missing_at IS NULL AND m.role = 'derived'"
    "   AND g.superseded_by_id IS NULL AND g.status = 'merged'"
    " ORDER BY m.captured_at DESC, m.rel_path DESC"
)

# §10「既定で選択肢に出すもの」を 1 個の SQL の断片にしたもの. **`media_file` の
# 別名は呼び出し側が決める**（`{m}`。既定の `SENDABLE_CLAUSE` は `m`）——
# `GET /media?collapse=stack` は、同じ条件を主の行と兄弟の行の 2 つの別名へ当てる。
# `_ORIGINALS` の member 条件・`_DERIVED` の group 条件と同じ §10 の
# 条件を表している。**片方を変えたらもう片方も変える。**
#
# digest そのもの（§10 の derived 条件の最後の 1 つ）はここでは計算できない。現行の
# 構成とプロファイルから計算し直す必要があり、SQL では書けない（`_matching_digests`）。
# **代わりに、digest がずれる原因のうち SQL で見えるものを見る**——グループが
# 作られたときのプロファイルリビジョンが、いまも現行であること。
#
# 残る差は「member の sha1 が変わった（同じ相対パスに別の中身を取り込み直した）」
# だけで、そのときこの断片は数に残す。構成を変えた場合は旧グループが supersede
# されるので、上の条件で既に落ちる。
#
# `verification_json` は壊れた値が入りうる列なので `json_valid` で先に弾く。
# `json_valid` が偽の行は SQLite が `json_type` を評価しないため
# （`AND` は行ごとに左から短絡評価される）、`OperationalError` にならない。
# `json_type(...) = 'true'` は本物の JSON bool にだけ一致し、`{"passed": 1}` の
# ような数値や `{"passed": "false"}` のような文字列を合格に数えない
# （`_verification_passed` の `is True` と同じ判断になる）。
_SENDABLE_TEMPLATE = (
    "{m}.missing_at IS NULL AND ("
    " ({m}.role = 'original' AND NOT EXISTS ("
    "   SELECT 1 FROM merge_member mm WHERE mm.media_file_id = {m}.id AND mm.active = 1))"
    " OR ({m}.role = 'derived' AND EXISTS ("
    "   SELECT 1 FROM merge_group g WHERE g.output_media_file_id = {m}.id"
    "    AND g.superseded_by_id IS NULL AND g.status = 'merged'"
    "    AND g.profile_revision_id = ("
    "      SELECT p.current_revision_id FROM device_profile p WHERE p.id = g.profile_id)"
    "    AND (g.adopted_at IS NOT NULL"
    "         OR (json_valid(g.verification_json)"
    "             AND json_type(g.verification_json, '$.passed') = 'true'))))"
    ")"
)


def sendable_clause(alias: str) -> str:
    """§10 の条件を、指定した `media_file` の別名に当てた SQL の断片.

    **別名は呼び出し側の定数だけ**（値を埋める口ではない）。
    """
    return _SENDABLE_TEMPLATE.format(m=alias)


SENDABLE_CLAUSE = sendable_clause("m")


# 「有効な宛先のどれにも送っていない」を表す SQL の断片（§13 の「やること」）。
#
# **ホームの件数（`/dashboard` の `unsent_total`）と、写真の一覧（`GET /media` の
# `status=unsent` を宛先無しで呼んだとき）が、同じ 1 つの条件を使う。** 2 か所に
# 書くと、片方だけ直したときに「N 件あります」と言いながら一覧が別の集合を出す。
#
# **有効な宛先が 1 つも無ければ、何も該当しない。** ホームの「やること」は**いま
# 押せる操作**なので、送り先が無い・全部休止中・全部アーカイブ済みのときに
# 「まだ送っていません」と言っても、押す先が無い。
#
# **休止中とアーカイブ済みの宛先への記録は「送った」に数えない。** そこへはもう
# 送れないので、その 1 件だけを頼りに「もう送ってある」とは言えない。
_SENT_NOWHERE_TEMPLATE = (
    "EXISTS (SELECT 1 FROM upload_destination d"
    "         WHERE d.archived_at IS NULL AND d.enabled = 1)"
    " AND NOT EXISTS ("
    "   SELECT 1 FROM upload_record u"
    "     JOIN upload_destination d ON d.id = u.destination_id"
    "    WHERE u.media_file_id = {m}.id AND u.invalidated_at IS NULL"
    "      AND d.archived_at IS NULL AND d.enabled = 1)"
)


def sent_nowhere_clause(alias: str) -> str:
    """「どこにも送っていない」を、指定した `media_file` の別名に当てた SQL の断片.

    **別名は呼び出し側の定数だけ**（値を埋める口ではない）。
    """
    return _SENT_NOWHERE_TEMPLATE.format(m=alias)


# **`skipped` はここに来ない。** 破棄したグループは member を手放すので
# （`merge_group_discard_deactivates_members`）、
# その構成ファイルは `_ORIGINALS` に戻る —— 破棄は「このまとまりは無し」であって、
# ファイルを隠すことではない。ここで拾うのは `failed` だけ（再試行できるので、
# グループは生きている）。
_MEMBERS_OF_UNMERGED = (
    "SELECT m.id, m.rel_path, m.role, g.id AS merge_group_id FROM media_file m"
    " JOIN merge_member mm ON mm.media_file_id = m.id"
    " JOIN merge_group g ON g.id = mm.merge_group_id"
    " WHERE m.missing_at IS NULL AND mm.active = 1"
    "   AND g.superseded_by_id IS NULL AND g.status = 'failed'"
    " ORDER BY m.captured_at DESC, m.rel_path DESC"
)


@dataclass(frozen=True)
class Selectable:
    media_file_id: str
    rel_path: str
    role: str
    reason: str
    merge_group_id: str | None


class SelectionService:
    def __init__(self, conn: sqlite3.Connection, registry: ProfileRegistry) -> None:
        self._conn = conn
        self._registry = registry

    def selectable(
        self, include: Sequence[str] = (), limit: int = DEFAULT_LIMIT
    ) -> list[Selectable]:
        """**返す件数に上限を置く。** 数万件の一覧を 1 応答に詰めない.

        呼び出し側は `len(result) == limit` で打ち切りを判断する。
        """
        items = [
            Selectable(row["id"], row["rel_path"], row["role"], "default", None)
            for row in self._conn.execute(_ORIGINALS)
        ]
        wanted_unadopted = INCLUDE_UNADOPTED_DERIVED in include
        derived = self._conn.execute(_DERIVED).fetchall()
        # profile と member はまとめて引く。derived 1 件ごとに問い合わせると、
        # グループが数千あるだけで一覧を開くたびに数千回の query になる。
        matching = self._matching_digests(derived)
        for row in derived:
            if row["merge_group_id"] not in matching:
                continue
            adopted = row["adopted_at"] is not None
            passed = _verification_passed(row["verification_json"])
            if adopted or passed:
                items.append(
                    Selectable(
                        row["id"], row["rel_path"], row["role"], "default", row["merge_group_id"]
                    )
                )
            elif wanted_unadopted:
                items.append(
                    Selectable(
                        row["id"],
                        row["rel_path"],
                        row["role"],
                        INCLUDE_UNADOPTED_DERIVED,
                        row["merge_group_id"],
                    )
                )
        if INCLUDE_FAILED_GROUP_MEMBERS in include:
            items.extend(
                Selectable(
                    row["id"],
                    row["rel_path"],
                    row["role"],
                    "failed_group_member",
                    row["merge_group_id"],
                )
                for row in self._conn.execute(_MEMBERS_OF_UNMERGED)
            )
        return items[:limit]

    def _matching_digests(self, rows: Sequence[sqlite3.Row]) -> set[str]:
        """現行の構成・設定・リビジョンから計算し直し、一致した group を返す."""
        if not rows:
            return set()
        group_ids = [row["merge_group_id"] for row in rows]
        marks = ", ".join("?" * len(group_ids))
        members: dict[str, list[tuple[str, str]]] = {}
        for member in self._conn.execute(
            "SELECT mm.merge_group_id AS group_id, m.id AS media_file_id, m.sha1 AS sha1"  # noqa: S608
            " FROM merge_member mm JOIN media_file m ON m.id = mm.media_file_id"
            f" WHERE mm.merge_group_id IN ({marks}) AND mm.active = 1"
            " ORDER BY mm.merge_group_id, mm.position",
            group_ids,
        ):
            members.setdefault(member["group_id"], []).append(
                (member["media_file_id"], member["sha1"])
            )

        profiles: dict[str, Any] = {}
        matching: set[str] = set()
        for row in rows:
            profile = profiles.get(row["profile_id"])
            if profile is None:
                profile = profiles[row["profile_id"]] = self._registry.by_id(row["profile_id"])
            current = input_digest(
                members.get(row["merge_group_id"], []),
                profile.definition.merge,
                profile.revision_id,
            )
            if current == row["input_digest"]:
                matching.add(row["merge_group_id"])
        return matching


def _verification_passed(verification_json: str | None) -> bool:
    """`passed` が真の bool のときだけ合格.

    `bool(value)` にすると、`"passed": "false"` のような文字列まで合格に
    してしまう。
    """
    if verification_json is None:
        return False
    try:
        return json.loads(verification_json).get("passed") is True
    except AttributeError, TypeError, ValueError:
        return False


def expected_digest(
    conn: sqlite3.Connection, registry: ProfileRegistry, group_id: str
) -> str | None:
    """現行の構成・設定・リビジョンから計算し直した digest.

    グループが無ければ None。**保存値との比較は呼び出し側が行う。**
    """
    row = conn.execute("SELECT profile_id FROM merge_group WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        return None
    members = [
        (member["media_file_id"], member["sha1"])
        for member in conn.execute(
            "SELECT m.id AS media_file_id, m.sha1 AS sha1 FROM merge_member mm"
            " JOIN media_file m ON m.id = mm.media_file_id"
            " WHERE mm.merge_group_id = ? AND mm.active = 1 ORDER BY mm.position",
            (group_id,),
        )
    ]
    profile = registry.by_id(row["profile_id"])
    return input_digest(members, profile.definition.merge, profile.revision_id)


def group_is_current(
    conn: sqlite3.Connection, registry: ProfileRegistry, group_id: str, media_file_id: str
) -> bool:
    """§10 (a) の derived 条件. claim 時と一覧の両方がこれを使う.

    supersede されておらず、その media_file がこのグループの出力で、
    入力の同一性が現行と一致していること。
    """
    row = conn.execute(
        "SELECT superseded_by_id, output_media_file_id, input_digest FROM merge_group WHERE id = ?",
        (group_id,),
    ).fetchone()
    if row is None or row["superseded_by_id"] is not None:
        return False
    if row["output_media_file_id"] != media_file_id:
        return False
    return expected_digest(conn, registry, group_id) == row["input_digest"]
