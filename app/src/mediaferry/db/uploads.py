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
from ..core.uploads.stacking import HIGH_SENTINEL
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
# **claim できる行を選ぶ条件を 1 か所に持つ。** `claim_next` と、進捗の分母を
# 数える `sendable_totals` が同じ集合を指す。片方だけ変えると、画面の
# 「N 件中 M 件」が実際に送る対象と食い違う。
CLAIMABLE_CLAUSE = (
    "destination_id = ? AND target_epoch = ? AND invalidated_at IS NULL"
    f" AND state IN ({', '.join('?' * len(CLAIMABLE_STATES))})"
)


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


class StackGroupChanged(RuntimeError):
    """組の前提が、相手を待っている間に変わった. **その組は諦める。**"""


class DestinationChanged(StackGroupChanged):
    """**宛先の向き先が変わった。** 固定した epoch 全体が無効なので打ち切る.

    組ごとの事情ではないので、次の組へ進んでも同じ失敗を繰り返すだけになる
    （旧 epoch の未評価行を末尾まで走査することになる）。
    """


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
            # **内部の ID を理由に混ぜない**（§13）。この文言は画面にそのまま出る。
            raise UploadRequestInvalid(
                f"選んだファイルのうち {len(missing)} 件が見つからない。画面を開き直す"
            )
        return rows

    def _load_destinations(self, destination_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        if not destination_ids:
            raise UploadRequestInvalid("宛先が 1 件も指定されていない")
        revisions: dict[str, sqlite3.Row] = {}
        for destination_id in destination_ids:
            row = self._destinations.get(destination_id)
            if row is None:
                raise UploadRequestInvalid("その送り先は見つからない。設定 › 送り先を確かめる")
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
            # **無効化された行は無いものとして扱う**（§10 の遷移表「再利用しない」）。
            # 拾うと `_existing` が断り、「まだ送っていない」と出ているのに送れない。
            "SELECT * FROM upload_record WHERE destination_id = ? AND target_epoch = ?"
            "   AND media_file_id = ? AND invalidated_at IS NULL",
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
        """§10「既存レコードがある場合の遷移」.

        **`invalidated_at` の分岐は保険。** `_pair` は無効化された行を引かないので
        構造的に到達しない。**それでも残す**（無効化された行を再利用しない、という
        判断をコードから消さない）。
        """
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

    def _current_revision(self, destination_id: str) -> sqlite3.Row | None:
        """送れる宛先の現行リビジョン. 無効・保管済みの宛先には無い."""
        return self._conn.execute(
            "SELECT r.* FROM upload_destination d"
            " JOIN destination_revision r ON r.id = d.current_revision_id"
            " WHERE d.id = ? AND d.enabled = 1 AND d.archived_at IS NULL",
            (destination_id,),
        ).fetchone()

    def sendable_totals(self, destination_id: str) -> tuple[int, int]:
        """これから送る件数と合計バイトを返す. **進捗の分母**（§2）.

        条件は `claim_next` が行を選ぶときの条件のうち、**期限を除く部分**
        （`CLAIMABLE_CLAUSE`）。`claim_next` はこれに
        `claim_expires_at IS NULL OR claim_expires_at < ?` を足す。**指す集合は
        一致する** —— `release_to` は必ず `claim_expires_at` を消し、claim 済みの
        行は `checking` にいて `CLAIMABLE_STATES` から外れるため。**片側にだけ
        条件を足すと一致が壊れる**（画面の「N 件中 M 件」が実際に送る対象と
        食い違う）。

        数えた後も対象は増減しうるので、これは開始時のひとにらみでしかない。
        実測が追い越したら、画面に出す側が合計を伸ばす。
        """
        revision = self._current_revision(destination_id)
        if revision is None:
            return (0, 0)
        row = self._conn.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(media_file.size_bytes), 0) AS bytes"  # noqa: S608
            " FROM upload_record JOIN media_file ON media_file.id = upload_record.media_file_id"
            f" WHERE {CLAIMABLE_CLAUSE}",
            (destination_id, revision["target_epoch"], *CLAIMABLE_STATES),
        ).fetchone()
        return (row["files"], row["bytes"])

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
            revision = self._current_revision(destination_id)
            if revision is None:
                return None
            row = self._conn.execute(
                f"SELECT id FROM upload_record WHERE {CLAIMABLE_CLAUSE}"  # noqa: S608
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
            # **`skipped` はここに来ない。** 破棄したグループは member を手放すので
            # （`0017`）、その構成ファイルは既定の一覧の側で選ばれる。
            if member is None or member["status"] != "failed":
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

        **照合したときの行にしか書かない。** 観測と現在の姿がずれていれば、
        こちらの結果は古い。id と現在の状態だけを条件にすると、他の照合が書いた
        新しい `remote_asset_id` を古い観測（消滅＝NULL）で消す。
        """
        written: set[str] = set()
        with immediate(self._conn):
            ctx.assert_lease()
            for stamp in stamps:
                # **先に、この観測がまだ通るかを確かめる。** 組を開くのは
                # `remote_asset_id` を変える前でなければならない（`0016`）が、
                # CAS に外れる観測で開いてしまうと「古い観測は何も書かない」を
                # 破る（現在のスタックを壊す）。**`BEGIN IMMEDIATE` の中なので、
                # 確認と書き込みの間に誰も割り込めない。**
                still = self._conn.execute(
                    "SELECT 1 FROM upload_record WHERE id = ? AND state = 'complete'"
                    "   AND remote_asset_id IS ? AND remote_checked_at IS ?",
                    (stamp.record_id, stamp.expect_asset_id, stamp.expect_checked_at),
                ).fetchone()
                if still is None:
                    continue
                # **ID が変わるなら、その前にスタックの結果を組ごと捨てる**（§9.11）。
                # スタックは「その `remote_asset_id` を送った結果」なので、消滅や
                # 別 ID への差し替えで現在の姿を表さなくなる。
                if stamp.asset_id != stamp.expect_asset_id:
                    self._reopen_stack_of(stamp.record_id)
                updated = self._conn.execute(
                    "UPDATE upload_record SET remote_asset_id = ?, remote_is_trashed = ?,"
                    " remote_checked_at = ?, updated_at = ?,"
                    # **消えていたら、その場で無効化する**（§9.10）。無効化された
                    # 記録は「この宛先の有効な記録」ではなくなるので、メディアは
                    # 通常の「まだ送っていない」へ戻る。**観測と無効化を別の取引に
                    # 分けない** —— 分けると「消えたと記録したが未送信に戻って
                    # いない」中途半端な状態が残る。
                    #
                    # **`COALESCE` で書く。** 在る側には NULL を渡すので、既存の
                    # 値をそのまま残す（消し戻さない）。
                    " invalidated_at = COALESCE(invalidated_at, ?),"
                    " invalidated_reason = COALESCE(invalidated_reason, ?)"
                    " WHERE id = ? AND state = 'complete'"
                    "   AND remote_asset_id IS ? AND remote_checked_at IS ?",
                    (
                        stamp.asset_id,
                        stamp.is_trashed,
                        checked_at,
                        now_iso(),
                        # **消滅の判定は呼び出し側で済んでいる。** `Stamp` に旗を
                        # 足さず、`asset_id` が無いことをそのまま条件にする。
                        checked_at if stamp.asset_id is None else None,
                        "remote_missing" if stamp.asset_id is None else None,
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
            # **契約は「`complete` だけを対象」。** 組もその内側なので、先に確かめる。
            still = self._conn.execute(
                "SELECT 1 FROM upload_record WHERE id = ? AND state = 'complete'",
                (record_id,),
            ).fetchone()
            if still is None:
                return
            # 資産 ID が変わるなら、スタックの結果を組ごと捨てる（上と同じ理由）。
            self._reopen_stack_of(record_id, new_asset_id=asset_id)
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

    # ------------------------------------------------------------------
    # スタック（§9.11）

    def unstacked_batch(
        self, destination_id: str, target_epoch: int, after_id: str, limit: int
    ) -> list[sqlite3.Row]:
        """スタック未評価の完了レコードを id の昇順で取る（keyset）.

        **`target_epoch` を必ず絞る。** 向き先を変えた宛先では旧 epoch の
        `complete` が監査履歴として残る（`_invalidate_old_epoch_locked` は
        `state <> 'complete'` だけを無効化する）ので、絞らないと**別ライブラリへ
        送った資産 ID を現行の資格情報で送る**。`records_for_recheck` が同じ理由で
        epoch を条件にしているのと同じ形にそろえる。

        **`LIMIT` を繰り返すだけでは足りない。** 相手が落ちていて未評価のまま
        残した行は次の周回でも条件を満たすので、同じ行を読み直して進まなくなる。

        述語の順序は `0015` の部分索引と一字一句そろえる。
        """
        return list(
            self._conn.execute(
                "SELECT * FROM upload_record"
                " WHERE destination_id = ? AND target_epoch = ? AND state = 'complete'"
                "   AND stack_state IS NULL AND invalidated_at IS NULL AND id > ?"
                " ORDER BY id LIMIT ?",
                (destination_id, target_epoch, after_id, limit),
            )
        )

    def stacked_groups(self, destination_id: str, target_epoch: int) -> dict[str, frozenset[str]]:
        """組んだと記録している組を、`remote_stack_id` → 資産 ID の集合で返す.

        再確認の照合が使う。**無効化された行は数えない。** `stacked` の行は
        `0015` と `0016` の trigger が `remote_stack_id` と `remote_asset_id` の
        実在を強制しているので、どちらも NULL にならない。

        **`state` は条件に入れない**（`0015` と同じ理由）。`stacked` は
        「その `remote_asset_id` を送った結果」であって、レコードの state とは
        独立で、再計算の差し戻しが `complete` → `needs_recheck` を動かしても真の
        ままである。`complete` に絞ると、片方が差し戻された組はこちらの集合が
        相手の集合の真部分集合になり、毎回「崩れている」と読む。

        **`target_epoch` を必ず絞る。** 旧 epoch は別ライブラリの履歴で、
        現行の資格情報で読んだスタック一覧とは突き合わせられない。
        """
        groups: dict[str, set[str]] = {}
        for row in self._conn.execute(
            "SELECT remote_stack_id, remote_asset_id FROM upload_record"
            " WHERE destination_id = ? AND target_epoch = ?"
            "   AND invalidated_at IS NULL AND stack_state = 'stacked'",
            (destination_id, target_epoch),
        ):
            groups.setdefault(row["remote_stack_id"], set()).add(row["remote_asset_id"])
        return {stack_id: frozenset(assets) for stack_id, assets in groups.items()}

    def reopen_stacks(
        self,
        ctx: JobContext,
        destination_id: str,
        target_epoch: int,
        stack_ids: Sequence[str],
    ) -> int:
        """崩れた組を未評価へ戻す. **戻した組の数**を返す.

        `_reopen_stack_of` と同じ形の CAS を、**1 つのトランザクション**で当てる
        （`assert_lease` も同じ取引に入れる）。**組ごとに数える** —— 組は互いに
        独立なので、片方が動いても他方の照合結果は古くならない。
        """
        reopened = 0
        with immediate(self._conn):
            ctx.assert_lease()
            for stack_id in stack_ids:
                updated = self._conn.execute(
                    "UPDATE upload_record SET stack_state = NULL, remote_stack_id = NULL,"
                    " stack_reason = NULL, updated_at = ?"
                    " WHERE destination_id = ? AND target_epoch = ? AND remote_stack_id = ?"
                    "   AND stack_state = 'stacked' AND invalidated_at IS NULL",
                    (now_iso(), destination_id, target_epoch, stack_id),
                )
                reopened += updated.rowcount > 0
        return reopened

    def sources_of(self, media_file_id: str) -> list[sqlite3.Row]:
        """公開の元になったカード上の観測（**すべて**）.

        **1 つに絞らない。** `observed_at` は再スキャンのたびに更新される
        （`scan.py` の `_touch`）ので、「最初の観測」を順序で選ぶと同じ組が実行の
        たびに変わりうる。**公開名では組を作れない**（衝突時に改名される。§6）。
        """
        return list(
            self._conn.execute(
                "SELECT volume_instance_id, rel_path FROM source_entry"
                " WHERE media_file_id = ? AND state = 'published'",
                (media_file_id,),
            )
        )

    def siblings_on_card(self, volume_instance_id: str, prefix: str) -> list[sqlite3.Row]:
        """同じカードで `<dir>/<stem>.` から始まる観測（UNIQUE 索引の範囲引き）."""
        return list(
            self._conn.execute(
                "SELECT rel_path, media_file_id, copresent_key FROM source_entry"
                " WHERE volume_instance_id = ? AND rel_path > ? AND rel_path < ?"
                "   AND media_file_id IS NOT NULL AND state = 'published'",
                (volume_instance_id, prefix, prefix + HIGH_SENTINEL),
            )
        )

    def record_for(
        self, destination_id: str, target_epoch: int, media_file_id: str
    ) -> sqlite3.Row | None:
        """**現行 epoch の有効なレコードだけ**を返す.

        旧 epoch は別ライブラリの履歴。**無効化された行も返さない** ——
        消滅を無効化して送り直すと、同じ組に行が 2 つ並ぶ。`ORDER BY` が
        無いので、除かないと古い方を返し、第 2 パスが「相方が無効化済み」と
        読んで永久に組めなくなる。
        """
        return self._conn.execute(
            "SELECT * FROM upload_record"
            " WHERE destination_id = ? AND target_epoch = ? AND media_file_id = ?"
            "   AND invalidated_at IS NULL",
            (destination_id, target_epoch, media_file_id),
        ).fetchone()

    def guard_stack_group(
        self,
        ctx: JobContext,
        members: Sequence[sqlite3.Row],
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
    ) -> None:
        """**外部へ触る直前に通す。** `uploader._guard` のスタック版.

        `complete` のレコードは claim を持たないので `prepare_side_effect` は
        流用できない。1 件でも合わなければ `StackGroupChanged` を送出して、その組を
        諦める（相手には触らない）。
        """
        with immediate(self._conn):
            self._assert_current(ctx, destination_id, target_epoch, profile_revision_id)
            for member in members:
                if self._member_moved(member, target_epoch):
                    raise StackGroupChanged(f"レコード {member['id']} が変わった")

    def mark_stacked(
        self,
        ctx: JobContext,
        members: Sequence[sqlite3.Row],
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
        remote_stack_id: str,
    ) -> None:
        """組の全員を 1 つのトランザクションで記録する.

        **全員に当たらなければ 1 行も書かない。** 一部だけ `stacked` になると、
        残りは別の組として再評価され、相手側に既にあるスタックを作り直そうとする。

        **見送り済みの相方は引き上げる。** 見送りは「今は組めない」の記録であって
        永久の拒否ではない（§9.11）。
        """
        with immediate(self._conn):
            # **guard と同じ現行値を見る。** 相手を待っている間に向き替えや
            # プロファイル編集が commit されうる。外部副作用は済んでいるので、
            # 落ちたら DB を書かずに次の送信の「既存スタックの回収」へ渡す。
            self._assert_current(ctx, destination_id, target_epoch, profile_revision_id)
            for member in members:
                updated = self._conn.execute(
                    "UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = ?,"
                    " stack_reason = NULL, updated_at = ?"
                    " WHERE id = ? AND target_epoch = ? AND state = 'complete'"
                    "   AND invalidated_at IS NULL AND remote_asset_id IS ?"
                    "   AND (stack_state IS NULL OR stack_state = 'skipped')",
                    (
                        remote_stack_id,
                        now_iso(),
                        member["id"],
                        target_epoch,
                        member["remote_asset_id"],
                    ),
                )
                if updated.rowcount != 1:
                    # `immediate` の外へ出して取引ごと巻き戻す。
                    raise StackGroupChanged(f"レコード {member['id']} を記録できない")

    def mark_skipped(
        self,
        ctx: JobContext,
        record: sqlite3.Row,
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
        reason: str,
    ) -> None:
        """見送りを記録する. **相手に触らない経路でも、記録の条件は同じにする。**

        規則が無効・観測が無い・相方が居ない、といった見送りは guard を通らない。
        だが**書く条件を緩めてはいけない** —— 規則を読んだ直後にプロファイルが
        編集されると、次の順序で**旧規則の判断が新しい版の世界へ残る**。

        1. こちらが旧版 R1 の規則で「見送り」と判断する
        2. 別の接続が R2 を発行し、既存の見送りを未評価へ戻して commit
           （この行はまだ未評価なので対象外）
        3. こちらが R1 の判断を書く → **R2 では二度と評価されない**

        リースも同じ理由で要る。見送りが大量にあると書いている間に切れうるし、
        `finish_claimed` は token と status しか見ないので、失効した後の書き込みが
        `succeeded` として残せてしまう。
        """
        with immediate(self._conn):
            self._assert_current(ctx, destination_id, target_epoch, profile_revision_id)
            updated = self._conn.execute(
                "UPDATE upload_record SET stack_state = 'skipped', stack_reason = ?,"
                " remote_stack_id = NULL, updated_at = ?"
                " WHERE id = ? AND target_epoch = ? AND state = 'complete'"
                "   AND invalidated_at IS NULL AND remote_asset_id IS ?"
                "   AND stack_state IS NULL",
                (reason, now_iso(), record["id"], target_epoch, record["remote_asset_id"]),
            )
            if updated.rowcount != 1:
                # **成功として数えない。** 数えると第 2 パスの集計が嘘になる。
                raise StackGroupChanged(f"レコード {record['id']} を記録できない")

    def _reopen_stack_of(self, record_id: str, new_asset_id: str | None = "") -> None:
        """その行が属する**スタックの全員**を未評価へ戻す（§9.11）.

        **組は 1 つの結果。** 片方の資産 ID が変わっただけでも、その
        `remote_stack_id` は現在の姿を表さなくなる。片方だけ戻すと、次に組み直す
        ときに `_member_moved` が相方の `stacked` を拒み、**以後ずっと組めない**
        （画面も古いスタックを現在の結果として出し続ける）。

        `new_asset_id` に既定の番兵を渡すと「変わる前提」で戻す。呼び出し側が
        新しい ID を渡した場合は、同じなら何もしない。

        **見送り（`skipped`）は組の結果ではない**ので触らない。
        """
        row = self._conn.execute(
            "SELECT destination_id, target_epoch, remote_asset_id, remote_stack_id"
            " FROM upload_record WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None or row["remote_stack_id"] is None:
            return
        if new_asset_id != "" and new_asset_id == row["remote_asset_id"]:
            return
        self._conn.execute(
            "UPDATE upload_record SET stack_state = NULL, remote_stack_id = NULL,"
            " stack_reason = NULL, updated_at = ?"
            " WHERE destination_id = ? AND target_epoch = ? AND remote_stack_id = ?"
            "   AND stack_state = 'stacked'",
            (now_iso(), row["destination_id"], row["target_epoch"], row["remote_stack_id"]),
        )

    def _assert_current(
        self,
        ctx: JobContext,
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
    ) -> None:
        """リース・宛先の現行 epoch・プロファイルの現行版をまとめて見る.

        **呼び出し側が開いた `BEGIN IMMEDIATE` の中で使う**（確認と書き込みの間に
        誰も割り込めない）。guard と記録の両方から同じものを見る —— 片方だけ
        弱くすると、そこが抜け道になる。

        **宛先の現行 epoch を見るのが要点。** epoch を進める編集は
        `state <> 'complete'` の行しか無効化しないので、`complete` を扱うこの経路
        だけが既存の停止境界から外れる。固定した旧リビジョンの preflight は、
        旧向き先が生きていれば成功してしまう。
        """
        ctx.assert_lease()
        # **`claim_next` と同じ安全条件を見る。** 第 2 パスは `complete` を扱うので
        # claim を通らない —— ここで見なければ、無効にした宛先や保管した宛先へ
        # `POST` / `PUT` を出す（`rename_or_toggle` と `archive` は epoch を進めない）。
        current = self._conn.execute(
            "SELECT r.target_epoch AS epoch FROM upload_destination d"
            " JOIN destination_revision r ON r.id = d.current_revision_id"
            " WHERE d.id = ? AND d.enabled = 1 AND d.archived_at IS NULL",
            (destination_id,),
        ).fetchone()
        if current is None or current["epoch"] != target_epoch:
            raise DestinationChanged("宛先が使えない（向き先が変わった・無効・保管済み）")
        profile = self._conn.execute(
            "SELECT 1 FROM device_profile"
            " WHERE id = (SELECT profile_id FROM profile_revision WHERE id = ?)"
            "   AND current_revision_id = ?",
            (profile_revision_id, profile_revision_id),
        ).fetchone()
        if profile is None:
            raise StackGroupChanged("プロファイルの版が変わった")

    def _member_moved(self, member: sqlite3.Row, target_epoch: int) -> bool:
        """組を決めたときの姿と違っていないか."""
        return (
            self._conn.execute(
                "SELECT 1 FROM upload_record WHERE id = ? AND target_epoch = ?"
                "   AND state = 'complete' AND invalidated_at IS NULL"
                "   AND remote_asset_id IS ? AND stack_state IS NOT 'stacked'",
                (member["id"], target_epoch, member["remote_asset_id"]),
            ).fetchone()
            is None
        )

    # ------------------------------------------------------------------

    def list_records(
        self,
        destination_id: str | None = None,
        state: str | None = None,
        limit: int = 200,
        stack_state: str | None = None,
    ) -> list[sqlite3.Row]:
        """`stack_state` は `stacked` / `skipped` / `unevaluated`.

        **未知の値を素通りさせない**（呼び出し側が 400 にする）。「絞ったつもりで
        全件が出る」を作らない。

        **無効になった記録は出さない。** 承認も却下も送り直しも 409 で断られる
        記録なので、出すと画面から消せないカードになる。件数（`/dashboard` の
        `awaiting_total` など）も無効を除いて数えるため、出すと画面ごとに数が
        食い違う。

        **ファイルの位置も返す。** 画面は内部の ID を出さない（§13）ので、
        どのファイルの話かを言うにはこれが要る。
        """
        # 無効の除外は絞り込みではなく前提なので、常に置く。
        clauses, params = ["r.invalidated_at IS NULL"], []
        if destination_id is not None:
            clauses.append("r.destination_id = ?")
            params.append(destination_id)
        if state is not None:
            clauses.append("r.state = ?")
            params.append(state)
        if stack_state is not None:
            if stack_state == "unevaluated":
                clauses.append("r.stack_state IS NULL")
            elif stack_state in ("stacked", "skipped"):
                clauses.append("r.stack_state = ?")
                params.append(stack_state)
            else:
                raise UploadRequestInvalid("絞り込みの指定が正しくない")
        where = " AND ".join(clauses)
        return list(
            self._conn.execute(
                # **行ごとに引き直させない。** 承認待ちの差分（`_datetime_diff`）が
                # 要る値も、ここで一緒に継いで返す（1 度に 200 件出す画面がある）。
                "SELECT r.*, m.rel_path AS rel_path,"  # noqa: S608
                "       m.profile_id AS media_profile_id,"
                "       m.captured_at AS media_captured_at,"
                # 画面が撮影日時と補正案に添える印の出所（`routes_media._media`
                # と同じ。空なら画面が `DEFAULT_TIMEZONE` とみなす）。
                "       m.captured_at_tz AS media_captured_at_tz"
                " FROM upload_record r"
                " JOIN media_file m ON m.id = r.media_file_id"
                f" WHERE {where} ORDER BY r.updated_at DESC LIMIT ?",
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
        if "remote_asset_id" in fields:
            # **資産 ID が変わるなら、その前にスタックの結果を組ごと捨てる**（§9.11）。
            # 再計算で `needs_recheck` へ戻った `stacked` の行を別の資産で送り直す
            # 経路がここを通る。`0016` の trigger が同じことを fail-closed で守る。
            self._reopen_stack_of(record_id, new_asset_id=fields["remote_asset_id"])
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
    except AttributeError, TypeError, ValueError:
        return False


def _expiry(seconds: int) -> str:
    return iso(utcnow() + timedelta(seconds=seconds))
