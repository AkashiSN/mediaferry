"""ジョブ 1 件ぶんの世界を組み立てる.

ジョブごとに DB 接続を開き、JobStore と ArtifactPublisher の両方をそれに
束ねる。手順 7（§9.3）でリースの確認と staged への遷移を 1 つの
BEGIN IMMEDIATE に入れる必要があり、別接続だと同じトランザクションに
できない。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from ..adapters.ffprobe import MediaProbe
from ..adapters.publisher import ArtifactPublisher
from ..db.connection import Database
from ..db.jobs import JobContext, JobStore
from ..db.profiles import ProfileRef, ProfileRegistry
from ..jobs.importer import Importer
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


def _fixed_profile(conn: sqlite3.Connection, selection: VolumeSelection) -> ProfileRef:
    """キュー投入時に固定したリビジョンを読む.

    現行リビジョンを読み直すと、キューで待っている間にプロファイルを
    編集しただけで、確認画面と違う規則で取り込まれる。
    """
    registry = ProfileRegistry(conn)
    definition = registry.definition_of(selection.profile_revision_id)
    return ProfileRef(
        profile_id=selection.profile_id,
        revision_id=selection.profile_revision_id,
        revision=0,
        definition=definition,
    )
