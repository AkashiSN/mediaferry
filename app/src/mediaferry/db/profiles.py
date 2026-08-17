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
    definition_to_json,
    load_builtin_definitions,
    parse_definition,
)
from ..ids import new_id
from .connection import immediate


class UnknownProfile(LookupError):
    pass


@dataclass(frozen=True)
class ProfileRef:
    profile_id: str
    revision_id: str
    revision: int
    definition: ProfileDefinition


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
            revision_id = new_id()
            self._conn.execute(
                "INSERT INTO profile_revision"
                " (id, profile_id, revision, definition_json, schema_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    revision_id,
                    profile_id,
                    revision,
                    definition_json,
                    PROFILE_SCHEMA_VERSION,
                    now_iso(),
                ),
            )
            self._conn.execute(
                "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
                (revision_id, profile_id),
            )
        return True

    def current(self, slug: str) -> ProfileRef:
        row = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            raise UnknownProfile(slug)
        return _to_ref(row)

    def by_id(self, profile_id: str) -> ProfileRef:
        row = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise UnknownProfile(profile_id)
        return _to_ref(row)

    def active(self) -> list[ProfileRef]:
        rows = self._conn.execute(
            "SELECT p.id AS profile_id, r.id AS revision_id, r.revision, r.definition_json"
            " FROM device_profile p JOIN profile_revision r ON r.id = p.current_revision_id"
            " WHERE p.archived_at IS NULL ORDER BY p.slug"
        )
        return [_to_ref(row) for row in rows]

    def definition_of(self, revision_id: str) -> ProfileDefinition:
        row = self._conn.execute(
            "SELECT definition_json FROM profile_revision WHERE id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise UnknownProfile(revision_id)
        return parse_definition(json.loads(row["definition_json"]))


def _to_ref(row: sqlite3.Row) -> ProfileRef:
    return ProfileRef(
        profile_id=row["profile_id"],
        revision_id=row["revision_id"],
        revision=row["revision"],
        definition=parse_definition(json.loads(row["definition_json"])),
    )
