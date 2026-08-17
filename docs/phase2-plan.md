# mediaferry Phase 2（結合）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分割録画のグループを検出して結合し、検証結果と §10 の選択肢を API で取れるようにする。

**Architecture:** 判断はすべて `core/merge/` の純粋関数に置く（境界判定・ダイジェスト・出力名・保持ストリーム・検証）。副作用は `adapters/ffmpeg.py`（外部プロセス）と `adapters/publisher.py`（公開）に閉じる。`db/merges.py` がグループの状態遷移を持ち、`jobs/detect_groups.py` と `jobs/merger.py` が単一 asyncio ワーカーの上でそれらを組み立てる。**公開は Phase 1 の `ArtifactPublisher` をそのまま通す。** derived 専用の別実装を作ると、結合物だけが回収不能になる。

**Tech Stack:** Python 3.12 / uv workspace / SQLite（WAL）/ FastAPI / ffmpeg・ffprobe / pytest / ruff

**Spec:** `docs/design.md`（正本。§9.7 グループ検出 / §9.8 結合と検証 / §10 選択肢規則 / §9.9 ジョブ）。実測で確定した事項は `docs/phase0-findings.md`、作業の前提は `docs/HANDOFF.md`、Phase 1 の実装計画は `docs/phase1-plan.md`。

## Phase 2 の範囲

| 入れる | 入れない（Phase 3 以降） |
| --- | --- |
| §9.7 のグループ検出（`detect_groups` ジョブ）と保存しない preview | 手動でのグループ分割・結合（`detected_by = manual` / supersede）→ Phase 4 |
| §9.8 の結合・検証・公開（`merge` ジョブ）。`detected` / `failed` から実行できる | 継ぎ目サムネイルの**画像生成**と `GET /media/{id}/thumbnail` → Phase 4 |
| 採用（`adopted_at`）の API | **公開済み結合物の破棄・再結合** → Phase 4 |
| §10 **(b) の選択肢の提示規則**と `GET /uploads/selectable` | `upload_record` / `selection_rule` / claim 時の §10 (a)(c) 評価 → Phase 3 |

継ぎ目については、**秒数（各パートの累積境界）を `verification_json` に残す**ところまでを Phase 2 とする。画像はまだ作らない。

**公開後の操作は「採用」だけにする。** 検証に落ちた出力も公開済み
（`status = merged`、`output_media_file_id` が入っている）なので、これを破棄したり
作り直したりするには、旧グループを `superseded_by_id` で新グループへ向ける仕組みが
要る。それを画面の無い段階で先に固めると、Phase 4 の手動編集と二重の仕様になる。
**「まだ何も公開していない失敗」（`status = failed`）からの結合実行は残す** ——
出力も `media_file` も無いので、やり直しても履歴を壊さない。

## Global Constraints

すべてのタスクの要件に、以下が暗黙に含まれる。Phase 1 と同じ内容なので、`docs/phase1-plan.md` の同名の節と読み比べる必要はない。

- **作業ディレクトリはリポジトリのルート。** コマンドはすべてここから実行する。
- Python は `>=3.12`。ruff の `line-length = 100`、`target-version = "py312"`、
  lint は `select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`（`ANN401` のみ ignore）。
  `**/tests/*` は `S101` / `S105`〜`S107` / `ANN` が免除される。
  **`docs/` は ruff の対象外**（`extend-exclude = ["docs"]`）。ruff は Markdown の
  コードブロックも整形するので、除外しないと仕様書と実装計画そのものが書き換わる。
  この計画に載っている Python はすべて ruff を通した形で書いてある。
- すべてのモジュールは `from __future__ import annotations` で始める。
- **コメントと docstring は日本語。**「いま書かれているコードを現在形で説明する」だけを書く。
  過去の経緯はコードに書かず `docs/` に残す（`CLAUDE.md` の規約）。
- **環境固有の値をリポジトリに含めない。** IP アドレス、ホスト名、データセットのパス、
  API キー、タイムゾーンの実値をコードにもテストにも書かない。
- **DB に絶対パスを保存しない。** `DATA_ROOT` からの相対パスのみが正規形（§7）。
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**（§12.3）。
- 外部コマンドは必ず引数配列で起動する。シェル文字列を組み立てない（§14）。
- システム時刻は **UTC の ISO-8601 文字列**で DB に入れ、生成は `mediaferry.clock` の
  関数だけを使う。**例外は `media_file.captured_at`**（解決したオフセット付きで保存する）。
- ID は `uuid4().hex`（32 文字の TEXT）。`job_event.id` だけ整数の自動採番。
- **DB 接続はスコープごとに 1 本。** トランザクションは接続に属していてスレッドには属さない。
- **ジョブは固定したプロファイルリビジョンを読む。** 現行を読み直すと、キューで待って
  いる間の編集で、確認画面と違う規則で処理される。
- テストのマーカー: root を要するものは `needs_root`、実 Immich を要するものは
  `needs_immich`。既定の `pytest` では実行されない。**ffmpeg を使うテストは
  マーカーを付けず、`shutil.which("ffmpeg") is None` のときだけ skip する**
  （既存の `test_adapter_ffprobe.py` と同じ作法）。
- 各タスクの最後に必ず `uv run pytest`・`uv run ruff check .`・`uv run ruff format .` を通す。
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付けて実行する。** 変異の前後で
  バイト数が変わらない書き換え（`>` を `<` にする、`a or b` を `b or a` にする等）
  では、`.pyc` の無効化条件（mtime の秒＋サイズ）をすり抜けて古いバイトコードが
  使われ、変異が効いているかを読み違える。
- **検出できない変異は、検出できないことをこの計画に書き戻す。**
- コミットは Conventional Commits + 日本語の本文。**本文に Claude のセッション URL を
  書かない**（`CLAUDE.md` の規約）。
- **Phase 2 は配布可能なリリースにしない。** `BIND_HOST` の既定は `127.0.0.1` のまま。

### 検証コマンド

```bash
uv sync --all-packages     # --all-packages が必須。素の sync ではメンバーが入らない
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

---

## ファイル構成

| ファイル | 責務 |
| --- | --- |
| `app/src/mediaferry/core/merge/__init__.py` | 空（パッケージ宣言） |
| `app/src/mediaferry/core/merge/grouping.py` | §9.7 の境界判定。純粋関数 |
| `app/src/mediaferry/core/merge/digest.py` | `input_digest` |
| `app/src/mediaferry/core/merge/streams.py` | `keep_streams` によるストリーム選択。`-map` と検証で共有する |
| `app/src/mediaferry/core/merge/output.py` | `output_name` の展開と `derived/` の相対パス |
| `app/src/mediaferry/core/merge/verify.py` | §9.8 の検証。ffprobe の出力を受ける純粋関数 |
| `app/src/mediaferry/adapters/ffmpeg.py` | `MergeRunner`。concat / TS フォールバック、プロセスグループの刈り取り |
| `app/src/mediaferry/adapters/publisher.py` | **修正**。`publish_prepared` を足す |
| `app/src/mediaferry/db/merges.py` | `MergeRepository`。グループの保存と状態遷移 |
| `app/src/mediaferry/db/selection.py` | §10 (b) の選択肢クエリ |
| `app/src/mediaferry/jobs/detect_groups.py` | `GroupDetector`（`detect_groups` ジョブ） |
| `app/src/mediaferry/jobs/merger.py` | `Merger`（`merge` ジョブ。1 ジョブ 1 グループ） |
| `app/src/mediaferry/jobs/reconcile.py` | **修正**。中断した結合の回収 |
| `app/src/mediaferry/api/routes_merges.py` | `/merge-groups` 系と `/uploads/selectable` |
| `app/src/mediaferry/api/jobs_wiring.py` | **修正**。`run_detect_groups` / `run_merge` |
| `app/src/mediaferry/api/app.py` | **修正**。ルータとハンドラの登録 |
| `app/tests/test_merge_grouping.py` 〜 `test_merge_e2e.py` | 単体・統合 |
| `app/tests/crash_child.py` / `test_crash_consistency.py` | **修正**。`publish_prepared` の 11 段 |
| `docs/design.md` | **修正**。§11 に検出エンドポイント、§9.8 に閾値の実値 |
| `docs/HANDOFF.md` | **修正**。Phase 2 完了時の現在地 |

### 実装順序と依存

```
1 grouping ─┐
2 digest ───┤
3 streams ──┼─→ 9 detect_groups ─┐
4 output ───┘                     ├─→ 13 API ─→ 14 統合テスト + docs
5 verify ───┐                     │
6 ffmpeg ───┼─→ 10 merger ────────┤
7 publisher ┘                     │
8 merges (DB) ────────────────────┤
11 reconcile ─────────────────────┤
12 selection ─────────────────────┘
```

---

### Task 1: グループ検出の境界判定

**Files:**
- Create: `app/src/mediaferry/core/merge/__init__.py`
- Create: `app/src/mediaferry/core/merge/grouping.py`
- Test: `app/tests/test_merge_grouping.py`

**Interfaces:**
- Consumes: `mediaferry.core.profiles.model.MergeRule`（既存）
- Produces:
  - `MergePart(media_file_id: str, rel_path: str, sha1: str, captured_at: datetime, duration_seconds: float | None, size_bytes: int, probe_state: str)`
  - `GroupCandidate(members: tuple[MergePart, ...], gaps: tuple[float, ...])`
  - `detect_groups(parts: Sequence[MergePart], rule: MergeRule) -> list[GroupCandidate]`
  - `GIB: int`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merge_grouping.py`:

```python
from datetime import UTC, datetime, timedelta

from mediaferry.core.merge.grouping import GIB, MergePart, detect_groups
from mediaferry.core.profiles.model import KeepStreams, MergeRule

BASE = datetime(2026, 8, 17, 14, 30, 0, tzinfo=UTC)


def a_rule(**overrides):
    values = {
        "enabled": True,
        "tolerance_seconds": 5,
        "min_part_size_gib": 15,
        "sequence_pattern": r"_(?P<seq>\d{4})_D$",
        "output_name": "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4",
        "keep_streams": KeepStreams(video="primary", audio="all", timecode=True, data=False),
    }
    values.update(overrides)
    return MergeRule(**values)


def a_part(index, *, offset_seconds, duration=1500.0, size=16 * GIB, probe_state="ok"):
    return MergePart(
        media_file_id=f"id-{index}",
        rel_path=f"library/dji-osmo/DCIM/DJI_001/DJI_{index:04d}_D.MP4",
        sha1=f"sha-{index}",
        captured_at=BASE + timedelta(seconds=offset_seconds),
        duration_seconds=duration,
        size_bytes=size,
        probe_state=probe_state,
    )


def test_two_parts_within_the_tolerance_form_one_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1502)]
    groups = detect_groups(parts, a_rule())
    assert len(groups) == 1
    assert [p.media_file_id for p in groups[0].members] == ["id-1", "id-2"]
    assert groups[0].gaps == (2.0,)


def test_a_gap_beyond_the_tolerance_splits_the_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1506)]
    assert detect_groups(parts, a_rule()) == []


def test_a_small_previous_part_splits_the_group():
    # 直前が min_part_size_gib 未満なら、時刻が続いていても別の録画。
    parts = [a_part(1, offset_seconds=0, size=1 * GIB), a_part(2, offset_seconds=1502)]
    assert detect_groups(parts, a_rule()) == []


def test_an_overlap_splits_the_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1499)]
    assert detect_groups(parts, a_rule()) == []


def test_a_failed_probe_is_a_boundary():
    # 失敗したパートを列から取り除くだけだと、その前後がつながって
    # 別の録画が 1 つのグループになる。差が tolerance に収まる並びで確かめる。
    parts = [
        a_part(1, offset_seconds=0),
        a_part(2, offset_seconds=1501, duration=None, probe_state="failed"),
        a_part(3, offset_seconds=1502),
    ]
    assert detect_groups(parts, a_rule()) == []


def test_a_boundary_does_not_stop_the_scan():
    # 切った後の並びからも候補が出る。切って終わりにしない。
    parts = [
        a_part(1, offset_seconds=0),
        a_part(2, offset_seconds=100_000),
        a_part(3, offset_seconds=101_502),
    ]
    groups = detect_groups(parts, a_rule())
    assert len(groups) == 1
    assert [p.media_file_id for p in groups[0].members] == ["id-2", "id-3"]


def test_three_parts_form_one_group_with_two_gaps():
    parts = [
        a_part(1, offset_seconds=0),
        a_part(2, offset_seconds=1502),
        a_part(3, offset_seconds=3003),
    ]
    groups = detect_groups(parts, a_rule())
    assert len(groups) == 1
    assert len(groups[0].members) == 3
    assert groups[0].gaps == (2.0, 1.0)


def test_a_single_part_is_not_a_candidate():
    assert detect_groups([a_part(1, offset_seconds=0)], a_rule()) == []


def test_a_disabled_rule_detects_nothing():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1502)]
    assert detect_groups(parts, a_rule(enabled=False)) == []
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_merge_grouping.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.core.merge'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/core/merge/__init__.py`:

```python
from __future__ import annotations
```

`app/src/mediaferry/core/merge/grouping.py`:

```python
"""分割録画のグループ検出（§9.7）.

同一録画と判定する条件は 2 つ。直前ファイルの終端（開始時刻 + duration）と
次ファイルの開始時刻の差が `tolerance_seconds` 以内であること、かつ直前
ファイルのサイズが `min_part_size_gib` 以上であること。第 2 条件は、DJI が
~16GiB で自動分割することを利用して「分割」と「連続した別録画」を区別する。

OS も DB も知らない。呼び出し側が並べた列を受け取り、境界で切るだけにする。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ..profiles.model import MergeRule

GIB = 1024**3


@dataclass(frozen=True)
class MergePart:
    media_file_id: str
    rel_path: str
    sha1: str
    captured_at: datetime
    duration_seconds: float | None
    size_bytes: int
    probe_state: str


@dataclass(frozen=True)
class GroupCandidate:
    """2 件以上のパートからなるグループ候補.

    `gaps` は継ぎ目ごとの差（秒）で、`members[i]` の終端と `members[i+1]` の
    開始の差が `gaps[i]` にあたる。画面で「なぜグループ化されたか」を示す。
    """

    members: tuple[MergePart, ...]
    gaps: tuple[float, ...]


def detect_groups(parts: Sequence[MergePart], rule: MergeRule) -> list[GroupCandidate]:
    """`parts` は開始時刻の昇順であること. 並べ替えは呼び出し側の責務."""
    if not rule.enabled:
        return []
    minimum = rule.min_part_size_gib * GIB
    groups: list[GroupCandidate] = []
    current: list[MergePart] = []
    gaps: list[float] = []

    for part in parts:
        if part.probe_state != "ok" or part.duration_seconds is None:
            # duration が無いファイルは境界。前後をつなぐ根拠が無い。
            groups.extend(_flush(current, gaps))
            current, gaps = [], []
            continue
        if not current:
            current = [part]
            continue
        previous = current[-1]
        gap = _gap_seconds(previous, part)
        if previous.size_bytes < minimum or gap < 0 or gap > rule.tolerance_seconds:
            # オーバーラップ（差が負）も別の録画として扱う。
            groups.extend(_flush(current, gaps))
            current, gaps = [part], []
            continue
        current.append(part)
        gaps.append(gap)

    groups.extend(_flush(current, gaps))
    return groups


def _gap_seconds(previous: MergePart, following: MergePart) -> float:
    """直前の終端から次の開始までの差. `previous.duration_seconds` は非 None."""
    end = previous.captured_at.timestamp() + previous.duration_seconds
    return following.captured_at.timestamp() - end


def _flush(current: list[MergePart], gaps: list[float]) -> list[GroupCandidate]:
    if len(current) < 2:
        return []
    return [GroupCandidate(members=tuple(current), gaps=tuple(gaps))]
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_merge_grouping.py -q`
Expected: PASS（10 件）

- [ ] **Step 5: 変異試験**

`PYTHONDONTWRITEBYTECODE=1` を付けて、1 つずつ壊して対応するテストが落ちることを確認し、
確認できたら戻す。

| 変異 | 落ちるべきテスト |
| --- | --- |
| `gap < 0` を消す | `test_an_overlap_splits_the_group` |
| `gap > rule.tolerance_seconds` を `>=` にする | `test_two_parts_within_the_tolerance_form_one_group` は差 2.0 なので落ちない。**差が tolerance ちょうどのケースを足す**（`offset_seconds=1505` で 1 グループ） |
| `previous.size_bytes < minimum` を消す | `test_a_small_previous_part_splits_the_group` |
| `_flush` の `< 2` を `< 1` にする | `test_a_single_part_is_not_a_candidate` |
| `current, gaps = [part], []` を `current, gaps = [], []` にする | `test_a_boundary_does_not_stop_the_scan` |
| `if not rule.enabled` を消す | `test_a_disabled_rule_detects_nothing` |
| probe 境界で `current` を捨てずに続ける | `test_a_failed_probe_is_a_boundary`。**当初の並び（失敗パートの次が +3004 秒）では素通りした** —— 失敗パートを飛ばしても前後の差が tolerance を超えるので、変異の有無にかかわらず `[]` になる。前後がつながる並び（+1501 / +1502）に直した |

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_merge_grouping.py -q
```

境界ちょうどのテストを足す:

```python
def test_a_gap_exactly_at_the_tolerance_stays_in_the_group():
    parts = [a_part(1, offset_seconds=0), a_part(2, offset_seconds=1505)]
    assert len(detect_groups(parts, a_rule())) == 1
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/core/merge app/tests/test_merge_grouping.py
git commit -m "feat(mediaferry): detect split recordings by their boundaries"
```

---

### Task 2: 入力ダイジェスト

**Files:**
- Create: `app/src/mediaferry/core/merge/digest.py`
- Test: `app/tests/test_merge_digest.py`

**Interfaces:**
- Consumes: `MergeRule`
- Produces: `input_digest(members: Sequence[tuple[str, str]], rule: MergeRule, profile_revision_id: str) -> str`（`(media_file_id, sha1)` の順序付き列を受ける）、`DIGEST_VERSION: int`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merge_digest.py`:

```python
from mediaferry.core.merge.digest import input_digest
from mediaferry.core.profiles.model import KeepStreams, MergeRule

MEMBERS = [("id-1", "sha-1"), ("id-2", "sha-2")]


def a_rule(**overrides):
    values = {
        "enabled": True,
        "tolerance_seconds": 5,
        "min_part_size_gib": 15,
        "sequence_pattern": r"_(?P<seq>\d{4})_D$",
        "output_name": "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4",
        "keep_streams": KeepStreams(video="primary", audio="all", timecode=True, data=False),
    }
    values.update(overrides)
    return MergeRule(**values)


def test_the_digest_is_deterministic():
    assert input_digest(MEMBERS, a_rule(), "rev-1") == input_digest(MEMBERS, a_rule(), "rev-1")


def test_the_order_of_the_members_changes_the_digest():
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(
        list(reversed(MEMBERS)), a_rule(), "rev-1"
    )


def test_a_changed_content_hash_changes_the_digest():
    other = [("id-1", "sha-1"), ("id-2", "sha-2-edited")]
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(other, a_rule(), "rev-1")


def test_a_changed_merge_setting_changes_the_digest():
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(
        MEMBERS, a_rule(tolerance_seconds=6), "rev-1"
    )


def test_a_nested_keep_streams_change_changes_the_digest():
    changed = KeepStreams(video="primary", audio="all", timecode=True, data=True)
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(
        MEMBERS, a_rule(keep_streams=changed), "rev-1"
    )


def test_a_changed_profile_revision_changes_the_digest():
    assert input_digest(MEMBERS, a_rule(), "rev-1") != input_digest(MEMBERS, a_rule(), "rev-2")


def test_the_digest_is_a_sha256_hex_string():
    digest = input_digest(MEMBERS, a_rule(), "rev-1")
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_merge_digest.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/core/merge/digest.py`:

```python
"""結合グループの入力ダイジェスト.

構成ファイルの順序付きの id と sha1、結合設定、プロファイルリビジョンから
決定的に作る。**順序が変われば値も変わる。**

グループを手動で編集した後に旧派生物が選択肢へ戻る経路を、この値の一致
判定で塞ぐ（§10）。旧グループは `status = merged` のままなので、これが
無いと候補に残る。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict

from ..profiles.model import MergeRule

# 算出方法を変えたら上げる。上げると既存グループの digest が一致しなくなり、
# 派生物が既定の選択肢から外れる（安全側に倒れる）。
DIGEST_VERSION = 1


def input_digest(
    members: Sequence[tuple[str, str]], rule: MergeRule, profile_revision_id: str
) -> str:
    """`members` は `(media_file_id, sha1)` を position の順に並べたもの."""
    payload = {
        "version": DIGEST_VERSION,
        "members": [{"media_file_id": media_id, "sha1": sha1} for media_id, sha1 in members],
        "merge": asdict(rule),
        "profile_revision_id": profile_revision_id,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_merge_digest.py -q`
Expected: PASS（7 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `members` をリストから `sorted(...)` にする | `test_the_order_of_the_members_changes_the_digest`。**`sorted` を掛ける位置に注意**。内包の外（dict の列）に掛けると dict が比較不能で全件 TypeError になり、順序を検証したことにならない。`sorted(members)` としてタプルの段で並べ替える |
| payload から `"merge"` を落とす | `test_a_changed_merge_setting_changes_the_digest` |
| payload から `"profile_revision_id"` を落とす | `test_a_changed_profile_revision_changes_the_digest` |
| `sha1` を payload から落とす | `test_a_changed_content_hash_changes_the_digest` |
| `sort_keys=True` を外す | **落ちない**（dict のキー順が実行間で変わらないため）。これは検出できない変異として記録する。将来キーを足したときに順序が揺れないための保険であり、単一プロセス内では差が出ない |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/core/merge/digest.py app/tests/test_merge_digest.py
git commit -m "feat(mediaferry): derive a deterministic digest for merge inputs"
```

---

### Task 3: 保持するストリームの決定

**Files:**
- Create: `app/src/mediaferry/core/merge/streams.py`
- Test: `app/tests/test_merge_streams.py`

**Interfaces:**
- Consumes: `mediaferry.core.profiles.model.KeepStreams`
- Produces:
  - `selected_streams(streams: Sequence[dict[str, Any]], keep: KeepStreams) -> list[dict[str, Any]]`
  - `stream_signature(streams: Sequence[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]`
  - `map_arguments(streams: Sequence[dict[str, Any]]) -> list[str]`
  - `stream_summary(stream: dict[str, Any]) -> dict[str, Any]`
  - `TIMECODE_TAG: str`

**なぜ独立したモジュールか:** `-map` の組み立て（`adapters/ffmpeg.py`）と検証
（`core/merge/verify.py`）が**同じ判断**を使う。2 か所に書くと、保持したつもりの
ストリームと検証が期待するストリームがずれる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merge_streams.py`:

```python
from mediaferry.core.merge.streams import map_arguments, selected_streams, stream_signature
from mediaferry.core.profiles.model import KeepStreams

# Phase 0 の実測（DJI Osmo Pocket 4 の MP4 は 6 ストリーム）を写したもの。
DJI_STREAMS = [
    {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1",
     "bit_rate": "79924667", "nb_frames": "45540"},
    {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a",
     "bit_rate": "317374"},
    {"index": 2, "codec_type": "data", "codec_name": "bin_data", "codec_tag_string": "djmd",
     "bit_rate": "11300"},
    {"index": 3, "codec_type": "data", "codec_name": "bin_data", "codec_tag_string": "dbgi",
     "bit_rate": "10300000"},
    {"index": 4, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"},
    {"index": 5, "codec_type": "video", "codec_name": "mjpeg", "codec_tag_string": "",
     "disposition": {"attached_pic": 1}},
]

DJI_KEEP = KeepStreams(video="primary", audio="all", timecode=True, data=False)


def test_the_dji_profile_keeps_video_audio_and_timecode():
    kept = selected_streams(DJI_STREAMS, DJI_KEEP)
    assert [s["index"] for s in kept] == [0, 1, 4]


def test_data_tracks_are_kept_when_the_profile_asks_for_them():
    keep = KeepStreams(video="primary", audio="all", timecode=True, data=True)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0, 1, 2, 3, 4]


def test_the_timecode_track_is_dropped_independently_of_the_other_data_tracks():
    keep = KeepStreams(video="primary", audio="all", timecode=False, data=True)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0, 1, 2, 3]


def test_an_attached_thumbnail_is_not_counted_as_video():
    keep = KeepStreams(video="all", audio="none", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0]


def test_primary_audio_keeps_only_the_first_track():
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    keep = KeepStreams(video="primary", audio="primary", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(streams, keep)] == [0, 1]


def test_audio_can_be_dropped_entirely():
    keep = KeepStreams(video="primary", audio="none", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(DJI_STREAMS, keep)] == [0]


def test_the_result_keeps_the_original_stream_order():
    keep = KeepStreams(video="primary", audio="all", timecode=True, data=True)
    indexes = [s["index"] for s in selected_streams(DJI_STREAMS, keep)]
    assert indexes == sorted(indexes)


def test_the_signature_covers_type_codec_and_tag():
    assert stream_signature(selected_streams(DJI_STREAMS, DJI_KEEP)) == (
        ("video", "hevc", "hvc1"),
        ("audio", "aac", "mp4a"),
        ("data", "none", "tmcd"),
    )


def test_map_arguments_use_the_absolute_index():
    assert map_arguments(selected_streams(DJI_STREAMS, DJI_KEEP)) == [
        "-map", "0:0", "-map", "0:1", "-map", "0:4",
    ]


def test_the_same_signature_can_have_different_absolute_indexes():
    """並びが違えば、同じ signature でも map は違う. 使い回してはいけない."""
    reordered = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "data", "codec_name": "bin_data",
         "codec_tag_string": "dbgi"},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    keep = KeepStreams(video="primary", audio="all", timecode=False, data=False)
    plain = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"},
    ]
    assert stream_signature(selected_streams(reordered, keep)) == stream_signature(
        selected_streams(plain, keep)
    )
    assert map_arguments(selected_streams(reordered, keep)) != map_arguments(
        selected_streams(plain, keep)
    )


def test_the_summary_keeps_what_the_screen_needs():
    assert stream_summary(DJI_STREAMS[3]) == {
        "index": 3, "codec_type": "data", "codec_name": "bin_data",
        "codec_tag_string": "dbgi", "bit_rate": "10300000",
    }
```

先頭の import に `stream_summary` を足す:

```python
from mediaferry.core.merge.streams import (
    map_arguments,
    selected_streams,
    stream_signature,
    stream_summary,
)
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_merge_streams.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/core/merge/streams.py`:

```python
"""保持するストリームの決定（§9.8）.

**ffmpeg の暗黙の選択に任せない。** 任せると「何が保持されたか」が出力を
見るまで分からず、誤ったストリームを選んだ出力を、その出力自身を基準に
合格させてしまう。ここで決めた集合を `-map` にも検証にも使う。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..profiles.model import KeepStreams

TIMECODE_TAG = "tmcd"


def selected_streams(
    streams: Sequence[dict[str, Any]], keep: KeepStreams
) -> list[dict[str, Any]]:
    """保持対象を、入力のストリーム順で返す."""
    kept: list[dict[str, Any]] = []

    videos = [s for s in streams if s.get("codec_type") == "video" and not _is_thumbnail(s)]
    kept.extend(videos[:1] if keep.video == "primary" else videos)

    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if keep.audio == "primary":
        audios = audios[:1]
    elif keep.audio == "none":
        audios = []
    kept.extend(audios)

    for stream in streams:
        if stream.get("codec_type") != "data":
            continue
        is_timecode = stream.get("codec_tag_string") == TIMECODE_TAG
        if keep.timecode if is_timecode else keep.data:
            kept.append(stream)

    return sorted(kept, key=lambda s: s["index"])


def stream_signature(streams: Sequence[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    """本数と codec の一致を比べるための署名."""
    return tuple(
        (
            str(s.get("codec_type", "")),
            str(s.get("codec_name", "")),
            str(s.get("codec_tag_string", "")),
        )
        for s in streams
    )


def map_arguments(streams: Sequence[dict[str, Any]]) -> list[str]:
    """`-map 0:<index>` の列. 絶対 index で指定し、選択を曖昧にしない.

    **index はそのストリームが属するファイルのもの。** 別のパートへ使い回すと、
    保持対象の並びが違うファイルで別のストリームを選ぶ。
    """
    args: list[str] = []
    for stream in streams:
        args.extend(["-map", f"0:{stream['index']}"])
    return args


def stream_summary(stream: dict[str, Any]) -> dict[str, Any]:
    """記録・表示用の要約. 検証と ffmpeg アダプタが同じ形で残す."""
    return {
        "index": stream.get("index"),
        "codec_type": stream.get("codec_type"),
        "codec_name": stream.get("codec_name"),
        "codec_tag_string": stream.get("codec_tag_string"),
        "bit_rate": stream.get("bit_rate"),
    }


def _is_thumbnail(stream: dict[str, Any]) -> bool:
    """埋め込みサムネイル（`attached_pic`）を映像として数えない."""
    return bool(stream.get("disposition", {}).get("attached_pic"))
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_merge_streams.py -q`
Expected: PASS（11 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `videos[:1]` を `videos` にする | `test_an_attached_thumbnail_is_not_counted_as_video` は落ちない（サムネイルは既に除外済み）。**`video="primary"` で本物の映像が 2 本あるケースを足す** |
| `_is_thumbnail` を常に `False` にする | `test_an_attached_thumbnail_is_not_counted_as_video` |
| `keep.timecode if is_timecode else keep.data` の 2 項を入れ替える | `test_the_timecode_track_is_dropped_independently_of_the_other_data_tracks` |
| `sorted(..., key=index)` を外す | `test_the_result_keeps_the_original_stream_order` も `test_the_dji_profile_keeps_video_audio_and_timecode` も**落ちない**。DJI の並びは video → audio → data なので、種別ごとに集めた順が index 順とたまたま一致する。**音声が映像より前にあるファイル**のテスト（`test_the_result_is_ordered_by_the_index_not_by_the_type`）を足して検出した |
| `stream_signature` から `codec_tag_string` を落とす | `test_the_signature_covers_type_codec_and_tag` |

映像 2 本のテストを足す:

```python
def test_primary_video_keeps_only_the_first_real_video():
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"},
    ]
    keep = KeepStreams(video="primary", audio="none", timecode=False, data=False)
    assert [s["index"] for s in selected_streams(streams, keep)] == [0]
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/core/merge/streams.py app/tests/test_merge_streams.py
git commit -m "feat(mediaferry): declare which streams a merge keeps"
```

**実機で確かめること（`phase1-manual-checklist.md` へ追記する）:** DJI の
`mjpeg` サムネイルに `disposition.attached_pic` が立っているか。立っていない
機種があれば `_is_thumbnail` の判定を足す必要がある。`keep_streams.video` が
`primary` の間は影響しない（最初の 1 本しか採らない）。

---

### Task 4: 出力名と `derived/` の置き場所

**Files:**
- Create: `app/src/mediaferry/core/merge/output.py`
- Test: `app/tests/test_merge_output.py`

**Interfaces:**
- Consumes: `MergePart`（Task 1）、`MergeRule`、`mediaferry.core.naming.library_rel_path`（既存）
- Produces:
  - `merged_rel_path(profile_slug: str, rule: MergeRule, members: Sequence[MergePart]) -> str`
  - `MergeOutputUndefined(ValueError)`
  - `TS_FORMAT: str`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merge_output.py`:

```python
from datetime import UTC, datetime, timedelta, timezone

import pytest

from mediaferry.core.merge.grouping import GIB, MergePart
from mediaferry.core.merge.output import MergeOutputUndefined, merged_rel_path
from mediaferry.core.profiles.model import KeepStreams, MergeRule


def a_rule(**overrides):
    values = {
        "enabled": True,
        "tolerance_seconds": 5,
        "min_part_size_gib": 15,
        "sequence_pattern": r"_(?P<seq>\d{4})_D$",
        "output_name": "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4",
        "keep_streams": KeepStreams(video="primary", audio="all", timecode=True, data=False),
    }
    values.update(overrides)
    return MergeRule(**values)


def a_part(name, *, captured_at=None, directory="DCIM/DJI_001"):
    return MergePart(
        media_file_id="id",
        rel_path=f"library/dji-osmo/{directory}/{name}",
        sha1="sha",
        captured_at=captured_at or datetime(2026, 8, 17, 14, 30, 0, tzinfo=UTC),
        duration_seconds=1500.0,
        size_bytes=16 * GIB,
        probe_state="ok",
    )


MEMBERS = [
    a_part("DJI_20260817143000_0001_D.MP4"),
    a_part("DJI_20260817145500_0002_D.MP4"),
]


def test_the_output_name_follows_the_profile_template():
    got = merged_rel_path("dji-osmo", a_rule(), MEMBERS)
    assert got == "derived/dji-osmo/DCIM/DJI_001/DJI_20260817143000_0001-0002_MERGED.MP4"


def test_the_directory_layout_mirrors_the_card():
    members = [
        a_part("DJI_20260817143000_0001_D.MP4", directory="DCIM/DJI_002"),
        a_part("DJI_20260817145500_0002_D.MP4", directory="DCIM/DJI_002"),
    ]
    assert merged_rel_path("dji-osmo", a_rule(), members).startswith(
        "derived/dji-osmo/DCIM/DJI_002/"
    )


def test_the_timestamp_is_the_local_wall_clock_of_the_first_part():
    # captured_at はオフセット付きで保存されている。UTC へ直さず、そのままの
    # 壁時計を名前に使う（library 側の名前と読み比べられる形にする）。
    tokyo = timezone(timedelta(hours=9))
    members = [
        a_part("DJI_20260817143000_0001_D.MP4", captured_at=datetime(2026, 8, 17, 14, 30, tzinfo=tokyo)),
        a_part("DJI_20260817145500_0002_D.MP4"),
    ]
    assert "DJI_20260817143000_" in merged_rel_path("dji-osmo", a_rule(), members)


def test_an_unreadable_sequence_is_refused():
    members = [a_part("PANO_0001.JPG"), a_part("PANO_0002.JPG")]
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(), members)


def test_an_unknown_placeholder_is_refused():
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(output_name="{unknown}.MP4"), MEMBERS)


def test_a_path_outside_the_library_is_refused():
    members = [
        MergePart("id", "derived/dji-osmo/x.MP4", "sha", MEMBERS[0].captured_at, 1.0, 1, "ok"),
        MEMBERS[1],
    ]
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(), members)


def test_a_rendered_separator_is_refused():
    # プロファイルの検証をすり抜けた値でも、展開後にもう一度確かめる。
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(output_name="../{first_seq}.MP4"), MEMBERS)
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_merge_output.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/core/merge/output.py`:

```python
"""結合結果の名前と置き場所.

`output_name` はプロファイルの値で、置換するのは `{ts}` `{first_seq}`
`{last_seq}` の 3 つだけにする。`str.format` を使わないのは、テンプレートから
値の属性を辿れてしまうため。置換できるキーをここで閉じる。

置き場所はカード上の階層を保つ（§7）。ユーザが NAS を直接開いて
`library/` と `derived/` を読み比べられることを保証する。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from ..naming import UnsafePath, library_rel_path, safe_source_rel_path
from ..profiles.model import MergeRule
from .grouping import MergePart

TS_FORMAT = "%Y%m%d%H%M%S"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class MergeOutputUndefined(ValueError):
    """出力名を決められない（連番が読めない、未知のプレースホルダ、範囲外のパス）."""


def merged_rel_path(profile_slug: str, rule: MergeRule, members: Sequence[MergePart]) -> str:
    first, last = members[0], members[-1]
    name = _render(
        rule.output_name,
        {
            # captured_at はオフセット付き。UTC へ直さず、そのままの壁時計を使う。
            "ts": first.captured_at.strftime(TS_FORMAT),
            "first_seq": _sequence(rule, first.rel_path),
            "last_seq": _sequence(rule, last.rel_path),
        },
    )
    parent = _source_parent(profile_slug, first.rel_path)
    return library_rel_path("derived", profile_slug, str(parent / name))


def _sequence(rule: MergeRule, rel_path: str) -> str:
    match = re.search(rule.sequence_pattern, PurePosixPath(rel_path).stem)
    if match is None:
        raise MergeOutputUndefined(f"連番が読めない: {rel_path}")
    try:
        return match.group("seq")
    except IndexError as exc:
        raise MergeOutputUndefined("sequence_pattern に seq グループが無い") from exc


def _source_parent(profile_slug: str, rel_path: str) -> PurePosixPath:
    path = PurePosixPath(rel_path)
    prefix = PurePosixPath("library") / profile_slug
    if prefix not in path.parents:
        raise MergeOutputUndefined(f"ライブラリの外のパス: {rel_path}")
    return path.parent.relative_to(prefix)


def _render(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise MergeOutputUndefined(f"未知のプレースホルダ: {{{key}}}")
        return values[key]

    rendered = _PLACEHOLDER.sub(replace, template)
    try:
        checked = safe_source_rel_path(rendered)
    except UnsafePath as exc:
        raise MergeOutputUndefined(str(exc)) from exc
    if "/" in checked:
        raise MergeOutputUndefined(f"出力名が単一の構成要素ではない: {rendered}")
    return checked
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_merge_output.py -q`
Expected: PASS（7 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `first.captured_at` を `last.captured_at` にする | `test_the_output_name_follows_the_profile_template`。ただし **`MEMBERS` の 2 件が同じ `captured_at` だと素通りする**（`a_part` の既定値のまま）。2 件目に `14:55` を持たせて先頭と末尾の差が出るようにした |
| `_sequence(rule, last.rel_path)` を `first` にする | 同上（`0001-0001` になる） |
| `key not in values` の判定を消す | `test_an_unknown_placeholder_is_refused` |
| `prefix not in path.parents` を消す | `test_a_path_outside_the_library_is_refused`。ただし **`x.MP4` のままだと素通りする** —— `_sequence` が先に落ちて置き場所の判定を一度も通らない。連番が読める名前（`.../DJI_20260817143000_0001_D.MP4`）に直した |
| `safe_source_rel_path` の検査を外す | `test_a_rendered_separator_is_refused` は**落ちない**（`../0001.MP4` は後段の `"/" in checked` が拾う）。バックスラッシュを含む名前のテスト（`test_a_rendered_backslash_is_refused`）を足して、`UnsafePath` ではなく `MergeOutputUndefined` で返すことを固定した |
| 連番が読めないときに落とさない | `test_an_unreadable_sequence_is_refused` |
| `"/" in checked` の判定を消す | `test_a_rendered_separator_is_refused` は `..` を `safe_source_rel_path` が先に弾くので落ちない。**`output_name="{first_seq}/x.MP4"` のケースを足す** |
| `.strftime(TS_FORMAT)` を UTC へ正規化してから行う | `test_the_timestamp_is_the_local_wall_clock_of_the_first_part` |

セパレータのテストを足す:

```python
def test_a_rendered_slash_is_refused():
    with pytest.raises(MergeOutputUndefined):
        merged_rel_path("dji-osmo", a_rule(output_name="{first_seq}/x.MP4"), MEMBERS)
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/core/merge/output.py app/tests/test_merge_output.py
git commit -m "feat(mediaferry): name merged outputs from the profile template"
```

---

### Task 5: 結合結果の検証

**Files:**
- Create: `app/src/mediaferry/core/merge/verify.py`
- Test: `app/tests/test_merge_verify.py`

**Interfaces:**
- Consumes: `selected_streams` / `stream_signature`（Task 3）、`KeepStreams`
- Produces:
  - `ProbedFile(duration_seconds: float | None, size_bytes: int, streams: tuple[dict[str, Any], ...])`
  - `Check(name: str, verdict: str, detail: dict[str, Any])` — `verdict` は `pass` / `fail` / `inconclusive`
  - `Verification(passed: bool, route: str, pipeline_version: int, checks: tuple[Check, ...], dropped_streams: tuple[dict[str, Any], ...], route_dropped_streams: tuple[dict[str, Any], ...], seam_offsets: tuple[float, ...])` と `Verification.to_json() -> str`
  - `verify(parts: Sequence[ProbedFile], merged: ProbedFile, keep: KeepStreams, route: str, route_dropped: Sequence[dict[str, Any]] = ()) -> Verification`
  - 定数 `PIPELINE_VERSION` / `DURATION_TOLERANCE_PER_PART` / `FRAME_ALLOWANCE_PER_SEAM` / `FRAME_ALLOWANCE_BASE` / `SIZE_TOLERANCE` / `BITRATE_SPREAD_LIMIT` / `ESTIMABLE_TYPES`

**設計に無い実値をここで決める（`design.md` へ書き戻す）:**

- **`BITRATE_SPREAD_LIMIT = 0.1`** — §9.8 の「各パートの `bit_rate` のばらつきが
  小さく、平均値として信用できる」の実装。分散ではなく範囲（`(max - min) / mean`）
  で見るのは、パートが 2 本のときでも意味を持つため。**対応するストリームごとに
  評価する。** 保持ストリームの合計で見ると、支配的な映像（80 Mbps）が音声
  （317 kbps）の大きな変動を隠す。
- **フレーム数は `nb_frames` だけを見る。** `-count_frames` は 30 GiB を全デコード
  するので使わない。取れないパートが 1 つでもあれば `inconclusive`。
- **期待サイズは `bit_rate` が取れた保持ストリームだけで組み立てる。**
  `tmcd`（タイムコード）は `bit_rate` を持たないことが多く、そこで全体を
  `inconclusive` にすると、**既定の DJI プロファイル（`timecode: true`）では
  サイズ検査が常に無効になる**（Phase 0 で直した検査が既定で死ぬ）。
  取れなかったのが `video` / `audio`（`ESTIMABLE_TYPES`）なら支配的なので
  `inconclusive`、`data` だけなら推定を続け、除外したストリームを `detail` に残す。
- **`PIPELINE_VERSION = 1`** — 検証器の版。`verification_json` に入れる。
  閾値や判定を変えたら上げる。**`input_digest` には入れない**（下記）。

**`input_digest` に検証器の版を入れない（codex の指摘を退けた）:** `input_digest` は
§8 で「構成ファイルの ordered な id と sha1、結合設定、プロファイルリビジョン」と
定義され、その役割は §10 のとおり**入力の同一性**の判定（グループを編集した後に
旧派生物が選択肢へ戻る経路を塞ぐ）。検証の閾値はプロファイルの `merge` 節にも無く、
入力ではない。ここへ混ぜると、閾値を 1 つ変えただけで**既存の結合物がそろって
既定の選択肢から消え、再結合するまで戻らない**。検証器の版は
`verification_json.pipeline_version` に残し、画面で「古い版で検証された」と
示せるようにする。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merge_verify.py`:

```python
import json

from mediaferry.core.merge.verify import ProbedFile, verify
from mediaferry.core.profiles.model import KeepStreams

KEEP = KeepStreams(video="primary", audio="all", timecode=True, data=False)


def a_part(duration=1500.0, size=16_000_000_000, *, video_rate="79924667", frames="45000",
           audio_rate="317374", extra=()):
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1",
         "bit_rate": video_rate, "nb_frames": frames},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a",
         "bit_rate": audio_rate},
        {"index": 2, "codec_type": "data", "codec_name": "bin_data",
         "codec_tag_string": "dbgi", "bit_rate": "10300000"},
    ]
    streams.extend(extra)
    return ProbedFile(duration_seconds=duration, size_bytes=size, streams=tuple(streams))


def a_merged(duration=3000.0, size=None, *, frames="89999", streams=None):
    if streams is None:
        streams = [
            {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1",
             "bit_rate": "79924667", "nb_frames": frames},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a",
             "bit_rate": "317374"},
        ]
    if size is None:
        size = int((79924667 + 317374) * duration / 8)
    return ProbedFile(duration_seconds=duration, size_bytes=size, streams=tuple(streams))


def verdicts(result):
    return {check.name: check.verdict for check in result.checks}


def test_a_clean_merge_passes_every_check():
    result = verify([a_part(), a_part()], a_merged(), KEEP, "concat")
    assert result.passed
    assert verdicts(result) == {
        "duration": "pass", "streams": "pass", "frames": "pass", "size": "pass"
    }


def test_a_duration_beyond_the_tolerance_fails():
    result = verify([a_part(), a_part()], a_merged(duration=3010.0), KEEP, "concat")
    assert verdicts(result)["duration"] == "fail"
    assert not result.passed


def test_the_duration_tolerance_scales_with_the_part_count():
    parts = [a_part(), a_part(), a_part()]
    merged = a_merged(duration=4500.0 + 2.5)
    assert verdicts(verify(parts, merged, KEEP, "concat"))["duration"] == "pass"


def test_a_missing_kept_stream_fails():
    merged = a_merged(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1",
         "bit_rate": "79924667", "nb_frames": "89999"},
    ])
    result = verify([a_part(), a_part()], merged, KEEP, "concat")
    assert verdicts(result)["streams"] == "fail"
    assert not result.passed


def test_parts_that_disagree_with_each_other_fail():
    other = a_part()
    changed = list(other.streams)
    changed[0] = {**changed[0], "codec_name": "h264"}
    result = verify([a_part(), ProbedFile(1500.0, 16_000_000_000, tuple(changed))],
                    a_merged(), KEEP, "concat")
    assert verdicts(result)["streams"] == "fail"


def test_dropped_streams_are_recorded():
    result = verify([a_part(), a_part()], a_merged(), KEEP, "concat")
    assert [s["codec_tag_string"] for s in result.dropped_streams] == ["dbgi"]


def test_seam_offsets_are_the_cumulative_boundaries():
    result = verify([a_part(), a_part(duration=1200.0)], a_merged(duration=2700.0), KEEP, "concat")
    assert result.seam_offsets == (1500.0,)


def test_lost_frames_within_the_allowance_pass():
    # 2 パートなら許容は 2 * (2 - 1) + 2 = 4 フレーム。
    result = verify([a_part(frames="45000"), a_part(frames="45000")],
                    a_merged(frames="89996"), KEEP, "concat")
    assert verdicts(result)["frames"] == "pass"


def test_lost_frames_beyond_the_allowance_fail():
    result = verify([a_part(frames="45000"), a_part(frames="45000")],
                    a_merged(frames="89995"), KEEP, "concat")
    assert verdicts(result)["frames"] == "fail"


def test_missing_frame_counts_are_inconclusive_not_failed():
    parts = [a_part(frames=None), a_part()]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["frames"] == "inconclusive"
    assert result.passed


def test_a_missing_video_bit_rate_makes_the_size_check_inconclusive():
    parts = [a_part(video_rate=None), a_part()]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "inconclusive"
    assert result.passed


def test_a_timecode_without_a_bit_rate_does_not_disable_the_size_check():
    """既定の DJI プロファイルは timecode を保持する. tmcd に bit_rate は無い.

    ここで全体を inconclusive にすると、Phase 0 で直したサイズ検査が既定で
    毎回死ぬ。推定から外して、外したことを detail に残す。
    """
    tmcd = {"index": 3, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"}
    parts = [a_part(extra=[tmcd]), a_part(extra=[tmcd])]
    merged_streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1",
         "bit_rate": "79924667", "nb_frames": "89999"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a",
         "bit_rate": "317374"},
        {"index": 2, "codec_type": "data", "codec_name": "none", "codec_tag_string": "tmcd"},
    ]
    result = verify(parts, a_merged(streams=merged_streams), KEEP, "concat")
    assert verdicts(result)["size"] == "pass"
    excluded = result.checks[3].detail["excluded_streams"]
    assert [s["codec_tag_string"] for s in excluded] == ["tmcd"]


def test_a_wide_bit_rate_spread_makes_the_size_check_inconclusive():
    parts = [a_part(video_rate="79924667"), a_part(video_rate="40000000")]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "inconclusive"


def test_a_spread_hidden_by_the_dominant_stream_is_still_caught():
    """合計で見ると、80 Mbps の映像が音声の 2 倍の変動を隠す."""
    parts = [a_part(audio_rate="317374"), a_part(audio_rate="634748")]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "inconclusive"


def test_the_size_is_compared_against_the_kept_streams_only():
    # データトラック（dbgi 10.3 Mbps）を足しても期待サイズは変わらない。
    parts = [a_part(), a_part()]
    result = verify(parts, a_merged(), KEEP, "concat")
    assert verdicts(result)["size"] == "pass"


def test_a_size_beyond_the_tolerance_fails():
    parts = [a_part(), a_part()]
    merged = a_merged(size=int((79924667 + 317374) * 3000.0 / 8 * 1.05))
    assert verdicts(verify(parts, merged, KEEP, "concat"))["size"] == "fail"


def test_the_ts_route_allows_a_wider_size_difference():
    parts = [a_part(), a_part()]
    merged = a_merged(size=int((79924667 + 317374) * 3000.0 / 8 * 1.04))
    assert verdicts(verify(parts, merged, KEEP, "concat"))["size"] == "fail"
    assert verdicts(verify(parts, merged, KEEP, "ts"))["size"] == "pass"


def test_streams_dropped_by_the_route_are_recorded():
    """TS 経路が運べずに外したストリームは、脱落の理由が違うので分けて残す."""
    dropped = [{"index": 4, "codec_type": "data", "codec_name": "none",
                "codec_tag_string": "tmcd"}]
    result = verify([a_part(), a_part()], a_merged(), KEEP, "ts", route_dropped=dropped)
    assert [s["codec_tag_string"] for s in result.route_dropped_streams] == ["tmcd"]


def test_the_result_serialises_to_json():
    result = verify([a_part(), a_part()], a_merged(), KEEP, "concat")
    payload = json.loads(result.to_json())
    assert payload["passed"] is True
    assert payload["route"] == "concat"
    assert payload["pipeline_version"] == 1
    assert payload["seam_offsets"] == [1500.0]
    assert {c["name"] for c in payload["checks"]} == {"duration", "streams", "frames", "size"}
    assert payload["checks"][3]["detail"]["part_bit_rates"] == [80242041.0, 80242041.0]
```

`a_part(frames=None)` / `a_part(video_rate=None)` はストリームからキーを落とす形にする。
ヘルパを次のようにしておく:

```python
def a_part(duration=1500.0, size=16_000_000_000, *, video_rate="79924667", frames="45000",
           audio_rate="317374", extra=()):
    video = {"index": 0, "codec_type": "video", "codec_name": "hevc",
             "codec_tag_string": "hvc1"}
    if video_rate is not None:
        video["bit_rate"] = video_rate
    if frames is not None:
        video["nb_frames"] = frames
    audio = {"index": 1, "codec_type": "audio", "codec_name": "aac",
             "codec_tag_string": "mp4a"}
    if audio_rate is not None:
        audio["bit_rate"] = audio_rate
    streams = [video, audio,
               {"index": 2, "codec_type": "data", "codec_name": "bin_data",
                "codec_tag_string": "dbgi", "bit_rate": "10300000"}]
    streams.extend(extra)
    return ProbedFile(duration_seconds=duration, size_bytes=size, streams=tuple(streams))
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_merge_verify.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/core/merge/verify.py`:

```python
"""結合結果の検証（§9.8）.

ffprobe の出力を受ける純粋関数。4 つの検査それぞれが pass / fail /
inconclusive を返し、**inconclusive は合否に使わない**。

サイズを「Σ パートのファイルサイズ」と比べてはならない。`-c copy` は宣言した
ストリームだけを引き継ぐので、正常な結合でもファイルサイズは大きく減る
（実測で 11.4%）。期待値は保持対象の `bit_rate × duration` から出す。
`bit_rate` は codec やコンテナによって取れず、可変ビットレートでは丸めた
平均でしかないので、一律に必須とすると正常な出力を不合格にしてしまう。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..profiles.model import KeepStreams
from .streams import selected_streams, stream_signature, stream_summary

# 検証器の版。閾値や判定を変えたら上げる。**input_digest には入れない**
# （入力の同一性の判定であって、検証器の同一性ではない）。
PIPELINE_VERSION = 1
# 継ぎ目ごとに 1 秒。パート数に比例させる。
DURATION_TOLERANCE_PER_PART = 1.0
# 継ぎ目で数フレーム落ちるのは正常。
FRAME_ALLOWANCE_PER_SEAM = 2
FRAME_ALLOWANCE_BASE = 2
# TS 経由は mux のオーバーヘッドが通常経路と異なる。
SIZE_TOLERANCE = {"concat": 0.02, "ts": 0.05}
# (max - min) / mean がこれを超えたら、平均ビットレートとして信用しない。
BITRATE_SPREAD_LIMIT = 0.1
# bit_rate が無いと期待サイズを組み立てられない種別。data はサイズへの寄与が
# 小さいので、取れなければ推定から外して先へ進む。
ESTIMABLE_TYPES = frozenset({"video", "audio"})

PASS = "pass"  # noqa: S105 （検査結果の名前。秘密ではない）
FAIL = "fail"
INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ProbedFile:
    duration_seconds: float | None
    size_bytes: int
    streams: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Check:
    name: str
    verdict: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class Verification:
    passed: bool
    route: str
    pipeline_version: int
    checks: tuple[Check, ...]
    # プロファイルが保持を宣言しなかったストリーム。
    dropped_streams: tuple[dict[str, Any], ...]
    # 保持を宣言したのに、経路のコンテナが運べずに外したストリーム。
    route_dropped_streams: tuple[dict[str, Any], ...]
    seam_offsets: tuple[float, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "passed": self.passed,
                "route": self.route,
                "pipeline_version": self.pipeline_version,
                "checks": [
                    {"name": c.name, "verdict": c.verdict, "detail": c.detail} for c in self.checks
                ],
                "dropped_streams": list(self.dropped_streams),
                "route_dropped_streams": list(self.route_dropped_streams),
                "seam_offsets": list(self.seam_offsets),
            },
            ensure_ascii=False,
        )


def verify(
    parts: Sequence[ProbedFile],
    merged: ProbedFile,
    keep: KeepStreams,
    route: str,
    route_dropped: Sequence[dict[str, Any]] = (),
) -> Verification:
    checks = (
        _duration_check(parts, merged),
        _streams_check(parts, merged, keep),
        _frames_check(parts, merged, keep),
        _size_check(parts, merged, keep, route),
    )
    return Verification(
        # inconclusive は合否に使わない。fail が 1 つも無ければ合格。
        passed=all(check.verdict != FAIL for check in checks),
        route=route,
        pipeline_version=PIPELINE_VERSION,
        checks=checks,
        dropped_streams=_dropped_streams(parts[0], keep),
        route_dropped_streams=tuple(stream_summary(stream) for stream in route_dropped),
        seam_offsets=_seam_offsets(parts),
    )


def _duration_check(parts: Sequence[ProbedFile], merged: ProbedFile) -> Check:
    if merged.duration_seconds is None:
        return Check("duration", FAIL, {"reason": "結合後の duration が取れない"})
    if any(part.duration_seconds is None for part in parts):
        return Check("duration", INCONCLUSIVE, {"reason": "duration が取れないパートがある"})
    expected = sum(part.duration_seconds for part in parts)
    difference = abs(merged.duration_seconds - expected)
    limit = DURATION_TOLERANCE_PER_PART * len(parts)
    return Check(
        "duration",
        PASS if difference <= limit else FAIL,
        {
            "expected_seconds": expected,
            "actual_seconds": merged.duration_seconds,
            "difference_seconds": difference,
            "limit_seconds": limit,
        },
    )


def _streams_check(parts: Sequence[ProbedFile], merged: ProbedFile, keep: KeepStreams) -> Check:
    signatures = {stream_signature(selected_streams(part.streams, keep)) for part in parts}
    if len(signatures) != 1:
        return Check(
            "streams",
            FAIL,
            {
                "reason": "パート間でストリーム構成が一致しない",
                "signatures": [[list(s) for s in signature] for signature in sorted(signatures)],
            },
        )
    expected = next(iter(signatures))
    if not expected:
        return Check("streams", FAIL, {"reason": "保持対象のストリームが 1 本も無い"})
    actual = stream_signature(selected_streams(merged.streams, keep))
    return Check(
        "streams",
        PASS if actual == expected else FAIL,
        {"expected": [list(s) for s in expected], "actual": [list(s) for s in actual]},
    )


def _frames_check(parts: Sequence[ProbedFile], merged: ProbedFile, keep: KeepStreams) -> Check:
    part_frames = [_video_frames(part, keep) for part in parts]
    merged_frames = _video_frames(merged, keep)
    if merged_frames is None or any(frames is None for frames in part_frames):
        return Check(
            "frames",
            INCONCLUSIVE,
            {"reason": "nb_frames が取れない映像ストリームがある"},
        )
    expected = sum(part_frames)
    allowance = FRAME_ALLOWANCE_PER_SEAM * (len(parts) - 1) + FRAME_ALLOWANCE_BASE
    lost = expected - merged_frames
    return Check(
        "frames",
        PASS if lost <= allowance else FAIL,
        {
            "expected_frames": expected,
            "actual_frames": merged_frames,
            "lost_frames": lost,
            "allowance_frames": allowance,
        },
    )


def _size_check(
    parts: Sequence[ProbedFile], merged: ProbedFile, keep: KeepStreams, route: str
) -> Check:
    """保持対象の `bit_rate × duration` から期待サイズを組み立てて比べる.

    **ばらつきは対応するストリームごとに見る。** 合計で見ると、支配的な映像が
    音声の大きな変動を隠す。`bit_rate` が取れないストリームは、映像・音声なら
    推定できないので `inconclusive`、data なら推定から外して先へ進む
    （`tmcd` は毎秒わずかで、許容誤差に埋もれる）。
    """
    if any(part.duration_seconds is None for part in parts):
        return Check("size", INCONCLUSIVE, {"reason": "duration が取れないパートがある"})
    selections = [selected_streams(part.streams, keep) for part in parts]
    if len({len(selection) for selection in selections}) != 1:
        return Check("size", INCONCLUSIVE, {"reason": "パート間で保持ストリームの本数が違う"})

    excluded: list[dict[str, Any]] = []
    part_rates = [0.0] * len(parts)
    expected_bits = 0.0
    # 位置で対応付ける。構成がずれている場合はストリーム検査が fail するので、
    # ここでは本数の一致だけを前提にする。
    for column in zip(*selections, strict=True):
        rates = [_bitrate_of(stream) for stream in column]
        if any(rate is None for rate in rates):
            if column[0].get("codec_type") in ESTIMABLE_TYPES:
                return Check(
                    "size",
                    INCONCLUSIVE,
                    {
                        "reason": "映像か音声の bit_rate が取れない",
                        "stream": stream_summary(column[0]),
                    },
                )
            excluded.append(stream_summary(column[0]))
            continue
        mean = sum(rates) / len(rates)
        if mean <= 0:
            excluded.append(stream_summary(column[0]))
            continue
        spread = (max(rates) - min(rates)) / mean
        if spread > BITRATE_SPREAD_LIMIT:
            return Check(
                "size",
                INCONCLUSIVE,
                {
                    "reason": "パート間の bit_rate のばらつきが大きく、平均として使えない",
                    "stream": stream_summary(column[0]),
                    "spread": spread,
                    "limit": BITRATE_SPREAD_LIMIT,
                },
            )
        for index, (rate, part) in enumerate(zip(rates, parts, strict=True)):
            expected_bits += rate * part.duration_seconds
            part_rates[index] += rate

    expected = expected_bits / 8
    if expected <= 0:
        return Check("size", INCONCLUSIVE, {"reason": "期待サイズを組み立てられない"})
    tolerance = SIZE_TOLERANCE[route]
    ratio = abs(merged.size_bytes - expected) / expected
    return Check(
        "size",
        PASS if ratio <= tolerance else FAIL,
        {
            "expected_bytes": expected,
            "actual_bytes": merged.size_bytes,
            "ratio": ratio,
            "tolerance": tolerance,
            "part_bit_rates": part_rates,
            "part_durations": [part.duration_seconds for part in parts],
            "excluded_streams": excluded,
        },
    )


def _video_frames(probed: ProbedFile, keep: KeepStreams) -> int | None:
    total = 0
    for stream in selected_streams(probed.streams, keep):
        if stream.get("codec_type") != "video":
            continue
        raw = stream.get("nb_frames")
        if raw is None:
            return None
        try:
            total += int(raw)
        except (TypeError, ValueError):
            return None
    return total


def _bitrate_of(stream: dict[str, Any]) -> float | None:
    raw = stream.get("bit_rate")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _dropped_streams(part: ProbedFile, keep: KeepStreams) -> tuple[dict[str, Any], ...]:
    """脱落したストリームを記録する. 画面に出してユーザが把握できるようにする."""
    kept = {id(stream) for stream in selected_streams(part.streams, keep)}
    return tuple(
        stream_summary(stream) for stream in part.streams if id(stream) not in kept
    )


def _seam_offsets(parts: Sequence[ProbedFile]) -> tuple[float, ...]:
    """継ぎ目の秒数（各パートの累積境界）. 最後の終端は継ぎ目ではない."""
    offsets: list[float] = []
    total = 0.0
    for part in parts[:-1]:
        if part.duration_seconds is None:
            return ()
        total += part.duration_seconds
        offsets.append(total)
    return tuple(offsets)
```

**`tmcd` を推定から外すのは、検査を弱めるためではなく生かすため。** DJI の実測
（0.002% の差）は映像と音声だけで出している。`tmcd` は `bit_rate` を持たないので、
これを理由に全体を `inconclusive` にすると、**既定のプロファイル（`timecode: true`）
ではサイズ検査が常に無効**になる。外したストリームは `excluded_streams` に残す。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_merge_verify.py -q`
Expected: PASS（19 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `passed` を `all(verdict == PASS)` にする | `test_missing_frame_counts_are_inconclusive_not_failed` |
| `limit` を `DURATION_TOLERANCE_PER_PART` 固定にする | `test_the_duration_tolerance_scales_with_the_part_count` |
| `signatures` の比較（`len != 1`）を消す | `test_parts_that_disagree_with_each_other_fail` |
| `actual == expected` を `len(actual) == len(expected)` にする | `test_a_missing_kept_stream_fails` は本数も減るので落ちない。**codec だけ違う merged のケースを足す** |
| `allowance` の `+ FRAME_ALLOWANCE_BASE` を消す | `test_lost_frames_within_the_allowance_pass`（許容 2 になり 4 フレーム欠けが fail） |
| `lost <= allowance` を `abs(lost) <= allowance` にする | 計画では「検出できない」としていたが、**片側であること自体をテストで固定すれば検出できる**。結合後が Σ より多いケースが `pass` になることを見る `test_extra_frames_do_not_fail_the_check` を足した（§9.8 の条件が片側なので、これが仕様どおり） |
| `merged_frames is None` の判定を消す | `test_a_merged_file_without_frame_counts_is_inconclusive`（**追加**）。結合後の `nb_frames` が取れないケースを 1 つも試していなかった |
| 保持ストリーム本数の一致判定（`len({len(selection)...}) != 1`）を消す | `test_parts_with_different_kept_stream_counts_are_inconclusive`（**追加**）。本数が違うパートを 1 つも試していなかった。消すと `zip(..., strict=True)` が例外を投げる |
| 結合後の duration 欠落を `FAIL` から `INCONCLUSIVE` にする | `test_a_merged_file_without_a_duration_fails`（**追加**）。判定不能で通すと壊れた結合物が合格になる |
| `spread > BITRATE_SPREAD_LIMIT` を消す | `test_a_wide_bit_rate_spread_makes_the_size_check_inconclusive` |
| ばらつきをストリームごとではなく合計で見る | `test_a_spread_hidden_by_the_dominant_stream_is_still_caught` |
| `ESTIMABLE_TYPES` の判定を消して、`bit_rate` が無ければ常に `inconclusive` | `test_a_timecode_without_a_bit_rate_does_not_disable_the_size_check` |
| `ESTIMABLE_TYPES` に `data` を足す（＝常に推定を続ける） | `test_a_missing_video_bit_rate_makes_the_size_check_inconclusive` は video なので落ちない。**`data` の `bit_rate` 欠落で fail 側へ倒れるケースは無い**ので、この変異は `ESTIMABLE_TYPES` から `video` を外す形で確かめる |
| `SIZE_TOLERANCE[route]` を `0.02` 固定にする | `test_the_ts_route_allows_a_wider_size_difference` |
| 推定を保持対象ではなく全ストリームで行う | `test_the_size_is_compared_against_the_kept_streams_only`（dbgi の 10.3 Mbps が乗って fail） |
| `route_dropped` を捨てる | `test_streams_dropped_by_the_route_are_recorded` |
| `PIPELINE_VERSION` を `to_json` から落とす | `test_the_result_serialises_to_json` |
| `_seam_offsets` の `parts[:-1]` を `parts` にする | `test_seam_offsets_are_the_cumulative_boundaries` |

codec だけ違うテストを足す:

```python
def test_a_recoded_stream_fails_even_with_the_same_count():
    merged = a_merged(streams=[
        {"index": 0, "codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1",
         "bit_rate": "79924667", "nb_frames": "89999"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a",
         "bit_rate": "317374"},
    ])
    result = verify([a_part(), a_part()], merged, KEEP, "concat")
    assert verdicts(result)["streams"] == "fail"
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/core/merge/verify.py app/tests/test_merge_verify.py
git commit -m "feat(mediaferry): verify a merge against its declared streams"
```

---

### Task 6: ffmpeg アダプタ（concat と TS フォールバック）

**Files:**
- Create: `app/src/mediaferry/adapters/ffmpeg.py`
- Test: `app/tests/test_adapter_ffmpeg.py`

**Interfaces:**
- Consumes: `map_arguments` / `selected_streams` / `stream_signature` / `stream_summary`（Task 3）、`KeepStreams`
- Produces:
  - `MergeRunner(ffmpeg_path: str = "ffmpeg", poll_interval: float = POLL_INTERVAL, pulse_interval: float = PULSE_INTERVAL, term_grace_seconds: float = TERM_GRACE_SECONDS)`
  - `MergeRunner.merge(parts: Sequence[Path], part_streams: Sequence[Sequence[dict[str, Any]]], keep: KeepStreams, work_dir: Path, output_name: str, on_progress: Callable[[], None], cancelled: Callable[[], bool]) -> MergeOutcome`
  - `MergeRunner.tool_version() -> str`
  - `MergeOutcome(route: str, output_path: Path, tool_version: str, dropped_by_route: tuple[dict[str, Any], ...])`
  - `MergeFailed(RuntimeError)` / `MergeCancelled(RuntimeError)`
  - `UNSUPPORTED_BY_TS: frozenset[str]`

**ストリームの選択は「パートごと」に作る。** 先頭パートの絶対 index を全パートへ
使い回すと、保持 signature が同じでも**保持しない data track の挿入位置が違う
パートで別のストリームを選ぶ**（part 1 が video=0/audio=1/data=2、part 2 が
video=0/data=1/audio=2 のとき、`-map 0:1` は part 2 の data を指す）。同じ codec の
別トラックなら、失敗せずに誤った中身のまま通る余地がある。**`merge()` は全パートの
ffprobe 結果を受け取り、各パートの選択から map を組む。**

**concat demuxer は preflight してから使う。** concat demuxer は最初のファイルの
ストリーム構成を全体に適用するので、**全パートの全ストリームの signature が一致し、
かつ保持対象の絶対 index の並びが一致する**ときだけ試す。満たさなければ concat を
試さずに TS 経路へ送る。

**TS 経路が運べないストリームは、意図して外して記録する。** `mpegts` は QuickTime の
data track（`tmcd` / `djmd`）を運べないので、map に残したままだと mux が拒否して
**検証できる出力そのものが作られない**（既定の DJI プロファイルは `timecode: true`
なので、fallback が常に使えない経路になる）。外したストリームは
`MergeOutcome.dropped_by_route` に入れ、`verification_json` の
`route_dropped_streams` に残す。ストリーム検査は「宣言した種別が揃っていない」ので
**不合格**になるが、出力は公開されるので、ユーザは中身を見て採用を選べる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_adapter_ffmpeg.py`:

```python
import shutil
import subprocess

import pytest

from mediaferry.adapters.ffmpeg import MergeCancelled, MergeFailed, MergeRunner
from mediaferry.adapters.ffprobe import MediaProbe
from mediaferry.core.profiles.model import KeepStreams

KEEP = KeepStreams(video="primary", audio="all", timecode=False, data=False)
VIDEO_ONLY = KeepStreams(video="primary", audio="none", timecode=False, data=False)


def make_clip(path, seconds=2, *, timecode=False, audio_first=False):
    command = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x64:rate=10",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
    ]
    # -map の順がそのまま出力のストリーム順になる。並びの違うパートを作れる。
    command += ["-map", "1:a", "-map", "0:v"] if audio_first else ["-map", "0:v", "-map", "1:a"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    if timecode:
        command += ["-timecode", "00:00:00:00"]
    command += ["-y", str(path)]
    subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    return path


@pytest.fixture
def clips(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    return [make_clip(tmp_path / "a.MP4"), make_clip(tmp_path / "b.MP4")]


@pytest.fixture
def work_dir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


def streams_for(paths):
    return [MediaProbe().describe(path, "MP4").streams for path in paths]


def never_cancelled():
    return False


def test_two_clips_are_joined_by_the_concat_demuxer(clips, work_dir):
    outcome = MergeRunner().merge(
        clips, streams_for(clips), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "concat"
    assert outcome.dropped_by_route == ()
    probe = MediaProbe().describe(outcome.output_path, "MP4")
    assert probe.probe_state == "ok"
    assert 3.6 < probe.duration_seconds < 4.4


def test_the_declared_streams_decide_what_is_kept(clips, work_dir):
    outcome = MergeRunner().merge(
        clips, streams_for(clips), VIDEO_ONLY, work_dir, "MERGED.MP4",
        lambda: None, never_cancelled,
    )
    kinds = {s["codec_type"] for s in MediaProbe().describe(outcome.output_path, "MP4").streams}
    assert kinds == {"video"}


def test_the_lease_pulse_is_throttled_but_always_fires_once(clips, work_dir):
    beats = []
    MergeRunner(pulse_interval=1000.0).merge(
        clips, streams_for(clips), KEEP, work_dir, "MERGED.MP4",
        lambda: beats.append(1), never_cancelled,
    )
    # 短い結合でも 1 回は打つ。poll のたびには打たない。
    assert len(beats) == 1


class FailingConcat(MergeRunner):
    """concat demuxer だけが失敗する. TS 経路が実際に走ることを確かめる."""

    def _concat_command(self, parts, maps, work_dir, output):
        return [self._ffmpeg, "-nostdin", "-v", "error", "-i", "/nonexistent.MP4",
                "-y", str(output)]


def test_the_ts_route_runs_when_the_concat_demuxer_fails(clips, work_dir):
    outcome = FailingConcat().merge(
        clips, streams_for(clips), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "ts"
    probe = MediaProbe().describe(outcome.output_path, "MP4")
    assert probe.probe_state == "ok"
    assert 3.6 < probe.duration_seconds < 4.4


def test_parts_with_a_different_stream_order_skip_the_concat_demuxer(tmp_path, work_dir):
    """concat demuxer は最初のファイルの構成を全体に適用する.

    並びが違うまま渡すと、後続のパートで別のストリームを拾う。preflight で
    弾いて TS 経路へ送る。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    parts = [
        make_clip(tmp_path / "a.MP4"),
        make_clip(tmp_path / "b.MP4", audio_first=True),
    ]
    outcome = MergeRunner().merge(
        parts, streams_for(parts), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "ts"
    assert not (work_dir / "concat.log").exists()
    kinds = sorted(
        s["codec_type"] for s in MediaProbe().describe(outcome.output_path, "MP4").streams
    )
    assert kinds == ["audio", "video"]


def test_each_part_is_mapped_by_its_own_indexes(tmp_path, work_dir):
    """並びの違うパートでも、映像と音声が取り違えられずに残る."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    parts = [
        make_clip(tmp_path / "a.MP4"),
        make_clip(tmp_path / "b.MP4", audio_first=True),
    ]
    outcome = MergeRunner().merge(
        parts, streams_for(parts), VIDEO_ONLY, work_dir, "MERGED.MP4",
        lambda: None, never_cancelled,
    )
    streams = MediaProbe().describe(outcome.output_path, "MP4").streams
    assert [s["codec_type"] for s in streams] == ["video"]


def test_the_ts_route_drops_what_mpegts_cannot_carry_and_records_it(tmp_path, work_dir):
    """mpegts は tmcd を運べない. map に残すと出力そのものが作られない."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    parts = [
        make_clip(tmp_path / "a.MP4", timecode=True),
        make_clip(tmp_path / "b.MP4", timecode=True),
    ]
    outcome = FailingConcat().merge(
        parts, streams_for(parts), KEEP, work_dir, "MERGED.MP4", lambda: None, never_cancelled
    )
    assert outcome.route == "ts"
    assert [s["codec_tag_string"] for s in outcome.dropped_by_route] == ["tmcd"]
    assert MediaProbe().describe(outcome.output_path, "MP4").probe_state == "ok"


def test_a_broken_input_fails_on_both_routes(tmp_path, work_dir):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    broken = tmp_path / "broken.MP4"
    broken.write_bytes(b"\x00" * 128)
    with pytest.raises(MergeFailed):
        MergeRunner().merge(
            [broken, broken], [[], []], KEEP, work_dir, "MERGED.MP4",
            lambda: None, never_cancelled,
        )


def test_a_cancelled_merge_raises_and_leaves_no_output(clips, work_dir):
    with pytest.raises(MergeCancelled):
        MergeRunner().merge(
            clips, streams_for(clips), KEEP, work_dir, "MERGED.MP4",
            lambda: None, lambda: True,
        )


def test_the_tool_version_is_the_first_line_of_ffmpeg_version(clips):
    assert MergeRunner().tool_version().startswith("ffmpeg version")
```

`KEEP` は `timecode=True` に直す（既定の DJI プロファイルと同じ形にして、
TS 経路の脱落を再現できるようにする）:

```python
KEEP = KeepStreams(video="primary", audio="all", timecode=True, data=False)
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_adapter_ffmpeg.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.adapters.ffmpeg'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/adapters/ffmpeg.py`:

```python
"""ffmpeg による結合（§9.8）.

concat demuxer を試し、失敗したら TS 経由へ落とす。**保持するストリームの選択は
パートごとに、そのパート自身の ffprobe 結果から作る。** 先頭パートの絶対 index を
使い回すと、保持しない data track の挿入位置が違うパートで別のストリームを選ぶ。

concat demuxer は最初のファイルの構成を全体に適用するので、全パートの構成が
一致するときだけ試す。一致しなければ preflight で弾いて TS 経路へ送る。

TS 経路では、選択を各パートの mpegts 化の段で適用する。mpegts の中では
ストリーム index が振り直されるので、結合の段で MP4 の絶対 index を使うと
別のストリームを指す。**mpegts は QuickTime の data track（tmcd / djmd）を
運べない**ので、保持を宣言されていても外し、外したことを呼び出し元へ返す。
map に残したままだと mux が拒否して、検証できる出力そのものが作られない。

外部プロセスはプロセスグループとして起動し、キャンセル時は SIGTERM → 猶予 →
SIGKILL の順に送って必ず刈り取る（§9.9）。
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.merge.streams import (
    map_arguments,
    selected_streams,
    stream_signature,
    stream_summary,
)
from ..core.profiles.model import KeepStreams

logger = logging.getLogger(__name__)

# キャンセル要求に気づくまでの間隔。
POLL_INTERVAL = 0.5
# リースを延ばす間隔。リース (60 秒) の 1/3。poll のたびに書くと、30 分の結合で
# 数千回 WAL へ書き、API とキャンセルの書き込みロックに不要に競合する。
PULSE_INTERVAL = 20.0
TERM_GRACE_SECONDS = 5.0
VERSION_TIMEOUT_SECONDS = 30
# 失敗の理由を伝えるのに要る分だけ。ログ全体は work/ に残る。
LOG_TAIL_CHARS = 2000

# mpegts が運べない種別。tmcd（タイムコード）と djmd / dbgi がここに入る。
UNSUPPORTED_BY_TS = frozenset({"data"})

# MP4 の中の H.264 / H.265 を mpegts へ入れるには Annex B へ直す。
_ANNEXB = {"h264": "h264_mp4toannexb", "hevc": "hevc_mp4toannexb"}


class MergeFailed(RuntimeError):
    """ffmpeg が非 0 で終了した."""


class MergeCancelled(RuntimeError):
    """キャンセル要求を観測して外部プロセスを刈った."""


@dataclass(frozen=True)
class MergeOutcome:
    route: str  # concat / ts
    output_path: Path
    tool_version: str
    # 保持を宣言されていたのに、経路のコンテナが運べずに外したストリーム。
    dropped_by_route: tuple[dict[str, Any], ...]


class MergeRunner:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        poll_interval: float = POLL_INTERVAL,
        pulse_interval: float = PULSE_INTERVAL,
        term_grace_seconds: float = TERM_GRACE_SECONDS,
    ) -> None:
        self._ffmpeg = ffmpeg_path
        self._poll_interval = poll_interval
        self._pulse_interval = pulse_interval
        self._term_grace = term_grace_seconds

    def merge(
        self,
        parts: Sequence[Path],
        part_streams: Sequence[Sequence[dict[str, Any]]],
        keep: KeepStreams,
        work_dir: Path,
        output_name: str,
        on_progress: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> MergeOutcome:
        selections = [selected_streams(streams, keep) for streams in part_streams]
        output = work_dir / output_name
        if _topology_matches(part_streams, selections):
            try:
                self._run(
                    self._concat_command(
                        parts, map_arguments(selections[0]), work_dir, output
                    ),
                    work_dir / "concat.log",
                    on_progress,
                    cancelled,
                )
                return MergeOutcome("concat", output, self.tool_version(), ())
            except MergeFailed as exc:
                logger.warning("concat demuxer に失敗した。TS 経由へ落とす: %s", exc)
        else:
            logger.warning("パート間でストリームの並びが違うので concat demuxer を使わない")
        output.unlink(missing_ok=True)
        dropped = self._ts_merge(
            parts, part_streams, selections, work_dir, output, on_progress, cancelled
        )
        return MergeOutcome("ts", output, self.tool_version(), dropped)

    def tool_version(self) -> str:
        completed = subprocess.run(  # noqa: S603
            [self._ffmpeg, "-version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
        return completed.stdout.splitlines()[0].strip()

    # ------------------------------------------------------------------
    def _concat_command(
        self, parts: Sequence[Path], maps: list[str], work_dir: Path, output: Path
    ) -> list[str]:
        listing = work_dir / "concat.txt"
        listing.write_text(
            "".join(f"file '{_escape(part)}'\n" for part in parts), encoding="utf-8"
        )
        return [
            self._ffmpeg, "-nostdin", "-v", "error",
            "-f", "concat", "-safe", "0", "-fflags", "+genpts",
            "-i", str(listing),
            *maps, "-c", "copy", "-y", str(output),
        ]

    def _ts_merge(
        self,
        parts: Sequence[Path],
        part_streams: Sequence[Sequence[dict[str, Any]]],
        selections: Sequence[Sequence[dict[str, Any]]],
        work_dir: Path,
        output: Path,
        on_progress: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> tuple[dict[str, Any], ...]:
        """各パートを mpegts にしてから `concat:` で結合する.

        map と bitstream filter は**そのパート自身の**構成から作る。
        """
        dropped: dict[tuple[Any, ...], dict[str, Any]] = {}
        pieces: list[Path] = []
        for index, part in enumerate(parts):
            carried = []
            for stream in selections[index]:
                if stream.get("codec_type") in UNSUPPORTED_BY_TS:
                    summary = stream_summary(stream)
                    dropped[(summary["codec_type"], summary["codec_tag_string"])] = summary
                    continue
                carried.append(stream)
            piece = work_dir / f"part-{index:04d}.ts"
            self._run(
                [
                    self._ffmpeg, "-nostdin", "-v", "error", "-i", str(part),
                    *map_arguments(carried), "-c", "copy",
                    *_video_bitstream(part_streams[index]),
                    "-f", "mpegts", "-y", str(piece),
                ],
                work_dir / f"ts-{index:04d}.log",
                on_progress,
                cancelled,
            )
            pieces.append(piece)
        self._run(
            [
                self._ffmpeg, "-nostdin", "-v", "error",
                "-i", "concat:" + "|".join(str(piece) for piece in pieces),
                "-map", "0", "-c", "copy", *_audio_bitstream(part_streams[0]),
                "-y", str(output),
            ],
            work_dir / "ts-join.log",
            on_progress,
            cancelled,
        )
        return tuple(dropped.values())

    def _run(
        self,
        command: list[str],
        log_path: Path,
        on_progress: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> None:
        # 引数配列で起動する。シェル文字列は組み立てない（§14）。
        with log_path.open("wb") as log:
            process = subprocess.Popen(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                # プロセスグループを分けて、子を取り残さずに刈れるようにする。
                start_new_session=True,
            )
            try:
                # 起動直後に 1 回打つ。短い結合でも heartbeat が 0 回にならない。
                on_progress()
                last_pulse = time.monotonic()
                while process.poll() is None:
                    # キャンセルは細かく見る。応答を待たせない。
                    if cancelled():
                        self._kill(process)
                        raise MergeCancelled("キャンセル要求を観測した")
                    # **リースの延長は throttle する。** poll のたびに打つと、
                    # 30 分の結合で数千回 WAL へ書き、API とキャンセルの
                    # 書き込みロックに不要に競合する。
                    if time.monotonic() - last_pulse >= self._pulse_interval:
                        on_progress()
                        last_pulse = time.monotonic()
                    time.sleep(self._poll_interval)
            finally:
                if process.poll() is None:
                    self._kill(process)
        if process.returncode != 0:
            raise MergeFailed(f"ffmpeg が {process.returncode} で終了した: {_tail(log_path)}")

    def _kill(self, process: subprocess.Popen[bytes]) -> None:
        """プロセスグループ単位で送り、子プロセスを取り残さない."""
        try:
            group = os.getpgid(process.pid)
        except ProcessLookupError:
            process.wait()
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGTERM)
        deadline = time.monotonic() + self._term_grace
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(self._poll_interval)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
        process.wait()


def _topology_matches(
    part_streams: Sequence[Sequence[dict[str, Any]]],
    selections: Sequence[Sequence[dict[str, Any]]],
) -> bool:
    """concat demuxer を使ってよいか.

    demuxer は最初のファイルの構成を全体に適用するので、**全ストリームの
    構成**と**保持対象の絶対 index の並び**の両方が一致していることを求める。
    """
    if len({stream_signature(streams) for streams in part_streams}) != 1:
        return False
    return len({tuple(s["index"] for s in selection) for selection in selections}) == 1


def _escape(path: Path) -> str:
    """concat demuxer の単一引用符の中で使える形にする."""
    return str(path).replace("'", "'\\''")


def _video_bitstream(streams: Sequence[dict[str, Any]]) -> list[str]:
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        name = _ANNEXB.get(str(stream.get("codec_name")))
        return [] if name is None else ["-bsf:v", name]
    return []


def _audio_bitstream(streams: Sequence[dict[str, Any]]) -> list[str]:
    """TS から MP4 へ戻す段で ADTS の AAC を ASC へ直す."""
    if any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in streams):
        return ["-bsf:a", "aac_adtstoasc"]
    return []


def _tail(log_path: Path) -> str:
    text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    return text[-LOG_TAIL_CHARS:]
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_adapter_ffmpeg.py -q`
Expected: PASS（10 件）

TS 経路が通らない場合は `work_dir/ts-*.log` を読む。`-bsf:v` の付け外しと
`-map` の位置（入力の直後、`-c copy` の前）を先に疑う。

**実装で 1 つ足りなかった: TS 片のストリームの並びを揃える（`_ts_layout`）。**
`concat:` は mpegts の生バイトを継ぐので、パートごとに TS の中の並びが違うと
後続のパートを読めない（`No start code is found` → `could not write header`）。
上の計画のままだと `test_parts_with_a_different_stream_order_skip_the_concat_demuxer`
が TS 経路でも落ちる（実際に落ちた）。map に使う index は**そのパート自身のもの**の
ままにして、`-map` に並べる順だけを種別順（video → audio → その他）へ揃える。

```python
# TS 片の中でのストリームの並び。ここに無い種別は後ろへ回す。
_TS_TYPE_ORDER = {"video": 0, "audio": 1}


def _ts_layout(streams: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """TS 片のストリームの並びを、パートによらず種別順に揃える."""
    return sorted(
        streams, key=lambda s: _TS_TYPE_ORDER.get(str(s.get("codec_type")), len(_TS_TYPE_ORDER))
    )
```

呼び出しは `*map_arguments(_ts_layout(carried))`。

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `maps` を `merge` に渡さない（ffmpeg の既定選択に任せる） | `test_the_declared_streams_decide_what_is_kept` |
| TS 化の map を `selections[0]` から作る（先頭パートの index を使い回す） | `test_each_part_is_mapped_by_its_own_indexes` |
| `_topology_matches` を常に `True` にする | `test_parts_with_a_different_stream_order_skip_the_concat_demuxer` |
| `_topology_matches` から絶対 index の比較を落とし、signature だけ見る | 同上を期待したが**素通りする**。並びが違うクリップは全ストリームの signature も違うので、1 つ目の判定で先に落ちて index の比較へ到達しない。ffprobe の出力では index が位置と一致するので、**この分岐は実クリップでは踏めない**。`_topology_matches` を直接呼ぶ単体テスト（`test_the_preflight_also_compares_the_absolute_indexes`）を足して固定した |
| `_ts_layout` の並べ替えを外す | `test_parts_with_a_different_stream_order_skip_the_concat_demuxer`（**実装で追加した処理**。下記） |
| `except MergeFailed` を握りつぶして concat の結果を返す | `test_the_ts_route_runs_when_the_concat_demuxer_fails` |
| `UNSUPPORTED_BY_TS` を空にする | `test_the_ts_route_drops_what_mpegts_cannot_carry_and_records_it`（mpegts が tmcd を拒否して `MergeFailed`） |
| `dropped` を返さず `()` にする | 同上（記録が空になる） |
| `on_progress()` の起動直後の 1 回を消す | `test_the_lease_pulse_is_throttled_but_always_fires_once` |
| pulse の throttle を外して poll のたびに打つ | 同上（`pulse_interval=1000` でも複数回打つ） |
| `cancelled()` の確認を消す | `test_a_cancelled_merge_raises_and_leaves_no_output` |
| `_video_bitstream` を常に `[]` にする | **落ちない**。現行の ffmpeg（`N-125084-geb7f4b4e79`）は mpegts へ入れるときに `h264_mp4toannexb` を自動で挿入する。明示は古い ffmpeg のための保険。検出できない変異として記録する |
| `_audio_bitstream` を常に `[]` にする | 同上。`aac_adtstoasc` も自動で挿入される。検出できない変異として記録する |
| `_kill` の SIGKILL 段を消す | **落ちない**。SIGTERM で終わる ffmpeg しかテストで使えないため。検出できない変異として記録する（`-i` に名前付きパイプを渡して SIGTERM を無視させる試験は、環境依存が大きいので入れない） |
| `start_new_session=True` を外す | **この変異は当ててはいけない。** 外すと子が**テストランナー自身のプロセスグループ**に入り、キャンセルのテストで `os.killpg` が pytest ごと撃つ（実際に走らせて、変異ドライバとシェルが死んだ）。保護が効いていることの裏返しだが、自動では確かめられない。検出できない変異として記録する |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/adapters/ffmpeg.py app/tests/test_adapter_ffmpeg.py
git commit -m "feat(mediaferry): join parts with ffmpeg and fall back through ts"
```

---

### Task 7: 出来上がったファイルの公開（`publish_prepared`）

**Files:**
- Modify: `app/src/mediaferry/adapters/publisher.py`
- Modify: `app/tests/crash_child.py`
- Modify: `app/tests/test_crash_consistency.py`
- Test: `app/tests/test_publisher.py`（追記）

**Interfaces:**
- Produces:
  - `ArtifactPublisher.publish_prepared(ctx: JobContext, request: ArtifactRequest, prepared_abs: Path) -> PublishedArtifact`
  - `PublishCancelled(PublishAborted)`
  - `HEARTBEAT_INTERVAL: float`
- 既存の `publish` / `resume` / 例外・11 手順の意味は**変えない**

**なぜ足すか:** `publish` は `write` コールバックで staging へ書きながら SHA-1 を
取る。結合はすでに `work/` に完成品があるので、この契約のままだと 30 GiB を
もう一度書き直すことになる。`work/` と `staging/` は同じファイルシステムなので
（§7 が保証する）、`os.link` で移せば書き直しが要らない。**手順 1 と 3 以降は
まったく同じコードを通る。**

**読み取りの間も heartbeat とキャンセル確認を続ける。** 取り込みでは
`Importer` の `write` コールバックの中で打っているが（`importer.py`）、
結合では読み取りが publisher の内側で起きる。30 GiB の SHA-1 が 60 秒を超えると、
読み切った後の手順 7 の `assert_lease` で**リースが失効し、正しく生成・検証済みの
結合物が `PublishAborted` になる**。低速な HDD や負荷中の NAS では毎回同じ位置で
再現する。キャンセルも走査が終わるまで効かない。`_materialise_link` は
`ctx.heartbeat()` を時間ベースで打ち、chunk ごとに `ctx.cancelled()` を見て
`PublishCancelled` を送出する（staged より前なので durable なものは残らない。
`PublishAborted` の派生にして、既存の呼び出し側の扱いを変えない）。

**chunk の合間だけでは足りない。** 読み終えた後にも、リースより長くなりうる
同期処理が 2 つ残っている。

| 処理 | どれくらいかかりうるか |
| --- | --- |
| `os.fsync`（手順 3） | 30 GiB を書いた直後の NAS では数十秒。**中断できない** |
| `MediaProbe.describe`（手順 5） | timeout がちょうど 60 秒で、リースと同値 |

どちらもハッシュ走査の後・手順 7 の前にあるので、走査中に打った heartbeat が
成功していても、この 2 つで失効しうる。**`_with_lease_pulse` で囲む** ——
処理を別スレッドで走らせ、待つ側（＝ジョブのスレッド）が `HEARTBEAT_INTERVAL`
ごとに `ctx.heartbeat()` を打つ。DB へ触るのは待つ側だけなので、**接続は
スコープごとに 1 本のまま**（トランザクションは接続に属する）。

**これは取り込み側にも同じ形で存在する穴で、共通の `_publish` を直すことで
両方に効く。** Phase 1 の実装では、16 GiB のコピーの後の `os.fsync` と ffprobe が
同じ位置にある。実 USB での確認（`phase1-manual-checklist.md`）は 100 バイトの
ファイルでしか通っていないので、まだ表に出ていない。

`os.fsync` そのものは中断できないので、**fsync 中のキャンセル要求には応答
できない**（終わってから次の確認点で降りる）。これは仕様として受け入れる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_publisher.py` に追記（既存の `setup` / `a_request` を使う）:

```python
def test_a_prepared_file_is_published_without_being_rewritten(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(b"merged-bytes")

    published = publisher.publish_prepared(
        ctx,
        a_request(
            profile,
            None,
            kind="merge",
            role="derived",
            desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
            merge_group_id=group_id,
        ),
        prepared,
    )

    final = data_root / "derived/dji-osmo/DCIM/MERGED.MP4"
    assert final.read_bytes() == b"merged-bytes"
    assert published.sha1 == hashlib.sha1(b"merged-bytes", usedforsecurity=False).hexdigest()
    assert published.size_bytes == len(b"merged-bytes")
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (published.media_file_id,)).fetchone()
    assert row["role"] == "derived"
    assert db.execute(
        "SELECT output_media_file_id FROM merge_group WHERE id = ?", (group_id,)
    ).fetchone()[0] == published.media_file_id


def test_the_published_file_survives_the_work_directory_being_cleaned(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(b"merged-bytes")
    publisher.publish_prepared(
        ctx,
        a_request(profile, None, kind="merge", role="derived",
                  desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4", merge_group_id=group_id),
        prepared,
    )

    shutil.rmtree(work)

    assert (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").read_bytes() == b"merged-bytes"


def test_publishing_a_prepared_file_leaves_no_staging_file(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(b"merged-bytes")
    publisher.publish_prepared(
        ctx,
        a_request(profile, None, kind="merge", role="derived",
                  desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4", merge_group_id=group_id),
        prepared,
    )
    assert [p for p in (data_root / "staging").rglob("*") if p.is_file()] == []


def test_the_prepared_file_gets_the_requested_mtime(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(b"merged-bytes")
    publisher.publish_prepared(
        ctx,
        a_request(profile, None, kind="merge", role="derived",
                  desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
                  merge_group_id=group_id, mtime_ns=1_600_000_000_000_000_000),
        prepared,
    )
    stat = (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").stat()
    assert stat.st_mtime_ns == 1_600_000_000_000_000_000


def test_a_missing_prepared_file_leaves_nothing_durable(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    with pytest.raises(OSError):
        publisher.publish_prepared(
            ctx,
            a_request(profile, None, kind="merge", role="derived",
                      desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
                      merge_group_id=group_id),
            data_root / "work" / ctx.job_id / "missing.MP4",
        )
    # writing の行だけが残る。次回起動の reconciliation が破棄する。
    row = db.execute("SELECT state FROM artifact_staging").fetchone()
    assert row["state"] == "writing"
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0


def test_the_hash_scan_pulses_the_lease(setup, data_root, db, monkeypatch):
    """リースより長い走査でも、手順 7 で失効しない."""
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    # 2 chunk 以上にして、走査の途中で打つ機会を作る。
    prepared.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    monkeypatch.setattr("mediaferry.adapters.publisher.HEARTBEAT_INTERVAL", 0)
    beats = []
    monkeypatch.setattr(ctx, "heartbeat", lambda: beats.append(1))

    publisher.publish_prepared(
        ctx,
        a_request(profile, None, kind="merge", role="derived",
                  desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4", merge_group_id=group_id),
        prepared,
    )
    assert beats


def test_a_slow_probe_does_not_lose_the_lease(db, data_root, monkeypatch):
    """ffprobe の timeout はリースと同値. 囲まないと手順 7 で失効する."""
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    store = JobStore(db, lease_seconds=1)
    store.enqueue("merge", {})
    ctx = store.claim_next()
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")

    class SlowProbe(StubProbe):
        def describe(self, path, extension):
            time.sleep(1.5)  # リース (1 秒) より長い
            return super().describe(path, extension)

    monkeypatch.setattr("mediaferry.adapters.publisher.HEARTBEAT_INTERVAL", 0.2)
    publisher = ArtifactPublisher(db, data_root, SlowProbe())
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(b"merged-bytes")

    published = publisher.publish_prepared(
        ctx,
        a_request(profile, None, kind="merge", role="derived",
                  desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4", merge_group_id=group_id),
        prepared,
    )
    assert (data_root / published.rel_path).exists()


def test_the_lease_pulse_propagates_the_failure(setup):
    """囲んだ処理の例外は、そのまま呼び出し側へ渡す."""
    _, ctx, _, _ = setup

    def boom():
        raise RuntimeError("fsync に失敗した")

    with pytest.raises(RuntimeError, match="fsync"):
        _with_lease_pulse(ctx, boom)


def test_a_cancelled_hash_scan_leaves_nothing_durable(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(b"merged-bytes")
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    with pytest.raises(PublishCancelled):
        publisher.publish_prepared(
            ctx,
            a_request(profile, None, kind="merge", role="derived",
                      desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
                      merge_group_id=group_id),
            prepared,
        )
    assert db.execute("SELECT count(*) FROM media_file").fetchone()[0] == 0
    assert db.execute("SELECT state FROM artifact_staging").fetchone()["state"] == "writing"
    assert not (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").exists()
```

import に `time` と `PublishCancelled` / `_with_lease_pulse` を足す。

ファイル先頭の import に次を足す:

```python
import shutil

from .test_schema_artifacts import a_merge_group, a_source_entry
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_publisher.py -q`
Expected: FAIL（`AttributeError: 'ArtifactPublisher' object has no attribute 'publish_prepared'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/adapters/publisher.py` の `publish` を、共通の `_publish` と
2 つの「実体を staging に置く」メソッドに分ける。**手順の番号と
`_checkpoint` の位置は変えない。**

```python
    # ------------------------------------------------------------------
    def publish(
        self,
        ctx: JobContext,
        request: ArtifactRequest,
        write: Callable[[HashingWriter], None],
    ) -> PublishedArtifact:
        """staging へ書きながら公開する（取り込み）."""
        return self._publish(
            ctx,
            request,
            lambda staging_abs: self._materialise_write(staging_abs, write, request.mtime_ns),
        )

    def publish_prepared(
        self, ctx: JobContext, request: ArtifactRequest, prepared_abs: Path
    ) -> PublishedArtifact:
        """既にできているファイルを公開する（結合）.

        `work/` と `staging/` は同じファイルシステムなので `os.link` で移せる
        （§7）。結合物をもう一度書き直さない。SHA-1 は 1 パス読んで確定する。
        手順 1 と 3 以降は `publish` と同じ経路を通るので、どこで落ちても
        reconciliation が同じように回収する。
        """
        return self._publish(
            ctx,
            request,
            lambda staging_abs: self._materialise_link(
                staging_abs, prepared_abs, request.mtime_ns
            ),
        )

    def _publish(
        self,
        ctx: JobContext,
        request: ArtifactRequest,
        materialise: Callable[[Path], tuple[int, str]],
    ) -> PublishedArtifact:
        staging_id = new_id()
        staging_rel = staging_rel_path(ctx.job_id, staging_id)
        staging_abs = self._data_root / staging_rel

        # 1. writing の行を先に commit する。ここから先はどこで落ちても回収できる。
        self._conn.execute(
            "INSERT INTO artifact_staging (id, kind, job_id, lease_token, state,"
            " staging_rel_path, source_entry_id, merge_group_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'writing', ?, ?, ?, ?, ?)",
            (
                staging_id,
                request.kind,
                ctx.job_id,
                ctx.lease_token,
                staging_rel,
                request.source_entry_id,
                request.merge_group_id,
                now_iso(),
                now_iso(),
            ),
        )
        self._checkpoint(STEP_WRITING_ROW)

        # 2〜3. 実体を staging に置く。ジョブ用ディレクトリを新しく作ったときは、
        #       その名前を持つ親（staging/）も fsync する。中のファイルだけ
        #       永続化しても、<job-id> のエントリが失われれば丸ごと消える。
        if not staging_abs.parent.exists():
            staging_abs.parent.mkdir(parents=True, exist_ok=True)
            fsync_dir(staging_abs.parent.parent)
        size, sha1 = materialise(staging_abs)

        # 4. サイズの検証
        on_disk = staging_abs.stat().st_size
        if on_disk != size:
            raise PublishAborted(f"書き込みサイズが一致しない（{on_disk} != {size}）")
        self._checkpoint(STEP_VERIFIED)
```

以降（手順 5 の `metadata` から）は現行のまま。`writer.size` を `size` に、
`writer.sha1` を `sha1` に置き換える（手順 7 の UPDATE の引数 2 か所）。
**手順 5 の ffprobe だけは `_with_lease_pulse` で囲む**（timeout がリースと
同値なので、囲まないと手順 7 で失効しうる）:

```python
        # 5. メタデータは公開前に確定させる。実体はあるがメタデータが欠けたまま
        #    永久にスキップされる状態を作らない。
        probe = _with_lease_pulse(
            ctx, lambda: self._probe.describe(staging_abs, request.extension)
        )
```

実体を置く 2 つのメソッドを足す:

```python
    def _materialise_write(
        self, staging_abs: Path, write: Callable[[HashingWriter], None], mtime_ns: int, ctx: JobContext
    ) -> tuple[int, str]:
        """書きながら SHA-1 を取る. 読み直しを 1 回省く."""
        with staging_abs.open("wb") as fileobj:
            writer = HashingWriter(fileobj)
            write(writer)
            fileobj.flush()
            self._checkpoint(STEP_WRITTEN)
            # mtime は fsync より前に付ける。後に付けると metadata の
            # 永続化が保証されない。
            os.utime(fileobj.fileno(), ns=(mtime_ns, mtime_ns))
            # 3. 中身とディレクトリエントリの両方を永続化する。親を fsync
            #    しないと、電源断で「DB は staged、ファイルは無い」になる。
            #    **16 GiB を書いた直後の fsync はリースより長くなりうる。**
            _with_lease_pulse(ctx, lambda: os.fsync(fileobj.fileno()))
        fsync_dir(staging_abs.parent)
        self._checkpoint(STEP_FSYNCED)
        return writer.size, writer.sha1

    def _materialise_link(
        self, staging_abs: Path, prepared_abs: Path, mtime_ns: int, ctx: JobContext
    ) -> tuple[int, str]:
        """既存のファイルを staging へ link し、1 パス読んで SHA-1 を取る.

        30 GiB の走査はリース（60 秒）より長い。**chunk 境界がキャンセル
        ポイントで、heartbeat は時間で打つ。** 打たないと、読み切った後の
        手順 7 でリースが失効し、検証済みの結合物が捨てられる。
        """
        os.link(prepared_abs, staging_abs)
        digest = hashlib.sha1(usedforsecurity=False)
        size = 0
        last_beat = time.monotonic()
        with staging_abs.open("rb") as fileobj:
            while chunk := fileobj.read(COPY_CHUNK):
                digest.update(chunk)
                size += len(chunk)
                if ctx.cancelled():
                    raise PublishCancelled("SHA-1 の走査中にキャンセル要求を観測した")
                if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL:
                    # **バイト数ではなく経過時間で打つ。** 低速な読み出しでは、
                    # 閾値バイトに達する前にリースが切れる。
                    ctx.heartbeat()
                    last_beat = time.monotonic()
            self._checkpoint(STEP_WRITTEN)
            os.utime(staging_abs, ns=(mtime_ns, mtime_ns))
            # 30 GiB を link した直後の fsync はリースより長くなりうる。
            _with_lease_pulse(ctx, lambda: os.fsync(fileobj.fileno()))
        fsync_dir(staging_abs.parent)
        self._checkpoint(STEP_FSYNCED)
        return size, digest.hexdigest()
```

`publish` と `publish_prepared` はどちらも `ctx` を渡す形にする:

```python
        return self._publish(
            ctx,
            request,
            lambda staging_abs: self._materialise_write(
                staging_abs, write, request.mtime_ns, ctx
            ),
        )
```

```python
        return self._publish(
            ctx,
            request,
            lambda staging_abs: self._materialise_link(
                staging_abs, prepared_abs, request.mtime_ns, ctx
            ),
        )
```

ファイル冒頭に足すもの:

```python
import threading
import time

from ..db.jobs import LEASE_SECONDS, JobContext, LeaseLost

# リース (60 秒) の 1/3 ごとに延ばす。30 GiB の走査はリースより長く、
# 読み出し速度は環境で桁が変わるので、バイト数ではなく時間で決める。
HEARTBEAT_INTERVAL = LEASE_SECONDS / 3


class PublishCancelled(PublishAborted):
    """staged へ進む前にキャンセル要求を観測した.

    `PublishAborted` の派生にしてあるので、既存の呼び出し側は今までどおり
    「durable なものは残っていない」として扱える。結合の呼び出し元は、
    これを見てグループを detected へ戻す。
    """
```

そして、staged より前の**中断できない長い処理**を囲むヘルパ（型引数は PEP 695 の
記法で書く。`TypeVar` を使うと ruff の `UP047` に当たる）:

```python
def _with_lease_pulse[T](ctx: JobContext, work: Callable[[], T]) -> T:
    """リースを延ばしながら、中断できない同期処理を待つ.

    `os.fsync` と ffprobe は、どちらも 1 回でリース (60 秒) を超えうるのに
    途中で止められない。処理は別スレッドで走らせ、**待つ側（ジョブの
    スレッド）が heartbeat を打つ**。DB へ触るのは待つ側だけなので、接続は
    スコープごとに 1 本のままで済む（トランザクションは接続に属する）。

    リースを失ったら、処理の完了を待ってから送出する。走っているスレッドを
    残したまま抜けると、後から staging へ書き込むことになる。
    """
    outcome: list[T] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome.append(work())
        except BaseException as exc:  # noqa: BLE001 - 呼び出し側へそのまま渡す
            failure.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    lost: LeaseLost | None = None
    while True:
        thread.join(timeout=HEARTBEAT_INTERVAL)
        if not thread.is_alive():
            break
        if lost is None:
            try:
                ctx.heartbeat()
            except LeaseLost as exc:
                # 打てなくなっても待ち続ける。処理の完了を待ってから送出する。
                lost = exc
    if lost is not None:
        raise lost
    if failure:
        raise failure[0]
    return outcome[0]
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_publisher.py app/tests/test_crash_consistency.py -q`
Expected: PASS（既存の import 経路のテストも全部通る）

- [ ] **Step 5: crash consistency を `publish_prepared` にも通す**

`app/tests/crash_child.py` の `main()` を、`kind == "merge_prepared"` に対応させる。

```python
def main() -> None:
    data_root = Path(sys.argv[1])
    die_after = int(sys.argv[2])
    kind = sys.argv[3]

    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    apply_migrations(conn)
    registry = ProfileRegistry(conn)
    registry.sync_builtins()
    profile = registry.current("dji-osmo")

    store = JobStore(conn)
    store.enqueue("import" if kind == "import" else "merge", {})
    ctx = store.claim_next()

    source_entry_id = merge_group_id = None
    if kind == "import":
        volume_id, source_entry_id = _a_source(conn, profile)
    else:
        merge_group_id = _a_merge_group(conn, profile)

    publisher = CrashingPublisher(conn, data_root, _Probe(), die_after=die_after)
    request = ArtifactRequest(
        kind="import" if kind == "import" else "merge",
        role="original" if kind == "import" else "derived",
        profile_id=profile.profile_id,
        profile_revision_id=profile.revision_id,
        desired_rel_path=(
            "library/dji-osmo/DCIM/A.MP4"
            if kind == "import"
            else "derived/dji-osmo/DCIM/MERGED.MP4"
        ),
        source_rel_path="DCIM/A.MP4",
        extension="MP4",
        captured=CapturedAt(
            at=datetime.fromisoformat("2026-08-17T14:30:00+09:00"),
            source="filename",
            tz="Asia/Tokyo",
            note=None,
        ),
        mtime_ns=1_700_000_000_000_000_000,
        source_entry_id=source_entry_id,
        merge_group_id=merge_group_id,
    )
    if kind == "merge_prepared":
        work = data_root / "work" / ctx.job_id
        work.mkdir(parents=True, exist_ok=True)
        prepared = work / "MERGED.MP4"
        prepared.write_bytes(PAYLOAD)
        publisher.publish_prepared(ctx, request, prepared)
    else:
        publisher.publish(ctx, request, lambda writer: writer.write(PAYLOAD))
    # ここへ来るのは die_after が 11 より大きいときだけ。
    sys.exit(0)
```

`app/tests/test_crash_consistency.py` の parametrize を広げる:

```python
@pytest.mark.parametrize("kind", ["import", "merge", "merge_prepared"])
@pytest.mark.parametrize("step", STEPS)
def test_reconciliation_recovers_from_a_crash_at_any_step(data_root, step, kind):
    crash_at(data_root, step, kind)
    conn, report = reconcile(data_root)

    final = (
        "library/dji-osmo/DCIM/A.MP4" if kind == "import" else "derived/dji-osmo/DCIM/MERGED.MP4"
    )
    ...
```

`_clean_job_dirs` は「生きたジョブが無く、`artifact_staging` の参照も無い」
`work/<job-id>/` を消す。`merge_prepared` で手順 7 より前に落ちた場合、
`work/` の実体は掃除され、`library`/`derived` には何も残らない。手順 7 以降なら
`derived/` に公開済みの実体が残り、`work/` 側は掃除されても**同じ inode の
別名が残っている**ので中身は失われない。既存のアサーション
（`report.orphans == []`、staging にファイルが残らない）はそのまま通る。

Run: `uv run pytest app/tests/test_crash_consistency.py -q`
Expected: PASS（11 段 × 3 種 + 既存の個別ケース）

- [ ] **Step 6: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `_materialise_link` の `os.utime` を消す | `test_the_prepared_file_gets_the_requested_mtime` |
| `ctx.heartbeat()` の pulse を消す | `test_the_hash_scan_pulses_the_lease` |
| pulse をバイト数（chunk 数）で打つ形にする | `test_the_hash_scan_pulses_the_lease`。計画では「落ちない」としていたが、**`HEARTBEAT_INTERVAL` を 0 にしてあるので、時間で打つ実装だけが chunk ごとに打つ**。バイト数の閾値（例: 1 GiB ごと）にすると小さい入力では 1 回も打たず落ちる |
| `ctx.cancelled()` の確認を消す | `test_a_cancelled_hash_scan_leaves_nothing_durable` |
| `PublishCancelled` を `PublishAborted` に戻す | 同上（`pytest.raises(PublishCancelled)` が落ちる） |
| 手順 5 の `_with_lease_pulse` を外す | `test_a_slow_probe_does_not_lose_the_lease` |
| `_with_lease_pulse` の `failure` の送出を消す | `test_the_lease_pulse_propagates_the_failure` |
| `_with_lease_pulse` で `LeaseLost` を即座に送出する（thread を待たない） | `test_the_lease_pulse_waits_for_the_work_before_raising`（**追加**）。計画では「落ちない」としていたが、競合の再現は要らない。`heartbeat` が必ず `LeaseLost` を投げる ctx と、0.3 秒かかる処理を渡し、**送出された時点で処理が完了しているか**を見れば固定できる |
| `_materialise_write` の `os.fsync` を囲まない | `test_a_slow_fsync_does_not_lose_the_lease`（**追加**）。計画では「落ちない」としていたが、`os.fsync` を差し替えて 1.5 秒（リース 1 秒より長く）かかるようにすれば観測できる。**遅くするのはファイルの fsync だけにする** —— ディレクトリの fsync まで遅くすると、`_publish` の中の `fsync_dir`（囲みの外。実際はメタデータだけで一瞬）で先に失効して、狙いの分岐へ届かない。**これは Phase 1 の取り込み側の穴をそのまま塞ぐテスト**でもある |
| `os.link` を `shutil.copy` にする | **落ちない**（結果は同じになる）。書き直しを避けることが目的なので、検出できない変異として記録する。ただし `test_the_published_file_survives_the_work_directory_being_cleaned` は copy でも通るので、**inode の共有を直接見るテストを足す**（下記） |
| `size` の検証（手順 4）を消す | **そのようなテストは Phase 1 に無かった**（計画の思い込み）。`_materialise_link` を差し替えて実体より 1 バイト大きい size を返させる `test_a_size_that_disagrees_with_the_disk_is_aborted` を足した |
| `_materialise_link` の `os.fsync` を消す | **落ちない**。電源断を再現できないため。検出できない変異として記録する（`os._exit` の crash 試験はページキャッシュを失わない） |

inode を直接見るテストを足す:

```python
def test_the_prepared_file_is_linked_not_copied(setup, data_root, db):
    publisher, ctx, profile, _ = setup
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-1")
    work = data_root / "work" / ctx.job_id
    work.mkdir(parents=True)
    prepared = work / "MERGED.MP4"
    prepared.write_bytes(b"merged-bytes")
    publisher.publish_prepared(
        ctx,
        a_request(profile, None, kind="merge", role="derived",
                  desired_rel_path="derived/dji-osmo/DCIM/MERGED.MP4", merge_group_id=group_id),
        prepared,
    )
    final = data_root / "derived/dji-osmo/DCIM/MERGED.MP4"
    assert final.stat().st_ino == prepared.stat().st_ino
```

- [ ] **Step 7: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/adapters/publisher.py app/tests/test_publisher.py \
        app/tests/crash_child.py app/tests/test_crash_consistency.py
git commit -m "feat(mediaferry): publish an already written artifact by link"
```

---

### Task 8: 結合グループのリポジトリ

**Files:**
- Create: `app/src/mediaferry/db/merges.py`
- Test: `app/tests/test_merge_repository.py`

**Interfaces:**
- Consumes: `GroupCandidate`（Task 1）、`ProfileRef`、`immediate`（既存）
- Produces:
  - `MergeRepository(conn: sqlite3.Connection)`
  - `.save_detected(profile: ProfileRef, candidate: GroupCandidate, digest: str) -> str | None`
  - `.claim_for_merge(group_id: str, expected_digest: str) -> None`（取れなければ `GroupNotClaimable`）
  - `.record_verification(group_id: str, verification_json: str, tool_version: str) -> None`
  - `.mark_merged(group_id) -> None` / `.mark_failed(group_id, error: str) -> None` / `.release(group_id) -> None` / `.adopt(group_id) -> None`
  - `.get(group_id) -> sqlite3.Row | None` / `.members(group_id) -> list[sqlite3.Row]` / `.list_groups(status: str | None = None, limit: int = 200, offset: int = 0) -> list[sqlite3.Row]`
  - `GroupNotClaimable(RuntimeError)`

**マイグレーションは足さない。** `merge_group` / `merge_member` は Phase 1 の
`0003_artifacts_and_merges.sql` にあり、supersede の不可逆性と `active` の
両方向 trigger まで入っている。出力の相対パスは列に持たず、必要なときに
`merged_rel_path` で計算する（構成から決まる値を二重に持たない）。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merge_repository.py`:

```python
from datetime import UTC, datetime

import pytest

from mediaferry.core.merge.grouping import GIB, GroupCandidate, MergePart
from mediaferry.db.merges import GroupNotClaimable, MergeRepository
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_media_file


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


def a_candidate(db, profile, count=2):
    members = []
    for index in range(count):
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/DJI_{index:04d}_D.MP4",
            sha1=f"{index:040d}",
        )
        members.append(
            MergePart(
                media_file_id=media_id,
                rel_path=f"library/dji-osmo/DCIM/DJI_{index:04d}_D.MP4",
                sha1=f"{index:040d}",
                captured_at=datetime(2026, 8, 17, 14, 30, tzinfo=UTC),
                duration_seconds=1500.0,
                size_bytes=16 * GIB,
                probe_state="ok",
            )
        )
    return GroupCandidate(members=tuple(members), gaps=(2.0,) * (count - 1))


def test_a_detected_group_keeps_its_members_in_order(db, profile):
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    group_id = repo.save_detected(profile, candidate, "digest-1")
    rows = repo.members(group_id)
    assert [row["position"] for row in rows] == [0, 1]
    assert [row["media_file_id"] for row in rows] == [
        part.media_file_id for part in candidate.members
    ]
    assert repo.get(group_id)["status"] == "detected"
    assert repo.get(group_id)["detected_by"] == "auto"


def test_the_same_digest_is_not_stored_twice(db, profile):
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    assert repo.save_detected(profile, candidate, "digest-1") is not None
    assert repo.save_detected(profile, candidate, "digest-1") is None


def test_a_member_of_an_active_group_is_not_taken_again(db, profile):
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    repo.save_detected(profile, candidate, "digest-1")
    # 同じファイルを含む別の構成は作れない（1 ファイル 1 アクティブグループ）。
    assert repo.save_detected(profile, candidate, "digest-2") is None


def test_claiming_moves_the_group_to_merging(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    assert repo.get(group_id)["status"] == "merging"


def test_claiming_twice_is_refused(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.claim_for_merge(group_id, "digest-1")


def test_a_changed_digest_cannot_be_claimed(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.claim_for_merge(group_id, "digest-2")


def test_a_failed_group_can_be_claimed_again(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    repo.mark_failed(group_id, "ffmpeg が落ちた")
    repo.claim_for_merge(group_id, "digest-1")
    assert repo.get(group_id)["status"] == "merging"
    # 再試行では前回の理由を残さない。
    assert repo.get(group_id)["error"] is None


def test_a_merged_group_cannot_be_claimed_again(db, profile):
    """再結合は旧 output_media_file_id を取り残す. supersede が要るので Phase 4."""
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    output_id = a_media_file(
        db, (profile.profile_id, profile.revision_id), role="derived",
        rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
    )
    repo.claim_for_merge(group_id, "digest-1")
    repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output_id, group_id)
    )
    repo.mark_merged(group_id)
    with pytest.raises(GroupNotClaimable):
        repo.claim_for_merge(group_id, "digest-1")


def test_recording_a_verification_needs_a_merging_group(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")


def test_marking_merged_needs_an_output_and_a_verification(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    # 検証も出力も無い状態では倒せない。呼び出し順のバグで「merged なのに
    # 出力が無い」行を作らせない。
    with pytest.raises(GroupNotClaimable):
        repo.mark_merged(group_id)
    repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")
    with pytest.raises(GroupNotClaimable):
        repo.mark_merged(group_id)


def test_releasing_puts_the_group_back_to_detected(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    repo.release(group_id)
    assert repo.get(group_id)["status"] == "detected"


def test_the_verification_is_recorded_before_the_group_is_merged(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")
    row = repo.get(group_id)
    assert row["verification_json"] == '{"passed": true}'
    assert row["tool_version"] == "ffmpeg version X"
    # まだ merged にはしない。公開が終わってから倒す。
    assert row["status"] == "merging"


def test_adopting_requires_a_merged_group_with_an_output(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.adopt(group_id)


def test_adopting_is_idempotent(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    output_id = a_media_file(
        db, (profile.profile_id, profile.revision_id), role="derived",
        rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
    )
    repo.claim_for_merge(group_id, "digest-1")
    repo.record_verification(group_id, '{"passed": false}', "ffmpeg version X")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output_id, group_id)
    )
    repo.mark_merged(group_id)
    repo.adopt(group_id)
    first = repo.get(group_id)["adopted_at"]
    repo.adopt(group_id)
    assert repo.get(group_id)["adopted_at"] == first


def test_groups_can_be_listed_by_status(db, profile):
    repo = MergeRepository(db)
    first = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(first, "digest-1")
    assert [row["id"] for row in repo.list_groups(status="merging")] == [first]
    assert repo.list_groups(status="merged") == []
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_merge_repository.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.db.merges'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/db/merges.py`:

```python
"""結合グループの保存と状態遷移.

`status` は detected → merging → merged / failed、および detected / failed →
skipped と動く。**遷移は `BEGIN IMMEDIATE` の中の条件付き UPDATE（CAS）で取る。**
SQLite に行ロックは無い。

claim の条件に `input_digest` を含めるのは、キューで待っている間に構成や
プロファイルが変わった場合に、確認画面と違う入力で結合しないため。
"""

from __future__ import annotations

import sqlite3

from ..clock import now_iso
from ..core.merge.grouping import GroupCandidate
from ..ids import new_id
from .connection import immediate
from .profiles import ProfileRef

CLAIMABLE = ("detected", "failed")


class GroupNotClaimable(RuntimeError):
    """求めた遷移ができない（状態が違う、構成が変わっている、出力が無い）."""


class MergeRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_detected(
        self, profile: ProfileRef, candidate: GroupCandidate, digest: str
    ) -> str | None:
        """作れたら group_id、既に同じものがあれば None."""
        media_ids = [part.media_file_id for part in candidate.members]
        marks = ", ".join("?" * len(media_ids))
        with immediate(self._conn):
            existing = self._conn.execute(
                "SELECT 1 FROM merge_group WHERE input_digest = ? AND superseded_by_id IS NULL",
                (digest,),
            ).fetchone()
            if existing is not None:
                return None
            taken = self._conn.execute(
                f"SELECT 1 FROM merge_member WHERE active = 1 AND media_file_id IN ({marks})",  # noqa: S608
                media_ids,
            ).fetchone()
            if taken is not None:
                # 1 つの media_file が同時に属せるアクティブグループは 1 つまで。
                return None
            group_id = new_id()
            self._conn.execute(
                "INSERT INTO merge_group (id, profile_id, profile_revision_id, status,"
                " input_digest, detected_by, created_at, updated_at)"
                " VALUES (?, ?, ?, 'detected', ?, 'auto', ?, ?)",
                (
                    group_id,
                    profile.profile_id,
                    profile.revision_id,
                    digest,
                    now_iso(),
                    now_iso(),
                ),
            )
            for position, media_id in enumerate(media_ids):
                self._conn.execute(
                    "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
                    " VALUES (?, ?, ?, 1)",
                    (group_id, media_id, position),
                )
        return group_id

    def claim_for_merge(self, group_id: str, expected_digest: str) -> None:
        """detected / failed → merging. 再試行でも同じ条件を使う."""
        marks = ", ".join("?" * len(CLAIMABLE))
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET status = 'merging', error = NULL, updated_at = ?"  # noqa: S608
                " WHERE id = ? AND input_digest = ? AND superseded_by_id IS NULL"
                f" AND status IN ({marks})",
                (now_iso(), group_id, expected_digest, *CLAIMABLE),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(
                    f"グループ {group_id} は結合を始められない（状態か構成が変わっている）"
                )

    def record_verification(
        self, group_id: str, verification_json: str, tool_version: str
    ) -> None:
        """**公開の前に呼ぶ。** 公開の途中で落ちても検証をやり直さずに済む.

        成立条件を DB 側でも確かめる。呼び出し順のバグ 1 つで、結合していない
        グループに検証結果が付くのを防ぐ。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET verification_json = ?, tool_version = ?, updated_at = ?"
                " WHERE id = ? AND status = 'merging' AND superseded_by_id IS NULL",
                (verification_json, tool_version, now_iso(), group_id),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(
                    f"グループ {group_id} は結合中ではないので検証結果を書けない"
                )

    def mark_merged(self, group_id: str) -> None:
        """**出力と検証結果が揃っていることを DB 側で確かめる。**

        揃っていない `merged` 行を作ると、選択肢の側が黙って隠すので
        異常が静かに残る。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET status = 'merged', error = NULL, updated_at = ?"
                " WHERE id = ? AND status = 'merging' AND superseded_by_id IS NULL"
                "   AND output_media_file_id IS NOT NULL AND verification_json IS NOT NULL",
                (now_iso(), group_id),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(
                    f"グループ {group_id} には merged にできる出力と検証結果が無い"
                )

    def mark_failed(self, group_id: str, error: str) -> None:
        self._transition(group_id, "failed", ("merging",), error=error)

    def release(self, group_id: str) -> None:
        """merging → detected. キャンセルと中断の後始末に使う."""
        self._transition(group_id, "detected", ("merging",))

    def adopt(self, group_id: str) -> None:
        """検証不合格の派生物を、中身を見た上で採用する（§10）."""
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT status, adopted_at, output_media_file_id, superseded_by_id"
                " FROM merge_group WHERE id = ?",
                (group_id,),
            ).fetchone()
            if row is None or row["superseded_by_id"] is not None:
                raise GroupNotClaimable(f"グループ {group_id} は採用できない")
            if row["status"] != "merged" or row["output_media_file_id"] is None:
                raise GroupNotClaimable(f"グループ {group_id} には採用できる出力が無い")
            if row["adopted_at"] is not None:
                return
            self._conn.execute(
                "UPDATE merge_group SET adopted_at = ?, updated_at = ? WHERE id = ?",
                (now_iso(), now_iso(), group_id),
            )

    def get(self, group_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM merge_group WHERE id = ?", (group_id,)
        ).fetchone()

    def members(self, group_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT mm.position, mm.media_file_id, m.rel_path, m.sha1, m.size_bytes,"
                " m.duration_seconds, m.probe_state, m.captured_at, m.captured_at_source,"
                " m.captured_at_tz, m.captured_at_note, m.missing_at"
                " FROM merge_member mm JOIN media_file m ON m.id = mm.media_file_id"
                " WHERE mm.merge_group_id = ? ORDER BY mm.position",
                (group_id,),
            )
        )

    def list_groups(
        self, status: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[sqlite3.Row]:
        if status is None:
            return list(
                self._conn.execute(
                    "SELECT * FROM merge_group ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            )
        return list(
            self._conn.execute(
                "SELECT * FROM merge_group WHERE status = ?"
                " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        )

    def _transition(
        self, group_id: str, target: str, allowed: tuple[str, ...], error: str | None = None
    ) -> None:
        marks = ", ".join("?" * len(allowed))
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE merge_group SET status = ?, error = ?, updated_at = ?"  # noqa: S608
                f" WHERE id = ? AND superseded_by_id IS NULL AND status IN ({marks})",
                (target, error, now_iso(), group_id, *allowed),
            )
            if updated.rowcount != 1:
                raise GroupNotClaimable(f"グループ {group_id} は {target} へ動かせない")
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_merge_repository.py -q`
Expected: PASS（15 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `save_detected` の digest 重複チェックを消す | `test_the_same_digest_is_not_stored_twice` では**落ちない**。同じ構成を 2 回渡しているので、`taken` のチェックが先に `None` を返す。**構成ファイルが違うのに digest が同じケース**（`test_a_digest_already_taken_by_another_group_is_refused`）を足すと、部分ユニーク索引の `IntegrityError` が漏れて落ちる |
| `taken` のチェックを消す | `test_a_member_of_an_active_group_is_not_taken_again`（同上。`merge_member_one_active_group` が `IntegrityError` を投げる） |
| `claim_for_merge` から `input_digest = ?` を落とす | `test_a_changed_digest_cannot_be_claimed` |
| `CLAIMABLE` に `"merging"` を足す | `test_claiming_twice_is_refused` |
| `CLAIMABLE` に `"merged"` を足す | `test_a_merged_group_cannot_be_claimed_again` |
| `claim_for_merge` の `error = NULL` を消す | `test_a_failed_group_can_be_claimed_again` の後半 |
| `record_verification` で `status = 'merged'` も同時に立てる | `test_the_verification_is_recorded_before_the_group_is_merged` |
| `record_verification` の `status = 'merging'` の条件を消す | `test_recording_a_verification_needs_a_merging_group` |
| `mark_merged` の `output_media_file_id IS NOT NULL` を消す | `test_marking_merged_needs_an_output_and_a_verification` |
| `mark_merged` の `verification_json IS NOT NULL` を消す | 同上（後半）では**落ちない**。出力がまだ無いので `output_media_file_id` の条件が先に効く。**出力だけがある状態**のテスト（`test_marking_merged_needs_a_verification_even_with_an_output`）を足した |
| `adopt` の `output_media_file_id IS NULL` の判定を消す | `test_adopting_requires_a_merged_group_with_an_output` では**落ちない**（`status != merged` で先に落ちる）。`status = 'merged'` かつ出力が無い行を DB へ直接作るテスト（`test_adopting_a_merged_group_without_an_output_is_refused`）を足した |
| `release` の遷移元に `detected` / `merged` を足す | `test_releasing_only_moves_a_merging_group`（**追加**）。公開済みのグループを detected へ戻すと出力が宙に浮く |
| `members` の `ORDER BY position` を逆にする | `test_a_detected_group_keeps_its_members_in_order` |
| `list_groups` の `status` の絞り込みを外す | `test_groups_can_be_listed_by_status` |
| `adopt` の `adopted_at is not None` の早期 return を消す | `test_adopting_is_idempotent` |

**検出できない変異:** `save_detected` と `claim_for_merge` の `BEGIN IMMEDIATE` を
外しても、テストは単一スレッドなので落ちない。Phase 1 の `claim_next` と同じ
構造的にテスト不能な保険であり、同時に 2 つのワーカーが動く構成
（Phase 2 の時点では 1 本しかいない）で効く。

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/db/merges.py app/tests/test_merge_repository.py
git commit -m "feat(mediaferry): store merge groups and their transitions"
```

---

### Task 9: グループ検出ジョブ

**Files:**
- Create: `app/src/mediaferry/jobs/detect_groups.py`
- Test: `app/tests/test_group_detector.py`

**Interfaces:**
- Consumes: `detect_groups` / `MergePart`（Task 1）、`input_digest`（Task 2）、`merged_rel_path` / `MergeOutputUndefined`（Task 4）、`MergeRepository`（Task 8）、`JobContext`、`ProfileRef`
- Produces:
  - `GroupDetector(conn: sqlite3.Connection, repo: MergeRepository)`
  - `.run(ctx: JobContext, profile: ProfileRef) -> DetectOutcome`
  - `.preview(profile: ProfileRef, rule: MergeRule) -> list[GroupCandidate]`
  - `DetectOutcome(created: int, existing: int, undefined: int)`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_group_detector.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mediaferry.core.merge.grouping import GIB
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.detect_groups import GroupDetector

from .test_schema_artifacts import a_media_file

BASE = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


@pytest.fixture
def ctx(db):
    store = JobStore(db)
    store.enqueue("detect_groups", {})
    return store.claim_next()


def a_part(db, profile, index, *, offset_seconds, duration=1500.0, size=16 * GIB,
           probe_state="ok", role="original", kind="video", missing_at=None):
    return a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role=role,
        kind=kind,
        rel_path=f"library/dji-osmo/DCIM/DJI_001/DJI_20260817143000_{index:04d}_D.MP4",
        sha1=f"{index:040d}",
        size_bytes=size,
        duration_seconds=duration,
        probe_state=probe_state,
        captured_at=(BASE + timedelta(seconds=offset_seconds)).isoformat(),
        missing_at=missing_at,
    )


def test_two_consecutive_parts_become_a_group(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    outcome = GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    assert outcome.created == 1
    groups = MergeRepository(db).list_groups()
    assert len(groups) == 1
    assert [row["position"] for row in MergeRepository(db).members(groups[0]["id"])] == [0, 1]


def test_running_twice_does_not_duplicate_the_group(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    detector = GroupDetector(db, MergeRepository(db))
    detector.run(ctx, profile)
    outcome = detector.run(ctx, profile)
    assert outcome.created == 0
    assert len(MergeRepository(db).list_groups()) == 1


def test_a_file_already_in_a_group_is_a_boundary(db, profile, ctx):
    # 1-2 が既にグループ化されている状態で 3 が来ても、2 と 3 はつながらない。
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    detector = GroupDetector(db, MergeRepository(db))
    detector.run(ctx, profile)
    a_part(db, profile, 3, offset_seconds=3004)
    outcome = detector.run(ctx, profile)
    assert outcome.created == 0
    assert len(MergeRepository(db).list_groups()) == 1


def test_photos_and_derived_files_are_not_parts(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0, kind="photo", duration=None, probe_state="not_applicable")
    a_part(db, profile, 2, offset_seconds=1502, role="derived")
    assert GroupDetector(db, MergeRepository(db)).run(ctx, profile).created == 0


def test_a_missing_file_is_not_a_part(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0, missing_at=BASE.isoformat())
    a_part(db, profile, 2, offset_seconds=1502)
    assert GroupDetector(db, MergeRepository(db)).run(ctx, profile).created == 0


def test_a_candidate_without_a_readable_sequence_is_reported_not_stored(db, profile, ctx):
    for index in (1, 2):
        a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/PANO_{index:04d}.MP4",
            sha1=f"{index:040d}",
            size_bytes=16 * GIB,
            duration_seconds=1500.0,
            captured_at=(BASE + timedelta(seconds=1502 * (index - 1))).isoformat(),
        )
    outcome = GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    assert outcome.created == 0
    assert outcome.undefined == 1
    assert MergeRepository(db).list_groups() == []


def test_a_disabled_profile_detects_nothing(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    definition = replace(
        profile.definition, merge=replace(profile.definition.merge, enabled=False)
    )
    disabled = replace(profile, definition=definition)
    assert GroupDetector(db, MergeRepository(db)).run(ctx, disabled).created == 0


def test_the_preview_does_not_store_anything(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    candidates = GroupDetector(db, MergeRepository(db)).preview(
        profile, profile.definition.merge
    )
    assert len(candidates) == 1
    assert MergeRepository(db).list_groups() == []


def test_the_preview_uses_the_thresholds_it_is_given(db, profile, ctx):
    a_part(db, profile, 1, offset_seconds=0)
    a_part(db, profile, 2, offset_seconds=1502)
    strict = replace(profile.definition.merge, tolerance_seconds=1)
    assert GroupDetector(db, MergeRepository(db)).preview(profile, strict) == []
```

`ProfileRef` と `ProfileDefinition` は frozen dataclass なので `dataclasses.replace`
で差し替えられる。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_group_detector.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/jobs/detect_groups.py`:

```python
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
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_group_detector.py -q`
Expected: PASS（9 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `taken` の行で `runs.append([])` せず `continue` だけにする | `test_a_file_already_in_a_group_is_a_boundary` |
| `role = 'original'` の条件を消す | `test_photos_and_derived_files_are_not_parts` |
| `kind = 'video'` の条件を消す | 同上 |
| `missing_at IS NULL` の条件を消す | `test_a_missing_file_is_not_a_part` |
| `ORDER BY m.captured_at` を `ORDER BY m.rel_path` だけにする | **落ちない**。rel_path の順と時刻の順が一致するデータしか使っていない。**順序が食い違うテストを足す**（下記） |
| `merged_rel_path` の事前確認を消す | `test_a_candidate_without_a_readable_sequence_is_reported_not_stored` |
| `rule.enabled` の確認を消す | `test_a_disabled_profile_detects_nothing` |
| `preview` が `save_detected` を呼ぶようにする | `test_the_preview_does_not_store_anything` |
| `preview` が `profile.definition.merge` を読み直す | `test_the_preview_uses_the_thresholds_it_is_given` |

順序が食い違うテストを足す:

```python
def test_the_parts_are_ordered_by_the_capture_time_not_the_name(db, profile, ctx):
    # 名前と時刻の順が逆になっているデータで、時刻の順に並ぶことを確かめる。
    a_part(db, profile, 2, offset_seconds=0)
    a_part(db, profile, 1, offset_seconds=1502)
    GroupDetector(db, MergeRepository(db)).run(ctx, profile)
    repo = MergeRepository(db)
    group = repo.list_groups()[0]
    assert [row["rel_path"][-13:-6] for row in repo.members(group["id"])] == ["_0002_D", "_0001_D"]
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/jobs/detect_groups.py app/tests/test_group_detector.py
git commit -m "feat(mediaferry): detect merge groups from the published library"
```

---

### Task 10: 結合ジョブ

**Files:**
- Create: `app/src/mediaferry/jobs/merger.py`
- Test: `app/tests/test_merger.py`

**Interfaces:**
- Consumes: `MergeRunner` / `MergeCancelled` / `MergeFailed`（Task 6）、`ArtifactPublisher.publish_prepared`（Task 7）、`MergeRepository`（Task 8）、`merged_rel_path`（Task 4）、`verify` / `ProbedFile`（Task 5）、`MediaProbe`（既存）
- Produces:
  - `Merger(conn, repo: MergeRepository, publisher: ArtifactPublisher, runner: MergeRunner, probe: MediaProbe, data_root: Path)`
  - `.run(ctx: JobContext, group_id: str, expected_digest: str, profile: ProfileRef) -> MergeResult`
  - `MergeResult(media_file_id: str, rel_path: str, route: str, passed: bool)`
  - `MergeInputsChanged(RuntimeError)` / `NotEnoughSpace(RuntimeError)`
  - `TS_PEAK_FACTOR: int` / `FREE_SPACE_MARGIN: int`

**順序の要点:**

1. `claim_for_merge`（構成が変わっていれば始めない）
2. 入力の実在確認と空き容量の確認（始めてから途中で止まる状態を作らない）。
   **TS 経路のピーク（`.ts` と出力が同時に置かれる）で見積もる**
3. **全パートを ffprobe する**（map をパートごとに作るため。結合の前に済ませる）
4. `work/<job-id>/` で結合
5. 出力を ffprobe して検証（経路が運べずに落としたストリームも受け取る）
6. **検証結果を commit する（公開の前）**
7. キャンセルを再確認して `publish_prepared`
8. `mark_merged`
9. `work/` を掃除（成功・失敗にかかわらず）

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merger.py`:

```python
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from mediaferry.adapters.ffmpeg import MergeCancelled, MergeRunner
from mediaferry.adapters.ffprobe import MediaProbe
from mediaferry.adapters.publisher import ArtifactPublisher, PublishInterrupted
from mediaferry.core.merge.grouping import GroupCandidate, MergePart
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import GroupNotClaimable, MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.merger import (
    FREE_SPACE_MARGIN,
    MergeInputsChanged,
    Merger,
    NotEnoughSpace,
)

from .test_schema_artifacts import a_media_file

BASE = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
NAMES = ["DJI_20260817143000_0001_D.MP4", "DJI_20260817143000_0002_D.MP4"]


def make_clip(path, seconds=2):
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x64:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-y", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def world(db, data_root):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)

    members = []
    for index, name in enumerate(NAMES):
        path = make_clip(directory / name)
        rel = f"library/dji-osmo/DCIM/DJI_001/{name}"
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=rel,
            sha1=f"{index:040d}",
            size_bytes=path.stat().st_size,
            duration_seconds=2.0,
            captured_at=(BASE + timedelta(seconds=2 * index)).isoformat(),
        )
        members.append(
            MergePart(
                media_file_id=media_id,
                rel_path=rel,
                sha1=f"{index:040d}",
                captured_at=BASE + timedelta(seconds=2 * index),
                duration_seconds=2.0,
                size_bytes=path.stat().st_size,
                probe_state="ok",
            )
        )

    repo = MergeRepository(db)
    group_id = repo.save_detected(
        profile, GroupCandidate(members=tuple(members), gaps=(0.0,)), "digest-1"
    )
    store = JobStore(db)
    store.enqueue("merge", {})
    ctx = store.claim_next()
    merger = Merger(
        db, repo, ArtifactPublisher(db, data_root, MediaProbe()), MergeRunner(),
        MediaProbe(), data_root,
    )
    return merger, ctx, profile, repo, group_id


def test_a_group_is_merged_verified_and_published(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    result = merger.run(ctx, group_id, "digest-1", profile)

    assert result.route == "concat"
    assert result.rel_path == (
        "derived/dji-osmo/DCIM/DJI_001/DJI_20260817143000_0001-0002_MERGED.MP4"
    )
    assert (data_root / result.rel_path).exists()
    row = repo.get(group_id)
    assert row["status"] == "merged"
    assert row["output_media_file_id"] == result.media_file_id
    assert row["verification_json"] is not None
    assert row["tool_version"].startswith("ffmpeg version")
    media = db.execute(
        "SELECT * FROM media_file WHERE id = ?", (result.media_file_id,)
    ).fetchone()
    assert media["role"] == "derived"
    assert 3.6 < media["duration_seconds"] < 4.4


def test_the_output_mtime_is_the_recording_end_in_wall_clock(world, data_root):
    merger, ctx, profile, _, group_id = world
    result = merger.run(ctx, group_id, "digest-1", profile)
    # 最後のパートの開始（壁時計 14:30:02）+ duration 2 秒 = 14:30:04。
    expected = datetime(2026, 8, 17, 14, 30, 4, tzinfo=UTC).timestamp()
    assert (data_root / result.rel_path).stat().st_mtime == pytest.approx(expected, abs=1)


def test_the_work_directory_is_cleaned(world, data_root):
    merger, ctx, profile, _, group_id = world
    merger.run(ctx, group_id, "digest-1", profile)
    assert not (data_root / "work" / ctx.job_id).exists()


def test_a_changed_digest_is_refused_before_anything_runs(world, data_root):
    merger, ctx, profile, repo, group_id = world
    with pytest.raises(GroupNotClaimable):
        merger.run(ctx, group_id, "digest-2", profile)
    assert repo.get(group_id)["status"] == "detected"
    assert not (data_root / "work" / ctx.job_id).exists()


def test_a_missing_input_stops_the_job_and_fails_the_group(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    (data_root / "library/dji-osmo/DCIM/DJI_001" / NAMES[0]).unlink()
    with pytest.raises(MergeInputsChanged):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "failed"
    assert repo.get(group_id)["error"]


def test_a_cancelled_merge_releases_the_group(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
    with pytest.raises(MergeCancelled):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "detected"
    assert not (data_root / "work" / ctx.job_id).exists()


def test_the_verification_is_recorded_before_the_publish(world, data_root, db, monkeypatch):
    merger, ctx, profile, repo, group_id = world

    def explode(*args, **kwargs):
        raise PublishInterrupted("公開の途中で落ちた")

    monkeypatch.setattr(merger._publisher, "publish_prepared", explode)
    with pytest.raises(PublishInterrupted):
        merger.run(ctx, group_id, "digest-1", profile)
    row = repo.get(group_id)
    # 検証結果は残る。**merging のままにする**（reconciliation が決着させる）。
    assert row["verification_json"] is not None
    assert row["status"] == "merging"


def test_the_space_check_covers_the_ts_peak(world, data_root, db, monkeypatch):
    """入力合計は入るが、TS の中間物と出力を同時に置けない空きでは始めない."""
    import os as os_module

    merger, ctx, profile, repo, group_id = world
    total = sum(row["size_bytes"] for row in repo.members(group_id))
    real = os_module.statvfs(data_root)

    class Tight:
        f_frsize = 1
        f_bavail = total + FREE_SPACE_MARGIN + 1  # 入力 1 本ぶんは足りる

    monkeypatch.setattr(os_module, "statvfs", lambda path: Tight() if str(path) == str(data_root) else real)
    with pytest.raises(NotEnoughSpace):
        merger.run(ctx, group_id, "digest-1", profile)
    assert repo.get(group_id)["status"] == "failed"


def test_a_failed_verification_is_still_published(world, data_root, db):
    merger, ctx, profile, repo, group_id = world
    # duration をずらして不合格にする。公開は行われ、採用はされない。
    db.execute("UPDATE media_file SET duration_seconds = 60.0 WHERE role = 'original'")
    result = merger.run(ctx, group_id, "digest-1", profile)
    assert not result.passed
    assert (data_root / result.rel_path).exists()
    row = repo.get(group_id)
    assert row["status"] == "merged"
    assert row["adopted_at"] is None
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_merger.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.jobs.merger'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/jobs/merger.py`:

```python
"""結合ジョブ（§9.8）.

1 ジョブが 1 グループを扱う。出力は `work/<job-id>/` に作り、検証してから
`ArtifactPublisher.publish_prepared` で `derived/` へ公開する。最終パスへ
直接書かない。

**検証結果は公開の前に commit する。** 公開の途中で落ちても検証をやり直さない。

合格・不合格にかかわらず公開する。不合格は `adopted_at = NULL` のまま残り、
既定の選択肢から外れる（§10）。`work/` に置いたままにすると、リース失効時の
掃除で消えてしまう。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..adapters.ffmpeg import MergeCancelled, MergeRunner
from ..adapters.ffprobe import MediaProbe
from ..adapters.publisher import (
    ArtifactPublisher,
    ArtifactRequest,
    PublishCancelled,
    PublishInterrupted,
)
from ..core.merge.grouping import MergePart
from ..core.merge.output import merged_rel_path
from ..core.merge.verify import ProbedFile, verify
from ..core.naming import work_rel_path
from ..core.timestamps import CapturedAt
from ..db.jobs import JobContext
from ..db.merges import MergeRepository
from ..db.profiles import ProfileRef

# 空き容量の見積りに乗せる余裕。DB とサムネイルの分。
FREE_SPACE_MARGIN = 512 * 1024 * 1024
# TS フォールバックのピーク。全パートの .ts と結合後の出力が同時に置かれる。
TS_PEAK_FACTOR = 2


class MergeInputsChanged(RuntimeError):
    """構成ファイルが読めない（消えた、欠損が立っている）."""


class NotEnoughSpace(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeResult:
    media_file_id: str
    rel_path: str
    route: str
    passed: bool


class Merger:
    def __init__(
        self,
        conn: sqlite3.Connection,
        repo: MergeRepository,
        publisher: ArtifactPublisher,
        runner: MergeRunner,
        probe: MediaProbe,
        data_root: Path,
    ) -> None:
        self._conn = conn
        self._repo = repo
        self._publisher = publisher
        self._runner = runner
        self._probe = probe
        self._data_root = data_root

    def run(
        self, ctx: JobContext, group_id: str, expected_digest: str, profile: ProfileRef
    ) -> MergeResult:
        # 構成か状態が変わっていれば、1 バイトも読まずに止まる。
        self._repo.claim_for_merge(group_id, expected_digest)
        try:
            return self._merge(ctx, group_id, profile)
        except (MergeCancelled, PublishCancelled):
            # どちらも staged より前。durable なものは残っていないので、
            # グループを detected へ戻して再実行できるようにする。
            self._repo.release(group_id)
            raise
        except PublishInterrupted:
            # staged 以降。ファイルは検証済みで、公開に必要な情報は永続化
            # されている。**failed にしない**（起動時の reconciliation が完遂する）。
            ctx.emit("warning", "結合物の公開は起動時に再開される")
            raise
        except Exception as exc:
            self._repo.mark_failed(group_id, str(exc))
            raise
        finally:
            shutil.rmtree(self._data_root / work_rel_path(ctx.job_id), ignore_errors=True)

    # ------------------------------------------------------------------
    def _merge(self, ctx: JobContext, group_id: str, profile: ProfileRef) -> MergeResult:
        members = self._repo.members(group_id)
        parts = [self._data_root / row["rel_path"] for row in members]
        for row, path in zip(members, parts, strict=True):
            if row["missing_at"] is not None or not path.exists():
                raise MergeInputsChanged(f"{row['rel_path']} が読めない")
        self._assert_space(members)

        rule = profile.definition.merge
        desired = merged_rel_path(profile.definition.slug, rule, _as_merge_parts(members))
        extension = PurePosixPath(desired).suffix.lstrip(".").upper()

        work = self._data_root / work_rel_path(ctx.job_id)
        work.mkdir(parents=True, exist_ok=True)
        # **全パートを先に probe する。** 先頭の構成を全体に当てはめると、
        # 保持しない data track の位置が違うパートで別のストリームを選ぶ。
        probed_parts = [self._probed(path, extension) for path in parts]
        outcome = self._runner.merge(
            parts,
            [probed.streams for probed in probed_parts],
            rule.keep_streams,
            work,
            PurePosixPath(desired).name,
            ctx.heartbeat,
            ctx.cancelled,
        )

        verification = verify(
            probed_parts,
            self._probed(outcome.output_path, extension),
            rule.keep_streams,
            outcome.route,
            outcome.dropped_by_route,
        )
        # 公開の前に残す。公開の途中で落ちても検証をやり直さない。
        self._repo.record_verification(group_id, verification.to_json(), outcome.tool_version)
        ctx.emit(
            "info" if verification.passed else "warning",
            f"検証は{'合格' if verification.passed else '不合格'}（経路 {outcome.route}）",
            {"merge_group_id": group_id},
        )

        # 公開の直前にキャンセルを再確認する。リースは公開の手順 7 が見る。
        if ctx.cancelled():
            raise MergeCancelled("公開の直前にキャンセルを観測した")
        published = self._publisher.publish_prepared(
            ctx,
            ArtifactRequest(
                kind="merge",
                role="derived",
                profile_id=profile.profile_id,
                profile_revision_id=profile.revision_id,
                desired_rel_path=desired,
                source_rel_path=members[0]["rel_path"],
                extension=extension,
                captured=_captured_of(members[0]),
                mtime_ns=_recording_end_ns(members),
                source_entry_id=None,
                merge_group_id=group_id,
            ),
            outcome.output_path,
        )
        self._repo.mark_merged(group_id)
        return MergeResult(
            media_file_id=published.media_file_id,
            rel_path=published.rel_path,
            route=outcome.route,
            passed=verification.passed,
        )

    def _probed(self, path: Path, extension: str) -> ProbedFile:
        result = self._probe.describe(path, extension)
        return ProbedFile(
            duration_seconds=result.duration_seconds,
            size_bytes=path.stat().st_size,
            streams=tuple(result.streams),
        )

    def _assert_space(self, members: list[sqlite3.Row]) -> None:
        """**TS 経路のピークで見積もる。**

        TS フォールバックでは、全パートの `.ts` と結合後の出力が同時に
        `work/` に存在する。入力の合計しか要求しないと、`.ts` を作り終えた後の
        出力生成で ENOSPC になり、「始める前に止める」という約束を破る。
        """
        needed = TS_PEAK_FACTOR * sum(row["size_bytes"] for row in members)
        stat = os.statvfs(self._data_root)
        if needed + FREE_SPACE_MARGIN > stat.f_bavail * stat.f_frsize:
            raise NotEnoughSpace(f"{needed} バイトの結合に空き容量が足りない")


def _as_merge_parts(members: list[sqlite3.Row]) -> list[MergePart]:
    return [
        MergePart(
            media_file_id=row["media_file_id"],
            rel_path=row["rel_path"],
            sha1=row["sha1"],
            captured_at=datetime.fromisoformat(row["captured_at"]),
            duration_seconds=row["duration_seconds"],
            size_bytes=row["size_bytes"],
            probe_state=row["probe_state"],
        )
        for row in members
    ]


def _captured_of(row: sqlite3.Row) -> CapturedAt:
    """派生物は先頭パートの撮影日時を引き継ぐ."""
    return CapturedAt(
        at=datetime.fromisoformat(row["captured_at"]),
        source=row["captured_at_source"],
        tz=row["captured_at_tz"],
        note=row["captured_at_note"],
    )


def _recording_end_ns(members: list[sqlite3.Row]) -> int:
    """録画終了時刻（最後のパートの開始 + duration）を mtime にする（§9.8 手順 6）.

    **壁時計を UTC として解釈した epoch にする。** 取り込みの mtime は
    カード上の時刻欄をそのまま UTC 表現で読んだ値で（`timestamps.py`）、
    公開名の衝突接尾辞（`publisher._collision_stamp`）もその表現から作る。
    オフセット付きの瞬間を使うと、`library/` と `derived/` で接尾辞の壁時計が
    ずれる。
    """
    last = members[-1]
    start = datetime.fromisoformat(last["captured_at"]).replace(tzinfo=UTC)
    duration = last["duration_seconds"] or 0.0
    return int(start.timestamp() * 1e9) + int(duration * 1e9)
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_merger.py -q`
Expected: PASS（9 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `claim_for_merge` を `_merge` の後に呼ぶ | `test_a_changed_digest_is_refused_before_anything_runs`（work が作られる） |
| 入力の実在確認を消す | `test_a_missing_input_stops_the_job_and_fails_the_group` |
| `TS_PEAK_FACTOR` を 1 にする | `test_the_space_check_covers_the_ts_peak` |
| `probed_parts` を先頭パートの複製にする | **落ちない**。統合テストのクリップは全パートが同じ並びだから。**並びの違う入力での確認は Task 6 の `test_each_part_is_mapped_by_its_own_indexes` が受け持つ**（検出できない変異として記録する） |
| `outcome.dropped_by_route` を `verify` へ渡さない | **落ちない**。統合テストは concat 経路しか通らない（`dropped_by_route` は空）。Task 6 の `test_the_ts_route_drops_what_mpegts_cannot_carry_and_records_it` が受け持つ |
| `record_verification` を公開の後に移す | `test_the_verification_is_recorded_before_the_publish` |
| `except PublishInterrupted` を消して `mark_failed` に落とす | 同上（`status` が `failed` になる） |
| `except MergeCancelled` の `release` を消す | `test_a_cancelled_merge_releases_the_group` |
| `finally` の `rmtree` を消す | `test_the_work_directory_is_cleaned` |
| `_recording_end_ns` の `.replace(tzinfo=UTC)` を消す | `test_the_output_mtime_is_the_recording_end_in_wall_clock`（テストの `captured_at` は UTC 表記なので**落ちない**。**オフセット付きの `captured_at` を使うケースを足す**） |
| `verification.passed` で公開を分岐させる | `test_a_failed_verification_is_still_published` |
| `members[-1]` を `members[0]` にする | `test_the_output_mtime_is_the_recording_end_in_wall_clock` |

オフセット付きのテストを足す:

```python
def test_the_offset_in_captured_at_does_not_move_the_output_mtime(world, data_root, db):
    merger, ctx, profile, _, group_id = world
    db.execute(
        "UPDATE media_file SET captured_at = replace(captured_at, '+00:00', '+09:00')"
        " WHERE role = 'original'"
    )
    result = merger.run(ctx, group_id, "digest-1", profile)
    # 壁時計は 14:30:04 のまま。オフセットで 9 時間ずらさない。
    expected = datetime(2026, 8, 17, 14, 30, 4, tzinfo=UTC).timestamp()
    assert (data_root / result.rel_path).stat().st_mtime == pytest.approx(expected, abs=1)
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/jobs/merger.py app/tests/test_merger.py
git commit -m "feat(mediaferry): merge a group and publish the result"
```

---

### Task 11: 中断した結合の回収

**Files:**
- Modify: `app/src/mediaferry/jobs/reconcile.py`
- Test: `app/tests/test_reconciler.py`（追記）

**Interfaces:**
- Produces: `ReconcileReport.merges_completed: int` / `ReconcileReport.merges_released: int` / `ReconcileReport.merges_blocked: int`

**なぜ要るか:** 公開は `publisher._commit` が `merge_group.output_media_file_id` を
埋めるところまでを 1 トランザクションで行うが、`status` を `merged` にするのは
その後の `Merger` の仕事なので、間で落ちると `merging` のまま残る。§10 (b) は
`status = 'merged'` を要求するので選択肢には出ない（安全側に倒れている）が、
**再試行もできなくなる**。起動時に決着させる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_reconciler.py` に追記:

```python
def test_a_merge_that_reached_the_publish_is_completed(db, data_root):
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="merging")
    output_id = a_media_file(db, profile, role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    (data_root / "derived/dji-osmo/DCIM").mkdir(parents=True)
    (data_root / "derived/dji-osmo/DCIM/MERGED.MP4").write_bytes(b"x")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?",
               (output_id, group_id))

    report = _reconcile(db, data_root)

    assert report.merges_completed == 1
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "merged"
    )


def test_a_merge_that_never_published_is_released_for_a_retry(db, data_root):
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="merging")

    report = _reconcile(db, data_root)

    assert report.merges_released == 1
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "detected"
    )


def test_a_finished_group_is_left_alone(db, data_root):
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="skipped")
    _reconcile(db, data_root)
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "skipped"
    )


def test_a_group_with_an_unrecoverable_staging_is_not_released(db, data_root):
    """`StagingLost` を残したまま再試行できると、履歴が上書きされうる."""
    profile = _a_profile(db)
    group_id = a_merge_group(db, profile, "digest-1", status="merging")
    job_id = JobStore(db).enqueue("merge", {})
    # staged なのに実体が無い（手順 7〜10 の間で停止し、両方が失われた形）。
    a_staging(
        db, job_id, kind="merge", state="staged", merge_group_id=group_id,
        final_rel_path="derived/dji-osmo/DCIM/MERGED.MP4", expected_size=10,
        content_sha1="0" * 40, metadata_json="{}",
    )

    report = _reconcile(db, data_root)

    assert report.unrecoverable
    assert report.merges_blocked == 1
    assert report.merges_released == 0
    assert db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()[0] == (
        "merging"
    )
```

`a_staging` は `test_schema_artifacts` から import する。`metadata_json` は
`_recover_staging` が読むので、最低限 `{}` を入れる（`StagingLost` は実体と
ハッシュの突き合わせで出る）。

`_a_profile` と `_reconcile` は既存のテストが使っているヘルパに合わせる。無ければ
次を足す:

```python
def _a_profile(db):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    return (profile.profile_id, profile.revision_id)


def _reconcile(db, data_root):
    return Reconciler(
        db, data_root, ArtifactPublisher(db, data_root, MediaProbe()), JobStore(db)
    ).run()
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_reconciler.py -q`
Expected: FAIL（`AttributeError: 'ReconcileReport' object has no attribute 'merges_completed'`）

- [ ] **Step 3: 最小実装**

`ReconcileReport` に 2 つのカウンタを足す:

```python
@dataclass
class ReconcileReport:
    discarded: int = 0
    resumed: int = 0
    recommitted: int = 0
    missing: int = 0
    restored: int = 0
    cleaned_dirs: int = 0
    merges_completed: int = 0
    merges_released: int = 0
    # 回収できない staging を抱えていて、自動では動かせないグループ。
    merges_blocked: int = 0
    orphans: list[OrphanFile] = field(default_factory=list)
    unrecoverable: list[str] = field(default_factory=list)
```

`run()` に 1 段足す。**`_recover_staging` の後**に置くのが要点で、そこで公開が
完遂して `output_media_file_id` が入る。

```python
    def run(self) -> ReconcileReport:
        report = ReconcileReport()
        # 先にジョブを倒す。生きているジョブが無いことを確定させてから
        # staging と work を掃除する。
        self._store.sweep_interrupted()
        self._recover_staging(report)
        self._settle_merges(report)
        self._sync_missing(report)
        self._collect_orphans(report)
        self._clean_job_dirs(report)
        return report

    def _settle_merges(self, report: ReconcileReport) -> None:
        """`merging` のまま残ったグループを決着させる.

        公開まで進んでいれば（`output_media_file_id` が入っていれば）merged へ、
        進んでいなければ detected へ戻す。戻さないと再試行もできない。
        起動時に呼ぶので、走っているジョブは既に倒れている。

        **回収できなかった `artifact_staging` を抱えたグループは動かさない。**
        `_recover_staging` が `StagingLost` を残したものがこれにあたる。
        detected へ戻すと再試行でき、古い staged 行と新しい公開が同じ
        グループを指して、後の reconciliation がどちらの出力を書き込むかで
        履歴が上書きされる。「自動では続行しない」という契約を守る。
        """
        blocked = {
            row["merge_group_id"]
            for row in self._conn.execute(
                "SELECT DISTINCT merge_group_id FROM artifact_staging"
                " WHERE merge_group_id IS NOT NULL AND state <> 'published'"
            )
        }
        for row in self._conn.execute(
            "SELECT id, output_media_file_id FROM merge_group WHERE status = 'merging'"
        ).fetchall():
            if row["id"] in blocked:
                report.merges_blocked += 1
                logger.warning("結合 %s は回収できない staging を抱えている", row["id"])
                continue
            if row["output_media_file_id"] is not None:
                target, counter = "merged", "merges_completed"
            else:
                target, counter = "detected", "merges_released"
            self._conn.execute(
                "UPDATE merge_group SET status = ?, updated_at = ? WHERE id = ?",
                (target, now_iso(), row["id"]),
            )
            setattr(report, counter, getattr(report, counter) + 1)
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_reconciler.py app/tests/test_crash_consistency.py -q`
Expected: PASS

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `_settle_merges` を `_recover_staging` の前に置く | crash 試験（Task 14 で group status も assert するようにする。手順 7 で落とした `merge_prepared` のグループが `detected` + 出力ありになる） |
| `output_media_file_id is not None` の分岐を反転する | `test_a_merge_that_reached_the_publish_is_completed` |
| `WHERE status = 'merging'` を外す | `test_a_finished_group_is_left_alone` |
| `blocked` の判定を消す | `test_a_group_with_an_unrecoverable_staging_is_not_released` |
| `blocked` の条件を `state = 'writing'` だけにする | 同上（`staged` のまま残った行を拾えない） |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/jobs/reconcile.py app/tests/test_reconciler.py
git commit -m "feat(mediaferry): settle merges that were interrupted mid publish"
```

---

### Task 12: 選択肢の提示規則（§10 (b)）

**Files:**
- Create: `app/src/mediaferry/db/selection.py`
- Test: `app/tests/test_selection.py`

**Interfaces:**
- Consumes: `input_digest`（Task 2）、`ProfileRegistry`
- Produces:
  - `SelectionService(conn: sqlite3.Connection, registry: ProfileRegistry)`
  - `.selectable(include: Sequence[str] = ()) -> list[Selectable]`
  - `Selectable(media_file_id: str, rel_path: str, role: str, reason: str, merge_group_id: str | None)`
  - `INCLUDE_FAILED_GROUP_MEMBERS: str` / `INCLUDE_UNADOPTED_DERIVED: str`

**Phase 3 との境界:** ここで実装するのは (b)「既定で選択肢に出す」条件と、
フィルタで出せるものだけ。(a) 安全条件と (c) `selection_rule` ごとの条件は
`upload_record` を claim するときに評価するもので、Phase 3 で足す。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_selection.py`:

```python
import json

import pytest

from mediaferry.core.merge.digest import input_digest
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.selection import (
    INCLUDE_FAILED_GROUP_MEMBERS,
    INCLUDE_UNADOPTED_DERIVED,
    SelectionService,
)

from .test_schema_artifacts import a_media_file, a_merge_group

PASSED = json.dumps({"passed": True})
NOT_PASSED = json.dumps({"passed": False})


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


def a_group(db, profile, members, *, status="merged", verification=PASSED, adopted_at=None,
            output_id=None, digest=None):
    if digest is None:
        digest = input_digest(
            [(media_id, sha1) for media_id, sha1 in members],
            profile.definition.merge,
            profile.revision_id,
        )
    group_id = a_merge_group(
        db, (profile.profile_id, profile.revision_id), digest,
        status=status, verification_json=verification, adopted_at=adopted_at,
        output_media_file_id=output_id,
    )
    for position, (media_id, _) in enumerate(members):
        db.execute(
            "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
            " VALUES (?, ?, ?, 1)",
            (group_id, media_id, position),
        )
    return group_id


def a_pair(db, profile, prefix="P"):
    """rel_path は UNIQUE なので、複数のグループを作るときは prefix を変える."""
    return [
        (
            a_media_file(db, (profile.profile_id, profile.revision_id),
                         rel_path=f"library/dji-osmo/DCIM/{prefix}{index}.MP4",
                         sha1=f"{prefix}{index:039d}"),
            f"{prefix}{index:039d}",
        )
        for index in (1, 2)
    ]


def ids(result):
    return {item.media_file_id for item in result}


def test_a_plain_original_is_selectable(db, profile):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    result = SelectionService(db, ProfileRegistry(db)).selectable()
    assert ids(result) == {media_id}
    assert result[0].reason == "default"


def test_a_missing_file_is_not_selectable(db, profile):
    a_media_file(db, (profile.profile_id, profile.revision_id), missing_at="2026-08-17T00:00:00+00:00")
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_a_member_of_an_active_group_is_not_selectable(db, profile):
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    a_group(db, profile, members, output_id=output_id)
    assert ids(SelectionService(db, ProfileRegistry(db)).selectable()) == {output_id}


def test_a_verified_derived_output_is_selectable(db, profile):
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    group_id = a_group(db, profile, members, output_id=output_id)
    result = SelectionService(db, ProfileRegistry(db)).selectable()
    assert [item.merge_group_id for item in result] == [group_id]


def test_an_unadopted_failed_verification_is_not_selectable_by_default(db, profile):
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    a_group(db, profile, members, output_id=output_id, verification=NOT_PASSED)
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_an_adopted_failed_verification_is_selectable(db, profile):
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    a_group(db, profile, members, output_id=output_id, verification=NOT_PASSED,
            adopted_at="2026-08-17T00:00:00+00:00")
    assert ids(SelectionService(db, ProfileRegistry(db)).selectable()) == {output_id}


def test_a_stale_digest_takes_the_derived_output_out_of_the_list(db, profile):
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    a_group(db, profile, members, output_id=output_id, digest="stale-digest")
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_a_group_that_is_not_merged_yet_hides_both_sides(db, profile):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="merging")
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_failed_group_members_can_be_shown_with_a_filter(db, profile):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="failed", verification=None)
    service = SelectionService(db, ProfileRegistry(db))
    assert service.selectable() == []
    shown = service.selectable(include=[INCLUDE_FAILED_GROUP_MEMBERS])
    assert ids(shown) == {media_id for media_id, _ in members}
    assert {item.reason for item in shown} == {"failed_group_member"}


def test_skipped_group_members_can_be_shown_with_the_same_filter(db, profile):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="skipped", verification=None)
    shown = SelectionService(db, ProfileRegistry(db)).selectable(
        include=[INCLUDE_FAILED_GROUP_MEMBERS]
    )
    assert ids(shown) == {media_id for media_id, _ in members}


def test_a_string_false_is_not_a_pass(db, profile):
    """`bool("false")` は真になる. `passed` は真の bool のときだけ合格."""
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    a_group(db, profile, members, output_id=output_id,
            verification=json.dumps({"passed": "false"}))
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []


def test_the_list_is_capped(db, profile):
    for index in range(5):
        a_media_file(db, (profile.profile_id, profile.revision_id),
                     rel_path=f"library/dji-osmo/DCIM/C{index}.MP4")
    assert len(SelectionService(db, ProfileRegistry(db)).selectable(limit=3)) == 3


def test_the_members_are_read_in_one_query(db, profile, monkeypatch):
    """derived 1 件ごとに問い合わせない（グループが増えても query 数が伸びない）."""
    for index in range(3):
        members = a_pair(db, profile, prefix=f"G{index}")
        output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                                 rel_path=f"derived/dji-osmo/DCIM/M{index}.MP4")
        a_group(db, profile, members, output_id=output_id)

    calls = []
    real = db.execute
    monkeypatch.setattr(db, "execute", lambda sql, *args: (calls.append(sql), real(sql, *args))[1])
    SelectionService(db, ProfileRegistry(db)).selectable()

    assert len([sql for sql in calls if "merge_member mm" in sql and "JOIN media_file" in sql]) == 1


def test_unadopted_derived_outputs_can_be_shown_with_a_filter(db, profile):
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    a_group(db, profile, members, output_id=output_id, verification=NOT_PASSED)
    shown = SelectionService(db, ProfileRegistry(db)).selectable(
        include=[INCLUDE_UNADOPTED_DERIVED]
    )
    assert ids(shown) == {output_id}
    assert {item.reason for item in shown} == {"unadopted_derived"}
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_selection.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.db.selection'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/db/selection.py`:

```python
"""アップロードの選択肢（§10）.

「既定で選択肢に出す」条件をここ 1 か所に置く。画面・API・ワーカーが同じ
定義を使うためで、写しを作らない。

`input_digest` の一致は SQL だけでは判定できない（現行の構成・設定・
プロファイルリビジョンから計算し直す必要がある）ので、SQL で絞ってから
Python で確かめる。この一致を見ないと、**グループを編集した後に旧派生物が
選択肢へ戻る**（旧グループは `status = merged` のまま残るため）。

安全条件 (a) と `selection_rule` ごとの条件 (c) は claim のときに評価する
もので、`upload_record` と一緒に足す。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core.merge.digest import input_digest
from .profiles import ProfileRegistry

INCLUDE_FAILED_GROUP_MEMBERS = "failed_group_members"
INCLUDE_UNADOPTED_DERIVED = "unadopted_derived"
# 1 応答で返す上限。画面の pagination は Phase 4。
DEFAULT_LIMIT = 500

_ORIGINALS = (
    "SELECT m.id, m.rel_path, m.role FROM media_file m"
    " WHERE m.missing_at IS NULL AND m.role = 'original'"
    "   AND NOT EXISTS (SELECT 1 FROM merge_member mm"
    "                   WHERE mm.media_file_id = m.id AND mm.active = 1)"
    " ORDER BY m.captured_at DESC"
)

_DERIVED = (
    "SELECT m.id, m.rel_path, m.role, g.id AS merge_group_id, g.profile_id,"
    " g.input_digest, g.verification_json, g.adopted_at"
    " FROM media_file m JOIN merge_group g ON g.output_media_file_id = m.id"
    " WHERE m.missing_at IS NULL AND m.role = 'derived'"
    "   AND g.superseded_by_id IS NULL AND g.status = 'merged'"
    " ORDER BY m.captured_at DESC"
)

# `skipped` は Phase 2 では作られない（破棄は Phase 4）。§10 が「failed / skipped の
# グループの member」と定めているので、条件は最初から両方書いておく。
_MEMBERS_OF_UNMERGED = (
    "SELECT m.id, m.rel_path, m.role, g.id AS merge_group_id FROM media_file m"
    " JOIN merge_member mm ON mm.media_file_id = m.id"
    " JOIN merge_group g ON g.id = mm.merge_group_id"
    " WHERE m.missing_at IS NULL AND mm.active = 1"
    "   AND g.superseded_by_id IS NULL AND g.status IN ('failed', 'skipped')"
    " ORDER BY m.captured_at DESC"
)


@dataclass(frozen=True)
class Selectable:
    media_file_id: str
    rel_path: str
    role: str
    reason: str
    merge_group_id: str | None


class SelectionService:
    def __init__(self, conn: sqlite3.Connection, registry: ProfileRegistry) -> None:
        self._conn = conn
        self._registry = registry

    def selectable(self, include: Sequence[str] = (), limit: int = DEFAULT_LIMIT) -> list[Selectable]:
        """**返す件数に上限を置く。** 数万件の一覧を 1 応答に詰めない.

        呼び出し側は `len(result) == limit` で打ち切りを判断する。カーソルを
        使った本格的な pagination は、画面の要件が決まる Phase 4 で足す。
        """
        items = [
            Selectable(row["id"], row["rel_path"], row["role"], "default", None)
            for row in self._conn.execute(_ORIGINALS)
        ]
        wanted_unadopted = INCLUDE_UNADOPTED_DERIVED in include
        derived = self._conn.execute(_DERIVED).fetchall()
        # profile と member はまとめて引く。derived 1 件ごとに問い合わせると、
        # グループが数千あるだけで一覧を開くたびに数千回の query になる。
        matching = self._matching_digests(derived)
        for row in derived:
            if row["merge_group_id"] not in matching:
                continue
            adopted = row["adopted_at"] is not None
            passed = _verification_passed(row["verification_json"])
            if adopted or passed:
                items.append(
                    Selectable(
                        row["id"], row["rel_path"], row["role"], "default", row["merge_group_id"]
                    )
                )
            elif wanted_unadopted:
                items.append(
                    Selectable(
                        row["id"],
                        row["rel_path"],
                        row["role"],
                        INCLUDE_UNADOPTED_DERIVED,
                        row["merge_group_id"],
                    )
                )
        if INCLUDE_FAILED_GROUP_MEMBERS in include:
            items.extend(
                Selectable(
                    row["id"],
                    row["rel_path"],
                    row["role"],
                    "failed_group_member",
                    row["merge_group_id"],
                )
                for row in self._conn.execute(_MEMBERS_OF_UNMERGED)
            )
        return items[:limit]

    def _matching_digests(self, rows: Sequence[sqlite3.Row]) -> set[str]:
        """現行の構成・設定・リビジョンから計算し直し、一致した group を返す."""
        if not rows:
            return set()
        group_ids = [row["merge_group_id"] for row in rows]
        marks = ", ".join("?" * len(group_ids))
        members: dict[str, list[tuple[str, str]]] = {}
        for member in self._conn.execute(
            "SELECT mm.merge_group_id AS group_id, m.id AS media_file_id, m.sha1 AS sha1"
            " FROM merge_member mm JOIN media_file m ON m.id = mm.media_file_id"
            f" WHERE mm.merge_group_id IN ({marks}) AND mm.active = 1"  # noqa: S608
            " ORDER BY mm.merge_group_id, mm.position",
            group_ids,
        ):
            members.setdefault(member["group_id"], []).append(
                (member["media_file_id"], member["sha1"])
            )

        profiles: dict[str, Any] = {}
        matching: set[str] = set()
        for row in rows:
            profile = profiles.get(row["profile_id"])
            if profile is None:
                profile = profiles[row["profile_id"]] = self._registry.by_id(row["profile_id"])
            current = input_digest(
                members.get(row["merge_group_id"], []),
                profile.definition.merge,
                profile.revision_id,
            )
            if current == row["input_digest"]:
                matching.add(row["merge_group_id"])
        return matching


def _verification_passed(verification_json: str | None) -> bool:
    """`passed` が真の bool のときだけ合格.

    `bool(value)` にすると、`"passed": "false"` のような文字列まで合格に
    してしまう。
    """
    if verification_json is None:
        return False
    try:
        return json.loads(verification_json).get("passed") is True
    except (AttributeError, TypeError, ValueError):
        return False
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_selection.py -q`
Expected: PASS（15 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `_ORIGINALS` の `NOT EXISTS` を消す | `test_a_member_of_an_active_group_is_not_selectable` |
| `missing_at IS NULL` を消す | `test_a_missing_file_is_not_selectable` |
| `_DERIVED` の `g.status = 'merged'` を消す | `test_a_group_that_is_not_merged_yet_hides_both_sides` |
| `_DERIVED` の `superseded_by_id IS NULL` を消す | **落ちない**。supersede は Phase 4 で入るので、テストデータに supersede されたグループが無い。**`superseded_by_id` を立てたケースを足す**（下記） |
| `_matching_digests` を常に全件一致にする | `test_a_stale_digest_takes_the_derived_output_out_of_the_list` |
| member をグループごとに 1 回ずつ引く形へ戻す | `test_the_members_are_read_in_one_query` |
| `_verification_passed` を `bool(...)` に戻す | `test_a_string_false_is_not_a_pass` |
| `items[:limit]` を消す | `test_the_list_is_capped` |
| `adopted or passed` を `passed` だけにする | `test_an_adopted_failed_verification_is_selectable` |
| `adopted or passed` を `adopted` だけにする | `test_a_verified_derived_output_is_selectable` |
| `wanted_unadopted` の分岐を既定でも通す | `test_an_unadopted_failed_verification_is_not_selectable_by_default` |
| `_MEMBERS_OF_UNMERGED` の `status IN ('failed','skipped')` から `skipped` を外す | `test_skipped_group_members_can_be_shown_with_the_same_filter` |

supersede のテストを足す:

```python
def test_a_superseded_group_takes_its_output_out_of_the_list(db, profile):
    members = a_pair(db, profile)
    output_id = a_media_file(db, (profile.profile_id, profile.revision_id), role="derived",
                             rel_path="derived/dji-osmo/DCIM/MERGED.MP4")
    old = a_group(db, profile, members, output_id=output_id)
    newer = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-new")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, old))
    # 旧派生物は消え、構成ファイルは個別に選べるようになる。
    assert ids(SelectionService(db, ProfileRegistry(db)).selectable()) == {
        media_id for media_id, _ in members
    }
```

supersede すると trigger が member の `active` を 0 にするので、元のパートは
「アクティブなグループに属していない」に戻り、`default` として一覧に出る。
**この挙動は §10 のとおり**（旧グループの構成ファイルは個別に選べるようになる）。

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/db/selection.py app/tests/test_selection.py
git commit -m "feat(mediaferry): expose which media are offered for upload"
```

---

### Task 13: API とワイヤリング

**Files:**
- Create: `app/src/mediaferry/api/routes_merges.py`
- Modify: `app/src/mediaferry/api/jobs_wiring.py`
- Modify: `app/src/mediaferry/api/app.py`
- Test: `app/tests/test_api_merges.py`
- Test: `app/tests/test_merge_job_wiring.py`（`JobRunner` を通したキャンセルの決着）

**Interfaces:**
- Produces（§11 のうち Phase 2 の分）:

| メソッド | パス | 内容 |
| --- | --- | --- |
| POST | `/merge-groups/detect?profile_slug=` | 検出ジョブを開始。`profile_slug` 省略時は `merge.enabled` な全プロファイル |
| POST | `/merge-groups/preview?profile_slug=&tolerance_seconds=&min_part_size_gib=` | 閾値を変えた候補を**保存せず**返す |
| GET | `/merge-groups?status=&limit=&offset=` | 一覧（構成・gap・検証結果） |
| GET | `/merge-groups/{id}` | 詳細 |
| POST | `/merge-groups/{id}/merge` | 結合ジョブを開始（`detected` / `failed` から） |
| PATCH | `/merge-groups/{id}?action=adopt` | 採用（検証不合格の出力を中身を見て採る） |
| GET | `/uploads/selectable?include=&limit=` | §10 (b) の選択肢 |

- `JobWorld.run_detect_groups(ctx, conn) -> None` / `JobWorld.run_merge(ctx, conn) -> None`

**キャンセルは例外で上へ抜かさない。** `JobRunner._run_one` は例外をすべて
`finish(..., "failed", ...)` へ送るので、`MergeCancelled` / `PublishCancelled` を
そのまま送出すると、**利用者が押したキャンセルがジョブの失敗として記録される**
（`job.status = cancelling` でも `finish` は `failed` への更新を通す）。
`run_merge` が受け止めて正常 return し、`finish_claimed` の
`cancelling -> cancelled` に決着させる。取り込みも同じ形で降りている
（`Importer.run` はキャンセルを観測するとループを抜けて返す）。

**リクエストの受け方:** 既存のルータはどれも pydantic モデルを使っていない
（`routes_devices.py`）。Phase 2 も**クエリパラメータで受ける**。入力スキーマは
Web UI と一緒に Phase 4 で入れる。

**`POST /merge-groups/detect` は §11 の表に無いので、`design.md` に 1 行足す**
（Task 14）。preview だけでは保存する経路が無い。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_api_merges.py`:

```python
import json

import pytest
from fastapi.testclient import TestClient

from mediaferry.api.app import create_app
from mediaferry.db.connection import Database
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_media_file, a_merge_group


@pytest.fixture
def client(data_root, broker, monkeypatch):
    """既存の `test_api.py` と同じ組み立て. 起動時に migration と builtin 同期が走る."""
    monkeypatch.setenv("MEDIAFERRY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    app = create_app(broker_factory=lambda: broker)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def api_db(client, data_root):
    """API と同じ DB ファイルを、テスト用の別接続で開く.

    **接続は共有しない**（トランザクションは接続に属する）。`client` に依存
    させるのは、アプリの起動で migration とビルトインの同期を先に済ませるため。
    """
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


def test_detecting_enqueues_one_job_per_profile(client):
    response = client.post("/api/merge-groups/detect")
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert [entry["profile_slug"] for entry in jobs] == ["dji-osmo"]


def test_detecting_an_unknown_profile_is_a_404(client):
    assert client.post("/api/merge-groups/detect?profile_slug=nope").status_code == 404


def test_the_group_list_carries_its_members_and_verification(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    media_id = a_media_file(api_db, (profile.profile_id, profile.revision_id))
    group_id = a_merge_group(
        api_db, (profile.profile_id, profile.revision_id), "digest-1",
        verification_json=json.dumps({"passed": True, "route": "concat"}),
    )
    api_db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group_id, media_id),
    )
    body = client.get("/api/merge-groups").json()
    assert [group["id"] for group in body["groups"]] == [group_id]
    assert body["groups"][0]["verification"]["passed"] is True
    assert [member["media_file_id"] for member in body["groups"][0]["members"]] == [media_id]


def test_merging_fixes_the_digest_and_the_revision_in_the_job(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    job_id = client.post(f"/api/merge-groups/{group_id}/merge").json()["job_id"]
    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["merge_group_id"] == group_id
    assert params["input_digest"] == "digest-1"
    assert params["profile_revision_id"] == profile.revision_id


def test_adopting_a_group_without_an_output_is_a_409(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    assert client.patch(f"/api/merge-groups/{group_id}?action=adopt").status_code == 409


def test_discarding_is_not_offered_in_this_phase(client, api_db):
    """破棄は公開済みの media_file を取り残す. supersede が入る Phase 4 で足す."""
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    assert client.patch(f"/api/merge-groups/{group_id}?action=discard").status_code == 400


def test_an_unknown_action_is_a_400(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    assert client.patch(f"/api/merge-groups/{group_id}?action=explode").status_code == 400


def test_a_missing_group_is_a_404(client):
    assert client.get("/api/merge-groups/nope").status_code == 404
    assert client.post("/api/merge-groups/nope/merge").status_code == 404


def test_the_selectable_list_is_served(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    media_id = a_media_file(api_db, (profile.profile_id, profile.revision_id))
    body = client.get("/api/uploads/selectable").json()
    assert [item["media_file_id"] for item in body["selectable"]] == [media_id]
    assert body["selectable"][0]["reason"] == "default"


def test_the_preview_does_not_store_anything(client, api_db):
    body = client.post("/api/merge-groups/preview?profile_slug=dji-osmo").json()
    assert body["candidates"] == []
    assert api_db.execute("SELECT count(*) FROM merge_group").fetchone()[0] == 0
```

`JobRunner` を実際に通して、キャンセルの決着を見るテストを別ファイルに置く
（`app/tests/test_merge_job_wiring.py`）。ffmpeg は使わず、`MergeRunner` だけを
差し替えて `MergeCancelled` を出させる。**ジョブは `cancelled`、グループは
`detected` に落ち着く**ことを、実物の `Merger` と `JobRunner` を通して確かめる。

```python
import anyio
import pytest

from mediaferry.adapters.ffmpeg import MergeCancelled
from mediaferry.api.jobs_wiring import JobWorld
from mediaferry.core.merge.grouping import GIB, GroupCandidate, MergePart
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.jobs.runner import JobRunner

from .test_merger import BASE
from .test_schema_artifacts import a_media_file


class CancellingRunner:
    """結合の入口で止まり、テストの合図でキャンセルを観測した形にする.

    合図で待たせないと、ハンドラが `request_cancel` より先に終わって
    `finish_claimed` が `succeeded` を書き、テストが競合で揺れる。
    """

    started = threading.Event()
    proceed = threading.Event()

    def merge(self, *args, **kwargs):
        CancellingRunner.started.set()
        CancellingRunner.proceed.wait(timeout=10)
        raise MergeCancelled("キャンセル要求を観測した")


@pytest.mark.anyio
async def test_a_cancelled_merge_job_ends_as_cancelled(db, database, data_root, monkeypatch):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)

    members = []
    for index in (1, 2):
        name = f"DJI_20260817143000_{index:04d}_D.MP4"
        (directory / name).write_bytes(b"x" * 16)
        rel = f"library/dji-osmo/DCIM/DJI_001/{name}"
        media_id = a_media_file(
            db, (profile.profile_id, profile.revision_id), rel_path=rel,
            sha1=f"{index:040d}", size_bytes=16, duration_seconds=2.0,
            captured_at=BASE.isoformat(),
        )
        members.append(
            MergePart(media_id, rel, f"{index:040d}", BASE, 2.0, 16 * GIB, "ok")
        )

    repo = MergeRepository(db)
    group_id = repo.save_detected(
        profile, GroupCandidate(members=tuple(members), gaps=(0.0,)), "digest-1"
    )
    monkeypatch.setattr("mediaferry.api.jobs_wiring.MergeRunner", CancellingRunner)

    store = JobStore(db)
    job_id = store.enqueue(
        "merge",
        {
            "merge_group_id": group_id,
            "input_digest": "digest-1",
            "profile_id": profile.profile_id,
            "profile_revision_id": profile.revision_id,
        },
    )
    world = JobWorld(database, {"MEDIAFERRY_DATA_ROOT": str(data_root)}, volumes=None)
    runner = JobRunner(database, poll_interval=0.01)
    runner.register("merge", world.run_merge)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_forever)
        with anyio.fail_after(10):
            # ハンドラが結合に入るまで待つ。ここで止まっている。
            while not CancellingRunner.started.is_set():
                await anyio.sleep(0.01)
            store.request_cancel(job_id)
            CancellingRunner.proceed.set()
            while store.get(job_id)["status"] in {"running", "cancelling"}:
                await anyio.sleep(0.01)
        await runner.stop()

    assert store.get(job_id)["status"] == "cancelled"
    assert repo.get(group_id)["status"] == "detected"
```

`threading` を import し、`CancellingRunner` の 2 つの `Event` はテストごとに
作り直す（クラス変数のまま複数のテストを足すと状態が漏れる）。

`client` フィクスチャは `test_api.py` と同じものなので、**`conftest.py` へ移して
共有する**（同じ組み立てを 2 か所に書かない）。移したら `test_api.py` 側の定義は
消す。`api_db` はこのファイルに置いたままでよい。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_api_merges.py -q`
Expected: FAIL（404。ルータがまだ無い）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/api/routes_merges.py`:

```python
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
    return {
        "groups": [_group(repo, row) for row in repo.list_groups(status, limit, offset)]
    }


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
    return [
        profile for profile in registry.active() if profile.definition.merge.enabled
    ]


def _profile(registry: ProfileRegistry, profile_slug: str):  # noqa: ANN202
    try:
        return registry.current(profile_slug)
    except UnknownProfile as exc:
        raise HTTPException(status_code=404, detail=f"そのプロファイルは無い: {profile_slug}") from exc


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
```

`app/src/mediaferry/api/jobs_wiring.py` に 2 つのハンドラを足す:

```python
    def run_detect_groups(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        profile = _profile_ref(conn, ctx.params)
        outcome = GroupDetector(conn, MergeRepository(conn)).run(ctx, profile)
        ctx.emit(
            "info",
            f"検出完了: 新規 {outcome.created} 件 / 既存 {outcome.existing} 件"
            f" / 見送り {outcome.undefined} 件",
        )

    def run_merge(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        profile = _profile_ref(conn, ctx.params)
        settings = SettingsService(conn, self._env).snapshot()
        publisher = ArtifactPublisher(conn, settings.data_root, MediaProbe())
        merger = Merger(
            conn,
            MergeRepository(conn),
            publisher,
            MergeRunner(),
            MediaProbe(),
            settings.data_root,
        )
        try:
            result = merger.run(
                ctx, ctx.params["merge_group_id"], ctx.params["input_digest"], profile
            )
        except (MergeCancelled, PublishCancelled) as exc:
            # **協調キャンセルは「完了」として返す。** 送出すると JobRunner の
            # 例外経路が job を failed にし、利用者が押したキャンセルが失敗として
            # 記録される。正常 return すれば `finish_claimed` が
            # `cancelling -> cancelled` を決着させる（取り込みも同じ形で降りる）。
            ctx.emit("info", f"結合を中止した: {exc}")
            return
        ctx.emit(
            "info",
            f"結合完了: {result.rel_path}（経路 {result.route} /"
            f" 検証 {'合格' if result.passed else '不合格'}）",
        )
```

`jobs_wiring.py` の import には `MergeCancelled`（`adapters.ffmpeg`）、
`PublishCancelled`（`adapters.publisher`）、`MergeRunner`、`Merger`、
`MergeRepository`、`GroupDetector` を足す。

`_fixed_profile` と共通の解決を切り出す:

```python
def _fixed_profile(conn: sqlite3.Connection, selection: VolumeSelection) -> ProfileRef:
    """キュー投入時に固定したリビジョンを読む."""
    return _profile_ref(
        conn,
        {
            "profile_id": selection.profile_id,
            "profile_revision_id": selection.profile_revision_id,
        },
    )


def _profile_ref(conn: sqlite3.Connection, params: Mapping[str, Any]) -> ProfileRef:
    """params に固定したリビジョンを読む.

    現行リビジョンを読み直すと、キューで待っている間にプロファイルを
    編集しただけで、確認画面と違う規則で処理される。
    """
    registry = ProfileRegistry(conn)
    revision_id = params["profile_revision_id"]
    return ProfileRef(
        profile_id=params["profile_id"],
        revision_id=revision_id,
        revision=0,
        definition=registry.definition_of(revision_id),
    )
```

`app/src/mediaferry/api/app.py`:

```python
from .routes_merges import router as merges_router
...
        runner.register("scan", world.run_scan)
        runner.register("import", world.run_import)
        runner.register("detect_groups", world.run_detect_groups)
        runner.register("merge", world.run_merge)
...
    app.include_router(media_router, prefix="/api")
    app.include_router(merges_router, prefix="/api")
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_api_merges.py app/tests/test_merge_job_wiring.py app/tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `start_merge` の params から `input_digest` を落とす | `test_merging_fixes_the_digest_and_the_revision_in_the_job` |
| `start_merge` の params で現行リビジョンを読み直す | 同上（リビジョンは一致するので**落ちない**。プロファイルを編集してから enqueue するケースを足す） |
| `patch_group` の `action` の検証を消す | `test_an_unknown_action_is_a_400` |
| `patch_group` に `discard`（`mark_skipped`）を足す | `test_discarding_is_not_offered_in_this_phase` |
| `adopt` の `GroupNotClaimable` を 500 のまま通す | `test_adopting_a_group_without_an_output_is_a_409` |
| `_found` を消す | `test_a_missing_group_is_a_404` |
| `_targets` の `merge.enabled` の絞り込みを消す | `test_profiles_that_do_not_merge_are_not_detected` |
| `preview` が `save_detected` を呼ぶ | `test_the_preview_does_not_store_anything` |
| `truncated` を常に `False` にする | **落ちない**。上限に届くデータを API のテストで作っていない。`SelectionService` 側の `test_the_list_is_capped` が上限そのものを見ている（検出できない変異として記録する） |
| `run_merge` の `except (MergeCancelled, PublishCancelled)` を消して送出する | `test_a_cancelled_merge_job_ends_as_cancelled`（ジョブが `failed` になる） |
| `run_merge` がキャンセルを握りつぶして `succeeded` で返す（`return` を消して続行） | 同上（`MergeResult` が無いので `AttributeError` になり、やはり `failed`） |

リビジョンとプロファイル絞り込みのテストを足す:

```python
def test_the_job_keeps_the_revision_that_was_current_when_it_was_queued(client, api_db):
    profile = ProfileRegistry(api_db).current("dji-osmo")
    group_id = a_merge_group(api_db, (profile.profile_id, profile.revision_id), "digest-1")
    job_id = client.post(f"/api/merge-groups/{group_id}/merge").json()["job_id"]
    # 投入の後にプロファイルを編集する（新しいリビジョンができる）。
    api_db.execute(
        "INSERT INTO profile_revision (id, profile_id, revision, definition_json,"
        " schema_version, created_at)"
        " SELECT 'rev-new', profile_id, revision + 1, definition_json, schema_version, created_at"
        " FROM profile_revision WHERE id = ?",
        (profile.revision_id,),
    )
    api_db.execute(
        "UPDATE device_profile SET current_revision_id = 'rev-new' WHERE id = ?",
        (profile.profile_id,),
    )
    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["profile_revision_id"] == profile.revision_id


def test_profiles_that_do_not_merge_are_not_detected(client, api_db):
    """`archived` ではなく `merge.enabled = false` で確かめる.

    archive は `registry.active()` が先に外すので、`_targets` から
    `merge.enabled` の条件を消しても通ってしまう。
    """
    when = "2026-08-17T00:00:00+00:00"
    profile = ProfileRegistry(api_db).current("dji-osmo")
    definition = json.loads(
        api_db.execute(
            "SELECT definition_json FROM profile_revision WHERE id = ?", (profile.revision_id,)
        ).fetchone()[0]
    )
    definition["slug"] = "no-merge"
    definition["merge"]["enabled"] = False
    api_db.execute(
        "INSERT INTO device_profile (id, slug, name, builtin, created_at)"
        " VALUES ('p-nomerge', 'no-merge', 'No merge', 0, ?)",
        (when,),
    )
    api_db.execute(
        "INSERT INTO profile_revision (id, profile_id, revision, definition_json,"
        " schema_version, created_at) VALUES ('r-nomerge', 'p-nomerge', 1, ?, 1, ?)",
        (json.dumps(definition), when),
    )
    api_db.execute(
        "UPDATE device_profile SET current_revision_id = 'r-nomerge' WHERE id = 'p-nomerge'"
    )

    jobs = client.post("/api/merge-groups/detect").json()["jobs"]
    assert [entry["profile_slug"] for entry in jobs] == ["dji-osmo"]
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/api/routes_merges.py app/src/mediaferry/api/jobs_wiring.py \
        app/src/mediaferry/api/app.py app/tests/test_api_merges.py \
        app/tests/test_merge_job_wiring.py
git commit -m "feat(mediaferry): expose merge groups and upload candidates over the api"
```

---

### Task 14: 統合テストとドキュメント

**Files:**
- Create: `app/tests/test_merge_e2e.py`
- Modify: `docs/design.md`
- Modify: `docs/HANDOFF.md`
- Modify: `docs/phase1-manual-checklist.md`
- Modify: `docs/phase2-plan.md`（この計画。実装との差分を書き戻す）

- [ ] **Step 1: 一連の流れを通すテストを書く**

`app/tests/test_merge_e2e.py`:

```python
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from mediaferry.adapters.ffmpeg import MergeRunner
from mediaferry.adapters.ffprobe import MediaProbe
from mediaferry.adapters.publisher import ArtifactPublisher
from mediaferry.db.jobs import JobStore
from mediaferry.db.merges import MergeRepository
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.selection import SelectionService
from mediaferry.jobs.detect_groups import GroupDetector
from mediaferry.jobs.merger import Merger
from mediaferry.jobs.reconcile import Reconciler

from .test_schema_artifacts import a_media_file

BASE = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
GIB = 1024**3


def make_clip(path, seconds=2):
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg", "-nostdin", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x64:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-y", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def library(db, data_root):
    """検出の閾値を満たすように、size_bytes には 16 GiB を書く.

    実体は小さいクリップのまま。`min_part_size_gib` の判定は DB の値を見る
    ので、16 GiB のファイルを作らずに分割録画の並びを再現できる。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM" / "DJI_001"
    directory.mkdir(parents=True)
    for index in (1, 2):
        name = f"DJI_20260817143000_{index:04d}_D.MP4"
        make_clip(directory / name)
        a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/DJI_001/{name}",
            sha1=f"{index:040d}",
            size_bytes=16 * GIB,
            duration_seconds=2.0,
            captured_at=(BASE + timedelta(seconds=2 * (index - 1))).isoformat(),
        )
    return profile


def test_detect_merge_and_offer_the_result(db, data_root, library):
    profile = library
    repo = MergeRepository(db)
    store = JobStore(db)

    store.enqueue("detect_groups", {})
    detect_ctx = store.claim_next()
    assert GroupDetector(db, repo).run(detect_ctx, profile).created == 1
    group = repo.list_groups()[0]

    # 検出したグループは、まだ選択肢に出ない（構成ファイルも出ない）。
    assert SelectionService(db, ProfileRegistry(db)).selectable() == []

    store.enqueue("merge", {})
    merge_ctx = store.claim_next()
    merger = Merger(
        db, repo, ArtifactPublisher(db, data_root, MediaProbe()), MergeRunner(),
        MediaProbe(), data_root,
    )
    result = merger.run(merge_ctx, group["id"], group["input_digest"], profile)

    assert (data_root / result.rel_path).exists()
    offered = SelectionService(db, ProfileRegistry(db)).selectable()
    assert [item.media_file_id for item in offered] == [result.media_file_id]
    assert offered[0].merge_group_id == group["id"]


def test_the_digest_stops_matching_when_a_part_is_replaced(db, data_root, library):
    profile = library
    repo = MergeRepository(db)
    store = JobStore(db)
    store.enqueue("detect_groups", {})
    GroupDetector(db, repo).run(store.claim_next(), profile)
    group = repo.list_groups()[0]
    store.enqueue("merge", {})
    merger = Merger(
        db, repo, ArtifactPublisher(db, data_root, MediaProbe()), MergeRunner(),
        MediaProbe(), data_root,
    )
    merger.run(store.claim_next(), group["id"], group["input_digest"], profile)

    # パートの中身が差し替わると digest が合わなくなり、派生物は候補から外れる。
    db.execute("UPDATE media_file SET sha1 = 'edited' WHERE rel_path LIKE 'library/%' LIMIT 1")

    assert [
        item.reason for item in SelectionService(db, ProfileRegistry(db)).selectable()
    ] == ["default", "default"]


def test_a_merge_interrupted_after_staging_is_settled_at_startup(db, data_root, library):
    profile = library
    repo = MergeRepository(db)
    store = JobStore(db)
    store.enqueue("detect_groups", {})
    GroupDetector(db, repo).run(store.claim_next(), profile)
    group = repo.list_groups()[0]

    store.enqueue("merge", {})
    ctx = store.claim_next()
    merger = Merger(
        db, repo, ArtifactPublisher(db, data_root, MediaProbe()), MergeRunner(),
        MediaProbe(), data_root,
    )
    # 公開は終わったが mark_merged の前で落ちた状態を作る。
    merger._repo.mark_merged = lambda group_id: None
    merger.run(ctx, group["id"], group["input_digest"], profile)
    assert repo.get(group["id"])["status"] == "merging"

    report = Reconciler(
        db, data_root, ArtifactPublisher(db, data_root, MediaProbe()), JobStore(db)
    ).run()

    assert report.merges_completed == 1
    assert repo.get(group["id"])["status"] == "merged"
```

`SQLite` の `UPDATE ... LIMIT` はビルドオプション依存なので、2 番目のテストは
`WHERE id = (SELECT id FROM media_file WHERE role = 'original' ORDER BY rel_path LIMIT 1)`
に直す。

**この 3 番目のテストは `_settle_merges` の順序を守らない。** 公開が手順 11 まで
終わってから `mark_merged` だけを潰しているので、`_settle_merges` を
`_recover_staging` より前へ動かしても `output_media_file_id` は既に入っており、
通ってしまう。順序は次の Step で crash 試験に受け持たせる。

- [ ] **Step 2: crash 試験でグループの状態も確かめる**

`app/tests/test_crash_consistency.py` の本体テストに、`merge` / `merge_prepared`
のときだけグループの状態を見るアサーションを足す。**`_settle_merges` を
`_recover_staging` より前へ動かすと、手順 7 で落とした場合に「`detected` なのに
出力 ID が入っている」状態になり、ここで落ちる。**

```python
    if kind != "import":
        group = conn.execute("SELECT status, output_media_file_id FROM merge_group").fetchone()
        if step <= 6:
            # 公開へ進んでいない。再試行できる状態へ戻す。
            assert group["status"] == "detected"
            assert group["output_media_file_id"] is None
        else:
            # 公開は完遂している。出力 ID が入り、merged になる。
            assert group["status"] == "merged"
            assert group["output_media_file_id"] is not None
```

`crash_child.py` はグループを `merging` で作るので、reconciliation を経ずに
この状態にはならない。**`merge_group.verification_json` も埋めておく**
（`mark_merged` の CAS が検証結果を要求するため、`_settle_merges` が倒すときの
前提を crash 試験でも満たす）:

```python
def _a_merge_group(conn, profile):  # noqa: ANN001, ANN202
    from mediaferry.clock import now_iso

    group_id = new_id()
    conn.execute(
        "INSERT INTO merge_group (id, profile_id, profile_revision_id, status, input_digest,"
        " detected_by, verification_json, tool_version, created_at, updated_at)"
        " VALUES (?, ?, ?, 'merging', 'digest-1', 'auto', '{\"passed\": true}', 'ffmpeg', ?, ?)",
        (group_id, profile.profile_id, profile.revision_id, now_iso(), now_iso()),
    )
    return group_id
```

Run: `uv run pytest app/tests/test_crash_consistency.py -q`
Expected: PASS

- [ ] **Step 3: 通ることを確認する**

Run: `uv run pytest -q`
Expected: PASS（全件）

```bash
uv run pytest
uv run pytest -m needs_root
uv run ruff check .
uv run ruff format --check .
```

- [ ] **Step 4: `docs/design.md` を直す**

1. §11 の API 表に 1 行足す（`/merge-groups/preview` の上）:

```markdown
| POST | `/merge-groups/detect` | 結合グループの検出ジョブを開始（プロファイルごとに 1 本） |
```

2. §9.8 の検証の節に、実装で決めた実値を書き足す:

```markdown
判定に使う実値（Phase 2 の実装で確定）:

- 映像フレーム数は ffprobe の `nb_frames` だけを見る。`-count_frames` は
  30 GiB を全デコードするので使わない。取れないパートが 1 つでもあれば
  `inconclusive` とする
- 「`bit_rate` のばらつきが小さい」は `(max - min) / mean ≤ 0.1`。分散ではなく
  範囲で見るのは、パートが 2 本のときにも意味を持たせるため。**対応する
  ストリームごとに評価する**（合計で見ると、支配的な映像が音声の変動を隠す）
- 期待サイズは **`bit_rate` が取れた保持ストリームだけ**で組み立てる。取れなかった
  のが映像・音声なら `inconclusive`、data（`tmcd` など）なら推定から外して続ける。
  外したストリームは `verification_json` に残す。`tmcd` を理由に全体を
  `inconclusive` にすると、既定の DJI プロファイルでサイズ検査が常に無効になる
- 検証器の版は `verification_json.pipeline_version` に記録する。**`input_digest`
  には入れない**（入力の同一性の判定であって、検証器の同一性ではない。混ぜると
  閾値を変えるたびに既存の派生物が選択肢から消える）
- TS 経路は `mpegts` が運べないストリーム（QuickTime の data track）を外して
  結合する。外したものは `verification_json.route_dropped_streams` に残る。
  ストリーム検査は不合格になるが、出力は公開されるのでユーザが目視して採用できる
```

3. §21 に節を足して、Phase 2 で確定した契約を残す:

```markdown
### Phase 2 の実装で確定した事項（実装を終えた日付を入れる）

| 判断 | 理由 |
| --- | --- |
| **結合物の公開は `ArtifactPublisher.publish_prepared`（`os.link`）** | `write` コールバックで staging へ書き直すと 30 GiB をもう一度書く。`work/` と `staging/` は同一ファイルシステム（§7）なので link で移せる。11 手順と回収の性質は `publish` と同じ |
| **`publish_prepared` の SHA-1 走査中も heartbeat とキャンセル確認を続ける** | 30 GiB の走査はリース（60 秒）より長い。打たないと、読み切った後の手順 7 で失効し、正しく生成・検証済みの結合物が捨てられる |
| **staged より前の「中断できない長い処理」は `_with_lease_pulse` で囲む** | `os.fsync`（30 GiB の直後は数十秒）と ffprobe（timeout がリースと同値）は途中で止められず、chunk の合間の heartbeat では守れない。処理を別スレッドへ出し、待つ側が打つ（DB へ触るのは待つ側だけなので接続は 1 本のまま）。**取り込み側にも同じ穴があり、共通の `_publish` を直すことで両方に効く** |
| **キャンセルは例外で `JobRunner` まで上げない** | `_run_one` は例外をすべて `failed` にするので、利用者が押したキャンセルがジョブの失敗として記録される。`run_merge` が受け止めて正常 return し、`finish_claimed` の `cancelling -> cancelled` に決着させる（取り込みも同じ形で降りている） |
| **検証結果は公開の前に commit する** | 公開の途中で落ちても検証をやり直さない。`merging` のまま残ったグループは起動時に決着させる |
| **`merging` のまま残ったグループは、出力の有無で merged / detected へ倒す** | 倒さないと再試行もできない。`_recover_staging` の後に走らせる（そこで公開が完遂して `output_media_file_id` が入る）。**回収できない `artifact_staging` を抱えたグループは動かさない**（再試行させると、古い staged 行と新しい公開が同じグループを指す） |
| **公開後にできる操作は採用だけ。破棄と再結合は Phase 4** | どちらも公開済みの `media_file` を取り残す。旧グループを `superseded_by_id` で向け直す仕組みが要り、それは手動編集と共通なので画面と一緒に入れる |
| **派生物の mtime は「壁時計を UTC として解釈した epoch」** | 取り込みの mtime と同じ表現にする。オフセット付きの瞬間を使うと、`library/` と `derived/` で衝突接尾辞の壁時計がずれる |
| **map はパートごとに、そのパート自身の ffprobe 結果から作る** | 保持 signature が同じでも、保持しない data track の挿入位置が違えば絶対 index は変わる。先頭の index を使い回すと、同じ codec の別トラックを黙って拾う |
| **concat demuxer は preflight してから使う** | demuxer は最初のファイルの構成を全体に適用する。全ストリームの構成と保持対象の index の並びが一致しないときは、試さずに TS へ送る |
| **TS 経路は mpegts が運べないストリームを外して記録する** | 外さないと mux が拒否して、検証できる出力そのものができない（既定の DJI プロファイルは `timecode: true` なので fallback が常に使えなくなる） |
| **空き容量は TS 経路のピーク（入力合計の 2 倍）で見積もる** | `.ts` の中間物と出力が同時に `work/` に置かれる。入力合計だけだと、始めた後の出力生成で ENOSPC になる |
| **リースの延長は throttle し、キャンセル確認だけを細かく回す** | poll のたびに延ばすと 30 分の結合で数千回 WAL へ書き、API とキャンセルの書き込みロックに競合する |
| **検出は「アクティブな member」を境界として扱う** | 列から取り除くだけだと、その前後がつながって別の録画を 1 つのグループにする |
| **`record_verification` と `mark_merged` は成立条件を DB 側で確かめる** | 呼び出し順のバグ 1 つで「merged なのに出力が無い」行ができ、選択肢の側が隠すので静かに残る |
```

- [ ] **Step 5: `docs/HANDOFF.md` を直す**

- §1 の表で Phase 2 を **完了**にし、検証状態のテスト件数を実測値に直す
- §3「Phase 1 で確定した契約」に倣い、**Phase 2 で確定した契約**の表を足す
  （上の §21 と同じ内容を要約して置く。Phase 3 が蒸し返さないように）
- §5「次にやること」を Phase 3（Immich 同期）の入口に書き換える
- §7「持ち越している判断」に、Task 3 の `attached_pic` の確認を足す

- [ ] **Step 6: `docs/phase1-manual-checklist.md` に 1 項目足す**

**Task 3 の実装時に追記済み**（判定を書いた場所で足す方が取りこぼさない）。内容は次のとおり。

```markdown
### 12. 埋め込みサムネイルの disposition を確かめる

結合が「最初の映像ストリームのみ」を保持する判定は、`disposition.attached_pic`
でサムネイルを見分けている（`core/merge/streams.py`）。

```bash
ffprobe -v error -print_format json -show_streams /path/to/DJI_....MP4
```

- `mjpeg` のストリームに `"attached_pic": 1` が立っている → 判定は正しい
- 立っていない → `keep_streams.video` が `primary` の間は影響しないが、`all` を
  使うプロファイルを足すときに `_is_thumbnail` の判定を増やす必要がある
```

- [ ] **Step 7: この計画に差分を書き戻してコミット**

実装で計画から外れた判断があれば、この `docs/phase2-plan.md` の該当タスクに
書き戻す。検出できなかった変異も同様。

```bash
git add docs/ app/tests/test_merge_e2e.py
git commit -m "docs(mediaferry): record what phase 2 settled"
```

---

## Phase 2 の完了条件（§20）

> 分割動画が結合され、検証結果と選択肢が API で取れる。

| 条件 | 確かめ方 |
| --- | --- |
| 分割動画が結合される | `test_merge_e2e.py::test_detect_merge_and_offer_the_result` |
| 検証結果が API で取れる | `GET /merge-groups/{id}` の `verification` |
| 選択肢が API で取れる | `GET /uploads/selectable` |
| 中断しても回収できる | `test_crash_consistency.py` の `merge_prepared`（11 段）と `test_merge_e2e.py` の起動時決着 |
| 実 USB での確認 | `phase1-manual-checklist.md` の 11 項目（Phase 1 から持ち越し）+ 12 番 |

## Phase 2 でやらないこと（意図的な除外）

| 項目 | いつ |
| --- | --- |
| 手動でのグループ分割・結合（`detected_by = manual` / supersede） | Phase 4（画面ありきの操作。スキーマと trigger は Phase 1 で入っている） |
| 継ぎ目サムネイルの画像生成、`ThumbnailService`、`GET /media/{id}/thumbnail` | Phase 4（Phase 2 は秒数を `verification_json` に残すところまで） |
| `upload_record` / `selection_rule` / §10 (a)(c) の claim 時評価 | Phase 3 |
| **公開済み結合物の破棄（`skipped` へ倒す）と再結合** | Phase 4。どちらも旧 `output_media_file_id` と `media_file` を取り残すので、supersede が要る。`status = failed`（何も公開していない）からの結合実行は Phase 2 でできる |
| カーソルによる pagination | Phase 4（Phase 2 は `limit` と `truncated` だけ） |
| 入力スキーマ（pydantic モデル）と CSRF | Phase 4 |

## レビュー記録

### 1 巡目（2026-08-18、codex。blocker 4 / major 7 / minor 1）

実装着手の前に依頼した。**11 件を反映し、1 件を退けた。**

| # | 指摘 | 反映先 |
| --- | --- | --- |
| 1 [blocker] | `publish_prepared` の 30 GiB SHA-1 走査中に heartbeat もキャンセル確認も無く、リースが失効して検証済みの結合物が捨てられる | Task 7（`_materialise_link` に時間ベースの pulse と chunk ごとのキャンセル判定、`PublishCancelled`） |
| 2 [blocker] | `StagingLost` を残したままグループを `detected` へ戻すので、古い staged 行と新しい公開が同じグループを指す | Task 11（`merges_blocked`。未解決の `artifact_staging` を持つグループは動かさない） |
| 3 [blocker] | 先頭パートの絶対 stream index を全パートの TS 化に使い回している | Task 6 / 10（パートごとに map を作る。concat は preflight してから使う） |
| 4 [blocker] | 検証不合格後の「再試行・破棄」が状態遷移として実装できず、範囲宣言と矛盾 | 範囲を「公開後は採用だけ」に決め直した（`mark_skipped` を落とし、API は `adopt` のみ） |
| 5 [major] | `tmcd` を map したまま mpegts 化するので、既定プロファイルでは TS 経路が常に失敗する | Task 6（`UNSUPPORTED_BY_TS`。外して `route_dropped_streams` に記録する） |
| 6 [major] | TS 経路のピーク容量を過小評価している | Task 10（`TS_PEAK_FACTOR = 2`） |
| 7 [major] | heartbeat を 0.5 秒ごとに SQLite へ書いている | Task 6（`PULSE_INTERVAL`。キャンセル確認と分ける） |
| 8 [major] | `tmcd` に `bit_rate` が無いため既定でサイズ検査が常に無効。ばらつきを合計で見ている | Task 5（`ESTIMABLE_TYPES` と `excluded_streams`、ばらつきはストリームごと） |
| 9 [major] | reconciliation 順序の変異試験が順序逆転を検出しない | Task 14 Step 2（crash 試験でグループの状態も assert する） |
| 10 [major] | 状態遷移メソッドが成立条件を DB 側で確認していない | Task 8（`record_verification` / `mark_merged` を CAS に） |
| 11 [major] | `selectable` が無制限 + derived ごとの N+1。`bool(value)` が `"false"` を合格にする | Task 12（batch query、`DEFAULT_LIMIT` と `truncated`、`is True`） |
| 12 [minor] | `merge.enabled` の変異を検出できないテスト | Task 13（archive ではなく `enabled: false` の第 2 プロファイルで確かめる） |

### 2 巡目（2026-08-18、codex。blocker 2）

1 巡目の反映を確認させたところ、接続部に 2 件残っていた。**どちらも反映した。**

| # | 指摘 | 反映先 |
| --- | --- | --- |
| 1 [blocker] | キャンセル例外を再送出すると `JobRunner._run_one` が `failed` にするので、利用者が押したキャンセルが失敗として記録される | Task 13（`run_merge` が受け止めて正常 return。`JobRunner` を通して job=cancelled / group=detected を見る統合テストを足した） |
| 2 [blocker] | pulse を入れたのは SHA-1 の read loop だけで、その後の `os.fsync` と ffprobe（timeout がリースと同値）で再び失効しうる | Task 7（`_with_lease_pulse`。処理を別スレッドへ出し、待つ側が打つ。**取り込み側の同じ穴も共通の `_publish` で塞がる**） |

`MERGE_PIPELINE_VERSION` の反論は受け入れられた。「将来、既知の不具合がある
検証版を無効化する必要が出たら、digest を変えるのではなく eligibility 側の
『再検証が必要』ポリシーとして扱う」という補足も、Phase 4 以降の判断材料として
ここに残す。

**退けた指摘: `MERGE_PIPELINE_VERSION` を `input_digest` に入れる（1 巡目 #8 の一部）。**
`input_digest` は §8 で「構成ファイルの ordered な id と sha1、結合設定、
プロファイルリビジョン」と定義され、その役割は §10 のとおり**入力の同一性**の
判定である。検証の閾値はプロファイルの `merge` 節にも無く、入力ではない。
混ぜると、閾値を 1 つ変えただけで既存の結合物がそろって既定の選択肢から消え、
再結合するまで戻らない。検証器の版は
`verification_json.pipeline_version` に残し、画面で「古い版で検証された」と
示せるようにした（Task 5）。

