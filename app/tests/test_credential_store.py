import base64
import os

import pytest

from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore, CredentialUnusable

SECRET = "immich-api-key-value"  # noqa: S105


@pytest.fixture
def box():
    return SecretBox(os.urandom(32))


@pytest.fixture
def destination(db):
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    destination_id = new_id()
    db.execute(
        "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
        " VALUES (?, 'home', 'immich', 1, ?)",
        (destination_id, now_iso()),
    )
    return destination_id


def test_a_stored_secret_comes_back(db, box, destination):
    store = CredentialStore(db, box)
    credential_id = store.store(destination, SECRET)
    assert store.reveal(credential_id) == SECRET


def test_the_ciphertext_is_not_the_secret(db, box, destination):
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    blob = db.execute(
        "SELECT secret_encrypted FROM destination_credential WHERE id = ?", (credential_id,)
    ).fetchone()[0]
    assert SECRET.encode("utf-8") not in blob


def test_the_revision_increases_per_destination(db, box, destination):
    store = CredentialStore(db, box)
    first = store.store(destination, SECRET)
    second = store.store(destination, "rotated")
    revisions = {
        row["id"]: row["revision"]
        for row in db.execute("SELECT id, revision FROM destination_credential")
    }
    assert revisions[first] == 1
    assert revisions[second] == 2


def test_a_wrong_master_key_is_reported_not_overwritten(db, box, destination):
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    other = CredentialStore(db, SecretBox(os.urandom(32)))
    with pytest.raises(CredentialUnusable):
        other.reveal(credential_id)
    # 行はそのまま残る。再登録できるように「要再登録」として見せる。
    row = db.execute(
        "SELECT secret_encrypted, purged_at FROM destination_credential WHERE id = ?",
        (credential_id,),
    ).fetchone()
    assert row["secret_encrypted"] is not None
    assert row["purged_at"] is None


def test_a_row_moved_to_another_destination_does_not_decrypt(db, box, destination):
    """AAD に destination_id を含める. 行の差し替えを復号で検出する."""
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    other_id = new_id()
    db.execute(
        "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
        " VALUES (?, 'other', 'immich', 1, ?)",
        (other_id, now_iso()),
    )
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    db.execute(
        "UPDATE destination_credential SET destination_id = ? WHERE id = ?",
        (other_id, credential_id),
    )
    with pytest.raises(CredentialUnusable):
        CredentialStore(db, box).reveal(credential_id)


def test_purging_keeps_the_referenced_credential(db, box, destination):
    """参照が絶えた旧版だけを消す. 現行を消すと宛先が使えなくなる."""
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    store = CredentialStore(db, box)
    old = store.store(destination, SECRET)
    current = store.store(destination, "rotated")
    revision_id = new_id()
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
        " base_url, credential_id, created_at) VALUES (?, ?, 1, 1, ?, ?, ?)",
        (revision_id, destination, "http://immich.invalid", current, now_iso()),
    )

    assert store.purge_unreferenced(destination) == 1

    rows = {
        row["id"]: row
        for row in db.execute(
            "SELECT * FROM destination_credential WHERE destination_id = ?", (destination,)
        )
    }
    assert rows[old]["secret_encrypted"] is None
    assert rows[old]["purged_at"] is not None
    assert rows[old]["key_fingerprint"]  # 監査のために指紋と作成時刻は残す
    assert rows[current]["secret_encrypted"] is not None


def test_a_purged_credential_cannot_be_revealed(db, box, destination):
    store = CredentialStore(db, box)
    credential_id = store.store(destination, SECRET)
    store.purge_unreferenced(destination)
    with pytest.raises(CredentialUnusable):
        store.reveal(credential_id)


def test_the_secret_is_not_in_the_exception_text(db, box, destination):
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    other = CredentialStore(db, SecretBox(base64.b64decode(base64.b64encode(os.urandom(32)))))
    with pytest.raises(CredentialUnusable) as caught:
        other.reveal(credential_id)
    assert SECRET not in str(caught.value)


def test_store_locked_refuses_to_run_without_a_transaction(db, box, destination):
    """docstring だけの約束にしない.

    単独で呼ばれると autocommit になり、宛先の作成・編集の途中で落ちたときに
    孤立した credential を残せてしまう（§8「編集は原子的に反映する」）。
    """
    store = CredentialStore(db, box)
    with pytest.raises(RuntimeError):
        store.store_locked(destination, SECRET)
    assert db.execute("SELECT count(*) FROM destination_credential").fetchone()[0] == 0
