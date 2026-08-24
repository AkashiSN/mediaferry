import os

import pytest

from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.scan import Scanner

from .test_schema_sources import a_volume


@pytest.fixture
def scanning(db, tmp_path):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    card = tmp_path / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").write_bytes(b"a" * 100)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.LRF").write_bytes(b"low")
    fd = os.open(card, os.O_RDONLY | os.O_DIRECTORY)
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    yield Scanner(db), ctx, fd, volume_id, profile, card
    os.close(fd)


def test_scanning_records_entries_for_matching_files(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.total == 1
    assert outcome.new == 1
    row = db.execute("SELECT * FROM source_entry").fetchone()
    assert row["rel_path"] == "DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"
    assert row["size_bytes"] == 100
    assert row["state"] == "seen"
    assert len(row["quick_fingerprint"]) == 40


def test_rescanning_an_unchanged_card_finds_nothing_new(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published'")
    second = scanner.scan(ctx, fd, volume_id, profile)
    assert second.already_imported == 1
    assert second.new == 0
    assert db.execute("SELECT count(*) FROM source_entry").fetchone()[0] == 1


def test_a_reused_filename_with_different_content_is_new_again(scanning, db):
    """SD をフォーマットして連番が再利用されたケース."""
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published'")
    target = card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4"
    target.write_bytes(b"b" * 100)  # 同じサイズ、違う中身
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.new == 1
    assert db.execute("SELECT state FROM source_entry").fetchone()["state"] == "seen"


def test_an_older_mtime_than_recorded_is_ambiguous(scanning, db):
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published', mtime_ns = mtime_ns + 1000000000")
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.ambiguous == 1


def test_an_old_fingerprint_version_is_recomputed(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET fingerprint_version = 0, quick_fingerprint = 'stale'")
    scanner.scan(ctx, fd, volume_id, profile)
    row = db.execute("SELECT * FROM source_entry").fetchone()
    assert row["fingerprint_version"] == 1
    assert row["quick_fingerprint"] != "stale"


def test_an_old_version_is_recomputed_even_when_the_fingerprint_matches(scanning, db):
    """版を上げる意味は「前の版の判定を信用しない」こと.

    指紋の文字列が一致しているかどうかで版の検査を代用すると、算出方法を
    変えた版でたまたま一致した行を取り込み済みのまま据え置いてしまう。
    """
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published', fingerprint_version = 0")

    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.new == 1
    assert outcome.already_imported == 0
    row = db.execute("SELECT * FROM source_entry").fetchone()
    assert row["fingerprint_version"] == 1
    assert row["state"] == "seen"


def test_an_entry_that_was_never_imported_is_still_new(scanning, db):
    """スキャンしただけの行を「取り込み済み」と報告すると、永久に取り込まれない."""
    scanner, ctx, fd, volume_id, profile, _ = scanning
    first = scanner.scan(ctx, fd, volume_id, profile)
    assert first.new == 1

    second = scanner.scan(ctx, fd, volume_id, profile)
    assert second.already_imported == 0
    assert second.new == 1
    assert db.execute("SELECT state FROM source_entry").fetchone()["state"] == "seen"


def test_the_lease_is_kept_alive_while_scanning(scanning, db):
    """16GiB のカードは 1 スキャンがリース (60 秒) より長くなりうる.

    heartbeat を打たないと、途中で失効して reap され、走り続けているのに
    interrupted として表示される。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    (card / "DCIM" / "DJI_001" / "DJI_20260817143001_0002_D.MP4").write_bytes(b"c" * 10)
    beats = []
    real_heartbeat = ctx.heartbeat
    ctx.heartbeat = lambda: (beats.append(1), real_heartbeat())[1]

    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert len(beats) == outcome.total == 2


def test_cancelling_stops_the_scan(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    JobStore(db).request_cancel(ctx.job_id)
    outcome = scanner.scan(ctx, fd, volume_id, profile)
    assert outcome.total == 0


def _scanned_at(db, volume_id: str) -> str | None:
    row = db.execute("SELECT scanned_at FROM volume_instance WHERE id = ?", (volume_id,)).fetchone()
    return row["scanned_at"]


def test_counting_an_empty_card_is_still_counting(scanning, db):
    """**中身が空でも「数えた」を記録する**（§11 の `scanned_at`）.

    一致するファイルが無いカードは `source_entry` を 1 行も作らないので、
    行から「数えたか」を導くと永久に「まだ数えていない」に見える。ホームは
    そのカードに「中身を数えています。」を出し続ける（DJI は内蔵ストレージと
    SD カードを同時に見せるので、実機で起きる）。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    for path in (card / "DCIM" / "DJI_001").iterdir():
        path.unlink()

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.total == 0
    assert db.execute("SELECT count(*) FROM source_entry").fetchone()[0] == 0
    assert _scanned_at(db, volume_id) is not None


def test_a_cancelled_scan_does_not_claim_to_have_counted(scanning, db):
    """途中で降りたスキャンは「数え終えた」ではない.

    記録してしまうと、1 件も見ていないカードに「取り込むものはありません。」と
    書くことになる。
    """
    scanner, ctx, fd, volume_id, profile, _ = scanning
    JobStore(db).request_cancel(ctx.job_id)

    scanner.scan(ctx, fd, volume_id, profile)

    assert _scanned_at(db, volume_id) is None


def test_progress_events_name_the_file(scanning, db):
    scanner, ctx, fd, volume_id, profile, _ = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    messages = [e["message"] for e in JobStore(db).events(ctx.job_id)]
    assert any("DJI_20260817143000_0001_D.MP4" in m for m in messages)
