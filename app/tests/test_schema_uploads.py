import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.ids import new_id

from .test_schema_artifacts import a_media_file
from .test_schema_jobs import a_job
from .test_schema_sources import a_profile


def a_destination(db, name="home", epoch=1):
    dest_id, cred_id, rev_id = new_id(), new_id(), new_id()
    db.execute(
        "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
        " VALUES (?, ?, 'immich', 1, ?)",
        (dest_id, name, now_iso()),
    )
    db.execute(
        "INSERT INTO destination_credential"
        " (id, destination_id, revision, secret_encrypted, key_fingerprint, created_at)"
        " VALUES (?, ?, 1, X'00', 'kf', ?)",
        (cred_id, dest_id, now_iso()),
    )
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch, base_url,"
        " credential_id, created_at) VALUES (?, ?, 1, ?, 'http://immich.invalid', ?, ?)",
        (rev_id, dest_id, epoch, cred_id, now_iso()),
    )
    db.execute(
        "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?", (rev_id, dest_id)
    )
    return dest_id, rev_id, cred_id


def an_upload(db, dest, media_id, **over):
    dest_id, rev_id, _ = dest
    row = {
        "id": new_id(),
        "destination_id": dest_id,
        "target_epoch": 1,
        "media_file_id": media_id,
        "state": "pending",
        "selection_rule": "default",
        "origin": "unknown",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO upload_record ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_destination_revision_is_immutable(db):
    _, rev_id, _ = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE destination_revision SET base_url = 'http://x.invalid' WHERE id = ?",
            (rev_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM destination_revision WHERE id = ?", (rev_id,))


def test_a_revision_cannot_borrow_another_destinations_credential(db):
    """他宛先の鍵で送ると、確認画面と違う先へ資産が渡る."""
    first = a_destination(db, name="a")
    _, _, other_cred = a_destination(db, name="b")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
            " base_url, credential_id, created_at)"
            " VALUES (?, ?, 2, 1, 'http://immich.invalid', ?, ?)",
            (new_id(), first[0], other_cred, now_iso()),
        )


def test_current_revision_must_belong_to_the_destination(db):
    first = a_destination(db, name="a")
    _, other_rev, _ = a_destination(db, name="b")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
            (other_rev, first[0]),
        )


def test_upload_record_revision_must_match_destination_and_epoch(db):
    """epoch を跨いだ revision を掴むと、進めた向き先の履歴が混ざる."""
    profile = a_profile(db)
    dest = a_destination(db)
    media_id = a_media_file(db, profile)
    an_upload(db, dest, media_id, destination_revision_id=dest[1])
    another_revision(db, dest, epoch=2)
    with pytest.raises(sqlite3.IntegrityError):
        # epoch 2 の行に epoch 1 の revision を掴ませる
        an_upload(
            db, dest, a_media_file(db, profile), target_epoch=2, destination_revision_id=dest[1]
        )


def another_revision(db, dest, epoch):
    """向き先を変えて epoch を進めた新しいリビジョン."""
    dest_id, _, cred_id = dest
    rev_id = new_id()
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch, base_url,"
        " credential_id, created_at) VALUES (?, ?, 2, ?, 'http://other.invalid', ?, ?)",
        (rev_id, dest_id, epoch, cred_id, now_iso()),
    )
    return rev_id


def test_one_record_per_destination_epoch_and_media(db):
    profile = a_profile(db)
    dest = a_destination(db)
    media_id = a_media_file(db, profile)
    an_upload(db, dest, media_id)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, media_id)
    # epoch を進めれば、旧記録を監査履歴として残したまま送り直せる
    another_revision(db, dest, epoch=2)
    an_upload(db, dest, media_id, target_epoch=2)


def test_a_record_cannot_name_an_epoch_that_has_no_revision(db):
    """複合 FK は destination_revision_id が NULL だと効かない."""
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError, match="epoch"):
        an_upload(db, dest, a_media_file(db, profile), target_epoch=7)


def test_the_identity_of_a_record_cannot_be_rewritten(db):
    """書き換えられると、INSERT 時の epoch guard も複合 FK も迂回できる."""
    profile = a_profile(db)
    dest = a_destination(db)
    record_id = an_upload(db, dest, a_media_file(db, profile))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE upload_record SET target_epoch = 9 WHERE id = ?", (record_id,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE upload_record SET media_file_id = ? WHERE id = ?",
            (a_media_file(db, profile), record_id),
        )


def test_an_active_record_must_name_its_owner_and_revision(db):
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), state="uploading")


def test_a_terminal_record_must_not_keep_a_claim(db):
    profile = a_profile(db)
    dest = a_destination(db)
    job_id = a_job(db, type="upload")
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(
            db,
            dest,
            a_media_file(db, profile),
            state="complete",
            destination_revision_id=dest[1],
            claim_job_id=job_id,
            claim_token="t",
            claim_expires_at=now_iso(),
        )


def test_a_complete_record_remembers_which_revision_it_used(db):
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), state="complete")
    an_upload(
        db, dest, a_media_file(db, profile), state="complete", destination_revision_id=dest[1]
    )


def test_claim_columns_are_all_null_or_all_set(db):
    """未来の期限だけが残ると、明示操作しても期限まで claim できなくなる."""
    profile = a_profile(db)
    dest = a_destination(db)
    job_id = a_job(db, type="upload")
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(
            db,
            dest,
            a_media_file(db, profile),
            state="checking",
            destination_revision_id=dest[1],
            claim_job_id=job_id,
        )
    an_upload(
        db,
        dest,
        a_media_file(db, profile),
        state="checking",
        destination_revision_id=dest[1],
        claim_job_id=job_id,
        claim_token="t",
        claim_expires_at=now_iso(),
    )


def test_selection_rule_cannot_be_rewritten(db):
    """再試行で上書きすると、なぜ送信を許可したかが失われる."""
    profile = a_profile(db)
    dest = a_destination(db)
    record_id = an_upload(db, dest, a_media_file(db, profile))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE upload_record SET selection_rule = 'adopted_derived' WHERE id = ?",
            (record_id,),
        )
    # 状態を進めること自体は妨げない
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE id = ?", (record_id,))


def test_first_check_result_is_write_once(db):
    """初回 checking の結果は pre_existing の証明に使う。書き換えられては困る."""
    profile = a_profile(db)
    dest = a_destination(db)
    record_id = an_upload(db, dest, a_media_file(db, profile))
    db.execute("UPDATE upload_record SET first_check_result = 'reject' WHERE id = ?", (record_id,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE upload_record SET first_check_result = 'accept' WHERE id = ?", (record_id,)
        )


def test_states_and_origins_are_constrained(db):
    profile = a_profile(db)
    dest = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), state="uploaded")
    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, a_media_file(db, profile), origin="probably_ours")


def test_purged_credentials_keep_only_the_fingerprint(db):
    _, _, cred_id = a_destination(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE destination_credential SET purged_at = ? WHERE id = ?", (now_iso(), cred_id)
        )
    db.execute(
        "UPDATE destination_credential SET secret_encrypted = NULL, purged_at = ? WHERE id = ?",
        (now_iso(), cred_id),
    )
