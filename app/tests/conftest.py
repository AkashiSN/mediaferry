import os
import socket
import threading
import time
from dataclasses import dataclass, replace

import pytest
from fastapi.testclient import TestClient

from mediaferry.adapters.broker_client import BrokerClient
from mediaferry.api.app import create_app
from mediaferry.clock import now_iso
from mediaferry.core.profiles.model import definition_to_json
from mediaferry.db.connection import Database
from mediaferry.db.migrate import apply_migrations
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.ids import new_id
from mediaferry_protocol.messages import UsbInfo, VolumeInfo
from mountd.server import BrokerServer

from .test_schema_artifacts import a_media_file
from .test_schema_sources import a_volume


class FakeMountManager:
    """マウントはせず、用意したディレクトリの dirfd を返す.

    プロトコルは実物の BrokerServer が話すので、取り違えは見逃さない。
    """

    def __init__(self, target):
        self.target = target
        self._open = {}
        self._n = 0

    @property
    def mounts(self) -> int:
        """これまでに開いた回数. 「判定のたびにマウントする」代償を測る."""
        return self._n

    def mount(self, volume, expect, verify):
        self._n += 1
        handle = f"h{self._n}"
        verify()
        self._open[handle] = os.open(self.target, os.O_RDONLY | os.O_DIRECTORY)
        return handle, self._open[handle]

    def release(self, handle):
        fd = self._open.pop(handle, None)
        if fd is not None:
            os.close(fd)

    def release_all(self):
        for handle in list(self._open):
            self.release(handle)


@pytest.fixture
def fake_card(tmp_path):
    card = tmp_path / "card"
    (card / "DCIM" / "DJI_001").mkdir(parents=True)
    (card / "DCIM" / "DJI_001" / "DJI_20260817143000_0001_D.MP4").write_bytes(b"a" * 100)
    return card


@pytest.fixture
def mount_manager(fake_card):
    """`target` を差し替えると、以後の open だけが新しい中身を見る.

    既に渡した dirfd は古いディレクトリを指したままになるので、
    「カードが差し替わったのに古い fd を使い回す」経路を再現できる。
    """
    return FakeMountManager(fake_card)


@pytest.fixture
def volumes():
    """broker が列挙するボリューム.

    テストはこのリストを書き換えて抜き差しを表す。クライアント側だけを
    差し替えると、サーバが知らないボリュームを開こうとして
    `unknown_volume` になる（実機では起きない状態）。
    """
    return [
        VolumeInfo(
            volume_key="8:160",
            device_node="/dev/sdk",
            major=8,
            minor=160,
            sysfs_path="/sys/x",
            fs_type="exfat",
            fs_uuid="26B1-2FD6",
            fs_label="SD_Card",
            size_bytes=512_000_000_000,
            usb=UsbInfo(
                vendor_id="2ca3",
                product_id="0020",
                product="OsmoPocket4-ABC123",
                serial="123456789ABCDEF",
            ),
            broker_epoch="",
            generation=1,
        )
    ]


@pytest.fixture
def broker_factory(mount_manager, tmp_path, volumes):
    """**呼ぶたびに新しい接続を作る。** サーバは 1 つで、接続だけを増やす。

    実物と同じく、handle は発行した接続に束縛される（§11）。同じ client を
    使い回すと「VolumeWatcher は専用のブローカー接続を持つ」という性質を
    テストで確かめられない —— watcher の停止が取り込みの相手を切る経路が
    そのまま素通りする。
    """
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        mount_manager=mount_manager,
        lister=lambda: list(volumes),
        allowed_uids=None,
    )
    made = []

    def make() -> BrokerClient:
        client_sock, server_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        thread = threading.Thread(target=server.handle_connection, args=(server_sock,), daemon=True)
        thread.start()
        client = BrokerClient.from_socket(client_sock)
        made.append((client, thread))
        return client

    yield make
    for client, thread in made:
        client.close()
        thread.join(timeout=5)


@pytest.fixture
def broker(broker_factory):
    return broker_factory()


@pytest.fixture
def anyio_backend():
    """JobRunner は asyncio ワーカーなので、anyio の trio 側は使わない."""
    return "asyncio"


@pytest.fixture
def data_root(tmp_path):
    """§7 のレイアウト. staging は library と同じファイルシステムに要る."""
    root = tmp_path / "data"
    for name in ("library", "derived", "staging", "work", "var"):
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
def database(data_root):
    return Database(data_root / "var" / "mediaferry.sqlite3")


@pytest.fixture
def db(database):
    conn = database.connect()
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(data_root, broker_factory, monkeypatch):
    """起動時に migration とビルトインの同期、reconciliation が走る."""
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    app = create_app(broker_factory=broker_factory)
    # **ブラウザと同じ形で叩く。** Host はループバック（rebinding 対策で名前は
    # 許可制。§14）、状態を変える要求には二重送信 Cookie の対を付ける。
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        token = "test-csrf-token"  # noqa: S105 - テスト用の見せかけの値
        client.cookies.set("XSRF-TOKEN", token)
        client.headers["X-CSRF-Token"] = token
        yield client


@pytest.fixture
def immich():
    """ループバックで listen する fake Immich. テストごとに新しいポート."""
    from .fake_immich import FakeImmich

    server = FakeImmich()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def secured_app(data_root, broker_factory, monkeypatch):
    """認証を有効にしたアプリと、その CSRF トークン."""
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MEDIAFERRY_AUTH_PASSWORD", "correct horse")
    app = create_app(broker_factory=broker_factory)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        token = client.get("/api/auth/session").cookies["XSRF-TOKEN"]
        client.headers["X-CSRF-Token"] = token
        yield client, token


# ----------------------------------------------------------------------
# `GET /media?collapse=stack` 用の fixture（Phase 10 Task 6）。


def _await_job(client, job_id, timeout=20.0):
    """ジョブが終わるまで待つ. `test_api.py` の同名の私用ヘルパーと同じ作法."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()["status"]
        if status not in {"queued", "running", "cancelling"}:
            assert status == "succeeded", client.get(f"/api/jobs/{job_id}").json()
            return
        time.sleep(0.05)
    raise AssertionError(f"ジョブ {job_id} が終わらない")


# **canon_pair / ambiguous_sibling が相乗りする同席の印。** 実物のスキャンが
# `_mark_copresence` で書く `<job_id>:<stem prefix>` と同じ形。
_CANON_PROOF = "job1:DCIM/100CANON/IMG_0001."


@dataclass
class CanonPair:
    """`canon_pair` が作った行の手がかり. `ambiguous_sibling` / `narrowed_stack_rule` が使う."""

    volume_instance_id: str
    profile_id: str
    revision_id: str
    media_ids: dict
    proof: str | None


def _insert_source_entry(
    db, *, volume_id, rel_path, media_id, extension, copresent_key, state="published"
):
    """`source_entry` を 1 行作る. **`extension` を明示する**

    （`一覧の従外しは `source_entry.extension` を rank と突き合わせるので、
    NULL のままだとどの rank にも一致しない）。
    """
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, media_file_id, state, observed_at,"
        " copresent_key, extension)"
        " VALUES (?, ?, ?, 10, 1, ?, 1, ?, ?, ?, ?, ?)",
        (
            new_id(),
            volume_id,
            rel_path,
            new_id(),
            media_id,
            state,
            now_iso(),
            copresent_key,
            extension,
        ),
    )


def _make_canon_pair(
    db, *, proof: str | None, insertion_order: tuple[str, str] = ("JPG", "CR2")
) -> CanonPair:
    registry = ProfileRegistry(db)
    profile = registry.current("canon-eos")
    # **`client` fixture が既定の DJI ボリューム（`fs_uuid="26B1-2FD6"`）を持つ。**
    # `a_volume` の既定値のままだと UNIQUE (fs_uuid, fs_type, size_bytes) が衝突する。
    volume_id = a_volume(db, (profile.profile_id, profile.revision_id), fs_uuid="CANON-EOS-0001")
    media_ids = {}
    for extension in insertion_order:
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/canon-eos/DCIM/100CANON/IMG_0001.{extension}",
            kind="photo",
            duration_seconds=None,
            captured_at="2026-08-19T10:30:00+09:00",
            captured_at_source="exif",
        )
        _insert_source_entry(
            db,
            volume_id=volume_id,
            rel_path=f"DCIM/100CANON/IMG_0001.{extension}",
            media_id=media_id,
            extension=extension,
            copresent_key=proof,
        )
        media_ids[extension] = media_id
    db.commit()
    return CanonPair(
        volume_instance_id=volume_id,
        profile_id=profile.profile_id,
        revision_id=profile.revision_id,
        media_ids=media_ids,
        proof=proof,
    )


@pytest.fixture
def canon_pair(client, db) -> CanonPair:
    """canon-eos の `media_file` を JPG・CR2 の 2 行と、同じ同席の証拠を持つ

    `source_entry` を 2 行作る. `stack.extensions` の順で JPG が primary."""
    return _make_canon_pair(db, proof=_CANON_PROOF)


@pytest.fixture
def canon_pair_without_proof(client, db) -> CanonPair:
    """`canon_pair` と同じ形だが `copresent_key` が NULL —— 同席の証拠が無い."""
    return _make_canon_pair(db, proof=None)


@pytest.fixture
def canon_pair_inserted_in_reverse(client, db) -> CanonPair:
    """`canon_pair` と同じ組だが、**CR2 を JPG より先に** `media_file` へ入れる.

    `stack.extensions` の順位（JPG が primary）は変わらない。挿入順と主従の順位を
    ずらすことで、`_members_of` の `ORDER BY r.rank` を消しても素通りしてしまう
    テスト（挿入順とたまたま同じ順で返る）を作らない。
    """
    return _make_canon_pair(db, proof=_CANON_PROOF, insertion_order=("CR2", "JPG"))


@pytest.fixture
def dji_media(client):
    """`client` の `fake_card`（dji-osmo, `stack.enabled = false`）をそのまま取り込む."""
    volume_id = client.get("/api/devices").json()["volumes"][0]["volume_instance_id"]
    _await_job(client, client.post(f"/api/volumes/{volume_id}/scan").json()["job_id"])
    _await_job(client, client.post(f"/api/volumes/{volume_id}/import").json()["job_id"])


@pytest.fixture
def ambiguous_sibling(db, canon_pair):
    """`canon_pair` の JPG と同じ順位・同じ同席の証拠を持つ、もう 1 つの JPG.

    大小文字違いの原名（`IMG_0001.jpg`）で、どちらが主か決まらない状況を作る。
    """
    media_id = a_media_file(
        db,
        (canon_pair.profile_id, canon_pair.revision_id),
        rel_path="library/canon-eos/DCIM/100CANON/IMG_0001_alt.JPG",
        kind="photo",
        duration_seconds=None,
        captured_at="2026-08-19T10:30:00+09:00",
        captured_at_source="exif",
    )
    _insert_source_entry(
        db,
        volume_id=canon_pair.volume_instance_id,
        rel_path="DCIM/100CANON/IMG_0001.jpg",
        media_id=media_id,
        extension="JPG",
        copresent_key=canon_pair.proof,
    )
    db.commit()
    return media_id


def _second_card(
    db, canon_pair, *, shared: str, fresh: str, shared_state: str = "published"
) -> str:
    """`canon_pair` と同じ原名の 2 枚組が載った、2 枚目のカードを足す.

    `shared` の拡張子は 1 枚目と**同じ中身**なので同じ `media_file`（観測が 2 つに
    増えるだけ）、`fresh` の拡張子は中身が違うので**別の** `media_file` になる。
    返すのは新しくできた `media_file` の id。
    """
    registry = ProfileRegistry(db)
    profile = registry.current("canon-eos")
    volume_id = a_volume(db, (profile.profile_id, profile.revision_id), fs_uuid="CANON-EOS-0002")
    proof = "job2:DCIM/100CANON/IMG_0001."
    _insert_source_entry(
        db,
        volume_id=volume_id,
        rel_path=f"DCIM/100CANON/IMG_0001.{shared}",
        media_id=canon_pair.media_ids[shared],
        extension=shared,
        copresent_key=proof,
        state=shared_state,
    )
    media_id = a_media_file(
        db,
        (canon_pair.profile_id, canon_pair.revision_id),
        rel_path=f"library/canon-eos/DCIM/100CANON/IMG_0001_2.{fresh}",
        kind="photo",
        duration_seconds=None,
        captured_at="2026-08-19T10:30:00+09:00",
        captured_at_source="exif",
    )
    _insert_source_entry(
        db,
        volume_id=volume_id,
        rel_path=f"DCIM/100CANON/IMG_0001.{fresh}",
        media_id=media_id,
        extension=fresh,
        copresent_key=proof,
    )
    db.commit()
    return media_id


@pytest.fixture
def second_card_with_another_raw(db, canon_pair):
    """同じ JPG が 2 枚目のカードにも在り、そのカードには**別の** CR2 が在る.

    `identity_partners` は主の**複数の観測にまたがって** `by_extension` を数える
    ので、JPG から見ると CR2 が 2 つ＝曖昧。**主が曖昧なら従を隠さない**。
    """
    return _second_card(db, canon_pair, shared="JPG", fresh="CR2")


@pytest.fixture
def second_card_with_another_jpeg(db, canon_pair):
    """同じ CR2 が 2 枚目のカードにも在り、そのカードには**別の** JPG が在る.

    従（CR2）の側だけが曖昧になる形。主（どちらの JPG）から見た相方は 1 枚に
    決まるので、主の曖昧さだけを見ると CR2 が隠れてしまう。
    """
    return _second_card(db, canon_pair, shared="CR2", fresh="JPG")


@pytest.fixture
def second_card_still_importing(db, canon_pair):
    """2 枚目のカードの CR2 は公開済みだが、同じカードの JPG はまだ取り込み中.

    **公開されていない観測は、身元の材料に数えない**（`sources_of` /
    `siblings_on_card` と同じ）。数えると、1 枚目の組が「CR2 が 2 つある」に
    見えて畳まれなくなる。
    """
    return _second_card(db, canon_pair, shared="JPG", fresh="CR2", shared_state="importing")


@pytest.fixture
def narrowed_stack_rule(db, canon_pair):
    """canon-eos の `stack.extensions` から CR2 を外した新しいリビジョンを作る.

    ビルトインは通常の `update()` では編集できない（`ProfileIsBuiltin`）。
    `sync_builtins` と同じ経路（`_upsert_revision`）で新しいリビジョンを作り、
    「アプリの更新で規則が変わった」を模す。
    """
    registry = ProfileRegistry(db)
    old = registry.current("canon-eos").definition
    # **2 つ未満にはできない**（`stack.extensions` は「1 つでは組にならない」で
    # 弾かれる）。CR2 を MOV に差し替えて、CR2 だけを外に出す。
    narrowed = replace(old, stack=replace(old.stack, extensions=("JPG", "MOV")))
    registry._upsert_revision("canon-eos", definition_to_json(narrowed))  # noqa: SLF001
    db.commit()


@pytest.fixture
def cross_profile_rank_collision(db, canon_pair):
    """`canon_pair` の CR2 を、`extensions` を逆順にした別プロファイルへ付け替える.

    **同席グループの中身が同じプロファイルとは限らない。** `media_file.profile_id`
    は取り込み時のまま不変で、`source_entry.copresent_key` はボリューム単位で
    採番されるだけなので、同じ同席グループに別プロファイルの `media_file` が
    混ざりうる（スキーマはこれを禁じない）。ここでは `canon-eos` を複製し
    `stack.extensions` を `["CR2", "JPG"]`（逆順）にした別プロファイルへ、
    既存の CR2 を付け替える。JPG（`canon-eos`, 順位 0）と CR2
    （複製先, 順位 0）は**拡張子が違う**ので `_AMBIGUOUS_EXISTS`
    （`b.extension = a.extension`）は反応しない —— `theirs.rank < mine.rank` の
    **厳密さ**だけが両者を隠さずに済ませる砦になる。
    """
    registry = ProfileRegistry(db)
    reversed_ref = registry.duplicate("canon-eos", "canon-eos-reversed", "Canon EOS (reversed)")
    narrowed = replace(
        reversed_ref.definition,
        stack=replace(reversed_ref.definition.stack, extensions=("CR2", "JPG")),
    )
    updated = registry.update("canon-eos-reversed", narrowed)
    # **`media_file_captured_revision_update` トリガの契約を守る。** `profile_id` を
    # 変えるときは、同じ UPDATE で `captured_at_revision_id` も新しいプロファイルの
    # リビジョンに揃える（複合 FK が「同じプロファイルの版であること」を求める）。
    db.execute(
        "UPDATE media_file SET profile_id = ?, profile_revision_id = ?,"
        " captured_at_revision_id = ? WHERE id = ?",
        (
            updated.profile_id,
            updated.revision_id,
            updated.revision_id,
            canon_pair.media_ids["CR2"],
        ),
    )
    db.commit()
    return updated
