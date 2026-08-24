import base64
import hashlib
import json
import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import ClaimLost, UploadRepository
from mediaferry.jobs.preflight import PreflightCache, PreflightFailed
from mediaferry.jobs.uploader import Uploader, _Reported

from .fake_immich import API_KEY
from .test_schema_artifacts import a_media_file, a_merge_group

PAYLOAD = b"video-bytes"
CAPTURED = "2026-08-17T14:30:00+09:00"


@pytest.fixture
def world(db, data_root, immich):
    import hashlib

    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home",
        base_url=server.url,
        public_url=None,
        secret=API_KEY,
        identity=RemoteIdentity.observed(server.user_id),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)

    directory = data_root / "library" / "dji-osmo" / "DCIM"
    directory.mkdir(parents=True)
    (directory / "A.MP4").write_bytes(PAYLOAD)
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
        size_bytes=len(PAYLOAD),
        captured_at=CAPTURED,
        mtime_ns=1_700_000_000_000_000_000,
    )
    uploads.create_pairs([media_id], [destination_id])

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    uploader = Uploader(
        db,
        uploads,
        destinations,
        ProfileRegistry(db),
        data_root,
        open_client,
        preflight=PreflightCache(destinations, open_client),
    )
    return server, uploader, ctx, uploads, destinations, destination_id, media_id


def record_of(db):
    return db.execute("SELECT * FROM upload_record").fetchone()


def test_a_new_asset_is_uploaded_tagged_and_dated(world, db):
    server, uploader, ctx, _, _, destination_id, media_id = world

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 1
    row = record_of(db)
    assert row["state"] == "complete"
    assert row["origin"] == "created_by_us"
    assert row["remote_asset_id"] == "asset-1"
    assert row["first_check_result"] == "accept"
    assert server.uploads[0]["deviceAssetId"] == f"mediaferry:{media_id}"
    # 既定の DJI プロファイルはタグを持つ。自作なので付ける。
    assert server.tagged
    assert server.datetimes[row["remote_asset_id"]] == CAPTURED


def test_an_asset_that_already_exists_is_not_uploaded_again(world, db):
    import base64
    import hashlib

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"

    uploader.run(ctx, destination_id)

    row = record_of(db)
    assert server.uploads == []
    assert row["remote_asset_id"] == "asset-existing"
    assert row["origin"] == "pre_existing"
    assert row["first_check_result"] == "reject"
    # 自作と証明できないので、日時は自動で書き換えない。
    assert row["state"] == "awaiting_datetime_approval"
    assert server.datetimes == {}


def test_a_trashed_asset_is_recorded_as_trashed(world, db):
    import base64
    import hashlib

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"
    server.trashed.add("asset-existing")

    uploader.run(ctx, destination_id)

    assert record_of(db)["remote_is_trashed"] == 1


def _an_existing_asset(server):
    import base64
    import hashlib

    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"


def _tag_policy(monkeypatch, tag_pre_existing):
    """プロファイルの `tag_pre_existing` だけを差し替える."""
    from dataclasses import replace

    real = ProfileRegistry.by_id

    def by_id(self, profile_id):
        ref = real(self, profile_id)
        immich = replace(ref.definition.immich, tag_pre_existing=tag_pre_existing)
        return replace(ref, definition=replace(ref.definition, immich=immich))

    monkeypatch.setattr(ProfileRegistry, "by_id", by_id)


def test_a_pre_existing_asset_is_tagged_when_the_profile_says_so(world, db, monkeypatch):
    """既定の DJI プロファイルは `tag_pre_existing: true`（design §6）."""
    server, uploader, ctx, _, _, destination_id, _ = world
    _an_existing_asset(server)
    _tag_policy(monkeypatch, True)

    uploader.run(ctx, destination_id)

    assert server.tagged


def test_a_pre_existing_asset_is_not_tagged_when_the_profile_forbids_it(world, db, monkeypatch):
    """自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めた
    タグを付けない（§9.10）."""
    server, uploader, ctx, _, _, destination_id, _ = world
    _an_existing_asset(server)
    _tag_policy(monkeypatch, False)

    uploader.run(ctx, destination_id)

    assert server.tagged == {}


def test_the_preflight_stops_everything_before_a_byte_is_sent(world, db):
    """preflight は claim の後だが、**リモートに触る前**なので pending へ戻す."""
    server, uploader, ctx, _, _, destination_id, _ = world
    server.user_id = "someone-else"

    with pytest.raises(PreflightFailed):
        uploader.run(ctx, destination_id)

    assert server.uploads == []
    row = record_of(db)
    assert row["state"] == "pending"
    assert row["claim_job_id"] is None


def test_a_cancel_while_the_upload_is_in_flight_stops_the_commit(world, db, monkeypatch):
    """送信は成功しても、キャンセル後の commit は通さない（§8）.

    通すと、画面はキャンセル済みなのにタグと日時まで進む。
    """
    server, uploader, ctx, _, _, destination_id, _ = world
    real = ImmichClient.upload_asset

    def cancel_then_upload(self, *args, **kwargs):
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", cancel_then_upload)
    uploader.run(ctx, destination_id)

    row = record_of(db)
    # サーバには上がったかもしれないので needs_recheck。タグも日時も付けない。
    assert row["state"] == "needs_recheck"
    # **結果を commit させない。** 通すと「キャンセル済みなのに送信済みの記録」ができる。
    assert row["remote_asset_id"] is None
    assert server.tagged == {}
    assert server.datetimes == {}


def test_the_target_is_re_checked_before_the_tags_when_the_ttl_expired(world, db, monkeypatch):
    """送信が TTL を跨いだら、タグと日時の前に向き先を取り直す."""
    server, uploader, ctx, _, _, destination_id, _ = world
    uploader._preflight._ttl = 0  # noqa: SLF001 - TTL 切れを再現する
    real = ImmichClient.upload_asset

    def upload_then_move(self, *args, **kwargs):
        outcome = real(self, *args, **kwargs)
        # 送信中に別のライブラリへ差し替わった。
        server.user_id = "someone-else"
        return outcome

    monkeypatch.setattr(ImmichClient, "upload_asset", upload_then_move)
    with pytest.raises(PreflightFailed):
        uploader.run(ctx, destination_id)

    # 別ライブラリにタグも日時も書かない。
    assert server.tagged == {}
    assert server.datetimes == {}


def test_an_asset_we_uploaded_stays_ours_after_a_retry(world, db, monkeypatch):
    """**アップロード成功後の後処理が失敗しても `created_by_us` を降格させない。**

    再開時の `bulk-upload-check` は、自分が上げた資産なので当然 `reject` を返す。
    そこで origin を付け直すと `unknown` になり、自作の資産なのにタグが付かず、
    日時の補正まで承認待ちへ変わる。
    """
    from mediaferry.adapters.immich import ImmichUnavailable

    server, uploader, ctx, _, _, destination_id, _ = world
    monkeypatch.setattr("mediaferry.jobs.uploader.BACKOFF_BASE_SECONDS", 0.01)
    calls = {"n": 0}
    real_find = ImmichClient.find_tag

    def once_unavailable(self, name):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise ImmichUnavailable("GET /api/tags が 503")
        return real_find(self, name)

    monkeypatch.setattr(ImmichClient, "find_tag", once_unavailable)

    uploader.run(ctx, destination_id)

    row = record_of(db)
    assert row["state"] == "complete"
    assert row["origin"] == "created_by_us"
    # 送信は 1 回だけ。2 周目は重複として引き受けている。
    assert len(server.uploads) == 1
    assert server.tagged
    assert server.datetimes[row["remote_asset_id"]] == CAPTURED


def test_a_server_error_is_retried_and_then_failed(world, db, monkeypatch):
    server, uploader, ctx, _, _, destination_id, _ = world
    # **preflight の後で落とす。** `server.fail_next` にすると preflight が先に
    # 落ちて、再試行の分岐を一度も通らない。
    monkeypatch.setattr("mediaferry.jobs.uploader.BACKOFF_BASE_SECONDS", 0.01)
    from mediaferry.adapters.immich import ImmichUnavailable

    def unavailable(*args, **kwargs):
        raise ImmichUnavailable("POST /api/assets/bulk-upload-check が 503")

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", unavailable)

    outcome = uploader.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.failed == 1
    assert row["state"] == "failed"
    assert row["attempts"] == 3
    assert row["last_error"]
    # 秘密は残さない。
    assert API_KEY not in row["last_error"]


def test_an_auth_failure_is_not_retried(world, db, monkeypatch):
    """鍵が失効した場合、何度試しても変わらない. 再試行に回さず落とす.

    preflight を通った後に 401 になる筋書きを作る（鍵の失効は送信の途中でも
    起きる）。preflight の段で落とすと、この分岐を一度も通らない。
    """
    from mediaferry.adapters.immich import ImmichAuthFailed

    server, uploader, ctx, _, _, destination_id, _ = world

    def refuse(*args, **kwargs):
        raise ImmichAuthFailed("POST /api/assets が 401")

    monkeypatch.setattr(ImmichClient, "upload_asset", refuse)
    with pytest.raises(ImmichAuthFailed):
        uploader.run(ctx, destination_id)

    row = record_of(db)
    assert row["attempts"] == 0
    # 送信の成否が不明なまま降りるので、次回は checking から照合し直す。
    assert row["state"] == "needs_recheck"


def test_a_cancel_before_sending_leaves_it_pending(world, db):
    _, uploader, ctx, _, _, destination_id, _ = world
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 0
    assert record_of(db)["state"] == "pending"


def test_a_record_that_loses_its_ground_before_the_send_is_not_sent(world, db, monkeypatch):
    """**送る直前にも §10 の根拠を見る。**

    claim の後の判定は「その時点の状態」でしかない。判定から最初の 1 バイトまでの
    間に根拠が崩れることがある（利用者が結合をやり直した等）。ここを見ないと、
    いま結合中のグループの構成ファイルを送ってしまう。
    """
    server, uploader, ctx, uploads, _, destination_id, media_id = world
    # 「結合に失敗したグループの構成ファイル」として送る根拠を作り直す。
    # `selection_rule` は不変なので、記録は作り直す（trigger が書き換えを拒む）。
    db.execute("DELETE FROM upload_record")
    media = db.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    group_id = a_merge_group(
        db, (media["profile_id"], media["profile_revision_id"]), "digest-1", status="failed"
    )
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group_id, media_id),
    )
    uploads.create_pairs([media_id], [destination_id])
    assert db.execute("SELECT selection_rule FROM upload_record").fetchone()[0] == (
        "failed_group_member"
    )

    real_check = ImmichClient.bulk_upload_check

    def flip_then_check(self, pairs):  # noqa: ANN001, ANN202
        # 判定は通った後。ここで利用者が結合をやり直した。
        db.execute("UPDATE merge_group SET status = 'merging' WHERE id = ?", (group_id,))
        return real_check(self, pairs)

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", flip_then_check)

    uploader.run(ctx, destination_id)

    assert server.uploads == []
    row = record_of(db)
    assert row["state"] == "pending"
    assert row["invalidated_at"] is not None


def test_an_existing_asset_that_loses_its_ground_is_not_tagged(world, db, monkeypatch):
    """**既存資産にも、最初の変更の前に §10 を見直す。**

    「もうリモートにあるので見送っても取り消せない」は、こちらが作った資産に
    しか当てはまらない。他人が上げた資産にタグを付けるのは、こちらが起こす
    最初の変更なので、根拠を失っていたら手を出さない。
    """
    server, uploader, ctx, uploads, _, destination_id, media_id = world
    # 相手には既にある（送信は起きず、reject の分岐へ入る）。
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-9"

    db.execute("DELETE FROM upload_record")
    media = db.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    group_id = a_merge_group(
        db, (media["profile_id"], media["profile_revision_id"]), "digest-1", status="failed"
    )
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group_id, media_id),
    )
    uploads.create_pairs([media_id], [destination_id])

    real_check = ImmichClient.bulk_upload_check

    def flip_then_check(self, pairs):  # noqa: ANN001, ANN202
        db.execute("UPDATE merge_group SET status = 'merging' WHERE id = ?", (group_id,))
        return real_check(self, pairs)

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", flip_then_check)

    uploader.run(ctx, destination_id)

    assert server.tagged == {}
    assert server.datetimes == {}
    row = record_of(db)
    assert row["invalidated_at"] is not None


def test_a_cancel_while_the_target_is_re_checked_stops_before_the_send(world, db, monkeypatch):
    """**preflight の後にもう一度所有権を確かめる。**

    向き先の再確認は相手待ちで、リース（60 秒）より長くなりうる。prepare を
    その前だけに置くと「直前に確かめた」という保証が消え、確認している間に
    commit されたキャンセルを見落として送信を始める。
    """
    server, uploader, ctx, _, _, destination_id, _ = world
    real_users_me = ImmichClient.users_me

    def cancel_during_users_me(self):  # noqa: ANN001, ANN202
        body = real_users_me(self)
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return body

    monkeypatch.setattr(ImmichClient, "users_me", cancel_during_users_me)

    uploader.run(ctx, destination_id)

    # 照合すら始めていない（bulk-upload-check が出ていない）。
    assert ("POST", "/api/assets/bulk-upload-check") not in server.requests
    assert server.uploads == []


def test_a_cancel_right_after_the_claim_sends_no_request(world, db, monkeypatch):
    """**claim の直後にキャンセルされたら、1 要求も出さない。**

    向き先の再確認（`GET /api/users/me`）も鍵を付けた要求なので、
    所有権を確かめる前に出してはいけない（§14）。
    """
    from mediaferry.db.uploads import UploadRepository

    server, uploader, ctx, _, _, destination_id, _ = world
    real_claim = UploadRepository.claim_next

    def cancel_after_claim(self, *args, **kwargs):  # noqa: ANN001, ANN202
        row = real_claim(self, *args, **kwargs)
        if row is not None:
            db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return row

    monkeypatch.setattr(UploadRepository, "claim_next", cancel_after_claim)

    uploader.run(ctx, destination_id)

    assert server.requests == []
    # まだ何も起きていないので、次回は最初からやり直せる。
    assert record_of(db)["state"] == "pending"


def test_a_cancel_during_the_send_asks_for_a_recheck(world, db, monkeypatch):
    """サーバ側の成否が不明なので、次回 checking から照合し直す."""
    server, uploader, ctx, _, _, destination_id, _ = world

    def cancel_then_upload(*args, **kwargs):
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        raise KeyboardInterrupt

    monkeypatch.setattr(ImmichClient, "upload_asset", cancel_then_upload)
    with pytest.raises(KeyboardInterrupt):
        uploader.run(ctx, destination_id)

    assert record_of(db)["state"] == "needs_recheck"


def test_a_record_that_lost_its_grounds_is_refused_not_sent(world, db):
    server, uploader, ctx, _, _, destination_id, _ = world
    db.execute(
        "UPDATE media_file SET missing_at = '2026-08-17T00:00:00+00:00' WHERE role = 'original'"
    )

    outcome = uploader.run(ctx, destination_id)

    assert outcome.skipped == 1
    assert server.uploads == []
    row = record_of(db)
    assert row["invalidated_at"] is not None


def test_the_lease_is_extended_while_the_file_is_sent(world, db, monkeypatch):
    import time

    server, uploader, ctx, _, _, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    beats = []
    monkeypatch.setattr(ctx, "heartbeat", lambda progress=None: beats.append(progress))
    real = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(0.3)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)
    uploader.run(ctx, destination_id)

    assert beats
    assert record_of(db)["state"] == "complete"


def test_the_job_stops_when_there_is_nothing_left(world, db):
    _, uploader, ctx, _, _, destination_id, _ = world
    uploader.run(ctx, destination_id)
    outcome = uploader.run(ctx, destination_id)
    assert (outcome.sent, outcome.failed, outcome.skipped) == (0, 0, 0)


def test_the_claim_is_extended_while_the_file_is_sent(world, db, monkeypatch):
    """リースだけ延ばしても、claim が切れれば結果を commit できない.

    **送信が claim の寿命より長い**状況を作る（短いと延長の有無で差が出ない）。
    claim の寿命を短くするには `claim_next` だけでは足りない ——
    **送信の直前の `prepare_side_effect` も claim を延ばす**（既定 60 秒）ので、
    そちらも短くしないと送信開始の時点で寿命が 60 秒に戻り、心拍の延長
    （`also=`）を消しても落ちないテストになる。
    """
    import time

    server, uploader, ctx, uploads, destinations, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    real = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(1.5)  # claim（1 秒）より長い
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)
    # claim を 1 秒で切れるようにしてから走らせる（取るときも、延ばすときも）。
    monkeypatch.setattr(uploads, "claim_next", _claim_with(uploads, seconds=1))
    monkeypatch.setattr(uploads, "prepare_side_effect", _prepare_with(uploads, seconds=1))
    extended = []
    real_extend = uploads.extend_claim

    def counting_extend(record_id, token, lease_seconds=60):
        extended.append(record_id)
        return real_extend(record_id, token, lease_seconds)

    monkeypatch.setattr(uploads, "extend_claim", counting_extend)

    uploader.run(ctx, destination_id)

    # 心拍が claim を延ばしたので、1 秒で切れる claim を跨いで commit できた。
    assert extended
    assert record_of(db)["state"] == "complete"


def test_the_send_thread_is_awaited_when_the_claim_extension_fails(world, db, monkeypatch):
    """claim の延長が失敗しても、送信スレッドを残したまま抜けない.

    `with_lease_pulse` の `ownership_errors` に `ClaimLost` が入っていないと、
    延長が失敗した瞬間に待つ側だけが例外で抜け、**呼び出し側が失敗を見た後で
    送信スレッドが数十 GiB を送り終える**。抜けた時点で送信が終わっていることを
    見て、その経路を塞ぐ。
    """
    import threading
    import time

    server, uploader, ctx, uploads, destinations, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    finished = threading.Event()
    real = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(0.5)  # 心拍（0.05 秒）より十分に長い
        try:
            return real(self, *args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)

    def refuse_to_extend(record_id, token, lease_seconds=60):
        raise ClaimLost(f"レコード {record_id} の claim を失っている")

    monkeypatch.setattr(uploads, "extend_claim", refuse_to_extend)

    with pytest.raises(ClaimLost):
        uploader.run(ctx, destination_id)

    # **例外を受け取った時点で、送信スレッドはもう走っていない。**
    assert finished.is_set()
    # 成否が不明なまま降りたので、次は `checking` からやり直す。
    assert record_of(db)["state"] == "needs_recheck"


def _claim_with(uploads, seconds):
    real = uploads.claim_next

    def claim(revision, job_id, token, lease_seconds=60):
        return real(revision, job_id, token, seconds)

    return claim


def _prepare_with(uploads, seconds):
    """`prepare_side_effect` が延ばす claim の寿命だけを短くする."""
    real = uploads.prepare_side_effect

    def prepare(ctx, record_id, expect_state, lease_seconds=60, verify_eligibility=False):
        return real(ctx, record_id, expect_state, seconds, verify_eligibility)

    return prepare


def test_a_duplicate_after_an_accept_is_unknown_not_ours(world, db, monkeypatch):
    """チェックとアップロードの間に別のクライアントが割り込んだ場合.

    自作の証明が無いので origin は unknown になり、日時は承認待ちになる。
    """
    import base64
    import hashlib

    from mediaferry.adapters.immich import CheckOutcome

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"  # 既にサーバにある
    # ただし初回の照合は accept を返した（割り込みの再現）。
    monkeypatch.setattr(
        ImmichClient,
        "bulk_upload_check",
        lambda self, items: {key: CheckOutcome("accept", None, False) for key, _ in items},
    )

    uploader.run(ctx, destination_id)

    row = record_of(db)
    assert row["origin"] == "unknown"
    assert row["state"] == "awaiting_datetime_approval"
    assert server.datetimes == {}


def test_a_rejected_request_fails_the_record_without_retrying(world, db, monkeypatch):
    """4xx は再試行しても変わらない. 理由を残して次のレコードへ進む."""
    from mediaferry.adapters.immich import ImmichRejected

    server, uploader, ctx, _, _, destination_id, _ = world

    def rejected(*args, **kwargs):
        raise ImmichRejected("POST /api/assets が 400")

    monkeypatch.setattr(ImmichClient, "upload_asset", rejected)

    outcome = uploader.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.failed == 1
    assert row["state"] == "failed"
    assert row["last_error"]
    assert row["attempts"] == 1


def _a_failed_group_ground(db, uploads, media_id, destination_id):
    """`failed_group_member` の根拠を作り直す（結合が失敗したグループの構成ファイル）."""
    db.execute("DELETE FROM upload_record")
    media = db.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    group_id = a_merge_group(
        db, (media["profile_id"], media["profile_revision_id"]), "digest-1", status="failed"
    )
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group_id, media_id),
    )
    uploads.create_pairs([media_id], [destination_id])
    return group_id


def test_a_ground_that_falls_away_during_the_target_check_stops_the_send(world, db, monkeypatch):
    """**2 段 guard の後段でも §10 の根拠を見直す。**

    向き先の再確認は相手待ちで、リース（60 秒）より長くなりうる。その間に
    利用者が結合をやり直すと、後段が claim とリースしか見ていない場合、
    **いま結合中のグループの構成ファイルを送る**。
    """
    server, uploader, ctx, uploads, _, destination_id, media_id = world
    group_id = _a_failed_group_ground(db, uploads, media_id, destination_id)
    uploader._preflight._ttl = 0  # noqa: SLF001 - guard のたびに相手へ聞きに行かせる
    real_users_me = ImmichClient.users_me
    calls = []

    def flip_on_the_send_guard(self):  # noqa: ANN001, ANN202
        body = real_users_me(self)
        calls.append(1)
        if len(calls) == 2:
            # 送信直前の guard の最中に根拠が崩れた。
            db.execute("UPDATE merge_group SET status = 'merging' WHERE id = ?", (group_id,))
        return body

    monkeypatch.setattr(ImmichClient, "users_me", flip_on_the_send_guard)

    uploader.run(ctx, destination_id)

    assert server.uploads == []
    row = record_of(db)
    assert row["invalidated_at"] is not None


def test_an_asset_that_turns_out_to_exist_is_not_tagged_when_the_ground_is_gone(
    world, db, monkeypatch
):
    """**送信が `duplicate` で返ったら、それは他人の資産かもしれない。**

    `accept` で始まった送信でも、応答が `duplicate` なら自作の証明は無い
    （`origin` は `unknown`）。こちらはまだ何も変えていないので、タグ付けが
    最初の変更になる。その前に §10 の根拠を見直す。
    """
    server, uploader, ctx, uploads, _, destination_id, media_id = world
    group_id = _a_failed_group_ground(db, uploads, media_id, destination_id)
    _tag_policy(monkeypatch, True)
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    real_upload = ImmichClient.upload_asset

    def another_client_wins_the_race(self, *args, **kwargs):  # noqa: ANN001, ANN202
        # 送信の最中に別のクライアントが同じ資産を作り、根拠も崩れた。
        server.assets[checksum] = "asset-someone-else"
        db.execute("UPDATE merge_group SET status = 'merging' WHERE id = ?", (group_id,))
        return real_upload(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", another_client_wins_the_race)

    uploader.run(ctx, destination_id)

    assert server.tagged == {}
    assert server.datetimes == {}
    row = record_of(db)
    assert row["invalidated_at"] is not None


def test_a_slow_target_check_does_not_lose_the_lease(world, db, monkeypatch):
    """**向き先の再確認の待ち時間もリースを跨ぐ。**

    `users/me` はクライアントの timeout（既定 86400 秒）まで待ちうる。心拍を
    打たずに待つと、遅いだけで壊れていない Immich で送信が失敗として記録される。
    """
    import time

    from mediaferry.db.jobs import JobStore

    server, uploader, _, _, _, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    store = JobStore(db, lease_seconds=1)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    real_users_me = ImmichClient.users_me

    def slow_users_me(self):  # noqa: ANN001, ANN202
        time.sleep(1.5)
        return real_users_me(self)

    monkeypatch.setattr(ImmichClient, "users_me", slow_users_me)

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 1
    assert record_of(db)["state"] == "complete"


def test_the_current_remote_datetime_is_recorded_when_approval_is_needed(world, db, monkeypatch):
    """**承認を求める時点の「現在値」を控える**（§13 の差分表示）.

    画面を開くたびに N 件ぶんの HTTP を出さないために、ここで 1 度だけ読む。
    """
    server, uploader, ctx, _, _, destination_id, _ = world
    _an_existing_asset(server)  # 既存資産 → origin は pre_existing → 承認待ちになる
    server.datetimes["asset-existing"] = "2020-01-01T00:00:00+00:00"

    uploader.run(ctx, destination_id)

    row = record_of(db)
    assert row["state"] == "awaiting_datetime_approval"
    assert row["remote_datetime_original"] == "2020-01-01T00:00:00+00:00"
    assert row["remote_checked_at"] is not None


def test_a_remote_that_cannot_be_read_still_ends_up_awaiting(world, db, monkeypatch):
    """**読めなくても送信の結果は変えない。** 画面は「分からない」と出す."""
    from mediaferry.adapters.immich import ImmichUnavailable

    server, uploader, ctx, _, _, destination_id, _ = world
    _an_existing_asset(server)

    def unavailable(self, asset_id):  # noqa: ANN001, ANN202
        raise ImmichUnavailable("読めない")

    monkeypatch.setattr(ImmichClient, "asset", unavailable)

    outcome = uploader.run(ctx, destination_id)

    assert outcome.awaiting == 1
    row = record_of(db)
    assert row["state"] == "awaiting_datetime_approval"
    assert row["remote_datetime_original"] is None


def test_many_quick_sends_do_not_lose_the_lease(world, db, data_root, monkeypatch):
    """**速い送信を続けても、ジョブのリースが切れない。**

    `with_lease_pulse` が心拍を打つのは「1 回の送信が `HEARTBEAT_INTERVAL` より
    長かったとき」だけなので、**1 件ずつが速いと一度も打たれない**。ループが
    自分で延ばさないと、写真を何十枚も送る道（いちばん普通の使い方）が
    リース（60 秒）の満期で落ちる。実機で 61 秒で落ちた。
    """
    import time

    from mediaferry.db.jobs import JobStore

    server, uploader, _, uploads, _, destination_id, _ = world
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM"
    media_ids = []
    for index in range(4):
        payload = f"clip-{index}".encode()
        (directory / f"B{index}.MP4").write_bytes(payload)
        media_ids.append(
            a_media_file(
                db,
                (profile.profile_id, profile.revision_id),
                rel_path=f"library/dji-osmo/DCIM/B{index}.MP4",
                sha1=hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
                size_bytes=len(payload),
                captured_at=CAPTURED,
                mtime_ns=1_700_000_000_000_000_000,
            )
        )
    uploads.create_pairs(media_ids, [destination_id])

    # **1 件ずつはリースより短い。** 心拍の間隔（既定 20 秒）にも届かないので、
    # `with_lease_pulse` は一度も打たない。
    real_upload = ImmichClient.upload_asset

    def slow_enough(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        time.sleep(0.6)
        return real_upload(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_enough)
    store = JobStore(db, lease_seconds=1)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 5
    states = [row["state"] for row in db.execute("SELECT state FROM upload_record")]
    assert states == ["complete"] * 5


def test_the_upload_job_reports_how_far_it_got(world, db):
    """71 GB を 6 分かけている間、画面に何も出ないのを直す."""
    _server, uploader, ctx, _uploads, _destinations, destination_id, _media_id = world

    uploader.run(ctx, destination_id)

    # 進捗は走っている間だけ入る（終わらせるのは runner なので、ここでは残る）。
    row = db.execute("SELECT progress_json FROM job").fetchone()
    assert row["progress_json"] is not None, "送信のジョブが進捗を一度も書いていない"
    progress = json.loads(row["progress_json"])
    assert progress["phase"] == "upload"
    assert progress["file_index"] == 1
    assert progress["file_count"] == 1
    # 1 件の中を数えている証拠。ここが 0 なら、件数だけ出して中は見ていない。
    assert progress["bytes_done_all"] == len(PAYLOAD)
    assert progress["bytes_total_all"] == len(PAYLOAD)
    # **`DATA_ROOT` からの相対パスだけを載せる**（絶対パスは画面にも API にも出さない）。
    assert progress["rel_path"] == "library/dji-osmo/DCIM/A.MP4"
    assert progress["bytes_total"] == len(PAYLOAD)


def test_the_reported_total_grows_rather_than_lying():
    """走っている間に対象が増えても `12 / 10 件` とは書かない."""
    reported = _Reported(file_count=2, bytes_total_all=100)

    reported.file_index = 3
    reported.bytes_done_all = 150

    snapshot = reported.snapshot()
    assert snapshot["file_count"] == 3
    assert snapshot["bytes_total_all"] == 150

    # **いま送っている 1 件の中でも同じ。** 分母は `media_file.size_bytes`（数えた
    # 時点の値）なので、実ファイルが伸びていると送った量が大きさを追い越し、
    # 画面に `12 GiB / 10 GiB` が出る。
    reported.begin("library/dji-osmo/DCIM/A.MP4", 10)
    reported.add(15)
    assert reported.snapshot()["bytes_total"] == 15


def test_settling_never_takes_back_what_was_already_sent():
    """決着で分子を**引かない**（`settle` は足りない分だけを足す）.

    実ファイルが `media_file.size_bytes` より大きいと、送った量が大きさを
    追い越したまま決着する。引くと、次の 1 件へ移った瞬間にバーが戻る。
    """
    reported = _Reported(file_count=1, bytes_total_all=10)
    reported.begin("library/dji-osmo/DCIM/A.MP4", 10)
    reported.add(15)

    reported.settle()

    assert (reported.bytes_done, reported.bytes_done_all) == (15, 15)


def test_bytes_are_counted_while_one_file_streams(immich, tmp_path):
    """大きい 1 件を送っている間も、その中で進む."""
    seen: list[int] = []
    payload = b"x" * 4096
    path = tmp_path / "big.mp4"
    path.write_bytes(payload)

    client = ImmichClient(immich.url, API_KEY)
    client.upload_asset(
        path,
        sha1_hex=hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
        device_asset_id="mediaferry:big",
        file_created_at=CAPTURED,
        file_modified_at=CAPTURED,
        on_bytes=seen.append,
    )

    assert sum(seen) == 4096


def test_progress_is_written_while_a_single_long_file_is_sent(world, db, monkeypatch):
    """71 GB の 1 件は 6 分かかる. **その間、書き手は送信中の心拍しかいない。**"""
    import time

    _server, uploader, ctx, _uploads, _destinations, destination_id, _media_id = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    beats: list[dict | None] = []
    real_heartbeat = ctx.heartbeat

    def spy(progress=None):
        beats.append(progress)
        real_heartbeat(progress)

    monkeypatch.setattr(ctx, "heartbeat", spy)
    real_upload = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(0.3)
        return real_upload(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)

    uploader.run(ctx, destination_id)

    # ループの心拍は 2 回（1 件目の前と、もう無いと分かる前）。それを超えた分が
    # 送信中の心拍で、**そこにも進捗が乗っている**。
    #
    # **「すべての心拍が運ぶ」とは求めない。** preflight の待ちを囲む
    # `with_lease_pulse`（`_guard`）は `progress` を渡さないので、そこが心拍の
    # 間隔を超えた回には運ばない心拍が混ざる。求めると時々落ちるテストになる。
    carried = [beat for beat in beats if beat is not None]
    assert len(carried) > 2
    assert all(beat["phase"] == "upload" for beat in carried)


def test_the_totals_are_counted_before_the_first_file_is_sent(world, db, data_root, monkeypatch):
    """分母は**送る前**に決まる. 実測を写すだけなら、バーは常に 100% になる."""
    _server, uploader, ctx, uploads, _destinations, destination_id, _media_id = world
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM"
    payload = b"second-clip"
    (directory / "C.MP4").write_bytes(payload)
    second = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/C.MP4",
        sha1=hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
        size_bytes=len(payload),
        captured_at=CAPTURED,
        mtime_ns=1_700_000_000_000_000_000,
    )
    done = b"already-sent"
    (directory / "D.MP4").write_bytes(done)
    settled = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/D.MP4",
        sha1=hashlib.sha1(done, usedforsecurity=False).hexdigest(),
        size_bytes=len(done),
        captured_at=CAPTURED,
        mtime_ns=1_700_000_000_000_000_000,
    )
    uploads.create_pairs([second, settled], [destination_id])
    # **無効化した 1 件は分母に入らない**（`claim_next` が拾わない行）。
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?"
        " WHERE media_file_id = ?",
        (CAPTURED, "テストで無効化した", settled),
    )
    beats: list[dict] = []
    real_heartbeat = ctx.heartbeat

    def spy(progress=None):
        beats.append(progress)
        real_heartbeat(progress)

    monkeypatch.setattr(ctx, "heartbeat", spy)

    uploader.run(ctx, destination_id)

    # 最初の心拍は 1 件目を claim する前。そこで既に全体が見えている。
    assert beats[0]["file_index"] == 0
    assert beats[0]["file_count"] == 2
    assert beats[0]["bytes_total_all"] == len(PAYLOAD) + len(payload)
    # **2 件目の `bytes_done` は 1 件目から続かない**（`begin` が 0 へ戻す）。
    # 続くと、1 件の中の進み具合が「送った合計」に化ける。`bytes_total` と
    # 比べるだけでは足りない（`snapshot` が合計のほうを伸ばして隠す）ので、
    # 2 件目の大きさそのものと比べる。
    assert beats[-1]["bytes_done"] == len(payload)


def test_a_file_that_did_not_need_sending_still_counts_as_done(world, db, data_root):
    """既にリモートにある件は 1 バイトも送らない. **それでも分子に入る。**

    分母には大きさが入っているので、拾わないと重複の多い回に「ほとんど
    終わっているのにバーが数 % のまま」になる。
    """
    server, uploader, ctx, uploads, _destinations, destination_id, _media_id = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"
    # 2 件目は実際に送る（分子が「送った分」だけで足りていないことを見るため、
    # 送る件と送らない件を混ぜる）。
    profile = ProfileRegistry(db).current("dji-osmo")
    payload = b"second-clip"
    (data_root / "library" / "dji-osmo" / "DCIM" / "C.MP4").write_bytes(payload)
    second = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/C.MP4",
        sha1=hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
        size_bytes=len(payload),
        captured_at=CAPTURED,
        mtime_ns=1_700_000_000_000_000_000,
    )
    uploads.create_pairs([second], [destination_id])

    uploader.run(ctx, destination_id)

    # 1 件は送っていない（既にあった）。それでも分子は分母に届く。
    assert len(server.uploads) == 1
    progress = json.loads(db.execute("SELECT progress_json FROM job").fetchone()["progress_json"])
    assert progress["bytes_total_all"] == len(PAYLOAD) + len(payload)
    assert progress["bytes_done_all"] == progress["bytes_total_all"]


def test_a_record_that_goes_back_for_a_retry_is_not_counted_as_done(world, db, monkeypatch):
    """**再試行に回した件は分子に入れない。** 次に取り直したときに二重で数える.

    `claim_next` がまた拾う状態（`pending` / `needs_recheck`）で降りた件を
    決着として数えると、同じレコードを 2 周するあいだに同じ大きさを 2 回
    足すことになり、11 バイトのジョブが `22 / 22` で終わる。
    """
    from mediaferry.adapters.immich import ImmichUnavailable

    server, uploader, ctx, _, _, destination_id, _ = world
    monkeypatch.setattr("mediaferry.jobs.uploader.BACKOFF_BASE_SECONDS", 0.01)
    calls = {"n": 0}
    real_check = ImmichClient.bulk_upload_check

    def once_unavailable(self, pairs):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise ImmichUnavailable("POST /api/assets/bulk-upload-check が 503")
        return real_check(self, pairs)

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", once_unavailable)

    uploader.run(ctx, destination_id)

    assert record_of(db)["state"] == "complete"
    assert len(server.uploads) == 1
    progress = json.loads(db.execute("SELECT progress_json FROM job").fetchone()["progress_json"])
    # 1 件を 2 周したが、送ったのは 1 件ぶん。
    assert progress["bytes_done_all"] == len(PAYLOAD)
    assert progress["bytes_total_all"] == len(PAYLOAD)
