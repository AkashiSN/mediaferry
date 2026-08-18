"""マイグレーションの適用.

**トランザクションは runner が所有する。** SQL ファイルは DDL だけを書き、
`BEGIN` / `COMMIT` も版の記録も書かない。ファイル側に任せると、記録の
INSERT を書き忘れた版が DDL だけ commit された状態で失敗し、次回起動で
再適用されて「table already exists」になる。

`executescript` は保留中のトランザクションを暗黙に COMMIT するので、Python の
`BEGIN` で囲むことはできない。代わりに、トランザクションを含む 1 本のスクリプトを
runner が組み立てて渡す。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from ..core.destinations.identity import fingerprint

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_VERSION_RE = re.compile(r"^(\d{4})_")
# trigger 本体の `BEGIN ... END;` と区別する。トランザクションの BEGIN は
# 直後がセミコロン（またはモード指定 + セミコロン）で終わる。
_TRANSACTION_RE = re.compile(
    r"^\s*(BEGIN(\s+(IMMEDIATE|DEFERRED|EXCLUSIVE))?\s*;|COMMIT\s*;|ROLLBACK\s*;)",
    re.IGNORECASE | re.MULTILINE,
)


class MigrationError(RuntimeError):
    pass


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """未適用の版を順に適用し、適用した版番号を返す."""
    # **SQLite に SHA-256 が無い。** データを作り替える版のために、こちらから
    # 関数を渡す（`0005`）。接続に紐づくので、他の経路には漏れない。
    conn.create_function("mediaferry_fingerprint", 1, fingerprint, deterministic=True)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " checksum TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    applied = {row["version"]: row for row in conn.execute("SELECT * FROM schema_migration")}
    done: list[int] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = _version_of(path)
        body = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()

        if version in applied:
            if applied[version]["checksum"] != checksum:
                # 適用済みの版を書き換えると、開発機と本番でスキーマが食い違う。
                raise MigrationError(
                    f"{path.name} は適用済みだが内容が変わっている。"
                    "新しい版のファイルを足すこと（開発中の DB なら作り直す）"
                )
            continue

        if _TRANSACTION_RE.search(body):
            raise MigrationError(
                f"{path.name} が BEGIN / COMMIT を含んでいる。トランザクションは runner が所有する"
            )
        _apply_one(conn, version, path.name, body, checksum)
        done.append(version)
    return done


def _apply_one(conn: sqlite3.Connection, version: int, name: str, body: str, checksum: str) -> None:
    """DDL と版の記録を 1 つのトランザクションで適用する.

    `executescript` はプレースホルダを受け取らないので、版の記録もリテラルで
    組み立てる。version は int、checksum は hex、name はファイル名なので、
    いずれも SQL の構造を壊す文字を含まない。
    """
    record = (
        # executescript にプレースホルダは渡せないのでリテラルで組み立てる。
        "INSERT INTO schema_migration (version, name, checksum, applied_at)"  # noqa: S608
        f" VALUES ({version}, '{name}', '{checksum}', datetime('now'));"
    )
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{body}\n{record}\nCOMMIT;")
    except Exception:
        # executescript の途中で失敗するとトランザクションが開いたまま残り、
        # 以後の SQL がその中で走ってしまう。
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _version_of(path: Path) -> int:
    match = _VERSION_RE.match(path.name)
    if match is None:
        raise MigrationError(f"{path.name} のファイル名が 4 桁の版番号で始まっていない")
    return int(match.group(1))
