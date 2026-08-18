"""ライブラリの一覧と、reconciliation が見つけた齟齬."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

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
