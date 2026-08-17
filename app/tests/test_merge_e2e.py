import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from mediaferry.adapters.ffmpeg import MergeRunner
from mediaferry.adapters.ffprobe import MediaProbe
from mediaferry.adapters.publisher import ArtifactPublisher
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.selection import SelectionService
from mediaferry.jobs.detect_groups import GroupDetector
from mediaferry.jobs.merger import Merger
from mediaferry.jobs.reconcile import Reconciler

from .test_schema_artifacts import a_media_file

BASE = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
GIB = 1024**3


def make_clip(path, seconds=2):
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def plenty_of_space(monkeypatch):
    """空き容量の検査を通す.

    `library` は `min_part_size_gib` の判定を満たすために `size_bytes` へ
    16 GiB を書く（実体は小さいクリップのまま）。結合ジョブの空き容量検査は
    その値を読むので、TS 経路のピーク（2 倍）で 64 GiB を要求してしまう。
    検査そのものは `test_merger.py` が実測に近い形で見ている。
    """

    class Plenty:
        f_frsize = 1
        f_bavail = 1024**5

    monkeypatch.setattr("mediaferry.jobs.merger.os.statvfs", lambda path: Plenty())


@pytest.fixture
def library(db, data_root, plenty_of_space):
    """検出の閾値を満たすように、size_bytes には 16 GiB を書く.

    実体は小さいクリップのまま。`min_part_size_gib` の判定は DB の値を見る
    ので、16 GiB のファイルを作らずに分割録画の並びを再現できる。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)
    for index in (1, 2):
        name = f"DJI_20260817143000_{index:04d}_D.MP4"
        make_clip(directory / name)
        a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/DJI_001/{name}",
            sha1=f"{index:040d}",
            size_bytes=16 * GIB,
            duration_seconds=2.0,
            captured_at=(BASE + timedelta(seconds=2 * (index - 1))).isoformat(),
        )
    return profile


def a_merger(db, data_root, repo):
    return Merger(
        db,
        repo,
        ArtifactPublisher(db, data_root, MediaProbe()),
        MergeRunner(),
        MediaProbe(),
        data_root,
    )


def test_detect_merge_and_offer_the_result(db, data_root, library):
    """検出 → 結合 → 公開 → 採用 → 選択肢まで通す.

    **合成クリップではサイズ検査が必ず不合格になる。** lavfi の低ビットレートな
    MP4 は、コンテナのオーバーヘッドが `bit_rate × duration` の 7〜8% を占める
    （実機の 16 GiB では 0.002%。Phase 0 の実測）。ここは閾値の妥当性ではなく
    経路を確かめるテストなので、不合格の出力を**採用**して選択肢に出す
    ところまでを 1 本で通す。閾値そのものは `test_merge_verify.py` が見る。
    """
    profile = library
    repo = MergeRepository(db)
    store = JobStore(db)

    store.enqueue("detect_groups", {})
    detect_ctx = store.claim_next()
    assert GroupDetector(db, repo).run(detect_ctx, profile).created == 1
    group = repo.list_groups()[0]

    # 検出したグループは、まだ選択肢に出ない（構成ファイルも出ない）。
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []

    store.enqueue("merge", {})
    merge_ctx = store.claim_next()
    result = a_merger(db, data_root, repo).run(
        merge_ctx, group["id"], group["input_digest"], profile
    )

    assert (data_root / result.rel_path).exists()
    assert repo.get(group["id"])["status"] == "merged"
    verification = json.loads(repo.get(group["id"])["verification_json"])
    verdicts = {check["name"]: check["verdict"] for check in verification["checks"]}
    assert verdicts["duration"] == "pass"
    assert verdicts["streams"] == "pass"
    assert verdicts["frames"] == "pass"
    # 合成クリップのコンテナのオーバーヘッドで、サイズだけが落ちる。
    assert verdicts["size"] == "fail"

    # 不合格なので既定では出ない。フィルタでは見える。
    service = SelectionService(db, ProfileRegistry(db))
    assert service.selectable() == []
    assert [item.media_file_id for item in service.selectable(include=["unadopted_derived"])] == [
        result.media_file_id
    ]

    repo.adopt(group["id"])

    offered = service.selectable()
    assert [item.media_file_id for item in offered] == [result.media_file_id]
    assert offered[0].merge_group_id == group["id"]


def test_the_digest_stops_matching_when_a_part_is_replaced(db, data_root, library):
    profile = library
    repo = MergeRepository(db)
    store = JobStore(db)
    store.enqueue("detect_groups", {})
    GroupDetector(db, repo).run(store.claim_next(), profile)
    group = repo.list_groups()[0]
    store.enqueue("merge", {})
    a_merger(db, data_root, repo).run(
        store.claim_next(), group["id"], group["input_digest"], profile
    )

    # パートの中身が差し替わると digest が合わなくなり、派生物は候補から外れる。
    db.execute(
        "UPDATE media_file SET sha1 = 'edited' WHERE id ="
        " (SELECT id FROM media_file WHERE role = 'original' ORDER BY rel_path LIMIT 1)"
    )

    assert [item.reason for item in SelectionService(db, ProfileRegistry(db)).selectable()] == []


def test_a_merge_interrupted_after_staging_is_settled_at_startup(db, data_root, library):
    profile = library
    repo = MergeRepository(db)
    store = JobStore(db)
    store.enqueue("detect_groups", {})
    GroupDetector(db, repo).run(store.claim_next(), profile)
    group = repo.list_groups()[0]

    store.enqueue("merge", {})
    ctx = store.claim_next()
    merger = a_merger(db, data_root, repo)
    # 公開は終わったが mark_merged の前で落ちた状態を作る。
    merger._repo.mark_merged = lambda group_id: None  # noqa: SLF001
    merger.run(ctx, group["id"], group["input_digest"], profile)
    assert repo.get(group["id"])["status"] == "merging"

    report = Reconciler(
        db, data_root, ArtifactPublisher(db, data_root, MediaProbe()), JobStore(db)
    ).run()

    assert report.merges_completed == 1
    assert repo.get(group["id"])["status"] == "merged"
