import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.jobs.preflight import PreflightCache, PreflightFailed

from .fake_immich import API_KEY


@pytest.fixture
def world(db, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    repo = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = repo.create(
        name="home",
        base_url=server.url,
        public_url=None,
        secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    opened = []

    def open_client(revision):
        opened.append(revision["id"])
        return ImmichClient(revision["base_url"], API_KEY)

    return server, repo, destination_id, PreflightCache(repo, open_client), opened


def test_a_matching_target_passes(world):
    _, repo, destination_id, preflight, _ = world
    preflight.assert_target(repo.current(destination_id)["id"])


def test_the_check_is_shared_within_the_job(world):
    _, repo, destination_id, preflight, opened = world
    revision_id = repo.current(destination_id)["id"]
    preflight.assert_target(revision_id)
    preflight.assert_target(revision_id)
    assert opened == [revision_id]


def test_the_check_is_repeated_after_the_ttl(world):
    """長いジョブでは、途中で向き先が差し替わりうる."""
    from mediaferry.jobs.preflight import PreflightCache

    server, repo, destination_id, _, opened = world
    revision_id = repo.current(destination_id)["id"]
    preflight = PreflightCache(repo, _opener(server, opened), ttl_seconds=0)

    preflight.assert_target(revision_id)
    server.user_id = "someone-else"
    with pytest.raises(PreflightFailed):
        preflight.assert_target(revision_id)


def _opener(server, opened):
    from mediaferry.adapters.immich import ImmichClient

    def open_client(revision):
        opened.append(revision["id"])
        return ImmichClient(revision["base_url"], API_KEY)

    return open_client


def test_a_different_user_stops_the_revision(world):
    server, repo, destination_id, preflight, _ = world
    # 同じ URL の先が別のライブラリに差し替わった。
    server.user_id = "someone-else"
    with pytest.raises(PreflightFailed):
        preflight.assert_target(repo.current(destination_id)["id"])


def test_an_unreachable_target_stops_the_revision(world):
    server, repo, destination_id, preflight, _ = world
    server.fail_next = 1
    with pytest.raises(PreflightFailed):
        preflight.assert_target(repo.current(destination_id)["id"])


def test_a_failure_is_remembered_so_the_rest_do_not_try(world):
    server, repo, destination_id, preflight, opened = world
    server.user_id = "someone-else"
    revision_id = repo.current(destination_id)["id"]
    for _ in range(3):
        with pytest.raises(PreflightFailed):
            preflight.assert_target(revision_id)
    assert opened == [revision_id]


def test_a_revision_without_a_recorded_identity_is_refused(world, db, monkeypatch):
    """**相手も id を返さない場合が危ない。**

    記録が無いだけなら「観測値と一致しない」で弾けるが、相手が id を返さない
    実装だと `None == None` で一致してしまい、検証していないリビジョンが
    「確認済み」として通る。
    """
    from mediaferry.adapters.immich import ImmichClient

    _, repo, destination_id, preflight, _ = world
    revision_id = repo.current(destination_id)["id"]
    # 検証していない（remote_user_id が無い）リビジョンは突き合わせようがない。
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("DROP TRIGGER destination_revision_no_update")
    db.execute("UPDATE destination_revision SET remote_user_id = NULL WHERE id = ?", (revision_id,))
    db.execute("PRAGMA foreign_keys = ON")
    monkeypatch.setattr(ImmichClient, "users_me", lambda self: {})

    with pytest.raises(PreflightFailed):
        preflight.assert_target(revision_id)


def test_an_unknown_revision_is_refused(world):
    _, _, _, preflight, _ = world
    with pytest.raises(PreflightFailed):
        preflight.assert_target("no-such-revision")
