import os

import pytest

from mediaferry.core.crypto import SecretAad, SecretBox, SecretCorrupt, WrongKeyError


def an_aad(**over):
    fields = {
        "credential_id": "cred-1",
        "destination_id": "dest-1",
        "revision": 1,
        "schema_version": 1,
    }
    fields.update(over)
    return SecretAad(**fields)


@pytest.fixture
def box():
    return SecretBox(os.urandom(32))


def test_round_trip(box):
    blob = box.encrypt("immich-api-key", an_aad())
    assert box.decrypt(blob, an_aad()) == "immich-api-key"


def test_the_plaintext_does_not_appear_in_the_blob(box):
    assert b"immich-api-key" not in box.encrypt("immich-api-key", an_aad())


def test_nonce_is_fresh_for_every_encryption(box):
    first = box.encrypt("k", an_aad())
    second = box.encrypt("k", an_aad())
    assert first != second


def test_moving_a_row_to_another_destination_is_detected(box):
    """AAD に destination_id を含めるので、行の差し替えが復号で落ちる."""
    blob = box.encrypt("k", an_aad())
    with pytest.raises(SecretCorrupt):
        box.decrypt(blob, an_aad(destination_id="dest-2"))
    with pytest.raises(SecretCorrupt):
        box.decrypt(blob, an_aad(revision=2))


def test_a_different_key_is_reported_as_wrong_key_not_corruption(box):
    """誤鍵を「壊れた credential」として上書きしてしまわないため、
    復号を試みる前に key_id で弾く."""
    other = SecretBox(os.urandom(32))
    blob = box.encrypt("k", an_aad())
    with pytest.raises(WrongKeyError) as exc:
        other.decrypt(blob, an_aad())
    assert exc.value.expected == other.key_id
    assert exc.value.found == box.key_id


def test_key_id_is_stable_and_does_not_leak_the_key():
    key = os.urandom(32)
    assert SecretBox(key).key_id == SecretBox(key).key_id
    assert SecretBox(key).key_id.encode() not in key


def test_a_truncated_blob_is_corruption_not_a_crash(box):
    blob = box.encrypt("k", an_aad())
    with pytest.raises(SecretCorrupt):
        box.decrypt(blob[:-1], an_aad())
    with pytest.raises(SecretCorrupt):
        box.decrypt(b"nonsense", an_aad())


def test_the_key_must_be_32_bytes():
    with pytest.raises(ValueError, match="32"):
        SecretBox(os.urandom(16))
