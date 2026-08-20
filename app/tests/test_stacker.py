"""アップロードの第 2 パス（§9.11）.

**相手に触る前後の境界**を見る。リース・キャンセル・宛先の向き替え・
プロファイル編集は、どれも「相手を待っている間」に起こりうる。
"""

import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.clock import now_iso
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore, LeaseLost
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.ids import new_id
from mediaferry.jobs.preflight import PreflightCache
from mediaferry.jobs.stacker import Stacker

from .fake_immich import API_KEY
from .test_schema_artifacts import a_media_file
from .test_schema_sources import a_volume

CAPTURED = "2026-08-19T10:30:00+09:00"


class World:
    """canon-eos の JPG + CR2 が、同じカードから取り込まれて送信済みの状態."""

    def __init__(self, db, immich):
        self.db = db
        self.immich = immich
        registry = ProfileRegistry(db)
        registry.sync_builtins()
        self.profile = registry.current("canon-eos")
        self.volume = a_volume(db, (self.profile.profile_id, self.profile.revision_id))
        self.destinations = DestinationRepository(
            db, CredentialStore(db, SecretBox(os.urandom(32)))
        )
        self.destination_id = self.destinations.create(
            name="home",
            base_url=immich.url,
            public_url=None,
            secret=API_KEY,
            identity=RemoteIdentity.observed(immich.user_id),
        )
        self.uploads = UploadRepository(db, registry, self.destinations)
        self.registry = registry
        self.records = {}
        self.assets = {}
        for extension in ("JPG", "CR2"):
            self.add_pair_member(extension, "IMG_1234")
        self.store = JobStore(db)
        self.store.enqueue("upload", {"destination_id": self.destination_id})
        self.ctx = self.store.claim_next()

    def with_a_short_lease(self, seconds=1):
        """**相手待ちがリースを跨ぐ状況**を作る（既定は 60 秒）."""
        store = JobStore(self.db, lease_seconds=seconds)
        self.db.execute(
            "UPDATE job SET status = 'queued', lease_token = NULL, lease_expires_at = NULL"
        )
        self.ctx = store.claim_next()
        return self.ctx

    def add_pair_member(self, extension, stem, captured_at=CAPTURED, **over):
        media = a_media_file(
            self.db,
            (self.profile.profile_id, self.profile.revision_id),
            rel_path=f"library/canon-eos/DCIM/100CANON/{stem}.{extension}",
            kind="photo",
            duration_seconds=None,
            captured_at=captured_at,
            captured_at_source="exif",
        )
        self.db.execute(
            "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
            " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at)"
            " VALUES (?, ?, ?, 10, 1, ?, 1, ?, 'published', ?)",
            (
                new_id(),
                self.volume,
                f"DCIM/100CANON/{stem}.{extension}",
                new_id(),
                media,
                now_iso(),
            ),
        )
        asset_id = f"asset-{stem}-{extension}"
        row = {
            "id": new_id(),
            "destination_id": self.destination_id,
            "target_epoch": 1,
            "media_file_id": media,
            "state": "complete",
            "selection_rule": "default",
            "origin": "created_by_us",
            "remote_asset_id": asset_id,
            "destination_revision_id": self.destinations.current(self.destination_id)["id"],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        row.update(over)
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        self.db.execute(
            f"INSERT INTO upload_record ({cols}) VALUES ({marks})",  # noqa: S608
            tuple(row.values()),
        )
        self.records[f"{stem}.{extension}"] = row["id"]
        self.assets[f"{stem}.{extension}"] = asset_id
        return row["id"]

    def stacker(self):
        def open_client(revision):
            return ImmichClient(revision["base_url"], API_KEY)

        return Stacker(
            self.db,
            self.uploads,
            self.destinations,
            self.registry,
            open_client,
            PreflightCache(self.destinations, open_client),
        )

    def run(self):
        return self.stacker().run(self.ctx, self.destination_id)

    def rows(self):
        return {
            row["id"]: row for row in self.db.execute("SELECT * FROM upload_record ORDER BY id")
        }

    def row(self, name):
        return self.rows()[self.records[name]]

    def repoint(self):
        """別ライブラリへ向き替える（epoch が進む）."""
        revision_id = new_id()
        credential = self.db.execute(
            "SELECT id FROM destination_credential WHERE destination_id = ?",
            (self.destination_id,),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
            " base_url, credential_id, created_at)"
            " VALUES (?, ?, 9, 2, 'http://other.invalid', ?, ?)",
            (revision_id, self.destination_id, credential, now_iso()),
        )
        self.db.execute(
            "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
            (revision_id, self.destination_id),
        )

    def edit_profile(self):
        """**同じ内容でも版を進める。** 見るのは `current_revision_id` の同一性."""
        revision_id = new_id()
        definition_json = self.db.execute(
            "SELECT definition_json FROM profile_revision WHERE id = ?",
            (self.profile.revision_id,),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO profile_revision (id, profile_id, revision, definition_json,"
            " schema_version, created_at) VALUES (?, ?, 99, ?, 1, ?)",
            (revision_id, self.profile.profile_id, definition_json, now_iso()),
        )
        self.db.execute(
            "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
            (revision_id, self.profile.profile_id),
        )

    def move_to_an_editable_copy(self):
        """ビルトインは編集できないので、複製を作ってそちらへ移す."""
        self.registry.duplicate("canon-eos", "canon-copy", "写し")
        copy = self.registry.current("canon-copy")
        self.db.execute(
            "UPDATE media_file SET profile_id = ?, profile_revision_id = ?,"
            " captured_at_revision_id = ?",
            (copy.profile_id, copy.revision_id, copy.revision_id),
        )
        return copy

    def disable_stacking(self):
        """**現行版の規則を変える**（`stack` を無効にする）."""
        from dataclasses import replace

        from mediaferry.core.profiles.model import STACK_DISABLED

        current = self.registry.current("canon-copy")
        self.registry.update("canon-copy", replace(current.definition, stack=STACK_DISABLED))

    def cancel(self):
        self.db.execute("UPDATE job SET status = 'cancelling'")

    def cancel_before_the_next_row(self):
        """**行頭の確認の直後**にキャンセルを commit する（行間 heartbeat の窓）."""
        state = {"seen": 0}
        original = self.ctx.cancelled

        def hooked():
            # **確認が偽を返した後**に commit する（先に cancel すると、その確認
            # 自身が真を返して窓にならない）。
            answer = original()
            state["seen"] += 1
            if state["seen"] == 2:
                self.cancel()
            return answer

        self.ctx.cancelled = hooked

    def expire_lease(self):
        self.db.execute("UPDATE job SET lease_expires_at = '2000-01-01T00:00:00+00:00'")

    def on_preflight(self, action):
        """**向き先の再確認の応答を返した直後**に割り込む（相手待ちの窓）."""
        original = self.immich.route
        state = {"done": False}

        def hooked(method, path, body, headers):
            result = original(method, path, body, headers)
            if not state["done"] and path == "/api/users/me":
                state["done"] = True
                action()
            return result

        self.immich.route = hooked

    def on_first_asset_read(self, action):
        """最初の `GET /api/assets/{id}` の**応答を返した直後**に割り込む."""
        original = self.immich.route
        state = {"done": False}

        def hooked(method, path, body, headers):
            result = original(method, path, body, headers)
            if not state["done"] and method == "GET" and path.startswith("/api/assets/"):
                state["done"] = True
                action()
            return result

        self.immich.route = hooked


@pytest.fixture
def world(db, immich):
    return World(db, immich)


def test_a_pair_is_stacked_with_the_jpeg_as_primary(world):
    outcome = world.run()

    assert outcome.stacked == 1
    assert len(world.immich.stacks) == 1
    stack = next(iter(world.immich.stacks.values()))
    assert stack["primary"] == world.assets["IMG_1234.JPG"]
    assert set(stack["assets"]) == set(world.assets.values())


def test_both_records_are_marked(world):
    world.run()

    states = {row["stack_state"] for row in world.rows().values()}
    stacks = {row["remote_stack_id"] for row in world.rows().values()}
    assert states == {"stacked"}
    assert len(stacks) == 1 and None not in stacks


def test_the_primary_is_not_moved_when_it_is_already_right(world):
    """**相手を無駄に変えない**（§9.11）."""
    world.run()
    assert not [path for method, path in world.immich.requests if method == "PUT"]


def test_the_primary_is_moved_when_the_peer_chose_another_one(world, monkeypatch):
    """`POST` がどれを primary にするかは仕様に書かれていない."""
    original = world.immich._create_stack

    def reversed_primary(payload):
        status, body = original({"assetIds": list(reversed(payload["assetIds"]))})
        return status, body

    monkeypatch.setattr(world.immich, "_create_stack", reversed_primary)

    world.run()

    stack = next(iter(world.immich.stacks.values()))
    assert stack["primary"] == world.assets["IMG_1234.JPG"]
    assert world.row("IMG_1234.JPG")["stack_state"] == "stacked"


def test_an_existing_stack_with_the_same_members_is_adopted(world):
    """**中断からの回収。** 送信直後に落ちた世界を、次の実行が拾う."""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.JPG"],
        "assets": list(world.assets.values()),
    }

    world.run()

    assert world.row("IMG_1234.JPG")["remote_stack_id"] == "stack-9"
    assert ("POST", "/api/stacks") not in world.immich.requests


def test_adopting_an_existing_stack_still_fixes_the_primary(world):
    """`POST` の直後・`PUT` の前に落ちた世界。**集合は一致するが primary が違う。**"""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.CR2"],
        "assets": list(world.assets.values()),
    }

    world.run()

    assert world.immich.stacks["stack-9"]["primary"] == world.assets["IMG_1234.JPG"]
    assert world.row("IMG_1234.JPG")["stack_state"] == "stacked"


def test_a_foreign_stack_is_left_alone(world):
    """利用者が手で作った組を作り直さない."""
    world.immich.stacks["stack-9"] = {
        "primary": "someone-else",
        "assets": [world.assets["IMG_1234.JPG"], "someone-else"],
    }

    outcome = world.run()

    # **行ごとに決着する。** どちらの行にも自分の理由が付く。
    assert outcome.skipped == 2
    assert ("POST", "/api/stacks") not in world.immich.requests
    assert "別のスタック" in world.row("IMG_1234.JPG")["stack_reason"]
    assert "別のスタック" in world.row("IMG_1234.CR2")["stack_reason"]


def test_a_partially_stacked_group_is_left_alone(world):
    """片方だけスタック済み。**集合が我々の組と一致しない。**"""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.JPG"],
        "assets": [world.assets["IMG_1234.JPG"], "someone-else"],
    }

    outcome = world.run()

    assert outcome.skipped == 2
    assert ("POST", "/api/stacks") not in world.immich.requests


def test_a_lonely_record_is_skipped_with_a_reason(world):
    world.add_pair_member("JPG", "IMG_9999")

    outcome = world.run()

    assert outcome.stacked == 1 and outcome.skipped == 1
    assert world.row("IMG_9999.JPG")["stack_reason"] == "相方が見つからない"


def test_a_disabled_profile_skips_every_record(world):
    world.move_to_an_editable_copy()
    world.disable_stacking()

    outcome = world.run()

    assert outcome.stacked == 0 and outcome.skipped == 2
    assert "スタックを使わない" in world.row("IMG_1234.JPG")["stack_reason"]


def test_a_record_without_an_observation_is_skipped(world):
    world.db.execute("UPDATE source_entry SET media_file_id = NULL, state = 'seen'")

    outcome = world.run()

    assert outcome.skipped == 2
    assert "観測が残っていない" in world.row("IMG_1234.JPG")["stack_reason"]


def test_evaluated_records_are_not_looked_at_again(world):
    world.run()
    world.immich.requests.clear()

    assert world.run() == type(world.run())(0, 0, 0)
    assert world.immich.requests == []


def test_a_5xx_stops_the_pass_and_leaves_the_records_unevaluated(world):
    """宛先が落ちているだけなら、次の送信で再試行するのが正しい（§9.11）."""
    world.immich.fail_next = 99

    outcome = world.run()

    assert outcome == type(outcome)(0, 0, 1)
    assert all(row["stack_state"] is None for row in world.rows().values())


def test_an_auth_failure_stops_the_pass(world):
    """**鍵の失効は組の事情ではない。** 次の組へ進んでも同じ結果になる。"""
    world.add_pair_member("JPG", "IMG_5555")
    world.add_pair_member("CR2", "IMG_5555")

    def refuse(method, path, body, headers):
        return 401, {"message": "Invalid API key"}

    world.immich.route = refuse

    outcome = world.run()

    assert outcome.deferred == 1
    assert all(row["stack_state"] is None for row in world.rows().values())


def test_a_4xx_is_recorded_as_skipped(world):
    """**組ごとに決着させる。** 相方も自分の理由を持って見送りになる。"""

    def reject(payload):
        return 400, {"message": "no"}

    world.immich._create_stack = reject

    outcome = world.run()

    assert outcome.skipped == 2
    assert "受け付けない" in world.row("IMG_1234.JPG")["stack_reason"]
    assert "受け付けない" in world.row("IMG_1234.CR2")["stack_reason"]


def test_records_of_an_old_epoch_are_never_touched(world):
    """**別ライブラリへ送った履歴に、現行の資格情報で触らない**（§9.11）."""
    world.repoint()

    outcome = world.run()

    assert outcome == type(outcome)(0, 0, 0)
    assert world.immich.requests == []


def test_a_cancelled_job_stops_before_the_next_group(world):
    world.add_pair_member("JPG", "IMG_7777")
    world.add_pair_member("CR2", "IMG_7777")
    world.cancel()

    outcome = world.run()

    assert outcome.stacked == 0
    assert world.immich.stacks == {}


def test_a_lost_lease_stops_before_touching_the_peer(world):
    """外部への副作用の**直前**にリースを確認する（§9.3 と同じ作法）."""
    world.expire_lease()

    with pytest.raises(LeaseLost):
        world.run()

    assert world.immich.stacks == {}


def test_a_cancel_committed_during_the_get_stops_before_the_post(world):
    """**`POST` の直前に取り直す。** GET を待っている間にキャンセルは commit される."""
    world.on_first_asset_read(world.cancel)

    outcome = world.run()

    assert outcome.stacked == 0
    assert ("POST", "/api/stacks") not in world.immich.requests


def test_a_repoint_during_the_get_stops_before_the_post(world):
    """**開始後に向き替えられたら、そこで止まる。**

    旧 epoch の `complete` は無効化されないので、進行中レコードの無効化では
    止まらない。preflight も固定した旧リビジョンを見るので、旧向き先が生きて
    いれば成功してしまう。
    """
    world.on_first_asset_read(world.repoint)

    world.run()

    assert ("POST", "/api/stacks") not in world.immich.requests
    assert all(row["stack_state"] is None for row in world.rows().values())


def test_a_profile_edit_during_the_get_stops_before_the_post(world):
    """**旧版で決めた組を、新しい版の世界へ送らない。**

    相手を待っている間に規則が変わったら、その組は諦める。次の行は現行版を
    読み直すので、**新しい規則（ここでは無効）に従う**。
    """
    world.move_to_an_editable_copy()
    world.on_first_asset_read(world.disable_stacking)

    outcome = world.run()

    assert ("POST", "/api/stacks") not in world.immich.requests
    assert outcome.stacked == 0
    # **どちらが先に処理されるかは `id` の順で決まる**（ランダム）ので、順序に
    # 依らない形で見る。割り込まれた側は「組が変わった」で決着させず未評価のまま、
    # もう一方は新しい規則で見送る。
    states = sorted((row["stack_state"] or "") for row in world.rows().values())
    assert states == ["", "skipped"]
    skipped = next(row for row in world.rows().values() if row["stack_state"] == "skipped")
    assert "スタックを使わない" in skipped["stack_reason"]


def test_an_invalidation_during_the_get_stops_before_the_post(world):
    def invalidate():
        world.db.execute(
            "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = '編集された'"
            " WHERE id = ?",
            (now_iso(), world.records["IMG_1234.CR2"]),
        )

    world.on_first_asset_read(invalidate)

    world.run()

    # **相手に触らない**のが要点。どちらの行も `stacked` にならない
    # （どちらが先に処理されるかは `id` の順で決まる）。
    assert ("POST", "/api/stacks") not in world.immich.requests
    assert "stacked" not in {row["stack_state"] for row in world.rows().values()}


def test_the_preflight_runs_before_the_first_touch(world):
    """**向き先の再確認を飛ばさない**（§9.10 の `_guard` と同じ理由）.

    TTL を跨いだ後の `POST` が別のライブラリへ飛ぶと、UUID が偶然存在すれば
    他人の資産を束ねる。
    """
    world.run()

    assert world.immich.requests[0] == ("GET", "/api/users/me")


def test_the_cursor_advances_so_the_pass_terminates(world, monkeypatch):
    """**keyset が無いと終わらない。**

    相手の障害で未評価のまま残した行は次の周回でも条件を満たすので、`LIMIT` を
    繰り返すだけだと同じ行を読み直して進まなくなる。**ハングではなく、渡した
    cursor が単調に増えることで観測する。**
    """
    world.add_pair_member("JPG", "IMG_7777")
    world.add_pair_member("CR2", "IMG_7777")
    seen = []
    original = world.uploads.unstacked_batch

    def spy(destination_id, epoch, after_id, limit):
        seen.append(after_id)
        return original(destination_id, epoch, after_id, limit)

    monkeypatch.setattr(world.uploads, "unstacked_batch", spy)

    world.run()

    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen)


def test_a_slow_peer_does_not_lose_the_lease(world, monkeypatch):
    """**相手待ちはすべて心拍で守る**（timeout は既定 86400 秒）.

    1 回の読み取りが 60 秒のリースを跨ぐのは正常な動作。囲まないと、送信が
    成功した直後の記録でリースを失って正常なジョブが失敗になる。
    """
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    # **preflight だけが囲まれていても足りない量**にする（2 回の読み取りで
    # リースを跨ぐ）。
    world.immich.delay_seconds = 0.6
    world.with_a_short_lease(seconds=1)

    assert world.run().stacked == 1


def test_many_local_skips_do_not_lose_the_lease(world, monkeypatch):
    """**行をまたいだ経過時間でも心拍を打つ。**

    1 件が短く終わると `with_lease_pulse` は 1 度も打たない
    （`thread.join(timeout=間隔)` が先に返る）。相手に触らない見送りが続くと積もる。
    """
    import time as _time

    for index in range(10):
        world.add_pair_member("JPG", f"IMG_{6000 + index}")
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.3)
    world.with_a_short_lease(seconds=1)
    original = world.uploads.mark_skipped

    def slow_skip(*args, **kwargs):
        _time.sleep(0.15)
        return original(*args, **kwargs)

    world.uploads.mark_skipped = slow_skip

    assert world.run().skipped == 10


def test_an_existing_stack_that_contains_more_is_left_alone(world):
    """**部分集合では採用しない。** 利用者が第 3 の資産を足した組かもしれない."""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.JPG"],
        "assets": [*world.assets.values(), "someone-else"],
    }

    outcome = world.run()

    assert outcome.skipped == 2
    assert ("POST", "/api/stacks") not in world.immich.requests


def test_a_primary_change_that_does_not_take_is_refused(world):
    """**応答を信じず、読み直して確かめる。**"""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.CR2"],
        "assets": list(world.assets.values()),
    }
    world.immich.ignore_primary_change = True

    outcome = world.run()

    assert outcome.stacked == 0
    assert "受け付けない" in world.row("IMG_1234.JPG")["stack_reason"]


def test_a_repoint_during_the_get_does_not_scan_the_rest(world):
    """**固定した epoch 全体が無効なので、続けても同じ失敗を繰り返すだけ。**"""
    world.add_pair_member("JPG", "IMG_8888")
    world.add_pair_member("CR2", "IMG_8888")
    world.on_first_asset_read(world.repoint)

    world.run()

    reads = [path for method, path in world.immich.requests if path.startswith("/api/assets/")]
    assert len(reads) <= 2


def test_the_current_profile_revision_decides_even_for_old_media(world):
    """**取り込み時の版に固定しない。**

    Phase 6 より前に取り込んだメディアは `stack` を持たない版を指している。
    現行版を見るからこそ、既存ライブラリも対象になる。
    """
    copy = world.move_to_an_editable_copy()
    world.disable_stacking()
    # メディアは「スタックを知らない版」を指したまま、現行版だけ有効へ戻す。
    world.db.execute(
        "UPDATE media_file SET profile_revision_id = ?, captured_at_revision_id = ?",
        (world.registry.current("canon-copy").revision_id, copy.revision_id),
    )
    world.db.execute(
        "UPDATE media_file SET profile_revision_id = (SELECT id FROM profile_revision"
        " WHERE profile_id = ? ORDER BY revision LIMIT 1)",
        (copy.profile_id,),
    )
    from dataclasses import replace

    from mediaferry.core.profiles.model import StackRule

    current = world.registry.current("canon-copy")
    world.registry.update(
        "canon-copy",
        replace(
            current.definition,
            stack=StackRule(enabled=True, extensions=("JPG", "CR2"), tolerance_seconds=0),
        ),
    )

    assert world.run().stacked == 1


def test_a_cancel_committed_during_the_preflight_stops_before_the_post(world):
    """**preflight の後にも取り直す。**

    向き先の再確認は相手待ちなので、その間にキャンセルが commit されうる。
    前だけに置くと、確かめた後に commit されたキャンセルを見落とす。
    """
    world.on_preflight(world.cancel)

    world.run()

    assert ("POST", "/api/stacks") not in world.immich.requests
    assert "stacked" not in {row["stack_state"] for row in world.rows().values()}


def test_a_repoint_stops_the_pass_instead_of_evaluating_the_rest(world, monkeypatch):
    """**固定した epoch 全体が無効なので、次の組を評価しても同じ失敗になる。**

    guard は相手に触る前に落ちるので、通信の数では差が出ない。**組を何回
    評価したか**で見る。
    """
    world.add_pair_member("JPG", "IMG_8888")
    world.add_pair_member("CR2", "IMG_8888")
    calls = []
    original = world.uploads.guard_stack_group

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(world.uploads, "guard_stack_group", spy)
    world.on_first_asset_read(world.repoint)

    world.run()

    # 1 組目の最初の guard は通り、GET の後の guard で落ちる。2 組目は評価しない。
    assert len(calls) == 3


def test_the_partner_in_the_same_batch_is_not_read_again(world):
    """**バッチは snapshot。** 組を記録したら、相方の行は相手に触らずに飛ばす.

    読み直さずに進むと、組ごとに資産の読み取りを 2 度出したうえで guard が弾く。
    """
    world.run()

    reads = [path for method, path in world.immich.requests if path.startswith("/api/assets/")]
    assert len(reads) == 2


def test_a_crash_between_the_creation_and_the_response_is_recovered(world):
    """**送信中の中断。** 相手は作れているが、こちらは失敗として見た状態.

    次の送信で「既存スタックのメンバー集合が我々の組と一致する」経路が拾う。
    新しい状態は要らない（§9.11）。
    """
    world.immich.fail_after_creating_the_stack = True

    first = world.run()

    assert first == type(first)(0, 0, 1)  # 未評価のまま残す
    assert len(world.immich.stacks) == 1
    assert all(row["stack_state"] is None for row in world.rows().values())

    world.immich.fail_after_creating_the_stack = False
    second = world.run()

    assert second.stacked == 1
    # **作り直さない。** 相手側のスタックは 1 つのまま。
    assert len(world.immich.stacks) == 1
    assert {row["stack_state"] for row in world.rows().values()} == {"stacked"}


def test_a_crash_before_the_primary_was_moved_is_recovered(world):
    """`POST` の直後・`PUT` の前に落ちた状態。**primary まで直す。**"""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.CR2"],
        "assets": list(world.assets.values()),
    }

    world.run()

    assert world.immich.stacks["stack-9"]["primary"] == world.assets["IMG_1234.JPG"]
    assert {row["stack_state"] for row in world.rows().values()} == {"stacked"}


def test_a_crash_before_the_records_were_written_is_recovered(world):
    """`PUT` の後・記録の前に落ちた状態。**`PUT` を打ち直さない。**"""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.JPG"],
        "assets": list(world.assets.values()),
    }

    world.run()

    assert not [path for method, path in world.immich.requests if method == "PUT"]
    assert {row["stack_state"] for row in world.rows().values()} == {"stacked"}


def test_a_cancel_between_the_row_check_and_the_heartbeat_is_not_a_failure(world, monkeypatch):
    """**利用者が押したキャンセルを失敗として記録しない**（§9.9）.

    行頭の `cancelled()` が偽を返した直後にキャンセルが commit されると、
    行間の heartbeat が `LeaseLost` を投げる。受け口の外に置くと、`JobRunner` の
    汎用経路がジョブを `failed` にする。
    """
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0)
    world.cancel_before_the_next_row()

    outcome = world.run()

    assert outcome.stacked == 0
    assert world.immich.stacks == {}


def test_a_slow_lookup_during_recovery_does_not_lose_the_lease(world, monkeypatch):
    """**回収の経路の GET も心拍で守る。**

    既存スタックを引く `GET /api/stacks` だけ囲み忘れると、正常な長い応答で
    直後の `mark_stacked` がリースを失い、ジョブが失敗になる。
    """
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.JPG"],
        "assets": list(world.assets.values()),
    }
    world.immich.stack_lookup_delay_seconds = 1.2
    world.with_a_short_lease(seconds=1)

    assert world.run().stacked == 1


def test_members_that_disagree_on_the_primary_are_left_alone(world):
    """資産ごとに別々の primary を名乗る相手には触らない."""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.JPG"],
        "assets": list(world.assets.values()),
    }
    world.immich.disagree_on_the_primary = True

    outcome = world.run()

    assert outcome.stacked == 0
    assert ("POST", "/api/stacks") not in world.immich.requests
    # **引きにさえ行かない**（どの primary を信じるか決められない）。
    assert not [path for _, path in world.immich.requests if path.startswith("/api/stacks?")]


def test_members_swapped_by_the_peer_are_refused(world):
    """**相手が要求と違う資産 ID を返したら、その応答で組を作らない。**"""
    world.immich.swap_asset_ids = True

    outcome = world.run()

    assert outcome.stacked == 0
    assert world.immich.stacks == {}


def test_members_changed_after_the_put_are_refused(world):
    """`PUT` と読み直しの間に member が差し替わっても、`stacked` と書かない."""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.CR2"],
        "assets": list(world.assets.values()),
    }
    world.immich.change_members_after_the_put = True

    outcome = world.run()

    assert outcome.stacked == 0
    assert "stacked" not in {row["stack_state"] for row in world.rows().values()}


def test_a_lookup_that_returns_another_stack_id_is_refused(world):
    """**引けた id が、資産が名乗った id と違ったら採用しない。**"""
    world.immich.stacks["stack-9"] = {
        "primary": world.assets["IMG_1234.JPG"],
        "assets": list(world.assets.values()),
    }
    world.immich.lookup_returns_another_stack_id = True

    outcome = world.run()

    assert outcome.stacked == 0
    assert "stacked" not in {row["stack_state"] for row in world.rows().values()}
