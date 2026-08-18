"""ライブラリの一覧と、reconciliation が見つけた齟齬."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from ..adapters.thumbnails import ThumbnailCache, ThumbnailFailed, quantise
from .deps import conn as get_conn
from .deps import state as get_state
from .errors import ApiError, ErrorCode

router = APIRouter()


@router.get("/media")
def list_media(limit: int = 200, offset: int = 0, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    rows = conn.execute(
        "SELECT * FROM media_file ORDER BY captured_at DESC LIMIT ? OFFSET ?", (limit, offset)
    )
    return {"media": [_media(row) for row in rows]}


@router.get("/media/{media_id}")
def get_media(media_id: str, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い")
    return _media(row)


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
    cache = ThumbnailCache(state.settings.data_root)
    try:
        path = cache.get_or_create(media_id, state.settings.data_root / row["rel_path"], position)
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
