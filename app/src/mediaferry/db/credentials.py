"""転送先 API キーの保管（§12.3）.

暗号文の形式は `core/crypto.py` が持つ。ここは版の採番と、参照が絶えた
旧版の破棄だけを行う。

**復号できない資格情報を「壊れたもの」として上書きしない。** マスター鍵の
取り違えは `WrongKeyError` で区別できるので、行を残したまま「要再登録」として
画面へ出す。上書きすると、正しい鍵を思い出しても戻せない。
"""

from __future__ import annotations

import sqlite3

from ..clock import now_iso
from ..core.crypto import SecretAad, SecretBox, SecretCorrupt, WrongKeyError
from ..ids import new_id
from .connection import immediate

# AAD に入れるスキーマ版。migration で意味が変わったら上げる。
SCHEMA_VERSION = 4


class CredentialUnusable(RuntimeError):
    """復号できない、または既に破棄されている.

    **秘密そのものは絶対に含めない。** 画面にも API 応答にも出る。
    """


class CredentialStore:
    def __init__(self, conn: sqlite3.Connection, box: SecretBox) -> None:
        self._conn = conn
        self._box = box

    def store(self, destination_id: str, secret: str) -> str:
        """新しい版として保存し、その id を返す."""
        with immediate(self._conn):
            return self.store_locked(destination_id, secret)

    def store_locked(self, destination_id: str, secret: str) -> str:
        """**呼び出し側が開いたトランザクションの中で使う。**

        宛先の作成・編集は 1 トランザクションで反映する必要がある（§8）ので、
        リポジトリ側の `BEGIN IMMEDIATE` の中から呼べる形を用意する。
        docstring だけの約束にしない —— 単独で呼ばれると autocommit になり、
        孤立した credential を作れてしまう。
        """
        if not self._conn.in_transaction:
            raise RuntimeError("store_locked は呼び出し側のトランザクションの中で使う")
        credential_id = new_id()
        row = self._conn.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM destination_credential"
            " WHERE destination_id = ?",
            (destination_id,),
        ).fetchone()
        revision = row["revision"] + 1
        aad = SecretAad(
            credential_id=credential_id,
            destination_id=destination_id,
            revision=revision,
            schema_version=SCHEMA_VERSION,
        )
        self._conn.execute(
            "INSERT INTO destination_credential (id, destination_id, revision,"
            " secret_encrypted, key_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                credential_id,
                destination_id,
                revision,
                self._box.encrypt(secret, aad),
                self._box.key_id,
                now_iso(),
            ),
        )
        return credential_id

    def reveal(self, credential_id: str) -> str:
        """送信の直前にだけ呼ぶ. 戻り値をログにも DB にも書かない."""
        row = self._conn.execute(
            "SELECT * FROM destination_credential WHERE id = ?", (credential_id,)
        ).fetchone()
        if row is None:
            raise CredentialUnusable(f"資格情報 {credential_id} が無い")
        if row["secret_encrypted"] is None:
            raise CredentialUnusable(f"資格情報 {credential_id} は破棄済み。再登録が要る")
        aad = SecretAad(
            credential_id=row["id"],
            destination_id=row["destination_id"],
            revision=row["revision"],
            schema_version=SCHEMA_VERSION,
        )
        try:
            return self._box.decrypt(row["secret_encrypted"], aad)
        except WrongKeyError as exc:
            raise CredentialUnusable(
                f"資格情報 {credential_id} は別のマスター鍵で暗号化されている"
                f"（記録 {exc.found} / 現在 {exc.expected}）。鍵を戻すか再登録する"
            ) from exc
        except SecretCorrupt as exc:
            raise CredentialUnusable(f"資格情報 {credential_id} を復号できない") from exc

    def purge_unreferenced(self, destination_id: str) -> int:
        """どのリビジョンからも参照されていない版の暗号文を消す.

        版管理したまま旧 API キーを持ち続けると、ローテートしても漏洩面が
        減らない。監査のために `key_fingerprint` と作成時刻は残す。
        """
        with immediate(self._conn):
            purged = self._conn.execute(
                "UPDATE destination_credential SET secret_encrypted = NULL, purged_at = ?"
                " WHERE destination_id = ? AND secret_encrypted IS NOT NULL"
                "   AND id NOT IN (SELECT credential_id FROM destination_revision"
                "                  WHERE destination_id = ?)",
                (now_iso(), destination_id, destination_id),
            )
            return purged.rowcount
