import os
from dataclasses import replace

import pytest

from mediaferry.core.profiles.model import StackRule
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.scan import Scanner

from .test_schema_artifacts import a_media_file
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


@pytest.fixture
def canon_scanning(db, tmp_path):
    """`stack` が有効なプロファイルと、RAW+JPEG が並ぶカード."""
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("canon-eos")
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    card = tmp_path / "canon"
    (card / "DCIM" / "100CANON").mkdir(parents=True)
    (card / "DCIM" / "100CANON" / "IMG_0001.JPG").write_bytes(b"j" * 100)
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").write_bytes(b"r" * 200)
    fd = os.open(card, os.O_RDONLY | os.O_DIRECTORY)
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    yield Scanner(db), ctx, fd, volume_id, profile, card
    os.close(fd)


def _import_all(db, profile) -> None:
    """取り込みが済んだ姿にする（`media_file` を作り、`source_entry` から指す）.

    **`media_file_id` まで埋める。** 印を消す引き金はこの列が外れることなので、
    `state` だけを `published` にした行では引き金そのものが立たない。
    """
    for row in db.execute("SELECT id, rel_path FROM source_entry").fetchall():
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/canon-eos/{row['rel_path']}",
            kind="photo",
        )
        db.execute(
            "UPDATE source_entry SET state = 'published', media_file_id = ? WHERE id = ?",
            (media_id, row["id"]),
        )


def _keys(db) -> dict[str, str | None]:
    return {
        r["rel_path"]: r["copresent_key"]
        for r in db.execute("SELECT rel_path, copresent_key FROM source_entry")
    }


def test_two_files_seen_together_get_the_same_mark(canon_scanning, db):
    """**同席の証拠。** 同じスキャンで、同じ stem の下に 2 つ見えたときだけ書く."""
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning

    scanner.scan(ctx, fd, volume_id, profile)

    keys = _keys(db)
    expected = f"{ctx.job_id}:DCIM/100CANON/IMG_0001."
    assert keys["DCIM/100CANON/IMG_0001.JPG"] == expected
    assert keys["DCIM/100CANON/IMG_0001.CR2"] == expected


def test_the_lease_is_kept_alive_while_marking_copresence(canon_scanning, db):
    """`_mark_copresence` も 1 行ずつ `UPDATE` を打つので、心拍を打ち続ける.

    実機のカードは 1488 件で、その大半が RAW+JPEG（同席の印付けの対象）。
    `_sweep_vanished` と同じ規模のループなのに心拍を打たないと、低速な
    ディスクやネットワーク越しの DB で `HEARTBEAT_INTERVAL` / `LEASE_SECONDS`
    に届き、走り続けているのに interrupted として表示される。
    """
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning
    beats = []
    real_heartbeat = ctx.heartbeat
    ctx.heartbeat = lambda: (beats.append(1), real_heartbeat())[1]

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    # 列挙で 2 件（総数）＋ 同席の印付けで 2 件（組の両方に UPDATE）
    assert len(beats) == outcome.total + 2 == 4


def test_a_lone_file_gets_no_mark(canon_scanning, db):
    """相方が居なければ同席していない。**印を書かない。**"""
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()

    scanner.scan(ctx, fd, volume_id, profile)

    assert _keys(db)["DCIM/100CANON/IMG_0001.JPG"] is None


def test_two_files_with_the_same_extension_get_no_mark(canon_scanning, db):
    """**「2 件」ではなく「2 種類」。** 拡張子の大小文字違いだけで並んだ 2 件は組ではない.

    `IMG_0001.JPG` と `IMG_0001.jpg`（大小文字だけ違う）は、ケースセンシティブな
    FS では別ファイルとして列挙されるが、`extension_of` は両方とも `JPG` に
    正規化するので、これは RAW+JPEG の組ではなく `identity_partners` が
    `ambiguous` とする曖昧な重複（`core/uploads/stacking.py`）。件数ではなく
    **拡張子の種類数**で判定しないと、無関係な重複が組として印を持ってしまう。
    """
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()
    (card / "DCIM" / "100CANON" / "IMG_0001.jpg").write_bytes(b"k" * 100)

    scanner.scan(ctx, fd, volume_id, profile)

    keys = _keys(db)
    assert keys["DCIM/100CANON/IMG_0001.JPG"] is None
    assert keys["DCIM/100CANON/IMG_0001.jpg"] is None


def test_different_stems_do_not_share_a_mark(canon_scanning, db):
    """**印はスキャンごとではなく組ごと。**

    スキャンの id だけにすると、1 回のスキャンが書いた別々の組が同じ印になり、
    一覧が無関係な写真を 1 タイルに畳む。
    """
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    (card / "DCIM" / "100CANON" / "IMG_0002.JPG").write_bytes(b"a" * 100)
    (card / "DCIM" / "100CANON" / "IMG_0002.CR2").write_bytes(b"b" * 200)

    scanner.scan(ctx, fd, volume_id, profile)

    keys = _keys(db)
    assert keys["DCIM/100CANON/IMG_0001.JPG"] != keys["DCIM/100CANON/IMG_0002.JPG"]


def test_a_mark_survives_the_partner_leaving_the_card(canon_scanning, db):
    """**一度証明された同席は消えない。** 送信は取り込みよりずっと後になりうる."""
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    scanner.scan(ctx, fd, volume_id, profile)
    before = _keys(db)["DCIM/100CANON/IMG_0001.JPG"]
    db.execute("UPDATE source_entry SET state = 'published'")
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()

    scanner.scan(ctx, fd, volume_id, profile)

    assert _keys(db)["DCIM/100CANON/IMG_0001.JPG"] == before


def test_an_unimported_row_keeps_its_mark_when_the_partner_leaves(canon_scanning, db):
    """**印を消す引き金は `media_file_id` を外すこと**。外すものが無ければ消さない.

    取り込み待ち（`media_file_id` が NULL）の行は、まだどの `media_file` も代表して
    いないので外すものが無い。ここで消しに行くと、途中で降りたスキャンが組の片方
    だけを落とす。**残った印が相方を作ることもない** —— 消えた側の行は
    `_sweep_vanished` が外し、印の一致は同じカードの実在する行どうしでしか見ない。
    """
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    scanner.scan(ctx, fd, volume_id, profile)  # 両方 'seen' のまま、印が付く
    before = _keys(db)["DCIM/100CANON/IMG_0001.JPG"]
    assert before is not None
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()

    scanner.scan(ctx, fd, volume_id, profile)

    assert _keys(db) == {"DCIM/100CANON/IMG_0001.JPG": before}


def test_a_changed_file_loses_its_mark(canon_scanning, db):
    """中身が変わった行は、前の中身での同席を引き継がない.

    引き継ぐと、撮り直した JPG が**無関係な古い RAW と組む**
    （`docs/history/phase10-design.md` の 3）。
    """
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    scanner.scan(ctx, fd, volume_id, profile)
    _import_all(db, profile)
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()
    (card / "DCIM" / "100CANON" / "IMG_0001.JPG").write_bytes(b"z" * 100)

    scanner.scan(ctx, fd, volume_id, profile)

    assert _keys(db)["DCIM/100CANON/IMG_0001.JPG"] is None


def test_the_touch_path_still_fills_in_extension(canon_scanning, db):
    """`_touch`（`published` のまま内容も mtime も変わっていない）でも `extension` を埋める.

    埋めないと、**移行対象そのもの**（既存の `published` で内容も mtime も
    変わっていない行）が NULL のままになり、一覧の `rank` への join が外れて
    従判定されない（`docs/history/phase10-design.md`）。
    """
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published', extension = NULL")

    outcome = scanner.scan(ctx, fd, volume_id, profile)

    assert outcome.already_imported == 2
    rows = db.execute("SELECT rel_path, extension FROM source_entry").fetchall()
    exts = {r["rel_path"]: r["extension"] for r in rows}
    assert exts["DCIM/100CANON/IMG_0001.JPG"] == "JPG"
    assert exts["DCIM/100CANON/IMG_0001.CR2"] == "CR2"


def test_a_cancelled_scan_writes_no_mark(canon_scanning, db):
    """途中で降りたスキャンは、見ていないだけの相方を「居なかった」と読む."""
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning
    JobStore(db).request_cancel(ctx.job_id)

    scanner.scan(ctx, fd, volume_id, profile)

    assert all(v is None for v in _keys(db).values())


def test_a_scan_cancelled_right_after_seeing_the_pair_still_writes_no_mark(canon_scanning, db):
    """両方を集め終えていても、**数え終えたことにはならない**なら印は書かない.

    最初のファイルで降りるケース（`eligible` が空のまま）だけだと、印を書く
    処理そのものを丸ごと `counted` の外に出す変異を見逃す —— `eligible` が
    空なら、門の有無に関わらず書くものが無い。ここでは両方を列挙し終えた
    直後に降りることで、`eligible` が揃っていることと「数え終えた」ことを
    区別する。
    """
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning
    calls = 0

    def fake_cancelled() -> bool:
        nonlocal calls
        calls += 1
        # 2 ファイル分の列挙（各 1 回ずつ判定される）は通し、その後で降りる。
        return calls > 2

    ctx.cancelled = fake_cancelled

    scanner.scan(ctx, fd, volume_id, profile)

    assert all(v is None for v in _keys(db).values())


def test_a_cancelled_rescan_keeps_the_two_marks_in_step(canon_scanning, db):
    """途中で降りたスキャンが、組の片方の印だけを落とさない.

    `_reconcile_entry` の `UPDATE` は取り込み待ち（`media_file_id` が NULL）の行も
    通る。ここで印を無条件に消すと、**末尾の `_mark_copresence` に届かない**
    スキャンが 2 つの行を非対称にし、その組は二度と成立しない（書き直せるのは
    完走したスキャンだけ）。
    """
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning
    scanner.scan(ctx, fd, volume_id, profile)
    before = _keys(db)
    assert before["DCIM/100CANON/IMG_0001.JPG"] is not None
    calls = 0

    def fake_cancelled() -> bool:
        nonlocal calls
        calls += 1
        # 1 件目を照合したところで降りる（2 件目には届かない）。
        return calls > 1

    ctx.cancelled = fake_cancelled

    scanner.scan(ctx, fd, volume_id, profile)

    keys = _keys(db)
    assert keys["DCIM/100CANON/IMG_0001.JPG"] == keys["DCIM/100CANON/IMG_0001.CR2"]
    assert keys == before


def test_a_profile_without_stacking_gets_no_marks(scanning, db):
    """`stack.enabled = false` のプロファイルには印を書かない.

    印は組の身元の材料でしかないので、組を持たない機種（DJI は RAW を書かない）に
    書いても意味が無い。書くと、`stack` を後から有効にしたときに**その時点で並んで
    いただけの 2 ファイル**が、同席を確かめた組として通ってしまう。
    """
    scanner, ctx, fd, volume_id, profile, card = scanning
    assert profile.definition.stack.enabled is False
    # `scan.extensions` には両方入っているので、門が無ければ 2 種類そろう。
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.JPG").write_bytes(b"j" * 50)

    scanner.scan(ctx, fd, volume_id, profile)

    assert set(_keys(db)) == {
        "DCIM/DJI_001/DJI_20260817143000_0001_D.MP4",
        "DCIM/DJI_001/DJI_20260817143000_0001_D.JPG",
    }
    assert all(v is None for v in _keys(db).values())


def test_a_disabled_rule_with_extensions_still_gets_no_marks(canon_scanning, db):
    """`enabled` と `extensions` は `StackRule` の別々の欄。**両方を見る**.

    拡張子だけを見る門は「無効な規則の拡張子」を通す。組を持たない機種として
    保存された規則が拡張子を残していれば、印だけが書かれ続ける。
    """
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning
    disabled = replace(
        profile,
        definition=replace(
            profile.definition, stack=StackRule(enabled=False, extensions=("JPG", "CR2"))
        ),
    )

    scanner.scan(ctx, fd, volume_id, disabled)

    assert all(v is None for v in _keys(db).values())


def test_an_extension_outside_the_stack_rule_gets_no_mark(canon_scanning, db):
    """`stack.extensions` の外は相方の候補にならないので、同席も数えない.

    Canon の `scan.extensions` には `MOV` が入っているが `stack.extensions` には
    無い。門が拡張子を見ないと、`IMG_0002.JPG` と `IMG_0002.MOV` が「2 種類」に
    数えられて印を持ち、動画と写真の組が宣言される。
    """
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    assert "MOV" in profile.definition.scan.extensions
    assert "MOV" not in profile.definition.stack.extensions
    (card / "DCIM" / "100CANON" / "IMG_0002.JPG").write_bytes(b"j" * 50)
    (card / "DCIM" / "100CANON" / "IMG_0002.MOV").write_bytes(b"m" * 60)

    scanner.scan(ctx, fd, volume_id, profile)

    keys = _keys(db)
    assert keys["DCIM/100CANON/IMG_0002.JPG"] is None
    assert keys["DCIM/100CANON/IMG_0002.MOV"] is None
    # 規則の中の組（JPG + CR2）は、これまでどおり印を持つ。
    assert keys["DCIM/100CANON/IMG_0001.JPG"] is not None
