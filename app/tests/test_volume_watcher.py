"""`VolumeWatcher` —— 自動取り込みの駆動（§12.1）.

**この試験の中心は 2 つの分離。**

1. **マウントを伴う判定（`refresh`）は観測トークンの変化でしか走らない。**
   毎 tick 回すと、カードを挿している限り数秒ごとにマウント／アンマウントが続く
2. **積んでよいかの判定は毎 tick、DB の現在値から組み直す。**
   信頼登録は `trusted_at` を `UPDATE` するだけで mountd の観測は動かないので、
   直前の `VolumeView` を見ていると承認しても取り込みが始まらない
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import pytest

from mediaferry.api.jobs_wiring import _profile_ref
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.watcher import VolumeWatcher


@pytest.fixture
def watcher(database, db, broker, monkeypatch):
    """watcher は専用の DB 接続と VolumeService を持つ（`db` とは別の接続）."""
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "UTC")
    ProfileRegistry(db).sync_builtins()
    w = VolumeWatcher(
        database,
        {"MEDIAFERRY_AUTO_IMPORT": "trusted", "MEDIAFERRY_DEFAULT_TIMEZONE": "UTC"},
        broker,
        poll_interval=0.01,
    )
    yield w
    w.close()


def reinsert(watcher, volumes):
    """抜いて挿し直す.

    **`generation` を書き換えても効かない。** `BrokerServer._observe` は
    lister が返した値を捨てて、自分の `broker_epoch` と、観測した集合
    `(volume_key, fs_uuid, fs_type, size_bytes)` の指紋から算出した世代で
    刻み直す。抜き挿しは「集合を変える」ことでしか表せない。
    """
    original = list(volumes)
    volumes.clear()
    watcher.tick()  # 抜けた: detach_absent が走る
    volumes.extend(original)
    watcher.tick()  # 挿し直し: 新しい presence 行になる


def a_known_card(watcher, volumes):
    """**2 度目以降の挿入**にする.

    §12.1 のとおり、初めて見るボリュームは `identity_confidence = low` で自動
    取り込みの対象にならない（`_identity_confidence` は憶えた指紋が無ければ
    必ず `low` を返す）。「一度承認すれば以後は挿すだけ」の「以後」を作るには、
    1 度観測して指紋を憶えさせてから挿し直す必要がある。
    """
    watcher.tick()  # 初回: low。content_manifest_digest を憶える
    reinsert(watcher, volumes)  # 挿し直し: 指紋が一致して high


def trust_the_card(db):
    """画面の承認に相当する（`VolumeService.trust` と同じ 1 文）."""
    db.execute("UPDATE volume_instance SET trusted_at = '2026-08-19T00:00:00Z'")
    db.commit()


def job_types(db) -> list[str]:
    return [row["type"] for row in db.execute("SELECT type FROM job ORDER BY created_at, id")]


def queued_imports(db) -> list[str]:
    return [
        row["id"]
        for row in db.execute("SELECT id FROM job WHERE type = 'import' AND status = 'queued'")
    ]


# ----------------------------------------------------------------------
# 1. マウントを伴う判定は、観測トークンが変わったときだけ


def test_a_tick_probes_once_and_then_stops_probing(watcher, mount_manager):
    """変わらない tick では `refresh` を回さない.

    回すと、カードを挿している限り数秒ごとにマウント／アンマウントが続く。
    16 GiB の取り込み中も裏で同じカードを開き続けることになる。
    """
    watcher.tick()
    after_first = mount_manager.mounts
    assert after_first > 0, "最初の tick で判定していない"
    watcher.tick()
    watcher.tick()
    assert mount_manager.mounts == after_first, "変化が無いのに開き直している"


def test_the_first_observation_always_probes_even_when_empty(watcher, volumes, mount_manager):
    """**「未観測」と「空集合」を同じ値にしない。**

    同一視すると、前回の停止時に live のまま残った presence があるとき、空で
    起動した最初の tick が「変化なし」と判定して `detach_absent` を飛ばし、
    抜けているカードに自動取り込みを積む。
    """
    volumes.clear()
    watcher.tick()
    assert watcher.observed, "空集合でも 1 度目は観測として扱う"
    # 空のまま繰り返しても、判定はもう走らない
    before = mount_manager.mounts
    watcher.tick()
    assert mount_manager.mounts == before


def test_removing_the_last_card_is_a_change(watcher, volumes, db):
    """最後の 1 枚を抜くと `volumes: []` になり、比較すべき generation が
    応答から読めない（`_do_list` は volumes を返すだけ）。空を番兵として扱う。
    """
    watcher.tick()
    live = db.execute("SELECT count(*) FROM volume_presence WHERE detached_at IS NULL").fetchone()[
        0
    ]
    assert live == 1
    volumes.clear()
    watcher.tick()
    live = db.execute("SELECT count(*) FROM volume_presence WHERE detached_at IS NULL").fetchone()[
        0
    ]
    assert live == 0, "空集合が変化として検出されていない"


class StubBroker:
    """`list_volumes` だけを返す。**トークンの判定だけを見るための土台。**

    実 `BrokerServer` では epoch も generation も作れない —— `_observe` が
    lister の値を捨てて自分の epoch と、集合の指紋から算出した世代で刻み直す
    ので、テストから値を指定できない。トークンの比較規則は、線の上の挙動とは
    別に単体で確かめる。
    """

    def __init__(self, volumes):
        self.volumes = volumes

    def list_volumes(self):
        return list(self.volumes)

    def close(self):
        pass


def a_volume_info(**over):
    from mediaferry_protocol.messages import VolumeInfo

    row = {
        "volume_key": "8:160",
        "device_node": "/dev/sdk",
        "major": 8,
        "minor": 160,
        "sysfs_path": "/sys/x",
        "fs_type": "exfat",
        "fs_uuid": "26B1-2FD6",
        "fs_label": "SD_Card",
        "size_bytes": 1024,
        "usb": None,
        "broker_epoch": "e1",
        "generation": 1,
    }
    row.update(over)
    return VolumeInfo(**row)


def test_a_restarted_broker_is_a_change_even_when_the_generation_resets(database, db):
    """`broker_epoch` は mountd 起動ごとの乱数。generation は 0 に戻る.

    epoch を組に含めないと、再起動をまたいだ古い世代が偶然一致する。
    """
    ProfileRegistry(db).sync_builtins()
    stub = StubBroker([a_volume_info(broker_epoch="e1", generation=7)])
    w = VolumeWatcher(database, {}, stub)
    try:
        first = w._token()
        # **generation は同じにする。** 変えてしまうと、epoch を見ていなくても
        # 世代の違いで検出でき、この試験が epoch を検証したことにならない。
        stub.volumes = [a_volume_info(broker_epoch="e2", generation=7)]
        assert w._token() != first, "epoch の違いを見ていない"
    finally:
        w.close()


def test_the_first_empty_observation_still_detaches_stale_presences(database, db):
    """**「未観測」と「空集合」を同じ値にしない**（計画レビュー 2 巡目の major）.

    前回の停止時に live のまま残った presence がある状態で、空で起動する。
    初期値を「空を観測済み」にすると、最初の tick が「変化なし」と判定して
    `refresh` を飛ばし、`detach_absent` が走らない —— 抜けているカードの
    presence が live のまま残り、自動取り込みの候補になる。
    """
    from .test_schema_sources import a_presence, a_volume

    ProfileRegistry(db).sync_builtins()
    profile = db.execute(
        "SELECT id, current_revision_id FROM device_profile WHERE slug = 'dji-osmo'"
    ).fetchone()
    volume = a_volume(db, profile=(profile["id"], profile["current_revision_id"]))
    stale = a_presence(db, volume)
    db.commit()

    w = VolumeWatcher(database, {}, StubBroker([]))
    try:
        assert not w.observed, "作った直後は未観測"
        w.tick()
        assert w.observed
    finally:
        w.close()
    row = db.execute("SELECT detached_at FROM volume_presence WHERE id = ?", (stale,)).fetchone()
    assert row["detached_at"] is not None, "空で起動した最初の tick が detach していない"


def test_an_unchanged_empty_observation_does_not_probe_again(database, db):
    """空のまま tick を重ねても判定は走らない."""
    ProfileRegistry(db).sync_builtins()
    w = VolumeWatcher(database, {}, StubBroker([]))
    try:
        w.tick()
        first = w._seen
        w.tick()
        assert w._seen == first
    finally:
        w.close()


def test_editing_a_profile_makes_the_watcher_probe_again(watcher, db, mount_manager):
    """プロファイルを変えると `require` の判定が変わりうるが、mountd の観測は
    動かない。門の入力に現行リビジョンの指紋を含める。
    """
    watcher.tick()
    before = mount_manager.mounts
    watcher.tick()
    assert mount_manager.mounts == before

    profile = db.execute(
        "SELECT p.id AS id, r.definition_json AS defn FROM device_profile p"
        " JOIN profile_revision r ON r.id = p.current_revision_id WHERE p.slug = 'dji-osmo'"
    ).fetchone()
    # 定義そのものは有効なまま新しい版にする（active() が読めないと判定できない）。
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES ('rev-new', ?, 99, ?, 1, '2026-08-19T00:00:00Z')",
        (profile["id"], profile["defn"]),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = 'rev-new' WHERE id = ?",
        (profile["id"],),
    )
    db.commit()
    watcher.tick()
    assert mount_manager.mounts > before, "プロファイルの変更で判定し直していない"


# ----------------------------------------------------------------------
# 2. 積んでよいかは毎 tick、DB の現在値から


def test_an_untrusted_card_is_not_imported(watcher, db, volumes):
    a_known_card(watcher, volumes)
    assert queued_imports(db) == []


def test_a_card_seen_for_the_first_time_is_not_imported(watcher, db):
    """初めて見るボリュームは確度が `low` なので、信頼登録済みでも積まない.

    §12.1 の「初めて見るボリューム…はユーザの承認を待つ」。read-only では
    永続マーカーを書けないので、1 度目の観測では前回との連続性を言えない。
    """
    watcher.tick()
    trust_the_card(db)
    watcher.tick()
    assert queued_imports(db) == []


def test_trusting_a_card_that_is_already_inserted_starts_the_import(watcher, db, volumes):
    """**計画レビュー 1 巡目の blocker。**

    `trust()` は `trusted_at` を `UPDATE` するだけで mountd の指紋を動かさない。
    観測トークンの門の内側で判定していると、カードを挿したまま画面で承認しても
    自動取り込みが始まらない —— §12.1 の「一度承認すれば以後は挿すだけ」が
    成立しなくなる。
    """
    a_known_card(watcher, volumes)
    assert queued_imports(db) == [], "信頼登録していないのに積まれた"
    trust_the_card(db)
    # **観測トークンは動いていない。** それでも次の tick で積まれること。
    watcher.tick()
    assert len(queued_imports(db)) == 1, "承認しても積まれていない"


def test_the_same_connection_is_only_enqueued_once(watcher, db, volumes):
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    watcher.tick()
    watcher.tick()
    assert len(queued_imports(db)) == 1


def test_reinserting_the_card_enqueues_again(watcher, db, volumes):
    """印は接続（presence）に付く。抜き挿しすれば新しい接続になる.

    **抜いた時点で、前の接続に紐づく queued のジョブは無効化される**（§9.2）。
    したがって「queued が 2 本」にはならない。積み直されたことは、ジョブが
    もう 1 本作られ、古い方が `cancelled` になっていることで見る。
    """
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    first = queued_imports(db)
    assert len(first) == 1

    reinsert(watcher, volumes)

    all_imports = list(db.execute("SELECT id, status FROM job WHERE type = 'import'"))
    assert len(all_imports) == 2, "挿し直しても積まれていない"
    by_id = {row["id"]: row["status"] for row in all_imports}
    assert by_id[first[0]] == "cancelled", "消えた接続のジョブが残っている"
    assert len(queued_imports(db)) == 1, "新しい接続のジョブが積まれていない"


def test_a_detached_presence_is_never_marked(watcher, db, volumes):
    """抜けた接続に印を付けない（`SELECT` と `UPDATE` の両方で見る）."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    db.execute("UPDATE job SET status = 'succeeded' WHERE type = 'import'")
    db.execute("UPDATE volume_presence SET auto_import_at = NULL, detached_at = '2026-01-01'")
    db.commit()
    watcher.tick()
    marked = db.execute(
        "SELECT count(*) FROM volume_presence WHERE auto_import_at IS NOT NULL"
    ).fetchone()[0]
    assert marked == 0, "抜けた接続に印を付けている"


def test_a_low_confidence_card_is_not_imported(watcher, db, volumes):
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    db.execute("UPDATE job SET status = 'succeeded' WHERE type = 'import'")
    db.execute("UPDATE volume_presence SET auto_import_at = NULL")
    db.execute("UPDATE volume_instance SET identity_confidence = 'low'")
    db.commit()
    watcher.tick()
    assert queued_imports(db) == []


def test_a_provisional_match_is_not_imported(watcher, db, volumes):
    """暫定マッチ（対象だが中身が無い）は自動取り込みの対象外（§12.1）."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    db.execute("UPDATE job SET status = 'succeeded' WHERE type = 'import'")
    db.execute("UPDATE volume_presence SET auto_import_at = NULL")
    db.execute("UPDATE volume_instance SET provisional = 1")
    db.commit()
    watcher.tick()
    assert queued_imports(db) == []


def test_a_profile_archived_between_probing_and_enqueueing_is_not_imported(watcher, db, volumes):
    """**計画レビュー 2 巡目の blocker の、実際に効く窓。**

    `volume_instance.profile_id` は前回の判定の写しでしかない。判定の後・積む
    前に別接続で archive が commit されうる。**tick を分解して、その窓を直接
    作る** —— tick 全体で試すと、プロファイルの指紋が変わって判定し直され、
    別の理由（`profile_id` が NULL になる）で積まれなくなってしまい、この
    条件を検証したことにならない。
    """
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher._volumes.refresh()
    db.execute("UPDATE device_profile SET archived_at = '2026-08-19T00:00:00Z'")
    db.commit()
    assert watcher._enqueue_ready() == [], "archive されたプロファイルで積んでいる"


def test_a_profile_edited_between_probing_and_enqueueing_is_not_imported(watcher, db, volumes):
    """判定に使った版が、積む前に編集されていたら積まない（同上の窓）."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher._volumes.refresh()
    profile = db.execute(
        "SELECT p.id AS id, r.definition_json AS defn FROM device_profile p"
        " JOIN profile_revision r ON r.id = p.current_revision_id WHERE p.slug = 'dji-osmo'"
    ).fetchone()
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES ('rev-later', ?, 77, ?, 1, '2026-08-19T00:00:00Z')",
        (profile["id"], profile["defn"]),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = 'rev-later' WHERE id = ?",
        (profile["id"],),
    )
    db.commit()
    assert watcher._enqueue_ready() == [], "旧リビジョンの判定で積んでいる"


def test_an_archived_profile_is_not_imported(watcher, db, volumes):
    """**計画レビュー 2 巡目の blocker。**

    `volume_instance.profile_id` は前回の判定の写しでしかない。archive された
    プロファイルで取り込みを積んではいけない。
    """
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    db.execute("UPDATE job SET status = 'succeeded' WHERE type = 'import'")
    db.execute("UPDATE volume_presence SET auto_import_at = NULL")
    db.execute("UPDATE device_profile SET archived_at = '2026-08-19T00:00:00Z'")
    db.commit()
    watcher.tick()
    assert queued_imports(db) == []


def test_a_stale_profile_revision_is_not_imported(watcher, db, volumes):
    """判定に使った版が編集されていたら積まない（同上）."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    db.execute("UPDATE job SET status = 'succeeded' WHERE type = 'import'")
    db.execute("UPDATE volume_presence SET auto_import_at = NULL")
    profile = db.execute("SELECT id FROM device_profile WHERE slug = 'dji-osmo'").fetchone()
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES ('rev-2', ?, 50, '{}', 1, '2026-08-19T00:00:00Z')",
        (profile["id"],),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = 'rev-2' WHERE id = ?", (profile["id"],)
    )
    db.commit()
    # 判定し直す前（volume_instance は旧版を指したまま）に積んではいけない
    assert queued_imports(db) == []


def test_auto_import_off_does_not_enqueue_but_still_refreshes(watcher, db, volumes, mount_manager):
    """`off` でも一覧は新鮮に保つ。積むかどうかだけを設定で決める."""
    a_known_card(watcher, volumes)
    watcher._env = {"MEDIAFERRY_AUTO_IMPORT": "off"}
    trust_the_card(db)
    before = mount_manager.mounts
    reinsert(watcher, volumes)
    assert mount_manager.mounts > before, "off でも判定はする"
    assert queued_imports(db) == []


def test_turning_auto_import_on_at_runtime_takes_effect(watcher, db, volumes):
    """`AUTO_IMPORT` は `Tier.RUNTIME`。起動時のスナップショットを見ない."""
    a_known_card(watcher, volumes)
    watcher._env = {}
    db.execute(
        "INSERT INTO app_setting (key, value, updated_at) VALUES ('AUTO_IMPORT', 'off', '2026')"
    )
    trust_the_card(db)
    watcher.tick()
    assert queued_imports(db) == []
    db.execute("UPDATE app_setting SET value = 'trusted' WHERE key = 'AUTO_IMPORT'")
    db.commit()
    watcher.tick()
    assert len(queued_imports(db)) == 1, "設定を読み直していない"


def test_the_enqueued_job_carries_the_presence_it_was_selected_for(watcher, db, volumes):
    """ジョブは選択した瞬間の presence を params に持つ（Phase 1 の契約）."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    params = json.loads(
        db.execute("SELECT params_json FROM job WHERE type = 'import'").fetchone()["params_json"]
    )
    presence = db.execute(
        "SELECT id, generation FROM volume_presence WHERE detached_at IS NULL"
    ).fetchone()
    assert params["presence_id"] == presence["id"]
    assert params["generation"] == presence["generation"]
    assert params["volume_instance_id"]
    assert params["profile_revision_id"]


def test_a_failed_enqueue_leaves_no_mark(watcher, db, volumes, monkeypatch):
    """**印付けと enqueue は原子的**（片方だけ残らない）.

    印を先に取るから安全なのではない —— 同じトランザクションだから、両方
    成立するか両方消えるかのどちらかになる。分けると、enqueue が落ちたときに
    印だけが残り、**そのカードは二度と自動取り込みされない**。
    """
    a_known_card(watcher, volumes)
    trust_the_card(db)

    def boom(self, job_type, params):  # noqa: ANN001, ARG001
        raise RuntimeError("積めなかった")

    monkeypatch.setattr(JobStore, "enqueue", boom)
    with pytest.raises(RuntimeError):
        watcher.tick()
    marked = db.execute(
        "SELECT count(*) FROM volume_presence WHERE auto_import_at IS NOT NULL"
    ).fetchone()[0]
    assert marked == 0, "積めなかったのに印だけ残っている"


def test_a_card_is_counted_as_soon_as_it_appears(watcher, db):
    """`scan` を積まないと `source_entry` が無く、自動取り込みは 0 件で成功する."""
    watcher.tick()
    assert job_types(db) == ["scan"]


def test_counting_happens_even_when_auto_import_is_off(database, db, broker, monkeypatch):
    """「取り込まない」は「数えない」ではない.

    **`watcher` フィクスチャは env で `trusted` に固定している**ので、
    ここだけ自前で組み立てる（env は DB より優先される）。
    """
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "UTC")
    ProfileRegistry(db).sync_builtins()
    off = VolumeWatcher(
        database,
        {"MEDIAFERRY_AUTO_IMPORT": "off", "MEDIAFERRY_DEFAULT_TIMEZONE": "UTC"},
        broker,
        poll_interval=0.01,
    )
    try:
        off.tick()
    finally:
        off.close()
    assert job_types(db) == ["scan"]


def test_the_automatic_path_also_looks_for_split_videos(watcher, db, volumes):
    """取り込んだあとに探さないと、ホームに「つなぐ」が出ない."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    assert job_types(db)[-2:] == ["import", "detect_groups"]


def test_the_detect_groups_params_are_what_the_runner_reads(watcher, db, volumes):
    """積んだ params を、実行側と同じ関数（`_profile_ref`）で読み戻す.

    **種類だけを見ていると、欄の名前を 1 つ書き違えても通る** —— 出荷される
    のは「claim された瞬間に `KeyError` で死ぬジョブ」で、自動の経路が丸ごと
    止まる。
    """
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    params = json.loads(
        db.execute("SELECT params_json FROM job WHERE type = 'detect_groups'").fetchone()[
            "params_json"
        ]
    )

    profile = _profile_ref(db, params)

    current = ProfileRegistry(db).current("dji-osmo")
    assert (profile.profile_id, profile.revision_id) == (
        current.profile_id,
        current.revision_id,
    )
    assert profile.definition.slug == "dji-osmo"


def test_one_tick_queues_them_in_the_order_they_must_run(watcher, db, volumes):
    """数えてから運ぶ。逆だと `import` は空の `source_entry` を読む."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    db.execute("DELETE FROM job")
    db.commit()
    reinsert(watcher, volumes)
    assert job_types(db) == ["scan", "import", "detect_groups"]


def test_reinserting_the_card_counts_it_again(watcher, db, volumes):
    """前回のスキャン以降に撮ったものを拾う道.

    印は presence ごとなので、挿し直せば新しい行になり、もう一度数える。
    """
    watcher.tick()
    reinsert(watcher, volumes)
    scans = db.execute("SELECT count(*) FROM job WHERE type = 'scan'").fetchone()[0]
    assert scans == 2


def test_an_archived_profile_is_not_counted(watcher, db):
    """archive されたプロファイルの `scan.roots` では数えない.

    `volume_instance.profile_id` は前回の判定の写しでしかない。判定の後・積む
    前に別接続で archive が commit されうるので、同じ排他区間で確かめる。
    """
    watcher.tick()
    db.execute("UPDATE volume_presence SET auto_scan_at = NULL")
    db.execute("UPDATE device_profile SET archived_at = '2026-08-19T00:00:00Z'")
    db.commit()
    assert watcher._enqueue_ready() == [], "archive されたプロファイルで数えている"


def test_a_stale_profile_revision_is_not_counted(watcher, db):
    """判定に使った版が編集されていたら、その版では数えない（同上の窓）."""
    watcher.tick()
    db.execute("UPDATE volume_presence SET auto_scan_at = NULL")
    profile = db.execute("SELECT id FROM device_profile WHERE slug = 'dji-osmo'").fetchone()
    db.execute(
        "INSERT INTO profile_revision"
        " (id, profile_id, revision, definition_json, schema_version, created_at)"
        " VALUES ('rev-counting', ?, 51, '{}', 1, '2026-08-19T00:00:00Z')",
        (profile["id"],),
    )
    db.execute(
        "UPDATE device_profile SET current_revision_id = 'rev-counting' WHERE id = ?",
        (profile["id"],),
    )
    db.commit()
    assert watcher._enqueue_ready() == [], "旧リビジョンの判定で数えている"


def test_a_detached_presence_is_never_counted(watcher, db, volumes):
    """抜けた接続に印を付けない（`SELECT` と `UPDATE` の両方で見る）."""
    watcher.tick()
    db.execute("UPDATE volume_presence SET auto_scan_at = NULL, detached_at = '2026-01-01'")
    db.commit()
    watcher.tick()
    marked = db.execute(
        "SELECT count(*) FROM volume_presence WHERE auto_scan_at IS NOT NULL"
    ).fetchone()[0]
    assert marked == 0, "抜けた接続に印を付けている"


def test_a_failed_scan_enqueue_leaves_no_mark(watcher, db, volumes, monkeypatch):
    """**印付けと enqueue は原子的**（片方だけ残らない）.

    分けると、enqueue が落ちたときに印だけが残り、**同じ presence のままでは
    二度と数えられない**（抜き差しすれば新しい presence になり、`scan` は
    積み直される）。
    """

    def boom(self, job_type, params):  # noqa: ANN001
        raise RuntimeError("積めなかった")

    monkeypatch.setattr(JobStore, "enqueue", boom)
    with pytest.raises(RuntimeError):
        watcher.tick()
    marked = db.execute(
        "SELECT count(*) FROM volume_presence WHERE auto_scan_at IS NOT NULL"
    ).fetchone()[0]
    assert marked == 0, "積めなかったのに印だけ残っている"


def test_the_counting_job_is_reported_even_when_auto_import_is_off(
    database, db, broker, monkeypatch
):
    """`off` で早く返るとき、積んだ `scan` を捨てない.

    `tick` の戻り値は「この周で積んだもの」。捨てると、呼び出し側からは
    何も積まなかったのと区別が付かない。
    """
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "UTC")
    ProfileRegistry(db).sync_builtins()
    off = VolumeWatcher(
        database,
        {"MEDIAFERRY_AUTO_IMPORT": "off", "MEDIAFERRY_DEFAULT_TIMEZONE": "UTC"},
        broker,
        poll_interval=0.01,
    )
    try:
        enqueued = off.tick()
    finally:
        off.close()
    scans = [row["id"] for row in db.execute("SELECT id FROM job WHERE type = 'scan'")]
    assert enqueued == scans, "積んだ `scan` を返していない"


def test_every_enqueued_job_is_written_to_the_log(watcher, db, caplog):
    """積んだものは、どの経路でも同じ数だけログに出す.

    **この不具合（watcher が `scan` を積まない）は実機のログから見つかった。**
    経路によって記録が落ちると、次に同じことが起きたときの手がかりが消える。
    """
    with caplog.at_level(logging.INFO, logger="mediaferry.jobs.watcher"):
        enqueued = watcher.tick()

    assert enqueued
    logged = [
        record.getMessage() for record in caplog.records if "自動で積んだ" in record.getMessage()
    ]
    assert len(logged) == len(enqueued)
    for job_id in enqueued:
        assert any(job_id in line for line in logged), f"{job_id} が記録に出ていない"


def test_the_log_says_which_kind_of_job_it_queued(watcher, db, volumes, caplog):
    """id だけでは、3 種類のどれを積んだのか読めない.

    1 行を `scan` / `import` / `detect_groups` の 3 つで共有しているので、
    種類が無いと「積んだのに走らない」を追うのに DB を引くしかない。
    **この不具合（watcher が `scan` を積まない）は実機のログから見つかった。**
    """
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    db.execute("DELETE FROM job")
    db.commit()
    with caplog.at_level(logging.INFO, logger="mediaferry.jobs.watcher"):
        reinsert(watcher, volumes)

    logged = [
        record.getMessage() for record in caplog.records if "自動で積んだ" in record.getMessage()
    ]
    kinds = {row["id"]: row["type"] for row in db.execute("SELECT id, type FROM job")}
    assert sorted(kinds.values()) == ["detect_groups", "import", "scan"]
    for line in logged:
        job_id = next(candidate for candidate in kinds if candidate in line)
        assert kinds[job_id] in line, f"何を積んだのかが記録に出ていない: {line}"
    assert len(logged) == len(kinds)


def test_counting_is_marked_so_it_does_not_pile_up(watcher, db):
    """同じ接続に何度も積まない（印を付けるのと積むのは同じ排他区間）."""
    watcher.tick()
    watcher.tick()
    watcher.tick()
    scans = db.execute("SELECT count(*) FROM job WHERE type = 'scan'").fetchone()[0]
    assert scans == 1


# ----------------------------------------------------------------------
# 3. 消えた接続の掃除


def test_queued_jobs_for_a_gone_presence_are_invalidated(watcher, db, volumes):
    """§9.2 の `remove` 規則。まだ claim されていないジョブだけを畳む."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    job = queued_imports(db)[0]
    volumes.clear()
    watcher.tick()
    status = db.execute("SELECT status FROM job WHERE id = ?", (job,)).fetchone()["status"]
    assert status == "cancelled", f"消えた接続のジョブが queued のまま: {status}"


def test_a_running_job_for_a_gone_presence_is_left_alone(watcher, db, volumes):
    """走っているジョブには触らない.

    `expect` 検証と `StaleSelection` が既に守っている。ここで触ると
    「実行中のジョブを外から失敗させる」経路を新設することになる。
    """
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    job = queued_imports(db)[0]
    db.execute(
        "UPDATE job SET status = 'running', lease_token = 't',"
        " lease_expires_at = '2099-01-01T00:00:00Z' WHERE id = ?",
        (job,),
    )
    db.commit()
    volumes.clear()
    watcher.tick()
    status = db.execute("SELECT status FROM job WHERE id = ?", (job,)).fetchone()["status"]
    assert status == "running", "実行中のジョブを外から止めている"


def test_refreshing_does_not_close_handles_that_a_job_holds(watcher, db, volumes, broker):
    """**2 枚挿して片方を抜いても、もう片方のコピーは止まらない。**

    マウントは handle ごとに独立していて（`MNT_DETACH` 済み）、
    `MountManager.release` は umount ではなく、`detach_absent` は DB の
    `UPDATE` でしかない。この性質を守るテストが 1 つも無かった。
    """
    from mediaferry.jobs.volumes import VolumeService

    watcher.tick()
    # ジョブ側（API 側の VolumeService）が handle を掴んでいる状態を作る
    service = VolumeService(db, ProfileRegistry(db), broker)
    selection = service.refresh()[0].selection
    handle = service.open(selection)
    assert not handle.closed
    try:
        volumes.clear()
        watcher.tick()
        assert service.opened() == [selection.volume_instance_id], "掴んでいる handle が消えた"
        assert not handle.closed, "掴んでいる handle が閉じられた"
    finally:
        service.release(selection)


# ----------------------------------------------------------------------
# 4. 停止


class SilentBroker:
    """応答を返さないブローカー. `close()` されるまで `list_volumes` が戻らない.

    **待ちには上限を置く。** 置かないと、停止が接続を閉じなくなる回帰のときに
    ワーカースレッドが永久に残り、テストが「失敗」ではなく**ハング**する
    （`to_thread` のスレッドはインタプリタ終了時に join される）。
    回帰は「時間切れ」ではなく assert で落ちなければならない。
    """

    RELEASE_AFTER_SECONDS = 20.0

    def __init__(self):
        self._released = threading.Event()
        self.closed = False

    def list_volumes(self):
        self._released.wait(timeout=self.RELEASE_AFTER_SECONDS)
        raise OSError("closed")

    def close(self):
        self.closed = True
        self._released.set()


@pytest.mark.anyio
async def test_stopping_returns_even_when_the_broker_never_answers(database, db, anyio_backend):
    """**`wait_for` では止まらない。**

    `to_thread` に出した呼び出しは task の cancel では止まらず、
    `recv_message` に timeout も無い。停止は自分のソケットを閉じて解く。
    """
    ProfileRegistry(db).sync_builtins()
    broker = SilentBroker()
    w = VolumeWatcher(database, {}, broker, poll_interval=0.01)
    task = asyncio.create_task(w.run_forever())
    await asyncio.sleep(0.05)
    assert not task.done(), "応答が無いのに戻ってきた（試験の前提が崩れている）"
    await w.stop()
    await asyncio.wait_for(task, timeout=5)
    assert broker.closed
    w.close()


def test_the_watcher_owns_its_own_broker_connection(client):
    """**watcher の停止が、走っている取り込みの handle 接続を切らない。**

    handle は発行した接続に束縛されている（§11）ので、API 側と共有していると
    停止の close が取り込みの相手を消す。
    """
    state = client.app.state.mediaferry
    assert state.watcher._client is not state.volumes._client, (
        "watcher が API 側のブローカー接続を借りている"
    )


def test_the_auto_import_setting_is_read_inside_the_exclusive_section(
    watcher, database, db, volumes, monkeypatch
):
    """**「積んでよいか」の入力は全部、同じ排他区間の中で読む。**

    `AUTO_IMPORT` だけ `BEGIN IMMEDIATE` の外で読んでいると、その隙間に
    別接続の `PUT /settings` が `off` を commit でき、**off になった後で
    `auto_import_at` を刻んで import を積める**（§12.1）。detach / archive /
    信頼解除 / 版は排他の中へ移してあるので、ここだけ古い snapshot になる。

    値そのものでは区別が付かない（読んだ順で正しい答えが変わる）ので、
    **割り込もうとした側が書き込みロックで弾かれること**を見る。
    """
    import sqlite3

    from mediaferry.settings import SettingsService

    a_known_card(watcher, volumes)
    trust_the_card(db)
    other = database.connect()
    other.execute("PRAGMA busy_timeout = 0")
    committed: list[bool] = []
    original = SettingsService.snapshot

    def peeking(self):  # noqa: ANN001, ANN202
        got = original(self)
        try:
            other.execute(
                "INSERT INTO app_setting (key, value, updated_at)"
                " VALUES ('AUTO_IMPORT', 'off', '2026')"
            )
            committed.append(True)
        except sqlite3.OperationalError:
            pass  # 書き込みロックに弾かれた＝排他の中で読んでいる
        return got

    monkeypatch.setattr(SettingsService, "snapshot", peeking)
    try:
        watcher.tick()
    finally:
        other.close()

    assert committed == [], "AUTO_IMPORT を読んだ後に、別接続が off を commit できた"


def test_a_first_seen_card_starts_after_the_screen_refreshes_it(watcher, db, volumes):
    """**挿し直さなくても、同じ挿入のうちに自動取り込みが始まる。**

    初回の観測は `identity_confidence = low`（憶えた指紋が無い）だが、その観測で
    指紋を憶える。`GET /devices` は必ず `refresh()` を走らせるので、承認して
    一覧を取り直すと同じ挿入のまま `high` になり、次の tick で積まれる。
    **画面の文言はこの経路を否定してはいけない**（`autoImportOutlook` の
    「確かめられしだい」がこれに対応する）。
    """
    watcher.tick()  # 初回の観測。low のまま指紋を憶える
    assert db.execute("SELECT identity_confidence FROM volume_instance").fetchone()[0] == "low"
    trust_the_card(db)
    watcher.tick()
    assert queued_imports(db) == [], "確度が low の間は積まない"

    # 画面が一覧を取り直す（`GET /devices` は必ず refresh する）。
    watcher._volumes.refresh()
    assert db.execute("SELECT identity_confidence FROM volume_instance").fetchone()[0] == "high"

    watcher.tick()
    assert len(queued_imports(db)) == 1
