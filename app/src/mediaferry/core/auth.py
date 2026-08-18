"""パスワードのハッシュとセッション ID（§12 / §14）.

`AUTH_PASSWORD` は環境変数にしか無く（`settings.py` の `Tier.BOOTSTRAP`）、
平文を DB へ置く経路は作らない。起動時にここでハッシュし、メモリ上のハッシュだけを
突き合わせに使う。

**セッション ID は生値を保存しない。** 保存するのは指紋（SHA-256）で、Cookie の値を
毎回ハッシュして突き合わせる。DB のバックアップが漏れても、そこから有効な Cookie を
組み立てられない（転送先の `remote_user_id` を指紋で持つのと同じ理屈。§12.3）。
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

# セッション ID の長さ（バイト）。base64url なので文字数はこれより長くなる。
SESSION_ID_BYTES = 32

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Argon2 でハッシュする. 塩は毎回変わる."""
    return _hasher.hash(plain)


def verify_password(stored: str, plain: str) -> bool:
    """突き合わせる. **壊れた保存値でも例外を出さずに拒む。**

    ここで送出すると、保存値が壊れているだけで API が 500 を返し、認証が
    「落ちている」のか「拒んでいる」のかを外から見分けられなくなる。
    """
    try:
        return _hasher.verify(stored, plain)
    except (Argon2Error, ValueError, TypeError):
        return False


def new_session_id() -> str:
    """推測できないセッション ID を作る."""
    return secrets.token_urlsafe(SESSION_ID_BYTES)


def session_fingerprint(session_id: str) -> str:
    """保存・照合に使う指紋. **生の ID は DB に置かない。**"""
    return hashlib.sha256(session_id.encode()).hexdigest()
