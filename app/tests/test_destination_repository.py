import os

import pytest

from mediaferry.core.crypto import SecretBox
from mediaferry.core.destinations.urls import EndpointRejected
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import (
    DestinationRepository,
    EpochDecisionRequired,
    RemoteIdentity,
)

USER_A = RemoteIdentity(remote_user_id="user-a", server_instance_id=None)
USER_B = RemoteIdentity(remote_user_id="user-b", server_instance_id=None)


@pytest.fixture
def repo(db):
    return DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))


def a_destination(repo, name="home", base_url="http://immich.invalid:2283"):
    return repo.create(
        name=name, base_url=base_url, public_url=None, secret="key-1", identity=USER_A
    )


def test_creating_a_destination_stores_a_verified_revision(repo, db):
    destination_id = a_destination(repo)
    row = repo.current(destination_id)
    assert row["revision"] == 1
    assert row["target_epoch"] == 1
    assert row["base_url"] == "http://immich.invalid:2283"
    assert row["remote_user_id"] == "user-a"
    assert row["verified_at"] is not None
    assert repo.secret_of(row["id"]) == "key-1"


def test_the_url_is_normalised_before_it_is_stored(repo):
    destination_id = repo.create(
        name="trailing",
        base_url="http://immich.invalid:2283/",
        public_url="HTTPS://Photos.Invalid/",
        secret="key-1",
        identity=USER_A,
    )
    row = repo.current(destination_id)
    assert row["base_url"] == "http://immich.invalid:2283"
    assert row["public_url"] == "https://photos.invalid"


def test_an_unusable_url_is_refused_before_anything_is_written(repo, db):
    with pytest.raises(EndpointRejected):
        repo.create(
            name="bad",
            base_url="javascript:alert(1)",
            public_url=None,
            secret="key-1",
            identity=USER_A,
        )
    assert db.execute("SELECT count(*) FROM upload_destination").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM destination_credential").fetchone()[0] == 0


def test_rotating_the_key_keeps_the_epoch(repo):
    destination_id = a_destination(repo)
    repo.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-2",
        identity=USER_A,
    )
    row = repo.current(destination_id)
    assert row["revision"] == 2
    assert row["target_epoch"] == 1  # 履歴を引き継ぐ
    assert repo.secret_of(row["id"]) == "key-2"


def test_pointing_at_another_account_advances_the_epoch(repo):
    destination_id = a_destination(repo)
    repo.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-1",
        identity=USER_B,
    )
    assert repo.current(destination_id)["target_epoch"] == 2


def test_a_changed_host_with_the_same_user_needs_an_answer(repo):
    """DB を複製・復元した別ライブラリかもしれない. 自動判定しない."""
    destination_id = a_destination(repo)
    with pytest.raises(EpochDecisionRequired):
        repo.add_revision(
            destination_id,
            base_url="http://other.invalid:2283",
            public_url=None,
            secret="key-1",
            identity=USER_A,
        )


def test_the_answer_decides_whether_the_history_carries_over(repo):
    destination_id = a_destination(repo)
    repo.add_revision(
        destination_id,
        base_url="http://other.invalid:2283",
        public_url=None,
        secret="key-1",
        identity=USER_A,
        same_library=True,
    )
    assert repo.current(destination_id)["target_epoch"] == 1

    repo.add_revision(
        destination_id,
        base_url="http://third.invalid:2283",
        public_url=None,
        secret="key-1",
        identity=USER_A,
        same_library=False,
    )
    assert repo.current(destination_id)["target_epoch"] == 2


def test_a_missing_identity_is_refused_atomically(repo, db):
    """向き先が分からない設定は保存しない.

    保存すると preflight が必ず失敗する宛先ができ、しかも epoch は進んでいる。
    """
    from mediaferry.db.destinations import IdentityUnknown

    destination_id = a_destination(repo)
    before = repo.current(destination_id)["id"]
    with pytest.raises(IdentityUnknown):
        repo.add_revision(
            destination_id,
            base_url="http://immich.invalid:2283",
            public_url=None,
            secret="key-2",
            identity=RemoteIdentity(remote_user_id=None, server_instance_id=None),
        )
    assert repo.current(destination_id)["id"] == before
    assert db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1
    # 資格情報も増えない。
    assert db.execute("SELECT count(*) FROM destination_credential").fetchone()[0] == 1


def test_a_failure_midway_leaves_nothing_behind(repo, db, monkeypatch):
    """1 回の編集は 1 トランザクション. 継ぎ目で落ちても中途半端にしない."""
    from mediaferry.db import destinations as module

    destination_id = a_destination(repo)
    monkeypatch.setattr(
        module.DestinationRepository,
        "_write_revision",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("継ぎ目で落ちた")),
    )
    with pytest.raises(RuntimeError):
        repo.add_revision(
            destination_id,
            base_url="http://immich.invalid:2283",
            public_url=None,
            secret="key-2",
            identity=USER_A,
        )
    assert db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1
    # 孤立した credential を残さない。
    assert db.execute("SELECT count(*) FROM destination_credential").fetchone()[0] == 1


def test_revisions_are_immutable(repo, db):
    import sqlite3

    destination_id = a_destination(repo)
    revision_id = repo.current(destination_id)["id"]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE destination_revision SET base_url = 'http://x.invalid' WHERE id = ?",
            (revision_id,),
        )


def test_the_previous_key_stays_while_a_revision_references_it(repo, db):
    destination_id = a_destination(repo)
    first = repo.current(destination_id)["credential_id"]
    repo.add_revision(
        destination_id,
        base_url="http://immich.invalid:2283",
        public_url=None,
        secret="key-2",
        identity=USER_A,
    )
    # 旧リビジョンは残るので、その credential も参照されたまま。
    assert (
        db.execute(
            "SELECT secret_encrypted IS NOT NULL FROM destination_credential WHERE id = ?", (first,)
        ).fetchone()[0]
        == 1
    )


def test_the_same_account_is_warned_not_refused(repo):
    a_destination(repo, name="internal")
    warnings = repo.same_account_warnings(USER_A)
    assert warnings and "internal" in warnings[0]
    # 拒否も統合もしない。同じアカウントを別名で持つのは正当な使い方。
    second = repo.create(
        name="vpn",
        base_url="http://vpn.invalid:2283",
        public_url=None,
        secret="key-1",
        identity=USER_A,
    )
    assert repo.current(second)["remote_user_id"] == "user-a"


def test_archiving_takes_it_out_of_the_list_but_keeps_the_history(repo, db):
    destination_id = a_destination(repo)
    repo.archive(destination_id)
    assert repo.list_destinations() == []
    assert len(repo.list_destinations(include_archived=True)) == 1
    assert db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1


def test_disabling_keeps_it_listed(repo):
    destination_id = a_destination(repo)
    repo.set_enabled(destination_id, False)
    rows = repo.list_destinations()
    assert [row["enabled"] for row in rows] == [0]
