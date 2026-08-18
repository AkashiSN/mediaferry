"""ログインセッションの保存と失効（§12 / §14）.

**生の Cookie 値は保存しない。** 指紋（SHA-256）で突き合わせる。
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from ..clock import iso, now_iso, utcnow
from ..core.auth import hash_password, new_session_id, session_fingerprint, verify_password
from .connection import immediate

# セッションの有効期間。画面を開いたまま寝かせても翌朝使える長さにする。
SESSION_TTL_SECONDS = 14 * 24 * 3600
# 延長は最後に見てからこの秒数が経ってから。**毎リクエストで書かない**
# （画面は数秒おきに API を叩くので、そのたびに書くと WAL が膨らむ）。
RENEW_AFTER_SECONDS = 3600


class SessionStore:
    def __init__(self, conn: sqlite3.Connection, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self._conn = conn
        self._ttl = ttl_seconds

    def create(self) -> tuple[str, str]:
        """セッションを作り、`(session_id, expires_at)` を返す."""
        session_id = new_session_id()
        expires_at = iso(utcnow() + timedelta(seconds=self._ttl))
        now = now_iso()
        with immediate(self._conn):
            self._conn.execute(
                "INSERT INTO auth_session (fingerprint, created_at, expires_at, last_seen_at)"
                " VALUES (?, ?, ?, ?)",
                (session_fingerprint(session_id), now, expires_at, now),
            )
        return session_id, expires_at

    def verify(self, session_id: str) -> bool:
        """有効なら真。ついでに、十分に時間が経っていれば期限を延ばす."""
        now = now_iso()
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT * FROM auth_session WHERE fingerprint = ? AND expires_at > ?",
                (session_fingerprint(session_id), now),
            ).fetchone()
            if row is None:
                return False
            if _seconds_since(row["last_seen_at"]) < RENEW_AFTER_SECONDS:
                return True
            self._conn.execute(
                "UPDATE auth_session SET last_seen_at = ?, expires_at = ? WHERE fingerprint = ?",
                (now, iso(utcnow() + timedelta(seconds=self._ttl)), row["fingerprint"]),
            )
        return True

    def revoke(self, session_id: str) -> None:
        with immediate(self._conn):
            self._conn.execute(
                "DELETE FROM auth_session WHERE fingerprint = ?", (session_fingerprint(session_id),)
            )

    def revoke_all(self) -> None:
        with immediate(self._conn):
            self._conn.execute("DELETE FROM auth_session")

    def purge_expired(self) -> int:
        with immediate(self._conn):
            deleted = self._conn.execute(
                "DELETE FROM auth_session WHERE expires_at <= ?", (now_iso(),)
            )
        return deleted.rowcount


def revoke_sessions_if_password_changed(conn: sqlite3.Connection, plain: str | None) -> bool:
    """パスワードが前回起動時と違えば、全セッションを失効させて真を返す.

    **起動のたびにハッシュし直して比べることはできない。** Argon2 の salt は毎回
    変わるので、同じ平文でもハッシュは一致せず、再起動のたびに全員がログアウト
    させられる。保存済みのハッシュに現在の平文を `verify` して世代を判定する。

    パスワードを変える理由はたいてい「漏れたから」なので、**変わったら既存の
    Cookie を残さない**。認証を切った場合（`plain is None`）も同じ。
    """
    with immediate(conn):
        row = conn.execute("SELECT hash FROM auth_password WHERE id = 1").fetchone()
        if plain is None:
            if row is None:
                return False
            conn.execute("DELETE FROM auth_password")
            conn.execute("DELETE FROM auth_session")
            return True
        if row is not None and verify_password(row["hash"], plain):
            return False
        changed = row is not None
        conn.execute(
            "INSERT INTO auth_password (id, hash, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT (id) DO UPDATE"
            " SET hash = excluded.hash, updated_at = excluded.updated_at",
            (hash_password(plain), now_iso()),
        )
        conn.execute("DELETE FROM auth_session")
    return changed


def _seconds_since(when: str) -> float:
    from datetime import datetime

    return (utcnow() - datetime.fromisoformat(when)).total_seconds()
