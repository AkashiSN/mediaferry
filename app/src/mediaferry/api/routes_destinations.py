"""転送先プロファイル（§11 / §12.3）.

**API キーは本文で受け取るだけ。応答には決して出さない。** 読み出しの API も
作らない。接続の検証に成功した設定だけを保存する。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from ..adapters.immich import ImmichClient, ImmichError
from ..core.destinations.urls import EndpointRejected, normalize_endpoint
from ..db.credentials import CredentialStore
from ..db.destinations import (
    DestinationNotFound,
    DestinationRepository,
    EpochDecisionRequired,
    RemoteIdentity,
)
from ..db.jobs import JobStore
from .deps import conn as get_conn
from .deps import secret_box as get_box
from .errors import ApiError, ErrorCode

router = APIRouter()


@router.get("/destinations")
def list_destinations(conn=Depends(get_conn), box=Depends(get_box)) -> dict[str, Any]:  # noqa: ANN001, B008
    repo = _repo(conn, box)
    return {"destinations": [_view(repo, row) for row in repo.list_destinations()]}


@router.post("/destinations")
def create_destination(
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    repo = _repo(conn, box)
    base_url, public_url, api_key = _fields(body)
    # **URL の検証を接続より先に行う。** 逆にすると `javascript:` のような値でも
    # まず接続を試すことになり、400 ではなく 502 を返してしまう。
    _checked(base_url, public_url)
    identity = _verify(base_url, api_key)
    destination_id = repo.create(
        name=body["name"],
        base_url=base_url,
        public_url=public_url,
        secret=api_key,
        identity=identity,
    )
    return {
        "id": destination_id,
        "remote_user_id": identity.remote_user_id,
        "warnings": repo.same_account_warnings(identity, exclude_id=destination_id),
    }


@router.patch("/destinations/{destination_id}")
def edit_destination(
    destination_id: str,
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    repo = _repo(conn, box)
    current = _found(repo, destination_id)
    unknown = set(body) - {"name", "enabled", "base_url", "public_url", "api_key", "same_library"}
    if unknown:
        raise ApiError(
            400, ErrorCode.UNKNOWN_FIELD, "知らない欄がある", {"fields": sorted(unknown)}
        )
    name = body.get("name")
    if name is not None:
        # **名前は画面が送り先を指す唯一の手掛かり**（§13 は内部の id を出さない）。
        # 空にできると、確認の本文も一覧も名前の無い行になる。
        #
        # **`str()` で何でも名前にしない。** dict を渡すと Python の repr
        # （`{'a': 1}`）が名前になる。
        if not isinstance(name, str):
            raise ApiError(400, ErrorCode.BAD_REQUEST, "名前は文字で送る")
        name = name.strip()
        if not name:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "名前は空にできない")
    if set(body) <= {"name", "enabled"}:
        # 接続に関わらない編集は、検証もリビジョンも要らない。
        repo.rename_or_toggle(destination_id, name=name, enabled=body.get("enabled"))
        return {"id": destination_id}
    base_url = body.get("base_url", current["base_url"])
    public_url = body.get("public_url", current["public_url"])
    api_key = body.get("api_key")
    if api_key is None:
        # 鍵を変えない編集でも、保存には可逆な値が要る。
        api_key = repo.secret_of(current["id"])
    _checked(base_url, public_url)
    identity = _verify(base_url, api_key)
    try:
        outcome = repo.add_revision(
            destination_id,
            base_url=base_url,
            public_url=public_url,
            secret=api_key,
            identity=identity,
            same_library=body.get("same_library"),
        )
    except EpochDecisionRequired as exc:
        raise ApiError(
            409,
            ErrorCode.SAME_LIBRARY_UNDECIDED,
            f"{exc}。same_library を true か false で指定する",
        ) from exc
    # **接続と一緒に送られてきた名前・休止も適用する。** 検証してから捨てると、
    # 200 が返るのに名前は古いまま残り、押した人には変わったように見える。
    # **検証が通ってから**当てる（`add_revision` が失敗した編集は、どの欄も
    # 反映しない）。
    if name is not None or "enabled" in body:
        repo.rename_or_toggle(destination_id, name=name, enabled=body.get("enabled"))
    # 参照が絶えた旧鍵を消す。ローテートしても漏洩面が減らないままにしない（§12.3）。
    repo.purge_superseded_credentials(destination_id)
    return {
        "id": destination_id,
        "target_epoch": outcome.target_epoch,
        # 破棄は `add_revision` が同じトランザクションで済ませている（§8）。
        "invalidated_records": outcome.invalidated_records,
        "warnings": repo.same_account_warnings(identity, destination_id),
    }


@router.post("/destinations/{destination_id}/verify")
def verify_destination(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    repo = _repo(conn, box)
    current = _found(repo, destination_id)
    identity = _verify(current["base_url"], repo.secret_of(current["id"]))
    return {
        "remote_user_id": identity.remote_user_id,
        "recorded_user_id": current["remote_user_id"],
        "matches": identity.remote_user_id == current["remote_user_id"],
    }


@router.post("/destinations/{destination_id}/archive")
def archive_destination(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    repo = _repo(conn, box)
    _found(repo, destination_id)
    repo.archive(destination_id)
    return {"status": "ok"}


@router.post("/destinations/{destination_id}/upload")
def start_upload(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    return _enqueue(conn, _repo(conn, box), destination_id, "send")


@router.post("/destinations/{destination_id}/recheck")
def start_recheck(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    return _enqueue(conn, _repo(conn, box), destination_id, "recheck")


# ----------------------------------------------------------------------
def _repo(conn, box) -> DestinationRepository:  # noqa: ANN001
    return DestinationRepository(conn, CredentialStore(conn, box))


def _uploads_of(conn, box):  # noqa: ANN001, ANN202
    from ..db.profiles import ProfileRegistry
    from ..db.uploads import UploadRepository

    return UploadRepository(conn, ProfileRegistry(conn), _repo(conn, box))


def _fields(body: dict[str, Any]) -> tuple[str, str | None, str]:
    try:
        return body["base_url"], body.get("public_url"), body["api_key"]
    except KeyError as exc:
        raise ApiError(
            400, ErrorCode.MISSING_FIELD, "必要な欄が無い", {"field": str(exc).strip("'")}
        ) from exc


def _checked(base_url: str, public_url: str | None) -> None:
    """保存する前に URL を検証する. **接続より先に呼ぶ。**"""
    try:
        normalize_endpoint(base_url)
        if public_url is not None:
            normalize_endpoint(public_url)
    except EndpointRejected as exc:
        raise ApiError(400, ErrorCode.INVALID_ENDPOINT, str(exc)) from exc


def _verify(base_url: str, api_key: str) -> RemoteIdentity:
    """接続を検証し、向き先を観測する. 失敗した設定は保存しない."""
    try:
        with ImmichClient(base_url, api_key) as client:
            body = client.users_me()
    except ImmichError as exc:
        # 502。こちらの要求は正しく、相手に届かないか拒まれている。
        raise ApiError(
            502, ErrorCode.DESTINATION_UNREACHABLE, f"転送先に接続できない: {exc}"
        ) from exc
    observed = body.get("id")
    # **観測値そのものは持ち回らない**（`core.destinations.identity`）。
    return RemoteIdentity.observed(observed if isinstance(observed, str) else None)


def _found(repo: DestinationRepository, destination_id: str):  # noqa: ANN202
    row = repo.get(destination_id)
    if row is None or row["archived_at"] is not None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "その転送先は無い")
    try:
        return repo.current(destination_id)
    except DestinationNotFound as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc


def _enqueue(conn, repo: DestinationRepository, destination_id: str, mode: str):  # noqa: ANN001, ANN202
    _found(repo, destination_id)
    # **params に秘密を入れない**（画面と SSE に出る）。
    return {
        "job_id": JobStore(conn).enqueue("upload", {"destination_id": destination_id, "mode": mode})
    }


def _view(repo: DestinationRepository, row) -> dict[str, Any]:  # noqa: ANN001
    current = repo.current(row["id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "base_url": current["base_url"],
        "public_url": current["public_url"],
        "remote_user_id": current["remote_user_id"],
        "target_epoch": current["target_epoch"],
        "revision": current["revision"],
        "verified_at": current["verified_at"],
    }
