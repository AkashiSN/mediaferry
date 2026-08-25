"""カード 1 枚の中身（Phase 11 の R8・R9）.

**画面が「どのカードか」を見分けられるようにする。** これまで出せたのは
`pending_count` だけで、何が入っているかは 1 件も読めなかった。ボリュームの
総容量（`size_bytes`）と取り込み待ちの合計は別物なので、**両方に名前を付けて
返す** —— 数字が 1 つだけ出ていると、どちらのサイズなのか読めない。

**撮影時刻は返せない。** `captured_at` は取り込んで probe を通したあとにしか
無い（`media_file` の欄）。`source_entry` が持つ時刻は `mtime_ns` だけ。
"""

from __future__ import annotations

import pytest

from mediaferry.clock import now_iso
from mediaferry.db.connection import Database
from mediaferry.ids import new_id

from .test_schema_sources import a_volume


@pytest.fixture
def api_db(client, data_root):
    """API と同じ DB ファイルを、テスト用の別接続で開く."""
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


def an_entry(db, volume_id: str, rel_path: str, *, size: int = 10, state: str = "seen") -> str:
    entry_id = new_id()
    db.execute(
        "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes, mtime_ns,"
        " quick_fingerprint, fingerprint_version, state, observed_at)"
        " VALUES (?, ?, ?, ?, 1700000000000000000, 'abc', 1, ?, ?)",
        (entry_id, volume_id, rel_path, size, state, now_iso()),
    )
    return entry_id


def test_contents_lists_the_pending_files_with_their_total(client, api_db):
    """取り込み待ちの一覧と、その合計サイズ."""
    volume = a_volume(api_db, fs_uuid="1111-1111")
    an_entry(api_db, volume, "DCIM/100CANON/IMG_0001.JPG", size=30)
    an_entry(api_db, volume, "DCIM/100CANON/IMG_0002.JPG", size=12)

    body = client.get(f"/api/volumes/{volume}/contents").json()

    assert [entry["rel_path"] for entry in body["entries"]] == [
        "DCIM/100CANON/IMG_0001.JPG",
        "DCIM/100CANON/IMG_0002.JPG",
    ]
    assert body["pending_bytes"] == 42
    assert body["pending_count"] == 2
    assert body["truncated"] is False


def test_contents_leaves_out_what_is_already_imported(client, api_db):
    """**取り込み済みは「取り込み待ち」ではない.**

    ホームの「N 件を取り込む」と同じ条件（`PENDING_CLAUSE`）を使う。別々に
    書くと、札の数と一覧の数が食い違う。
    """
    volume = a_volume(api_db, fs_uuid="2222-2222")
    an_entry(api_db, volume, "DCIM/A.JPG", state="seen")
    an_entry(api_db, volume, "DCIM/B.JPG", state="published")
    an_entry(api_db, volume, "DCIM/C.JPG", state="failed")

    body = client.get(f"/api/volumes/{volume}/contents").json()

    assert sorted(entry["rel_path"] for entry in body["entries"]) == ["DCIM/A.JPG", "DCIM/C.JPG"]
    assert body["pending_count"] == 2


def test_contents_says_when_it_stopped_short(client, api_db):
    """**数万件のカードがある.** 上限で切ったことを黙らない（裁定 20）."""
    volume = a_volume(api_db, fs_uuid="3333-3333")
    for index in range(7):
        an_entry(api_db, volume, f"DCIM/{index:04d}.JPG")

    body = client.get(f"/api/volumes/{volume}/contents?limit=3").json()

    assert len(body["entries"]) == 3
    assert body["truncated"] is True
    # **合計と件数は切らない。** 出せる分だけを合計にすると、画面が
    # 「このカードにどれだけ入っているか」を小さく見せる。
    assert body["pending_count"] == 7


def test_contents_returns_the_card_time_not_a_capture_time(client, api_db):
    """**撮影時刻とは名乗らせない.** 取り込む前に分かるのはファイルの時刻だけ."""
    volume = a_volume(api_db, fs_uuid="4444-4444")
    an_entry(api_db, volume, "DCIM/A.JPG")

    entry = client.get(f"/api/volumes/{volume}/contents").json()["entries"][0]

    assert entry["mtime_ns"] == 1700000000000000000
    assert "captured_at" not in entry


def test_contents_of_an_unknown_volume_is_a_404(client):
    assert client.get("/api/volumes/does-not-exist/contents").status_code == 404
