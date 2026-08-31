"""アップロードのレコード（§11）."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from ..adapters.immich import ImmichClient
from ..clock import now_iso
from ..core.uploads.decisions import datetime_plan, instant, same_instant
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
from .errors import ApiError, ErrorCode

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
        raise ApiError(400, ErrorCode.BAD_REQUEST, str(exc)) from exc
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
    stack_state: str | None = None,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    try:
        rows = _uploads(conn, box).list_records(destination_id, state, limit, stack_state)
    except UploadRequestInvalid as exc:
        raise ApiError(400, ErrorCode.BAD_REQUEST, str(exc)) from exc
    # カメラの種類は**行ごとに引き直さない**（1 度に 200 件出す画面がある）。
    profiles = _ProfileCache(ProfileRegistry(conn))
    return {"records": [_view(row, profiles) for row in rows]}


@router.post("/uploads/{record_id}/retry")
def retry_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """`failed` → `pending` の明示操作. **`selection_rule` は変えない**（§8）."""
    uploads = _uploads(conn, box)
    if uploads.get(record_id) is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのレコードは無い")
    with immediate(conn):
        updated = conn.execute(
            "UPDATE upload_record SET state = 'pending', claim_job_id = NULL,"
            " claim_token = NULL, claim_expires_at = NULL, updated_at = ?,"
            # **送り直す前に前回の結果を捨てる**（§9.11）。残すと第 2 パスが
            # 拾わず、新しい資産で組み直せない。
            " stack_state = NULL, remote_stack_id = NULL, stack_reason = NULL"
            " WHERE id = ? AND state = 'failed' AND invalidated_at IS NULL",
            (now_iso(), record_id),
        )
    if updated.rowcount != 1:
        raise ApiError(409, ErrorCode.NOT_RETRYABLE, "失敗した状態ではないので再試行できない")
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
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのレコードは無い")
    if row["state"] != "awaiting_datetime_approval":
        raise ApiError(
            409, ErrorCode.NOT_AWAITING_APPROVAL, "承認待ちではない", {"state": row["state"]}
        )
    if row["invalidated_at"] is not None:
        raise ApiError(409, ErrorCode.ALREADY_INVALIDATED, "無効化されている")
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
            raise ApiError(409, ErrorCode.APPROVAL_ALREADY_QUEUED, "この承認は既に実行待ち")
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
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    return {"status": "ok"}


# ----------------------------------------------------------------------
def _uploads(conn, box) -> UploadRepository:  # noqa: ANN001
    destinations = DestinationRepository(conn, CredentialStore(conn, box))
    return UploadRepository(conn, ProfileRegistry(conn), destinations)


class _ProfileCache:
    """1 応答の間だけ、カメラの種類を覚えておく（**同じものを何度も引かない**）."""

    def __init__(self, registry: ProfileRegistry) -> None:
        self._registry = registry
        self._known: dict[str, Any] = {}

    def by_id(self, profile_id: str):  # noqa: ANN202
        if profile_id not in self._known:
            self._known[profile_id] = self._registry.by_id(profile_id)
        return self._known[profile_id]


def _view(row, profiles: _ProfileCache | None = None) -> dict[str, Any]:  # noqa: ANN001
    """記録 1 件の表示. 承認待ちには**差分**を添える（§13）.

    **現在値はその場で取りに行かない。** 一覧の描画で N 件ぶんの HTTP を出すことに
    なる。保存済みの観測（`remote_checked_at` の時点の値）を返し、画面が
    「いつ時点か」を示す。最新が要るなら宛先の再確認を回す。
    """
    view = {
        "id": row["id"],
        "destination_id": row["destination_id"],
        "media_file_id": row["media_file_id"],
        # 画面は内部の ID を出さない（§13）。**一覧はファイルの位置も持って返る**
        # （`list_records` が `media_file` を継いで必ず添える）。
        "rel_path": row["rel_path"],
        "state": row["state"],
        "selection_rule": row["selection_rule"],
        "origin": row["origin"],
        "remote_asset_id": row["remote_asset_id"],
        "remote_is_trashed": bool(row["remote_is_trashed"]),
        "remote_checked_at": row["remote_checked_at"],
        # スタックの結果（§9.11）。未評価は `null`。
        "stack_state": row["stack_state"],
        "remote_stack_id": row["remote_stack_id"],
        "stack_reason": row["stack_reason"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "eligibility_reason": row["eligibility_reason"],
        "invalidated_at": row["invalidated_at"],
        "invalidated_reason": row["invalidated_reason"],
        "updated_at": row["updated_at"],
    }
    if row["state"] == "awaiting_datetime_approval":
        view.update(_datetime_diff(row, profiles))
    return view


def _datetime_diff(row, profiles: _ProfileCache | None) -> dict[str, Any]:  # noqa: ANN001
    """承認待ちの差分（現在値・補正案・同じかどうか）.

    **`media_file` は引き直さない。** 一覧が撮影時刻とカメラの種類を継いで返すので
    （`db/uploads.list_records`）、行ごとの問い合わせは要らない。
    """
    proposed = None
    if profiles is not None:
        profile = profiles.by_id(row["media_profile_id"])
        plan = datetime_plan(
            profile.definition.immich,
            profile.definition.timestamp.timezone_policy,
            row["media_captured_at"],
            row["origin"],
        )
        proposed = plan.proposed
    current = row["remote_datetime_original"]
    current_at = instant(current)
    proposed_at = instant(proposed)
    return {
        # **提案と同じオフセットに直して返す。** 画面は文字列から壁時計を切り出す
        # だけなので（`web/src/utils/formatDateTime.ts`）、片方が UTC のままだと
        # 同じ瞬間が 9 時間ずれた 2 つの時刻として並ぶ。**直せないものはそのまま**
        # （オフセットが無い値に「たぶん現地時刻だろう」と補わない）。
        "remote_current": (
            current_at.astimezone(proposed_at.tzinfo).isoformat()
            if current_at is not None and proposed_at is not None
            else current
        ),
        "proposed": proposed,
        # 画面が両側に添える印の出所。**両側とも提案のオフセットで並ぶ**ので、
        # 印も 1 つでよい（空なら画面が `DEFAULT_TIMEZONE` とみなす）。
        "captured_at_tz": row["media_captured_at_tz"],
        # **瞬間で比べる**（`same_instant`）。送信のときに承認を待つかどうかも同じ
        # 判定で決めているので、**ここに 2 本目を書かない** —— 片方だけ直すと、
        # 画面に出る差分と状態機械の判断が食い違う。
        "identical": same_instant(current, proposed),
    }
