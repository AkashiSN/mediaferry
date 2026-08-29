import json
import os

import pytest

from mediaferry.clock import now_iso
from mediaferry.core.crypto import SecretBox
from mediaferry.db import uploads as uploads_module
from mediaferry.db.connection import Database
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository, UploadRequestInvalid

from .test_schema_artifacts import a_media_file
from .test_selection import a_derived, a_group, a_pair

IDENTITY = RemoteIdentity.observed("user-a")
KEY = os.urandom(32)


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


@pytest.fixture
def destinations(db):
    return DestinationRepository(db, CredentialStore(db, SecretBox(KEY)))


@pytest.fixture
def uploads(db, destinations):
    return UploadRepository(db, ProfileRegistry(db), destinations)


def a_destination(destinations, name="home"):
    return destinations.create(
        name=name,
        base_url=f"http://{name}.invalid:2283",
        public_url=None,
        secret="key-1",
        identity=IDENTITY,
    )


def results_of(pairs):
    return {(pair.media_file_id, pair.destination_id): pair.result for pair in pairs}


def test_a_plain_original_becomes_a_pending_record(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([media_id], [destination_id])

    assert [pair.result for pair in pairs] == ["created"]
    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["state"] == "pending"
    assert row["selection_rule"] == "default"
    assert row["origin"] == "unknown"
    assert row["target_epoch"] == destinations.current(destination_id)["target_epoch"]
    assert row["eligibility_reason"]


def test_the_cross_product_is_expanded(db, profile, destinations, uploads):
    media = [
        a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/X{i}.MP4",
        )
        for i in (1, 2)
    ]
    targets = [a_destination(destinations, "home"), a_destination(destinations, "family")]

    pairs = uploads.create_pairs(media, targets)

    assert len(pairs) == 4
    assert set(results_of(pairs).values()) == {"created"}


def test_an_unknown_media_id_rejects_the_whole_request(db, profile, destinations, uploads):
    destination_id = a_destination(destinations)
    with pytest.raises(UploadRequestInvalid):
        uploads.create_pairs(["no-such-media"], [destination_id])
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 0


def test_a_disabled_destination_rejects_the_whole_request(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    destinations.set_enabled(destination_id, False)
    with pytest.raises(UploadRequestInvalid):
        uploads.create_pairs([media_id], [destination_id])
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 0


def test_an_archived_destination_rejects_the_whole_request(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    # `archive` は enabled も落とすので、**保管の判定だけ**が効く状態を作る
    # （enabled の判定が先に効くと、この分岐を一度も通らない）。
    db.execute(
        "UPDATE upload_destination SET archived_at = ?, enabled = 1 WHERE id = ?",
        ("2026-08-17T00:00:00+00:00", destination_id),
    )
    with pytest.raises(UploadRequestInvalid):
        uploads.create_pairs([media_id], [destination_id])


def test_a_destination_without_a_verified_revision_is_refused(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    db.execute(
        "UPDATE upload_destination SET current_revision_id = NULL WHERE id = ?", (destination_id,)
    )
    with pytest.raises(UploadRequestInvalid):
        uploads.create_pairs([media_id], [destination_id])


def test_the_pair_carries_the_current_epoch(db, profile, destinations, uploads):
    """epoch を進めた後に作った pair は、新しい epoch に属する（§8）."""
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    destinations.add_revision(
        destination_id,
        base_url="http://home.invalid:2283",
        public_url=None,
        secret="key-2",
        identity=RemoteIdentity.observed("user-b"),
    )
    assert destinations.current(destination_id)["target_epoch"] == 2

    uploads.create_pairs([media_id], [destination_id])

    assert db.execute("SELECT target_epoch FROM upload_record").fetchone()[0] == 2


def test_a_revision_bumped_just_before_the_insert_does_not_leave_a_stale_pair(
    db, data_root, profile, destinations, uploads, monkeypatch
):
    """**epoch の読み出しは pair の INSERT と同じトランザクションで行う**（§8）.

    外で読むと、読んだ後・書く前に他の書き手が epoch を進めて旧 epoch の
    無効化を済ませられる。すり抜けた行は `claim_next` が現行 epoch しか拾わない
    ので送られず、次の起動の掃除まで理由の無い `pending` として残る。
    """
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)

    other = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    bumped = False

    def bump_then(conn):
        # 「別の書き手が、こちらがトランザクションを開く直前に commit した」形。
        nonlocal bumped
        if not bumped:
            bumped = True
            DestinationRepository(other, CredentialStore(other, SecretBox(KEY))).add_revision(
                destination_id,
                base_url="http://moved.invalid:2283",
                public_url=None,
                secret="key-2",
                identity=RemoteIdentity.observed("user-b"),
            )
        return real_immediate(conn)

    real_immediate = uploads_module.immediate
    monkeypatch.setattr(uploads_module, "immediate", bump_then)
    try:
        uploads.create_pairs([media_id], [destination_id])
    finally:
        other.close()

    epoch = destinations.current(destination_id)["target_epoch"]
    assert epoch == 2
    assert db.execute("SELECT target_epoch FROM upload_record").fetchone()[0] == epoch


def test_a_missing_file_is_rejected_per_pair(db, profile, destinations, uploads):
    present = a_media_file(
        db, (profile.profile_id, profile.revision_id), rel_path="library/dji-osmo/DCIM/OK.MP4"
    )
    gone = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/GONE.MP4",
        missing_at="2026-08-17T00:00:00+00:00",
    )
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([present, gone], [destination_id])

    assert results_of(pairs)[(gone, destination_id)] == "rejected"
    assert results_of(pairs)[(present, destination_id)] == "created"
    # 1 件の拒否が他を巻き込まない。
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 1


def test_a_member_of_an_active_group_is_rejected(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id)
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([members[0][0]], [destination_id])

    assert pairs[0].result == "rejected"
    assert "グループ" in pairs[0].reason


def test_a_member_of_a_failed_group_is_allowed_with_its_rule(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="failed", verification=None)
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([members[0][0]], [destination_id])

    assert pairs[0].result == "created"
    assert db.execute("SELECT selection_rule FROM upload_record").fetchone()[0] == (
        "failed_group_member"
    )


def test_a_verified_derived_is_default(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([output_id], [destination_id])

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert pairs[0].result == "created"
    assert row["selection_rule"] == "default"
    assert row["merge_group_id"] == group_id


def test_choosing_an_unadopted_derived_adopts_it(db, profile, destinations, uploads):
    """採用そのものとして扱う. 別操作にすると、作った瞬間に条件を満たさなくなる."""
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(
        db, profile, members, output_id=output_id, verification=json.dumps({"passed": False})
    )
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([output_id], [destination_id])

    assert pairs[0].result == "created"
    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["selection_rule"] == "adopted_derived"
    assert (
        db.execute("SELECT adopted_at FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0]
        is not None
    )


def test_a_derived_from_a_stale_group_is_rejected(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id, digest="stale-digest")
    destination_id = a_destination(destinations)

    assert uploads.create_pairs([output_id], [destination_id])[0].result == "rejected"


def test_a_complete_record_is_a_no_op(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    uploads.create_pairs([media_id], [destination_id])
    revision_id = destinations.current(destination_id)["id"]
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ?", (revision_id,)
    )

    pairs = uploads.create_pairs([media_id], [destination_id])

    assert pairs[0].result == "already_complete"
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 1


def test_an_active_record_is_not_claimed_twice(db, profile, destinations, uploads):
    from mediaferry.db.jobs import JobStore

    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    uploads.create_pairs([media_id], [destination_id])
    revision_id = destinations.current(destination_id)["id"]
    # claim_job_id は job(id) への外部キー。実在するジョブでないと入らない。
    job_id = JobStore(db).enqueue("upload", {"destination_id": destination_id})
    db.execute(
        "UPDATE upload_record SET state = 'uploading', claim_job_id = ?, claim_token = 't',"
        " claim_expires_at = '2999-01-01T00:00:00+00:00', destination_revision_id = ?",
        (job_id, revision_id),
    )
    assert uploads.create_pairs([media_id], [destination_id])[0].result == "already_active"


def test_a_waiting_record_is_left_to_the_approval_flow(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    uploads.create_pairs([media_id], [destination_id])
    db.execute("UPDATE upload_record SET state = 'awaiting_datetime_approval'")
    assert uploads.create_pairs([media_id], [destination_id])[0].result == "awaiting_approval"


def test_a_failed_record_is_queued_again_without_changing_its_rule(
    db, profile, destinations, uploads
):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="failed", verification=None)
    destination_id = a_destination(destinations)
    uploads.create_pairs([members[0][0]], [destination_id])
    db.execute("UPDATE upload_record SET state = 'failed', attempts = 3, last_error = 'boom'")

    pairs = uploads.create_pairs([members[0][0]], [destination_id])

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert pairs[0].result == "retry_queued"
    assert row["state"] == "pending"
    # 選択の根拠は書き換えない。再試行は「なぜ送信を許可したか」を変えない。
    assert row["selection_rule"] == "failed_group_member"
    assert row["attempts"] == 3


def test_an_invalidated_record_is_not_reused(db, profile, destinations, uploads):
    """`_existing` の無効化判定は保険として残る（§10）.

    `_pair` は無効化された行を渡さないので、この経路は `create_pairs` からは
    到達しない。`_existing` を直接呼んで、判断そのものが生きていることを確かめる。
    """
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    record_id = uploads.create_pairs([media_id], [destination_id])[0].record_id
    db.execute(
        "UPDATE upload_record SET invalidated_at = '2026-08-17T00:00:00+00:00',"
        " invalidated_reason = 'group changed' WHERE id = ?",
        (record_id,),
    )
    media = db.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record_id,)).fetchone()

    result = uploads._existing(media, destination_id, row)

    assert result.result == "rejected"
    assert "無効" in result.reason


def test_pairs_for_two_destinations_are_independent(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    home = a_destination(destinations, "home")
    family = a_destination(destinations, "family")
    uploads.create_pairs([media_id], [home])
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ?",
        (destinations.current(home)["id"],),
    )

    pairs = uploads.create_pairs([media_id], [home, family])

    assert results_of(pairs)[(media_id, home)] == "already_complete"
    assert results_of(pairs)[(media_id, family)] == "created"


def test_an_invalidated_record_does_not_block_a_new_one(db, uploads, destinations, profile):
    """**無効化された記録は無いものとして扱う。**

    再確認が消滅を無効化すると（§9.10）、そのメディアは「まだ送っていない」に
    戻る。ここで古い行を拾って断ると、画面には「まだ送っていない」と出るのに
    送れない。`design.md` §10 の遷移表が既に「再利用しない」と書いている。
    """
    destination_id = a_destination(destinations)
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    first = uploads.create_pairs([media_id], [destination_id])[0]
    # `state = 'complete'` は `destination_revision_id` 必須（`upload_record` の CHECK）。
    revision_id = destinations.current(destination_id)["id"]
    db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = NULL,"
        " remote_checked_at = ?, invalidated_at = ?, invalidated_reason = 'remote_missing',"
        " destination_revision_id = ?"
        " WHERE id = ?",
        (now_iso(), now_iso(), revision_id, first.record_id),
    )

    again = uploads.create_pairs([media_id], [destination_id])[0]

    assert again.result == "created"
    assert again.record_id != first.record_id
    live = db.execute(
        "SELECT id FROM upload_record WHERE media_file_id = ? AND invalidated_at IS NULL",
        (media_id,),
    ).fetchall()
    assert [row["id"] for row in live] == [again.record_id]


def a_redone_group_sharing_one_output(db, profile, members, derived):
    """**構成を変えないやり直し。** 2 つのグループが同じ出力を指す状態を作る.

    `merges.supersede` と同じ順序で組む —— 旧グループを先に向け直し、外部キーの
    検査を commit まで遅らせる（`UNIQUE(input_digest) WHERE superseded_by_id IS
    NULL` があるので、digest が同じ新しい行を先に入れると必ず衝突する）。
    出力が同じ `media_file` 行になるのは、`rel_path` が変わらないので
    `publisher._commit` が同じ行を再利用するため。

    旧グループが先に入るので **rowid は旧の方が小さい**。並び順を指定しない
    `fetchone()` は、決定的に旧（superseded）側を拾う。
    """
    from mediaferry.core.merge.digest import input_digest
    from mediaferry.db.connection import immediate
    from mediaferry.ids import new_id

    from .test_schema_artifacts import a_merge_group
    from .test_selection import PASSED

    digest = input_digest(
        [(media_id, sha1) for media_id, sha1 in members],
        profile.definition.merge,
        profile.revision_id,
    )
    old = a_group(db, profile, members, output_id=derived, digest=digest)
    new = new_id()
    with immediate(db):
        db.execute("PRAGMA defer_foreign_keys = ON")
        db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (new, old))
        a_merge_group(
            db,
            (profile.profile_id, profile.revision_id),
            digest,
            id=new,
            status="merged",
            verification_json=PASSED,
            output_media_file_id=derived,
        )
        for position, (media_id, _) in enumerate(members):
            db.execute(
                "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
                " VALUES (?, ?, ?, 1)",
                (new, media_id, position),
            )
    return old, new


def test_a_redone_derived_is_still_sendable(db, profile, destinations, uploads):
    """**持ち主のグループは「現行」を拾う**（`db/media.py` の `owner_group`）.

    構成を変えないやり直しでは、旧グループが `output_media_file_id` を持ったまま
    superseded になり、新グループが同じ出力を指す。並び順を指定せずに引くと索引順
    ＝ rowid の小さい順で**古い superseded 側**を決定的に拾い、`group_is_current`
    が偽になる。結果として、一覧が「送れる」と数えているファイルの送信が
    「生成元のグループが現在の構成と一致しない」で断られる。
    """
    members = a_pair(db, profile)
    derived = a_derived(db, profile)
    old, new = a_redone_group_sharing_one_output(db, profile, members, derived)

    dest = a_destination(destinations)
    pairs = uploads.create_pairs([derived], [dest])

    assert [pair.result for pair in pairs] == ["created"], pairs[0].reason
    record = uploads.get(pairs[0].record_id)
    assert record["merge_group_id"] == new, "持ち主は現行のグループ"
