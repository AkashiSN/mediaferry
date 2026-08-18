"""アップロードの pair と状態遷移（§8 / §9.10 / §10）.

`POST /uploads` は `media_ids × destination_ids` の直積を pair 単位の作業項目へ
展開する。**先に一括で検証し、落ちたら何も作らない。** 作成は 1 トランザクション
で行い、実行・失敗・再試行は pair ごとに独立させる。

`selection_rule` は**選択を許可した根拠**で、作成時に決まって以後は変わらない。
再試行は根拠を変えない（`failed` → `pending` の CAS だけ）。上書きすると
「なぜ最初に送信を許可したか」が失われ、claim が安全条件しか見なくなる。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from ..clock import now_iso
from ..ids import new_id
from .connection import immediate
from .destinations import DestinationRepository
from .profiles import ProfileRegistry
from .selection import group_is_current

RESULTS = frozenset(
    {
        "created",
        "retry_queued",
        "already_complete",
        "already_active",
        "awaiting_approval",
        "rejected",
    }
)

ACTIVE_STATES = ("checking", "uploading", "asset_known", "tagging", "fixing_datetime")
CLAIMABLE_STATES = ("pending", "needs_recheck")


class UploadRequestInvalid(ValueError):
    """要求そのものが成立しない. **何も作らずに全体を拒否する。**"""


@dataclass(frozen=True)
class PairResult:
    media_file_id: str
    destination_id: str
    result: str
    record_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _Choice:
    """その pair を許可する根拠. 許可できなければ `reason` だけが入る."""

    selection_rule: str | None
    merge_group_id: str | None
    eligibility_reason: str
    adopt_group_id: str | None = None


class UploadRepository:
    def __init__(
        self,
        conn: sqlite3.Connection,
        registry: ProfileRegistry,
        destinations: DestinationRepository,
    ) -> None:
        self._conn = conn
        self._registry = registry
        self._destinations = destinations

    def create_pairs(
        self, media_ids: Sequence[str], destination_ids: Sequence[str]
    ) -> list[PairResult]:
        media = self._load_media(media_ids)
        revisions = self._load_destinations(destination_ids)

        results: list[PairResult] = []
        with immediate(self._conn):
            for media_id in media_ids:
                choice = self._choose(media[media_id])
                for destination_id in destination_ids:
                    results.append(self._pair(media[media_id], revisions[destination_id], choice))
        return results

    # ------------------------------------------------------------------
    def _load_media(self, media_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        if not media_ids:
            raise UploadRequestInvalid("メディアが 1 件も指定されていない")
        marks = ", ".join("?" * len(media_ids))
        rows = {
            row["id"]: row
            for row in self._conn.execute(
                f"SELECT * FROM media_file WHERE id IN ({marks})",  # noqa: S608
                list(media_ids),
            )
        }
        missing = [media_id for media_id in media_ids if media_id not in rows]
        if missing:
            raise UploadRequestInvalid(f"知らないメディア: {', '.join(missing)}")
        return rows

    def _load_destinations(self, destination_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        if not destination_ids:
            raise UploadRequestInvalid("宛先が 1 件も指定されていない")
        revisions: dict[str, sqlite3.Row] = {}
        for destination_id in destination_ids:
            row = self._destinations.get(destination_id)
            if row is None:
                raise UploadRequestInvalid(f"知らない宛先: {destination_id}")
            if row["archived_at"] is not None:
                raise UploadRequestInvalid(f"宛先「{row['name']}」は保管済み")
            if not row["enabled"]:
                raise UploadRequestInvalid(f"宛先「{row['name']}」は無効になっている")
            if row["current_revision_id"] is None:
                raise UploadRequestInvalid(f"宛先「{row['name']}」は接続を検証していない")
            revisions[destination_id] = self._destinations.current(destination_id)
        return revisions

    def _choose(self, media: sqlite3.Row) -> _Choice:
        """§10 (b)(c) のどの根拠で選べるかを決める."""
        if media["missing_at"] is not None:
            return _Choice(None, None, "ファイルが見つからない")
        if media["role"] == "derived":
            return self._choose_derived(media)
        return self._choose_original(media)

    def _choose_original(self, media: sqlite3.Row) -> _Choice:
        member = self._conn.execute(
            "SELECT g.id AS group_id, g.status AS status FROM merge_member mm"
            " JOIN merge_group g ON g.id = mm.merge_group_id"
            " WHERE mm.media_file_id = ? AND mm.active = 1",
            (media["id"],),
        ).fetchone()
        if member is None:
            return _Choice("default", None, "結合グループに属さないオリジナル")
        if member["status"] in ("failed", "skipped"):
            return _Choice(
                "failed_group_member",
                member["group_id"],
                f"結合できなかったグループ（{member['status']}）の構成ファイル",
            )
        return _Choice(None, None, f"アクティブな結合グループの構成ファイル（{member['status']}）")

    def _choose_derived(self, media: sqlite3.Row) -> _Choice:
        group = self._conn.execute(
            "SELECT * FROM merge_group WHERE output_media_file_id = ?", (media["id"],)
        ).fetchone()
        if group is None:
            return _Choice(None, None, "生成元のグループが分からない派生物")
        if group["status"] != "merged":
            return _Choice(None, None, f"グループが {group['status']} のまま")
        if not group_is_current(self._conn, self._registry, group["id"], media["id"]):
            return _Choice(None, None, "生成元のグループが現在の構成と一致しない")
        if group["adopted_at"] is not None or _passed(group["verification_json"]):
            return _Choice("default", group["id"], "検証に合格した（または採用済みの）結合物")
        # **選ぶ操作が採用そのもの。** 別操作にすると、作った瞬間に
        # 「まだ採用していない」という条件を自分自身が満たさなくなる。
        return _Choice(
            "adopted_derived",
            group["id"],
            "検証不合格の結合物を、中身を確認した上で採用した",
            adopt_group_id=group["id"],
        )

    def _pair(self, media: sqlite3.Row, revision: sqlite3.Row, choice: _Choice) -> PairResult:
        destination_id = revision["destination_id"]
        existing = self._conn.execute(
            "SELECT * FROM upload_record WHERE destination_id = ? AND target_epoch = ?"
            "   AND media_file_id = ?",
            (destination_id, revision["target_epoch"], media["id"]),
        ).fetchone()
        if existing is not None:
            return self._existing(media, destination_id, existing)
        if choice.selection_rule is None:
            return PairResult(
                media["id"], destination_id, "rejected", reason=choice.eligibility_reason
            )
        if choice.adopt_group_id is not None:
            self._conn.execute(
                "UPDATE merge_group SET adopted_at = COALESCE(adopted_at, ?), updated_at = ?"
                " WHERE id = ?",
                (now_iso(), now_iso(), choice.adopt_group_id),
            )
        record_id = new_id()
        self._conn.execute(
            "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
            " selection_rule, origin, eligibility_reason, merge_group_id, checksum,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?, 'unknown', ?, ?, ?, ?, ?)",
            (
                record_id,
                destination_id,
                revision["target_epoch"],
                media["id"],
                choice.selection_rule,
                choice.eligibility_reason,
                choice.merge_group_id,
                media["sha1"],
                now_iso(),
                now_iso(),
            ),
        )
        return PairResult(media["id"], destination_id, "created", record_id=record_id)

    def _existing(self, media: sqlite3.Row, destination_id: str, row: sqlite3.Row) -> PairResult:
        """§10「既存レコードがある場合の遷移」."""
        if row["invalidated_at"] is not None:
            return PairResult(
                media["id"],
                destination_id,
                "rejected",
                row["id"],
                f"無効化されている: {row['invalidated_reason']}",
            )
        if row["state"] == "complete":
            return PairResult(media["id"], destination_id, "already_complete", row["id"])
        if row["state"] in ACTIVE_STATES:
            return PairResult(media["id"], destination_id, "already_active", row["id"])
        if row["state"] == "awaiting_datetime_approval":
            return PairResult(media["id"], destination_id, "awaiting_approval", row["id"])
        if row["state"] == "failed":
            self._conn.execute(
                "UPDATE upload_record SET state = 'pending', claim_job_id = NULL,"
                " claim_token = NULL, claim_expires_at = NULL, updated_at = ?"
                " WHERE id = ? AND state = 'failed'",
                (now_iso(), row["id"]),
            )
            return PairResult(media["id"], destination_id, "retry_queued", row["id"])
        # pending / needs_recheck は既に claim できる状態。二重に作らない。
        return PairResult(media["id"], destination_id, "created", row["id"])


def _passed(verification_json: str | None) -> bool:
    """検証の合否. `passed` が真の bool のときだけ合格（§10）."""
    import json

    if verification_json is None:
        return False
    try:
        return json.loads(verification_json).get("passed") is True
    except (AttributeError, TypeError, ValueError):
        return False
