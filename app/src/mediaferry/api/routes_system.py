"""ヘルス・設定・プロファイル・ジョブ."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..db.jobs import JobStore
from ..db.profiles import ProfileRegistry
from ..settings import SettingInvalid, SettingLocked, SettingsService, startup_warnings
from .deps import conn as get_conn
from .deps import state as get_state
from .errors import ApiError, ErrorCode

router = APIRouter()
# **`/health` だけは認証を掛けない**（監視と compose の healthcheck が叩く）。
public_router = APIRouter()


@public_router.get("/health")
def health(conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    version = conn.execute("SELECT MAX(version) AS v FROM schema_migration").fetchone()["v"]
    return {"status": "ok", "schema_version": version}


@router.get("/settings")
def list_settings(state=Depends(get_state), conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    settings = SettingsService(conn, state.env).snapshot()
    return {
        "warnings": [
            {"code": warning.code, "message": warning.message}
            for warning in startup_warnings(settings)
        ],
        "settings": [
            {
                "key": s.key,
                "value": s.value,
                "source": s.source,
                "locked": s.locked,
                "tier": s.tier.value,
                "writable": s.writable,
            }
            for s in SettingsService(conn, state.env).describe_all()
        ],
    }


@router.put("/settings")
def write_setting(
    body: dict[str, str],
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, str]:
    try:
        tier = SettingsService(conn, state.env).set(body["key"], body["value"])
    except SettingLocked as exc:
        raise ApiError(409, ErrorCode.SETTING_LOCKED, str(exc)) from exc
    except (SettingInvalid, KeyError) as exc:
        raise ApiError(400, ErrorCode.BAD_REQUEST, str(exc)) from exc
    # いつ効くかを返す。RESTART の値を変えて「反映されない」と見えるのを防ぐ。
    return {"status": "ok", "applies": tier.value}


@router.get("/profiles")
def list_profiles(conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {
        "profiles": [
            {"slug": ref.definition.slug, "name": ref.definition.name, "revision": ref.revision}
            for ref in ProfileRegistry(conn).active()
        ]
    }


@router.get("/jobs")
def list_jobs(conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {"jobs": [_job(row) for row in JobStore(conn).list_jobs()]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    row = JobStore(conn).get(job_id)
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのジョブは無い")
    return _job(row)


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, after_seq: int = 0, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    return {
        "events": [
            {"seq": e["seq"], "level": e["level"], "message": e["message"], "at": e["at"]}
            for e in JobStore(conn).events(job_id, after_seq)
        ]
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, conn=Depends(get_conn)) -> dict[str, str]:  # noqa: ANN001, B008
    if not JobStore(conn).request_cancel(job_id):
        raise ApiError(409, ErrorCode.JOB_ALREADY_FINISHED, "そのジョブはもう終わっている")
    return {"status": "cancelling"}


def _job(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "error": row["error"],
    }
