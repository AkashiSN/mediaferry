"""結合グループと、アップロードの選択肢（§11）.

入力はクエリパラメータで受ける。入力スキーマは Web UI と一緒に足す。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..core.merge.digest import input_digest
from ..core.merge.output import MergeOutputUndefined, merged_rel_path
from ..db.jobs import JobStore
from ..db.merges import GroupNotClaimable, GroupNotEditable, MergeRepository
from ..db.profiles import ProfileRegistry, UnknownProfile
from ..db.selection import DEFAULT_LIMIT, SelectionService
from ..jobs.detect_groups import GroupDetector
from .deps import conn as get_conn
from .errors import ApiError, ErrorCode

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
def patch_group(  # noqa: ANN201
    group_id: str,
    action: str,
    body: dict[str, Any] = Body(default={}),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
):
    """採用・破棄・構成変更（§13）.

    **構成変更は「新しいグループを作って旧を supersede」**。公開済みの
    `media_file` を取り残すので、消さずに向け直す（§3）。
    """
    repo = MergeRepository(conn)
    _found(repo, group_id)
    if action == ADOPT:
        try:
            repo.adopt(group_id)
        except GroupNotClaimable as exc:
            raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
        return {"status": "ok"}
    if action == "discard":
        _edited(repo.discard, group_id)
        return {"status": "ok"}
    if action == "regroup":
        media_ids = body.get("media_ids")
        if not isinstance(media_ids, list) or not media_ids:
            raise ApiError(400, ErrorCode.MISSING_FIELD, "media_ids が要る")
        digest = _digest_of(conn, media_ids)
        new_id = _edited(repo.supersede, group_id, media_ids, digest)
        return {"status": "ok", "group_id": new_id}
    raise ApiError(400, ErrorCode.UNKNOWN_ACTION, "知らない操作", {"action": action})


@router.post("/merge-groups")
def create_group(
    body: dict[str, Any] = Body(default={}),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, str]:
    """手動でグループを作る（検出が拾えなかった並びを人が組む）."""
    media_ids = body.get("media_ids")
    if not isinstance(media_ids, list) or len(media_ids) < 2:
        raise ApiError(400, ErrorCode.MISSING_FIELD, "media_ids は 2 件以上が要る")
    repo = MergeRepository(conn)
    try:
        group_id = repo.create_manual(media_ids, _digest_of(conn, media_ids))
    except GroupNotEditable as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    return {"status": "ok", "group_id": group_id}


def _edited(operation, *args: object):  # noqa: ANN001, ANN202
    """編集の共通の断り方（**送信中は動かさない**）."""
    try:
        return operation(*args)
    except GroupNotEditable as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc


def _digest_of(conn, media_ids: list[str]) -> str:  # noqa: ANN001
    """構成から入力の指紋を作る（§8 の `input_digest` と同じ定義）.

    **手で組んだグループも同じ digest の定義を使う。** 別の作り方にすると、
    同じ構成でも検出と手動で違う指紋になり、二重に候補が出る。
    """
    placeholders = ",".join("?" * len(media_ids))
    rows = conn.execute(
        "SELECT id, sha1, profile_id, profile_revision_id FROM media_file"  # noqa: S608
        f" WHERE id IN ({placeholders})",
        media_ids,
    ).fetchall()
    found = {row["id"]: row for row in rows}
    missing = [media_id for media_id in media_ids if media_id not in found]
    if missing:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い", {"media_ids": missing})
    first = found[media_ids[0]]
    profile = ProfileRegistry(conn).by_id(first["profile_id"])
    return input_digest(
        [(media_id, found[media_id]["sha1"]) for media_id in media_ids],
        profile.definition.merge,
        first["profile_revision_id"],
    )


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
        raise ApiError(
            404, ErrorCode.NOT_FOUND, "そのプロファイルは無い", {"profile": profile_slug}
        ) from exc


def _found(repo: MergeRepository, group_id: str):  # noqa: ANN202
    row = repo.get(group_id)
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのグループは無い")
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
