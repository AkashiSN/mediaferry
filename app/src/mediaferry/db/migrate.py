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

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_VERSION_RE = re.compile(r"^(\d{4})_")
# trigger 本体の `BEGIN ... END;` と区別する。トランザクションの BEGIN は
# 直後がセミコロン（またはモード指定 + セミコロン）で終わる。
_TRANSACTION_RE = re.compile(
    r"^\s*(BEGIN(\s+(IMMEDIATE|DEFERRED|EXCLUSIVE))?\s*;|COMMIT\s*;|ROLLBACK\s*;)",
    re.IGNORECASE | re.MULTILINE,
)

# 外部キーを外して走らせる版の目印。**先頭行だけを見る** —— 本文の途中に現れる
# 同じ文字列（コメントの引用など）で外れないようにする。
FK_OFF_MARKER = "-- mediaferry:foreign-keys-off"


class MigrationError(RuntimeError):
    pass


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """未適用の版を順に適用し、適用した版番号を返す."""
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
        if body.split("\n", 1)[0].strip() == FK_OFF_MARKER:
            _apply_with_foreign_keys_off(conn, version, path.name, body, checksum)
        else:
            _apply_one(conn, version, path.name, body, checksum)
        done.append(version)
    return done


def _record_statement(version: int, name: str, checksum: str) -> str:
    """版の記録を 1 文の SQL にする.

    `executescript` はプレースホルダを受け取らないので、版の記録もリテラルで
    組み立てる。version は int、checksum は hex、name はファイル名なので、
    いずれも SQL の構造を壊す文字を含まない。`_apply_one` と
    `_apply_with_foreign_keys_off` の両方がここを通ることで、記録の作り方が
    2 か所に分かれない。
    """
    return (
        "INSERT INTO schema_migration (version, name, checksum, applied_at)"  # noqa: S608
        f" VALUES ({version}, '{name}', '{checksum}', datetime('now'));"
    )


def _apply_one(conn: sqlite3.Connection, version: int, name: str, body: str, checksum: str) -> None:
    """DDL と版の記録を 1 つのトランザクションで適用する."""
    record = _record_statement(version, name, checksum)
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{body}\n{record}\nCOMMIT;")
    except Exception:
        # executescript の途中で失敗するとトランザクションが開いたまま残り、
        # 以後の SQL がその中で走ってしまう。
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _apply_with_foreign_keys_off(
    conn: sqlite3.Connection, version: int, name: str, body: str, checksum: str
) -> None:
    """外部キーを外して 1 本の版を適用する.

    **`PRAGMA foreign_keys` はトランザクションの中では黙って無視される**ので、
    移行ファイルの中からは外せない。`defer_foreign_keys` も `legacy_alter_table` も
    代わりにならない（前者は DROP の暗黙 DELETE で立った違反が COMMIT まで残り、
    後者はトランザクション内では効かず RENAME が子の参照先を書き換える）。

    外すことを許す代わりに、**`PRAGMA foreign_key_check` を COMMIT より前に
    確かめる**（SQLite 公式の 12 手順の 10 番目が 11 番目の COMMIT より前に
    ある順序と同じ）。ここで COMMIT してしまうと、DDL もデータ変更も版の記録も
    確定済みになり、壊れた参照を見つけても ROLLBACK で消せない。
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    record = _record_statement(version, name, checksum)
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{body}\n{record}")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            conn.execute("ROLLBACK")
            raise MigrationError(f"{name} の適用後に参照が壊れている（{len(broken)} 件）")
        conn.execute("COMMIT")
    except Exception:
        # 検査で見つけた ROLLBACK 済みの場合も含め、開いたままのトランザクションを
        # 残さない。
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _version_of(path: Path) -> int:
    match = _VERSION_RE.match(path.name)
    if match is None:
        raise MigrationError(f"{path.name} のファイル名が 4 桁の版番号で始まっていない")
    return int(match.group(1))
