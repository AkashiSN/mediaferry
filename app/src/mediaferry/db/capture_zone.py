"""撮影地のタイムゾーンの上書きと、撮影日時を動かした後始末."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import available_timezones

from ..clock import now_iso
from ..core.profiles.model import ProfileDefinition
from ..core.timestamps import CapturedAt, with_zone_override
from .connection import immediate
from .profiles import ProfileRegistry


class ZoneOverrideInvalid(ValueError):
    """付け替えられない要求. 何も書かずに全体を断る."""


class ZoneOverrideBusy(RuntimeError):
    """いま付け替えると、走っている作業が古い日時で仕上げてしまう. 何も書かない."""


# **送信ジョブが掴んでいる状態.** この間に読んだ日時を Immich へ書き、`complete` で
# 終える。付け替えを通すと古い日時のまま終わり、`complete` からの差し戻しも
# 間に合わない。`pending` / `needs_recheck` / `awaiting_datetime_approval` は
# 誰も掴んでおらず、送る（承認する）ときに今の日時を読み直すので止めない。
_HELD_STATES = ("checking", "uploading", "asset_known", "tagging", "fixing_datetime")


@dataclass(frozen=True)
class ZoneOutcome:
    changed: int
    unchanged: int
    requeued: int


def known_zones() -> list[str]:
    """選べるゾーン名. 検める側と画面の候補を同じ集合にする."""
    return sorted(available_timezones())


def apply_zone_override(
    conn: sqlite3.Connection,
    media_ids: Sequence[str],
    zone: str | None,
    default_timezone: str | None,
) -> ZoneOutcome:
    """選んだファイルを撮影地のゾーンへ付け替える. `None` は上書きを外す.

    **瞬間は変えない。** 保存済みの `captured_at` をそのゾーンの壁時計へ直すだけで、
    ファイル名や EXIF から解き直さない。

    **1 つのトランザクションで全部か何もしないか。** 一部だけ付け替えると、
    どれが直ったかを画面から読めない。検めに落ちた時点で ROLLBACK する。

    `derived` の id は、その active member 全員へ置き換える（写真の一覧は結合した
    動画もタイルに出す）。上書きを持つのは `original` だけで、`derived` は先頭の
    active member から継ぐ。
    """
    if zone is not None and zone not in available_timezones():
        raise ZoneOverrideInvalid(f"IANA タイムゾーンとして解釈できない: {zone}")
    if not media_ids:
        raise ZoneOverrideInvalid("付け替えるファイルが選ばれていない")
    registry = ProfileRegistry(conn)
    changed = unchanged = requeued = 0
    moved: list[str] = []
    with immediate(conn):
        for row in _originals_of(conn, media_ids):
            defn = _definition_of(registry, row)
            if defn.timestamp.timezone_policy == "none":
                raise ZoneOverrideInvalid(
                    f"{row['rel_path']} はカメラの時計のゾーンが決まっていないので付け替えられない"
                )
            # 外すときはカメラの時計のゾーンへ戻す. これも瞬間を保つ変換。
            target = zone or defn.timestamp.timezone or default_timezone
            if target is None:
                raise ZoneOverrideInvalid(f"{row['rel_path']} のカメラの時計のゾーンが決まらない")
            value = with_zone_override(_captured(row), target)
            value_moved = (value.at.isoformat(), value.tz) != (
                row["captured_at"],
                row["captured_at_tz"],
            )
            if not value_moved and zone == row["captured_at_zone_override"]:
                unchanged += 1
                continue
            conn.execute(
                "UPDATE media_file SET captured_at = ?, captured_at_tz = ?,"
                " captured_at_zone_override = ? WHERE id = ?",
                (value.at.isoformat(), value.tz, zone, row["id"]),
            )
            changed += 1
            # **送り直すのは値が動いたときだけ。** 撮影地を記録しただけなら、
            # 相手に書いた日時との差は生じない。
            if value_moved:
                moved.append(row["id"])
                requeued += _after_move(conn, row["id"], defn)
        outputs = _outputs_of(conn, moved)
        _refuse_if_busy(conn, moved, outputs)
        for output in outputs:
            requeued += _inherit(conn, output, registry)
    return ZoneOutcome(changed, unchanged, requeued)


def _originals_of(conn: sqlite3.Connection, media_ids: Sequence[str]) -> list[sqlite3.Row]:
    """選ばれた id を `original` の行へ解く. 重複は除き、選んだ順を保つ.

    組のタイルとその元パートを一緒に選びうるので、同じ行が 2 度出る。
    """
    rows: dict[str, sqlite3.Row] = {}
    for media_id in media_ids:
        row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
        if row is None:
            raise ZoneOverrideInvalid(f"そのメディアは無い: {media_id}")
        if row["role"] == "original":
            rows.setdefault(row["id"], row)
            continue
        members = conn.execute(
            "SELECT f.* FROM merge_group g"
            " JOIN merge_member mm ON mm.merge_group_id = g.id AND mm.active = 1"
            " JOIN media_file f ON f.id = mm.media_file_id"
            " WHERE g.output_media_file_id = ? ORDER BY mm.position",
            (media_id,),
        ).fetchall()
        if not members:
            raise ZoneOverrideInvalid(
                f"{row['rel_path']} はつなぎ直す前の古い動画なので付け替えられない"
            )
        for member in members:
            rows.setdefault(member["id"], member)
    return list(rows.values())


def _refuse_if_busy(
    conn: sqlite3.Connection, members: Sequence[str], outputs: Sequence[str]
) -> None:
    """値が動くファイルを、走っている作業が掴んでいれば断る（ROLLBACK させる）.

    - 送信ジョブが掴んでいる記録（`_HELD_STATES`）
    - つないでいる最中（`merging`）のグループの active member。出力は読み終えた
      先頭の日時で公開され、ここからは見えない（まだ `output_media_file_id` が無い）
    - 走っている `recompute_timestamps`。排他区間の外で値を作り、読んだ時点の上書きで
      書くので、間に入った付け替えを古い値で壊す。まだ走っていないものは、走り出した
      ときに今の上書きを読むので止めない
    """
    targets = [*members, *outputs]
    if not targets:
        return
    if conn.execute(
        "SELECT 1 FROM job WHERE type = 'recompute_timestamps'"
        " AND status IN ('running', 'cancelling') LIMIT 1"
    ).fetchone():
        raise ZoneOverrideBusy("撮影日時を再計算している最中。終わってから付け替える")
    marks = ", ".join("?" * len(targets))
    held = ", ".join("?" * len(_HELD_STATES))
    if conn.execute(
        "SELECT 1 FROM upload_record"  # noqa: S608 - 埋めるのは ? だけ
        f" WHERE media_file_id IN ({marks}) AND state IN ({held})"
        "   AND invalidated_at IS NULL LIMIT 1",
        (*targets, *_HELD_STATES),
    ).fetchone():
        raise ZoneOverrideBusy("送っている最中のファイルがある。終わってから付け替える")
    member_marks = ", ".join("?" * len(members))
    if (
        members
        and conn.execute(
            "SELECT 1 FROM merge_member mm"  # noqa: S608 - 埋めるのは ? だけ
            " JOIN merge_group g ON g.id = mm.merge_group_id"
            " WHERE mm.active = 1 AND g.status = 'merging'"
            f"   AND mm.media_file_id IN ({member_marks}) LIMIT 1",
            tuple(members),
        ).fetchone()
    ):
        raise ZoneOverrideBusy("つないでいる最中のファイルがある。終わってから付け替える")


def _definition_of(registry: ProfileRegistry, row: sqlite3.Row) -> ProfileDefinition:
    """その値を解いた版の定義. カメラの時計のゾーンはこの版が決めた."""
    return registry.definition_of(row["captured_at_revision_id"] or row["profile_revision_id"])


def _captured(row: sqlite3.Row) -> CapturedAt:
    return CapturedAt(
        at=datetime.fromisoformat(row["captured_at"]),
        source=row["captured_at_source"],
        tz=row["captured_at_tz"],
        note=row["captured_at_note"],
    )


def _after_move(conn: sqlite3.Connection, media_id: str, defn: ProfileDefinition) -> int:
    """撮影日時が動いた後始末. 差し戻した件数を返す."""
    reopen_skipped_stack(conn, media_id)
    return requeue_sent(conn, media_id, fixes_datetime=defn.immich.fix_datetime_after_upload)


def _outputs_of(conn: sqlite3.Connection, member_ids: Sequence[str]) -> list[str]:
    """`member_ids` のどれかを active member に持つ結合出力.

    継ぐのは先頭だけだが、ここでは絞らない。`_inherit` が先頭から読み直すので、
    先頭が動いていない出力は値が変わらずに素通りする。
    """
    if not member_ids:
        return []
    marks = ", ".join("?" * len(member_ids))
    return [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT g.output_media_file_id FROM merge_group g"  # noqa: S608 - 埋めるのは ? だけ
            " JOIN merge_member mm ON mm.merge_group_id = g.id AND mm.active = 1"
            " WHERE g.output_media_file_id IS NOT NULL"
            f"   AND mm.media_file_id IN ({marks})",
            tuple(member_ids),
        )
    ]


def _inherit(conn: sqlite3.Connection, output_id: str, registry: ProfileRegistry) -> int:
    """結合出力の撮影日時を先頭の active member から継ぎ直す（`Merger._captured_of`）."""
    member = conn.execute(
        "SELECT f.captured_at, f.captured_at_tz FROM merge_group g"
        " JOIN merge_member mm ON mm.merge_group_id = g.id AND mm.active = 1"
        " JOIN media_file f ON f.id = mm.media_file_id"
        " WHERE g.output_media_file_id = ? ORDER BY mm.position LIMIT 1",
        (output_id,),
    ).fetchone()
    output = conn.execute("SELECT * FROM media_file WHERE id = ?", (output_id,)).fetchone()
    if (member["captured_at"], member["captured_at_tz"]) == (
        output["captured_at"],
        output["captured_at_tz"],
    ):
        return 0
    conn.execute(
        "UPDATE media_file SET captured_at = ?, captured_at_tz = ? WHERE id = ?",
        (member["captured_at"], member["captured_at_tz"], output_id),
    )
    return _after_move(conn, output_id, _definition_of(registry, output))


def requeue_sent(conn: sqlite3.Connection, media_id: str, *, fixes_datetime: bool) -> int:
    """送信済みを `needs_recheck` へ戻す（`CLAIMABLE_STATES` に入っている）.

    **戻すのは「プロファイルが日時を書き戻す」ものだけ。**
    `fix_datetime_after_upload` が偽なら、こちらがリモートの日時を書いたことが
    無いので、ローカルが変わってもリモートに差は生じない。戻すと、何も
    変わらない再送を全件に強いる。

    条件は `state = 'complete'` の CAS。進行中のレコードを踏むと、所有者の
    いる行を横から動かすことになる。

    **無効化された行は触らない。** 無効化された `complete` は「なぜ送信を
    許可したか」を残すための監査履歴で、送信済みの記録ではない（§2.3）。
    claim も一覧も数え上げも無効化を除くので、戻しても誰も拾わない。
    """
    if not fixes_datetime:
        return 0
    return conn.execute(
        "UPDATE upload_record SET state = 'needs_recheck', updated_at = ?"
        " WHERE media_file_id = ? AND state = 'complete' AND invalidated_at IS NULL",
        (now_iso(), media_id),
    ).rowcount


def reopen_skipped_stack(conn: sqlite3.Connection, media_id: str) -> int:
    """スタックの見送りを未評価へ戻す（§6）.

    抽出は未評価しか拾わないので、戻さないと二度と再評価されない。
    **`stacked` は戻さない**（相手側に既にあるものを作り直さない）。

    **組の判定は撮影時刻を見ない**（§6）ので、この戻しで結論が変わることは無い。
    見送りを未評価へ戻すのが `captured_at` を動かす側の責任だ、という形だけを残す。

    **無効化された行は触らない。** 監査履歴なので書き換えない。第 2 パスの
    抽出も無効化を除くので、戻しても拾われない。
    """
    return conn.execute(
        "UPDATE upload_record SET stack_state = NULL, stack_reason = NULL, updated_at = ?"
        " WHERE media_file_id = ? AND stack_state = 'skipped' AND invalidated_at IS NULL",
        (now_iso(), media_id),
    ).rowcount
