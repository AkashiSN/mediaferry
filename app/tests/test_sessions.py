"""ログインセッションの保存と失効（§12 / §14）.

Cookie の値は **DB に生のままでは無い**。指紋で突き合わせ、期限が来れば拒む。
パスワードが変わったら全部失効させる —— パスワードを変える理由はたいてい
「漏れたから」なので、既存の Cookie が生き残ってはいけない。
"""

from __future__ import annotations

from mediaferry.core.auth import hash_password, session_fingerprint
from mediaferry.db.sessions import (
    RENEW_AFTER_SECONDS,
    SessionStore,
    revoke_sessions_if_password_changed,
)


def _rows(db):
    return list(db.execute("SELECT * FROM auth_session"))


def test_a_created_session_verifies(db):
    store = SessionStore(db)
    session_id, _ = store.create()
    assert store.verify(session_id)
    assert not store.verify("someone-elses-id")


def test_the_raw_session_id_is_not_stored(db):
    store = SessionStore(db)
    session_id, _ = store.create()
    stored = _rows(db)
    assert [row["fingerprint"] for row in stored] == [session_fingerprint(session_id)]
    assert all(session_id not in str(tuple(row)) for row in stored)


def test_an_expired_session_is_refused(db):
    store = SessionStore(db)
    session_id, _ = store.create()
    db.execute("UPDATE auth_session SET expires_at = '2020-01-01T00:00:00+00:00'")
    assert not store.verify(session_id)


def test_a_session_in_use_is_extended_but_not_on_every_request(db):
    """**毎リクエストで書かない。** 画面は数秒おきに API を叩く."""
    store = SessionStore(db)
    session_id, _ = store.create()
    before = db.execute("SELECT expires_at, last_seen_at FROM auth_session").fetchone()

    assert store.verify(session_id)
    unchanged = db.execute("SELECT expires_at, last_seen_at FROM auth_session").fetchone()
    assert tuple(unchanged) == tuple(before)

    # 最後に見てから十分に経つと延びる（開いたまま寝かせても翌朝使える）。
    db.execute(
        "UPDATE auth_session SET last_seen_at = ?",
        ("2026-01-01T00:00:00+00:00",),
    )
    assert store.verify(session_id)
    after = db.execute("SELECT expires_at, last_seen_at FROM auth_session").fetchone()
    assert after["expires_at"] > before["expires_at"]
    assert RENEW_AFTER_SECONDS > 0


def test_revoking_removes_only_that_session(db):
    store = SessionStore(db)
    first, _ = store.create()
    second, _ = store.create()
    store.revoke(first)
    assert not store.verify(first)
    assert store.verify(second)


def test_revoking_all_removes_every_session(db):
    store = SessionStore(db)
    ids = [store.create()[0] for _ in range(3)]
    store.revoke_all()
    assert not any(store.verify(value) for value in ids)


def test_purging_removes_only_expired_sessions(db):
    store = SessionStore(db)
    alive, _ = store.create()
    dead, _ = store.create()
    db.execute(
        "UPDATE auth_session SET expires_at = '2020-01-01T00:00:00+00:00' WHERE fingerprint = ?",
        (session_fingerprint(dead),),
    )
    assert store.purge_expired() == 1
    assert store.verify(alive)


def test_changing_the_password_revokes_every_session(db):
    """パスワードを変える理由はたいてい「漏れたから」."""
    store = SessionStore(db)
    revoke_sessions_if_password_changed(db, "first-password")
    session_id, _ = store.create()

    # 同じパスワードでは失効しない（再起動のたびにログアウトさせない）。
    assert revoke_sessions_if_password_changed(db, "first-password") is False
    assert store.verify(session_id)

    assert revoke_sessions_if_password_changed(db, "second-password") is True
    assert not store.verify(session_id)


def test_turning_authentication_off_revokes_every_session(db):
    """認証を切ったなら、開いたままの画面も切る."""
    store = SessionStore(db)
    revoke_sessions_if_password_changed(db, "a-password")
    session_id, _ = store.create()

    assert revoke_sessions_if_password_changed(db, None) is True
    assert not store.verify(session_id)


def test_the_password_is_not_stored_in_plain_text(db):
    revoke_sessions_if_password_changed(db, "correct horse")
    stored = db.execute("SELECT * FROM auth_password").fetchall()
    assert stored
    assert all("correct horse" not in str(tuple(row)) for row in stored)
    assert all(str(tuple(row)).find("$argon2") >= 0 for row in stored)
    # 保存しているのは Argon2 のハッシュなので、突き合わせはそちらで行える。
    assert hash_password("correct horse") != stored[0]["hash"]
