"""作り直すための一括削除（§13 のリセット）.

**Phase 9 の削除の規則（`DELETE /media/{id}`）はここには掛からない。** あちらが
守っているのは「Immich にしか無いものを mediaferry から消させない」で、1 件ずつ
判断している場面の不変条件である。リセットは**mediaferry が持っているものを捨てる
操作**で、Immich にある資産は対象ではない —— 消しに行かないし、消えない
（`docs/history/phase11-design.md` の 6。利用者の裁定、2026-08-25）。

**段は積み上げ。** 深い段は浅い段を含む。別々に押させると、片付けたつもりで
中途半端な組み合わせが残る。

**送り先とカメラの種類は、どの段でも残す。** どちらも取り込んだデータではなく
設定で、消すと接続のやり直し（API キーの入れ直し）になる。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from .connection import immediate

#: 浅い順。**この並びが「積み上げ」の定義**で、画面もこの順で段を出す。
SCOPES = ("jobs", "uploads", "library", "all")


class ResetNotPossible(RuntimeError):
    """いま走っている作業があるので、足元を外せない."""


class UnknownScope(ValueError):
    """知らない段。**黙って浅い方へ倒さない**（消えない話になる）."""


def _index(scope: str) -> int:
    if scope not in SCOPES:
        raise UnknownScope(f"知らないリセットの段: {scope}")
    return SCOPES.index(scope)


def reset(conn: sqlite3.Connection, data_root: Path, scope: str) -> dict[str, int]:
    """段まで消して、消した行数を表で返す.

    **走っている作業がある間は何もしない。** `artifact_staging` が指している
    `source_entry` は `ON DELETE RESTRICT` で消せず、消せたとしても走っている
    取り込みが書き込み先を失う。
    """
    depth = _index(scope)
    removed: dict[str, int] = {}
    with immediate(conn):
        # **走っているものだけを見る。** `queued` はまだ誰も掴んでいないので、
        # 消しても書きかけのものは無い（claim は CAS なので、消えた行は拾えない）。
        # ここに `queued` を入れると、監視が積んだスキャンが常に居るカードでは
        # リセットが一度も通らない。
        live = conn.execute(
            "SELECT count(*) AS n FROM job WHERE status IN ('running', 'cancelling')"
        ).fetchone()["n"]
        if live:
            raise ResetNotPossible("走っている作業があるので、いまはリセットできない")

        # 1. 作業の記録。**作り直せる**（再スキャン・再検出）。
        removed["job_event"] = conn.execute("DELETE FROM job_event").rowcount
        removed["job"] = conn.execute("DELETE FROM job").rowcount

        if depth >= _index("uploads"):
            # 2. 送信の記録。**戻らない** —— 次に送ると初回の checking が reject を
            # 返して `origin` が `pre_existing` に決まり、`first_check_result` は
            # 不変なので `created_by_us` には二度と戻らない（§9.10）。
            removed["upload_record"] = conn.execute("DELETE FROM upload_record").rowcount

        if depth >= _index("library"):
            # 3. 取り込んだファイル。**カードに元があれば取り込み直せる。**
            #    参照している側から順に消す（外部キー）。
            removed["artifact_staging"] = conn.execute("DELETE FROM artifact_staging").rowcount
            removed["merge_member"] = conn.execute("DELETE FROM merge_member").rowcount
            removed["merge_group"] = conn.execute("DELETE FROM merge_group").rowcount
            removed["source_entry"] = conn.execute("DELETE FROM source_entry").rowcount
            removed["media_file"] = conn.execute("DELETE FROM media_file").rowcount

        if depth >= _index("all"):
            # 4. カードの記録。**信頼の記録もここで消える。**
            removed["volume_presence"] = conn.execute("DELETE FROM volume_presence").rowcount
            removed["volume_instance"] = conn.execute("DELETE FROM volume_instance").rowcount
            removed["source_device"] = conn.execute("DELETE FROM source_device").rowcount

    if depth >= _index("library"):
        _empty_trees(data_root)
    return removed


#: 消してよい木。**`DATA_ROOT` の直下のこの名前だけ**を対象にする。
TREES = ("library", "derived", "staging", "work", "cache")


def _empty_trees(data_root: Path) -> None:
    """取り込んだ実体を捨てる。**入れ物は残す.**

    **`DATA_ROOT` の下の決まった名前だけを触る。** 経路を組み立てず、`var/`
    （DB そのもの）には手を出さない。入れ物を消さないのは、次の取り込みが
    作り直す手間を増やさないため。
    """
    for name in TREES:
        tree = data_root / name
        if not tree.is_dir():
            continue
        for child in tree.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
