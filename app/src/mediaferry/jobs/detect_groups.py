"""結合グループの検出（§9.7 / `detect_groups` ジョブ）.

公開時に確定した `media_file.duration_seconds` を使う（§9.3 手順 5）。
`probe_state = failed` のファイルと、既にアクティブなグループに属している
ファイルは**境界**として扱う。列から取り除くだけにすると、その前後が
つながって別の録画を 1 つのグループにしてしまう。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..core.merge.digest import input_digest
from ..core.merge.grouping import GroupCandidate, MergePart, detect_groups
from ..core.merge.output import MergeOutputUndefined, merged_rel_path
from ..core.profiles.model import MergeRule
from ..db.jobs import JobContext
from ..db.merges import MergeRepository
from ..db.profiles import ProfileRef


@dataclass(frozen=True)
class DetectOutcome:
    created: int
    existing: int
    undefined: int


class GroupDetector:
    def __init__(self, conn: sqlite3.Connection, repo: MergeRepository) -> None:
        self._conn = conn
        self._repo = repo

    def run(self, ctx: JobContext, profile: ProfileRef) -> DetectOutcome:
        rule = profile.definition.merge
        if not rule.enabled:
            ctx.emit("info", f"プロファイル {profile.definition.slug} は結合しない")
            return DetectOutcome(created=0, existing=0, undefined=0)

        created = existing = undefined = 0
        for candidate in self._candidates(profile, rule):
            try:
                merged_rel_path(profile.definition.slug, rule, candidate.members)
            except MergeOutputUndefined as exc:
                # 出力名が決まらないものは作らない。結合ジョブで初めて
                # 失敗するより、検出の時点で見送って理由を出す。
                undefined += 1
                ctx.emit("warning", f"出力名を決められないので見送る: {exc}")
                continue
            digest = input_digest(
                [(part.media_file_id, part.sha1) for part in candidate.members],
                rule,
                profile.revision_id,
            )
            group_id = self._repo.save_detected(profile, candidate, digest)
            if group_id is None:
                existing += 1
                continue
            created += 1
            ctx.emit(
                "info",
                f"{len(candidate.members)} 件のグループを検出した",
                {"merge_group_id": group_id},
            )
        return DetectOutcome(created=created, existing=existing, undefined=undefined)

    def preview(self, profile: ProfileRef, rule: MergeRule) -> list[GroupCandidate]:
        """閾値を変えたときの候補. **保存しない**（§11 の `/merge-groups/preview`）."""
        return self._candidates(profile, rule)

    # ------------------------------------------------------------------
    def _candidates(self, profile: ProfileRef, rule: MergeRule) -> list[GroupCandidate]:
        candidates: list[GroupCandidate] = []
        for run in self._runs(profile):
            candidates.extend(detect_groups(run, rule))
        return candidates

    def _runs(self, profile: ProfileRef) -> list[list[MergePart]]:
        """アクティブな member を境界にして、連続した並びの断片に分ける."""
        rows = self._conn.execute(
            "SELECT m.id, m.rel_path, m.sha1, m.captured_at, m.duration_seconds,"
            " m.size_bytes, m.probe_state,"
            " EXISTS (SELECT 1 FROM merge_member mm"
            "         WHERE mm.media_file_id = m.id AND mm.active = 1) AS taken"
            " FROM media_file m"
            " WHERE m.profile_id = ? AND m.role = 'original' AND m.kind = 'video'"
            "   AND m.missing_at IS NULL"
            " ORDER BY m.captured_at, m.rel_path",
            (profile.profile_id,),
        )
        runs: list[list[MergePart]] = [[]]
        for row in rows:
            if row["taken"]:
                runs.append([])
                continue
            runs[-1].append(
                MergePart(
                    media_file_id=row["id"],
                    rel_path=row["rel_path"],
                    sha1=row["sha1"],
                    captured_at=datetime.fromisoformat(row["captured_at"]),
                    duration_seconds=row["duration_seconds"],
                    size_bytes=row["size_bytes"],
                    probe_state=row["probe_state"],
                )
            )
        return [run for run in runs if len(run) >= 2]
