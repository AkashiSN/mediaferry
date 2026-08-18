"""転送先の向き先を表す**指紋**（§12.3 / §14）.

`remote_user_id` は「同じ Immich の同じ利用者を指し続けているか」を確かめる
guard であって、相手を同定する値ではない（Phase 0 の実測）。用途が等値比較
だけなので、観測した値そのものではなくハッシュを保存する。

**生の観測値を持ち回らない理由。** `GET /api/users/me` の応答は相手が決める。
侵害された転送先は、受け取った `x-api-key` をそのまま `id` として返せる。生の値を
保存すると、`SecretBox` で暗号化したはずの鍵の平文の複製が DB の列・API 応答・
例外の文言・ログに現れる。ハッシュにすれば guard の意味を保ったまま、その経路が
まとめて閉じる。
"""

from __future__ import annotations

import hashlib

# **指紋であることを値自身に持たせる。** 形（64 文字の 16 進）で推定すると、
# 同じ形の生の観測値と見分けが付かない。移行が「もう指紋だ」と誤認した値は
# 変換されずに残り、相手が鍵を echo していた場合はその平文が居座る。
FINGERPRINT_PREFIX = "sha256:"


def fingerprint(observed: str | None) -> str | None:
    """観測した識別子を指紋にする. 観測できていなければ None のまま."""
    if observed is None:
        return None
    return FINGERPRINT_PREFIX + hashlib.sha256(observed.encode()).hexdigest()
