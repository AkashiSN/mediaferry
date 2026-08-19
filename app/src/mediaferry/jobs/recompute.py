"""撮影日時の再計算（§6 の `recompute_timestamps`）.

タイムスタンプ解釈やタイムゾーンを変えても既存データは自動では直らない。
プロファイル単位の明示のジョブで直す。

**ファイルは動かさない。** ライブラリのパスは
`library/<slug>/<カード上の相対パス>` で `captured_at` を含まない（§7）ので、
直すのは `captured_at` / `captured_at_source` / `captured_at_tz` /
`captured_at_note` と `captured_at_revision_id` の 5 列だけ。

**`profile_revision_id` は触らない。** それは「そのレコードが取り込みに使用した
不変の版」という別の問いで、進めると timestamp 以外の新定義（`scan` / `merge` /
`immich`）もそのファイルに適用したと偽ることになる（`0011`）。

**`recompute` は Immich に触らない。** 日時が変わった送信済みレコードは
`needs_recheck` へ戻し、次のアップロードジョブにパイプラインを再実行させる。
そこでリースと preflight と guard の下で、現在値を観測したうえで承認待ちか
完了に落ち着く。
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from ..adapters.exif import read_datetime_original
from ..adapters.ffprobe import PHOTO_EXTENSIONS
from ..clock import now_iso
from ..core import lease_pulse
from ..core.lease_pulse import with_lease_pulse
from ..core.timestamps import CapturedAt, resolve_captured_at
from ..db.connection import immediate
from ..db.jobs import JobContext, LeaseLost
from ..db.profiles import ProfileRef

BATCH_SIZE = 100


@dataclass(frozen=True)
class RecomputeOutcome:
    changed: int
    unchanged: int
    skipped: int
    requeued: int


@dataclass
class _Tally:
    changed: int = 0
    unchanged: int = 0
    skipped: int = 0
    requeued: int = 0

    def outcome(self) -> RecomputeOutcome:
        return RecomputeOutcome(self.changed, self.unchanged, self.skipped, self.requeued)


class Recomputer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        data_root: Path,
        default_timezone: str | None,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._conn = conn
        self._data_root = data_root
        self._default_timezone = default_timezone
        self._batch_size = batch_size

    def run(self, ctx: JobContext, profile: ProfileRef) -> RecomputeOutcome:
        """**original を全部直してから derived を直す。**

        派生物の `captured_at` は算出ではなく継承（`Merger._captured_of`）なので、
        先に derived を回すと再計算前の値を継いでしまう。
        """
        tally = _Tally()
        finished = self._pass(
            ctx,
            profile,
            self._originals(profile.profile_id),
            self._recomputed_original,
            tally,
            "カード上の原名（source_entry）が残っていない",
        )
        if finished:
            self._pass(
                ctx,
                profile,
                self._derived(profile.profile_id),
                self._recomputed_derived,
                tally,
                "先頭の active member が無いか、その member を再計算できていない",
            )
        return tally.outcome()

    # ------------------------------------------------------------------
    # 対象の抽出

    def _originals(self, profile_id: str) -> list[sqlite3.Row]:
        """**`filename` はカード上の原名に当てる。** `media_file.rel_path` は
        公開先の名前で、衝突時には接尾辞が付いている（§9.3）。
        """
        return list(
            self._conn.execute(
                "SELECT m.id, m.rel_path, m.mtime_ns, m.captured_at, m.captured_at_source,"
                " m.captured_at_tz, m.captured_at_note,"
                " (SELECT s.rel_path FROM source_entry s"
                "   WHERE s.media_file_id = m.id AND s.state = 'published'"
                "   ORDER BY s.observed_at, s.id LIMIT 1) AS source_rel_path"
                " FROM media_file m WHERE m.profile_id = ? AND m.role = 'original'"
                " ORDER BY m.rel_path",
                (profile_id,),
            )
        )

    def _derived(self, profile_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT m.id, m.rel_path, m.mtime_ns, m.captured_at, m.captured_at_source,"
                " m.captured_at_tz, m.captured_at_note,"
                " (SELECT mm.media_file_id FROM merge_group g"
                "   JOIN merge_member mm ON mm.merge_group_id = g.id AND mm.active = 1"
                "   WHERE g.output_media_file_id = m.id"
                "   ORDER BY mm.position LIMIT 1) AS member_id"
                " FROM media_file m WHERE m.profile_id = ? AND m.role = 'derived'"
                " ORDER BY m.rel_path",
                (profile_id,),
            )
        )

    # ------------------------------------------------------------------
    # 再計算の入力

    def _recomputed_original(
        self, ctx: JobContext, row: sqlite3.Row, profile: ProfileRef
    ) -> CapturedAt | None:
        """`source_entry` が無ければ再計算しない.

        **勝手に mtime へ落とすと、正しかった値を壊す**（カードを再フォーマット
        すれば原名は消える）。
        """
        source_rel = row["source_rel_path"]
        if source_rel is None:
            return None
        return resolve_captured_at(
            profile.definition,
            source_rel,
            row["mtime_ns"],
            self._default_timezone,
            exif_wall=self._exif_wall(ctx, row, source_rel, profile),
        )

    def _exif_wall(
        self, ctx: JobContext, row: sqlite3.Row, source_rel: str, profile: ProfileRef
    ) -> datetime | None:
        """**読むのは公開済みの実体。** カードはもう手元に無い.

        取り込みと同じく、画像以外では読みに行かない（`exifread` は認識できない
        入力に例外ではなく WARNING を出す）。

        **読んでいる間はリースを延ばす。** ここはトランザクションの外なので
        リースの確認が無く、遅いディスクで積もると次のバッチの `assert_lease` が
        落ちて、**先のバッチを commit したまま**ジョブが失敗する。囲むのは
        ファイル入出力だけ —— DB へ触る処理を別スレッドへ入れると、1 本の接続を
        2 つのスレッドが同時に使うことになる。
        """
        extension = PurePosixPath(source_rel).suffix.lstrip(".").upper()
        if profile.definition.timestamp.source != "exif" or extension not in PHOTO_EXTENSIONS:
            return None
        path = self._data_root / row["rel_path"]
        return with_lease_pulse(ctx, lambda: read_datetime_original(path))

    def _recomputed_derived(
        self, ctx: JobContext, row: sqlite3.Row, profile: ProfileRef
    ) -> CapturedAt | None:
        """先頭の active member から derive する. **算出しない。**

        結合出力そのものの名前・EXIF・mtime を読むと意味が変わる（出力名の
        壁時計は先頭パートのもの、mtime は録画終了時刻）。

        **継承元がこの版で再計算できていなければ、派生物も飛ばす。**
        """
        if row["member_id"] is None:
            return None
        member = self._conn.execute(
            "SELECT captured_at, captured_at_source, captured_at_tz, captured_at_note,"
            " captured_at_revision_id FROM media_file WHERE id = ?",
            (row["member_id"],),
        ).fetchone()
        if member is None:
            return None
        if member["captured_at_revision_id"] != profile.revision_id:
            # **継承元が旧版のままなら進めない。** 先頭 member が飛ばされている
            # （原名が残っていない）ときにここへ来る。値は旧版で算出したままなのに
            # 新版で算出したと記録すると、`0011` で列を分けた意味が消える。
            return None
        return CapturedAt(
            at=datetime.fromisoformat(member["captured_at"]),
            source=member["captured_at_source"],
            tz=member["captured_at_tz"],
            note=member["captured_at_note"],
        )

    # ------------------------------------------------------------------
    # 適用

    def _pass(
        self,
        ctx: JobContext,
        profile: ProfileRef,
        rows: Sequence[sqlite3.Row],
        recompute: Callable[[JobContext, sqlite3.Row, ProfileRef], CapturedAt | None],
        tally: _Tally,
        skip_reason: str,
    ) -> bool:
        """1 巡ぶん. 最後まで回れたら True. 件数が多いのでバッチで区切る."""
        skipped = 0
        for start in range(0, len(rows), self._batch_size):
            # **バッチごとに、キャンセルとリースの両方を見る。** ここまでの
            # バッチは commit 済みで、残りは手つかずのまま降りる。
            if ctx.cancelled():
                ctx.emit("info", "キャンセルを観測したので再計算を中止した")
                return False
            try:
                ctx.assert_lease()
                ctx.heartbeat()
                skipped += self._apply_batch(
                    ctx, rows[start : start + self._batch_size], profile, recompute, tally
                )
            except LeaseLost:
                # 確認の直後にキャンセルが commit されると、リースを失った形で
                # 降りてくる。**利用者が押したキャンセルを失敗として記録しない。**
                # EXIF を読んでいる間の pulse も `assert_lease` を通すので、
                # バッチの最中のキャンセルはここへ落ちる。
                if ctx.cancelled():
                    ctx.emit("info", "キャンセルを観測したので再計算を中止した")
                    return False
                raise
        if skipped:
            # **黙って飛ばさない。** 件数が実際と食い違うと、直っていない行に
            # 気付けない。
            ctx.emit("info", f"{skip_reason}ため {skipped} 件を飛ばした")
        return True

    def _apply_batch(
        self,
        ctx: JobContext,
        batch: Sequence[sqlite3.Row],
        profile: ProfileRef,
        recompute: Callable[[JobContext, sqlite3.Row, ProfileRef], CapturedAt | None],
        tally: _Tally,
    ) -> int:
        """1 バッチを 1 つのトランザクションで書く.

        **差し戻しは同じトランザクションに入れる。** 割ると「値は新しいのに
        `complete` のまま」が残る。EXIF の読み取りは長いので外で済ませ、
        その間のリースは 2 つの仕掛けで守る（下）。**書き込みの側では
        リースを取り直す** —— 頭の確認から時間が空いている。
        """
        # **リースを守る仕掛けが 2 つ要る。** `_exif_wall` の pulse は「1 枚が
        # 長い」場合を守るが、`with_lease_pulse` は処理が間隔より短く終わると
        # 1 度も打たない（`thread.join(timeout=間隔)` が先に返る）。1 枚ずつが
        # 短くても 100 枚で積もるので、行をまたいだ経過時間でも打つ。
        resolved = []
        last_beat = time.monotonic()
        for row in batch:
            resolved.append((row, recompute(ctx, row, profile)))
            if time.monotonic() - last_beat >= lease_pulse.HEARTBEAT_INTERVAL:
                # **`assert_lease` を先に呼ぶ**（`with_lease_pulse` と同じ理由）。
                # `extend_lease` は `cancelling` でも延ばすので、`heartbeat` だけだと
                # キャンセル済みのリースを延ばし続け、**残りの EXIF を最後まで
                # 読んでから**書き込みの確認でようやく止まる。
                ctx.assert_lease()
                ctx.heartbeat()
                last_beat = time.monotonic()
        skipped = 0
        with immediate(self._conn):
            # **確認と遷移を 1 つの `BEGIN IMMEDIATE` に入れる**（§9.3 手順 7 と同じ形）。
            # バッチの頭の確認から再計算を挟むので、その間にキャンセルが commit
            # されうる。ここで取り直さないと、**キャンセル済みと表示した後に
            # commit される**。書き込みロックを取った状態で確認するので、確認と
            # 書き込みの間には誰も割り込めない。
            ctx.assert_lease()
            for row, value in resolved:
                if value is None:
                    skipped += 1
                    tally.skipped += 1
                    continue
                self._write(row, value, profile.revision_id)
                if _same(row, value):
                    tally.unchanged += 1
                    continue
                tally.changed += 1
                tally.requeued += self._requeue(row, profile)
        return skipped

    def _write(self, row: sqlite3.Row, value: CapturedAt, revision_id: str) -> None:
        self._conn.execute(
            "UPDATE media_file SET captured_at = ?, captured_at_source = ?, captured_at_tz = ?,"
            " captured_at_note = ?, captured_at_revision_id = ? WHERE id = ?",
            (value.at.isoformat(), value.source, value.tz, value.note, revision_id, row["id"]),
        )

    def _requeue(self, row: sqlite3.Row, profile: ProfileRef) -> int:
        """送信済みを `needs_recheck` へ戻す（`CLAIMABLE_STATES` に入っている）.

        **戻すのは「プロファイルが日時を書き戻す」ものだけ。**
        `fix_datetime_after_upload` が偽なら、こちらがリモートの日時を書いたことが
        無いので、ローカルが変わってもリモートに差は生じない。戻すと、何も
        変わらない再送を全件に強いる。

        条件は `state = 'complete'` の CAS。進行中のレコードを踏むと、所有者の
        いる行を横から動かすことになる。
        """
        if not profile.definition.immich.fix_datetime_after_upload:
            return 0
        return self._conn.execute(
            "UPDATE upload_record SET state = 'needs_recheck', updated_at = ?"
            " WHERE media_file_id = ? AND state = 'complete'",
            (now_iso(), row["id"]),
        ).rowcount


def _same(row: sqlite3.Row, value: CapturedAt) -> bool:
    return (
        row["captured_at"] == value.at.isoformat()
        and row["captured_at_source"] == value.source
        and row["captured_at_tz"] == value.tz
        and row["captured_at_note"] == value.note
    )
