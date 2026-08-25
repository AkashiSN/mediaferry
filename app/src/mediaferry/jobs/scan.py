"""ソースボリュームのスキャン（§9.5）.

dirfd 起点で scan.roots 配下を列挙し、既知の source_entry と照合する。
この段階でフル SHA-1 は計算しない（16GiB を読む必要があるため）。
同一性の判定には quick_fingerprint を使う。

**最後まで見たら `volume_instance.scanned_at` に印を付ける。中身が空でも付ける**
——「数えたか」は行の有無からは分からない（§11）。

**最後まで見たら、カードから消えたファイルの行も外す。** 外さないと
`pending_count` が実体より多いまま残り、取り込みが開けない ENOENT で失敗する。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from ..adapters.fs import iter_media_files, open_beneath, probe_beneath
from ..clock import now_iso
from ..core.fingerprint import FINGERPRINT_VERSION, quick_fingerprint
from ..core.uploads.stacking import extension_of, stem_prefix
from ..db.jobs import JobContext
from ..db.profiles import ProfileRef
from ..db.sources import mark_scanned
from ..ids import new_id
from .volumes import PENDING_CLAUSE


@dataclass(frozen=True)
class ScanOutcome:
    total: int
    new: int
    already_imported: int
    ambiguous: int
    vanished: int = 0


class Scanner:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def scan(
        self, ctx: JobContext, dirfd: int, volume_instance_id: str, profile: ProfileRef
    ) -> ScanOutcome:
        defn = profile.definition
        # 掃除の候補を絞る基準。列挙より先に採ることで、今回触れた行が候補から
        # 外れる。**絞りであって守りではない** —— 触れた行はカードに実在するので、
        # 基準が無くても `probe_beneath` が残す。開き直す回数が減るだけ。
        started = now_iso()
        total = new = imported = ambiguous = vanished = 0
        counted = True
        # 同席の証拠を書く対象。**組の対象になる拡張子だけ**集める。
        eligible: list[str] = []
        for found in iter_media_files(dirfd, defn.scan.roots, defn.scan.extensions):
            if ctx.cancelled():
                counted = False
                break
            total += 1
            ctx.heartbeat()
            fingerprint = self._fingerprint(dirfd, found.rel_path, found.size_bytes)
            verdict = self._reconcile_entry(volume_instance_id, found, fingerprint)
            if verdict == "imported":
                imported += 1
            elif verdict == "ambiguous":
                ambiguous += 1
            else:
                new += 1
            ctx.emit("info", f"{found.rel_path}: {verdict}", {"size_bytes": found.size_bytes})
            if defn.stack.enabled and extension_of(found.rel_path) in defn.stack.extensions:
                eligible.append(found.rel_path)
        # **列挙が 1 度も回らなくてもキャンセルを見る。** 一致するファイルが 0 件の
        # カードでは for の中の判定に届かないので、降りたことに気づけない。
        # 掃除は破壊的なので、気づかないまま実行してはいけない。
        if ctx.cancelled():
            counted = False
        if counted:
            # **最後まで見たときだけ**。途中で降りたスキャンを数え終わったことに
            # すると、1 件も見ていないカードに画面が「取り込むものはありません。」と
            # 書く。掃除も同じ理由で、見ていないだけのファイルを「消えた」と読む。
            # 同席の証拠も同じ理由で、見ていないだけの相方を「居なかった」と読む。
            vanished = self._sweep_vanished(ctx, dirfd, volume_instance_id, started)
            self._mark_copresence(ctx, volume_instance_id, eligible)
            mark_scanned(self._conn, volume_instance_id)
        return ScanOutcome(
            total=total,
            new=new,
            already_imported=imported,
            ambiguous=ambiguous,
            vanished=vanished,
        )

    def _sweep_vanished(
        self, ctx: JobContext, dirfd: int, volume_instance_id: str, started: str
    ) -> int:
        """今回触れなかった取り込み待ちの行のうち、本当に消えたものを外す.

        **「無い」には観測を要求する。** 列挙に出ないことと、カードから消えたことは
        別 —— `scan.extensions` を狭めれば、実在するファイルの行も列挙から外れる。
        だから 1 件ずつ開いて確かめてから消す。

        外すのは `PENDING_CLAUSE` の行だけ。`published` は「このカードから
        取り込んだ」という記録で、スタッキングの「同じカード」判定（§9.11）と
        `_known_files_survive` の標本がこれを引く。staging がまだ指している行も
        残す —— `ON DELETE RESTRICT` なので消しに行くとスキャンごと落ちる。

        **1 件ずつ開くので、列挙と同じくリースを打ち続ける**（`heartbeat`）。

        **「無い」と言い切れた行だけ消す。** 権限や I/O の一時的な失敗を「無い」と
        読むと、実在するファイルの記録を落とす。列挙側も開けないディレクトリを
        黙って飛ばすので、確かめずに消すと部分木ぶんがまとめて消える。
        """
        rows = self._conn.execute(
            "SELECT id, rel_path FROM source_entry"  # noqa: S608
            f" WHERE volume_instance_id = ? AND {PENDING_CLAUSE} AND observed_at < ?"
            " AND id NOT IN (SELECT source_entry_id FROM artifact_staging"
            "                WHERE source_entry_id IS NOT NULL)",
            (volume_instance_id, started),
        ).fetchall()
        gone = []
        unknown = 0
        for row in rows:
            ctx.heartbeat()
            present = probe_beneath(dirfd, row["rel_path"])
            if present is None:
                unknown += 1
            elif present is False:
                gone.append(row["id"])
        for entry_id in gone:
            self._conn.execute("DELETE FROM source_entry WHERE id = ?", (entry_id,))
        if unknown:
            # **黙って残さない。** 次の取り込みがこの行で失敗したとき、理由が
            # どこにも無いと追えない（起動時の回収で同じことがあった）。
            ctx.emit("warning", f"{unknown} 件は在るか確かめられなかった（そのまま残す）")
        return len(gone)

    def _mark_copresence(
        self, ctx: JobContext, volume_instance_id: str, eligible: list[str]
    ) -> None:
        """同じ stem の下で 2 つ以上見えた行に、同じ印を書く.

        **印は `<job_id>:<stem prefix>`。** スキャンの id だけにすると、1 回の
        スキャンが書いた別々の組が同じ印になる（一覧が無関係な写真を畳む）。

        **拡張子が 2 種類以上あることを要求する。** 大小文字だけ違う拡張子
        （`IMG_0001.JPG` と `IMG_0001.jpg`）はケースセンシティブな FS では別
        ファイルとして列挙されるが `extension_of` が正規化すると同じ拡張子に
        なるので、実際に起こりうる。この場合は `core/uploads/stacking.py` の
        `Identity.ambiguous` と同じ「曖昧な重複」であって RAW+JPEG の組では
        ないため、条件は「2 件以上」ではなく「2 種類以上」でなければならない。

        **1 件ずつ `UPDATE` するので、列挙や `_sweep_vanished` と同じくリースを
        打ち続ける。** 実機のカードは 1488 件で、その大半がこの対象になる。
        """
        by_prefix: dict[str, list[str]] = {}
        for rel_path in eligible:
            by_prefix.setdefault(stem_prefix(rel_path), []).append(rel_path)
        for prefix, paths in by_prefix.items():
            if len({extension_of(path) for path in paths}) < 2:
                continue
            for rel_path in paths:
                ctx.heartbeat()
                self._conn.execute(
                    "UPDATE source_entry SET copresent_key = ?"
                    " WHERE volume_instance_id = ? AND rel_path = ?",
                    (f"{ctx.job_id}:{prefix}", volume_instance_id, rel_path),
                )

    def _fingerprint(self, dirfd: int, rel_path: str, size: int) -> str:
        fd = open_beneath(dirfd, rel_path)
        with os.fdopen(fd, "rb") as fileobj:
            return quick_fingerprint(fileobj, size)

    def _reconcile_entry(self, volume_instance_id: str, found, fingerprint: str) -> str:  # noqa: ANN001
        row = self._conn.execute(
            "SELECT * FROM source_entry WHERE volume_instance_id = ? AND rel_path = ?",
            (volume_instance_id, found.rel_path),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes,"
                " mtime_ns, quick_fingerprint, fingerprint_version, state, observed_at,"
                " extension)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'seen', ?, ?)",
                (
                    new_id(),
                    volume_instance_id,
                    found.rel_path,
                    found.size_bytes,
                    found.mtime_ns,
                    fingerprint,
                    FINGERPRINT_VERSION,
                    now_iso(),
                    extension_of(found.rel_path),
                ),
            )
            return "new"

        same = (
            row["size_bytes"] == found.size_bytes
            and row["quick_fingerprint"] == fingerprint
            and row["fingerprint_version"] == FINGERPRINT_VERSION
        )
        if same and row["mtime_ns"] == found.mtime_ns and row["state"] == "published":
            self._touch(row["id"], extension_of(found.rel_path))
            return "imported"
        if same and found.mtime_ns < row["mtime_ns"]:
            # 指紋は一致するが mtime が記録より古い。フルハッシュで確認する
            # （deep_verify で扱う。ここでは曖昧として画面に出す）。
            self._touch(row["id"], extension_of(found.rel_path))
            return "ambiguous"
        self._conn.execute(
            "UPDATE source_entry SET size_bytes = ?, mtime_ns = ?, quick_fingerprint = ?,"
            " fingerprint_version = ?, state = 'seen', media_file_id = NULL,"
            # **`media_file_id` を外すのと同じ拍で証拠も外す。** この行がもう前の
            # media_file を代表しないと決めた瞬間だから。**`same` では判断しない**
            # —— quick_fingerprint はサイズと 16 窓しか見ない確率的な判定で、標本窓の
            # 外だけが変わったファイルを取りこぼす（`core/fingerprint.py` の
            # docstring）。取りこぼすと、撮り直した JPG が古い RAW と組む。
            #
            # **SET の式は更新前の行を見る**ので、`media_file_id` は「この UPDATE で
            # 外れる前の値」。もともと NULL（取り込み待ち）の行は外すものが無いので
            # 印を残す —— 無条件に消すと、末尾の `_mark_copresence` に届かない
            # スキャンが組の 2 行を非対称にし、その組が二度と成立しなくなる。
            " copresent_key = CASE WHEN media_file_id IS NOT NULL THEN NULL"
            "                      ELSE copresent_key END,"
            " extension = ?, observed_at = ?"
            " WHERE id = ?",
            (
                found.size_bytes,
                found.mtime_ns,
                fingerprint,
                FINGERPRINT_VERSION,
                extension_of(found.rel_path),
                now_iso(),
                row["id"],
            ),
        )
        return "new"

    def _touch(self, entry_id: str, extension: str) -> None:
        self._conn.execute(
            "UPDATE source_entry SET observed_at = ?, extension = ? WHERE id = ?",
            (now_iso(), extension, entry_id),
        )
