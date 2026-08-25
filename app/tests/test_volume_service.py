import os
import threading
from dataclasses import replace

import pytest

from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.volumes import StaleSelection, VolumeBusy, VolumeService


def service(db, broker):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    return VolumeService(db, registry, broker)


def test_refresh_registers_the_device_the_volume_and_the_presence(db, broker):
    views = service(db, broker).refresh()
    assert len(views) == 1
    assert db.execute("SELECT count(*) FROM source_device").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM volume_instance").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM volume_presence").fetchone()[0] == 1


def test_the_profile_is_resolved_from_the_contents(db, broker):
    view = service(db, broker).refresh()[0]
    assert view.profile_slug == "dji-osmo"
    assert view.provisional is False


def test_an_empty_card_is_provisional(db, broker, fake_card):
    for path in (fake_card / "DCIM" / "DJI_001").iterdir():
        path.unlink()
    view = service(db, broker).refresh()[0]
    assert view.profile_slug == "dji-osmo"
    assert view.provisional is True


def test_a_first_sighting_is_never_high_confidence(db, broker):
    """初めて見るカードは §12.1 のとおり必ず承認を待つ."""
    view = service(db, broker).refresh()[0]
    assert view.identity_confidence == "low"
    assert view.trusted is False


def test_a_returning_card_with_a_matching_manifest_becomes_high(db, broker):
    svc = service(db, broker)
    svc.refresh()
    assert svc.refresh()[0].identity_confidence == "high"


def test_without_a_remembered_manifest_survival_alone_is_not_enough(db, broker):
    """記憶が無いカードは、既知ファイルが残っていても high にしない（§12.1）.

    残存率の判定に落ちると、manifest を一度も記録していない相手に対して
    「前回と連続的だ」と主張することになる。
    """
    svc = service(db, broker)
    view = svc.refresh()[0]
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES ('e1', ?, 'DCIM/DJI_001/DJI_20260817143000_0001_D.MP4', 100, 1, 'abc', 1,"
        " 'published', '2026-01-01T00:00:00+00:00')",
        (view.volume_instance_id,),
    )
    db.execute("UPDATE volume_instance SET content_manifest_digest = NULL")
    assert svc.refresh()[0].identity_confidence == "low"


def test_devices_that_differ_only_by_product_are_not_merged(db, broker, volumes):
    """Osmo の serial は機種の既定値なので、product を落とすと 2 台が 1 台になる."""
    first = volumes[0]
    volumes.append(
        replace(
            first,
            volume_key="8:176",
            major=8,
            minor=176,
            fs_uuid="AAAA-BBBB",
            usb=replace(first.usb, product="OsmoPocket4-XYZ789"),
        )
    )
    service(db, broker).refresh()
    assert db.execute("SELECT count(*) FROM source_device").fetchone()[0] == 2


def test_a_reformatted_card_drops_back_to_low(db, broker, fake_card):
    """UUID を保持したまま中身が入れ替わったカードを high のままにしない."""
    svc = service(db, broker)
    svc.refresh()
    svc.refresh()
    (fake_card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()
    (fake_card / "DCIM" / "DJI_001").rmdir()
    (fake_card / "DCIM" / "OTHER").mkdir()
    assert svc.refresh()[0].identity_confidence == "low"


def test_a_card_without_a_uuid_is_always_low(db, broker, volumes):
    volumes[0] = replace(volumes[0], fs_uuid="")
    svc = service(db, broker)
    svc.refresh()
    assert svc.refresh()[0].identity_confidence == "low"


def test_profile_match_does_not_raise_identity_confidence(db, broker, fake_card):
    """中身が DJI のファイルであることは「同じカードだ」の証明にならない."""
    view = service(db, broker).refresh()[0]
    assert view.profile_slug == "dji-osmo"
    assert view.provisional is False
    assert view.identity_confidence == "low"  # 初回なので low のまま


def test_the_volume_is_closed_after_probing(db, broker):
    """対象を確かめたら dirfd を握り続けない. 明示的に開くまでは閉じておく."""
    svc = service(db, broker)
    svc.refresh()
    assert svc.opened() == []


def test_open_and_release_manage_the_dirfd(db, broker):
    """release でその場で閉じる. 次のジョブのために取っておかない."""
    svc = service(db, broker)
    view = svc.refresh()[0]
    handle = svc.open(view.selection)
    assert svc.opened() == [view.volume_instance_id]
    assert "DCIM" in os.listdir(handle.dirfd)
    svc.release(view.selection)
    assert svc.opened() == []
    # os.listdir(-1) はカレントディレクトリを黙って返すので、閉じたことは
    # 契約（closed と dirfd の無効化）で確かめる。
    assert handle.closed is True
    assert handle.dirfd == -1


def test_opening_the_same_volume_twice_is_refused(db, broker):
    """observation は媒体の同一性を保証しないので、黙って共有しない."""
    svc = service(db, broker)
    selection = svc.refresh()[0].selection
    svc.open(selection)
    with pytest.raises(VolumeBusy):
        svc.open(selection)
    svc.release(selection)


def test_a_selection_from_an_older_generation_is_refused(db, broker):
    """抜き差しで /dev/sdX が再利用され、別のカードが同じノードに来る."""
    svc = service(db, broker)
    view = svc.refresh()[0]
    observation = replace(
        view.selection.observation, generation=view.selection.observation.generation - 1
    )
    with pytest.raises(StaleSelection):
        svc.open(replace(view.selection, observation=observation))


def test_a_selection_from_a_previous_mountd_run_is_refused(db, broker):
    """generation は mountd の再起動で 0 に戻る. epoch が無いと偶然一致する."""
    svc = service(db, broker)
    view = svc.refresh()[0]
    observation = replace(view.selection.observation, broker_epoch="a-previous-run")
    with pytest.raises(StaleSelection):
        svc.open(replace(view.selection, observation=observation))


def test_closing_a_volume_a_job_is_using_is_refused(db, broker):
    """実行中のワーカーの fd を、API の別スレッドから閉じない."""
    svc = service(db, broker)
    view = svc.refresh()[0]
    svc.open(view.selection)
    with pytest.raises(VolumeBusy):
        svc.close(view.volume_instance_id)
    svc.release(view.selection)
    svc.close(view.volume_instance_id)


def test_the_broker_is_not_called_concurrently(db, broker):
    """1 本の SOCK_SEQPACKET を同時に使うと応答を取り違える."""
    svc = service(db, broker)
    svc.refresh()
    errors = []

    def hammer():
        try:
            for _ in range(20):
                svc.refresh()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []


def test_the_same_card_keeps_its_identity_and_presence_across_refreshes(db, broker):
    """列挙のたびに presence を増やすと、キュー投入時と実行時で別物になる."""
    svc = service(db, broker)
    first = svc.refresh()[0]
    second = svc.refresh()[0]
    assert first.volume_instance_id == second.volume_instance_id
    assert first.selection == second.selection
    assert db.execute("SELECT count(*) FROM volume_presence").fetchone()[0] == 1


def test_a_selection_survives_intervening_refreshes(db, broker):
    """GET /devices → scan → import の間に何度 refresh が挟まっても開ける."""
    svc = service(db, broker)
    selection = svc.selection_for(svc.refresh()[0].volume_instance_id)
    svc.refresh()
    svc.refresh()
    handle = svc.open(selection)
    assert "DCIM" in os.listdir(handle.dirfd)
    svc.release(selection)


def test_a_vanished_presence_is_detached(db, broker, volumes):
    """抜いたポートの行が live のままだと、同一 identity の同時接続を誤検出する."""
    svc = service(db, broker)
    svc.refresh()
    volumes.clear()
    svc.refresh()
    rows = db.execute("SELECT count(*) AS n FROM volume_presence WHERE detached_at IS NULL")
    assert rows.fetchone()["n"] == 0


def test_reinserting_into_another_port_does_not_pin_confidence_low(db, broker, volumes):
    """抜いて別ポートへ挿し直したカードが、以後ずっと low のままにならない."""
    svc = service(db, broker)
    svc.refresh()
    svc.refresh()
    volumes[0] = replace(
        volumes[0],
        major=8,
        minor=176,
        volume_key="8:176",
        generation=volumes[0].generation + 1,
    )
    assert svc.refresh()[0].identity_confidence == "high"


def test_no_handle_survives_a_finished_job(db, broker):
    """使い終わった handle が残っていると、次のジョブがそれを掴む."""
    svc = service(db, broker)
    selection = svc.refresh()[0].selection
    svc.open(selection)
    svc.release(selection)
    svc.refresh()
    assert svc.opened() == []


def test_two_cards_with_the_same_identity_are_both_low_on_the_first_sighting(db, broker, volumes):
    """判定を live 集合の確定より前に行うと、先に見た方だけが high になる.

    先に 1 本だけで high を作っておき、**2 本目が現れた最初の refresh** を見る。
    最初から 2 本を返すと、初回は remembered digest が無くて両方 low になり、
    反映しながら判定する実装でも通ってしまう。
    """
    svc = service(db, broker)
    svc.refresh()
    assert svc.refresh()[0].identity_confidence == "high"

    volumes.append(replace(volumes[0], major=8, minor=176, volume_key="8:176"))
    views = svc.refresh()
    assert len(views) == 2
    assert [view.identity_confidence for view in views] == ["low", "low"]


def a_swapped_card(tmp_path):
    """同じ UUID・容量だが中身の違うカード（複製・再フォーマット相当）."""
    other = tmp_path / "swapped"
    (other / "DCIM" / "100CANON").mkdir(parents=True)
    (other / "DCIM" / "100CANON" / "IMG_0001.JPG").write_bytes(b"other camera")
    return other


def test_a_swapped_card_is_judged_on_its_own_contents(db, broker, mount_manager, tmp_path):
    """observation が完全一致でも、開いてある dirfd を使い回してはいけない.

    mountd の generation は「観測した集合の指紋が変わったとき」だけ進む
    (mountd/server.py::_observe)。Phase 1 は polling なので、同じ UUID・型・
    容量のカードが同じ major:minor で観測の合間に差し替わると、generation も
    epoch も据え置きのままになる。既存 fd は open_tree で切り離した旧カードを
    指したままなので、流用すると旧カードの中身で新カードを判定する。
    """
    svc = service(db, broker)
    selection = svc.refresh()[0].selection
    handle = svc.open(selection)
    svc.release(selection)

    # ジョブが終われば handle はその場で閉じる。取っておくと、次のジョブが
    # 差し替え後もこの fd（＝旧カード）を読むことになる。
    assert handle.closed is True

    # 新しく open するものだけが差し替わる（世代も epoch も据え置き）。
    mount_manager.target = a_swapped_card(tmp_path)

    # 差し替え後のカードは Canon の構成（DCIM/100CANON/IMG_0001.JPG）。
    # **旧 dirfd を流用していれば DJI のファイルが見えて dji-osmo のまま**に
    # なる。新しく開いていれば canon-eos に変わる。
    view = svc.refresh()[0]
    assert view.profile_slug == "canon-eos", "旧カードの中身で判定している"
    # 中身の指紋が変わったので、前回との連続性は言えない（§8）。
    assert view.identity_confidence == "low"

    # 判定だけでなく、次に開く dirfd も新しいカードでなければならない。
    # 画面には新カードが見えるのに取り込むのは旧カード、が最悪の食い違い。
    current = svc.open(view.selection)
    dcim = os.open("DCIM", os.O_RDONLY | os.O_DIRECTORY, dir_fd=current.dirfd)
    try:
        assert "100CANON" in os.listdir(dcim)
        assert "DJI_001" not in os.listdir(dcim)
    finally:
        os.close(dcim)
        svc.release(view.selection)


def test_a_card_without_a_uuid_keeps_its_selection_across_refreshes(db, broker, volumes):
    """毎 refresh で新しい volume_instance を作ると、直前に選んだ selection が
    次の refresh で detached になる."""
    volumes[0] = replace(volumes[0], fs_uuid="")
    svc = service(db, broker)
    first = svc.refresh()[0]
    second = svc.refresh()[0]
    assert first.volume_instance_id == second.volume_instance_id
    assert first.selection == second.selection
    assert db.execute("SELECT count(*) FROM volume_instance").fetchone()[0] == 1
    handle = svc.open(first.selection)
    assert "DCIM" in os.listdir(handle.dirfd)
    svc.release(first.selection)


def test_a_card_without_a_uuid_never_becomes_confident(db, broker, volumes):
    """**`low` には 2 種類ある。** 何度観測しても `high` にならないものがある.

    `fs_uuid` が無い媒体は「前回と同じカードだ」と言える根拠が無いので、
    `_identity_confidence` は毎回 `low` を返す。`watcher.py` の `CANDIDATES` は
    `high` を要求するので、**このカードは信頼登録しても自動取り込みされない**。
    画面の文言（`autoImportOutlook` の `pending`）が「確かめられしだい取り込みます」と
    約束してはいけないのは、この経路があるため。
    """
    volumes[0] = replace(volumes[0], fs_uuid="")
    svc = service(db, broker)
    for _ in range(3):
        assert svc.refresh()[0].identity_confidence == "low"


def test_trust_is_recorded_and_reported(db, broker):
    svc = service(db, broker)
    view = svc.refresh()[0]
    assert view.trusted is False
    svc.trust(view.volume_instance_id)
    assert svc.refresh()[0].trusted is True


def test_the_serial_alone_does_not_identify_the_device(db, broker):
    """Osmo の serial は Linux ガジェットの既定値だった（Phase 0 実測）."""
    svc = service(db, broker)
    svc.refresh()
    row = db.execute("SELECT * FROM source_device").fetchone()
    assert row["serial"] == "123456789ABCDEF"
    assert row["usb_product_id"] == "0020"


def _seed_entries(db, volume_instance_id: str, states: list[str]) -> None:
    """`scan` が作る行を、状態だけ指定して並べる."""
    for index, state in enumerate(states):
        db.execute(
            "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes,"
            " mtime_ns, quick_fingerprint, fingerprint_version, state, observed_at)"
            " VALUES (?, ?, ?, 1, 1, 'x', 1, ?, '2026-08-24T00:00:00Z')",
            (f"entry-{index}", volume_instance_id, f"DCIM/{index}.MP4", state),
        )


def test_a_card_nobody_counted_yet_is_told_apart_from_an_empty_one(db, broker):
    """挿した直後は「0 件」ではなく「まだ数えていない」."""
    view = service(db, broker).refresh()[0]
    assert view.scanned_at is None
    assert view.pending_count == 0


def test_pending_counts_exactly_what_import_would_carry(db, broker):
    svc = service(db, broker)
    view = svc.refresh()[0]
    _seed_entries(db, view.volume_instance_id, ["seen", "seen", "published", "failed"])
    # `Importer.run` が拾うのは seen と failed だけ。
    assert svc.refresh()[0].pending_count == 3


def test_the_rows_of_a_scan_are_not_the_proof_that_it_ran(db, broker):
    """**「数えたか」を行数から導かない**（§11 の `scanned_at`）.

    一致するファイルが無いカードはスキャンが完全に成功しても行が 0 件なので、
    行から導くと「まだ数えていない」から永久に出られない。逆に、途中で降りた
    スキャンは行を残すが数え終わっていない。**数えた事実は
    `volume_instance.scanned_at` にしか無い。**
    """
    svc = service(db, broker)
    view = svc.refresh()[0]
    _seed_entries(db, view.volume_instance_id, ["published"])
    assert svc.refresh()[0].scanned_at is None

    db.execute(
        "UPDATE volume_instance SET scanned_at = '2026-08-25T09:00:00Z' WHERE id = ?",
        (view.volume_instance_id,),
    )
    assert svc.refresh()[0].scanned_at == "2026-08-25T09:00:00Z"


def test_a_card_held_by_a_running_job_is_busy(db, broker):
    svc = service(db, broker)
    view = svc.refresh()[0]
    assert view.selection is not None
    svc.open(view.selection)
    try:
        assert svc.refresh()[0].busy is True
    finally:
        svc.release(view.selection)
    assert svc.refresh()[0].busy is False


_KNOWN = "DCIM/DJI_001/DJI_20260817143000_0001_D.MP4"


def _remember_a_published_observation(db, volume_instance_id: str) -> None:
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES ('known', ?, ?, 100, 1, 'abc', 1, 'published', '2026-01-01T00:00:00+00:00')",
        (volume_instance_id, _KNOWN),
    )


def test_a_card_still_holding_its_known_files_stays_high(db, broker, fake_card):
    """中身が増えても、記録した観測がカードに残っていれば連続的とみなす.

    **残存の判定が「在る」を「無い」と読むと**、承認済みのカードが挿し直すたびに
    `low` へ落ち、自動取り込みが始まらなくなる。`_known_files_survive` の真偽を
    ここで固定する（この向きを 1 本も見ていなかった）。
    """
    svc = service(db, broker)
    svc.refresh()
    view = svc.refresh()[0]
    _remember_a_published_observation(db, view.volume_instance_id)
    # manifest を変えて、残存率の判定まで落とす（一致していれば見ずに high）。
    (fake_card / "DCIM" / "DJI_001" / "DJI_20260817143001_0002_D.MP4").write_bytes(b"x")

    assert svc.refresh()[0].identity_confidence == "high"


def test_a_card_that_lost_its_known_files_drops_to_low(db, broker, fake_card):
    """記録した観測がカードから消えていれば、連続的とは言えない."""
    svc = service(db, broker)
    svc.refresh()
    view = svc.refresh()[0]
    _remember_a_published_observation(db, view.volume_instance_id)
    (fake_card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").unlink()
    (fake_card / "DCIM" / "DJI_001" / "DJI_20260817143001_0002_D.MP4").write_bytes(b"x")

    assert svc.refresh()[0].identity_confidence == "low"
