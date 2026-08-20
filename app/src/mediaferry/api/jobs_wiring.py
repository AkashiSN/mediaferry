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
from ..adapters.immich import ImmichClient
from ..adapters.publisher import ArtifactPublisher, PublishCancelled
from ..core.crypto import SecretBox
from ..db.connection import Database
from ..db.credentials import CredentialStore
from ..db.destinations import DestinationRepository
from ..db.jobs import JobContext, JobStore
from ..db.merges import MergeRepository
from ..db.profiles import ProfileRef, ProfileRegistry
from ..db.uploads import UploadRepository
from ..jobs.approvals import ApprovalService
from ..jobs.detect_groups import GroupDetector
from ..jobs.importer import Importer
from ..jobs.merger import Merger
from ..jobs.preflight import PreflightCache
from ..jobs.recheck import Rechecker
from ..jobs.recompute import Recomputer
from ..jobs.scan import Scanner
from ..jobs.stacker import Stacker
from ..jobs.uploader import Uploader
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

    def run_recompute_timestamps(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        profile = _profile_ref(conn, ctx.params)
        settings = SettingsService(conn, self._env).snapshot()
        outcome = Recomputer(conn, settings.data_root, settings.default_timezone).run(ctx, profile)
        # **キャンセルを「完了」と書かない。** 協調キャンセルは正常 return で降りて
        # くるので、`finished` を見ないとログに「中止」の後で「完了」が並ぶ。
        ctx.emit(
            "info",
            f"再計算{'完了' if outcome.finished else 'を中止した（ここまでの分は反映済み）'}:"
            f" 変更 {outcome.changed} 件 / 据え置き {outcome.unchanged} 件"
            f" / 飛ばし {outcome.skipped} 件 / 再確認へ戻し {outcome.requeued} 件"
            f" / スタック再評価 {outcome.reopened} 件",
        )

    def run_upload(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        settings = SettingsService(conn, self._env).snapshot()
        if settings.secret_key is None:
            raise RuntimeError("MEDIAFERRY_SECRET_KEY が未設定なので転送先を開けない")
        destinations = DestinationRepository(
            conn, CredentialStore(conn, SecretBox(settings.secret_key))
        )
        uploads = UploadRepository(conn, ProfileRegistry(conn), destinations)

        def open_client(revision: sqlite3.Row) -> ImmichClient:
            return ImmichClient(
                revision["base_url"],
                destinations.secret_of(revision["id"]),
                settings.upload_timeout_seconds,
            )

        preflight = PreflightCache(destinations, open_client)
        destination_id = ctx.params["destination_id"]
        stacker = Stacker(
            conn, uploads, destinations, ProfileRegistry(conn), open_client, preflight
        )
        if ctx.params.get("mode") == "approve":
            # 承認は 1 件だけを扱う。外部副作用の所有権はジョブのリースが持つ。
            ApprovalService(
                conn, uploads, destinations, ProfileRegistry(conn), open_client, preflight
            ).approve(ctx, ctx.params["upload_record_id"])
            ctx.emit("info", "日時の補正を承認して書き戻した")
            # **承認で `complete` になった行も第 2 パスの対象**（§9.11）。
            _emit_stacks(ctx, stacker.run(ctx, destination_id))
            return
        if ctx.params.get("mode") == "recheck":
            outcome = Rechecker(uploads, destinations, open_client, preflight).run(
                ctx, destination_id
            )
            ctx.emit(
                "info",
                f"再確認: {outcome.checked} 件 / ゴミ箱 {outcome.trashed} 件"
                f" / 消滅 {outcome.vanished} 件 / 復元 {outcome.restored} 件",
            )
            _emit_stacks(ctx, stacker.run(ctx, destination_id))
            return
        uploader = Uploader(
            conn,
            uploads,
            destinations,
            ProfileRegistry(conn),
            settings.data_root,
            open_client,
            preflight,
            settings.upload_max_attempts,
        )
        outcome = uploader.run(ctx, destination_id)
        ctx.emit(
            "info",
            f"アップロード完了: 送信 {outcome.sent} 件 / 承認待ち {outcome.awaiting} 件"
            f" / 見送り {outcome.skipped} 件 / 失敗 {outcome.failed} 件",
        )
        _emit_stacks(ctx, stacker.run(ctx, destination_id))


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


def _emit_stacks(ctx: JobContext, outcome) -> None:  # noqa: ANN001 - StackOutcome
    """第 2 パスの結果を出す. **何も無かったときは黙る**（毎回並べない）."""
    if outcome.stacked or outcome.skipped or outcome.deferred:
        ctx.emit(
            "info",
            f"スタック: {outcome.stacked} 組 / 見送り {outcome.skipped} 件"
            f" / 保留 {outcome.deferred} 件",
        )
