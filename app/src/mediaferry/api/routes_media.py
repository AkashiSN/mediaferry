"""ライブラリの一覧と、reconciliation が見つけた齟齬."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from ..adapters.thumbnails import ThumbnailFailed, quantise
from ..core.listing import DEFAULT_PAGE_SIZE, escape_like, page_bounds
from ..db.media import MediaRepository
from ..db.merges import GroupNotEditable
from ..db.selection import SENDABLE_CLAUSE
from .deps import conn as get_conn
from .deps import state as get_state
from .errors import ApiError, ErrorCode

router = APIRouter()


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
        # **値の検査はしない。** 知らない値は 0 件になるだけで、`kind` と同じ扱い。
        clauses.append("m.role = ?")
        params.append(role)
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
def get_media(media_id: str, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い")
    return _media(row)


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
