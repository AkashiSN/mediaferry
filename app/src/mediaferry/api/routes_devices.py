"""接続中デバイスとボリュームの操作."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..db.jobs import JobStore
from ..jobs.volumes import StaleSelection, VolumeBusy
from .deps import conn as get_conn
from .deps import state as get_state

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
            }
            for view in state.volumes.refresh()
        ]
    }


@router.post("/volumes/{volume_instance_id}/trust")
def trust(volume_instance_id: str, state=Depends(get_state)) -> dict[str, str]:  # noqa: ANN001, B008
    state.volumes.trust(volume_instance_id)
    return {"status": "ok"}


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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JobStore(conn).enqueue(job_type, selection.to_params())


@router.post("/volumes/{volume_instance_id}/close")
def close(volume_instance_id: str, state=Depends(get_state)) -> dict[str, str]:  # noqa: ANN001, B008
    try:
        state.volumes.close(volume_instance_id)
    except VolumeBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}
