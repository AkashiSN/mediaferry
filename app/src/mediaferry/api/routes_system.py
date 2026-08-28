"""ヘルス・設定・プロファイル・ジョブ."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, Depends

from ..core.profiles.model import ProfileInvalid, parse_definition
from ..db.jobs import JobStore
from ..db.profiles import (
    ProfileAlreadyArchived,
    ProfileExists,
    ProfileIsBuiltin,
    ProfileRegistry,
    UnknownProfile,
)
from ..db.reset import ResetNotPossible, UnknownScope, reset
from ..db.selection import SENDABLE_CLAUSE
from ..settings import SettingInvalid, SettingLocked, SettingsService, startup_warnings
from .deps import conn as get_conn
from .deps import state as get_state
from .errors import ApiError, ErrorCode

router = APIRouter()

_BUILTIN_MESSAGE = "ビルトインは編集できない。複製してから編集する"
# **`/health` だけは認証を掛けない**（監視と compose の healthcheck が叩く）。
public_router = APIRouter()

_BUILTIN_MESSAGE = "ビルトインは編集できない。複製してから編集する"


@router.post("/reset")
def reset_data(
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """作り直すために、段まで捨てる（§13）.

    **Immich にある資産は対象ではない** —— 消しに行かないし、消えない
    （`db/reset.py`）。段は積み上げで、深い段は浅い段を含む。
    """
    try:
        removed = reset(conn, state.settings.data_root, body.get("scope", ""))
    except UnknownScope as exc:
        raise ApiError(400, ErrorCode.BAD_REQUEST, str(exc)) from exc
    except ResetNotPossible as exc:
        # **generic な conflict にしない。** 画面は code で文面を選ぶので、
        # conflict のままだと「いまの状態ではこの操作はできません」としか出ず、
        # 何を待てばよいのかが読めない。理由ごとに code を分けるため、
        # `exc.reason` をそのまま `ErrorCode` に渡す。
        raise ApiError(409, ErrorCode(exc.reason), str(exc)) from exc
    return {"status": "ok", "removed": removed}


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
        # **画面が時刻に印を添えるために要る。** この 1 つのために画面ごとへ
        # `/settings` を引かせない（この集計はアプリ全体で 1 度だけ引かれる）。
        "default_timezone": settings.default_timezone,
        "destinations": [_destination_summary(conn, row) for row in _destinations(conn)],
        "running_jobs": conn.execute(
            "SELECT count(*) AS n FROM job WHERE status IN ('running', 'cancelling')"
        ).fetchone()["n"],
        # **撮影日時にはゾーンを添える。** 画面はこれで印を作る（`timezone_policy:
        # none` の値は `+00:00` で保存されるので、オフセットだけでは本当に UTC で
        # 撮ったものと区別が付かない）。
        "recent_imports": [
            {
                "id": row["id"],
                "rel_path": row["rel_path"],
                "captured_at": row["captured_at"],
                "captured_at_tz": row["captured_at_tz"],
            }
            for row in conn.execute(
                "SELECT id, rel_path, captured_at, captured_at_tz FROM media_file"
                " ORDER BY created_at DESC, id DESC LIMIT 10"
            )
        ],
        "orphans": len(state.last_reconcile.orphans),
        "missing": conn.execute(
            "SELECT count(*) AS n FROM media_file WHERE missing_at IS NOT NULL"
        ).fetchone()["n"],
        # **これからつなぐグループの数**（§13 の「やること」）。skipped は破棄、
        # supersede 済みは組み直しの旧版なので、どれも押せるボタンが無い。
        "merge_candidates": conn.execute(
            "SELECT count(*) AS n FROM merge_group"
            " WHERE status IN ('detected', 'failed') AND superseded_by_id IS NULL"
        ).fetchone()["n"],
        # **つないだが、人が中身を見るまで宙に浮いているグループの数。**
        #
        # 検証に落ちた結合物は送る候補に出ず（`SENDABLE_CLAUSE`）、構成ファイルも
        # active な member なので出ない。ここで数えないと、ホームが「やることは
        # ありません」と書く一方で、つなぐ画面には「中身を見て、これを使う」が
        # 出ている状態になる。条件は `work/Merge.tsx` の `adoptable` と揃える。
        #
        # **現行の版で作った組だけを数える**（`SENDABLE_CLAUSE` と同じ条件）。
        # カメラの種類を保存すると版が上がり、その版で作った出力は採用しても
        # `group_is_current` が必ず断る —— 数えると、押しても送れないまま数だけが
        # 0 に落ちる行き止まりになる。
        "merge_review_total": conn.execute(
            "SELECT count(*) AS n FROM merge_group g"
            " WHERE g.status = 'merged' AND g.superseded_by_id IS NULL"
            "   AND g.adopted_at IS NULL AND g.output_media_file_id IS NOT NULL"
            "   AND g.profile_revision_id = ("
            "     SELECT p.current_revision_id FROM device_profile p WHERE p.id = g.profile_id)"
            "   AND json_valid(g.verification_json)"
            "   AND json_type(g.verification_json, '$.passed') IS NOT 'true'"
        ).fetchone()["n"],
        # **和を取らない。** 2 つの宛先に未送信の 1 件は 1 件。休止中の宛先は
        # 送り先に選べないので、それしか無ければ「やること」は無い。
        "unsent_total": conn.execute(
            "SELECT count(*) AS n FROM media_file m WHERE EXISTS ("  # noqa: S608
            " SELECT 1 FROM upload_destination d"
            "  WHERE d.archived_at IS NULL AND d.enabled = 1"
            "    AND NOT EXISTS (SELECT 1 FROM upload_record u"
            "                    WHERE u.media_file_id = m.id AND u.destination_id = d.id"
            "                      AND u.invalidated_at IS NULL))"
            f" AND {SENDABLE_CLAUSE}"
        ).fetchone()["n"],
        # **宛先をまたいだ合計。** 承認待ちは宛先ごとの操作なので、和で問題ない。
        "awaiting_total": conn.execute(
            "SELECT count(*) AS n FROM upload_record"
            " WHERE state = 'awaiting_datetime_approval' AND invalidated_at IS NULL"
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
        # スタックの結果（§9.11）。**無効化された記録は数えない。**
        #
        # **`stacked` は「組の数」。** 1 つのスタックに 2 件以上のレコードが属する
        # ので、行を数えると画面に「2 組」と出る。見送りは行ごとに理由が違いうる
        # ので、そちらは件数のまま。
        "stacked": conn.execute(
            "SELECT count(DISTINCT remote_stack_id) AS n FROM upload_record"
            " WHERE destination_id = ? AND stack_state = 'stacked' AND invalidated_at IS NULL",
            (row["id"],),
        ).fetchone()["n"],
        "stack_skipped": conn.execute(
            "SELECT count(*) AS n FROM upload_record"
            " WHERE destination_id = ? AND stack_state = 'skipped' AND invalidated_at IS NULL",
            (row["id"],),
        ).fetchone()["n"],
        # **「まだ送っていない」＝ この宛先の有効な記録がまだ無く、いま送れるもの。**
        # 失敗や承認待ちは既に記録があるので別に数える（画面はそれぞれ違う操作を出す）。
        # `/media?status=unsent`（`routes_media._status_clause`）と同じ定義を使う。
        "unsent": conn.execute(
            "SELECT count(*) AS n FROM media_file m WHERE NOT EXISTS ("  # noqa: S608
            " SELECT 1 FROM upload_record u WHERE u.media_file_id = m.id"
            "  AND u.destination_id = ? AND u.invalidated_at IS NULL)"
            f" AND {SENDABLE_CLAUSE}",
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
    if "key" not in body or "value" not in body:
        # **`KeyError` の文字列を画面に出さない**（`str(KeyError("value"))` は
        # `'value'`）。足りない項目は、画面が定型文で言える種類の失敗。
        raise ApiError(400, ErrorCode.MISSING_FIELD, "key と value が要る")
    try:
        tier = SettingsService(conn, state.env).set(body["key"], body["value"])
    except SettingLocked as exc:
        raise ApiError(409, ErrorCode.SETTING_LOCKED, str(exc)) from exc
    except SettingInvalid as exc:
        raise ApiError(400, ErrorCode.BAD_REQUEST, str(exc)) from exc
    # いつ効くかを返す。RESTART の値を変えて「反映されない」と見えるのを防ぐ。
    return {"status": "ok", "applies": tier.value}


def _profile_view(ref, *, with_definition: bool = False) -> dict[str, Any]:  # noqa: ANN001
    view = {
        "slug": ref.definition.slug,
        "name": ref.definition.name,
        "revision": ref.revision,
        "revision_id": ref.revision_id,
        # 画面はビルトインに錠前を出し、編集の代わりに「複製して編集」を出す。
        "builtin": ref.builtin,
        "archived": ref.archived,
    }
    if with_definition:
        view["definition"] = asdict(ref.definition)
    return view


@router.get("/profiles")
def list_profiles(conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    # archive 済みも返す。画面は区別して出す（消えたのか外したのか分かるように）。
    return {"profiles": [_profile_view(ref) for ref in ProfileRegistry(conn).all()]}


@router.get("/profiles/{profile_slug}")
def get_profile(profile_slug: str, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    try:
        ref = ProfileRegistry(conn).current(profile_slug)
    except UnknownProfile as exc:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのプロファイルは無い") from exc
    return _profile_view(ref, with_definition=True)


@router.post("/profiles")
def create_profile(body: dict, conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    defn = _parsed(body)
    try:
        ref = ProfileRegistry(conn).create(defn)
    except ProfileExists as exc:
        raise ApiError(409, ErrorCode.CONFLICT, "その slug はもう使われている") from exc
    return _profile_view(ref, with_definition=True)


@router.put("/profiles/{profile_slug}")
def update_profile(
    profile_slug: str,
    body: dict,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    defn = _parsed(body)
    if defn.slug != profile_slug:
        # slug はライブラリのパス（library/<slug>/）に使う。変えると過去の
        # 取り込みが宙に浮く（§6）。
        raise ApiError(400, ErrorCode.BAD_REQUEST, "slug は作成後に変更できない")
    try:
        ref = ProfileRegistry(conn).update(profile_slug, defn)
    except UnknownProfile as exc:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのプロファイルは無い") from exc
    except ProfileIsBuiltin as exc:
        raise ApiError(409, ErrorCode.CONFLICT, _BUILTIN_MESSAGE) from exc
    return _profile_view(ref, with_definition=True)


@router.post("/profiles/{profile_slug}/duplicate")
def duplicate_profile(
    profile_slug: str,
    body: dict,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """ビルトインからユーザ定義を作る（§6）. **元は変わらない。**"""
    registry = ProfileRegistry(conn)
    try:
        ref = registry.duplicate(profile_slug, str(body.get("slug", "")), str(body.get("name", "")))
    except UnknownProfile as exc:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのプロファイルは無い") from exc
    except ProfileExists as exc:
        raise ApiError(409, ErrorCode.CONFLICT, "その slug はもう使われている") from exc
    except ProfileInvalid as exc:
        raise ApiError(400, ErrorCode.VALIDATION_FAILED, str(exc)) from exc
    return _profile_view(ref, with_definition=True)


@router.post("/profiles/{profile_slug}/archive")
def archive_profile(profile_slug: str, conn=Depends(get_conn)) -> dict[str, str]:  # noqa: ANN001, B008
    try:
        ProfileRegistry(conn).archive(profile_slug)
    except UnknownProfile as exc:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのプロファイルは無い") from exc
    except ProfileIsBuiltin as exc:
        raise ApiError(409, ErrorCode.CONFLICT, _BUILTIN_MESSAGE) from exc
    except ProfileAlreadyArchived as exc:
        raise ApiError(409, ErrorCode.CONFLICT, "そのプロファイルはもう外してある") from exc
    return {"status": "ok"}


@router.post("/profiles/{profile_slug}/recompute")
def recompute_timestamps(profile_slug: str, conn=Depends(get_conn)) -> dict[str, str]:  # noqa: ANN001, B008
    """撮影日時を今の定義で計算し直す（§6）.

    **ビルトインでも受ける。** 編集できないだけで、`DEFAULT_TIMEZONE` を後から
    設定した場合に既存レコードを直す手段はこれしかない（§12.2）。

    キュー投入時のリビジョンを params に固定する。実行時に現行を読み直すと、
    キューで待っている間の編集で違う規則の再計算になる。
    """
    try:
        profile = ProfileRegistry(conn).current(profile_slug)
    except UnknownProfile as exc:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのプロファイルは無い") from exc
    return {
        "job_id": JobStore(conn).enqueue(
            "recompute_timestamps",
            {"profile_id": profile.profile_id, "profile_revision_id": profile.revision_id},
        )
    }


def _parsed(body: dict):  # noqa: ANN202
    """定義を検証してから返す. **commit の前に落とす。**"""
    if not isinstance(body, dict) or "definition" not in body:
        raise ApiError(400, ErrorCode.MISSING_FIELD, "definition が要る")
    try:
        return parse_definition(body["definition"])
    except ProfileInvalid as exc:
        raise ApiError(400, ErrorCode.VALIDATION_FAILED, str(exc)) from exc
    except (TypeError, AttributeError) as exc:
        raise ApiError(400, ErrorCode.BAD_REQUEST, "definition の形が違う") from exc


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
    return {"jobs": [_job(row, with_last_message=True) for row in JobStore(conn).list_jobs()]}


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


def _job(row, with_last_message: bool = False) -> dict[str, Any]:  # noqa: ANN001
    """ジョブ 1 行の表現.

    `with_last_message` は一覧だけ（`list_jobs` の問い合わせがその欄を持つ）。
    1 件を引く経路は `/jobs/{id}/events` で全文が読めるので、要約は要らない。
    """
    view = {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "created_at": row["created_at"],
        # 速度と残り時間を画面が出すのに要る（進捗と合わせて平均を取る）。
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "error": row["error"],
        # どのカードの作業か。**`params_json` から取り出すのはこの 1 欄だけ**
        # —— params には秘密を入れない約束だが、丸ごと返す口は作らない。
        "volume_instance_id": json.loads(row["params_json"]).get("volume_instance_id"),
        # **走っている間だけ入る**（`finish` が落とす）。速度と残り時間は画面が
        # 2 点の差分から出す —— こちらで持つと、心拍の間隔に依存した値を
        # 永続化することになる。
        "progress": _progress(row),
    }
    if with_last_message:
        # **終わった作業の要約。** 画面が開く前に届いた知らせは持っていないので、
        # ここで添えないと「完了」としか出せない（`docs/history/phase11-design.md` の N4）。
        view["last_message"] = row["last_message"]
    return view


# 進捗を持ちうる状態。**終わった行に「いま何をしているか」は無い。**
_LIVE_STATUSES = ("queued", "running", "cancelling")


def _progress(row) -> dict[str, Any] | None:  # noqa: ANN001
    """**読む側でも守る。** 落とすのは終了時の 1 回きりなので、書き手が
    取り違えると（`finish` と `finish_claimed`）画面に残り続ける。
    """
    raw = row["progress_json"]
    if not raw or row["status"] not in _LIVE_STATUSES:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        # 読めない値で一覧全体を落とさない。進捗は表示のためだけのもの。
        return None
    return value if isinstance(value, dict) else None
