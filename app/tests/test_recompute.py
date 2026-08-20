"""`recompute_timestamps`（§6）.

タイムスタンプ解釈やタイムゾーンを変えても既存データは自動では直らない。
明示のジョブで直す。**ファイルは動かさない**（ライブラリのパスは
`library/<slug>/<カード上の相対パス>` で `captured_at` を含まない、§7）。
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from mediaferry.adapters.exif import read_datetime_original
from mediaferry.clock import now_iso
from mediaferry.db.jobs import JobStore, LeaseLost
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.ids import new_id
from mediaferry.jobs.recompute import Recomputer

from .exif_fixtures import a_jpeg_with
from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_jobs import a_job
from .test_schema_sources import a_profile, a_volume
from .test_schema_uploads import a_destination, an_upload

TOKYO = "Asia/Tokyo"


def ns(wall: str) -> int:
    """カード上の壁時計を UTC 表現の epoch ナノ秒にする（`timestamps.py` の前提）."""
    return int(datetime.fromisoformat(wall).replace(tzinfo=UTC).timestamp() * 1_000_000_000)


def a_user_profile(db, source_slug, slug, **timestamp):
    """ビルトインからユーザ定義を作る. ビルトインは編集できない（§6）."""
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    defn = registry.current(source_slug).definition
    ref = registry.create(
        replace(
            defn,
            slug=slug,
            name=slug,
            timestamp=replace(defn.timestamp, **timestamp),
        )
    )
    return ref


def an_original(db, profile, volume, *, source_rel, rel_path=None, mtime_ns, captured_at, **over):
    """カード上の原名（`source_entry`）と公開先の名前（`media_file`）を持つ 1 件."""
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path=rel_path or f"library/{profile.definition.slug}/{source_rel}",
        mtime_ns=mtime_ns,
        captured_at=captured_at,
        **over,
    )
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at)"
        " VALUES (?, ?, ?, 10, ?, 'abc', 1, ?, 'published', ?)",
        (new_id(), volume, source_rel, mtime_ns, media_id, now_iso()),
    )
    return media_id


def a_context(db):
    store = JobStore(db)
    store.enqueue("recompute_timestamps", {})
    return store.claim_next()


@pytest.fixture
def dji(db, data_root):
    """`filename` + `force_offset` のユーザ定義と、その配下の 3 件.

    - part1 はカード上の名前が pattern に当たらないので `mtime` へ落ちる
    - part2 は当たるので `filename`
    - orphan は `source_entry` が無い（カードを再フォーマットした後の姿）
    """
    profile = a_user_profile(db, "dji-osmo", "my-dji", timezone=TOKYO)
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    part1 = an_original(
        db,
        profile,
        volume,
        source_rel="DCIM/DJI_0001_D.MP4",
        mtime_ns=ns("2026-08-17T14:30:00"),
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_source="mtime",
        captured_at_tz=TOKYO,
    )
    part2 = an_original(
        db,
        profile,
        volume,
        source_rel="DCIM/DJI_20260817150000_0002_D.MP4",
        mtime_ns=ns("2026-08-17T15:00:02"),
        captured_at="2026-08-17T15:00:00+09:00",
        captured_at_source="filename",
        captured_at_tz=TOKYO,
    )
    orphan = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/my-dji/DCIM/DJI_20260101000000_0009_D.MP4",
        mtime_ns=ns("2026-01-01T00:00:00"),
        captured_at="2026-01-01T00:00:00+09:00",
        captured_at_tz=TOKYO,
    )
    return profile, volume, part1, part2, orphan


def to_berlin(db, profile):
    """`timezone` を変えて新しいリビジョンを作り、現行を返す."""
    registry = ProfileRegistry(db)
    registry.update(
        profile.definition.slug,
        replace(
            profile.definition,
            timestamp=replace(profile.definition.timestamp, timezone="Europe/Berlin"),
        ),
    )
    return registry.current(profile.definition.slug)


def run(db, data_root, profile, **over):
    return Recomputer(db, data_root, over.pop("default_timezone", TOKYO), **over).run(
        a_context(db), profile
    )


def captured(db, media_id):
    row = db.execute(
        "SELECT captured_at, captured_at_source, captured_at_tz, captured_at_note,"
        " captured_at_revision_id, profile_revision_id, rel_path FROM media_file WHERE id = ?",
        (media_id,),
    ).fetchone()
    return row


# ----------------------------------------------------------------------
# 値と範囲


def test_changing_the_timezone_moves_the_captured_at(dji, db, data_root):
    profile, _, part1, part2, _ = dji
    outcome = run(db, data_root, to_berlin(db, profile))

    assert captured(db, part1)["captured_at"] == "2026-08-17T14:30:00+02:00"
    assert captured(db, part2)["captured_at"] == "2026-08-17T15:00:00+02:00"
    assert outcome.changed == 2


def test_other_profiles_are_left_alone(dji, db, data_root):
    """対象はプロファイル単位. 隣のプロファイルの解釈まで動かさない.

    **原名を持たせておく。** `source_entry` が無いと「飛ばす」側の分岐で先に
    落ちてしまい、絞り込みを外しても結果が変わらない。
    """
    profile, _, _, _, _ = dji
    other = a_profile(db, slug="untouched")
    other_volume = a_volume(db, other, fs_uuid="1234-ABCD")
    stranger = a_media_file(
        db,
        other,
        rel_path="library/untouched/DCIM/DJI_0007_D.MP4",
        mtime_ns=ns("2026-08-17T14:30:00"),
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_source="mtime",
        captured_at_tz=TOKYO,
    )
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at)"
        " VALUES (?, ?, 'DCIM/DJI_0007_D.MP4', 10, ?, 'abc', 1, ?, 'published', ?)",
        (new_id(), other_volume, ns("2026-08-17T14:30:00"), stranger, now_iso()),
    )
    run(db, data_root, to_berlin(db, profile))

    assert captured(db, stranger)["captured_at"] == "2026-08-17T14:30:00+09:00"
    assert captured(db, stranger)["captured_at_revision_id"] == other[1]


def test_no_file_is_moved(dji, db, data_root):
    """ライブラリのパスは `captured_at` を含まない（§7）."""
    profile, _, part1, part2, _ = dji

    def paths():
        return {
            row["id"]: row["rel_path"] for row in db.execute("SELECT id, rel_path FROM media_file")
        }

    before = paths()
    run(db, data_root, to_berlin(db, profile))
    after = paths()

    assert after == before


# ----------------------------------------------------------------------
# provenance


def test_only_the_captured_revision_advances(dji, db, data_root):
    """`profile_revision_id` は「そのレコードが使用した不変の版」（§6）.

    値だけ新しくすると嘘になり、版ごと進めると timestamp 以外の新定義も
    適用したと偽る。**列を分けて、片方だけを進める。**
    """
    profile, _, part1, _, _ = dji
    old_revision = profile.revision_id
    new_profile = to_berlin(db, profile)
    run(db, data_root, new_profile)

    row = captured(db, part1)
    assert row["profile_revision_id"] == old_revision
    assert row["captured_at_revision_id"] == new_profile.revision_id


def test_an_original_without_a_source_entry_is_skipped(dji, db, data_root):
    """**勝手に mtime へ落とさない。** 正しかった値を壊す."""
    profile, _, _, _, orphan = dji
    outcome = run(db, data_root, to_berlin(db, profile))

    row = captured(db, orphan)
    assert row["captured_at"] == "2026-01-01T00:00:00+09:00"
    assert row["captured_at_revision_id"] == profile.revision_id
    assert outcome.skipped == 1


def test_the_skipped_count_and_reason_are_reported(dji, db, data_root):
    """黙って飛ばさない. 件数が実際と食い違うと消えたことに気付けない."""
    profile, _, _, _, _ = dji
    run(db, data_root, to_berlin(db, profile))

    messages = [row["message"] for row in db.execute("SELECT message FROM job_event")]
    assert any("カード上の原名" in message and "1 件" in message for message in messages)


# ----------------------------------------------------------------------
# 再計算の入力


def test_a_filename_original_is_matched_against_the_name_on_the_card(db, data_root):
    """`media_file.rel_path` は**公開先の名前**で、衝突時は接尾辞が付く（§9.3）.

    原名に当てないと、接尾辞の壁時計を撮影日時として拾う。
    """
    profile = a_user_profile(
        db, "dji-osmo", "my-cam", pattern=r"_(?P<ts>\d{14})", timezone_policy="none", timezone=None
    )
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    media = an_original(
        db,
        profile,
        volume,
        source_rel="DCIM/100MEDIA/IMG_0001.JPG",
        # 同名の別ファイルとぶつかったので、公開先には mtime の壁時計が付いた。
        rel_path="library/my-cam/DCIM/100MEDIA/IMG_0001_20260818090000.JPG",
        mtime_ns=ns("2026-08-17T12:00:00"),
        captured_at="2026-01-01T00:00:00+00:00",
        kind="photo",
        duration_seconds=None,
    )
    run(db, data_root, profile)

    row = captured(db, media)
    assert row["captured_at_source"] == "mtime"
    assert row["captured_at"] == "2026-08-17T12:00:00+00:00"


def test_an_exif_original_is_read_from_the_published_file(db, data_root):
    """カードはもう手元に無い. 読む対象は公開済みの実体."""
    profile = a_user_profile(db, "canon-eos", "my-canon")
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    rel = "library/my-canon/DCIM/100CANON/IMG_0001.JPG"
    path = data_root / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(a_jpeg_with(b"2026:02:03 04:05:06"))
    media = an_original(
        db,
        profile,
        volume,
        source_rel="DCIM/100CANON/IMG_0001.JPG",
        rel_path=rel,
        mtime_ns=ns("2026-08-17T12:00:00"),
        captured_at="2026-08-17T12:00:00+00:00",
        captured_at_source="mtime",
        kind="photo",
        duration_seconds=None,
    )
    run(db, data_root, profile)

    row = captured(db, media)
    assert row["captured_at_source"] == "exif"
    assert row["captured_at"] == "2026-02-03T04:05:06+00:00"


def test_a_derived_inherits_from_the_first_active_member(dji, db, data_root):
    """派生物は算出ではなく継承（`Merger._captured_of`）.

    結合出力そのものの名前・EXIF・mtime を読むと、意味が変わる。ここでは
    先頭 member が `mtime` へ落ちているので、**出力名に当てれば `filename` に
    なる**（出力名は pattern に当たる）。source がずれれば当てたと分かる。
    """
    profile, _, part1, part2, _ = dji
    group = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    output = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/my-dji/DCIM/DJI_20260817143000_0001-0002_MERGED.MP4",
        # 録画終了時刻（§9.8 手順 6）。member の撮影開始とは違う。
        mtime_ns=ns("2026-08-17T15:00:05"),
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_source="mtime",
        captured_at_tz=TOKYO,
    )
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, part1))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group, part2))

    new_profile = to_berlin(db, profile)
    run(db, data_root, new_profile)

    row = captured(db, output)
    # 先頭 member の**再計算後の**値。順序が逆なら +09:00 のまま残る。
    assert row["captured_at"] == "2026-08-17T14:30:00+02:00"
    assert row["captured_at"] == captured(db, part1)["captured_at"]
    # 出力名に当てれば filename、自分の mtime を使えば 15:00:05 になる。
    assert row["captured_at_source"] == "mtime"
    assert row["captured_at_revision_id"] == new_profile.revision_id


def test_a_derived_without_an_active_member_is_skipped(dji, db, data_root):
    profile, _, part1, _, _ = dji
    group = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    output = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/my-dji/DCIM/DJI_20260817143000_0001-0002_MERGED.MP4",
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_tz=TOKYO,
    )
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))

    outcome = run(db, data_root, to_berlin(db, profile))

    assert captured(db, output)["captured_at"] == "2026-08-17T14:30:00+09:00"
    assert outcome.skipped == 2  # orphan と、この派生物


# ----------------------------------------------------------------------
# 送信済みのものをどう知らせるか


def a_sent_record(db, media_id, dest):
    return an_upload(
        db,
        dest,
        media_id,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id="asset-1",
        remote_is_trashed=0,
        remote_checked_at=now_iso(),
    )


def test_a_sent_record_whose_time_changed_goes_back_to_needs_recheck(dji, db, data_root):
    """`awaiting_datetime_approval` へ直接戻さない.

    その状態は `tagging` からしか入れず、承認画面が出す「現在値」は承認待ちに
    する瞬間に Immich へ問い合わせて控えた値。**`recompute` は Immich に触らない**
    ので埋められない。既にある `needs_recheck` に載せて、次のアップロード
    ジョブにパイプラインを再実行させる。
    """
    profile, _, part1, _, _ = dji
    dest = a_destination(db)
    record = a_sent_record(db, part1, dest)

    outcome = run(db, data_root, to_berlin(db, profile))

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["state"] == "needs_recheck"
    # リモートの観測はそのまま残す（この時点では相手を触っていない）。
    assert row["remote_asset_id"] == "asset-1"
    assert outcome.requeued == 1


def test_a_sent_record_whose_time_did_not_change_stays_complete(dji, db, data_root):
    """変わらないものまで戻すと、何も変わらない再送を全件に強いる."""
    profile, volume, _, _, _ = dji
    steady = an_original(
        db,
        profile,
        volume,
        source_rel="DCIM/DJI_20260817160000_0003_D.MP4",
        mtime_ns=ns("2026-08-17T16:00:02"),
        captured_at="2026-08-17T16:00:00+09:00",
        captured_at_source="filename",
        captured_at_tz=TOKYO,
    )
    dest = a_destination(db)
    record = a_sent_record(db, steady, dest)

    # 版だけを進める（timezone は据え置き）。値は動かない。
    registry = ProfileRegistry(db)
    registry.update(
        profile.definition.slug, replace(profile.definition, name="renamed but the same rule")
    )
    outcome = run(db, data_root, registry.current(profile.definition.slug))

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["state"] == "complete"
    assert outcome.requeued == 0
    assert outcome.changed == 0
    # 値は同じでも「この版で算出した」ことは進む。
    assert captured(db, steady)["captured_at_revision_id"] != profile.revision_id


def test_a_profile_that_never_writes_the_remote_datetime_leaves_records_complete(db, data_root):
    """`fix_datetime_after_upload` が偽なら、リモートに差は生じない."""
    profile = a_user_profile(db, "canon-eos", "my-canon")
    assert profile.definition.immich.fix_datetime_after_upload is False
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    rel = "library/my-canon/DCIM/100CANON/IMG_0001.JPG"
    (data_root / rel).parent.mkdir(parents=True)
    (data_root / rel).write_bytes(a_jpeg_with(b"2026:02:03 04:05:06"))
    media = an_original(
        db,
        profile,
        volume,
        source_rel="DCIM/100CANON/IMG_0001.JPG",
        rel_path=rel,
        mtime_ns=ns("2026-08-17T12:00:00"),
        captured_at="2026-08-17T12:00:00+00:00",
        captured_at_source="mtime",
        kind="photo",
        duration_seconds=None,
    )
    dest = a_destination(db)
    record = a_sent_record(db, media, dest)

    outcome = run(db, data_root, profile)

    assert captured(db, media)["captured_at"] == "2026-02-03T04:05:06+00:00"
    assert (
        db.execute("SELECT state FROM upload_record WHERE id = ?", (record,)).fetchone()[0]
        == "complete"
    )
    assert outcome.requeued == 0


def test_a_record_that_is_not_complete_is_left_alone(dji, db, data_root):
    """進行中のレコードを踏まない. CAS の条件は `state = 'complete'`."""
    profile, _, part1, part2, _ = dji
    dest = a_destination(db)
    job = a_job(db)
    running = an_upload(
        db,
        dest,
        part1,
        state="uploading",
        destination_revision_id=dest[1],
        claim_job_id=job,
        claim_token="t",
        claim_expires_at=now_iso(),
    )
    pending = an_upload(db, dest, part2, state="pending")

    run(db, data_root, to_berlin(db, profile))

    states = {row["id"]: row["state"] for row in db.execute("SELECT id, state FROM upload_record")}
    assert states[running] == "uploading"
    assert states[pending] == "pending"


def test_the_requeue_shares_the_transaction_with_the_update(dji, db, data_root, monkeypatch):
    """割ると「値は新しいのに `complete` のまま」が残る.

    差し戻しの側だけが落ちる筋書きを作り、**値も戻っている**ことを見る。
    """
    profile, _, part1, _, _ = dji
    dest = a_destination(db)
    record = a_sent_record(db, part1, dest)

    def explode(*args, **kwargs):
        raise RuntimeError("差し戻しが失敗した")

    monkeypatch.setattr(Recomputer, "_requeue", explode)
    with pytest.raises(RuntimeError):
        run(db, data_root, to_berlin(db, profile))

    assert captured(db, part1)["captured_at"] == "2026-08-17T14:30:00+09:00"
    assert (
        db.execute("SELECT state FROM upload_record WHERE id = ?", (record,)).fetchone()[0]
        == "complete"
    )


# ----------------------------------------------------------------------
# キャンセルとリース


def test_cancelling_stops_partway_and_keeps_what_was_committed(dji, db, data_root, monkeypatch):
    """件数が多いので、**バッチごとに**キャンセルとリースの両方を見る."""
    profile, _, part1, part2, _ = dji
    new_profile = to_berlin(db, profile)
    store = JobStore(db)
    store.enqueue("recompute_timestamps", {})
    ctx = store.claim_next()
    recomputer = Recomputer(db, data_root, TOKYO, batch_size=1)

    seen = []
    original = Recomputer._apply_batch

    def counting(self, ctx_, batch, *args, **kwargs):
        seen.append(len(batch))
        result = original(self, ctx_, batch, *args, **kwargs)
        # バッチが commit を終えた後で押される。次のバッチは始まらない。
        store.request_cancel(ctx.job_id)
        return result

    monkeypatch.setattr(Recomputer, "_apply_batch", counting)
    outcome = recomputer.run(ctx, new_profile)

    assert seen == [1]
    # 最初のバッチは commit 済みで、残りは手つかず。
    done = [
        row["captured_at"]
        for row in db.execute("SELECT captured_at FROM media_file ORDER BY rel_path")
    ]
    assert "+02:00" in done[0]
    assert outcome.changed == 1


def test_losing_the_lease_that_is_not_a_cancel_fails_the_job(dji, db, data_root):
    """失効したリースのまま書き続けない.

    `cancelled()` はジョブの**状態**しか見ないので、`running` のままリースだけを
    失った worker を止められない（再確認・取り込みと同じ形）。
    """
    profile, _, part1, _, _ = dji
    new_profile = to_berlin(db, profile)
    store = JobStore(db)
    store.enqueue("recompute_timestamps", {})
    ctx = store.claim_next()
    db.execute("UPDATE job SET lease_token = 'someone-else' WHERE id = ?", (ctx.job_id,))

    with pytest.raises(LeaseLost):
        Recomputer(db, data_root, TOKYO).run(ctx, new_profile)

    assert captured(db, part1)["captured_at"] == "2026-08-17T14:30:00+09:00"


def test_a_superseded_groups_output_is_not_re_derived(dji, db, data_root):
    """supersede した結合の出力は、もう「先頭 active member」を持たない.

    再結合で作り直された側が現行で、旧出力は退役している（`invalidate_for_group`
    がアップロードも無効にする）。ここで**非 active な member から derive し直すと、
    退役した出力に新しい値を書く**ことになる。
    """
    profile, _, part1, part2, _ = dji
    old_group = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    new_group = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d2")
    output = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/my-dji/DCIM/DJI_20260817143000_0001-0002_MERGED.MP4",
        mtime_ns=ns("2026-08-17T15:00:05"),
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_source="mtime",
        captured_at_tz=TOKYO,
    )
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, old_group))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (old_group, part1))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (old_group, part2))
    # trigger が member を非 active にする。
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (new_group, old_group))

    outcome = run(db, data_root, to_berlin(db, profile))

    assert captured(db, output)["captured_at"] == "2026-08-17T14:30:00+09:00"
    assert outcome.skipped == 2  # orphan と、退役した出力


def test_exif_is_not_read_for_a_video(dji, db, data_root, monkeypatch):
    """**画像以外では読みに行かない。** `exifread` は認識できない入力に例外では
    なく WARNING を出すので、呼べば動画 1 本ごとに警告が並ぶ（取り込みと同じ）.

    結果は `None` を返して fallback へ落ちるだけで変わらないので、
    **呼んだかどうか**をスパイで見る。
    """
    profile, _, _, _, _ = dji
    registry = ProfileRegistry(db)
    registry.update(
        profile.definition.slug,
        replace(
            profile.definition,
            timestamp=replace(profile.definition.timestamp, source="exif"),
        ),
    )
    calls = []
    monkeypatch.setattr(
        "mediaferry.jobs.recompute.read_datetime_original", lambda path: calls.append(path)
    )

    run(db, data_root, registry.current(profile.definition.slug))

    assert calls == []


def test_a_slow_exif_pass_does_not_lose_the_lease(db, data_root, monkeypatch):
    """**EXIF の読み取りはトランザクションの外**で、そこにはリースの確認が無い.

    1 件ずつは短くても、バッチぶん積もると 60 秒を越える。越えると次のバッチの
    `assert_lease` が落ち、**先のバッチを commit したまま**ジョブが失敗する
    （公開の `_with_lease_pulse` と同じ形の穴）。
    """
    import time

    profile = a_user_profile(db, "canon-eos", "my-canon")
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    for index in range(2):
        rel = f"library/my-canon/DCIM/100CANON/IMG_000{index}.JPG"
        (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_root / rel).write_bytes(a_jpeg_with(b"2026:02:03 04:05:06"))
        an_original(
            db,
            profile,
            volume,
            source_rel=f"DCIM/100CANON/IMG_000{index}.JPG",
            rel_path=rel,
            mtime_ns=ns("2026-08-17T12:00:00"),
            captured_at="2026-08-17T12:00:00+00:00",
            captured_at_source="mtime",
            kind="photo",
            duration_seconds=None,
        )

    real = read_datetime_original

    def slow(path):
        time.sleep(1.5)  # リース（1 秒）より長い
        return real(path)

    monkeypatch.setattr("mediaferry.jobs.recompute.read_datetime_original", slow)
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.2)
    store = JobStore(db, lease_seconds=1)
    store.enqueue("recompute_timestamps", {})
    ctx = store.claim_next()

    outcome = Recomputer(db, data_root, TOKYO, batch_size=1).run(ctx, profile)

    assert outcome.changed == 2


def test_a_cancel_between_the_check_and_the_write_leaves_nothing_written(
    dji, db, data_root, monkeypatch
):
    """**確認と書き込みを 1 つの `BEGIN IMMEDIATE` に入れる**（§9.3 手順 7 と同じ形）.

    バッチの頭で `assert_lease` を済ませてから再計算に入るので、その間に
    キャンセルが commit されうる。書き込みの側でも取り直さないと、
    **キャンセル済みと表示した後に commit される**。
    """
    profile, _, part1, _, _ = dji
    new_profile = to_berlin(db, profile)
    store = JobStore(db)
    store.enqueue("recompute_timestamps", {})
    ctx = store.claim_next()
    original = Recomputer._recomputed_original

    def cancelling(self, ctx_, row, prof):  # noqa: ANN001, ANN202
        # 確認の後・書き込みの前という窓を、決定的に作る。
        store.request_cancel(ctx.job_id)
        return original(self, ctx_, row, prof)

    monkeypatch.setattr(Recomputer, "_recomputed_original", cancelling)

    outcome = Recomputer(db, data_root, TOKYO).run(ctx, new_profile)

    assert captured(db, part1)["captured_at"] == "2026-08-17T14:30:00+09:00"
    assert outcome.changed == 0


def test_a_derived_is_skipped_when_its_member_could_not_be_recomputed(dji, db, data_root):
    """**継承元が旧版のままなら、派生物も進めない。**

    先頭 active member が `source_entry` 欠落で飛ばされると、その `captured_at` は
    旧版で算出したまま。それを継いだ派生物に新版の
    `captured_at_revision_id` を書くと、**値は旧版由来なのに新版で算出したと
    記録する**ことになる（`0011` で列を分けた意味が消える）。
    """
    profile, _, part1, part2, _ = dji
    # カードを再フォーマットしたので、先頭パートの原名が残っていない。
    db.execute("DELETE FROM source_entry WHERE media_file_id = ?", (part1,))
    group = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    output = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/my-dji/DCIM/DJI_20260817143000_0001-0002_MERGED.MP4",
        mtime_ns=ns("2026-08-17T15:00:05"),
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_source="mtime",
        captured_at_tz=TOKYO,
    )
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, part1))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group, part2))

    outcome = run(db, data_root, to_berlin(db, profile))

    row = captured(db, output)
    assert row["captured_at"] == "2026-08-17T14:30:00+09:00"
    assert row["captured_at_revision_id"] == profile.revision_id
    assert outcome.skipped == 3  # orphan と part1 と、その派生物


def test_many_short_exif_reads_do_not_lose_the_lease(db, data_root, monkeypatch):
    """1 件ずつは間隔より短くても、**バッチの中で積もるとリースを越える**.

    `with_lease_pulse` は `thread.join(timeout=間隔)` が先に返るので、
    **間隔より短く終わる処理では 1 度も打たない**。行をまたいで打たないと、
    100 枚の EXIF が 1 枚 1 秒でも 100 秒になり、書き込みの `assert_lease` で
    正常なジョブが落ちる。
    """
    import time

    profile = a_user_profile(db, "canon-eos", "my-canon")
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    for index in range(15):
        rel = f"library/my-canon/DCIM/100CANON/IMG_{index:04d}.JPG"
        (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_root / rel).write_bytes(a_jpeg_with(b"2026:02:03 04:05:06"))
        an_original(
            db,
            profile,
            volume,
            source_rel=f"DCIM/100CANON/IMG_{index:04d}.JPG",
            rel_path=rel,
            mtime_ns=ns("2026-08-17T12:00:00"),
            captured_at="2026-08-17T12:00:00+00:00",
            captured_at_source="mtime",
            kind="photo",
            duration_seconds=None,
        )

    real = read_datetime_original

    def short(path):
        time.sleep(0.1)  # 間隔 (0.3) より短い。合計 1.5 秒でリース (1 秒) を越える。
        return real(path)

    monkeypatch.setattr("mediaferry.jobs.recompute.read_datetime_original", short)
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.3)
    store = JobStore(db, lease_seconds=1)
    store.enqueue("recompute_timestamps", {})
    ctx = store.claim_next()

    outcome = Recomputer(db, data_root, TOKYO).run(ctx, profile)

    assert outcome.changed == 15


def test_a_cancel_stops_the_batch_before_reading_the_rest(db, data_root, monkeypatch):
    """**`heartbeat` だけでは足りない。** `extend_lease` は `cancelling` でも延ばす.

    行をまたぐ pulse が `assert_lease` を欠くと、キャンセル済みのリースを延ばし
    続け、**残りの EXIF を最後まで読んでから**書き込みの `assert_lease` で
    ようやく止まる（100 枚なら数十分）。書き込みは防げても、キャンセルの
    取りこぼしになる（`core/lease_pulse.py` が `assert_lease` を先に呼ぶのと同じ理由）。
    """
    import time

    profile = a_user_profile(db, "canon-eos", "my-canon")
    volume = a_volume(db, (profile.profile_id, profile.revision_id))
    media = []
    for index in range(30):
        rel = f"library/my-canon/DCIM/100CANON/IMG_{index:04d}.JPG"
        (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_root / rel).write_bytes(a_jpeg_with(b"2026:02:03 04:05:06"))
        media.append(
            an_original(
                db,
                profile,
                volume,
                source_rel=f"DCIM/100CANON/IMG_{index:04d}.JPG",
                rel_path=rel,
                mtime_ns=ns("2026-08-17T12:00:00"),
                captured_at="2026-08-17T12:00:00+00:00",
                captured_at_source="mtime",
                kind="photo",
                duration_seconds=None,
            )
        )

    store = JobStore(db)
    store.enqueue("recompute_timestamps", {})
    ctx = store.claim_next()
    real = read_datetime_original
    reads: list[str] = []

    def watched(path):
        reads.append(str(path))
        if len(reads) == 1:
            store.request_cancel(ctx.job_id)
        # **1 枚は間隔より短い。** `with_lease_pulse` は 1 度も打たないので、
        # 行をまたぐ側だけがキャンセルに気づける経路になる。
        time.sleep(0.01)
        return real(path)

    monkeypatch.setattr("mediaferry.jobs.recompute.read_datetime_original", watched)
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)

    outcome = Recomputer(db, data_root, TOKYO).run(ctx, profile)

    # 1 枚目でキャンセルが commit されたら、30 枚を読み切らずに降りる。
    assert len(reads) < 30, f"キャンセル後も読み続けた: {len(reads)} 枚"
    assert outcome.changed == 0
    assert captured(db, media[0])["captured_at"] == "2026-08-17T12:00:00+00:00"


def test_the_targets_are_read_in_pages_under_the_lease(dji, db, data_root, monkeypatch):
    """**対象の抽出もリースとキャンセルの内側で行う。**

    全件を先に materialize すると、`media_file` 1 行ごとの相関副問い合わせが
    最初の `assert_lease` より前に 60 秒を超えうる（正常なジョブがリース切れで
    落ち、その間キャンセルも観測されない）。`BATCH_SIZE` は書き込みだけでなく
    **読み出しにも効かせる**。
    """
    profile, _, _, _, _ = dji
    new_profile = to_berlin(db, profile)
    pages: list[int] = []
    original = Recomputer._fetch_originals

    def counting(self, profile_id, cursor, limit):  # noqa: ANN001, ANN202
        rows = original(self, profile_id, cursor, limit)
        pages.append(len(rows))
        return rows

    monkeypatch.setattr(Recomputer, "_fetch_originals", counting)
    outcome = run(db, data_root, new_profile, batch_size=1)

    # 3 件を 1 件ずつ読み、最後に空を読んで終わる。
    assert pages == [1, 1, 1, 0]
    assert outcome.changed == 2
    assert outcome.skipped == 1


def test_a_regroup_between_the_read_and_the_write_stops_the_derived(
    dji, db, database, data_root, monkeypatch
):
    """**継承元は排他区間の中で解決する。**

    ページへ読み出してから書くまでの間に、API 側の別接続で結合をやり直せる
    （`merges.py` の supersede は trigger で旧 member を非 active にする）。
    読んだときの member を持ち回ると、**退役した出力に新しい値と新しい版を書く**
    ことになり、「supersede した結合の出力は再導出しない」に反する。
    """
    profile, _, part1, part2, _ = dji
    group = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    later = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d2")
    output = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/my-dji/DCIM/DJI_20260817143000_0001-0002_MERGED.MP4",
        mtime_ns=ns("2026-08-17T15:00:05"),
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_source="mtime",
        captured_at_tz=TOKYO,
    )
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, part1))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group, part2))
    new_profile = to_berlin(db, profile)

    other = database.connect()
    original = Recomputer._fetch_derived

    def regrouping(self, profile_id, cursor, limit):  # noqa: ANN001, ANN202
        rows = original(self, profile_id, cursor, limit)
        if rows:
            # 読んだ直後・書く前に、別接続で結合をやり直す。
            other.execute(
                "UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (later, group)
            )
        return rows

    monkeypatch.setattr(Recomputer, "_fetch_derived", regrouping)
    try:
        run(db, data_root, new_profile)
    finally:
        other.close()

    row = captured(db, output)
    assert row["captured_at"] == "2026-08-17T14:30:00+09:00"
    assert row["captured_at_revision_id"] == profile.revision_id


def test_nothing_can_regroup_between_the_resolve_and_the_write(
    dji, db, database, data_root, monkeypatch
):
    """**解決と書き込みの間に、別接続が割り込めない。**

    継承元を排他区間の外で解決すると、再取得していても「解決した後・書く前」の
    窓が残る。ここで確かめるのはその窓が**無い**ことなので、割り込もうとした側が
    書き込みロックで弾かれることを見る（`busy_timeout = 0` で待たずに失敗させる）。
    値そのものを見ても区別が付かない —— 割り込めた世界と割り込めなかった世界の
    どちらでも「正しい値」は違うものになる。
    """
    import sqlite3 as _sqlite3

    profile, _, part1, part2, _ = dji
    group = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d1")
    later = a_merge_group(db, (profile.profile_id, profile.revision_id), digest="d2")
    output = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/my-dji/DCIM/DJI_20260817143000_0001-0002_MERGED.MP4",
        mtime_ns=ns("2026-08-17T15:00:05"),
        captured_at="2026-08-17T14:30:00+09:00",
        captured_at_source="mtime",
        captured_at_tz=TOKYO,
    )
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, part1))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group, part2))
    new_profile = to_berlin(db, profile)

    other = database.connect()
    other.execute("PRAGMA busy_timeout = 0")
    committed: list[bool] = []
    original = Recomputer._recomputed_derived

    def resolving(self, ctx_, row, prof):  # noqa: ANN001, ANN202
        value = original(self, ctx_, row, prof)
        try:
            other.execute(
                "UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (later, group)
            )
            committed.append(True)
        except _sqlite3.OperationalError:
            pass  # 書き込みロックに弾かれた＝窓が無い
        return value

    monkeypatch.setattr(Recomputer, "_recomputed_derived", resolving)
    try:
        run(db, data_root, new_profile)
    finally:
        other.close()

    assert committed == [], "解決した後・書き込む前に、別接続が supersede を commit できた"


def test_a_cancelled_run_is_not_reported_as_finished(dji, db, data_root):
    """**キャンセルを「完了」と書かない。** ログに「中止」の後で「完了」が並ぶ.

    協調キャンセルは正常 return で表すので、戻り値だけを見る呼び出し側は
    区別が付かない。`RecomputeOutcome` に完走したかを持たせる。
    """
    profile, _, _, _, _ = dji
    new_profile = to_berlin(db, profile)
    store = JobStore(db)
    store.enqueue("recompute_timestamps", {})
    ctx = store.claim_next()
    store.request_cancel(ctx.job_id)

    outcome = Recomputer(db, data_root, TOKYO).run(ctx, new_profile)

    assert outcome.finished is False
    assert run(db, data_root, to_berlin(db, ProfileRegistry(db).current("my-dji"))).finished is True


# --- スタックの見送りを未評価へ戻す（Phase 6 / §6） ---------------------


def a_skipped_record(db, media_id, dest, reason="相方と撮影時刻が一致しない"):
    record = a_sent_record(db, media_id, dest)
    db.execute(
        "UPDATE upload_record SET stack_state = 'skipped', stack_reason = ? WHERE id = ?",
        (reason, record),
    )
    return record


def test_a_changed_capture_time_reopens_a_skipped_stack(dji, db, data_root):
    """時刻がずれていて見送った組は、再計算で成立しうる（§6）."""
    profile, _, part1, _, _ = dji
    record = a_skipped_record(db, part1, a_destination(db))

    outcome = run(db, data_root, to_berlin(db, profile))

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["stack_state"] is None
    assert row["stack_reason"] is None
    assert outcome.reopened == 1


def test_an_unchanged_capture_time_leaves_the_skip_alone(dji, db, data_root):
    profile, _, part1, _, _ = dji
    record = a_skipped_record(db, part1, a_destination(db))

    outcome = run(db, data_root, profile)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["stack_state"] == "skipped"
    assert outcome.reopened == 0


def test_an_existing_stack_is_not_reopened(dji, db, data_root):
    """**相手側に既にあるものを作り直さない**（§6）."""
    profile, _, part1, _, _ = dji
    dest = a_destination(db)
    record = a_sent_record(db, part1, dest)
    db.execute(
        "UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = 's' WHERE id = ?",
        (record,),
    )

    run(db, data_root, to_berlin(db, profile))

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record,)).fetchone()
    assert row["stack_state"] == "stacked"


def test_the_skip_of_another_media_is_left_alone(dji, db, data_root):
    """戻すのは**時刻が動いたレコード**だけ."""
    profile, _, part1, part2, _ = dji
    dest = a_destination(db)
    other = a_skipped_record(db, part2, dest)
    db.execute(
        "UPDATE media_file SET captured_at_revision_id = profile_revision_id WHERE id = ?",
        (part2,),
    )
    # part2 だけを対象外にする（別プロファイルへ移す）。
    moved = a_user_profile(db, "dji-osmo", "other-camera")
    db.execute(
        "UPDATE media_file SET profile_id = ?, profile_revision_id = ?,"
        " captured_at_revision_id = ? WHERE id = ?",
        (moved.profile_id, moved.revision_id, moved.revision_id, part2),
    )

    run(db, data_root, to_berlin(db, profile))

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (other,)).fetchone()
    assert row["stack_state"] == "skipped"
