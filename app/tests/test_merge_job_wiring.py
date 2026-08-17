import json
import threading

import anyio
import pytest

from mediaferry.adapters.ffmpeg import MergeCancelled
from mediaferry.api.jobs_wiring import JobWorld
from mediaferry.core.merge.grouping import GIB, GroupCandidate, MergePart
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.runner import JobRunner

from .test_merger import BASE
from .test_schema_artifacts import a_media_file


def a_cancelling_runner():
    """結合の入口で止まり、テストの合図でキャンセルを観測した形にする.

    合図で待たせないと、ハンドラが `request_cancel` より先に終わって
    `finish_claimed` が `succeeded` を書き、テストが競合で揺れる。
    """
    started = threading.Event()
    proceed = threading.Event()

    class CancellingRunner:
        def merge(self, *args, **kwargs):
            started.set()
            proceed.wait(timeout=10)
            raise MergeCancelled("キャンセル要求を観測した")

    return CancellingRunner, started, proceed


@pytest.mark.anyio
async def test_a_cancelled_merge_job_ends_as_cancelled(db, database, data_root, monkeypatch):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)

    members = []
    for index in (1, 2):
        name = f"DJI_20260817143000_{index:04d}_D.MP4"
        (directory / name).write_bytes(b"x" * 16)
        rel = f"library/dji-osmo/DCIM/DJI_001/{name}"
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=rel,
            sha1=f"{index:040d}",
            size_bytes=16,
            duration_seconds=2.0,
            captured_at=BASE.isoformat(),
        )
        members.append(MergePart(media_id, rel, f"{index:040d}", BASE, 2.0, 16 * GIB, "ok"))

    repo = MergeRepository(db)
    group_id = repo.save_detected(
        profile, GroupCandidate(members=tuple(members), gaps=(0.0,)), "digest-1"
    )
    runner_class, started, proceed = a_cancelling_runner()
    monkeypatch.setattr("mediaferry.api.jobs_wiring.MergeRunner", runner_class)

    store = JobStore(db)
    job_id = store.enqueue(
        "merge",
        {
            "merge_group_id": group_id,
            "input_digest": "digest-1",
            "profile_id": profile.profile_id,
            "profile_revision_id": profile.revision_id,
        },
    )
    world = JobWorld(database, {"MEDIAFERRY_DATA_ROOT": str(data_root)}, volumes=None)
    runner = JobRunner(database, poll_interval=0.01)
    runner.register("merge", world.run_merge)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(10):
            # ハンドラが結合に入るまで待つ。ここで止まっている。
            while not started.is_set():
                await anyio.sleep(0.01)
            store.request_cancel(job_id)
            proceed.set()
            while store.get(job_id)["status"] in {"running", "cancelling"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(job_id)["status"] == "cancelled"
    assert repo.get(group_id)["status"] == "detected"


@pytest.mark.anyio
async def test_the_handler_reads_the_revision_pinned_in_the_params(
    db, database, data_root, monkeypatch
):
    """params に固定したリビジョンで動く.

    現行を読み直すと、キューで待っている間の編集で違う規則になる。ここでは
    連番を読めない `sequence_pattern` の版を現行にして、読み直していれば
    出力名を決められずに失敗することで見分ける。
    """
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)

    members = []
    for index in (1, 2):
        name = f"DJI_20260817143000_{index:04d}_D.MP4"
        (directory / name).write_bytes(b"x" * 16)
        rel = f"library/dji-osmo/DCIM/DJI_001/{name}"
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=rel,
            sha1=f"{index:040d}",
            size_bytes=16,
            duration_seconds=2.0,
            captured_at=BASE.isoformat(),
        )
        members.append(MergePart(media_id, rel, f"{index:040d}", BASE, 2.0, 16 * GIB, "ok"))

    repo = MergeRepository(db)
    group_id = repo.save_detected(
        profile, GroupCandidate(members=tuple(members), gaps=(0.0,)), "digest-1"
    )
    runner_class, started, proceed = a_cancelling_runner()
    monkeypatch.setattr("mediaferry.api.jobs_wiring.MergeRunner", runner_class)

    store = JobStore(db)
    job_id = store.enqueue(
        "merge",
        {
            "merge_group_id": group_id,
            "input_digest": "digest-1",
            "profile_id": profile.profile_id,
            "profile_revision_id": profile.revision_id,
        },
    )
    # 投入の後にプロファイルを編集する。新しい版では連番を読めない。
    definition = json.loads(
        db.execute(
            "SELECT definition_json FROM profile_revision WHERE id = ?", (profile.revision_id,)
        ).fetchone()[0]
    )
    definition["merge"]["sequence_pattern"] = "_(?P<seq>[0-9]{9})_X$"
    db.execute(
        "INSERT INTO profile_revision (id, profile_id, revision, definition_json,"
        " schema_version, created_at)"
        " SELECT 'rev-new', profile_id, revision + 1, ?, schema_version, created_at"
        " FROM profile_revision WHERE id = ?",
        (json.dumps(definition), profile.revision_id),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = 'rev-new' WHERE id = ?",
        (profile.profile_id,),
    )

    world = JobWorld(database, {"MEDIAFERRY_DATA_ROOT": str(data_root)}, volumes=None)
    runner = JobRunner(database, poll_interval=0.01)
    runner.register("merge", world.run_merge)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(10):
            while not started.is_set():
                await anyio.sleep(0.01)
            store.request_cancel(job_id)
            proceed.set()
            while store.get(job_id)["status"] in {"running", "cancelling"}:
                await anyio.sleep(0.01)
        await runner.stop()

    # 固定した版で動いたので、出力名は決まりキャンセルまで到達する。
    assert store.get(job_id)["status"] == "cancelled"
    assert repo.get(group_id)["status"] == "detected"
