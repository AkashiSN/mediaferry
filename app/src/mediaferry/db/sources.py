"""ソース側のレコードの upsert.

デバイスの同定は (vendor, product_id, product, serial) の組で行う。serial 単独は
機種の既定値でありうるので識別子にしない。ボリュームは (fs_uuid, fs_type,
size_bytes) で引くが、これは識別子ではなく推測である。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ..clock import now_iso
from ..ids import new_id


def upsert_device(conn: sqlite3.Connection, usb) -> str | None:  # noqa: ANN001
    if usb is None:
        return None
    key = (usb.vendor_id, usb.product_id, usb.product or "", usb.serial or "")
    row = conn.execute(
        "SELECT id FROM source_device WHERE usb_vendor_id = ? AND usb_product_id = ?"
        " AND usb_product = ? AND serial = ?",
        key,
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE source_device SET last_seen_at = ? WHERE id = ?", (now_iso(), row["id"])
        )
        return row["id"]
    device_id = new_id()
    conn.execute(
        "INSERT INTO source_device (id, usb_vendor_id, usb_product_id, usb_product, serial,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (device_id, *key, now_iso(), now_iso()),
    )
    return device_id


def resolve_volume_instance(conn: sqlite3.Connection, volume, device_id: str | None) -> str:  # noqa: ANN001
    """観測したボリュームを既存の行に結び付ける. 無ければ作る.

    UUID があれば `(fs_uuid, fs_type, size_bytes)` で引く（これは識別子では
    なく推測なので、確度は別に判定する）。

    **UUID が無いときは、同じ接続がまだ live かどうかで引く。** 毎回新しい行を
    作ると、同じカードが挿さったままでも refresh のたびに `volume_instance` と
    `presence` が変わり、直前に画面で選んだ selection が次の refresh で
    detached になる。

    ただし世代が変われば同定は継承しない。UUID が無い以上「同じカードだ」と
    言えないので、抜き差しをまたぐ継承は誤同定になる。
    """
    row = None
    if volume.fs_uuid:
        row = conn.execute(
            "SELECT id FROM volume_instance WHERE fs_uuid = ? AND fs_type = ? AND size_bytes = ?",
            (volume.fs_uuid, volume.fs_type or "", volume.size_bytes),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT v.id AS id FROM volume_instance v"
            " JOIN volume_presence p ON p.volume_instance_id = v.id"
            " WHERE v.fs_uuid = '' AND p.detached_at IS NULL AND p.broker_epoch = ?"
            " AND p.generation = ? AND p.major = ? AND p.minor = ? LIMIT 1",
            (volume.broker_epoch, volume.generation, volume.major, volume.minor),
        ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE volume_instance SET last_seen_at = ?, last_source_device_id = ?,"
            " fs_label = ? WHERE id = ?",
            (now_iso(), device_id, volume.fs_label or "", row["id"]),
        )
        return row["id"]
    volume_id = new_id()
    conn.execute(
        "INSERT INTO volume_instance (id, fs_uuid, fs_type, fs_label, size_bytes,"
        " identity_confidence, last_source_device_id, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'low', ?, ?, ?)",
        (
            volume_id,
            volume.fs_uuid or "",
            volume.fs_type or "",
            volume.fs_label or "",
            volume.size_bytes,
            device_id,
            now_iso(),
            now_iso(),
        ),
    )
    return volume_id


def sync_presence(conn: sqlite3.Connection, volume_instance_id: str, volume) -> str:  # noqa: ANN001
    """観測した接続を 1 行に対応させる. 列挙のたびに増やさない.

    増やすと、キューに積んだときの `presence_id` と実行時のそれが別物になり、
    同じカードが挿さったままでも `StaleSelection` になる。
    """
    key = (volume_instance_id, volume.broker_epoch, volume.generation, volume.major, volume.minor)
    row = conn.execute(
        "SELECT id FROM volume_presence WHERE volume_instance_id = ? AND broker_epoch = ?"
        " AND generation = ? AND major = ? AND minor = ?",
        key,
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE volume_presence SET detached_at = NULL, device_node = ?, sysfs_path = ?"
            " WHERE id = ?",
            (volume.device_node, volume.sysfs_path, row["id"]),
        )
        return row["id"]
    presence_id = new_id()
    conn.execute(
        "INSERT INTO volume_presence (id, volume_instance_id, broker_epoch, generation,"
        " device_node, major, minor, sysfs_path, attached_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            presence_id,
            volume_instance_id,
            volume.broker_epoch,
            volume.generation,
            volume.device_node,
            volume.major,
            volume.minor,
            volume.sysfs_path,
            now_iso(),
        ),
    )
    return presence_id


def detach_absent(conn: sqlite3.Connection, seen_presence_ids: Sequence[str]) -> int:
    """今回の観測に無い live な接続に detached_at を立てる.

    立てないと、抜いたポートの行が永久に live のままになり、
    「同一 identity の同時接続」を誤検出して確度が上がらなくなる。
    """
    placeholders = ",".join("?" * len(seen_presence_ids))
    condition = f" AND id NOT IN ({placeholders})" if seen_presence_ids else ""
    cursor = conn.execute(
        "UPDATE volume_presence SET detached_at = ?"  # noqa: S608
        f" WHERE detached_at IS NULL{condition}",
        (now_iso(), *seen_presence_ids),
    )
    return cursor.rowcount
