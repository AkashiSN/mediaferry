"""転送先 API キーの保存形式.

Immich API は可逆な値を要求するのでハッシュ化できない。マスター鍵による
AEAD 暗号化で保存する。マスター鍵は環境変数にあり DATA_ROOT の外なので、
DB やバックアップ単体の流出には効く。app の RCE には効かない（§12.3）。

形式を仕様で固定してあるのは、後から変えると全 credential の migration が
要るため。ヘッダは自己記述で、アルゴリズムと鍵の指紋を含む。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"mfk"
FORMAT_VERSION = 1
ALG_AES_256_GCM = 1
NONCE_BYTES = 12
KEY_BYTES = 32
KEY_ID_CHARS = 16


class WrongKeyError(RuntimeError):
    """暗号文が別のマスター鍵で作られている.

    復号の失敗と区別する。区別しないと、鍵を取り違えた状態で
    「壊れた credential」として上書きしてしまう。
    """

    def __init__(self, expected: str, found: str) -> None:
        super().__init__(f"key_id が一致しない（期待 {expected} / 実際 {found}）")
        self.expected = expected
        self.found = found


class SecretCorrupt(RuntimeError):
    """形式が壊れているか、AAD が一致しない."""


@dataclass(frozen=True)
class SecretAad:
    """暗号文に束縛する文脈.

    行を別の宛先・別の版へ差し替える攻撃を復号時に検出する。
    """

    credential_id: str
    destination_id: str
    revision: int
    schema_version: int

    def to_bytes(self) -> bytes:
        payload = {"v": FORMAT_VERSION, **asdict(self)}
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SecretBox:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != KEY_BYTES:
            raise ValueError(f"マスター鍵は {KEY_BYTES} バイトである必要がある")
        self._aead = AESGCM(master_key)
        self.key_id = hashlib.sha256(b"mediaferry-key-id" + master_key).hexdigest()[:KEY_ID_CHARS]

    def encrypt(self, plaintext: str, aad: SecretAad) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        body = self._aead.encrypt(nonce, plaintext.encode("utf-8"), aad.to_bytes())
        return self._header() + nonce + body

    def decrypt(self, blob: bytes, aad: SecretAad) -> str:
        header = self._header()
        if not blob.startswith(MAGIC) or len(blob) < len(header) + NONCE_BYTES + 16:
            raise SecretCorrupt("暗号文のヘッダが読めない")
        found_key_id = blob[len(MAGIC) + 2 : len(header)].decode("ascii", errors="replace")
        if found_key_id != self.key_id:
            raise WrongKeyError(expected=self.key_id, found=found_key_id)
        if blob[len(MAGIC)] != FORMAT_VERSION or blob[len(MAGIC) + 1] != ALG_AES_256_GCM:
            raise SecretCorrupt("未知の形式またはアルゴリズム")
        nonce = blob[len(header) : len(header) + NONCE_BYTES]
        body = blob[len(header) + NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, body, aad.to_bytes()).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise SecretCorrupt("復号できない（AAD 不一致または改竄）") from exc

    def _header(self) -> bytes:
        return MAGIC + bytes([FORMAT_VERSION, ALG_AES_256_GCM]) + self.key_id.encode("ascii")
