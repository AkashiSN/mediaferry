"""アップロードのレコード（§11）."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..adapters.immich import ImmichClient
from ..clock import now_iso
from ..db.connection import immediate
from ..db.credentials import CredentialStore
from ..db.destinations import DestinationRepository
from ..db.jobs import JobStore
from ..db.profiles import ProfileRegistry
from ..db.uploads import UploadRepository, UploadRequestInvalid
from ..jobs.approvals import ApprovalNotPossible, ApprovalService
from ..jobs.preflight import PreflightCache
from .deps import conn as get_conn
from .deps import secret_box as get_box

router = APIRouter()


@router.post("/uploads")
def create_uploads(
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """`media_ids × destination_ids` を pair に展開する（§10）."""
    try:
        pairs = _uploads(conn, box).create_pairs(
            body.get("media_ids", []), body.get("destination_ids", [])
        )
    except UploadRequestInvalid as exc:
        # 何も作らずに全体を拒否する。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "pairs": [
            {
                "media_file_id": pair.media_file_id,
                "destination_id": pair.destination_id,
                "result": pair.result,
                "upload_record_id": pair.record_id,
                "reason": pair.reason,
            }
            for pair in pairs
        ]
    }


@router.get("/uploads")
def list_uploads(
    destination_id: str | None = None,
    state: str | None = None,
    limit: int = 200,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    rows = _uploads(conn, box).list_records(destination_id, state, limit)
    return {"records": [_view(row) for row in rows]}


@router.post("/uploads/{record_id}/retry")
def retry_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """`failed` → `pending` の明示操作. **`selection_rule` は変えない**（§8）."""
    uploads = _uploads(conn, box)
    if uploads.get(record_id) is None:
        raise HTTPException(status_code=404, detail="そのレコードは無い")
    with immediate(conn):
        updated = conn.execute(
            "UPDATE upload_record SET state = 'pending', claim_job_id = NULL,"
            " claim_token = NULL, claim_expires_at = NULL, updated_at = ?"
            " WHERE id = ? AND state = 'failed' AND invalidated_at IS NULL",
            (now_iso(), record_id),
        )
    if updated.rowcount != 1:
        raise HTTPException(status_code=409, detail="失敗した状態ではないので再試行できない")
    return {"status": "ok"}


@router.post("/uploads/{record_id}/requeue")
def requeue_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """リモートから消えた資産を、利用者の明示操作で送り直す（§9.10）.

    **自動では戻さない。** 対象は「再確認でサーバに無いと分かった `complete`」
    （`remote_asset_id IS NULL` かつ `remote_checked_at IS NOT NULL`）だけ。
    通常の `complete` は拒否する。
    """
    uploads = _uploads(conn, box)
    row = uploads.get(record_id)
    if row is None:
        raise HTTPException(status_code=404, detail="そのレコードは無い")
    reason = uploads.check_eligibility(row)
    if reason is not None:
        raise HTTPException(status_code=409, detail=f"送り直せない: {reason}")
    with immediate(conn):
        updated = conn.execute(
            "UPDATE upload_record SET state = 'pending', remote_is_trashed = NULL,"
            " updated_at = ? WHERE id = ? AND state = 'complete'"
            "   AND remote_asset_id IS NULL AND remote_checked_at IS NOT NULL"
            "   AND invalidated_at IS NULL",
            (now_iso(), record_id),
        )
    if updated.rowcount != 1:
        raise HTTPException(
            status_code=409, detail="リモートに存在しないと確認できたレコードだけ送り直せる"
        )
    return {"status": "ok"}


@router.post("/uploads/{record_id}/approve")
def approve_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """**承認はジョブとして実行する**（外部への副作用に所有権が要る。Task 11）."""
    uploads = _uploads(conn, box)
    row = uploads.get(record_id)
    if row is None:
        raise HTTPException(status_code=404, detail="そのレコードは無い")
    if row["state"] != "awaiting_datetime_approval":
        raise HTTPException(status_code=409, detail=f"承認待ちではない（{row['state']}）")
    if row["invalidated_at"] is not None:
        raise HTTPException(status_code=409, detail="無効化されている")
    with immediate(conn):
        # **同じレコードの承認ジョブを二重に積まない。** 積めてしまうと、
        # 1 本目が終わった後の残りが軒並み失敗として画面に並ぶ。
        active = conn.execute(
            "SELECT 1 FROM job WHERE type = 'upload'"
            "   AND status IN ('queued', 'running', 'cancelling')"
            "   AND params_json LIKE ?",
            (f'%"upload_record_id": "{record_id}"%',),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="この承認は既に実行待ち")
        job_id = JobStore(conn).enqueue(
            "upload",
            {
                "destination_id": row["destination_id"],
                "mode": "approve",
                "upload_record_id": record_id,
            },
        )
    return {"job_id": job_id}


@router.post("/uploads/{record_id}/reject")
def reject_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """**却下はリモートに触らない**ので同期で終える（Task 11）."""
    destinations = DestinationRepository(conn, CredentialStore(conn, box))
    service = ApprovalService(
        conn,
        _uploads(conn, box),
        destinations,
        ProfileRegistry(conn),
        lambda revision: ImmichClient(revision["base_url"], destinations.secret_of(revision["id"])),
        PreflightCache(
            destinations,
            lambda revision: ImmichClient(
                revision["base_url"], destinations.secret_of(revision["id"])
            ),
        ),
    )
    try:
        service.reject(record_id)
    except ApprovalNotPossible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok"}


# ----------------------------------------------------------------------
def _uploads(conn, box) -> UploadRepository:  # noqa: ANN001
    destinations = DestinationRepository(conn, CredentialStore(conn, box))
    return UploadRepository(conn, ProfileRegistry(conn), destinations)


def _view(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "destination_id": row["destination_id"],
        "media_file_id": row["media_file_id"],
        "state": row["state"],
        "selection_rule": row["selection_rule"],
        "origin": row["origin"],
        "remote_asset_id": row["remote_asset_id"],
        "remote_is_trashed": bool(row["remote_is_trashed"]),
        "remote_checked_at": row["remote_checked_at"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "eligibility_reason": row["eligibility_reason"],
        "invalidated_at": row["invalidated_at"],
        "invalidated_reason": row["invalidated_reason"],
        "updated_at": row["updated_at"],
    }
