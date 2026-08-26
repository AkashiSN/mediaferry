"""プロファイルとリビジョンの解決.

編集は既存定義を書き換えず新しいリビジョンを作る。取り込み・結合・アップロードの
各レコードが使用したリビジョン ID を持つので、後からプロファイルを変えても
過去データの解釈は変わらない。

ビルトインはアプリの更新で内容が変わりうる。起動時に定義を突き合わせ、
変わっていれば新リビジョンを作る。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..clock import now_iso
from ..core.profiles.model import (
    PROFILE_SCHEMA_VERSION,
    ProfileDefinition,
    ProfileInvalid,
    definition_to_json,
    load_builtin_definitions,
    parse_definition,
)
from ..ids import new_id
from .connection import immediate


class UnknownProfile(LookupError):
    pass


class ProfileIsBuiltin(RuntimeError):
    """ビルトインは編集できない（§6）.

    **`duplicate` 以外のすべての mutation で拒む。** 編集を許すと、次のアプリ
    更新で `sync_builtins` が黙って上書きする。archive も同じで、
    `sync_builtins` は `archived_at` を戻さないので、一度 archive すると
    再起動しても候補から消えたままになる —— 元に戻す手段が無くなる。
    """


class ProfileExists(RuntimeError):
    """その slug はもう使われている."""


class ProfileAlreadyArchived(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileRef:
    profile_id: str
    revision_id: str
    revision: int
    definition: ProfileDefinition
    builtin: bool = True
    archived: bool = False


class ProfileRegistry:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def sync_builtins(self) -> list[str]:
        """定義が変わったビルトインの slug を返す."""
        changed = []
        for defn in load_builtin_definitions():
            if self._upsert_revision(defn.slug, definition_to_json(defn), name=defn.name):
                changed.append(defn.slug)
        return changed

    def _upsert_revision(self, slug: str, definition_json: str, name: str | None = None) -> bool:
        """現行と違えば新リビジョンを作る. 作ったら True."""
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
                " FROM device_profile p"
                " LEFT JOIN profile_revision r ON r.id = p.current_revision_id"
                " WHERE p.slug = ?",
                (slug,),
            ).fetchone()
            if row is not None and row["definition_json"] == definition_json:
                return False

            profile_id = row["profile_id"] if row is not None else new_id()
            if row is None:
                self._conn.execute(
                    "INSERT INTO device_profile (id, slug, name, builtin, created_at)"
                    " VALUES (?, ?, ?, 1, ?)",
                    (profile_id, slug, name or slug, now_iso()),
                )
            revision = (row["revision"] or 0) + 1 if row is not None else 1
            self._publish_revision(
                profile_id,
                new_id(),
                revision,
                definition_json,
                row["definition_json"] if row is not None else None,
            )
        return True

    def current(self, slug: str) -> ProfileRef:
        row = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json,"
            " p.builtin, p.archived_at"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            raise UnknownProfile(slug)
        return _to_ref(row)

    def by_id(self, profile_id: str) -> ProfileRef:
        row = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json,"
            " p.builtin, p.archived_at"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise UnknownProfile(profile_id)
        return _to_ref(row)

    def active(self) -> list[ProfileRef]:
        rows = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json,"
            " p.builtin, p.archived_at"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.archived_at IS NULL ORDER BY p.slug"
        )
        return [_to_ref(row) for row in rows]

    def all(self) -> list[ProfileRef]:
        """archive 済みも含む（画面は区別して出す）."""
        rows = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json,"
            " p.builtin, p.archived_at"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " ORDER BY p.builtin DESC, p.slug"
        )
        return [_to_ref(row) for row in rows]

    # ------------------------------------------------------------------
    # 編集（Phase 5）

    def _assert_editable(self, slug: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT id, builtin, archived_at FROM device_profile WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None:
            raise UnknownProfile(slug)
        if row["builtin"]:
            raise ProfileIsBuiltin(slug)
        return row

    def create(self, defn: ProfileDefinition) -> ProfileRef:
        """ユーザ定義を新規に作る. `slug` は以後不変."""
        with immediate(self._conn):
            if self._conn.execute(
                "SELECT 1 FROM device_profile WHERE slug = ?", (defn.slug,)
            ).fetchone():
                raise ProfileExists(defn.slug)
            profile_id, revision_id = new_id(), new_id()
            self._conn.execute(
                "INSERT INTO device_profile (id, slug, name, builtin, created_at)"
                " VALUES (?, ?, ?, 0, ?)",
                (profile_id, defn.slug, defn.name, now_iso()),
            )
            self._publish_revision(profile_id, revision_id, 1, definition_to_json(defn), None)
        return self.current(defn.slug)

    def update(self, slug: str, defn: ProfileDefinition) -> ProfileRef:
        """新しいリビジョンを作る. **ビルトインは拒む。**"""
        with immediate(self._conn):
            row = self._assert_editable(slug)
            current = self._conn.execute(
                "SELECT r.revision FROM device_profile p"
                " JOIN profile_revision r ON r.id = p.current_revision_id WHERE p.id = ?",
                (row["id"],),
            ).fetchone()
            previous = self._conn.execute(
                "SELECT definition_json FROM profile_revision r"
                " JOIN device_profile p ON p.current_revision_id = r.id WHERE p.id = ?",
                (row["id"],),
            ).fetchone()
            self._publish_revision(
                row["id"],
                new_id(),
                current["revision"] + 1,
                definition_to_json(defn),
                previous["definition_json"],
            )
            self._conn.execute(
                "UPDATE device_profile SET name = ? WHERE id = ?", (defn.name, row["id"])
            )
        return self.current(slug)

    def duplicate(self, slug: str, new_slug: str, name: str) -> ProfileRef:
        """ビルトインからユーザ定義を作る. **元は変えない。**"""
        source = self.current(slug)
        from dataclasses import replace as _replace

        return self.create(_replace(source.definition, slug=new_slug, name=name))

    def archive(self, slug: str) -> None:
        """候補から外す. **削除ではない** —— 使用済みの版は参照が残る."""
        with immediate(self._conn):
            row = self._assert_editable(slug)
            if row["archived_at"] is not None:
                raise ProfileAlreadyArchived(slug)
            self._conn.execute(
                "UPDATE device_profile SET archived_at = ? WHERE id = ?", (now_iso(), row["id"])
            )

    def _publish_revision(
        self,
        profile_id: str,
        revision_id: str,
        revision: int,
        definition_json: str,
        previous_json: str | None,
    ) -> None:
        """新しいリビジョンを現行にする. **呼び出し側の取引の中で使う。**

        版を発行する経路は 2 つある（利用者の編集とビルトインの同期）ので、
        ここにまとめる。**分けると、片方だけ戻らない。**

        `stack` 節が変わったときは、そのプロファイルのメディアの**見送りを未評価へ
        戻す**（規則そのものが変わったので、前の判断は根拠を失っている）。
        **`stacked` は戻さない**（相手側に既にあるものを作り直さない）。

        **戻す範囲を `stack` の変化に限る。** 名前やタグだけの編集で全件を再評価
        すると、相手側の事情で見送った行まで問い合わせ直すことになる。
        """
        self._conn.execute(
            "INSERT INTO profile_revision"
            " (id, profile_id, revision, definition_json, schema_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (revision_id, profile_id, revision, definition_json, PROFILE_SCHEMA_VERSION, now_iso()),
        )
        self._conn.execute(
            "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
            (revision_id, profile_id),
        )
        if previous_json is None:
            # 新規作成。メディアがまだ無いので戻す対象も無い。
            return
        if _stack_rule_of(previous_json) != _stack_rule_of(definition_json):
            self._conn.execute(
                "UPDATE upload_record SET stack_state = NULL, stack_reason = NULL,"
                " updated_at = ?"
                " WHERE stack_state = 'skipped' AND media_file_id IN ("
                "     SELECT id FROM media_file WHERE profile_id = ?)",
                (now_iso(), profile_id),
            )

    def definition_of(self, revision_id: str) -> ProfileDefinition:
        row = self._conn.execute(
            "SELECT definition_json FROM profile_revision WHERE id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise UnknownProfile(revision_id)
        return parse_definition(json.loads(row["definition_json"]))


def _to_ref(row: sqlite3.Row) -> ProfileRef:
    keys = row.keys()
    return ProfileRef(
        profile_id=row["profile_id"],
        revision_id=row["revision_id"],
        revision=row["revision"],
        definition=parse_definition(json.loads(row["definition_json"])),
        builtin=bool(row["builtin"]) if "builtin" in keys else True,
        archived=(row["archived_at"] is not None) if "archived_at" in keys else False,
    )


# `_stack_rule_of` が「読めなかった」ことを表す番兵。**`StackRule` とは
# 絶対に等しくならない。** 旧形の定義は現行スキーマの語彙では規則を復元でき
# ないので、「読めない」＝「いまの規則と同じとは言えない」＝「変わった」に倒す。
_UNREADABLE_STACK_RULE = object()


def _stack_rule_of(definition_json: str) -> object:
    """定義から `stack` 節を**正規化して**取り出す.

    **生の dict で比べてはいけない。** Phase 6 より前のリビジョンの JSON には
    `stack` キーが無く（省略時は `STACK_DISABLED` として読む）、新しい JSON は
    正規形で `{"enabled": false, ...}` を持つ。生で比べると**規則が実質変わって
    いないのに全件を戻す**ことになる。

    渡される旧リビジョンの JSON は、現行スキーマでは読めない形（例:
    `timestamp.source` が配列でなく単一の文字列）を持つことがある。ここは
    `_publish_revision` が新旧を比べるために呼ぶ経路で、比べられずに
    `ProfileInvalid` を上へ通すと `sync_builtins` ごと lifespan で止まる。
    読めない定義は番兵を返し、「規則が変わった」側へ倒す
    （見送り記録の再評価に戻すだけなので、壊すものはない）。
    """
    try:
        return parse_definition(json.loads(definition_json)).stack
    except ProfileInvalid:
        return _UNREADABLE_STACK_RULE
