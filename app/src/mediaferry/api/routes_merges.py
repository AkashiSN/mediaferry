"""結合グループと、アップロードの選択肢（§11）.

入力はクエリパラメータで受ける。入力スキーマは Web UI と一緒に足す。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.merge.output import MergeOutputUndefined, merged_rel_path
from ..db.jobs import JobStore
from ..db.merges import GroupNotClaimable, MergeRepository
from ..db.profiles import ProfileRegistry, UnknownProfile
from ..db.selection import DEFAULT_LIMIT, SelectionService
from ..jobs.detect_groups import GroupDetector
from .deps import conn as get_conn

router = APIRouter()

ADOPT = "adopt"


@router.post("/merge-groups/detect")
def detect(profile_slug: str | None = None, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    """検出ジョブを開始する. **プロファイルごとに 1 本**立てる.

    ジョブはキュー投入時のリビジョンを params に固定して持つ。実行時に
    現行を読み直すと、待っている間の編集で違う規則の検出になる。
    """
    registry = ProfileRegistry(conn)
    profiles = _targets(registry, profile_slug)
    store = JobStore(conn)
    return {
        "jobs": [
            {
                "profile_slug": profile.definition.slug,
                "job_id": store.enqueue(
                    "detect_groups",
                    {
                        "profile_id": profile.profile_id,
                        "profile_revision_id": profile.revision_id,
                    },
                ),
            }
            for profile in profiles
        ]
    }


@router.post("/merge-groups/preview")
def preview(
    profile_slug: str,
    tolerance_seconds: int | None = None,
    min_part_size_gib: int | None = None,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """閾値を変えたときの候補. **保存しない**."""
    registry = ProfileRegistry(conn)
    profile = _profile(registry, profile_slug)
    rule = profile.definition.merge
    if tolerance_seconds is not None:
        rule = replace(rule, tolerance_seconds=tolerance_seconds)
    if min_part_size_gib is not None:
        rule = replace(rule, min_part_size_gib=min_part_size_gib)
    candidates = GroupDetector(conn, MergeRepository(conn)).preview(profile, rule)
    return {
        "candidates": [
            {
                "members": [
                    {"media_file_id": part.media_file_id, "rel_path": part.rel_path}
                    for part in candidate.members
                ],
                "gaps_seconds": list(candidate.gaps),
                "output_rel_path": _output_or_none(profile, rule, candidate),
            }
            for candidate in candidates
        ]
    }


@router.get("/merge-groups")
def list_groups(
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    repo = MergeRepository(conn)
    return {"groups": [_group(repo, row) for row in repo.list_groups(status, limit, offset)]}


@router.get("/merge-groups/{group_id}")
def get_group(group_id: str, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    repo = MergeRepository(conn)
    return _group(repo, _found(repo, group_id))


@router.post("/merge-groups/{group_id}/merge")
def start_merge(group_id: str, conn=Depends(get_conn)) -> dict[str, str]:  # noqa: ANN001, B008
    """結合ジョブを開始する.

    キュー投入時の `input_digest` とプロファイルリビジョンを params に固定する。
    実行時に構成が変わっていれば、ジョブは 1 バイトも読まずに止まる。
    """
    repo = MergeRepository(conn)
    row = _found(repo, group_id)
    return {
        "job_id": JobStore(conn).enqueue(
            "merge",
            {
                "merge_group_id": group_id,
                "input_digest": row["input_digest"],
                "profile_id": row["profile_id"],
                "profile_revision_id": row["profile_revision_id"],
            },
        )
    }


@router.patch("/merge-groups/{group_id}")
def patch_group(group_id: str, action: str, conn=Depends(get_conn)) -> dict[str, str]:  # noqa: ANN001, B008
    """公開後にできる操作は採用だけ.

    破棄と再結合は公開済みの `media_file` を取り残すので、supersede が入る
    Phase 4 で足す。
    """
    repo = MergeRepository(conn)
    _found(repo, group_id)
    if action != ADOPT:
        raise HTTPException(status_code=400, detail=f"知らない操作: {action}")
    try:
        repo.adopt(group_id)
    except GroupNotClaimable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok"}


@router.get("/uploads/selectable")
def selectable(
    include: list[str] = Query(default=[]),  # noqa: B008
    limit: int = DEFAULT_LIMIT,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    service = SelectionService(conn, ProfileRegistry(conn))
    items = service.selectable(include, limit)
    return {
        # 上限で切れたかを応答で示す。黙って一部だけ返さない。
        "truncated": len(items) == limit,
        "selectable": [
            {
                "media_file_id": item.media_file_id,
                "rel_path": item.rel_path,
                "role": item.role,
                "reason": item.reason,
                "merge_group_id": item.merge_group_id,
            }
            for item in items
        ],
    }


# ----------------------------------------------------------------------
def _targets(registry: ProfileRegistry, profile_slug: str | None) -> list:
    if profile_slug is not None:
        return [_profile(registry, profile_slug)]
    return [profile for profile in registry.active() if profile.definition.merge.enabled]


def _profile(registry: ProfileRegistry, profile_slug: str):  # noqa: ANN202
    try:
        return registry.current(profile_slug)
    except UnknownProfile as exc:
        raise HTTPException(
            status_code=404, detail=f"そのプロファイルは無い: {profile_slug}"
        ) from exc


def _found(repo: MergeRepository, group_id: str):  # noqa: ANN202
    row = repo.get(group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="そのグループは無い")
    return row


def _output_or_none(profile, rule, candidate) -> str | None:  # noqa: ANN001
    try:
        return merged_rel_path(profile.definition.slug, rule, candidate.members)
    except MergeOutputUndefined:
        return None


def _group(repo: MergeRepository, row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "status": row["status"],
        "detected_by": row["detected_by"],
        "input_digest": row["input_digest"],
        "output_media_file_id": row["output_media_file_id"],
        "adopted_at": row["adopted_at"],
        "superseded_by_id": row["superseded_by_id"],
        "tool_version": row["tool_version"],
        "error": row["error"],
        "verification": (
            None if row["verification_json"] is None else json.loads(row["verification_json"])
        ),
        "members": [
            {
                "position": member["position"],
                "media_file_id": member["media_file_id"],
                "rel_path": member["rel_path"],
                "size_bytes": member["size_bytes"],
                "duration_seconds": member["duration_seconds"],
                "captured_at": member["captured_at"],
                "missing_at": member["missing_at"],
            }
            for member in repo.members(row["id"])
        ],
    }
