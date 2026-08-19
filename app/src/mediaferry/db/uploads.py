"""アップロードの pair と状態遷移（§8 / §9.10 / §10）.

`POST /uploads` は `media_ids × destination_ids` の直積を pair 単位の作業項目へ
展開する。**先に一括で検証し、落ちたら何も作らない。** 作成は 1 トランザクション
で行い、実行・失敗・再試行は pair ごとに独立させる。

`selection_rule` は**選択を許可した根拠**で、作成時に決まって以後は変わらない。
再試行は根拠を変えない（`failed` → `pending` の CAS だけ）。上書きすると
「なぜ最初に送信を許可したか」が失われ、claim が安全条件しか見なくなる。
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from ..clock import iso, now_iso, utcnow
from ..ids import new_id
from .connection import immediate
from .destinations import DestinationRepository
from .jobs import JobContext
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


@dataclass(frozen=True)
class Stamp:
    """再確認 1 件分の結果と、**それを観測したときの行の姿**.

    `expect_*` は「この結果が誰の観測か」を示す。書く直前に行が動いていたら、
    こちらの観測は古いので書かない（`stamp_many`）。
    """

    record_id: str
    asset_id: str | None
    is_trashed: int
    expect_asset_id: str | None
    expect_checked_at: str | None


CLAIMABLE_STATES = ("pending", "needs_recheck")


class ClaimLost(RuntimeError):
    """自分の claim_token では、その行を動かせない.

    キャンセルされた古いジョブが、新しいジョブの状態を上書きするのを防ぐ。
    """


# 進行中に置ける状態と、claim を外す状態（`0004` の CHECK と一致させる）。
TERMINAL_STATES = ("complete", "failed", "awaiting_datetime_approval")
RELEASED_STATES = ("pending", "needs_recheck")


# 進行中に置ける状態と、claim を外す状態（`0004` の CHECK と一致させる）。
TERMINAL_STATES = ("complete", "failed", "awaiting_datetime_approval")
RELEASED_STATES = ("pending", "needs_recheck")


class NoLongerEligible(RuntimeError):
    """送る直前に §10 の根拠が崩れていた. 送らずに見送る."""


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

        results: list[PairResult] = []
        with immediate(self._conn):
            # **宛先の現行リビジョンは INSERT と同じトランザクションで解決する**（§8）。
            # 外で読むと、読んだ後・書く前に他の書き手が epoch を進めて旧 epoch の
            # 無効化まで済ませられる。すり抜けた行は `claim_next` が現行 epoch しか
            # 拾わないので送られず、次の起動の掃除まで理由の無い `pending` で残る。
            revisions = self._load_destinations(destination_ids)
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

    def claim_next(
        self,
        destination_id: str,
        job_id: str,
        token: str,
        lease_seconds: int = 60,
    ) -> sqlite3.Row | None:
        """CAS で 1 件だけ所有権を取る. 取れなければ None.

        **`SELECT ... FOR UPDATE` は無い。** 更新できた 1 ジョブだけが実行者になる。

        **現行リビジョンは pair ごとに、同じトランザクションの中で解決する**（§8）。
        ジョブの開始時に 1 回だけ読んで固定すると、途中で API キーを変えた場合に
        未 claim の pair まで旧リビジョンで送る（旧 credential は purge されて
        いるかもしれない）。epoch が進んでいれば、そもそも対象から外れる。
        """
        marks = ", ".join("?" * len(CLAIMABLE_STATES))
        with immediate(self._conn):
            revision = self._conn.execute(
                "SELECT r.* FROM upload_destination d"
                " JOIN destination_revision r ON r.id = d.current_revision_id"
                " WHERE d.id = ? AND d.enabled = 1 AND d.archived_at IS NULL",
                (destination_id,),
            ).fetchone()
            if revision is None:
                return None
            row = self._conn.execute(
                "SELECT id FROM upload_record"  # noqa: S608
                " WHERE destination_id = ? AND target_epoch = ? AND invalidated_at IS NULL"
                f"   AND state IN ({marks})"
                "   AND (claim_expires_at IS NULL OR claim_expires_at < ?)"
                " ORDER BY created_at LIMIT 1",
                (
                    destination_id,
                    revision["target_epoch"],
                    *CLAIMABLE_STATES,
                    now_iso(),
                ),
            ).fetchone()
            if row is None:
                return None
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'checking', claim_job_id = ?,"  # noqa: S608
                " claim_token = ?, claim_expires_at = ?, destination_revision_id = ?,"
                " updated_at = ?"
                f" WHERE id = ? AND invalidated_at IS NULL AND state IN ({marks})",
                (
                    job_id,
                    token,
                    _expiry(lease_seconds),
                    revision["id"],
                    now_iso(),
                    row["id"],
                    *CLAIMABLE_STATES,
                ),
            )
            if updated.rowcount != 1:
                return None
            return self._conn.execute(
                "SELECT * FROM upload_record WHERE id = ?", (row["id"],)
            ).fetchone()

    def check_eligibility(self, row: sqlite3.Row) -> str | None:
        """§10 (a) と、`selection_rule` に対応する (c) を**今の状態で**評価する.

        claim 時に評価するのは「選べる場面」ではなく「その根拠が今も成立して
        いるか」。混同すると、採用した瞬間に自分自身が条件を満たさなくなる。
        """
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (row["media_file_id"],)
        ).fetchone()
        if media is None or media["missing_at"] is not None:
            return "ファイルが見つからない"
        if row["invalidated_at"] is not None:
            return f"無効化されている: {row['invalidated_reason']}"
        destination = self._destinations.get(row["destination_id"])
        if destination is None or destination["archived_at"] is not None:
            return "宛先が保管済み"
        if not destination["enabled"]:
            return "宛先が無効になっている"
        revision = self._destinations.revision(row["destination_revision_id"])
        if revision["target_epoch"] != row["target_epoch"]:
            return "宛先の向き先が変わっている"

        if media["role"] == "derived" and not group_is_current(
            self._conn, self._registry, row["merge_group_id"] or "", media["id"]
        ):
            return "生成元のグループが現在の構成と一致しない"
        return self._check_rule(row, media)

    def _check_rule(self, row: sqlite3.Row, media: sqlite3.Row) -> str | None:
        rule = row["selection_rule"]
        if rule == "failed_group_member":
            member = self._conn.execute(
                "SELECT g.status AS status FROM merge_member mm"
                " JOIN merge_group g ON g.id = mm.merge_group_id"
                " WHERE mm.media_file_id = ? AND mm.active = 1 AND g.id = ?",
                (media["id"], row["merge_group_id"]),
            ).fetchone()
            if member is None or member["status"] not in ("failed", "skipped"):
                return "結合できなかったグループの構成ファイル、という根拠が成立しない"
            return None
        if rule == "adopted_derived":
            adopted = self._conn.execute(
                "SELECT adopted_at FROM merge_group WHERE id = ?", (row["merge_group_id"],)
            ).fetchone()
            if adopted is None or adopted["adopted_at"] is None:
                return "採用の記録が無い"
            return None
        # default は (b) を満たすこと。derived の条件は上で見たので、
        # ここではアクティブなグループの member でないことを確かめる。
        if media["role"] == "original":
            member = self._conn.execute(
                "SELECT g.status AS status FROM merge_member mm"
                " JOIN merge_group g ON g.id = mm.merge_group_id"
                " WHERE mm.media_file_id = ? AND mm.active = 1",
                (media["id"],),
            ).fetchone()
            if member is not None and member["status"] not in ("failed", "skipped"):
                return "アクティブな結合グループの構成ファイルになっている"
        return None

    def prepare_side_effect(
        self,
        ctx: JobContext,
        record_id: str,
        expect_state: str,
        lease_seconds: int = 60,
        verify_eligibility: bool = False,
    ) -> None:
        """外部への副作用の直前に呼ぶ（§8）.

        **リースと claim を 1 つの `BEGIN IMMEDIATE` の中で確かめる。** 分けると
        その隙間にキャンセルが commit でき、「キャンセル済みと表示した後に
        送信・タグ付与・日時変更が行われる」経路が残る。`ctx.assert_lease()` は
        `cancelling` を通さない（`extend_lease` は通すので、これが必要）。

        `verify_eligibility` を立てると、§10 の根拠も同じトランザクションで
        見直す。**最初の 1 バイトを送る前に立てる。** claim の直後の判定は
        その時点の状態でしかなく、そこから送信までの間に利用者が結合を
        やり直せば、いま結合中のグループの構成ファイルを送ってしまう。
        資産が既にリモートにある後（タグ・日時）は、見送っても取り消せないので
        立てない。
        """
        with immediate(self._conn):
            ctx.assert_lease()
            if verify_eligibility:
                reason = self.check_eligibility(
                    self._conn.execute(
                        "SELECT * FROM upload_record WHERE id = ?", (record_id,)
                    ).fetchone()
                )
                if reason is not None:
                    raise NoLongerEligible(reason)
            updated = self._conn.execute(
                "UPDATE upload_record SET claim_expires_at = ?, updated_at = ?"
                " WHERE id = ? AND claim_token = ? AND state = ? AND invalidated_at IS NULL"
                "   AND claim_expires_at > ?",
                (
                    _expiry(lease_seconds),
                    now_iso(),
                    record_id,
                    ctx.lease_token,
                    expect_state,
                    now_iso(),
                ),
            )
            if updated.rowcount != 1:
                raise ClaimLost(f"レコード {record_id} の所有権を失っている")

    def extend_claim(self, record_id: str, token: str, lease_seconds: int = 60) -> None:
        self._cas(record_id, token, "claim_expires_at = ?", (_expiry(lease_seconds),), strict=True)

    def advance_owned(
        self, ctx: JobContext, record_id: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """外部副作用の結果を commit する. **リースも同じ取引の中で確かめる。**"""
        with immediate(self._conn):
            ctx.assert_lease()
            self._locked_cas(
                record_id, ctx.lease_token, "state = ?", (state,), expect_state, **fields
            )

    def finish_owned(
        self, ctx: JobContext, record_id: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """終端へ倒して claim を外す. **リースも同じ取引の中で確かめる。**"""
        if state not in TERMINAL_STATES:
            raise ValueError(f"終端ではない状態: {state}")
        with immediate(self._conn):
            ctx.assert_lease()
            self._locked_cas(
                record_id,
                ctx.lease_token,
                "state = ?, claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
                (state,),
                expect_state,
                **fields,
            )

    def advance(
        self, record_id: str, token: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """進行中の状態へ進める. claim は保ったまま.

        **`expect_state` を必ず渡す。** 外部副作用の結果を commit する時点でも、
        自分が期待した状態のままであることを確かめる（§8）。
        """
        self._cas(
            record_id,
            token,
            "state = ?",
            (state,),
            strict=True,
            expect_state=expect_state,
            **fields,
        )

    def finish(
        self, record_id: str, token: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """終端（complete / failed / awaiting）へ倒し、claim を外す.

        **未来の期限を残したまま終端にしない。** 残ると、明示操作しても期限まで
        claim できなくなる（§8）。
        """
        if state not in TERMINAL_STATES:
            raise ValueError(f"終端ではない状態: {state}")
        self._cas(
            record_id,
            token,
            "state = ?, claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
            (state,),
            strict=True,
            expect_state=expect_state,
            **fields,
        )

    def release_to(self, record_id: str, token: str, state: str, **fields: object) -> None:
        """再び claim できる状態へ戻し、claim を外す."""
        if state not in RELEASED_STATES:
            raise ValueError(f"claim できる状態ではない: {state}")
        self._cas(
            record_id,
            token,
            "state = ?, claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
            (state,),
            **fields,
        )

    def refuse(self, record_id: str, token: str, reason: str) -> None:
        """claim してから条件を満たさないと分かった行を無効化する（§10 の多重防御）.

        **`state` も `pending` へ戻す。** `0004` の CHECK が「進行中の状態なら
        claim を持つ」と定めているので、`checking` のまま claim を外すと
        `IntegrityError` になる。無効化された行は claim の条件
        （`invalidated_at IS NULL`）で弾かれるので、`pending` に戻しても
        拾われない。

        **既に無効化されている行の理由と時刻は上書きしない。** claim を持っている
        間に別の経路（グループの構成変更など）で無効化されることがあり、
        上書きすると監査で見えるのが二次的な文言に変わって、いつ何が起きたのかを
        読めなくする。
        """
        self._cas(
            record_id,
            token,
            "state = 'pending',"
            " invalidated_at = COALESCE(invalidated_at, ?),"
            " invalidated_reason = COALESCE(invalidated_reason, ?),"
            " claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
            (now_iso(), reason),
        )

    def invalidate_for_group(self, group_id: str, reason: str) -> int:
        """グループが変わったときに、未完了のレコードをまとめて無効化する."""
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?,"
                " updated_at = ? WHERE merge_group_id = ? AND invalidated_at IS NULL"
                "   AND state <> 'complete'",
                (now_iso(), reason, now_iso(), group_id),
            )
            return updated.rowcount

    def claim_for_approval(
        self, record_id: str, job_id: str, token: str, lease_seconds: int = 60
    ) -> None:
        """`awaiting_datetime_approval` → `fixing_datetime` を CAS で取る.

        却下と競合したら 0 行になる（先に `complete` へ倒れている）。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'fixing_datetime', claim_job_id = ?,"
                " claim_token = ?, claim_expires_at = ?, updated_at = ?"
                " WHERE id = ? AND state = 'awaiting_datetime_approval'"
                "   AND invalidated_at IS NULL AND claim_job_id IS NULL",
                (job_id, token, _expiry(lease_seconds), now_iso(), record_id),
            )
            if updated.rowcount != 1:
                raise ClaimLost(f"レコード {record_id} は承認できる状態ではない")

    def release_from_approval(self, record_id: str, token: str) -> None:
        """承認の途中で降りる. 承認待ちへ戻して人に見せる."""
        with contextlib.suppress(ClaimLost):
            self._cas(
                record_id,
                token,
                "state = 'awaiting_datetime_approval', claim_job_id = NULL,"
                " claim_token = NULL, claim_expires_at = NULL",
                (),
            )

    def invalidate_stale(self) -> int:
        """根拠が成立しなくなった未完了のレコードを無効化する（§10 の多重防御）."""
        invalidated = 0
        for row in self._conn.execute(
            "SELECT * FROM upload_record WHERE invalidated_at IS NULL AND state <> 'complete'"
        ).fetchall():
            reason = self._stale_reason(row)
            if reason is None:
                continue
            self._conn.execute(
                "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?,"
                " updated_at = ? WHERE id = ?",
                (now_iso(), reason, now_iso(), row["id"]),
            )
            invalidated += 1
        return invalidated

    def _stale_reason(self, row: sqlite3.Row) -> str | None:
        """グループに紐づく根拠だけを見る. 宛先の有効・無効は claim 時に見る."""
        if row["merge_group_id"] is None:
            return None
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (row["media_file_id"],)
        ).fetchone()
        if media is None:
            return f"メディア {row['media_file_id']} が無い"
        if media["role"] == "derived" and not group_is_current(
            self._conn, self._registry, row["merge_group_id"], media["id"]
        ):
            return f"グループ {row['merge_group_id']} が現在の構成と一致しない"
        return self._check_rule(row, media)

    def invalidate_old_epoch(self, destination_id: str, current_epoch: int, reason: str) -> int:
        """epoch を進めた宛先の、旧 epoch の未完了レコードを破棄する（§8）.

        **`complete` は残す。** 旧 epoch の記録は監査履歴として意味がある。
        claim は epoch で絞るので送られはしないが、理由が無いまま `pending` で
        残ると、利用者から見て「いつまでも送られない項目」になる。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?,"
                " updated_at = ? WHERE destination_id = ? AND target_epoch < ?"
                "   AND invalidated_at IS NULL AND state <> 'complete'",
                (now_iso(), reason, now_iso(), destination_id, current_epoch),
            )
            return updated.rowcount

    def records_for_recheck(self, destination_id: str, target_epoch: int) -> list[sqlite3.Row]:
        """再確認の対象. **現行 epoch の `complete` だけ**を、全件返す.

        旧 epoch は別ライブラリへ送った履歴なので、現行の資格情報で照合しない。
        件数の上限は置かない（打ち切ると「N 件確認した」が嘘になる）。
        """
        return list(
            self._conn.execute(
                "SELECT * FROM upload_record WHERE destination_id = ? AND target_epoch = ?"
                "   AND state = 'complete' AND invalidated_at IS NULL"
                " ORDER BY created_at",
                (destination_id, target_epoch),
            )
        )

    def stamp_many(
        self,
        ctx: JobContext,
        stamps: Sequence[Stamp],
        checked_at: str,
    ) -> set[str]:
        """再確認の結果を**まとめて 1 つのトランザクションで**書き、書けた id を返す.

        1 行ずつ commit すると、途中でキャンセルやリースの失効が起きたときに
        中途半端な状態が残る。`ctx.assert_lease()` を同じ取引に入れるので、
        リースが切れていれば 1 行も書かない（`cancelled()` はジョブの状態しか
        見ないので、これが要る）。

        **`complete` の行だけ**を対象にする。進行中の行には所有者がいる。

        **照合したときの行にしか書かない。** `complete` は終端ではない: 消滅と
        判定された行を利用者が requeue でき、送り直しが済めばまた `complete` に
        戻る。id と現在の状態だけを条件にすると、その新しい `remote_asset_id` を
        古い観測（消滅＝NULL）で消す。観測した値そのものを条件に入れて、動いた
        行は**書かずに飛ばす**（相手側は新しい観測を持っているので、こちらの
        古い結果で上書きする理由が無い）。書けた id を返すので、呼び出し側は
        飛ばした行を「確認した」と数えずに済む。
        """
        written: set[str] = set()
        with immediate(self._conn):
            ctx.assert_lease()
            for stamp in stamps:
                updated = self._conn.execute(
                    "UPDATE upload_record SET remote_asset_id = ?, remote_is_trashed = ?,"
                    " remote_checked_at = ?, updated_at = ?"
                    " WHERE id = ? AND state = 'complete'"
                    "   AND remote_asset_id IS ? AND remote_checked_at IS ?",
                    (
                        stamp.asset_id,
                        stamp.is_trashed,
                        checked_at,
                        now_iso(),
                        stamp.record_id,
                        stamp.expect_asset_id,
                        stamp.expect_checked_at,
                    ),
                )
                if updated.rowcount == 1:
                    written.add(stamp.record_id)
        return written

    def stamp_remote(
        self, record_id: str, asset_id: str | None, is_trashed: int, checked_at: str
    ) -> None:
        """再確認の結果を書く. **`complete` の行だけ**を対象にする.

        進行中の行は所有者がいるので、claim を持たないこの経路では触らない。
        """
        with immediate(self._conn):
            self._conn.execute(
                "UPDATE upload_record SET remote_asset_id = ?, remote_is_trashed = ?,"
                " remote_checked_at = ?, updated_at = ? WHERE id = ? AND state = 'complete'",
                (asset_id, is_trashed, checked_at, now_iso(), record_id),
            )

    def stamp_remote_datetime(
        self,
        record_id: str,
        date_time_original: str | None,
        checked_at: str,
        expect_checked_at: str | None,
    ) -> bool:
        """観測したリモートの日時を書く. 書けたら真.

        **観測したときの姿を条件に入れる**（`stamp_many` と同じ形）。別の経路が
        先に新しい観測を書いていたら、こちらの古い結果で上書きしない。

        **`complete` の行だけ**を対象にする（進行中の行には所有者がいる）。
        日時と観測時刻は同じ UPDATE で書く —— 分けると「日時は新しいが観測時刻は
        古い」行ができる。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET remote_datetime_original = ?, remote_checked_at = ?,"
                " updated_at = ? WHERE id = ? AND state = 'complete' AND remote_checked_at IS ?",
                (date_time_original, checked_at, now_iso(), record_id, expect_checked_at),
            )
        return updated.rowcount == 1

    def release_interrupted(self) -> int:
        """進行中のまま残ったレコードを `needs_recheck` へ落とす.

        **`pending` ではない。** `uploading` で落ちた場合、サーバ側で成功して
        いるかもしれない。次回 `checking` から照合し直せば二重にはならない。
        起動時に呼ぶので、走っているジョブは既に倒れている。

        **放置された claim を回収する唯一の経路**でもある（`claim_next` は
        進行中の状態を拾わないので、期限切れの横取りは起こらない）。
        """
        marks = ", ".join("?" * len(ACTIVE_STATES))
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'needs_recheck', claim_job_id = NULL,"  # noqa: S608
                " claim_token = NULL, claim_expires_at = NULL, updated_at = ?"
                f" WHERE state IN ({marks})",
                (now_iso(), *ACTIVE_STATES),
            )
            return updated.rowcount

    def get(self, record_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM upload_record WHERE id = ?", (record_id,)
        ).fetchone()

    def list_records(
        self, destination_id: str | None = None, state: str | None = None, limit: int = 200
    ) -> list[sqlite3.Row]:
        clauses, params = [], []
        if destination_id is not None:
            clauses.append("destination_id = ?")
            params.append(destination_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return list(
            self._conn.execute(
                f"SELECT * FROM upload_record{where} ORDER BY updated_at DESC LIMIT ?",  # noqa: S608
                (*params, limit),
            )
        )

    def _cas(
        self,
        record_id: str,
        token: str,
        assignment: str,
        params: tuple,
        strict: bool = False,
        expect_state: str | None = None,
        **fields: object,
    ) -> None:
        """`claim_token` が自分のものである行だけを動かす.

        `strict` を立てると、期限切れと無効化も条件に入れる。**外部副作用の
        結果を書く経路では必ず立てる。** 逆に、降りるための解放
        （`release_to` / `refuse`）では立てない —— 期限が切れていても、
        自分が持っていた行は片付けられる必要がある。
        """
        with immediate(self._conn):
            self._locked_cas(
                record_id, token, assignment, params, expect_state, strict=strict, **fields
            )

    def _locked_cas(
        self,
        record_id: str,
        token: str,
        assignment: str,
        params: tuple,
        expect_state: str | None,
        strict: bool = True,
        **fields: object,
    ) -> None:
        """**呼び出し側が開いたトランザクションの中で使う。** 条件は `_cas` と同じ."""
        extra = "".join(f", {name} = ?" for name in fields)
        clauses = ["id = ?", "claim_token = ?"]
        guard: list[object] = [record_id, token]
        if strict:
            clauses += ["claim_expires_at > ?", "invalidated_at IS NULL"]
            guard.append(now_iso())
        if expect_state is not None:
            clauses.append("state = ?")
            guard.append(expect_state)
        updated = self._conn.execute(
            f"UPDATE upload_record SET {assignment}{extra}, updated_at = ?"  # noqa: S608
            f" WHERE {' AND '.join(clauses)}",
            (*params, *fields.values(), now_iso(), *guard),
        )
        if updated.rowcount != 1:
            raise ClaimLost(f"レコード {record_id} の claim を失っている")


def _passed(verification_json: str | None) -> bool:
    """検証の合否. `passed` が真の bool のときだけ合格（§10）."""
    import json

    if verification_json is None:
        return False
    try:
        return json.loads(verification_json).get("passed") is True
    except (AttributeError, TypeError, ValueError):
        return False


def _expiry(seconds: int) -> str:
    return iso(utcnow() + timedelta(seconds=seconds))
