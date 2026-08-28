"""ジョブの永続化と所有権.

SQLite に行ロックは無いので、所有権は `BEGIN IMMEDIATE` の中の条件付き
UPDATE（CAS）で取る。更新できた 1 ワーカーだけが実行者になる。

実行中のジョブはリースを持ち、heartbeat で延長する。ファイルを公開する直前に
リースの有効性を確認するので、失効したジョブが後から書き込むことはない。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ..clock import iso, utcnow
from ..ids import new_id
from .connection import immediate

LEASE_SECONDS = 60

ACTIVE = ("running", "cancelling")


class LeaseLost(RuntimeError):
    """自分のトークンではその行を操作できない."""


@dataclass
class JobContext:
    job_id: str
    lease_token: str
    params: dict[str, Any]
    _store: JobStore = field(repr=False)

    def cancelled(self) -> bool:
        row = self._store.get(self.job_id)
        return row is None or row["status"] != "running"

    def heartbeat(self, progress: dict[str, Any] | None = None) -> None:
        """リースを延ばす. **進捗があれば同じ UPDATE に乗せる**（書き込みを増やさない）.

        `progress` を渡さない心拍は、前の値をそのまま残す。脈動は進捗を持たない
        場所（`fsync` や ffprobe の待ち）からも打たれるので、消すと表示が点滅する。
        """
        self._store.extend_lease(self.job_id, self.lease_token, progress)

    def assert_lease(self) -> None:
        """外部への副作用の直前に呼ぶ. 延長はしない."""
        self._store.assert_lease(self.job_id, self.lease_token)

    def emit(self, level: str, message: str, data: dict[str, Any] | None = None) -> None:
        self._store.emit(self.job_id, level, message, data)


class JobStore:
    def __init__(self, conn: sqlite3.Connection, lease_seconds: int = LEASE_SECONDS) -> None:
        self._conn = conn
        self._lease_seconds = lease_seconds

    def enqueue(self, job_type: str, params: dict[str, Any]) -> str:
        """params に秘密を入れない（画面と SSE に出る）."""
        job_id = new_id()
        self._conn.execute(
            "INSERT INTO job (id, type, status, params_json, created_at)"
            " VALUES (?, ?, 'queued', ?, ?)",
            (job_id, job_type, json.dumps(params, ensure_ascii=False), iso(utcnow())),
        )
        return job_id

    def claim_next(self) -> JobContext | None:
        token = new_id()
        now = utcnow()
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT id, params_json FROM job"
                " WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = self._conn.execute(
                "UPDATE job SET status = 'running', lease_token = ?, lease_expires_at = ?,"
                " started_at = COALESCE(started_at, ?)"
                " WHERE id = ? AND status = 'queued'",
                (token, self._expiry(), iso(now), row["id"]),
            )
            if updated.rowcount != 1:
                return None
        return JobContext(
            job_id=row["id"],
            lease_token=token,
            params=json.loads(row["params_json"]),
            _store=self,
        )

    def assert_lease(self, job_id: str, token: str) -> None:
        """自分がまだ実行者かを確かめる. 延長はしない.

        `cancelling` を通さないのが要点。通すと「キャンセル済みと表示した後に
        公開される」経路が開く。期限切れも通さない（延長で復活させない）。

        SELECT だけなので、呼び出し側の `BEGIN IMMEDIATE` の中で使える。
        書き込みロックを取った状態で確認してから同じトランザクションで
        状態を進めれば、確認と遷移の間にキャンセルが割り込めない。
        """
        row = self._conn.execute(
            "SELECT 1 FROM job WHERE id = ? AND lease_token = ? AND status = 'running'"
            " AND lease_expires_at > ?",
            (job_id, token, iso(utcnow())),
        ).fetchone()
        if row is None:
            raise LeaseLost(f"ジョブ {job_id} のリースが無効（キャンセル・失効・別の所有者）")

    def extend_lease(self, job_id: str, token: str, progress: dict[str, Any] | None = None) -> None:
        """heartbeat. 期限切れのリースは復活させない.

        **進捗は同じ UPDATE に乗せる。** 別の書き込みにすると、長い処理の間ずっと
        DB への書き込みが 2 倍になる。渡されなければ列に触らない。
        """
        if progress is None:
            updated = self._conn.execute(
                "UPDATE job SET lease_expires_at = ? WHERE id = ? AND lease_token = ?"
                " AND status IN ('running', 'cancelling') AND lease_expires_at > ?",
                (self._expiry(), job_id, token, iso(utcnow())),
            )
        else:
            updated = self._conn.execute(
                "UPDATE job SET lease_expires_at = ?, progress_json = ?"
                " WHERE id = ? AND lease_token = ?"
                " AND status IN ('running', 'cancelling') AND lease_expires_at > ?",
                (
                    self._expiry(),
                    json.dumps(progress, ensure_ascii=False),
                    job_id,
                    token,
                    iso(utcnow()),
                ),
            )
        if updated.rowcount != 1:
            raise LeaseLost(f"ジョブ {job_id} のリースを失っている")

    def finish(self, job_id: str, token: str, status: str, error: str | None = None) -> None:
        """終わったジョブの「いま何をしているか」は無いので、進捗は落とす."""
        updated = self._conn.execute(
            "UPDATE job SET status = ?, error = ?, finished_at = ?, progress_json = NULL,"
            " lease_token = NULL, lease_expires_at = NULL"
            " WHERE id = ? AND lease_token = ? AND status IN ('running', 'cancelling')",
            (status, error, iso(utcnow()), job_id, token),
        )
        if updated.rowcount != 1:
            raise LeaseLost(f"ジョブ {job_id} のリースを失っている")

    def finish_claimed(self, job_id: str, token: str) -> str:
        """正常終了の決着を 1 文で付ける.

        「status を読む → finish する」を分けると、その間に入った cancel が
        succeeded で上書きされる。cancel API は成功を返したのにジョブは
        succeeded、という食い違いになる。
        """
        row = self._conn.execute(
            "UPDATE job SET"
            " status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'succeeded' END,"
            # 終わったジョブの「いま何をしているか」は無い。**正常終了はこちらを
            # 通る**ので、`finish` だけ落としても画面には残り続ける。
            " finished_at = ?, lease_token = NULL, lease_expires_at = NULL, progress_json = NULL"
            " WHERE id = ? AND lease_token = ? AND status IN ('running', 'cancelling')"
            " RETURNING status",
            (iso(utcnow()), job_id, token),
        ).fetchone()
        if row is None:
            raise LeaseLost(f"ジョブ {job_id} のリースを失っている")
        return row["status"]

    def request_cancel(self, job_id: str) -> bool:
        """queued は即 cancelled、running だけ cancelling にする.

        queued を cancelling にすると、claim_next は queued しか取らないので
        誰も終わらせられず、画面に永遠に「キャンセル中」が残る。
        """
        with immediate(self._conn):
            done = self._conn.execute(
                "UPDATE job SET status = 'cancelled', finished_at = ?"
                " WHERE id = ? AND status = 'queued'",
                (iso(utcnow()), job_id),
            )
            if done.rowcount == 1:
                return True
            updated = self._conn.execute(
                "UPDATE job SET status = 'cancelling' WHERE id = ? AND status = 'running'",
                (job_id,),
            )
            return updated.rowcount == 1

    def sweep_interrupted(self) -> int:
        """起動時に、前回落ちたまま running だったジョブを倒す."""
        updated = self._conn.execute(
            "UPDATE job SET status = 'interrupted', finished_at = ?,"
            " lease_token = NULL, lease_expires_at = NULL"
            " WHERE status IN ('running', 'cancelling')",
            (iso(utcnow()),),
        )
        return updated.rowcount

    def reap_expired_leases(self) -> int:
        updated = self._conn.execute(
            "UPDATE job SET status = 'interrupted', finished_at = ?,"
            " lease_token = NULL, lease_expires_at = NULL"
            " WHERE status IN ('running', 'cancelling') AND lease_expires_at < ?",
            (iso(utcnow()), iso(utcnow())),
        )
        return updated.rowcount

    def emit(self, job_id: str, level: str, message: str, data: dict | None = None) -> None:
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM job_event WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._conn.execute(
                "INSERT INTO job_event (job_id, seq, level, message, data_json, at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    row["seq"] + 1,
                    level,
                    message,
                    None if data is None else json.dumps(data, ensure_ascii=False),
                    iso(utcnow()),
                ),
            )

    def get(self, job_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()

    def list_jobs(self, limit: int = 50) -> list[sqlite3.Row]:
        """一覧。**最後の 1 文を添えて返す**（`docs/history/phase11-design.md` の N4）.

        画面は進捗の知らせで受けた分しか要約を持てないので、**開く前に終わった
        作業は「完了」としか出せなかった**。`スキャン完了: … / 消えた 2 件` の
        ような 1 文は、なぜ件数が変わったのかを後から追う唯一の手がかりなので、
        一覧が持って返す。**1 件ずつ引かない**（相関副問い合わせ 1 本で足りる）。
        """
        return list(
            self._conn.execute(
                "SELECT j.*,"
                " (SELECT e.message FROM job_event e WHERE e.job_id = j.id"
                "  ORDER BY e.seq DESC LIMIT 1) AS last_message"
                " FROM job j ORDER BY j.created_at DESC LIMIT ?",
                (limit,),
            )
        )

    def events(self, job_id: str, after_seq: int = 0) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM job_event WHERE job_id = ? AND seq > ? ORDER BY seq",
                (job_id, after_seq),
            )
        )

    def _expiry(self) -> str:
        return iso(utcnow() + timedelta(seconds=self._lease_seconds))
