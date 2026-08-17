"""ジョブ 1 件ぶんの世界を組み立てる.

ジョブごとに DB 接続を開き、JobStore と ArtifactPublisher の両方をそれに
束ねる。手順 7（§9.3）でリースの確認と staged への遷移を 1 つの
BEGIN IMMEDIATE に入れる必要があり、別接続だと同じトランザクションに
できない。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from ..adapters.ffmpeg import MergeCancelled, MergeRunner
from ..adapters.ffprobe import MediaProbe
from ..adapters.publisher import ArtifactPublisher, PublishCancelled
from ..db.connection import Database
from ..db.jobs import JobContext, JobStore
from ..db.merges import MergeRepository
from ..db.profiles import ProfileRef, ProfileRegistry
from ..jobs.detect_groups import GroupDetector
from ..jobs.importer import Importer
from ..jobs.merger import Merger
from ..jobs.scan import Scanner
from ..jobs.volumes import VolumeSelection, VolumeService
from ..settings import SettingsService


class JobWorld:
    def __init__(self, database: Database, env: Mapping[str, str], volumes: VolumeService) -> None:
        self._database = database
        self._env = env
        self._volumes = volumes

    def store(self, conn: sqlite3.Connection) -> JobStore:
        return JobStore(conn)

    def connect(self) -> sqlite3.Connection:
        return self._database.connect()

    def run_scan(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        selection = VolumeSelection.from_params(ctx.params)
        profile = _fixed_profile(conn, selection)
        handle = self._volumes.open(selection)
        try:
            outcome = Scanner(conn).scan(ctx, handle.dirfd, selection.volume_instance_id, profile)
        finally:
            self._volumes.release(selection)
        ctx.emit(
            "info",
            f"スキャン完了: 新規 {outcome.new} 件 / 取込済 {outcome.already_imported} 件",
        )

    def run_import(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        selection = VolumeSelection.from_params(ctx.params)
        profile = _fixed_profile(conn, selection)
        # RUNTIME 層の設定はジョブの開始時に読み直す（画面での変更を待たせない）。
        settings = SettingsService(conn, self._env).snapshot()
        publisher = ArtifactPublisher(conn, settings.data_root, MediaProbe())
        importer = Importer(conn, publisher, settings.data_root, settings.default_timezone)
        handle = self._volumes.open(selection)
        try:
            outcome = importer.run(ctx, handle.dirfd, selection.volume_instance_id, profile)
        finally:
            self._volumes.release(selection)
        ctx.emit("info", f"取り込み完了: {outcome.published} 件")

    def run_detect_groups(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        profile = _profile_ref(conn, ctx.params)
        outcome = GroupDetector(conn, MergeRepository(conn)).run(ctx, profile)
        ctx.emit(
            "info",
            f"検出完了: 新規 {outcome.created} 件 / 既存 {outcome.existing} 件"
            f" / 見送り {outcome.undefined} 件",
        )

    def run_merge(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        profile = _profile_ref(conn, ctx.params)
        settings = SettingsService(conn, self._env).snapshot()
        publisher = ArtifactPublisher(conn, settings.data_root, MediaProbe())
        merger = Merger(
            conn,
            MergeRepository(conn),
            publisher,
            MergeRunner(),
            MediaProbe(),
            settings.data_root,
        )
        try:
            result = merger.run(
                ctx, ctx.params["merge_group_id"], ctx.params["input_digest"], profile
            )
        except (MergeCancelled, PublishCancelled) as exc:
            # **協調キャンセルは「完了」として返す。** 送出すると JobRunner の
            # 例外経路が job を failed にし、利用者が押したキャンセルが失敗として
            # 記録される。正常 return すれば `finish_claimed` が
            # `cancelling -> cancelled` を決着させる（取り込みも同じ形で降りる）。
            ctx.emit("info", f"結合を中止した: {exc}")
            return
        ctx.emit(
            "info",
            f"結合完了: {result.rel_path}（経路 {result.route} /"
            f" 検証 {'合格' if result.passed else '不合格'}）",
        )


def _fixed_profile(conn: sqlite3.Connection, selection: VolumeSelection) -> ProfileRef:
    """キュー投入時に固定したリビジョンを読む."""
    return _profile_ref(
        conn,
        {
            "profile_id": selection.profile_id,
            "profile_revision_id": selection.profile_revision_id,
        },
    )


def _profile_ref(conn: sqlite3.Connection, params: Mapping[str, Any]) -> ProfileRef:
    """params に固定したリビジョンを読む.

    現行リビジョンを読み直すと、キューで待っている間にプロファイルを
    編集しただけで、確認画面と違う規則で処理される。
    """
    registry = ProfileRegistry(conn)
    revision_id = params["profile_revision_id"]
    return ProfileRef(
        profile_id=params["profile_id"],
        revision_id=revision_id,
        revision=0,
        definition=registry.definition_of(revision_id),
    )
