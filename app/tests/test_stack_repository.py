"""第 2 パスが使う問い合わせと記録（§9.11）.

**記録の条件は 3 経路（guard / stacked / skipped）で同じにする。** 片方だけ
弱くすると、そこが抜け道になる。
"""

import pytest

from mediaferry.clock import now_iso
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository
from mediaferry.db.jobs import JobStore, LeaseLost
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import StackGroupChanged, UploadRepository
from mediaferry.ids import new_id

from .test_schema_artifacts import a_media_file
from .test_schema_sources import PLACEHOLDER_DEFINITION_JSON, a_volume
from .test_schema_uploads import a_destination, an_upload

EPOCH = 1


@pytest.fixture
def world(db):
    """canon-eos の JPG + CR2 が、同じカードから取り込まれて送信済みの状態."""
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    profile = registry.current("canon-eos")
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    dest = a_destination(db)

    records = {}
    for extension in ("JPG", "CR2"):
        media = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/canon-eos/DCIM/100CANON/IMG_1234.{extension}",
            kind="photo",
            duration_seconds=None,
            captured_at="2026-08-19T10:30:00+09:00",
            captured_at_source="exif",
        )
        db.execute(
            "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
            " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at)"
            " VALUES (?, ?, ?, 10, 1, 'abc', 1, ?, 'published', ?)",
            (new_id(), volume, f"DCIM/100CANON/IMG_1234.{extension}", media, now_iso()),
        )
        records[extension] = an_upload(
            db,
            dest,
            media,
            state="complete",
            origin="created_by_us",
            destination_revision_id=dest[1],
            remote_asset_id=f"asset-{extension}",
        )

    store = JobStore(db)
    store.enqueue("upload", {"destination_id": dest[0]})
    ctx = store.claim_next()
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(b"0" * 32)))
    repo = UploadRepository(db, registry, destinations)
    return repo, ctx, dest[0], profile.revision_id, records


def rows(db):
    return {row["id"]: row for row in db.execute("SELECT * FROM upload_record")}


def test_the_batch_returns_the_unevaluated_complete_records(world, db):
    repo, _, destination_id, _, records = world
    found = repo.unstacked_batch(destination_id, EPOCH, "", 50)
    assert {row["id"] for row in found} == set(records.values())


def test_the_batch_is_a_keyset_so_a_left_over_row_does_not_loop(world, db):
    """5xx で未評価のまま残した行があっても、次のバッチは前へ進む."""
    repo, _, destination_id, _, _ = world
    first = repo.unstacked_batch(destination_id, EPOCH, "", 1)
    second = repo.unstacked_batch(destination_id, EPOCH, first[0]["id"], 1)
    assert second and second[0]["id"] > first[0]["id"]


def test_records_of_an_old_epoch_are_not_extracted(world, db):
    """旧 epoch の `complete` は無効化されずに残る（§8）が、別ライブラリの履歴."""
    repo, _, destination_id, _, _ = world
    assert repo.unstacked_batch(destination_id, EPOCH + 1, "", 50) == []


def test_invalidated_records_are_not_extracted(world, db):
    repo, _, destination_id, _, records = world
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = '編集された'",
        (now_iso(),),
    )
    assert repo.unstacked_batch(destination_id, EPOCH, "", 50) == []


def test_records_that_are_not_complete_are_not_extracted(world, db):
    repo, _, destination_id, _, records = world
    db.execute("UPDATE upload_record SET state = 'pending', destination_revision_id = NULL")
    assert repo.unstacked_batch(destination_id, EPOCH, "", 50) == []


def test_evaluated_records_are_not_extracted_again(world, db):
    repo, ctx, destination_id, revision, records = world
    repo.mark_skipped(
        ctx, rows(db)[records["JPG"]], destination_id, EPOCH, revision, "相方が見つからない"
    )
    found = repo.unstacked_batch(destination_id, EPOCH, "", 50)
    assert {row["id"] for row in found} == {records["CR2"]}


def test_the_observations_of_a_media_are_all_returned(world, db):
    """**1 つに絞らない**（`observed_at` は再スキャンで動く）."""
    repo, _, _, _, records = world
    media = rows(db)[records["JPG"]]["media_file_id"]
    assert [row["rel_path"] for row in repo.sources_of(media)] == ["DCIM/100CANON/IMG_1234.JPG"]


def test_only_published_observations_are_returned(world, db):
    repo, _, _, _, records = world
    media = rows(db)[records["JPG"]]["media_file_id"]
    db.execute("UPDATE source_entry SET state = 'seen' WHERE media_file_id = ?", (media,))
    assert repo.sources_of(media) == []


def test_siblings_are_found_by_the_stem_prefix(world, db):
    repo, _, _, _, records = world
    volume = db.execute("SELECT volume_instance_id FROM source_entry").fetchone()[0]
    found = repo.siblings_on_card(volume, "DCIM/100CANON/IMG_1234.")
    assert {row["rel_path"] for row in found} == {
        "DCIM/100CANON/IMG_1234.JPG",
        "DCIM/100CANON/IMG_1234.CR2",
    }


def test_siblings_do_not_leak_into_a_longer_name(world, db):
    """`IMG_1234.` の範囲に `IMG_12345.JPG` を入れない.

    **範囲だけで落ちることを見る。** 他の条件（published / media_file_id）でも
    落ちる形にすると、上端を外す変異が素通りする。
    """
    repo, _, _, _, _ = world
    volume = db.execute("SELECT volume_instance_id FROM source_entry").fetchone()[0]
    media = db.execute("SELECT media_file_id FROM source_entry LIMIT 1").fetchone()[0]
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at)"
        " VALUES (?, ?, 'DCIM/100CANON/IMG_12345.JPG', 10, 1, 'abc', 1, ?, 'published', ?)",
        (new_id(), volume, media, now_iso()),
    )
    found = repo.siblings_on_card(volume, "DCIM/100CANON/IMG_1234.")
    assert all("IMG_12345" not in row["rel_path"] for row in found)


def test_siblings_do_not_leak_from_another_directory(db, world):
    """**下端も要る。** 範囲を開くと、同じカードの別フォルダまで拾う。"""
    repo, _, _, _, _ = world
    volume = db.execute("SELECT volume_instance_id FROM source_entry").fetchone()[0]
    media = db.execute("SELECT media_file_id FROM source_entry LIMIT 1").fetchone()[0]
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at)"
        " VALUES (?, ?, 'DCIM/099CANON/IMG_0001.JPG', 10, 1, 'abc', 1, ?, 'published', ?)",
        (new_id(), volume, media, now_iso()),
    )
    found = repo.siblings_on_card(volume, "DCIM/100CANON/IMG_1234.")
    assert all("099CANON" not in row["rel_path"] for row in found)


def test_siblings_of_another_card_are_not_returned(db, world):
    repo, _, _, _, _ = world
    other_volume = a_volume(db, None, fs_uuid="0000-0001")
    media = db.execute("SELECT media_file_id FROM source_entry LIMIT 1").fetchone()[0]
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at)"
        " VALUES (?, ?, 'DCIM/100CANON/IMG_1234.CR2', 10, 1, 'abc', 1, ?, 'published', ?)",
        (new_id(), other_volume, media, now_iso()),
    )
    volume = db.execute(
        "SELECT volume_instance_id FROM source_entry WHERE volume_instance_id <> ?",
        (other_volume,),
    ).fetchone()[0]
    found = repo.siblings_on_card(volume, "DCIM/100CANON/IMG_1234.")
    assert len(found) == 2


def test_the_guard_passes_when_nothing_moved(world, db):
    repo, ctx, destination_id, revision, records = world
    members = list(rows(db).values())
    repo.guard_stack_group(ctx, members, destination_id, EPOCH, revision)


def test_the_guard_refuses_when_the_destination_was_repointed(world, db):
    """**開始後に向き替えられたら止める。** 進行中の無効化はここには効かない."""
    repo, ctx, destination_id, revision, _ = world
    members = list(rows(db).values())
    _repoint(db, destination_id)
    with pytest.raises(StackGroupChanged, match="向き先"):
        repo.guard_stack_group(ctx, members, destination_id, EPOCH, revision)


def test_the_guard_refuses_when_the_profile_revision_moved(world, db):
    repo, ctx, destination_id, revision, _ = world
    members = list(rows(db).values())
    _advance_profile(db, revision)
    with pytest.raises(StackGroupChanged, match="プロファイル"):
        repo.guard_stack_group(ctx, members, destination_id, EPOCH, revision)


def test_the_guard_refuses_when_a_member_was_invalidated(world, db):
    repo, ctx, destination_id, revision, records = world
    members = list(rows(db).values())
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'x' WHERE id = ?",
        (now_iso(), records["CR2"]),
    )
    with pytest.raises(StackGroupChanged):
        repo.guard_stack_group(ctx, members, destination_id, EPOCH, revision)


def test_the_guard_refuses_when_the_remote_asset_changed(world, db):
    repo, ctx, destination_id, revision, records = world
    members = list(rows(db).values())
    db.execute("UPDATE upload_record SET remote_asset_id = 'other' WHERE id = ?", (records["CR2"],))
    with pytest.raises(StackGroupChanged):
        repo.guard_stack_group(ctx, members, destination_id, EPOCH, revision)


def test_a_lost_lease_refuses_the_guard(world, db):
    repo, ctx, destination_id, revision, _ = world
    _expire_lease(db)
    with pytest.raises(LeaseLost):
        repo.guard_stack_group(ctx, [], destination_id, EPOCH, revision)


def test_marking_stacked_writes_every_member(world, db):
    repo, ctx, destination_id, revision, _ = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    assert {row["stack_state"] for row in rows(db).values()} == {"stacked"}
    assert {row["remote_stack_id"] for row in rows(db).values()} == {"stack-1"}


def test_marking_stacked_upgrades_a_previously_skipped_partner(world, db):
    """見送りは「今は組めない」の記録であって永久の拒否ではない（§9.11）."""
    repo, ctx, destination_id, revision, records = world
    repo.mark_skipped(
        ctx, rows(db)[records["JPG"]], destination_id, EPOCH, revision, "相方が見つからない"
    )
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    assert rows(db)[records["JPG"]]["stack_state"] == "stacked"
    assert rows(db)[records["JPG"]]["stack_reason"] is None


def test_nothing_is_written_when_one_member_cannot_be_marked(world, db):
    """**一部だけ書かない。** 例外で取引ごと巻き戻す."""
    repo, ctx, destination_id, revision, records = world
    members = list(rows(db).values())
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'x' WHERE id = ?",
        (now_iso(), records["CR2"]),
    )
    with pytest.raises(StackGroupChanged):
        repo.mark_stacked(ctx, members, destination_id, EPOCH, revision, "stack-1")
    assert rows(db)[records["JPG"]]["stack_state"] is None


def test_a_profile_edit_after_the_post_refuses_the_record(world, db):
    """外部副作用は済んでいる。**書かずに、次の送信の回収経路へ渡す**（§9.11）."""
    repo, ctx, destination_id, revision, records = world
    members = list(rows(db).values())
    _advance_profile(db, revision)
    with pytest.raises(StackGroupChanged):
        repo.mark_stacked(ctx, members, destination_id, EPOCH, revision, "stack-1")
    assert rows(db)[records["JPG"]]["stack_state"] is None


def test_a_repoint_after_the_post_refuses_the_record(world, db):
    repo, ctx, destination_id, revision, records = world
    members = list(rows(db).values())
    _repoint(db, destination_id)
    with pytest.raises(StackGroupChanged):
        repo.mark_stacked(ctx, members, destination_id, EPOCH, revision, "stack-1")
    assert rows(db)[records["JPG"]]["stack_state"] is None


def test_marking_skipped_records_the_reason(world, db):
    repo, ctx, destination_id, revision, records = world
    repo.mark_skipped(
        ctx, rows(db)[records["JPG"]], destination_id, EPOCH, revision, "相方が見つからない"
    )
    assert rows(db)[records["JPG"]]["stack_reason"] == "相方が見つからない"


def test_a_profile_edit_before_the_skip_is_written_refuses_it(world, db):
    """**旧規則の判断を新しい版の世界へ残さない。**

    1. こちらが旧版の規則で「見送り」と判断する
    2. 別の接続が新しい版を出し、既存の見送りを未評価へ戻して commit
    3. こちらが書く → 戻す対象に入っていないので、二度と評価されない
    """
    repo, ctx, destination_id, revision, records = world
    record = rows(db)[records["JPG"]]
    _advance_profile(db, revision)
    with pytest.raises(StackGroupChanged):
        repo.mark_skipped(ctx, record, destination_id, EPOCH, revision, "相方が見つからない")
    assert rows(db)[records["JPG"]]["stack_state"] is None


def test_a_repoint_before_the_skip_is_written_refuses_it(world, db):
    repo, ctx, destination_id, revision, records = world
    record = rows(db)[records["JPG"]]
    _repoint(db, destination_id)
    with pytest.raises(StackGroupChanged):
        repo.mark_skipped(ctx, record, destination_id, EPOCH, revision, "相方が見つからない")


def test_marking_skipped_needs_the_lease(world, db):
    """相手に触らない見送りも、リースの下で書く（大量にあると失効しうる）."""
    repo, ctx, destination_id, revision, records = world
    record = rows(db)[records["JPG"]]
    _expire_lease(db)
    with pytest.raises(LeaseLost):
        repo.mark_skipped(ctx, record, destination_id, EPOCH, revision, "相方が見つからない")


def test_a_changed_remote_asset_id_refuses_the_skip(world, db):
    """送った相手と、記録する相手が同じであることまで見る."""
    repo, ctx, destination_id, revision, records = world
    record = rows(db)[records["JPG"]]
    db.execute("UPDATE upload_record SET remote_asset_id = 'other' WHERE id = ?", (records["JPG"],))
    with pytest.raises(StackGroupChanged):
        repo.mark_skipped(ctx, record, destination_id, EPOCH, revision, "相方が見つからない")


def _repoint(db, destination_id):
    """向き先を別ライブラリへ変える（epoch が進む）."""
    revision_id = new_id()
    credential = db.execute(
        "SELECT id FROM destination_credential WHERE destination_id = ?", (destination_id,)
    ).fetchone()[0]
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch, base_url,"
        " credential_id, created_at) VALUES (?, ?, 2, 2, 'http://other.invalid', ?, ?)",
        (revision_id, destination_id, credential, now_iso()),
    )
    db.execute(
        "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
        (revision_id, destination_id),
    )


def _advance_profile(db, revision_id):
    """プロファイルの版を進める（規則が変わりうる）."""
    profile_id = db.execute(
        "SELECT profile_id FROM profile_revision WHERE id = ?", (revision_id,)
    ).fetchone()[0]
    new_revision = new_id()
    db.execute(
        "INSERT INTO profile_revision (id, profile_id, revision, definition_json, schema_version,"
        " created_at) VALUES (?, ?, 99, ?, 1, ?)",
        (new_revision, profile_id, PLACEHOLDER_DEFINITION_JSON, now_iso()),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
        (new_revision, profile_id),
    )


def _expire_lease(db):
    db.execute("UPDATE job SET lease_expires_at = '2000-01-01T00:00:00+00:00'")


def test_the_guard_refuses_a_disabled_destination(world, db):
    """**claim を使わない経路にも、同じ安全条件が要る。**

    `claim_next` は `enabled = 1 AND archived_at IS NULL` を見る。第 2 パスは
    `complete` を扱うので claim を通らない —— guard が代わりに見なければ、
    無効にした宛先へ `POST` / `PUT` を出す。
    """
    repo, ctx, destination_id, revision, _ = world
    members = list(rows(db).values())
    db.execute("UPDATE upload_destination SET enabled = 0 WHERE id = ?", (destination_id,))
    with pytest.raises(StackGroupChanged, match="宛先"):
        repo.guard_stack_group(ctx, members, destination_id, EPOCH, revision)


def test_the_guard_refuses_an_archived_destination(world, db):
    """**`archived_at` だけを見る**（`enabled` は触らない）.

    現行の `archive()` は `enabled = 0` も立てるので、実運用ではどちらの条件でも
    止まる。ここで条件を分けて見るのは、`claim_next` と同じ安全条件を保つため
    —— 将来「保管するが無効にしない」書き手ができても閉じている。
    """
    repo, ctx, destination_id, revision, _ = world
    members = list(rows(db).values())
    db.execute(
        "UPDATE upload_destination SET archived_at = ? WHERE id = ?",
        (now_iso(), destination_id),
    )
    with pytest.raises(StackGroupChanged, match="宛先"):
        repo.guard_stack_group(ctx, members, destination_id, EPOCH, revision)


def test_marking_refuses_a_disabled_destination(world, db):
    """記録の側も同じ条件（片方だけ弱くすると抜け道になる）."""
    repo, ctx, destination_id, revision, records = world
    members = list(rows(db).values())
    db.execute("UPDATE upload_destination SET enabled = 0 WHERE id = ?", (destination_id,))
    with pytest.raises(StackGroupChanged):
        repo.mark_stacked(ctx, members, destination_id, EPOCH, revision, "stack-1")
    assert rows(db)[records["JPG"]]["stack_state"] is None


def test_a_changed_remote_asset_id_reopens_the_stack(world, db):
    """**スタックは「その `remote_asset_id` を送った結果」。**

    再確認で資産が消えた（`NULL`）／別 ID になったら、その結果はもう現在の姿を
    表さない。3 列を未評価へ戻さないと、`unstacked_batch` が拾わず、旧スタックを
    現在の結果として画面に出し続ける。
    """
    from mediaferry.db.uploads import Stamp

    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    record = rows(db)[records["JPG"]]

    repo.stamp_many(
        ctx,
        [
            Stamp(
                record_id=record["id"],
                asset_id=None,
                is_trashed=0,
                expect_asset_id=record["remote_asset_id"],
                expect_checked_at=record["remote_checked_at"],
            )
        ],
        now_iso(),
    )

    after = rows(db)[records["JPG"]]
    assert after["stack_state"] is None
    assert after["remote_stack_id"] is None


def test_an_unchanged_remote_asset_id_keeps_the_stack(world, db):
    """同じ ID を再確認しただけなら、結果は現在の姿のまま."""
    from mediaferry.db.uploads import Stamp

    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    record = rows(db)[records["JPG"]]

    repo.stamp_many(
        ctx,
        [
            Stamp(
                record_id=record["id"],
                asset_id=record["remote_asset_id"],
                is_trashed=0,
                expect_asset_id=record["remote_asset_id"],
                expect_checked_at=record["remote_checked_at"],
            )
        ],
        now_iso(),
    )

    assert rows(db)[records["JPG"]]["stack_state"] == "stacked"


def test_stamp_remote_also_reopens_the_stack(world, db):
    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")

    repo.stamp_remote(records["JPG"], "another-asset", 0, now_iso())

    assert rows(db)[records["JPG"]]["stack_state"] is None


def test_a_changed_asset_id_reopens_the_whole_group(world, db):
    """**組は 1 つの結果。** 片方の資産 ID が変われば、相方の結果も無効になる.

    片方だけ戻すと、次に組み直そうとしたとき `_member_moved` が相方の
    `stacked` を拒み、**以後ずっと組めない**（画面も古いスタックを出し続ける）。
    """
    from mediaferry.db.uploads import Stamp

    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    record = rows(db)[records["JPG"]]

    repo.stamp_many(
        ctx,
        [
            Stamp(
                record_id=record["id"],
                asset_id=None,
                is_trashed=0,
                expect_asset_id=record["remote_asset_id"],
                expect_checked_at=record["remote_checked_at"],
            )
        ],
        now_iso(),
    )

    assert {row["stack_state"] for row in rows(db).values()} == {None}
    assert {row["remote_stack_id"] for row in rows(db).values()} == {None}


def test_stamp_remote_reopens_the_whole_group(world, db):
    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")

    repo.stamp_remote(records["JPG"], "another-asset", 0, now_iso())

    assert {row["stack_state"] for row in rows(db).values()} == {None}


def test_a_skipped_partner_is_not_disturbed(world, db):
    """見送りは組の結果ではないので、他人の資産 ID の変化で戻さない."""
    repo, ctx, destination_id, revision, records = world
    repo.mark_skipped(
        ctx, rows(db)[records["CR2"]], destination_id, EPOCH, revision, "相方が見つからない"
    )

    repo.stamp_remote(records["JPG"], "another-asset", 0, now_iso())

    assert rows(db)[records["CR2"]]["stack_state"] == "skipped"


def test_uploading_a_new_asset_over_a_stacked_record_reopens_the_group(world, db):
    """**再確認へ戻った `stacked` の行を、別の資産で送り直す経路。**

    再計算は `complete` → `needs_recheck` を動かすが `stack_state` は残す
    （スタック済みという事実は真のまま）。その後の送信が新しい資産 ID を書く
    ので、**その時点で組の結果は無効になる**。塞がないと `0016` がジョブごと
    落とすか、古い `remote_stack_id` が新しい資産の結果として残る。
    """
    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    record_id = records["JPG"]
    db.execute(
        "UPDATE upload_record SET state = 'checking', claim_job_id = ?, claim_token = ?,"
        " claim_expires_at = ? WHERE id = ?",
        (ctx.job_id, "token-1", "2999-01-01T00:00:00+00:00", record_id),
    )

    repo.advance_owned(
        _with_token(ctx, "token-1"),
        record_id,
        "asset_known",
        "checking",
        remote_asset_id="asset-new",
    )

    assert {row["stack_state"] for row in rows(db).values()} == {None}
    assert rows(db)[record_id]["remote_asset_id"] == "asset-new"


def _with_token(ctx, token):
    """claim のトークンだけを差し替えた文脈（リースは本物）."""
    from dataclasses import replace as _replace

    return _replace(ctx, lease_token=ctx.lease_token) if token is None else _Ctx(ctx, token)


class _Ctx:
    def __init__(self, ctx, token):
        self._ctx = ctx
        self.lease_token = token

    def __getattr__(self, name):
        return getattr(self._ctx, name)


def test_rewriting_the_same_asset_id_keeps_the_stack(world, db):
    """**同じ値を書き直すだけなら、結果は現在の姿のまま。**

    再確認は毎回この経路を通るので、ここで戻すと組が永久に落ち着かない。
    """
    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    record = rows(db)[records["JPG"]]

    repo.stamp_remote(record["id"], record["remote_asset_id"], 0, now_iso())

    assert rows(db)[records["JPG"]]["stack_state"] == "stacked"


def test_a_stale_observation_does_not_touch_the_group(world, db):
    """**古い観測は何も書かない。** 組の再オープンも「何も」に含まれる.

    観測の後に別経路が同じ行を動かしていたら、その `Stamp` は自分の UPDATE を
    飛ばす。**その前に組を開いてしまうと、現在のスタックを壊す。**
    """
    from mediaferry.db.uploads import Stamp

    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    record = rows(db)[records["JPG"]]

    written = repo.stamp_many(
        ctx,
        [
            Stamp(
                record_id=record["id"],
                asset_id=None,
                is_trashed=0,
                # **観測した時点とは違う値**を期待値に渡す（＝古い観測）。
                expect_asset_id="observed-something-else",
                expect_checked_at=record["remote_checked_at"],
            )
        ],
        now_iso(),
    )

    assert written == set()
    assert {row["stack_state"] for row in rows(db).values()} == {"stacked"}
    assert {row["remote_stack_id"] for row in rows(db).values()} == {"stack-1"}


def test_stamp_remote_leaves_the_group_alone_when_the_row_is_not_complete(world, db):
    """公開契約は「`complete` だけを対象」。**組もその契約の内側。**"""
    repo, ctx, destination_id, revision, records = world
    repo.mark_stacked(ctx, list(rows(db).values()), destination_id, EPOCH, revision, "stack-1")
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE id = ?", (records["JPG"],))

    repo.stamp_remote(records["JPG"], "another-asset", 0, now_iso())

    assert {row["stack_state"] for row in rows(db).values()} == {"stacked"}


def test_record_for_returns_the_live_row_not_the_invalidated_one(world, db):
    """**送り直した後は同じ組に行が 2 つ並ぶ。**

    `record_for` は `ORDER BY` を持たないので、無効化を除かないと古い方を
    返す。第 2 パスはこれで相方を引くので、返し間違えると「相方が無効化
    済み」と読んで、送り直しても永久に組めない（§9.11）。
    """
    repo, _, destination_id, _, records = world
    old = records["CR2"]
    media_id = db.execute(
        "SELECT media_file_id FROM upload_record WHERE id = ?", (old,)
    ).fetchone()["media_file_id"]
    revision_id = db.execute(
        "SELECT destination_revision_id FROM upload_record WHERE id = ?", (old,)
    ).fetchone()["destination_revision_id"]
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'remote_missing'"
        " WHERE id = ?",
        (now_iso(), old),
    )
    fresh = an_upload(
        db,
        (destination_id, None, None),
        media_id,
        state="complete",
        origin="created_by_us",
        destination_revision_id=revision_id,
        remote_asset_id="asset-CR2-again",
    )

    found = repo.record_for(destination_id, EPOCH, media_id)

    assert found is not None
    assert found["id"] == fresh


def test_record_for_returns_none_when_only_an_invalidated_row_is_left(world, db):
    """消えたまま送り直していない相方は「この宛先へ送っていない」と同じ扱い."""
    repo, _, destination_id, _, records = world
    old = records["CR2"]
    media_id = db.execute(
        "SELECT media_file_id FROM upload_record WHERE id = ?", (old,)
    ).fetchone()["media_file_id"]
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'remote_missing'"
        " WHERE id = ?",
        (now_iso(), old),
    )

    assert repo.record_for(destination_id, EPOCH, media_id) is None
