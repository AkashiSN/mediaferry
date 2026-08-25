"""ライブラリの一覧と、reconciliation が見つけた齟齬."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from ..adapters.thumbnails import ThumbnailFailed, quantise
from ..core.listing import DEFAULT_PAGE_SIZE, escape_like, page_bounds
from ..db.media import IN_FLIGHT_STATES, MediaRepository
from ..db.merges import GroupNotEditable
from ..db.selection import SENDABLE_CLAUSE
from .deps import conn as get_conn
from .deps import state as get_state
from .errors import ApiError, ErrorCode

router = APIRouter()

# `media_file.role` の CHECK 制約が許す値そのもの（`db/migrations/0003_*.sql`）。
# `_filters` の role 節がこの外の値をリテラルで埋めないために使う。
_KNOWN_ROLES = frozenset({"original", "derived"})


@router.get("/media")
def list_media(  # noqa: PLR0913
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    kind: str | None = None,
    role: str | None = None,
    profile: str | None = None,
    captured_from: str | None = None,
    captured_to: str | None = None,
    q: str | None = None,
    destination_id: str | None = None,
    status: str | None = None,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """ライブラリの一覧（§11）.

    **並びは `captured_at DESC, id DESC` で固定する。** 同じ撮影日時の行があるので、
    tie-break を入れないとページの境目で重複・欠落する。

    `status` は**宛先ごとの状態**なので、`destination_id` と併せて指定する。
    `role=derived` で写真タブの「つないだ動画」だけに絞れる。
    """
    where, params = _filters(
        kind, role, profile, captured_from, captured_to, q, destination_id, status
    )
    limit, offset = page_bounds(page, page_size)
    total = conn.execute(f"SELECT count(*) AS n FROM media_file m {where}", params).fetchone()["n"]  # noqa: S608
    rows = conn.execute(
        f"SELECT m.* FROM media_file m {where}"  # noqa: S608
        " ORDER BY m.captured_at DESC, m.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return {
        "media": [_media(row) for row in rows],
        "total": total,
        "page": max(1, page),
        "page_size": limit,
    }


def _filters(  # noqa: PLR0913
    kind: str | None,
    role: str | None,
    profile: str | None,
    captured_from: str | None,
    captured_to: str | None,
    q: str | None,
    destination_id: str | None,
    status: str | None,
) -> tuple[str, tuple[Any, ...]]:
    """WHERE 節と引数を組み立てる. **文字列を連結して値を埋めない。**"""
    clauses: list[str] = []
    params: list[Any] = []
    if kind is not None:
        clauses.append("m.kind = ?")
        params.append(kind)
    if role is not None:
        # **既知の 2 値だけリテラルで埋める。** `_KNOWN_ROLES` に無い値（利用者が
        # 送った任意の文字列を含む）は SQL へ触れさせず、常に 0 件になる節にする
        # —— `f"m.role = '{role}'"` へそのまま渡すと文字列連結になってしまう。
        # 既知の 2 値はバインド変数ではなくリテラルで埋める（`_status_clause` の
        # `known[status]` と同じ作法）。バインド変数のままだと SQLite が prepare
        # 時に `role = 'derived'` を証明できず、`0023` の部分索引
        # （`WHERE role = 'derived'`）が選ばれる保証が無い。
        if role in _KNOWN_ROLES:
            clauses.append(f"m.role = '{role}'")  # noqa: S608 - 語彙は上で固定
        else:
            # 知らない値（＝ CHECK 制約の外）は、そもそも 1 行も一致しない。
            clauses.append("0")
    if profile is not None:
        # **`IN` ではなく `=` で書く。** `IN` だと SQLite は複数の値を取りうると
        # 見なして、索引があっても並べ替えを外せない（`0014` が効かず、その
        # プロファイルの全行を拾ってから並べ替える）。slug は UNIQUE なので
        # 値は高々 1 つで、意味は変わらない（無ければ NULL 比較で 0 件）。
        clauses.append("m.profile_id = (SELECT id FROM device_profile WHERE slug = ?)")
        params.append(profile)
    if captured_from is not None:
        clauses.append("m.captured_at >= ?")
        params.append(captured_from)
    if captured_to is not None:
        clauses.append("m.captured_at <= ?")
        params.append(captured_to)
    if q is not None:
        # 保存先の名前で探す（カード上の原名は列に持っていない）。
        clauses.append("m.rel_path LIKE ? ESCAPE '\\'")
        params.append(f"%{escape_like(q)}%")
    if status is not None:
        if destination_id is None:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "status は destination_id と一緒に指定する")
        clauses.append(_status_clause(status))
        params.append(destination_id)
    elif destination_id is not None:
        clauses.append("1 = 1 AND ? IS NOT NULL")
        params.append(destination_id)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", tuple(params)


def _status_clause(status: str) -> str:
    """宛先ごとの状態。**無効化された記録は数えない**（§10）."""
    existing = (
        "SELECT 1 FROM upload_record u WHERE u.media_file_id = m.id"
        " AND u.destination_id = ? AND u.invalidated_at IS NULL"
    )
    if status == "unsent":
        # **「まだ送っていない」＝ この宛先の有効な記録がまだ無く、いま送れるもの。**
        # `failed` は再試行という別の操作、`pending` は既に積んである。
        # **積んだまま claim されない `pending` はここに出てこない。** `/dashboard` の
        # 宛先ごとの `pending` 件数で別に見せる（`status=pending` でも個別に絞れる）。
        return f"NOT EXISTS ({existing}) AND {SENDABLE_CLAUSE}"  # noqa: S608 - 定数のみ
    known = {
        "sent": "complete",
        "failed": "failed",
        "awaiting": "awaiting_datetime_approval",
        "pending": "pending",
    }
    if status not in known:
        raise ApiError(400, ErrorCode.BAD_REQUEST, "知らない status", {"status": status})
    return f"EXISTS ({existing} AND u.state = '{known[status]}')"  # noqa: S608 - 語彙は上で固定


# **`/media/{media_id}` より前に置く。** 後ろだと `media_id = "stale-derived"` として
# 飲まれ、404 になる（この案件で何度も出た「並びの順で API が飲まれる」）。
@router.get("/media/stale-derived")
def list_stale_derived(
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """もう使われていない派生物（やり直しの後片付けの対象）.

    置き換えられたグループは `GET /merge-groups` に出ないので、その「できた
    ファイル」はここからしか辿れない。
    """
    repo = MediaRepository(conn, state.settings.data_root)
    return {"stale": repo.list_stale_derived()}


@router.get("/media/{media_id}")
def get_media(  # noqa: ANN201
    media_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
):
    """1 件のくわしく（§13 の「くわしく」画面）.

    **画面が要るものを 1 本で返す。** 複数の API を継ぎ足すと、片方だけ古い状態が出る。
    """
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い")
    repo = MediaRepository(conn, state.settings.data_root)
    blocker = repo.deletion_blocker(media_id)
    return {
        **_media(row),
        "sources": _sources(conn, media_id),
        "destinations": _destinations(conn, media_id),
        "deletable": blocker is None,
        "delete_blocked_reason": blocker,
    }


def _sources(conn, media_id: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """この 1 件の元になったファイル. **`position` 順**（つないだ順）."""
    rows = conn.execute(
        "SELECT mm.position, m.id, m.rel_path, m.missing_at"
        " FROM merge_group g JOIN merge_member mm ON mm.merge_group_id = g.id"
        " JOIN media_file m ON m.id = mm.media_file_id"
        " WHERE g.output_media_file_id = ? ORDER BY mm.position",
        (media_id,),
    )
    return [
        {
            "media_file_id": row["id"],
            "rel_path": row["rel_path"],
            "position": row["position"],
            "missing": row["missing_at"] is not None,
        }
        for row in rows
    ]


# **「生きている」順に並べた presence の優先度.** 同じ宛先に複数の有効な記録が
# 残ることがある —— 向き先が変わって `target_epoch` が進んでも、`complete` は
# 履歴として invalidate されない（`db/destinations.py` の
# `_invalidate_old_epoch_locked`）。`_destinations` はこの並びで 1 宛先 1 行に畳む。
# **`target_epoch` だけに絞って現行分だけを出さない。** `deletion_blocker` は
# epoch を区別せず有効な記録を全部見るので、現行 epoch だけを出す画面は
# 「旧 epoch に `present` な資産が残っていて消せない」を説明できなくなる。
# 優先度で畳めば、画面に出る状況と削除の可否が必ず一致する。
_PRESENCE_PRIORITY = ("present", "sending", "trashed", "gone", "unknown", "failed", "not_sent")


def _destinations(conn, media_id: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """宛先ごとの状況. **日本語にはしない** —— 画面が §13 の語彙で訳す.

    **1 宛先 1 行に畳む。** `target_epoch` は API に出さない内部の概念。
    `_PRESENCE_PRIORITY` の優先度で、同じ宛先の有効な記録から最良の 1 件を選ぶ。

    **退役した宛先（`archived_at` あり）は、記録が無ければ出さない。** もう
    送り先ではないので「まだ送っていません」を並べても押しようが無い。ただし
    過去に送った（無効化されていない）記録が残っているなら、履歴として出す。
    """
    rows = conn.execute(
        "SELECT d.id, d.name, d.archived_at, u.id AS upload_id, u.state,"
        "       u.remote_asset_id, u.remote_is_trashed, u.remote_checked_at"
        " FROM upload_destination d"
        " LEFT JOIN upload_record u ON u.destination_id = d.id"
        "   AND u.media_file_id = ? AND u.invalidated_at IS NULL"
        " ORDER BY d.name",
        (media_id,),
    )
    destinations: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        destination_id = row["id"]
        if destination_id not in destinations:
            destinations[destination_id] = {
                "name": row["name"],
                "archived_at": row["archived_at"],
                "records": [],
            }
            order.append(destination_id)
        if row["upload_id"] is not None:
            destinations[destination_id]["records"].append(row)
    result: list[dict[str, Any]] = []
    for destination_id in order:
        info = destinations[destination_id]
        records = info["records"]
        if not records:
            if info["archived_at"] is not None:
                # 記録の無い退役済みの宛先は、押しようの無い行を並べない。
                continue
            result.append(
                {
                    "destination_id": destination_id,
                    "name": info["name"],
                    "state": None,
                    "presence": "not_sent",
                }
            )
            continue
        best = min(records, key=lambda r: _PRESENCE_PRIORITY.index(_presence(r)))
        result.append(
            {
                "destination_id": destination_id,
                "name": info["name"],
                "state": best["state"],
                "presence": _presence(best),
            }
        )
    return result


def _presence(row) -> str:  # noqa: ANN001
    """`deletion_blocker` と同じ判断を、1 行ぶんの語彙にほどく.

    片方を変えたらもう片方も変える（`deletion_blocker` が正）。
    """
    if row["state"] is None:
        return "not_sent"
    if row["state"] in IN_FLIGHT_STATES:
        return "sending"
    if row["remote_asset_id"] is not None:
        return "trashed" if row["remote_is_trashed"] else "present"
    if row["state"] == "complete":
        return "gone" if row["remote_checked_at"] is not None else "unknown"
    return "failed"


@router.delete("/media/{media_id}")
def delete_media(  # noqa: ANN201
    media_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
):
    """**Immich に生きていない `derived` だけ**消す（写真タブの「消す」）.

    元ファイルは消せない。現行のグループの出力なら、グループごと「別々にした」
    にしてから消す。消せない理由は 409 で返す（規則は `deletion_blocker`）。
    """
    repo = MediaRepository(conn, state.settings.data_root)
    try:
        rel_path = repo.delete_derived(media_id)
    except GroupNotEditable as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    return {"status": "ok", "rel_path": rel_path}


@router.get("/media/{media_id}/thumbnail")
def get_thumbnail(  # noqa: ANN201
    media_id: str,
    request: Request,
    at: int = 0,
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
):
    """サムネイルを返す（`at` は秒。刻みに丸める）.

    **同じ絵には同じ札を付ける。** 丸めた後の位置で `ETag` を作るので、
    `at=13` と `at=17` は同じ応答になる。
    """
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い")
    position = quantise(at, row["duration_seconds"])
    etag = f'"{row["sha1"]}-{position}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    try:
        path = state.thumbnails.get_or_create(
            media_id, state.settings.data_root / row["rel_path"], position
        )
    except ThumbnailFailed as exc:
        # 元のファイルが消えている・壊れている。**理由の分かる形で返す。**
        raise ApiError(
            422, ErrorCode.THUMBNAIL_FAILED, "サムネイルを作れなかった", {"at": position}
        ) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"ETag": etag, "Cache-Control": "private, max-age=604800"},
    )


@router.get("/orphans")
def list_orphans(state=Depends(get_state), conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    report = state.last_reconcile
    missing = conn.execute(
        "SELECT id, rel_path, missing_at FROM media_file WHERE missing_at IS NOT NULL"
    )
    return {
        "orphans": [
            {"rel_path": o.rel_path, "size_bytes": o.size_bytes, "sha1": o.sha1}
            for o in report.orphans
        ],
        "unrecoverable": report.unrecoverable,
        "missing": [
            {"id": row["id"], "rel_path": row["rel_path"], "missing_at": row["missing_at"]}
            for row in missing
        ],
    }


def _media(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "role": row["role"],
        "rel_path": row["rel_path"],
        "size_bytes": row["size_bytes"],
        "kind": row["kind"],
        "captured_at": row["captured_at"],
        "captured_at_source": row["captured_at_source"],
        "duration_seconds": row["duration_seconds"],
        "probe_state": row["probe_state"],
        "missing_at": row["missing_at"],
    }
