# 撮影地のタイムゾーンの上書き Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取り込んだ後に、選んだファイルだけ「撮影地のタイムゾーン」へ付け替えられるようにする（瞬間は変えない）。

**Architecture:** `media_file.captured_at_zone_override` に上書きを持つ。変換は `core/timestamps.py` の純粋関数 `with_zone_override` 1 つに置き、`POST /media/timezone`（`db/capture_zone.py`）と `Recomputer` の両方が使う。送信済みの差し戻しは `Recomputer` から `db/capture_zone.py` へ関数として取り出して共有する。画面は写真の選択バーから付け替える。

**Tech Stack:** Python 3.14 / FastAPI / SQLite、React + Vite + vitest

**Spec:** [`phase17-design.md`](phase17-design.md)

## Global Constraints

- すべての Python モジュールは `from __future__ import annotations` で始める
- コメントと docstring は日本語。**過去の経緯を書かない**（現在形で説明する）
- ruff: `line-length = 100`、`select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`
- 環境固有の値（ホスト名・パス・実在の TZ 設定値）をコードにもテストにも書かない。テストで使うゾーン名は IANA の公開名（`Asia/Tokyo` / `Asia/Ho_Chi_Minh`）でよい
- システム時刻の生成は `mediaferry.clock` だけを使う。`captured_at` はオフセット付きで保存する（UTC へ正規化しない）
- 移行ファイルは追加のみ。`app/tests/migration_checksums.txt` に 1 行足す
- API を変えたら `npm --prefix web run typegen` で `web/src/api/types.ts` を作り直す
- 失敗するテストを先に書き、失敗を確かめてから実装する。変異試験は `PYTHONDONTWRITEBYTECODE=1` を付け、`git checkout` を使わず scratchpad に控えを取ってから壊す
- コミットは Conventional Commits + 日本語の本文（なぜそうしたか）。セッション URL を入れない

## Review Focus

1. **上書きした後にプロファイルを保存して再計算する** → 付け替えが残る（Task 3 の `test_recompute_keeps_the_zone_override`）
2. **結合した動画のタイルを選んで付け替える** → 元パート全員と結合動画の両方が付け替わる（Task 4 の `test_a_derived_id_spreads_to_its_active_members`）
3. **送信済みを付け替える** → `complete` が `needs_recheck` に戻り、動かなかった行は戻らない（Task 4 の `test_changed_rows_are_requeued_and_unchanged_are_not`）
4. **選んだ中に `timezone_policy: none` のファイルが混ざる** → 400 で全体を断り、1 行も書かない（Task 4 の `test_a_none_policy_row_rejects_the_whole_request`）
5. **上書きを外す（`null`）** → カメラの時計のゾーンへ戻り、瞬間は変わらない（Task 4 の `test_clearing_returns_to_the_camera_zone`）

---

### Task 1: 移行 `0002` で列を足す

**Files:**
- Create: `app/src/mediaferry/db/migrations/0002_capture_zone_override.sql`
- Modify: `app/tests/migration_checksums.txt`
- Test: `app/tests/test_db_migrate.py`

**Interfaces:**
- Produces: `media_file.captured_at_zone_override TEXT`（NULL 可）

- [ ] **Step 1: 失敗するテストを書く**（`test_db_migrate.py` の末尾）

```python
def test_capture_zone_override_is_added_and_empty(db):
    """既存行を壊さずに列だけ足す. 上書きは利用者が付けるまで無い."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(media_file)")}
    assert "captured_at_zone_override" in columns
```

- [ ] **Step 2: 失敗を確かめる**

Run: `uv run pytest app/tests/test_db_migrate.py -k capture_zone -v`
Expected: FAIL（`assert ... in columns`）

- [ ] **Step 3: 移行を書く**

```sql
-- 撮影地のタイムゾーンの上書き（Phase 17）。
-- NULL は上書きなし。値は IANA のゾーン名で、正しさは API が検める
-- （SQL ではゾーン名を判定できない）。`derived` の行では常に NULL
-- （結合した動画は先頭の active member から継ぐ）。
ALTER TABLE media_file ADD COLUMN captured_at_zone_override TEXT;
```

`sha256sum app/src/mediaferry/db/migrations/0002_capture_zone_override.sql` の値で
`migration_checksums.txt` に `0002_capture_zone_override.sql <sha256>` を足す。

- [ ] **Step 4: 通ることを確かめる**

Run: `uv run pytest app/tests/test_db_migrate.py -v`
Expected: PASS（`test_a_shipped_migration_is_never_edited` も含む）

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/db/migrations/0002_capture_zone_override.sql app/tests/migration_checksums.txt app/tests/test_db_migrate.py
git commit -m "feat(db): 撮影地のタイムゾーンの上書きを持つ列を足す"
```

---

### Task 2: `with_zone_override`

**Files:**
- Modify: `app/src/mediaferry/core/timestamps.py`（`resolve_captured_at` の後ろに置く）
- Test: `app/tests/test_timestamps.py`

**Interfaces:**
- Produces: `with_zone_override(value: CapturedAt, zone_name: str | None) -> CapturedAt`

- [ ] **Step 1: 失敗するテストを書く**

```python
from mediaferry.core.timestamps import CapturedAt, with_zone_override

VIETNAM = "Asia/Ho_Chi_Minh"


def _jst(at: str = "2026-09-22T20:16:31+09:00") -> CapturedAt:
    return CapturedAt(
        at=datetime.fromisoformat(at), source="filename", tz="Asia/Tokyo", note="メモ"
    )


def test_zone_override_keeps_the_instant_and_changes_the_wall_clock():
    """時計を JST のまま持って行った: 数字は JST の数字なので、瞬間は保つ."""
    moved = with_zone_override(_jst(), VIETNAM)
    assert moved.at.isoformat() == "2026-09-22T18:16:31+07:00"
    assert moved.at == _jst().at
    assert moved.tz == VIETNAM


def test_zone_override_keeps_source_and_note():
    moved = with_zone_override(_jst(), VIETNAM)
    assert (moved.source, moved.note) == ("filename", "メモ")


def test_no_zone_override_passes_through():
    assert with_zone_override(_jst(), None) == _jst()
```

- [ ] **Step 2: 失敗を確かめる**

Run: `uv run pytest app/tests/test_timestamps.py -k zone_override -v`
Expected: FAIL（`ImportError: cannot import name 'with_zone_override'`）

- [ ] **Step 3: 実装する**

```python
def with_zone_override(value: CapturedAt, zone_name: str | None) -> CapturedAt:
    """瞬間を保ったまま、表示のゾーンを撮影地のものへ付け替える.

    プロファイルの timezone はカメラの時計のゾーンで、撮影地とは限らない
    （時計を合わせずに旅行先で撮る）。**付け替えるのは見せ方だけ**で、
    ファイル名や EXIF から解いた瞬間は変えない。`source` と `note` は
    その瞬間をどう解いたかの記録なので保つ。

    `timezone_policy: none` の値は瞬間ではないので、呼び出し側が渡さない。
    """
    if zone_name is None:
        return value
    return CapturedAt(
        at=value.at.astimezone(ZoneInfo(zone_name)),
        source=value.source,
        tz=zone_name,
        note=value.note,
    )
```

- [ ] **Step 4: 通ることを確かめる**

Run: `uv run pytest app/tests/test_timestamps.py -v`
Expected: PASS

- [ ] **Step 5: 変異試験**（控えを scratchpad に取る）
  - `astimezone(...)` を `replace(tzinfo=ZoneInfo(zone_name))` に → 1 本目が落ちる
  - `tz=zone_name` を `tz=value.tz` に → 1 本目が落ちる
  - `note=value.note` を `note=None` に → 2 本目が落ちる
  - `if zone_name is None: return value` を消す → 3 本目が落ちる（`ZoneInfo(None)` で TypeError）

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core/timestamps.py app/tests/test_timestamps.py
git commit -m "feat(timestamps): 瞬間を保って撮影地のゾーンへ付け替える関数を足す"
```

---

### Task 3: 差し戻しを共有の関数にし、再計算が上書きを守る

**Files:**
- Create: `app/src/mediaferry/db/capture_zone.py`（この Task では差し戻し 2 関数だけ）
- Modify: `app/src/mediaferry/jobs/recompute.py`（`_fetch_originals` / `_recomputed_original` / `_requeue` / `_reopen_stack`）
- Test: `app/tests/test_recompute.py`

**Interfaces:**
- Consumes: `with_zone_override`（Task 2）、列 `captured_at_zone_override`（Task 1）
- Produces:
  - `requeue_sent(conn: sqlite3.Connection, media_id: str, *, fixes_datetime: bool) -> int`
  - `reopen_skipped_stack(conn: sqlite3.Connection, media_id: str) -> int`

- [ ] **Step 1: 失敗するテストを書く**（`test_recompute.py`。既存の `dji` fixture の `part2` を使う）

```python
def test_recompute_keeps_the_zone_override(db, data_root, dji):
    """プロファイルを保存して再計算しても、利用者の付け替えを JST へ戻さない."""
    profile, _, _, part2, _ = dji
    db.execute(
        "UPDATE media_file SET captured_at_zone_override = 'Asia/Ho_Chi_Minh',"
        " captured_at = '2026-08-17T13:00:00+07:00', captured_at_tz = 'Asia/Ho_Chi_Minh'"
        " WHERE id = ?",
        (part2,),
    )
    run(db, data_root, profile)

    row = db.execute(
        "SELECT captured_at, captured_at_tz FROM media_file WHERE id = ?", (part2,)
    ).fetchone()
    assert (row["captured_at"], row["captured_at_tz"]) == (
        "2026-08-17T13:00:00+07:00",
        "Asia/Ho_Chi_Minh",
    )
```

（`dji` fixture は `(profile, volume, part1, part2, orphan)` を返す。`run` は同じファイルの
既存ヘルパー。`part2` のカード上の名前は `DJI_20260817150000_...` なので
JST 15:00 = ベトナム 13:00。）

- [ ] **Step 2: 失敗を確かめる**

Run: `uv run pytest app/tests/test_recompute.py -k zone_override -v`
Expected: FAIL（`2026-08-17T15:00:00+09:00` へ戻っている）

- [ ] **Step 3: 実装する**

`db/capture_zone.py` を作り、`Recomputer._requeue` / `_reopen_stack` の本体（SQL と
docstring の現在の理由）をそのまま関数へ移す。

```python
"""撮影地のタイムゾーンの上書き（Phase 17）と、撮影日時を動かした後始末."""

from __future__ import annotations

import sqlite3

from ..clock import now_iso


def requeue_sent(conn: sqlite3.Connection, media_id: str, *, fixes_datetime: bool) -> int:
    """送信済みを `needs_recheck` へ戻す（`CLAIMABLE_STATES` に入っている）.

    （`Recomputer._requeue` の docstring の本文をそのまま移す）
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

    （`Recomputer._reopen_stack` の docstring の本文をそのまま移す）
    """
    return conn.execute(
        "UPDATE upload_record SET stack_state = NULL, stack_reason = NULL, updated_at = ?"
        " WHERE media_file_id = ? AND stack_state = 'skipped' AND invalidated_at IS NULL",
        (now_iso(), media_id),
    ).rowcount
```

`recompute.py` の変更:

```python
# _apply_batch の中
tally.requeued += requeue_sent(
    self._conn, row["id"],
    fixes_datetime=profile.definition.immich.fix_datetime_after_upload,
)
tally.reopened += reopen_skipped_stack(self._conn, row["id"])
```

`_requeue` / `_reopen_stack` メソッドは消す。`_fetch_originals` の SELECT に
`m.captured_at_zone_override` を足し、`_recomputed_original` の戻り値を包む。

```python
        resolved = resolve_captured_at(...)  # 既存の呼び出しそのまま
        # **利用者が付けた撮影地を再計算で消さない。** 解き直すのはカメラの時計で、
        # 撮影地はその上に載る見せ方なので、解いた瞬間へ毎回付け直す。
        return with_zone_override(resolved, row["captured_at_zone_override"])
```

- [ ] **Step 4: 通ることを確かめる**

Run: `uv run pytest app/tests/test_recompute.py -v`
Expected: PASS（既存の差し戻し・見送りのテストも含む）

- [ ] **Step 5: 変異試験**
  - `with_zone_override(resolved, ...)` を `resolved` に → 新しいテストが落ちる
  - `requeue_sent` の `if not fixes_datetime: return 0` を消す → 既存の「日時を書き戻さないプロファイルは戻さない」テストが落ちることを確かめる（落ちなければ記録に残す）

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/capture_zone.py app/src/mediaferry/jobs/recompute.py app/tests/test_recompute.py
git commit -m "feat(recompute): 撮影地の上書きを再計算で消さない"
```

---

### Task 4: `apply_zone_override` と API

**Files:**
- Modify: `app/src/mediaferry/db/capture_zone.py`
- Modify: `app/src/mediaferry/api/routes_media.py`（`POST /media/timezone`・`GET /timezones`・`_media` に列を足す）
- Modify: `web/src/api/types.ts`（typegen）
- Create: `app/tests/test_capture_zone.py`

**Interfaces:**
- Consumes: `with_zone_override`、`requeue_sent`、`reopen_skipped_stack`、`ProfileRegistry.definition_of(revision_id)`
- Produces:
  - `class ZoneOverrideInvalid(ValueError)`
  - `@dataclass(frozen=True) class ZoneOutcome: changed: int; unchanged: int; requeued: int`
  - `apply_zone_override(conn, media_ids: Sequence[str], zone: str | None, default_timezone: str | None) -> ZoneOutcome`
  - `known_zones() -> list[str]`（`sorted(available_timezones())`）
  - `POST /api/media/timezone` 本文 `{"ids": [...], "timezone": str | null}` → `{"changed", "unchanged", "requeued"}`、不正は 400 `bad_request`
  - `GET /api/timezones` → `{"timezones": [...]}`
  - `GET /api/media` と `GET /api/media/{id}` の各行に `captured_at_zone_override`

- [ ] **Step 1: 失敗するテストを書く**（`app/tests/test_capture_zone.py`）

```python
"""撮影地のタイムゾーンの上書き（Phase 17）.

**瞬間は変えない。** 時計を JST のまま旅行先で撮ったファイルを、撮影地の
壁時計で見せるように付け替えるだけ。
"""

from __future__ import annotations

import pytest

from mediaferry.db.capture_zone import ZoneOverrideInvalid, apply_zone_override

from .test_recompute import a_user_profile
from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_uploads import a_destination, an_upload

TOKYO = "Asia/Tokyo"
VIETNAM = "Asia/Ho_Chi_Minh"


@pytest.fixture
def dji(db):
    ref = a_user_profile(db, "dji-osmo", "my-dji", timezone=TOKYO)
    return (ref.profile_id, ref.revision_id)


def a_jst_video(db, profile, at="2026-09-22T20:16:31+09:00", **over):
    return a_media_file(db, profile, captured_at=at, captured_at_tz=TOKYO, **over)


def captured(db, media_id):
    row = db.execute(
        "SELECT captured_at, captured_at_tz, captured_at_zone_override"
        " FROM media_file WHERE id = ?",
        (media_id,),
    ).fetchone()
    return tuple(row)


def test_overriding_moves_the_wall_clock_and_keeps_the_instant(db, dji):
    video = a_jst_video(db, dji)
    outcome = apply_zone_override(db, [video], VIETNAM, TOKYO)
    assert captured(db, video) == ("2026-09-22T18:16:31+07:00", VIETNAM, VIETNAM)
    assert (outcome.changed, outcome.unchanged) == (1, 0)


def test_clearing_returns_to_the_camera_zone(db, dji):
    video = a_jst_video(db, dji)
    apply_zone_override(db, [video], VIETNAM, TOKYO)
    apply_zone_override(db, [video], None, TOKYO)
    assert captured(db, video) == ("2026-09-22T20:16:31+09:00", TOKYO, None)


def test_applying_the_same_zone_twice_changes_nothing(db, dji):
    video = a_jst_video(db, dji)
    apply_zone_override(db, [video], VIETNAM, TOKYO)
    outcome = apply_zone_override(db, [video], VIETNAM, TOKYO)
    assert (outcome.changed, outcome.unchanged) == (0, 1)


def test_changed_rows_are_requeued_and_unchanged_are_not(db, dji):
    destination = a_destination(db)
    moved = a_jst_video(db, dji)
    already = a_jst_video(db, dji, at="2026-09-22T18:00:00+07:00", captured_at_tz=VIETNAM,
                          captured_at_zone_override=VIETNAM)
    moved_upload = an_upload(db, destination, moved, state="complete")
    already_upload = an_upload(db, destination, already, state="complete")

    outcome = apply_zone_override(db, [moved, already], VIETNAM, TOKYO)

    states = dict(db.execute("SELECT id, state FROM upload_record").fetchall())
    assert states[moved_upload] == "needs_recheck"
    assert states[already_upload] == "complete"
    assert outcome.requeued == 1


def test_a_derived_id_spreads_to_its_active_members(db, dji):
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    second = a_jst_video(db, dji, at="2026-09-22T19:10:00+09:00")
    output = a_jst_video(db, dji, role="derived", at="2026-09-22T19:00:00+09:00")
    group = a_merge_group(db, dji, digest="d", status="merged", output_media_file_id=output)
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, first))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group, second))

    apply_zone_override(db, [output], VIETNAM, TOKYO)

    assert captured(db, first)[1:] == (VIETNAM, VIETNAM)
    assert captured(db, second)[1:] == (VIETNAM, VIETNAM)
    # 結合した動画は先頭から継ぐ. 自分では上書きを持たない。
    assert captured(db, output) == ("2026-09-22T17:00:00+07:00", VIETNAM, None)


def test_changing_the_first_member_carries_to_the_output(db, dji):
    first = a_jst_video(db, dji, at="2026-09-22T19:00:00+09:00")
    output = a_jst_video(db, dji, role="derived", at="2026-09-22T19:00:00+09:00")
    group = a_merge_group(db, dji, digest="d", status="merged", output_media_file_id=output)
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, first))

    apply_zone_override(db, [first], VIETNAM, TOKYO)

    assert captured(db, output)[:2] == ("2026-09-22T17:00:00+07:00", VIETNAM)


def test_a_none_policy_row_rejects_the_whole_request(db, dji):
    video = a_jst_video(db, dji)
    generic = a_user_profile(db, "generic-dcim", "my-generic")  # timezone_policy: none
    plain = a_media_file(
        db, (generic.profile_id, generic.revision_id), captured_at="2026-09-22T20:16:31+00:00"
    )
    with pytest.raises(ZoneOverrideInvalid):
        apply_zone_override(db, [video, plain], VIETNAM, TOKYO)
    assert captured(db, video) == ("2026-09-22T20:16:31+09:00", TOKYO, None)


@pytest.mark.parametrize("zone", ["Mars/Olympus", "../etc/passwd", ""])
def test_an_unknown_zone_is_rejected(db, dji, zone):
    with pytest.raises(ZoneOverrideInvalid):
        apply_zone_override(db, [a_jst_video(db, dji)], zone, TOKYO)


def test_an_unknown_id_is_rejected(db, dji):
    video = a_jst_video(db, dji)
    with pytest.raises(ZoneOverrideInvalid):
        apply_zone_override(db, [video, "nope"], VIETNAM, TOKYO)
    assert captured(db, video)[2] is None


def test_the_api_applies_and_reports(client, db, dji):
    video = a_jst_video(db, dji)
    body = client.post("/api/media/timezone", json={"ids": [video], "timezone": VIETNAM})
    assert body.status_code == 200
    assert body.json() == {"changed": 1, "unchanged": 0, "requeued": 0}
    detail = client.get(f"/api/media/{video}").json()
    assert detail["captured_at_zone_override"] == VIETNAM


def test_the_api_rejects_with_400(client, db, dji):
    video = a_jst_video(db, dji)
    response = client.post("/api/media/timezone", json={"ids": [video], "timezone": "Mars/X"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_the_zone_list_contains_iana_names(client):
    zones = client.get("/api/timezones").json()["timezones"]
    assert VIETNAM in zones
    assert zones == sorted(zones)
```

**注意:** `an_upload(db, dest, media_id, **over)` の `dest` は `a_destination` が返す
タプルそのもの。`client` と `db` は同じ `data_root` の DB ファイルを開くので、`db` で
入れた行は API から見える。`an_upload` が `upload_record` の必須列を全部埋めるかは、
書く前に `test_schema_uploads.py` で確かめる。

- [ ] **Step 2: 失敗を確かめる**

Run: `uv run pytest app/tests/test_capture_zone.py -v`
Expected: FAIL（`ImportError: cannot import name 'ZoneOverrideInvalid'`）

- [ ] **Step 3: 実装する**（`db/capture_zone.py` に足す）

```python
class ZoneOverrideInvalid(ValueError):
    """付け替えられない要求. 何も書かずに全体を断る."""


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

    **1 つのトランザクションで全部か何もしないか。** 一部だけ付け替えると、
    どれが直ったかを画面から読めない。検めに落ちた時点で ROLLBACK する。

    `derived` の id は、その active member 全員へ置き換える（写真の一覧は結合した
    動画もタイルに出す）。上書きを持つのは `original` だけで、`derived` は先頭の
    active member から継ぐ。
    """
    if zone is not None and zone not in available_timezones():
        raise ZoneOverrideInvalid(f"IANA タイムゾーンとして解釈できない: {zone}")
    if not media_ids:
        raise ZoneOverrideInvalid("ids が空")
    registry = ProfileRegistry(conn)
    changed = unchanged = requeued = 0
    touched: list[str] = []
    with immediate(conn):
        for row in _originals_of(conn, media_ids):
            defn = registry.definition_of(
                row["captured_at_revision_id"] or row["profile_revision_id"]
            )
            if defn.timestamp.timezone_policy == "none":
                raise ZoneOverrideInvalid(
                    f"{row['rel_path']} はカメラの時計のゾーンが決まっていないので付け替えられない"
                )
            # 外すときはカメラの時計のゾーンへ戻す. これも瞬間を保つ変換。
            target = zone or defn.timestamp.timezone or default_timezone
            if target is None:
                raise ZoneOverrideInvalid(f"{row['rel_path']} のカメラの時計のゾーンが決まらない")
            value = with_zone_override(_captured(row), target)
            if (
                value.at.isoformat() == row["captured_at"]
                and value.tz == row["captured_at_tz"]
                and zone == row["captured_at_zone_override"]
            ):
                unchanged += 1
                continue
            conn.execute(
                "UPDATE media_file SET captured_at = ?, captured_at_tz = ?,"
                " captured_at_zone_override = ? WHERE id = ?",
                (value.at.isoformat(), value.tz, zone, row["id"]),
            )
            changed += 1
            touched.append(row["id"])
            requeued += _after_move(conn, row["id"], defn)
        for output in _outputs_led_by(conn, touched):
            requeued += _inherit(conn, output, registry)
    return ZoneOutcome(changed, unchanged, requeued)
```

補助関数（同じファイル）:

- `_originals_of(conn, ids)`: `SELECT * FROM media_file WHERE id IN (...)` で引く。
  見つからない id があれば `ZoneOverrideInvalid`。`role = 'derived'` の行は
  `SELECT f.* FROM merge_group g JOIN merge_member mm ON mm.merge_group_id = g.id AND mm.active = 1 JOIN media_file f ON f.id = mm.media_file_id WHERE g.output_media_file_id = ?`
  で member へ置き換え、member が 0 件なら `ZoneOverrideInvalid("つなぎ直す前の古い動画は付け替えられない")`。
  重複は id で除く（組のタイルとその元パートを一緒に選びうる）
- `_captured(row) -> CapturedAt`: `datetime.fromisoformat(row["captured_at"])` と
  `source` / `tz` / `note` から作る
- `_after_move(conn, media_id, defn) -> int`: `requeue_sent(..., fixes_datetime=defn.immich.fix_datetime_after_upload)` と `reopen_skipped_stack` を呼び、前者の件数を返す
- `_outputs_led_by(conn, member_ids) -> list[str]`: 先頭の active member（`position` 最小）が
  `member_ids` に入っている `merge_group.output_media_file_id`
- `_inherit(conn, output_id, registry) -> int`: `Recomputer._recomputed_derived` と同じ
  SQL で先頭 member の `captured_at` / `captured_at_tz` を読み、出力の行と違えば書いて
  `_after_move` を呼ぶ（出力のプロファイルの定義で）

API（`routes_media.py`）:

```python
@router.post("/media/timezone")
def override_capture_zone(
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
) -> dict[str, int]:
    """選んだファイルを撮影地のタイムゾーンへ付け替える（瞬間は変えない）."""
    ids = body.get("ids")
    zone = body.get("timezone")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ApiError(400, ErrorCode.BAD_REQUEST, "ids は文字列の配列")
    if zone is not None and not isinstance(zone, str):
        raise ApiError(400, ErrorCode.BAD_REQUEST, "timezone は文字列か null")
    try:
        outcome = apply_zone_override(conn, ids, zone, state.settings.default_timezone)
    except ZoneOverrideInvalid as exc:
        raise ApiError(400, ErrorCode.BAD_REQUEST, str(exc)) from exc
    return {"changed": outcome.changed, "unchanged": outcome.unchanged, "requeued": outcome.requeued}


@router.get("/timezones")
def list_timezones() -> dict[str, list[str]]:
    return {"timezones": known_zones()}
```

**ルートの順序に注意:** `POST /media/timezone` は `/media/{media_id}` と方式が違うので
衝突しないが、`GET` を足すなら `/media/{media_id}` より前に置く。

`_media(row)` に `"captured_at_zone_override": row["captured_at_zone_override"]` を足す
（一覧の SELECT が `m.*` でなければ列を足す。`_media` を使う全ての SELECT を確かめる）。

型を作り直す: `npm --prefix web run typegen`

- [ ] **Step 4: 通ることを確かめる**

Run: `uv run pytest app/tests/test_capture_zone.py app/tests/test_api_types_are_current.py app/tests/test_api_media.py -v`
Expected: PASS

- [ ] **Step 5: 変異試験**（1 つずつ壊して、どのテストが落ちるかを記録する）
  - `zone not in available_timezones()` の検めを消す → `test_an_unknown_zone_is_rejected`
  - `timezone_policy == "none"` の検めを消す → `test_a_none_policy_row_rejects_the_whole_request`
  - `target = zone or ...` を `target = zone` に → `test_clearing_returns_to_the_camera_zone`
  - `unchanged` の比較から `zone == row[...]` を消す → 検出できるか確かめ、できなければ理由を記録
  - `_after_move` を呼ばない → `test_changed_rows_are_requeued_and_unchanged_are_not`
  - derived を member へ置き換えない → `test_a_derived_id_spreads_to_its_active_members`
  - `_outputs_led_by` を空にする → `test_changing_the_first_member_carries_to_the_output`

- [ ] **Step 6: 全体を回す**

Run: `uv run pytest -q > "$SCRATCH/pytest.log" 2>&1; tail -5 "$SCRATCH/pytest.log"; uv run ruff check . && uv run ruff format --check .`
Expected: 失敗 0

- [ ] **Step 7: コミット**

```bash
git add app/src/mediaferry/db/capture_zone.py app/src/mediaferry/api/routes_media.py app/tests/test_capture_zone.py web/src/api/types.ts web/openapi.json
git commit -m "feat(api): 選んだファイルを撮影地のタイムゾーンへ付け替える"
```

---

### Task 5: 画面 —— 選択バーから付け替え、くわしくで上書きを示す

**Files:**
- Create: `web/src/components/ZoneDialog.tsx`、`web/src/components/ZoneDialog.test.tsx`
- Modify: `web/src/screens/Photos.tsx`（選択バーにボタン）、`web/src/screens/Photos.test.tsx`
- Modify: `web/src/screens/PhotoDetail.tsx`（一文）、`web/src/screens/PhotoDetail.test.tsx`

**Interfaces:**
- Consumes: `POST /media/timezone`、`GET /timezones`、`captured_at_zone_override`
- Produces: `ZoneDialog({ onApply: (zone: string | null) => void; onClose: () => void; busy: boolean })`

- [ ] **Step 1: 失敗するテストを書く**

`ZoneDialog.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { stubApi } from "../test/api";
import { ZoneDialog } from "./ZoneDialog";

describe("撮影地のタイムゾーンのダイアログ", () => {
  it("候補にあるゾーンを選ぶと、その名前で適用する", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Ho_Chi_Minh", "Asia/Tokyo"] } });
    const onApply = vi.fn();
    render(<ZoneDialog onApply={onApply} onClose={() => {}} busy={false} />);

    await userEvent.type(await screen.findByLabelText("撮影地のタイムゾーン"), "Asia/Ho_Chi_Minh");
    await userEvent.click(screen.getByRole("button", { name: "付け替える" }));

    expect(onApply).toHaveBeenCalledWith("Asia/Ho_Chi_Minh");
  });

  it("候補に無い名前では適用できない", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Tokyo"] } });
    render(<ZoneDialog onApply={vi.fn()} onClose={() => {}} busy={false} />);

    await userEvent.type(await screen.findByLabelText("撮影地のタイムゾーン"), "Mars/X");

    expect(screen.getByRole("button", { name: "付け替える" })).toBeDisabled();
  });

  it("上書きを外せる", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Tokyo"] } });
    const onApply = vi.fn();
    render(<ZoneDialog onApply={onApply} onClose={() => {}} busy={false} />);

    await userEvent.click(await screen.findByRole("button", { name: "上書きを外す" }));

    expect(onApply).toHaveBeenCalledWith(null);
  });
});
```

`Photos.test.tsx`（`describe("写真の画面")` の中）:

```tsx
  it("選んだものを撮影地のタイムゾーンへ付け替え、一覧を読み直す", async () => {
    const bodies: unknown[] = [];
    const api = stubApi(
      {
        "/media": {
          media: [media("a", "2026-09-22T20:16:31+09:00"), media("b", "2026-09-22T19:00:00+09:00")],
          total: 2,
          page: 1,
          page_size: 200,
        },
        "/destinations": { destinations: [] },
        "/timezones": { timezones: ["Asia/Ho_Chi_Minh"] },
        "POST /media/timezone": { changed: 2, unchanged: 0, requeued: 0 },
      },
      (path, init) => {
        if (path === "/media/timezone") bodies.push(JSON.parse(String(init?.body)));
      },
    );
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("checkbox", { name: /をまとめて選ぶ/ }));
    await userEvent.click(screen.getByRole("button", { name: "撮影地のタイムゾーン" }));
    await userEvent.type(await screen.findByLabelText("撮影地のタイムゾーン"), "Asia/Ho_Chi_Minh");
    await userEvent.click(screen.getByRole("button", { name: "付け替える" }));

    await waitFor(() => expect(screen.queryByText(/件を選択中/)).not.toBeInTheDocument());
    expect(bodies).toEqual([{ ids: ["a", "b"], timezone: "Asia/Ho_Chi_Minh" }]);
    expect(api.calls().filter((c) => c.path.startsWith("/media?")).length).toBeGreaterThan(1);
  });
```

`PhotoDetail.test.tsx`: 既存の詳細の fixture に `captured_at_zone_override: "Asia/Ho_Chi_Minh"` を
足した版を描き、`screen.getByText(/撮影地のタイムゾーンを Asia\/Ho_Chi_Minh に付け替えています/)` を
確かめる。`null` の版ではその文が無いことも確かめる。

- [ ] **Step 2: 失敗を確かめる**

Run: `npm --prefix web test -- ZoneDialog Photos PhotoDetail`
Expected: FAIL（`ZoneDialog` が無い／ボタンが無い）

- [ ] **Step 3: 実装する**

`ZoneDialog.tsx`: `ConfirmDialog` と同じ `useDialogFocus` を使うモーダル。`useQuery<{ timezones: string[] }>("/timezones")` で候補を読み、`<input list>` + `<datalist>` で入力を絞り込む。ラベルは「撮影地のタイムゾーン」。値が候補に含まれるときだけ「付け替える」を押せる。「上書きを外す」は `onApply(null)`。説明文は「撮った瞬間はそのままで、表示する時刻を撮影地のものに直します。カメラの時計を日本時間のまま旅行先で撮ったときに使います。」。

`Photos.tsx` の選択バー（「送る」と「やめる」の間）:

```tsx
<button type="button" className="btn" onClick={() => setZoneOpen(true)}>
  撮影地のタイムゾーン
</button>
```

```tsx
const [zoneOpen, setZoneOpen] = useState(false);
const [zoneBusy, setZoneBusy] = useState(false);
const [zoneError, setZoneError] = useState<unknown>(null);

async function applyZone(zone: string | null) {
  setZoneBusy(true);
  try {
    await request("/media/timezone", {
      method: "POST",
      body: { ids: [...selected.keys()], timezone: zone },
    });
    setZoneOpen(false);
    setSelected(new Map());
    media.reload();
  } catch (error) {
    setZoneError(error);
  } finally {
    setZoneBusy(false);
  }
}
```

失敗は既存の `ErrorBanner` で出す。

`PhotoDetail.tsx`: 撮影日時の行の下に、`data.captured_at_zone_override !== null` のときだけ

```tsx
<p className="muted">
  撮影地のタイムゾーンを {data.captured_at_zone_override} に付け替えています（撮った瞬間は変えていません）。
</p>
```

型（`PhotoDetail` の `Detail` 型、`MediaTile` の `Media` 型）に `captured_at_zone_override: string | null` を足す。

- [ ] **Step 4: 通ることを確かめる**

Run: `npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build`
Expected: PASS

- [ ] **Step 5: E2E を単独で回す**（並行させると偽陽性が出る）

Run: `npm --prefix web run test:e2e`
Expected: PASS（既存が壊れていないこと）

- [ ] **Step 6: コミット**

```bash
git add web/src
git commit -m "feat(web): 選んだ写真を撮影地のタイムゾーンへ付け替える"
```

---

### Task 6: 仕様と記録を更新する

**Files:**
- Modify: `docs/design.md`（§6 の撮影日時、`media_file` の列一覧、API 表に `POST /media/timezone` と `GET /timezones`）
- Modify: `docs/known-issues.md`（自作と証明できない Immich 資産には付け替えが届かない）
- Modify: `docs/user-guide.md`（旅行先で撮ったときの直し方）
- Modify: `docs/history/README.md`（Phase 17 の行とファイル）
- Create: `docs/history/phase17-record.md`（変異試験の結果、検出できなかった変異とその理由）

- [ ] **Step 1: 書く**（現在形で。経緯は `phase17-*` にだけ置く）
- [ ] **Step 2: 全体の受け入れ**

```bash
uv run pytest -q > "$SCRATCH/pytest.log" 2>&1; tail -5 "$SCRATCH/pytest.log"
uv run ruff check . && uv run ruff format --check .
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck
npm --prefix web run test:e2e
```

- [ ] **Step 3: コミット**

```bash
git add docs
git commit -m "docs: 撮影地のタイムゾーンの上書きを仕様と利用者向けの案内に書く"
```

### 実機で確かめること（PR を出した後、イメージを `:sha-xxxxxxx` で入れ替えてもらってから）

- 9/19〜9/22 の DJI の動画を写真の画面で選び、`Asia/Ho_Chi_Minh` に付け替える
- `GET /api/media?limit=200` で該当行が `+07:00` になり、瞬間が変わっていないこと
- Immich へ送ると現地の時刻で表示されること（送信は画面から利用者が行う）
