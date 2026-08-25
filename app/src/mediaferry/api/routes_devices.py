"""接続中デバイスとボリュームの操作."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..db.jobs import JobStore
from ..db.sources import pending_contents
from ..jobs.volumes import StaleSelection, VolumeBusy
from .deps import conn as get_conn
from .deps import state as get_state
from .errors import ApiError, ErrorCode

router = APIRouter()


@router.get("/devices")
def list_devices(state=Depends(get_state)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {
        "volumes": [
            {
                "volume_instance_id": view.volume_instance_id,
                "volume_key": view.volume_key,
                "fs_label": view.fs_label,
                "size_bytes": view.size_bytes,
                "profile_slug": view.profile_slug,
                "identity_confidence": view.identity_confidence,
                "provisional": view.provisional,
                "trusted": view.trusted,
                "reason": view.reason,
                "pending_count": view.pending_count,
                "scanned_at": view.scanned_at,
                "busy": view.busy,
            }
            for view in state.volumes.refresh()
        ]
    }


@router.post("/volumes/{volume_instance_id}/trust")
def trust(volume_instance_id: str, state=Depends(get_state)) -> dict[str, str]:  # noqa: ANN001, B008
    state.volumes.trust(volume_instance_id)
    return {"status": "ok"}


# **1 度に返す上限。** 数万件のカードで全件を返さない（`MANIFEST_LIMIT` と同じ
# 考え方）。切ったことは `truncated` で言う —— 黙って切ると、画面は出た分が
# 全部だと読む（裁定 20）。
CONTENTS_LIMIT = 200


@router.get("/volumes/{volume_instance_id}/contents")
def contents(
    volume_instance_id: str,
    limit: int = CONTENTS_LIMIT,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """カード 1 枚の中身（取り込み待ちのファイル）.

    **ボリュームの総容量とは別の欄で返す。** 画面に数字が 1 つしか無いと、
    それが写真のサイズなのかカードのサイズなのか読めない（R8）。

    **出せる時刻はカード上の `mtime_ns` だけ。** 撮影時刻は取り込んで probe を
    通したあとにしか決まらないので、この経路では返さない。
    """
    known = conn.execute(
        "SELECT 1 FROM volume_instance WHERE id = ?", (volume_instance_id,)
    ).fetchone()
    if known is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのカードは無い")
    rows, count, total_bytes = pending_contents(conn, volume_instance_id, limit)
    return {
        "entries": [
            {
                "rel_path": row["rel_path"],
                "size_bytes": row["size_bytes"],
                "mtime_ns": row["mtime_ns"],
            }
            for row in rows
        ],
        "pending_count": count,
        "pending_bytes": total_bytes,
        "truncated": count > len(rows),
    }


@router.post("/volumes/{volume_instance_id}/scan")
def scan(
    volume_instance_id: str,
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, str]:
    return {"job_id": _enqueue(state, conn, "scan", volume_instance_id)}


@router.post("/volumes/{volume_instance_id}/import")
def start_import(
    volume_instance_id: str,
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, str]:
    return {"job_id": _enqueue(state, conn, "import", volume_instance_id)}


def _enqueue(state, conn, job_type: str, volume_instance_id: str) -> str:  # noqa: ANN001
    """選択した瞬間の presence とプロファイルリビジョンを params に固定する.

    volume_instance_id だけを渡すと、実行時に「最新の presence」を選ぶことに
    なり、抜き差しで別のカードが同じノードに来ていても、その現在値から
    正しい expect を組み立ててブローカーの TOCTOU 検証をすり抜ける（§9.2）。
    """
    try:
        selection = state.volumes.selection_for(volume_instance_id)
    except StaleSelection as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    return JobStore(conn).enqueue(job_type, selection.to_params())


@router.post("/volumes/{volume_instance_id}/close")
def close(volume_instance_id: str, state=Depends(get_state)) -> dict[str, str]:  # noqa: ANN001, B008
    try:
        state.volumes.close(volume_instance_id)
    except VolumeBusy as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    except LookupError as exc:
        raise ApiError(404, ErrorCode.NOT_FOUND, str(exc)) from exc
    return {"status": "ok"}
