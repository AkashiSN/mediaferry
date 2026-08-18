"""パスワードのハッシュとセッション ID（§12 / §14）.

**平文を保存しない・比較を定数時間で行う・セッション ID を推測させない**の 3 つを
ここに閉じる。API とセッションストアの両方から呼ぶので、パラメータを変えるときに
触る場所を 1 つにしておく。
"""

from __future__ import annotations

from mediaferry.core.auth import (
    hash_password,
    new_session_id,
    session_fingerprint,
    verify_password,
)


def test_a_hash_does_not_contain_the_password():
    stored = hash_password("correct horse")
    assert "correct horse" not in stored
    assert stored.startswith("$argon2")


def test_the_same_password_hashes_differently_each_time():
    """塩が入っている（同じ値が並ぶと、DB を見ただけで同一と分かる）."""
    assert hash_password("correct horse") != hash_password("correct horse")


def test_verification_accepts_the_password_and_rejects_others():
    stored = hash_password("correct horse")
    assert verify_password(stored, "correct horse")
    assert not verify_password(stored, "correct horses")
    assert not verify_password(stored, "")


def test_a_broken_hash_is_refused_without_raising():
    """壊れた保存値で 500 にしない。**認証は落とさずに拒む。**"""
    for stored in ("not-a-hash", "", "$argon2id$broken"):
        assert not verify_password(stored, "correct horse")


def test_session_ids_are_unpredictable_and_unique():
    ids = {new_session_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(len(value) >= 32 for value in ids)


def test_the_stored_fingerprint_is_not_the_session_id():
    """**生の session id を DB に置かない。**

    置くと、DB のバックアップが漏れた時点で有効な Cookie を作れてしまう
    （転送先の `remote_user_id` を指紋で持つのと同じ理屈）。
    """
    session_id = new_session_id()
    fingerprint = session_fingerprint(session_id)
    assert fingerprint != session_id
    assert session_id not in fingerprint
    assert fingerprint == session_fingerprint(session_id)
    assert fingerprint != session_fingerprint(new_session_id())
