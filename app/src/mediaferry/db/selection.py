"""アップロードの選択肢（§10）.

「既定で選択肢に出す」条件をここ 1 か所に置く。画面・API・ワーカーが同じ
定義を使うためで、写しを作らない。

`input_digest` の一致は SQL だけでは判定できない（現行の構成・設定・
プロファイルリビジョンから計算し直す必要がある）ので、SQL で絞ってから
Python で確かめる。この一致を見ないと、**グループを編集した後に旧派生物が
選択肢へ戻る**（旧グループは `status = merged` のまま残るため）。

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
# 1 応答で返す上限。画面の pagination は Phase 4。
DEFAULT_LIMIT = 500

_ORIGINALS = (
    "SELECT m.id, m.rel_path, m.role FROM media_file m"
    " WHERE m.missing_at IS NULL AND m.role = 'original'"
    "   AND NOT EXISTS (SELECT 1 FROM merge_member mm"
    "                   WHERE mm.media_file_id = m.id AND mm.active = 1)"
    " ORDER BY m.captured_at DESC"
)

_DERIVED = (
    "SELECT m.id, m.rel_path, m.role, g.id AS merge_group_id, g.profile_id,"
    " g.input_digest, g.verification_json, g.adopted_at"
    " FROM media_file m JOIN merge_group g ON g.output_media_file_id = m.id"
    " WHERE m.missing_at IS NULL AND m.role = 'derived'"
    "   AND g.superseded_by_id IS NULL AND g.status = 'merged'"
    " ORDER BY m.captured_at DESC"
)

# `skipped` は Phase 2 では作られない（破棄は Phase 4）。§10 が「failed / skipped の
# グループの member」と定めているので、条件は最初から両方書いておく。
_MEMBERS_OF_UNMERGED = (
    "SELECT m.id, m.rel_path, m.role, g.id AS merge_group_id FROM media_file m"
    " JOIN merge_member mm ON mm.media_file_id = m.id"
    " JOIN merge_group g ON g.id = mm.merge_group_id"
    " WHERE m.missing_at IS NULL AND mm.active = 1"
    "   AND g.superseded_by_id IS NULL AND g.status IN ('failed', 'skipped')"
    " ORDER BY m.captured_at DESC"
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

        呼び出し側は `len(result) == limit` で打ち切りを判断する。カーソルを
        使った本格的な pagination は、画面の要件が決まる Phase 4 で足す。
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
