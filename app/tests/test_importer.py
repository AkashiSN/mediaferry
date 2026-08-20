import errno
import os

import pytest

from mediaferry.adapters.publisher import ArtifactPublisher, PublishInterrupted
from mediaferry.core.profiles.model import definition_to_json, parse_definition
from mediaferry.core.timestamps import TimezoneUnresolved
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.importer import Importer, ImportFailed, NotEnoughSpace
from mediaferry.jobs.scan import Scanner

from .test_publisher import StubProbe
from .test_schema_sources import a_volume


@pytest.fixture
def importing(db, data_root, tmp_path):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()
    card = tmp_path / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").write_bytes(b"a" * 100)
    (card / "PANORAMA").mkdir()
    (card / "PANORAMA" / "PANO_0001.JPG").write_bytes(b"jpeg")
    fd = os.open(card, os.O_RDONLY | os.O_DIRECTORY)
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    Scanner(db).scan(ctx, fd, volume_id, profile)
    publisher = ArtifactPublisher(db, data_root, StubProbe())
    importer = Importer(db, publisher, data_root, default_timezone="Asia/Tokyo")
    yield importer, ctx, fd, volume_id, profile
    os.close(fd)


def test_import_mirrors_the_path_on_the_card(importing, db, data_root):
    importer, ctx, fd, volume_id, profile = importing
    outcome = importer.run(ctx, fd, volume_id, profile)
    assert outcome.published == 2
    assert (
        data_root / "library/dji-osmo/DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"
    ).read_bytes() == b"a" * 100
    assert (data_root / "library/dji-osmo/PANORAMA/PANO_0001.JPG").exists()


def test_captured_at_comes_from_the_filename_when_it_matches(importing, db):
    importer, ctx, fd, volume_id, profile = importing
    importer.run(ctx, fd, volume_id, profile)
    row = db.execute(
        "SELECT * FROM media_file WHERE rel_path LIKE '%DJI_20260817143000%'"
    ).fetchone()
    assert row["captured_at"].startswith("2026-08-17T14:30:00")
    assert row["captured_at_source"] == "filename"
    assert row["captured_at_tz"] == "Asia/Tokyo"


def test_files_without_a_timestamp_in_the_name_fall_back_to_mtime(importing, db):
    importer, ctx, fd, volume_id, profile = importing
    importer.run(ctx, fd, volume_id, profile)
    row = db.execute("SELECT * FROM media_file WHERE rel_path LIKE '%PANO_0001%'").fetchone()
    assert row["captured_at_source"] == "mtime"


def test_reimporting_is_a_no_op(importing, db, data_root):
    importer, ctx, fd, volume_id, profile = importing
    importer.run(ctx, fd, volume_id, profile)
    second = importer.run(ctx, fd, volume_id, profile)
    assert second.published == 0
    assert second.skipped == 2
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 2


def test_missing_timezone_stops_before_touching_anything(db, data_root, importing):
    """force_offset なのに TZ が無いなら、取り込みを一切開始しない（§12.2）."""
    _, ctx, fd, volume_id, profile = importing
    importer = Importer(
        db, ArtifactPublisher(db, data_root, StubProbe()), data_root, default_timezone=None
    )
    with pytest.raises(TimezoneUnresolved):
        importer.run(ctx, fd, volume_id, profile)
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0
    assert not list((data_root / "library").rglob("*"))


def test_not_enough_space_stops_before_starting(importing, db, monkeypatch):
    importer, ctx, fd, volume_id, profile = importing
    monkeypatch.setattr(importer, "_free_bytes", lambda: 1)
    with pytest.raises(NotEnoughSpace):
        importer.run(ctx, fd, volume_id, profile)
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0


def test_cancelling_stops_between_files(importing, db, data_root, monkeypatch):
    """キャンセル済みなら 1 件目にも手を付けない.

    ファイル単位の確認が無くても chunk 境界で降りるので結果は同じだが、
    16GiB のカードでは「開いて読み始めてから降りる」だけで待たされる。
    """
    importer, ctx, fd, volume_id, profile = importing
    attempted = []
    original = importer._publish_one  # noqa: SLF001
    monkeypatch.setattr(
        importer,
        "_publish_one",
        lambda *a, **k: (attempted.append(1), original(*a, **k))[1],
    )

    JobStore(db).request_cancel(ctx.job_id)
    outcome = importer.run(ctx, fd, volume_id, profile)
    assert outcome.published == 0
    assert attempted == []


def test_a_failing_file_does_not_stop_the_rest_but_fails_the_job(importing, db, monkeypatch):
    """ファイル単位では続行する. ただしジョブは failed で終わる.

    全件失敗しても succeeded になると、監視も画面も「取り込めた」と読む。
    """
    importer, ctx, fd, volume_id, profile = importing
    calls = {"n": 0}
    original = importer._publish_one  # noqa: SLF001

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("メタデータが読めない")
        return original(*args, **kwargs)

    monkeypatch.setattr(importer, "_publish_one", flaky)
    with pytest.raises(ImportFailed) as exc:
        importer.run(ctx, fd, volume_id, profile)
    assert exc.value.outcome.failed == 1
    assert exc.value.outcome.published == 1
    failed_count = db.execute(
        "SELECT count(*) FROM source_entry WHERE state = 'failed'"
    ).fetchone()[0]
    assert failed_count == 1


def test_a_vanished_card_stops_the_run_instead_of_grinding_through(importing, db, monkeypatch):
    """取り込み中にカードを抜くケース（手動チェックリスト #5）."""
    importer, ctx, fd, volume_id, profile = importing

    def gone(*args, **kwargs):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(importer, "_publish_one", gone)
    with pytest.raises(ImportFailed) as exc:
        importer.run(ctx, fd, volume_id, profile)
    assert exc.value.outcome.published == 0
    # 2 件目は試さずに降りる
    assert exc.value.outcome.failed == 1


def test_an_interrupted_publish_leaves_the_entry_for_reconciliation(importing, db, monkeypatch):
    """staged 以降の失敗で failed に戻すと、次のスキャンで二重に取り込む."""
    importer, ctx, fd, volume_id, profile = importing

    def interrupted(*args, **kwargs):
        raise PublishInterrupted("link に失敗した")

    monkeypatch.setattr(importer, "_publish_one", interrupted)
    with pytest.raises(PublishInterrupted):
        importer.run(ctx, fd, volume_id, profile)
    failed_count = db.execute(
        "SELECT count(*) FROM source_entry WHERE state = 'failed'"
    ).fetchone()[0]
    assert failed_count == 0


def test_the_copy_heartbeats_on_elapsed_time_not_bytes(importing, db, monkeypatch):
    """低速なカードだと、閾値バイトに達する前にリースが切れる."""
    from mediaferry.jobs import importer as importer_module

    importer, ctx, fd, volume_id, profile = importing
    monkeypatch.setattr(importer_module, "COPY_CHUNK", 8)
    monkeypatch.setattr(importer_module, "HEARTBEAT_INTERVAL", 0)
    beats = []
    monkeypatch.setattr(ctx, "heartbeat", lambda progress=None: beats.append(1))
    importer.run(ctx, fd, volume_id, profile)
    # 100 バイトを 8 バイトずつなので、ファイル単位の 1 回より多く打つ
    assert len(beats) > 2


def test_the_copy_stops_at_a_chunk_boundary_when_cancelled(importing, db, monkeypatch):
    """chunk 境界がキャンセルポイント（§9.9）. 見ないと 16GiB 待たされる.

    最後まで読んでも staged の直前で assert_lease が止めるので、結果だけを
    見ると差が出ない。**降りるまでに何バイト読んだか**が違いになる。
    キャンセルはコピーが始まってから出す。開始前に出すと、ファイル単位の
    確認で先に抜けてこの境界を通らない。
    """
    from mediaferry.adapters.publisher import HashingWriter
    from mediaferry.jobs import importer as importer_module

    importer, ctx, fd, volume_id, profile = importing
    monkeypatch.setattr(importer_module, "COPY_CHUNK", 8)

    written = []
    real_write = HashingWriter.write

    def spy_write(self, data):
        written.append(len(data))
        if len(written) == 1:
            JobStore(db).request_cancel(ctx.job_id)
        return real_write(self, data)

    monkeypatch.setattr(HashingWriter, "write", spy_write)

    outcome = importer.run(ctx, fd, volume_id, profile)
    assert outcome.published == 0
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0
    # 100 バイトのファイルを 8 バイト刻みで読み切る前に降りている
    assert sum(written) < 100


# ----------------------------------------------------------------------
# source: exif（Task 3）


def an_exif_profile(db, source="exif"):
    """`canon-eos` 相当（`timezone_policy: none`、EXIF から日時）のプロファイル."""
    from .test_profile_model import a_definition

    registry = ProfileRegistry(db)
    defn = a_definition(
        slug="canon-like",
        timestamp={
            "source": source,
            "pattern": None,
            "format": None,
            "fallback": "mtime",
            "timezone_policy": "none",
            "timezone": None,
        },
    )
    registry._upsert_revision("canon-like", definition_to_json(parse_definition(defn)))
    return registry.current("canon-like")


def test_a_photo_gets_its_time_from_the_exif_of_the_staged_copy(importing, db, tmp_path):
    """**読むのはステージ済みのコピー。** ソースを 2 度読むと、コピー中に
    書き換えられた場合に「取り込んだ中身と読んだ EXIF が違う」状態を作れる。
    """
    from .exif_fixtures import a_jpeg_with

    importer, ctx, fd, volume_id, profile = importing
    profile = an_exif_profile(db)
    card = tmp_path / "card"
    (card / "PANORAMA" / "PANO_0001.JPG").write_bytes(a_jpeg_with(b"2026:03:04 05:06:07"))
    db.execute("DELETE FROM source_entry")
    Scanner(db).scan(ctx, fd, volume_id, profile)

    importer.run(ctx, fd, volume_id, profile)
    row = db.execute("SELECT * FROM media_file WHERE rel_path LIKE '%PANO_0001%'").fetchone()
    assert row["captured_at_source"] == "exif"
    assert row["captured_at"].startswith("2026-03-04T05:06:07")


def test_a_video_never_goes_through_exif(importing, db, monkeypatch):
    """**画像以外では読みに行かない。**

    `exifread` は認識できない入力に対して例外ではなく警告を出す（実測）。
    Canon は MOV も `source: exif` のプロファイルを通るので、呼べば動画
    1 本ごとに警告が並ぶ。

    **結果で見てはいけない。** 動画で読みに行っても `None` が返って `fallback`
    に落ちるので、`captured_at_source` は読んでも読まなくても `mtime` になる。
    見るべきは「呼んだかどうか」。
    """
    from mediaferry.jobs import importer as importer_module

    importer, ctx, fd, volume_id, profile = importing
    profile = an_exif_profile(db)
    read: list = []

    def spy(path):
        # 中身はその場で控える。staging のファイルは公開後に消える。
        read.append(path.read_bytes())
        return None

    monkeypatch.setattr(importer_module, "read_datetime_original", spy)
    db.execute("DELETE FROM source_entry")
    Scanner(db).scan(ctx, fd, volume_id, profile)
    importer.run(ctx, fd, volume_id, profile)

    # **渡されるのはステージ済みのパス**（拡張子を持たない UUID）なので、
    # 名前では見分けられない。カードには動画 1・写真 1 があるので、回数と
    # 中身で判定する。
    assert len(read) == 1, f"画像 1 枚のはずが {len(read)} 回読んでいる"
    assert read[0] == b"jpeg", "読んだのが写真ではない"
    row = db.execute("SELECT * FROM media_file WHERE rel_path LIKE '%.MP4'").fetchone()
    assert row["captured_at_source"] == "mtime"


def test_a_photo_without_exif_falls_back(importing, db, tmp_path):
    importer, ctx, fd, volume_id, profile = importing
    profile = an_exif_profile(db)
    db.execute("DELETE FROM source_entry")
    Scanner(db).scan(ctx, fd, volume_id, profile)
    importer.run(ctx, fd, volume_id, profile)
    row = db.execute("SELECT * FROM media_file WHERE rel_path LIKE '%PANO_0001%'").fetchone()
    assert row["captured_at_source"] == "mtime"


def test_the_copy_reports_where_it_is(importing, db, monkeypatch):
    """**16 GiB のコピーが無言だと、止まっているのか進んでいるのか分からない.**

    心拍と同じ 1 回の UPDATE に乗せるので、書き込みは増えない。
    """
    import json

    importer, ctx, fd, volume_id, profile = importing
    seen = []
    real = ctx.heartbeat

    def watching(progress=None):
        if progress is not None:
            seen.append(progress)
        real(progress)

    monkeypatch.setattr(ctx, "heartbeat", watching)
    monkeypatch.setattr("mediaferry.jobs.importer.HEARTBEAT_INTERVAL", 0)
    importer.run(ctx, fd, volume_id, profile)

    assert seen, "進捗が 1 度も報告されていない"
    copying = [p for p in seen if p["phase"] == "copy"]
    assert copying
    first = copying[0]
    assert first["file_count"] == 2
    assert first["file_index"] >= 1
    assert first["bytes_total"] > 0
    assert first["bytes_total_all"] >= first["bytes_total"]
    assert first["rel_path"]
    # 心拍と同じ行に乗っている（別の書き込みではない）。
    stored = json.loads(JobStore(db).get(ctx.job_id)["progress_json"])
    assert stored["phase"] == "copy"
