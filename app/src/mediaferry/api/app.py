"""FastAPI の組み立てと起動時の手順.

起動時に必ず行うこと:
  1. マイグレーション適用
  2. ビルトインプロファイルの同期
  3. reconciliation（前回の中断からの回収）
  4. ワーカーの開始

`BIND_HOST` の既定は loopback。認証と CSRF が入る Phase 4 より前に LAN へ
公開しない。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI

from ..adapters.broker_client import BrokerClient
from ..adapters.ffprobe import MediaProbe
from ..adapters.fs import assert_same_filesystem
from ..adapters.publisher import ArtifactPublisher
from ..db.connection import Database
from ..db.jobs import JobStore
from ..db.migrate import apply_migrations
from ..db.profiles import ProfileRegistry
from ..jobs.reconcile import Reconciler, ReconcileReport
from ..jobs.runner import JobRunner
from ..jobs.volumes import VolumeService
from ..settings import Settings, SettingsService, bootstrap_data_root, startup_warnings
from .errors import install_error_handlers
from .jobs_wiring import JobWorld
from .routes_destinations import router as destinations_router
from .routes_devices import router as devices_router
from .routes_media import router as media_router
from .routes_merges import router as merges_router
from .routes_system import router as system_router
from .routes_uploads import router as uploads_router

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    database: Database
    env: Mapping[str, str]
    volumes: VolumeService
    runner: JobRunner
    last_reconcile: ReconcileReport = field(default_factory=ReconcileReport)


def create_app(
    env: Mapping[str, str] | None = None,
    broker_factory: Callable[[], BrokerClient] | None = None,
) -> FastAPI:
    env = dict(os.environ if env is None else env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(bootstrap_data_root(env) / "var" / "mediaferry.sqlite3")

        # 起動の手順はこの 1 本で行い、終わったら閉じる。
        startup = database.connect()
        try:
            apply_migrations(startup)
            settings = SettingsService(startup, env).snapshot()
            for warning in startup_warnings(settings):
                logger.warning("%s", warning)
            # 公開は os.link なので、staging と公開先が別デバイスだと必ず失敗する。
            assert_same_filesystem(
                settings.data_root / "staging",
                settings.data_root / "library",
                settings.data_root / "derived",
            )
            ProfileRegistry(startup).sync_builtins()
            # **転送先が 1 件でもあってマスター鍵が無ければ起動しない**（§12.3）。
            _assert_master_key(startup, settings)
            report = Reconciler(
                startup,
                settings.data_root,
                ArtifactPublisher(startup, settings.data_root, MediaProbe()),
                JobStore(startup),
            ).run()
        finally:
            startup.close()

        client = broker_factory() if broker_factory else BrokerClient(settings.broker_socket)
        # VolumeService は長寿命なので専用の接続を持つ。他とは共有しない。
        volumes_conn = database.connect()
        volumes = VolumeService(volumes_conn, ProfileRegistry(volumes_conn), client)
        world = JobWorld(database, env, volumes)
        runner = JobRunner(database)
        runner.register("scan", world.run_scan)
        runner.register("import", world.run_import)
        runner.register("detect_groups", world.run_detect_groups)
        runner.register("merge", world.run_merge)
        runner.register("upload", world.run_upload)

        state = AppState(
            database=database, env=env, volumes=volumes, runner=runner, last_reconcile=report
        )
        app.state.mediaferry = state

        worker = asyncio.create_task(runner.run_forever())
        try:
            yield
        finally:
            # 走っているジョブにキャンセルを要求し、**実際に終わるまで待つ**。
            #
            # ここで timeout を付けて worker を cancel してはいけない。
            # `to_thread` のハンドラはそれでは止まらないのに、coroutine 側の
            # finally だけが走って、まだ読み書きしている接続を閉じてしまう。
            # 猶予を超えた場合はコンテナの SIGKILL に委ねる（プロセスごと
            # 終わるので、中途半端に資源を剥がすより安全）。
            await runner.stop()
            await worker
            volumes.close_all()
            volumes_conn.close()

    app = FastAPI(title="mediaferry", lifespan=lifespan)
    # **すべての失敗を同じ封筒に入れる**（画面は `code` を見て日本語を決める。§13）。
    install_error_handlers(app)
    app.include_router(system_router, prefix="/api")
    app.include_router(devices_router, prefix="/api")
    app.include_router(media_router, prefix="/api")
    app.include_router(merges_router, prefix="/api")
    app.include_router(destinations_router, prefix="/api")
    app.include_router(uploads_router, prefix="/api")
    return app


def _assert_master_key(conn: sqlite3.Connection, settings: Settings) -> None:
    """鍵が無いまま起動すると、資格情報を復号できないジョブが走る."""
    if settings.secret_key is not None:
        return
    count = conn.execute(
        "SELECT count(*) FROM upload_destination WHERE archived_at IS NULL"
    ).fetchone()[0]
    if count:
        raise RuntimeError(
            f"転送先が {count} 件あるが MEDIAFERRY_SECRET_KEY が未設定。鍵を与えるまで起動しない"
        )
