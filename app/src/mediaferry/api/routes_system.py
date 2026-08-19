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


@router.get("/dashboard")
def dashboard(state=Depends(get_state), conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    """ダッシュボードの集計（§13）.

    **画面ごとに数えさせない。** 宛先が 3 つあると一覧の API を 3 回叩くことになり、
    そのたびに全件を走査する。ここで 1 度にまとめる。
    """
    media_total = conn.execute("SELECT count(*) AS n FROM media_file").fetchone()["n"]
    settings = SettingsService(conn, state.env).snapshot()
    return {
        "media_total": media_total,
        "destinations": [_destination_summary(conn, row) for row in _destinations(conn)],
        "running_jobs": conn.execute(
            "SELECT count(*) AS n FROM job WHERE status IN ('running', 'cancelling')"
        ).fetchone()["n"],
        "recent_imports": [
            {"id": row["id"], "rel_path": row["rel_path"], "captured_at": row["captured_at"]}
            for row in conn.execute(
                "SELECT id, rel_path, captured_at FROM media_file"
                " ORDER BY created_at DESC, id DESC LIMIT 10"
            )
        ],
        "orphans": len(state.last_reconcile.orphans),
        "missing": conn.execute(
            "SELECT count(*) AS n FROM media_file WHERE missing_at IS NOT NULL"
        ).fetchone()["n"],
        "warnings": [
            {"code": warning.code, "message": warning.message}
            for warning in startup_warnings(settings)
        ],
    }


def _destinations(conn) -> list:  # noqa: ANN001
    return list(
        conn.execute(
            "SELECT id, name, enabled FROM upload_destination WHERE archived_at IS NULL"
            " ORDER BY name"
        )
    )


def _destination_summary(conn, row) -> dict[str, Any]:  # noqa: ANN001
    """宛先 1 つぶんの内訳. **無効化された記録は数えない**（§10）."""
    counts = {
        state: conn.execute(
            "SELECT count(*) AS n FROM upload_record"
            " WHERE destination_id = ? AND state = ? AND invalidated_at IS NULL",
            (row["id"], state),
        ).fetchone()["n"]
        for state in ("complete", "failed", "awaiting_datetime_approval", "pending")
    }
    return {
        "destination_id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "complete": counts["complete"],
        "failed": counts["failed"],
        "awaiting_approval": counts["awaiting_datetime_approval"],
        "pending": counts["pending"],
        # **「まだ送っていない」＝ この宛先の記録がまだ無いもの。** 失敗や承認待ちは
        # 既に記録があるので別に数える（画面はそれぞれ違う操作を出す）。
        "unsent": conn.execute(
            "SELECT count(*) AS n FROM media_file m WHERE NOT EXISTS ("
            " SELECT 1 FROM upload_record u WHERE u.media_file_id = m.id"
            "  AND u.destination_id = ? AND u.invalidated_at IS NULL)",
            (row["id"],),
        ).fetchone()["n"],
    }


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


@router.post("/profiles/{profile_slug}/test")
def try_profile(
    profile_slug: str,
    volume_instance_id: str,
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """指定のボリュームに対する判定を試す（§11）.

    **判定そのものはやり直さない。** いまの観測（`refresh` の結果）を読んで、
    そのプロファイルが選ばれたかどうかと理由を返す。プロファイルを直す前後で
    同じものを見られるようにするための窓であって、別の判定器ではない。
    """
    if profile_slug not in {ref.definition.slug for ref in ProfileRegistry(conn).active()}:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのプロファイルは無い", {"slug": profile_slug})
    views = [
        view for view in state.volumes.refresh() if view.volume_instance_id == volume_instance_id
    ]
    if not views:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのボリュームは無い")
    view = views[0]
    return {
        "profile": profile_slug,
        "volume_instance_id": view.volume_instance_id,
        "matched": view.profile_slug == profile_slug,
        "matched_profile": view.profile_slug,
        "reason": view.reason,
        "identity_confidence": view.identity_confidence,
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
