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


def _paths(db) -> set[str]:
    return {r["rel_path"] for r in db.execute("SELECT rel_path FROM source_entry")}


def test_a_file_that_left_the_card_stops_being_pending(scanning, db):
    """カードから消えたファイルの行は、スキャンが外す.

    外さないと `pending_count` が実体より多いまま残り、画面は「N 件取り込む」と
    言うのに取り込みは開けない ENOENT で失敗する。しかも失敗した行は
    `PENDING_CLAUSE` に居座るので、以後の取り込みが毎回失敗する。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 1
    assert _paths(db) == set()


def test_an_imported_entry_survives_the_file_leaving_the_card(scanning, db):
    """取り込み済みの観測は残す.

    `published` な行は「このカードから取り込んだ」という記録で、スタッキングの
    「同じカード」判定（design.md §9.11）と `_known_files_survive` の標本が
    これを引く。消すと、取り込んだ後にカードを消した人の組が崩れる。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published'")
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 0
    assert _paths(db) == {"DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"}


def test_a_failed_entry_that_left_the_card_stops_being_pending(scanning, db):
    """`failed` も取り込み対象（`PENDING_CLAUSE`）なので、同じく外す."""
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'failed'")
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 1
    assert _paths(db) == set()


def test_an_entry_whose_file_is_still_there_is_kept(scanning, db):
    """**「無い」には観測を要求する。** 列挙に出ないだけでは消さない.

    プロファイルの `scan.extensions` を狭めると、カードに実在するファイルの行が
    列挙に現れなくなる。そこで消すと、広げ直すまで取り込めたはずのものが
    黙って消える。`.LRF` は `dji-osmo` の `extensions` に無いが実在する。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES ('lrf', ?, 'DCIM/DJI_001/DJI_20260817143000_0001_D.LRF', 3, 0, 'x', 1,"
        " 'seen', '2000-01-01T00:00:00+00:00')",
        (volume_id,),
    )

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 0
    assert "DCIM/DJI_001/DJI_20260817143000_0001_D.LRF" in _paths(db)


def test_a_cancelled_scan_does_not_sweep(scanning, db):
    """途中で降りたスキャンは、見ていないだけのファイルを「消えた」と読む.

    `mark_scanned` と同じ理由で、完走したときだけ掃除する。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    # **カードを空にしない。** 列挙が 1 度も回らないとキャンセルを観測できず、
    # 「降りた」ではなく「数え終えた」になる（既存の `mark_scanned` と同じ）。
    (card / "DCIM" / "DJI_001" / "DJI_20260817143001_0002_D.MP4").write_bytes(b"d" * 10)
    scanner.scan(ctx, fd, volume_id, profile)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()
    JobStore(db).request_cancel(ctx.job_id)

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 0
    assert "DCIM/DJI_001/DJI_20260817143000_0001_D.MP4" in _paths(db)


def test_an_entry_a_staging_still_points_at_is_kept(scanning, db):
    """公開の途中で落ちた行は消さない.

    `artifact_staging.source_entry_id` は `ON DELETE RESTRICT` なので、消しに
    行くとスキャンごと `IntegrityError` で落ちる。中身は起動時の
    reconciliation（§9.6）が公開を完遂するので、行が要る。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    entry_id = db.execute("SELECT id FROM source_entry").fetchone()["id"]
    db.execute("UPDATE source_entry SET state = 'failed' WHERE id = ?", (entry_id,))
    db.execute(
        "INSERT INTO artifact_staging (id, kind, job_id, lease_token, state, staging_rel_path,"
        " final_rel_path, expected_size, content_sha1, metadata_json, source_entry_id,"
        " created_at, updated_at)"
        " VALUES ('stg', 'import', ?, 'tok', 'staged', 'staging/x', 'library/x', 100, 'sha',"
        " '{}', ?, '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')",
        (ctx.job_id, entry_id),
    )
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 0
    assert _paths(db) == {"DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"}


def test_the_lease_is_kept_alive_while_sweeping(scanning, db):
    """掃除も 1 件ずつ開くので、列挙と同じくリースを打ち続ける.

    打たないと、消えた行が多いカード（実機では 1420 件）で掃除の最中に失効し、
    走り続けているのに interrupted として表示される。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    (card / "DCIM" / "DJI_001" / "DJI_20260817143001_0002_D.MP4").write_bytes(b"d" * 10)
    scanner.scan(ctx, fd, volume_id, profile)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()
    beats = []
    real_heartbeat = ctx.heartbeat
    ctx.heartbeat = lambda: (beats.append(1), real_heartbeat())[1]

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 1
    # 列挙で 1 件（残ったファイル）＋ 掃除で 1 件（消えた候補）
    assert len(beats) == outcome.total + outcome.vanished == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="root は権限を無視して開けてしまう")
def test_an_entry_we_could_not_look_at_is_kept(scanning, db):
    """**「無い」と「確かめられなかった」を分ける。**

    `exists_beneath` は全 `OSError` を False にするので、EACCES・EIO・EMFILE でも
    「無い」に見える。列挙側（`_walk`）も開けないディレクトリを黙って飛ばすので、
    一時的な障害で**部分木ぶんの行がまとめて消える**。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    (card / "DCIM" / "DJI_001").chmod(0o000)
    try:
        outcome = scanner.scan(ctx, fd, volume_id, profile)
    finally:
        (card / "DCIM" / "DJI_001").chmod(0o755)

    assert outcome.vanished == 0
    assert _paths(db) == {"DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root は権限を無視して開けてしまう")
def test_what_we_could_not_look_at_is_said_out_loud(scanning, db):
    """黙って残すと、次の取り込みが ENOENT で落ちる理由が誰にも分からない."""
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    (card / "DCIM" / "DJI_001").chmod(0o000)
    try:
        scanner.scan(ctx, fd, volume_id, profile)
    finally:
        (card / "DCIM" / "DJI_001").chmod(0o755)

    messages = [e["message"] for e in JobStore(db).events(ctx.job_id) if e["level"] == "warning"]
    assert any("確かめられなかった" in m for m in messages)


def test_a_cancelled_scan_of_an_empty_card_does_not_sweep(scanning, db):
    """**掃除は破壊的なので、列挙が 1 度も回らなくてもキャンセルを見る。**

    `ctx.cancelled()` を for の中でしか見ないと、一致するファイルが 0 件の
    カードでは降りたことに気づけず、そのまま掃除してしまう。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    scanner.scan(ctx, fd, volume_id, profile)
    for path in (card / "DCIM" / "DJI_001").iterdir():
        path.unlink()
    JobStore(db).request_cancel(ctx.job_id)

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.vanished == 0
    assert _paths(db) == {"DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"}


def test_a_cancelled_scan_of_an_empty_card_does_not_claim_to_have_counted(scanning, db):
    scanner, ctx, fd, volume_id, profile, card = scanning
    for path in (card / "DCIM" / "DJI_001").iterdir():
        path.unlink()
    JobStore(db).request_cancel(ctx.job_id)

    scanner.scan(ctx, fd, volume_id, profile)

    assert _scanned_at(db, volume_id) is None
