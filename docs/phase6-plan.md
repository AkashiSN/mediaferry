# Phase 6 実装計画 — RAW / JPEG のスタッキング

> **エージェントで実行する場合:** `superpowers:subagent-driven-development`
> または `superpowers:executing-plans` を使い、タスク単位で進める。
> 手順はチェックボックス（`- [ ]`）で追跡する。

**目標:** 同じシャッターで出た RAW と JPEG を、Immich 上で 1 つのスタックに束ねる。
あわせて、効かないまま 3 フェーズ持ち越された `UPLOAD_CONCURRENCY` を撤去する。

**方式:** スタックはアップロードの**後始末**として、同じ upload ジョブの**第 2 パス**で
回す。`upload_record` の状態機械には状態を足さず、3 列（`stack_state` /
`remote_stack_id` / `stack_reason`）で結果を持つ。組の同一性は**カード上の原名**
（`source_entry`）と `captured_at` で取る。

**技術:** Python 3.12 / SQLite / httpx / FastAPI / React + TypeScript / Playwright。

**仕様の正本:** [`docs/design.md`](design.md) §6「スタッキング（`stack`）」、§8、
§9.10、§9.11、§12、§20、§21「Phase 6 の設計で確定した事項」。**この計画は仕様から
論を進めるので、実行者は両方を読む。**

## 全体の制約（すべてのタスクに掛かる）

- Python は `>=3.12`。すべてのモジュールは `from __future__ import annotations` で始める
- ruff: `line-length = 100`、`select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`。
  `docs/` は対象外
- **コメントと docstring は日本語**で、**現在形**で書く。過去の経緯は `docs/` へ
- **環境固有の値をリポジトリに含めない**（IP、ホスト名、データセットのパス、API キー、
  タイムゾーンの実値）
- **DB に絶対パスを保存しない。** `DATA_ROOT` からの相対パスだけが正規形
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**
- システム時刻は **UTC の ISO-8601 文字列**で DB に入れ、生成は `mediaferry.clock` の
  関数だけを使う。**例外は `media_file.captured_at`**（解決したオフセット付き）
- **DB 接続はスコープごとに 1 本。** トランザクションは接続に属する
- 各タスクは「失敗するテストを書く → 失敗を確認 → 最小実装 → 通ることを確認 →
  変異試験 → コミット」で完結させる
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付ける。** ドライバはリポジトリに無い
  （`docs/HANDOFF.md` §5 の仕様どおり scratchpad に置いて使い捨てる）
- コミットは Conventional Commits + 日本語の本文。**なぜそうしたか**を本文に残す

## 範囲

| 入れるもの | 入れないもの |
| --- | --- |
| プロファイルの `stack` 節（§6） | 組を GUI で組み立てる仕組み（YAML のテキストエリアのまま） |
| `0015`（3 列・トリガ・部分索引・設定行の掃除） | `media_file` への `stack_key` の実体化（§21 の判断） |
| upload ジョブの第 2 パス（§9.11） | スタックの手動作成・解除の操作（YAGNI。再評価は再送と再計算で足りる） |
| Immich クライアントのスタック経路 + fake への実装 | 動画とサムネイルの組（`MVI_*.MOV` は組にしない） |
| `UPLOAD_CONCURRENCY` の撤去 | **ワーカーの多重化そのもの**（やらないと決めた。§21） |
| 見送りの理由を出す画面と E2E | Immich 側のスタックをこちらへ取り込む同期 |

## ファイル構成

**作る:**

| ファイル | 責務 |
| --- | --- |
| `app/src/mediaferry/db/migrations/0015_stacking.sql` | 3 列・トリガ 2 本・部分索引・`app_setting` の掃除 |
| `app/src/mediaferry/core/uploads/stacking.py` | **純関数**。原名から stem を取る、4 条件で組を決める、primary を選ぶ |
| `app/src/mediaferry/jobs/stacker.py` | 第 2 パス本体（抽出・リース・相手との往復・記録） |
| `app/tests/test_stacking_rules.py` | `stacking.py` の単体 |
| `app/tests/test_stacker.py` | 第 2 パスの単体（fake Immich） |
| `app/tests/test_stack_migration.py` | `0015` のトリガと索引 |

**触る:**

| ファイル | 変更 |
| --- | --- |
| `app/src/mediaferry/core/profiles/model.py` | `StackRule` と `_parse_stack`。**省略時は無効**（既存リビジョンとの互換） |
| `app/src/mediaferry/core/profiles/builtin/canon-eos.yaml` | `stack` を有効に（`[JPG, CR2]`、`tolerance_seconds: 0`） |
| `app/src/mediaferry/core/profiles/builtin/dji-osmo.yaml` / `generic-dcim.yaml` | `stack.enabled: false` |
| `app/src/mediaferry/adapters/immich.py` | `RemoteAsset.stack_id` / `stack_primary_asset_id`、`RemoteStack`、`create_stack` / `stack_by_primary` / `set_stack_primary` |
| `app/src/mediaferry/db/uploads.py` | `unstacked_batch` / `source_of` / `siblings_on_card` / `record_for` / `mark_stacked` / `mark_skipped` |
| `app/src/mediaferry/api/jobs_wiring.py` | 3 つの mode すべてのあとで第 2 パスを回す |
| `app/src/mediaferry/jobs/recompute.py` | `captured_at` を動かしたら `skipped` を未評価へ戻す |
| `app/src/mediaferry/db/profiles.py` | 新しいリビジョンを作る取引の中で `skipped` を未評価へ戻す（規則が変わったので前の判断は根拠を失う） |
| `app/src/mediaferry/settings.py` | `UPLOAD_CONCURRENCY` の撤去 |
| `app/src/mediaferry/api/routes_uploads.py` | `_view` に 3 列、`GET /uploads` に `stack_state` フィルタ |
| `app/src/mediaferry/api/routes_system.py` | 宛先サマリに `stacked` / `stack_skipped` |
| `app/tests/fake_immich.py` | `/api/stacks` の 3 経路と、既存スタックの状態 |
| `app/tests/exif_fixtures.py` | `a_tiff_with()`（合成 CR2 の中身） |
| `app/tests/system/harness.py` | Canon カードに RAW+JPEG の対を足す |
| `web/src/screens/Destinations.tsx` | 宛先ごとの「スタック」節（見送りの理由） |
| `web/src/screens/Dashboard.tsx` | サマリに 2 つのカウント |
| `web/src/screens/Settings.tsx` | 新規プロファイルの雛形に `stack` |
| `web/src/api/types.ts` | `npm --prefix web run typegen` で再生成 |
| `web/e2e/phase6.spec.ts` | 受け入れ（新規） |
| `app/tests/test_immich_live.py` | 実機のスタック経路 |
| `app/tests/test_crash_consistency.py` | 第 2 パスの途中で `os._exit` |
| `docs/HANDOFF.md` | 現在地・残件・「送ったもの」の書き換え |

---

## Task 0: プロファイルの `stack` 節

**Files:**
- Modify: `app/src/mediaferry/core/profiles/model.py`
- Modify: `app/src/mediaferry/core/profiles/builtin/{canon-eos,dji-osmo,generic-dcim}.yaml`
- Modify: `web/src/screens/Settings.tsx`（雛形 `TEMPLATE`）
- Test: `app/tests/test_profile_definition.py`（既存。無ければ `test_profile_matching.py` の隣に作る）

**Interfaces:**
- Produces: `StackRule(enabled: bool, extensions: tuple[str, ...], tolerance_seconds: int)`、
  `ProfileDefinition.stack`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_stack_is_optional_so_old_revisions_still_parse():
    """**既存リビジョンの JSON には `stack` が無い。** 必須にすると DB が開けない。"""
    defn = parse_definition(_a_definition())          # stack を含まない
    assert defn.stack.enabled is False
    assert defn.stack.extensions == ()


def test_stack_extensions_must_be_upper_and_dotless():
    with pytest.raises(ProfileInvalid, match="ドット無しの大文字"):
        parse_definition(_a_definition(stack={"enabled": True, "extensions": [".jpg", "CR2"],
                                              "tolerance_seconds": 0}))


def test_stack_needs_at_least_two_extensions():
    """1 つでは組にならない（自分としか当たらない）."""
    with pytest.raises(ProfileInvalid, match="2 つ以上"):
        parse_definition(_a_definition(stack={"enabled": True, "extensions": ["JPG"],
                                              "tolerance_seconds": 0}))


def test_stack_extensions_must_be_scanned():
    """**取り込まない拡張子は組にならない。** 書き間違いを早く教える."""
    with pytest.raises(ProfileInvalid, match="scan.extensions に無い"):
        parse_definition(_a_definition(
            scan={"roots": ["DCIM"], "extensions": ["JPG"]},
            stack={"enabled": True, "extensions": ["JPG", "CR2"], "tolerance_seconds": 0},
        ))


def test_tolerance_seconds_must_not_be_negative():
    with pytest.raises(ProfileInvalid, match="0 以上"):
        parse_definition(_a_definition(stack={"enabled": True, "extensions": ["JPG", "CR2"],
                                              "tolerance_seconds": -1}))


def test_disabled_stack_does_not_require_the_rest():
    """`merge.enabled: false` と同じ扱い（使われない値を発明させない）."""
    defn = parse_definition(_a_definition(stack={"enabled": False}))
    assert defn.stack.enabled is False


def test_canon_eos_stacks_jpg_and_cr2():
    canon = {d.slug: d for d in load_builtin_definitions()}["canon-eos"]
    assert canon.stack.enabled is True
    assert canon.stack.extensions == ("JPG", "CR2")     # 先頭が primary
    assert canon.stack.tolerance_seconds == 0
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_profile_definition.py -q`
Expected: FAIL（`ProfileDefinition` に `stack` が無い）

- [ ] **Step 3: 最小実装**

```python
@dataclass(frozen=True)
class StackRule:
    """RAW+JPEG の組の規則（§6）.

    `extensions` は**先頭ほど primary**。`tolerance_seconds` は `captured_at` の
    許容差で、既定は完全一致。
    """

    enabled: bool
    extensions: tuple[str, ...]
    tolerance_seconds: int


# **省略できる。** 既存リビジョンの `definition_json` にこのキーは無く、必須に
# すると適用済みの DB が開けなくなる。
STACK_DISABLED = StackRule(enabled=False, extensions=(), tolerance_seconds=0)
```

`parse_definition` の `_reject_unknown` の集合に `"stack"` を足し、

```python
        stack=_parse_stack(_mapping(data, "stack"), scan) if "stack" in data else STACK_DISABLED,
```

```python
def _parse_stack(data: Mapping[str, Any], scan: ScanRule) -> StackRule:
    _reject_unknown(data, {"enabled", "extensions", "tolerance_seconds"}, "stack")
    if not _bool(data, "enabled"):
        # 無効なら拡張子も許容差も要らない（merge と同じ扱い）。
        return STACK_DISABLED
    extensions = _strings(data, "extensions")
    for ext in extensions:
        if ext != ext.upper() or ext.startswith("."):
            raise ProfileInvalid(f"stack.extensions はドット無しの大文字で書く: {ext!r}")
        if ext not in scan.extensions:
            raise ProfileInvalid(f"stack.extensions が scan.extensions に無い: {ext}")
    if len(extensions) < 2:
        raise ProfileInvalid("stack.extensions は 2 つ以上必要（1 つでは組にならない）")
    if len(set(extensions)) != len(extensions):
        raise ProfileInvalid(f"stack.extensions に重複がある: {extensions}")
    return StackRule(
        enabled=True, extensions=extensions, tolerance_seconds=_positive_int(data, "tolerance_seconds")
    )
```

`canon-eos.yaml` に足す:

```yaml
stack:
  # RAW+JPEG の同時記録。**先頭が primary**（Immich の一覧で代表になる方）。
  enabled: true
  extensions: ["JPG", "CR2"]
  # **実カードを見ていないので緩めない**（merge.enabled: false と同じ理由）。
  tolerance_seconds: 0
```

`dji-osmo.yaml` と `generic-dcim.yaml` には `stack: {enabled: false}` を足す
（DJI は RAW を書かず、汎用はメーカー固有 RAW を拾わない）。

`web/src/screens/Settings.tsx` の `TEMPLATE` にも `stack: { enabled: false }` を足す。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_profile_definition.py app/tests/test_profile_matching.py -q`
Expected: PASS

**注意:** `definition_to_json` の出力が変わるので、`sync_builtins` は起動時に
ビルトイン 3 つの**新しいリビジョン**を作る。これは設計どおりの経路（§6）。
既存のレコードは古いリビジョンを指したままで正しい。この挙動を確かめるテストが
既にあるなら、期待リビジョン番号の更新が要る。

- [ ] **Step 5: 変異試験**

対象: `model.py`。変異例（すべて検出されること）:
`ext not in scan.extensions` → `in`、`len(extensions) < 2` → `< 1`、
`if "stack" in data` → 常に `_parse_stack` を呼ぶ、`STACK_DISABLED` の `enabled` を `True`。

- [ ] **Step 6: コミット**

```bash
git add -A app/src/mediaferry/core/profiles app/tests web/src/screens/Settings.tsx
git commit -m "feat(profiles): stack 節を足す"
```

---

## Task 1: `0015` マイグレーション

**Files:**
- Create: `app/src/mediaferry/db/migrations/0015_stacking.sql`
- Test: `app/tests/test_stack_migration.py`

**Interfaces:**
- Produces: `upload_record.stack_state` / `.remote_stack_id` / `.stack_reason`、
  索引 `upload_record_unstacked`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_stacked_requires_a_stack_id(db):
    record = _a_complete_record(db)
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute("UPDATE upload_record SET stack_state = 'stacked' WHERE id = ?", (record,))


def test_skipped_requires_a_reason(db):
    record = _a_complete_record(db)
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute("UPDATE upload_record SET stack_state = 'skipped' WHERE id = ?", (record,))


def test_unevaluated_must_not_carry_leftovers(db):
    """未評価に戻すときは理由も消す（消し忘れると画面に古い理由が残る）."""
    record = _a_complete_record(db)
    db.execute("UPDATE upload_record SET stack_state = 'skipped', stack_reason = 'x'"
               " WHERE id = ?", (record,))
    with pytest.raises(sqlite3.IntegrityError, match="stack"):
        db.execute("UPDATE upload_record SET stack_state = NULL WHERE id = ?", (record,))


def test_the_extraction_uses_the_partial_index(db):
    """**索引を足したら EXPLAIN で駆動を確かめる**（Phase 5 の 5・6 巡目の教訓）."""
    plan = db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM upload_record"
        " WHERE destination_id = ? AND target_epoch = ? AND state = 'complete'"
        "   AND stack_state IS NULL AND invalidated_at IS NULL AND id > ?"
        " ORDER BY id LIMIT 50",
        ("d", 1, ""),
    ).fetchall()
    # **鍵が 2 本とも search key に入っていることまで見る。** 索引名だけの一致では、
    # 先頭 prefix（destination_id だけ）で使われている場合と区別できない。
    assert any(
        "upload_record_unstacked (destination_id=? AND target_epoch=?" in row["detail"]
        for row in plan
    )
    # 並べ替えが消えていること（`id` は索引の第 3 列）。
    assert not any("USE TEMP B-TREE FOR ORDER BY" in row["detail"] for row in plan)


def test_the_dead_setting_row_is_removed(db):
    assert db.execute(
        "SELECT count(*) AS n FROM app_setting WHERE key = 'UPLOAD_CONCURRENCY'"
    ).fetchone()["n"] == 0
```

（最後のテストは、`0014` までを適用した DB に `UPLOAD_CONCURRENCY` の行を入れてから
`0015` を適用する形で書く。`test_db_migrate.py` の既存の作法に合わせる。）

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_stack_migration.py -q`
Expected: FAIL（列が無い）

- [ ] **Step 3: 最小実装**

```sql
-- Phase 6: RAW/JPEG のスタッキング（§9.11）。
-- **状態機械には状態を足さない。** スタックは「その宛先へその資産を送った結果」
-- なので、`remote_asset_id` と同じ層に置く。

ALTER TABLE upload_record ADD COLUMN stack_state TEXT
    CHECK (stack_state IN ('stacked', 'skipped'));
ALTER TABLE upload_record ADD COLUMN remote_stack_id TEXT;
ALTER TABLE upload_record ADD COLUMN stack_reason TEXT;

-- `ALTER TABLE` では表制約を足せないので、3 列の組み合わせは trigger で守る
-- （`0011` の `captured_at_revision_id` と同じ形）。
--
-- **`state = 'complete'` は条件に入れない。** 再計算の差し戻し（`_requeue`）が
-- `complete` → `needs_recheck` を動かすので、入れると正当な差し戻しが ABORT する。
-- スタック済みという事実は、レコードが再確認へ戻っても真のままである。
CREATE TRIGGER upload_record_stack_shape_insert
AFTER INSERT ON upload_record
WHEN NOT (
       (NEW.stack_state IS NULL AND NEW.remote_stack_id IS NULL AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state = 'stacked' AND NEW.remote_stack_id IS NOT NULL
        AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state = 'skipped' AND NEW.stack_reason IS NOT NULL
        AND NEW.remote_stack_id IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'stack_state と remote_stack_id / stack_reason の組が不正');
END;

CREATE TRIGGER upload_record_stack_shape_update
AFTER UPDATE OF stack_state, remote_stack_id, stack_reason ON upload_record
WHEN NOT (
       (NEW.stack_state IS NULL AND NEW.remote_stack_id IS NULL AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state = 'stacked' AND NEW.remote_stack_id IS NOT NULL
        AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state = 'skipped' AND NEW.stack_reason IS NOT NULL
        AND NEW.remote_stack_id IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'stack_state と remote_stack_id / stack_reason の組が不正');
END;

-- 第 2 パスの抽出の駆動索引。**述語は問い合わせ側と一字一句そろえる**
-- （部分索引は述語が一致しないと使われない）。
--
-- **`target_epoch` を鍵に入れる。** 向き先を変えた宛先では旧 epoch の `complete` が
-- 監査履歴として残る（無効化されない）ので、epoch で絞らないと別ライブラリへ送った
-- 資産 ID を現行の資格情報で送ることになる（§9.11）。
CREATE INDEX upload_record_unstacked ON upload_record (destination_id, target_epoch, id)
    WHERE stack_state IS NULL AND state = 'complete' AND invalidated_at IS NULL;

-- 効かないまま残っていた設定行を消す（§21）。env に残っていても未知のキーは
-- 読まれないので、起動は壊れない。
DELETE FROM app_setting WHERE key = 'UPLOAD_CONCURRENCY';
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_stack_migration.py app/tests/test_db_migrate.py -q`
Expected: PASS

- [ ] **Step 5: 変異試験**

トリガの各枝（3 つの OR）を 1 つずつ壊し、対応するテストが落ちること。

**実施結果（2026-08-19）: 11 件中 10 件を検出。**

- **`IS` を `=` に戻す変異が検出された** —— これは実装中に見つけた本物の欠陥でもある。
  `stack_state` が NULL のとき `NEW.stack_state = 'stacked'` は NULL を返し、
  `偽 OR NULL` は NULL、`NOT NULL` も NULL になるので **WHEN が成立せず trigger が
  黙って素通りする**。最初に `=` で書いたときは「未評価へ戻すのに理由が残っている」が
  通ってしまった。**比較は `IS` で書く**
- **3 件は当て方を直して初めて成立した。** `WHEN 0` を足す形と、
  `NEW.state = 'complete' AND` を括らずに足す形は、どちらも構文エラーか
  「最初の枝にしか掛からない」になり、狙いの判断を壊していなかった
  （`AND` は `OR` より強いので、**全体を括る**必要がある）
- **素通り 1 件（構造的に検出できない）:** 部分索引の述語から
  `invalidated_at IS NULL` を落とす変異。部分索引は「索引の述語が問い合わせの
  WHERE から導ける」ときに使えるので、**述語を緩めても索引は使われ続け、
  EXPLAIN に差が出ない**。差が出るのは索引の大きさと走査量だけで、
  無効化された行を大量に作って走査量を測るテストが要る。**入れない**
  （効果に対して試験が重い）

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/migrations/0015_stacking.sql app/tests/test_stack_migration.py
git commit -m "feat(db): 0015 でスタックの 3 列と部分索引を足す"
```

---

## Task 2: `UPLOAD_CONCURRENCY` の撤去

**Files:**
- Modify: `app/src/mediaferry/settings.py:73,152,239`
- Test: `app/tests/test_settings.py:25`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_the_concurrency_setting_is_gone():
    """**効かない設定を画面に並べない**（§9.10 は当初から直列と決めている）."""
    assert "UPLOAD_CONCURRENCY" not in SETTING_SPECS


def test_an_unknown_env_var_does_not_break_startup(tmp_path):
    """撤去後も、古い env が残っている環境が起動できる."""
    service = SettingsService(_a_db(), {"MEDIAFERRY_UPLOAD_CONCURRENCY": "4"})
    assert service.snapshot().upload_max_attempts == 3


def test_setting_it_through_the_api_is_refused():
    with pytest.raises(SettingInvalid, match="未知の設定キー"):
        SettingsService(_a_db(), {}).set("UPLOAD_CONCURRENCY", "4")
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_settings.py -q`
Expected: FAIL（まだ spec に在る）

- [ ] **Step 3: 最小実装**

`SETTING_SPECS` の該当行、`Settings.upload_concurrency`、`snapshot()` の代入を削る。
既存テスト `test_settings.py:25` の `assert snapshot.upload_concurrency == 2` も消す。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest -q && grep -rn "upload_concurrency\|UPLOAD_CONCURRENCY" app web --include='*.py' --include='*.ts' --include='*.tsx'`
Expected: PASS、grep は**何も出ない**

- [ ] **Step 5: コミット**

```bash
git add -A app
git commit -m "refactor(settings): UPLOAD_CONCURRENCY を撤去する"
```

---

## Task 3: Immich クライアントのスタック経路と fake

**Files:**
- Modify: `app/src/mediaferry/adapters/immich.py`
- Modify: `app/tests/fake_immich.py`
- Test: `app/tests/test_adapter_immich.py`

**Interfaces:**
- Produces:
  - `RemoteAsset.stack_id: str | None`、`RemoteAsset.stack_primary_asset_id: str | None`
  - `RemoteStack(stack_id: str, primary_asset_id: str, asset_ids: tuple[str, ...])`
  - `ImmichClient.create_stack(asset_ids: Sequence[str]) -> RemoteStack`
  - `ImmichClient.stack_by_primary(primary_asset_id: str) -> RemoteStack | None`
  - `ImmichClient.set_stack_primary(stack_id: str, asset_id: str) -> None`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_an_asset_reports_its_stack(fake):
    fake.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "asset-2"]}
    asset = _client(fake).asset("asset-2")
    assert asset.stack_id == "stack-1"
    assert asset.stack_primary_asset_id == "asset-1"


def test_an_asset_without_a_stack_reports_none(fake):
    assert _client(fake).asset("asset-1").stack_id is None


def test_creating_a_stack_returns_its_members(fake):
    stack = _client(fake).create_stack(["asset-1", "asset-2"])
    assert set(stack.asset_ids) == {"asset-1", "asset-2"}
    assert stack.primary_asset_id in stack.asset_ids


def test_a_stack_can_be_read_back_by_its_primary(fake):
    created = _client(fake).create_stack(["asset-1", "asset-2"])
    found = _client(fake).stack_by_primary(created.primary_asset_id)
    assert found is not None and found.stack_id == created.stack_id


def test_an_unknown_primary_has_no_stack(fake):
    assert _client(fake).stack_by_primary("asset-9") is None


def test_the_primary_can_be_moved(fake):
    created = _client(fake).create_stack(["asset-1", "asset-2"])
    other = next(a for a in created.asset_ids if a != created.primary_asset_id)
    _client(fake).set_stack_primary(created.stack_id, other)
    assert _client(fake).stack_by_primary(other).stack_id == created.stack_id


def test_identifiers_from_the_peer_are_validated(fake):
    """**相手が選べる値を経路へ組み立てない**（§14。既存の `_identifier` と同じ扱い）."""
    fake.echo_key_as_ids = True
    with pytest.raises(ImmichProtocolError):
        _client(fake).create_stack(["asset-1", "asset-2"])


def test_a_broken_stack_response_is_a_protocol_error(fake):
    fake.stack_response_without_assets = True
    with pytest.raises(ImmichProtocolError):
        _client(fake).create_stack(["asset-1", "asset-2"])


def test_a_response_with_a_different_set_is_a_protocol_error(fake):
    fake.drop_one_asset_from_the_stack_response = True
    with pytest.raises(ImmichProtocolError):
        _client(fake).create_stack(["asset-1", "asset-2"])


def test_duplicate_inputs_are_refused_before_sending(fake):
    with pytest.raises(ValueError):
        _client(fake).create_stack(["asset-1", "asset-1"])
    assert fake.requests == []


def test_the_create_does_not_follow_a_redirect(fake):
    """**非冪等で吸収する要求を自動 replay させない**（303 でも method は変わらない）."""
    fake.redirect_to = "/api/stacks"
    with pytest.raises(ImmichRedirected):
        _client(fake).create_stack(["asset-1", "asset-2"])
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_adapter_immich.py -q`
Expected: FAIL（`create_stack` が無い）

- [ ] **Step 3: 最小実装**

```python
@dataclass(frozen=True)
class RemoteStack:
    """相手が持っているスタック（読み取り）."""

    stack_id: str
    primary_asset_id: str
    asset_ids: tuple[str, ...]
```

`RemoteAsset` に 2 つ足し、`asset()` で `body.get("stack")` を読む
（**object でなければ `None` として扱い、例外にしない** —— 相手が古い版なら
このキーごと無い）。

```python
    def create_stack(self, asset_ids: Sequence[str]) -> RemoteStack:
        """**既存スタックを吸収しうる。** 呼ぶ前に全員の `stack` を見ること（§9.11）."""
        checked = [self._identifier(a, "POST /api/stacks の asset id") for a in asset_ids]
        if len(set(checked)) != len(checked):
            # **入力の重複を先に閉じる。** 重複したまま送ると、応答が畳んで返しても
            # 集合の比較が通ってしまう（[A, A, B] を送って [A, B] が返る）。
            raise ValueError("create_stack に同じ asset id が複数ある")
        body = _as_object(
            self._request(
                "POST",
                "/api/stacks",
                # **非冪等で既存スタックを吸収するので、redirect を追わない。**
                allow_redirect=False,
                json={"assetIds": checked},
            ),
            "POST /api/stacks",
        )
        created = self._stack_from(body, "POST /api/stacks")
        # **要求した集合と全単射であることを確かめる。** 吸収の仕様がある以上、
        # 返ってきた集合が違えば「別のものを作った」ので、その id を確定させない。
        if len(created.asset_ids) != len(checked) or set(created.asset_ids) != set(checked):
            raise ImmichProtocolError("POST /api/stacks が要求と違う集合を返した")
        return created

    def stack_by_primary(self, primary_asset_id: str) -> RemoteStack | None:
        checked = self._identifier(primary_asset_id, "GET /api/stacks の primary asset id")
        response = self._request("GET", "/api/stacks", params={"primaryAssetId": checked})
        # **非 JSON も object でない要素も、素通りさせずに protocol error にする**
        # （`_as_object` と同じ作法。`response.json()` を直に呼ぶと `ValueError` が
        # そのまま漏れて、第 2 パスの分岐に入らない）。
        for item in _as_array(response, "GET /api/stacks"):
            stack = self._stack_from(item, "GET /api/stacks")
            if stack.primary_asset_id == checked:
                return stack
        return None

    def set_stack_primary(self, stack_id: str, asset_id: str) -> None:
        stack = self._identifier(stack_id, "PUT /api/stacks の stack id")
        asset = self._identifier(asset_id, "PUT /api/stacks の primary asset id")
        self._request("PUT", f"/api/stacks/{stack}", json={"primaryAssetId": asset})

    def _stack_from(self, body: dict[str, Any], label: str) -> RemoteStack:
        """**壊れた応答を DB へ確定させない。** 形が違えば protocol error にする."""
        assets = body.get("assets")
        if not isinstance(assets, list) or not assets:
            raise ImmichProtocolError(f"{label} の応答に assets が無い")
        ids = tuple(
            self._identifier(_required_str(a, "id", label), label)
            for a in assets
            if isinstance(a, dict)
        )
        if len(ids) != len(assets):
            raise ImmichProtocolError(f"{label} の assets に object でない要素がある")
        if len(set(ids)) != len(ids):
            raise ImmichProtocolError(f"{label} の assets に重複がある")
        stack = RemoteStack(
            stack_id=self._identifier(_required_str(body, "id", label), label),
            primary_asset_id=self._identifier(
                _required_str(body, "primaryAssetId", label), label
            ),
            asset_ids=ids,
        )
        # **primary は必ず member。** 外れていると、こちらの primary 検査が
        # 永久に一致せず PUT を打ち続ける。
        if stack.primary_asset_id not in stack.asset_ids:
            raise ImmichProtocolError(f"{label} の primaryAssetId が assets に無い")
        return stack
```

`_as_object` の隣に `_as_array` を足す（同じ作法で、配列を配列として読む）:

```python
def _as_array(response: httpx.Response, label: str) -> list[dict[str, Any]]:
    """JSON を object の配列として読む. 壊れた応答も protocol error に正規化する."""
    try:
        body = response.json()
    except ValueError as exc:
        raise ImmichProtocolError(f"{label} の応答が JSON ではない") from exc
    if not isinstance(body, list):
        raise ImmichProtocolError(f"{label} の応答が配列ではない")
    for item in body:
        if not isinstance(item, dict):
            # **黙って読み飛ばさない**（「N 件見た」と言いながら見ていない状態を作る）。
            raise ImmichProtocolError(f"{label} の応答に object でない要素がある")
    return body
```

`RemoteAsset.stack` の読み方も 2 つに分ける。**キーが無いのは旧版として `None`、
キーがあって object でない・必須の値が無いのは protocol error。**

**`POST /api/stacks` は `allow_redirect=False` にする。`PUT /api/stacks/{id}` は
既定のまま。** `_request` は 303 でも method を変えずに同じ本文で再送するので、
**非冪等で既存スタックを吸収する `POST` を自動で replay させてはいけない**
（安全でない理由はストリームの EOF だけではない）。`PUT` は冪等なので、
`tag_assets` / `set_date_time_original` と同じ扱いでよい。別 origin への redirect は
どちらも `_same_origin_target` が必ず拒む。

fake 側（`fake_immich.py`）:

```python
        # スタック。`stack_id -> {"primary": asset_id, "assets": [asset_id, ...]}`
        self.stacks: dict[str, dict[str, Any]] = {}
        self.stack_response_without_assets: bool = False
```

`route` に 3 経路を足す。**実物の地雷（既存スタックの吸収）も再現する** ——
`POST /api/stacks` は、渡された資産のどれかが既存スタックの primary なら
その既存スタックを畳み込む。`GET /api/assets/{id}` の応答にも `stack` を載せる。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_adapter_immich.py -q`
Expected: PASS

- [ ] **Step 5: 変異試験**

`_stack_from` の `_identifier` を素通しに、`stack_by_primary` の
`stack.primary_asset_id == checked` を常に真に、`assets` の空検査を外す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/adapters/immich.py app/tests/fake_immich.py app/tests/test_adapter_immich.py
git commit -m "feat(immich): スタックの作成・取得・primary 差し替えを足す"
```

---

## Task 4: 組の解決（純関数）とリポジトリの問い合わせ

**Files:**
- Create: `app/src/mediaferry/core/uploads/stacking.py`
- Modify: `app/src/mediaferry/db/uploads.py`
- Test: `app/tests/test_stacking_rules.py`

**Interfaces:**
- Consumes: `StackRule`（Task 0）
- Produces:
  - `stem_prefix(rel_path: str) -> str`（`"DCIM/100CANON/IMG_1234."`）
  - `Candidate(record_id, media_file_id, profile_id, volume_instance_id, rel_path,
    captured_at, captured_at_source, origin, state, remote_asset_id, invalidated)`
    —— **観測 1 つぶん**。`source_key` は `(volume_instance_id, stem_prefix)`
  - `Group(members: tuple[Candidate, ...])` / `Refusal(reason: str)`
  - `resolve_group(primary: Candidate, candidates: Sequence[Candidate], rule: StackRule) -> Group | Refusal`
  - `StackGroupChanged`（前提が変わった。その組は諦める）
  - `UploadRepository.unstacked_batch(destination_id, target_epoch, after_id, limit)` /
    `.sources_of(media_file_id)` / `.siblings_on_card(volume_instance_id, prefix)` /
    `.record_for(destination_id, target_epoch, media_file_id)` /
    `.guard_stack_group(ctx, members, destination_id, target_epoch, profile_revision_id)` /
    `.mark_stacked(ctx, members, destination_id, target_epoch, profile_revision_id, remote_stack_id)` /
    `.mark_skipped(ctx, record, destination_id, target_epoch, profile_revision_id, reason)`
    —— **3 つとも同じ `_assert_current` を通す**（片方だけ弱くすると抜け道になる）

- [ ] **Step 1: 失敗するテストを書く**

```python
RULE = StackRule(enabled=True, extensions=("JPG", "CR2"), tolerance_seconds=0)


def _candidate(rel_path: str, **overrides) -> Candidate:
    """既定は「送り終わった、自分が上げた、同じ時刻」の相方.

    **既定と一致するだけで通るテストを書かない**（Phase 5 の教訓）。壊す条件を
    1 つずつ `overrides` で与える。
    """
    base = {
        "record_id": f"rec-{rel_path}",
        "media_file_id": f"media-{rel_path}",
        "profile_id": "profile-1",
        "volume_instance_id": "volume-1",
        "rel_path": rel_path,
        "captured_at": "2026-08-19T10:30:00+09:00",
        "captured_at_source": "exif",
        "origin": "created_by_us",
        "state": "complete",
        "remote_asset_id": f"asset-{rel_path}",
        "invalidated": False,
    }
    return Candidate(**{**base, **overrides})


def _jpg(**overrides) -> Candidate:
    return _candidate("DCIM/100CANON/IMG_1234.JPG", **overrides)


def _cr2(**overrides) -> Candidate:
    rel_path = overrides.pop("rel_path", "DCIM/100CANON/IMG_1234.CR2")
    return _candidate(rel_path, **overrides)


def test_a_pair_on_the_same_card_forms_a_group():
    group = resolve_group(_jpg(), [_jpg(), _cr2()], RULE)
    assert [m.rel_path for m in group.members] == [
        "DCIM/100CANON/IMG_1234.JPG",     # **先頭の拡張子が primary**
        "DCIM/100CANON/IMG_1234.CR2",
    ]


def test_a_lonely_file_is_refused():
    assert resolve_group(_jpg(), [_jpg()], RULE).reason == "相方が見つからない"


def test_a_different_stem_is_not_a_partner():
    other = _cr2(rel_path="DCIM/100CANON/IMG_9999.CR2")
    assert isinstance(resolve_group(_jpg(), [_jpg(), other], RULE), Refusal)


def test_a_different_capture_time_is_not_a_partner():
    """**同じシャッターであることの直接の証拠**（§6）."""
    late = _cr2(captured_at="2026-08-19T10:30:01+09:00")
    assert "撮影時刻" in resolve_group(_jpg(), [_jpg(), late], RULE).reason


def test_the_tolerance_is_honoured():
    late = _cr2(captured_at="2026-08-19T10:30:01+09:00")
    rule = replace(RULE, tolerance_seconds=2)
    assert isinstance(resolve_group(_jpg(), [_jpg(), late], rule), Group)


def test_a_different_time_source_is_not_a_partner():
    """EXIF の時刻と mtime の時刻という**別々の時計**を突き合わせない（§6）."""
    fallen_back = _cr2(captured_at_source="mtime")
    assert "時刻の根拠" in resolve_group(_jpg(), [_jpg(), fallen_back], RULE).reason


def test_offsets_are_compared_as_instants_not_strings():
    """`captured_at` はオフセット付きで保存される（§8 の唯一の例外）."""
    same_instant = _cr2(captured_at="2026-08-19T01:30:00+00:00")
    assert isinstance(resolve_group(_jpg(), [_jpg(), same_instant], RULE), Group)


def test_a_partner_that_is_not_ours_refuses_the_group():
    """`POST /stacks` は既存スタックを吸収する。**証明できない相手には触らない**."""
    theirs = _cr2(origin="pre_existing")
    assert "自分が上げたと証明" in resolve_group(_jpg(), [_jpg(), theirs], RULE).reason


def test_a_partner_that_is_not_complete_refuses_the_group():
    assert "まだ送信が終わっていない" in resolve_group(
        _jpg(), [_jpg(), _cr2(state="pending")], RULE
    ).reason


def test_an_invalidated_partner_refuses_the_group():
    assert isinstance(resolve_group(_jpg(), [_jpg(), _cr2(invalidated=True)], RULE), Refusal)


def test_an_extension_outside_the_rule_is_ignored():
    movie = _cr2(rel_path="DCIM/100CANON/IMG_1234.MOV")
    assert isinstance(resolve_group(_jpg(), [_jpg(), movie], RULE), Refusal)


def test_a_partner_observed_only_on_another_card_is_refused():
    """連番が一周した別カードとの誤結合を閉じる（§6）."""
    other_card = _cr2(volume_instance_id="volume-2")
    assert isinstance(resolve_group(_jpg(), [_jpg(), other_card], RULE), Refusal)


def test_the_volume_and_the_stem_must_match_in_the_same_observation():
    """**平坦化した集合では通ってしまう組を閉じる。**

    JPG は (volume-1, A.) と (volume-2, B.)、CR2 は (volume-1, C.) と (volume-2, B.)。
    ボリュームの集合も stem の集合も交わるが、**同じ観測では一度も一致しない**。
    """
    jpg = [
        _candidate("DCIM/100CANON/A.JPG", media_file_id="m-jpg", volume_instance_id="volume-1"),
        _candidate("DCIM/100CANON/B.JPG", media_file_id="m-jpg", volume_instance_id="volume-2"),
    ]
    cr2 = [
        _candidate("DCIM/100CANON/C.CR2", media_file_id="m-cr2", volume_instance_id="volume-1"),
        _candidate("DCIM/100CANON/B.CR2", media_file_id="m-cr2", volume_instance_id="volume-2"),
    ]
    assert isinstance(resolve_group(jpg[0], [*jpg, *cr2], RULE), Refusal)


def test_a_media_observed_twice_appears_once_in_the_group():
    """**同じ資産を 2 回送らない**（2 回記録もしない）."""
    cr2_twice = [
        _cr2(volume_instance_id="volume-1"),
        _cr2(volume_instance_id="volume-1", record_id="rec-dup"),
    ]
    group = resolve_group(_jpg(), [_jpg(), *cr2_twice], RULE)
    assert len(group.members) == 2


def test_a_partner_of_another_profile_is_refused():
    """規則が 1 つに決まらない組は作らない（§9.11）."""
    assert "別のプロファイル" in resolve_group(
        _jpg(), [_jpg(), _cr2(profile_id="profile-2")], RULE
    ).reason


def test_the_stem_prefix_keeps_the_directory():
    assert stem_prefix("DCIM/100CANON/IMG_1234.JPG") == "DCIM/100CANON/IMG_1234."
```

リポジトリ側（`app/tests/test_upload_repository.py` の隣）:

```python
EPOCH = 1
REVISION = "revision-1"


def test_the_batch_is_a_keyset_so_a_left_over_row_does_not_loop(repo):
    """5xx で未評価のまま残した行があっても、次のバッチは前へ進む."""
    first = repo.unstacked_batch("dest-1", EPOCH, "", 1)
    second = repo.unstacked_batch("dest-1", EPOCH, first[0]["id"], 1)
    assert second[0]["id"] > first[0]["id"]


def test_records_of_an_old_epoch_are_not_extracted(repo, db):
    """旧 epoch の `complete` は無効化されずに残る（§8）が、別ライブラリの履歴."""
    assert [r["id"] for r in repo.unstacked_batch("dest-1", EPOCH + 1, "", 50)] == []


def test_the_guard_refuses_when_the_destination_was_repointed(repo, db, ctx):
    """**開始後に向き替えられたら止める。** 進行中の無効化はここには効かない."""
    members = repo.unstacked_batch("dest-1", EPOCH, "", 50)
    _repoint_to_another_library(db)                     # epoch が進む
    with pytest.raises(StackGroupChanged):
        repo.guard_stack_group(ctx, members, "dest-1", EPOCH, "revision-1")


def test_the_guard_refuses_when_the_profile_revision_moved(repo, db, ctx):
    members = repo.unstacked_batch("dest-1", EPOCH, "", 50)
    _edit_the_profile(db)
    with pytest.raises(StackGroupChanged):
        repo.guard_stack_group(ctx, members, "dest-1", EPOCH, "revision-1")


def test_a_lost_lease_refuses_the_guard(repo, db, ctx):
    _expire_the_lease(db)
    with pytest.raises(LeaseLost):
        repo.guard_stack_group(ctx, [], "dest-1", EPOCH, "revision-1")


def test_invalidated_records_are_not_extracted(repo, db):
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = '編集された'"
        " WHERE id = ?",
        (now_iso(), record_id),
    )
    assert [r["id"] for r in repo.unstacked_batch("dest-1", EPOCH, "", 50)] == []


def test_records_of_another_destination_are_not_extracted(repo, db):
    assert [r["id"] for r in repo.unstacked_batch("dest-2", EPOCH, "", 50)] == []


def test_records_that_are_not_complete_are_not_extracted(repo, db):
    db.execute("UPDATE upload_record SET state = 'pending' WHERE id = ?", (record_id,))
    assert [r["id"] for r in repo.unstacked_batch("dest-1", EPOCH, "", 50)] == []


def test_marking_stacked_upgrades_a_previously_skipped_partner(repo, db, ctx):
    """相方が後から完了したときに、**見送りの側も stacked へ上がる**（§9.11）."""
    repo.mark_skipped(ctx, repo.get(record_id), "dest-1", EPOCH, REVISION, "相方が見つからない")
    repo.mark_stacked(ctx, [repo.get(record_id)], "dest-1", EPOCH, REVISION, "stack-1")
    assert repo.get(record_id)["stack_state"] == "stacked"


def test_nothing_is_written_when_one_member_cannot_be_marked(repo, db, ctx):
    """**一部だけ書かない。** 例外で取引ごと巻き戻す."""
    members = [repo.get(record_id), repo.get(partner_id)]
    db.execute("UPDATE upload_record SET invalidated_at = ? WHERE id = ?", (now_iso(), partner_id))
    with pytest.raises(StackGroupChanged):
        repo.mark_stacked(ctx, members, "dest-1", EPOCH, REVISION, "stack-1")
    assert repo.get(record_id)["stack_state"] is None


def test_a_profile_edit_before_the_skip_is_written_refuses_it(repo, db, ctx):
    """**旧規則の判断を新しい版の世界へ残さない。**

    1. こちらが旧版の規則で「見送り」と判断する
    2. 別の接続が新しい版を出し、既存の見送りを未評価へ戻して commit
    3. こちらが書く → 戻す対象に入っていないので、二度と評価されない
    """
    record = repo.get(record_id)
    _edit_the_profile(db)
    with pytest.raises(StackGroupChanged):
        repo.mark_skipped(ctx, record, "dest-1", EPOCH, REVISION, "相方が見つからない")
    assert repo.get(record_id)["stack_state"] is None


def test_a_repoint_before_the_skip_is_written_refuses_it(repo, db, ctx):
    record = repo.get(record_id)
    _repoint_to_another_library(db)
    with pytest.raises(StackGroupChanged):
        repo.mark_skipped(ctx, record, "dest-1", EPOCH, REVISION, "相方が見つからない")


def test_a_profile_edit_after_the_post_refuses_the_record(repo, db, ctx):
    """外部副作用は済んでいる。**書かずに、次の送信の回収経路へ渡す**（§9.11）."""
    members = [repo.get(record_id), repo.get(partner_id)]
    _edit_the_profile(db)
    with pytest.raises(StackGroupChanged):
        repo.mark_stacked(ctx, members, "dest-1", EPOCH, REVISION, "stack-1")
    assert repo.get(record_id)["stack_state"] is None


def test_a_changed_remote_asset_id_refuses_the_record(repo, db, ctx):
    """送った相手と、記録する相手が同じであることまで見る."""
    members = [repo.get(record_id), repo.get(partner_id)]
    db.execute("UPDATE upload_record SET remote_asset_id = 'other' WHERE id = ?", (record_id,))
    with pytest.raises(StackGroupChanged):
        repo.mark_stacked(ctx, members, "dest-1", EPOCH, REVISION, "stack-1")


def test_marking_skipped_needs_the_lease(repo, db, ctx):
    """相手に触らない見送りも、リースの下で書く（大量にあると失効しうる）."""
    record = repo.get(record_id)
    _expire_the_lease(db)
    with pytest.raises(LeaseLost):
        repo.mark_skipped(ctx, record, "dest-1", EPOCH, REVISION, "相方が見つからない")
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_stacking_rules.py -q`
Expected: FAIL（モジュールが無い）

- [ ] **Step 3: 最小実装**

```python
"""RAW/JPEG の組を決める規則（§6・§9.11）.

**判断だけを持つ。** DB も相手も触らないので、4 条件を 1 つずつ壊す試験が書ける。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from posixpath import splitext

from ..profiles.model import StackRule

# 範囲引きの上端。UTF-8 で最も大きい符号位置。
HIGH_SENTINEL = "\U0010ffff"


@dataclass(frozen=True)
class Candidate:
    """**観測 1 つぶん**の候補.

    同じ `media_file` が複数の `source_entry` を持つときは、この値が複数できる。
    **1 つに絞らない**（`observed_at` は再スキャンで動くので、順序で選ぶと同じ組が
    実行のたびに変わる）。**平坦化もしない** —— 「ボリュームの集合」と「1 つの
    stem」に潰すと、*別々の観測*でそれぞれが一致するだけの組が通る。
    """

    record_id: str
    media_file_id: str
    profile_id: str
    volume_instance_id: str
    rel_path: str            # **カード上の原名**（`source_entry.rel_path`）
    captured_at: str
    captured_at_source: str
    origin: str
    state: str
    remote_asset_id: str | None
    invalidated: bool

    @property
    def source_key(self) -> tuple[str, str]:
        """組の鍵。**ボリュームと stem は必ず組で持つ。**"""
        return (self.volume_instance_id, stem_prefix(self.rel_path))


@dataclass(frozen=True)
class Group:
    members: tuple[Candidate, ...]      # **先頭が primary**


@dataclass(frozen=True)
class Refusal:
    reason: str


def stem_prefix(rel_path: str) -> str:
    """`DCIM/100CANON/IMG_1234.JPG` → `DCIM/100CANON/IMG_1234.`"""
    return splitext(rel_path)[0] + "."


def extension_of(rel_path: str) -> str:
    return splitext(rel_path)[1].lstrip(".").upper()


def resolve_group(
    primary: Candidate, candidates: Sequence[Candidate], rule: StackRule
) -> Group | Refusal:
    """4 条件（§6）で組を決める. 同じ組はどの member から呼んでも同じになる."""
    if not rule.enabled:
        return Refusal("プロファイルがスタックを使わない")
    if extension_of(primary.rel_path) not in rule.extensions:
        return Refusal("この拡張子は組の対象ではない")
    keys = {c.source_key for c in candidates if c.media_file_id == primary.media_file_id}
    matched = [
        c
        for c in candidates
        if c.media_file_id != primary.media_file_id
        # **鍵は組で比べる**（同じカードの、同じディレクトリの、同じ stem）。
        and c.source_key in keys
        and extension_of(c.rel_path) in rule.extensions
    ]
    # **同じ資産を 2 回送らない。** 1 つの media_file が複数の観測で候補に入る。
    # 集合を 1 つだけ持って O(n) にする（観測の数に上限は無い）。
    partners: list[Candidate] = []
    seen: set[str] = set()
    for candidate in matched:
        if candidate.media_file_id not in seen:
            seen.add(candidate.media_file_id)
            partners.append(candidate)
    if not partners:
        return Refusal("相方が見つからない")
    for partner in partners:
        if partner.profile_id != primary.profile_id:
            # 規則は 1 つに決まっている必要がある（§9.11）。
            return Refusal("相方が別のプロファイルに属している")
        if partner.invalidated or partner.state != "complete":
            return Refusal("相方はまだ送信が終わっていない")
        if partner.captured_at_source != primary.captured_at_source:
            return Refusal("相方と時刻の根拠が違う（EXIF と mtime を突き合わせない）")
        if not _within(primary.captured_at, partner.captured_at, rule.tolerance_seconds):
            return Refusal("相方と撮影時刻が一致しない")
        if partner.origin != "created_by_us" or primary.origin != "created_by_us":
            return Refusal("自分が上げたと証明できない資産が含まれる")
        if partner.remote_asset_id is None:
            return Refusal("相方の資産 ID が分からない")
    members = sorted(
        [primary, *partners], key=lambda c: rule.extensions.index(extension_of(c.rel_path))
    )
    return Group(members=tuple(members))


def _within(left: str, right: str, tolerance_seconds: int) -> bool:
    """**文字列ではなく瞬間で比べる**（オフセットが違っても同じ時刻でありうる）."""
    delta = datetime.fromisoformat(left) - datetime.fromisoformat(right)
    return abs(delta.total_seconds()) <= tolerance_seconds
```

`db/uploads.py` に足す問い合わせ（**述語の順序は部分索引と一字一句そろえる**）。
`HIGH_SENTINEL` は `..core.uploads.stacking` から import する（範囲の上端は
組の規則と同じ場所に置く）:

```python
    def unstacked_batch(
        self, destination_id: str, target_epoch: int, after_id: str, limit: int
    ) -> list[sqlite3.Row]:
        """スタック未評価の完了レコードを id の昇順で取る（keyset）.

        **`target_epoch` を必ず絞る。** 向き先を変えた宛先では旧 epoch の
        `complete` が監査履歴として残る（`_invalidate_old_epoch_locked` は
        `state <> 'complete'` だけを無効化する）。絞らないと**別ライブラリへ送った
        資産 ID を現行の資格情報で送る**。`records_for_recheck` が同じ理由で
        epoch を条件にしているのと同じ形にそろえる。

        **`LIMIT` を繰り返すだけでは足りない。** 相手が落ちていて未評価のまま
        残した行は次の周回でも条件を満たすので、同じ行を読み直して進まなくなる。
        """
        return list(
            self._conn.execute(
                "SELECT * FROM upload_record"
                " WHERE destination_id = ? AND target_epoch = ? AND state = 'complete'"
                "   AND stack_state IS NULL AND invalidated_at IS NULL AND id > ?"
                " ORDER BY id LIMIT ?",
                (destination_id, target_epoch, after_id, limit),
            )
        )

    def sources_of(self, media_file_id: str) -> list[sqlite3.Row]:
        """公開の元になったカード上の観測（**すべて**）.

        **1 つに絞らない。** `observed_at` は再スキャンのたびに更新されるので
        （`scan.py` の `_touch`）、「最初の観測」を順序で選ぶと同じ組が実行のたびに
        変わりうる。**公開名では組を作れない**（衝突時に改名される。§6）。
        """
        return list(
            self._conn.execute(
                "SELECT volume_instance_id, rel_path FROM source_entry"
                " WHERE media_file_id = ? AND state = 'published'",
                (media_file_id,),
            )
        )

    def siblings_on_card(self, volume_instance_id: str, prefix: str) -> list[sqlite3.Row]:
        """同じカードで `<dir>/<stem>.` から始まる観測（UNIQUE 索引の範囲引き）."""
        return list(
            self._conn.execute(
                "SELECT rel_path, media_file_id FROM source_entry"
                " WHERE volume_instance_id = ? AND rel_path > ? AND rel_path < ?"
                "   AND media_file_id IS NOT NULL AND state = 'published'",
                (volume_instance_id, prefix, prefix + HIGH_SENTINEL),
            )
        )

    def record_for(
        self, destination_id: str, target_epoch: int, media_file_id: str
    ) -> sqlite3.Row | None:
        """**現行 epoch のレコードだけ**を返す（旧 epoch は別ライブラリの履歴）."""
        return self._conn.execute(
            "SELECT * FROM upload_record"
            " WHERE destination_id = ? AND target_epoch = ? AND media_file_id = ?",
            (destination_id, target_epoch, media_file_id),
        ).fetchone()

    def guard_stack_group(
        self,
        ctx: JobContext,
        members: Sequence[sqlite3.Row],
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
    ) -> None:
        """**外部へ触る直前に通す。** `_guard`（§9.10）のスタック版.

        `complete` のレコードは claim を持たないので `prepare_side_effect` は
        流用できない。**ジョブのリース・組の全員の現在値・宛先の現行 epoch・
        プロファイルの現行版を 1 つの `BEGIN IMMEDIATE` で照合する。**
        1 件でも合わなければ `StackGroupChanged` を送出して、その組を諦める
        （相手には触らない）。

        **宛先の現行 epoch を見るのが要点。** epoch を進める編集は
        `state <> 'complete'` の行しか無効化しないので（§8）、`complete` を扱う
        この経路だけが既存の停止境界から外れる。開始後に別ライブラリへ向き替え
        られても、固定した旧リビジョンの preflight は旧向き先が生きていれば成功
        するので、**そこで止められない。**
        """
        with immediate(self._conn):
            self._assert_current(ctx, destination_id, target_epoch, profile_revision_id)
            for member in members:
                row = self._conn.execute(
                    "SELECT 1 FROM upload_record WHERE id = ? AND target_epoch = ?"
                    "   AND state = 'complete' AND invalidated_at IS NULL"
                    "   AND remote_asset_id = ? AND stack_state IS NOT 'stacked'",
                    (member["id"], target_epoch, member["remote_asset_id"]),
                ).fetchone()
                if row is None:
                    raise StackGroupChanged(f"レコード {member['id']} が変わった")

    def _assert_current(
        self,
        ctx: JobContext,
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
    ) -> None:
        """リース・宛先の現行 epoch・プロファイルの現行版をまとめて見る.

        **呼び出し側が開いた `BEGIN IMMEDIATE` の中で使う**（確認と書き込みの間に
        誰も割り込めない）。guard と記録の両方から同じものを見る —— 片方だけ
        弱くすると、そこが抜け道になる。
        """
        ctx.assert_lease()
        current = self._conn.execute(
            "SELECT r.target_epoch AS epoch FROM upload_destination d"
            " JOIN destination_revision r ON r.id = d.current_revision_id"
            " WHERE d.id = ?",
            (destination_id,),
        ).fetchone()
        if current is None or current["epoch"] != target_epoch:
            # **組ごとではなく、固定した epoch 全体が無効。** run が打ち切る。
            raise DestinationChanged("宛先の向き先が変わった")
        profile = self._conn.execute(
            "SELECT 1 FROM device_profile"
            " WHERE id = (SELECT profile_id FROM profile_revision WHERE id = ?)"
            "   AND current_revision_id = ?",
            (profile_revision_id, profile_revision_id),
        ).fetchone()
        if profile is None:
            raise StackGroupChanged("プロファイルの版が変わった")

    def mark_stacked(
        self,
        ctx: JobContext,
        members: Sequence[sqlite3.Row],
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
        remote_stack_id: str,
    ) -> None:
        """組の全員を 1 つのトランザクションで記録する.

        **全員に当たらなければ 1 行も書かない。** 一部だけ `stacked` になると、
        残りは別の組として再評価され、相手側に既にあるスタックを作り直そうとする。

        **見送り済みの相方は引き上げる。** 見送りは「今は組めない」の記録であって
        永久の拒否ではない（§9.11）。
        """
        with immediate(self._conn):
            # **guard と同じ現行値を見る。** 相手を待っている間に向き替えや
            # プロファイル編集が commit されうる。外部副作用は済んでいるので、
            # 落ちたら DB を書かずに次の送信の「既存スタックの回収」へ渡す。
            self._assert_current(ctx, destination_id, target_epoch, profile_revision_id)
            for member in members:
                updated = self._conn.execute(
                    "UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = ?,"
                    " stack_reason = NULL, updated_at = ?"
                    " WHERE id = ? AND target_epoch = ? AND state = 'complete'"
                    "   AND invalidated_at IS NULL AND remote_asset_id = ?"
                    "   AND (stack_state IS NULL OR stack_state = 'skipped')",
                    (remote_stack_id, now_iso(), member["id"], target_epoch,
                     member["remote_asset_id"]),
                )
                if updated.rowcount != 1:
                    # ロールバックさせる（`immediate` の外へ出す）。
                    raise StackGroupChanged(f"レコード {member['id']} を記録できない")

    def mark_skipped(
        self,
        ctx: JobContext,
        record: sqlite3.Row,
        destination_id: str,
        target_epoch: int,
        profile_revision_id: str,
        reason: str,
    ) -> None:
        """見送りを記録する. **相手に触らない経路でも、記録の条件は同じにする。**

        規則が無効・観測が無い・相方が居ない、といった見送りは相手に触らないので
        guard を通らない。だが**書く条件を緩めてはいけない** —— 規則を読んだ直後に
        プロファイルが編集されると、次の interleaving で**旧規則の判断が新しい版の
        世界へ残る**。

        1. こちらが旧版 R1 の規則で「見送り」と判断する
        2. 別の接続が R2 を発行し、既存の見送りを未評価へ戻して commit
           （この行はまだ未評価なので対象外）
        3. こちらが R1 の判断を書く → **R2 では二度と評価されない**

        リースも同じ理由で要る。見送りが大量にあると書いている間に切れうるし、
        `finish_claimed` は token と status しか見ないので、失効した後の書き込みが
        `succeeded` として残せてしまう。
        """
        with immediate(self._conn):
            self._assert_current(ctx, destination_id, target_epoch, profile_revision_id)
            updated = self._conn.execute(
                "UPDATE upload_record SET stack_state = 'skipped', stack_reason = ?,"
                " remote_stack_id = NULL, updated_at = ?"
                " WHERE id = ? AND target_epoch = ? AND state = 'complete'"
                "   AND invalidated_at IS NULL AND remote_asset_id IS ?"
                "   AND stack_state IS NULL",
                (reason, now_iso(), record["id"], target_epoch, record["remote_asset_id"]),
            )
            if updated.rowcount != 1:
                # **成功として数えない。** 数えると StackOutcome が嘘になる。
                raise StackGroupChanged(f"レコード {record['id']} を記録できない")
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_stacking_rules.py app/tests/test_upload_repository.py -q`
Expected: PASS

- [ ] **Step 5: 変異試験**

対象: `stacking.py` と `uploads.py` の新規部分。最低限この 8 つ:
`captured_at_source` の比較を外す、`tolerance_seconds` を `<` から `<=` へ、
`origin` の検査を primary だけにする、`extensions.index` を逆順に、
`c.media_file_id != primary.media_file_id` を外す（自分を相方に数える）、
`stem_prefix` の比較を「前方一致」に緩める、`unstacked_batch` の `id > ?` を外す、
`mark_stacked` の `OR stack_state = 'skipped'` を外す。

**素通りを見たら、まず「その変異は狙いの判断を壊しているか」を疑う**
（`docs/HANDOFF.md` §5 の型の表）。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core/uploads/stacking.py app/src/mediaferry/db/uploads.py app/tests
git commit -m "feat(uploads): 組の解決と抽出の問い合わせを足す"
```

---

## Task 5: 第 2 パス（`Stacker`）

**Files:**
- Create: `app/src/mediaferry/jobs/stacker.py`
- Modify: `app/src/mediaferry/api/jobs_wiring.py:130-180`
- Test: `app/tests/test_stacker.py`

**Interfaces:**
- Consumes: Task 3 の client、Task 4 の `resolve_group` とリポジトリ
- Produces: `Stacker.run(ctx: JobContext, destination_id: str) -> StackOutcome`、
  `StackOutcome(stacked: int, skipped: int, deferred: int)`

- [ ] **Step 1: 失敗するテストを書く**

`world` は fake Immich・DB・`JobContext` を束ねた fixture で、`world.stacker()` は
次を組み立てて返す（`test_uploader.py` の組み立てと同じ形）。

```python
Stacker(conn, uploads, destinations, ProfileRegistry(conn), open_client, preflight)
```

```python
def test_a_pair_is_stacked_with_the_jpeg_as_primary(world):
    outcome = world.stacker().run(world.ctx, "dest-1")
    assert outcome.stacked == 1
    stack = world.immich.only_stack()
    assert stack["primary"] == world.asset_of("IMG_1234.JPG")


def test_both_records_are_marked(world):
    world.stacker().run(world.ctx, "dest-1")
    assert {r["stack_state"] for r in world.records()} == {"stacked"}
    assert len({r["remote_stack_id"] for r in world.records()}) == 1


def test_the_primary_is_not_moved_when_it_is_already_right(world):
    """**相手を無駄に変えない**（§9.11）."""
    world.stacker().run(world.ctx, "dest-1")
    assert ("PUT", "/api/stacks") not in [(m, p.split("?")[0]) for m, p in world.immich.requests]


def test_an_existing_stack_with_the_same_members_is_adopted(world):
    """**中断からの回収**。送信直後に落ちた世界を作って、次の実行で拾わせる."""
    world.immich.stacks["stack-9"] = {
        "primary": world.asset("JPG"), "assets": [world.asset("JPG"), world.asset("CR2")]
    }
    world.stacker().run(world.ctx, "dest-1")
    assert world.records()[0]["remote_stack_id"] == "stack-9"
    assert ("POST", "/api/stacks") not in world.immich.requests    # 作り直さない


def test_adopting_an_existing_stack_still_fixes_the_primary(world):
    """`POST` の直後・`PUT` の前に落ちた世界。**集合は一致するが primary が違う。**

    検査しないと、望みの primary へ二度と直らない。
    """
    world.immich.stacks["stack-9"] = {
        "primary": world.asset("CR2"), "assets": [world.asset("JPG"), world.asset("CR2")]
    }
    world.stacker().run(world.ctx, "dest-1")
    assert world.immich.stacks["stack-9"]["primary"] == world.asset("JPG")
    assert world.records()[0]["stack_state"] == "stacked"


def test_a_foreign_stack_is_left_alone(world):
    """利用者が手で作った組を作り直さない."""
    world.immich.stacks["stack-9"] = {"primary": "someone-else", "assets": [...]}
    world.stacker().run(world.ctx, "dest-1")
    assert world.records()[0]["stack_state"] == "skipped"
    assert ("POST", "/api/stacks") not in world.immich.requests


def test_a_5xx_leaves_the_record_unevaluated(world):
    """宛先が落ちているだけなら、次の送信で再試行するのが正しい（§9.11）."""
    world.immich.fail_next = 99
    world.stacker().run(world.ctx, "dest-1")
    assert world.records()[0]["stack_state"] is None


def test_a_destination_failure_stops_the_whole_pass(world):
    """**宛先の障害は組ごとの失敗ではない。**

    次の組へ進むと、失効した鍵や停止したサーバへ未評価の全件ぶん要求を投げ続ける
    （timeout は既定 86400 秒）。
    """
    world.with_pairs(5)
    world.immich.fail_next = 99
    outcome = world.stacker().run(world.ctx, "dest-1")
    assert outcome.deferred == 1
    assert world.immich.request_count() <= 2       # 1 組ぶんで止まる


def test_an_auth_failure_stops_the_whole_pass(world):
    world.with_pairs(5)
    world.immich.api_key = "rotated"
    outcome = world.stacker().run(world.ctx, "dest-1")
    assert outcome.deferred == 1 and all(r["stack_state"] is None for r in world.records())


def test_records_of_an_old_epoch_are_never_touched(world):
    """**別ライブラリへ送った履歴に、現行の資格情報で触らない**（§9.11）.

    旧 epoch の `complete` は監査履歴として無効化されずに残る。
    """
    world.repoint_to_another_library()          # epoch が進む。旧 complete は残る
    outcome = world.stacker().run(world.ctx, "dest-1")
    assert outcome == StackOutcome(0, 0, 0)
    assert world.immich.requests == []


def test_a_record_that_changed_during_the_network_wait_is_not_written(world):
    """GET の間に無効化された組は、相手に触らず記録もしない."""
    world.invalidate_partner_during_first_get()
    world.stacker().run(world.ctx, "dest-1")
    assert world.immich.stack_count() == 0
    assert all(r["stack_state"] is None for r in world.records())


def test_nothing_is_written_when_one_member_cannot_be_marked(world):
    """**一部だけ stacked にしない。** 残りが別の組として再評価される."""
    world.invalidate_partner_after_the_post()
    world.stacker().run(world.ctx, "dest-1")
    assert all(r["stack_state"] is None for r in world.records())


def test_a_4xx_is_recorded_as_skipped(world):
    world.immich.reject_stacks = True
    world.stacker().run(world.ctx, "dest-1")
    assert world.records()[0]["stack_state"] == "skipped"


def test_a_cancelled_job_stops_before_the_next_group(world):
    world.cancel_after_first_group()
    outcome = world.stacker().run(world.ctx, "dest-1")
    assert outcome.stacked == 1 and world.immich.stack_count() == 1


def test_a_lost_lease_stops_before_touching_the_peer(world):
    """外部への副作用の**直前**にリースを確認する（§9.3 と同じ作法）."""
    world.expire_lease()
    with pytest.raises(LeaseLost):
        world.stacker().run(world.ctx, "dest-1")
    assert world.immich.stack_count() == 0


def test_a_cancel_committed_during_the_get_stops_before_the_post(world):
    """**`POST` の直前に取り直す。** GET を待っている間にキャンセルは commit される.

    利用者のキャンセルなので、**例外ではなく正常な中止**として返る（§9.9）。
    """
    world.cancel_during_first_get()
    outcome = world.stacker().run(world.ctx, "dest-1")
    assert outcome.stacked == 0
    assert ("POST", "/api/stacks") not in world.immich.requests


def test_a_repoint_stops_the_pass_instead_of_scanning_the_rest(world):
    """**固定した epoch 全体が無効なので、続けても同じ失敗を繰り返すだけ。**"""
    world.with_pairs(200)
    world.repoint_during_first_group()
    world.stacker().run(world.ctx, "dest-1")
    assert ("POST", "/api/stacks") not in world.immich.requests
    assert world.rows_examined() <= 2


def test_a_repoint_during_the_get_stops_before_the_post(world):
    """**開始後に別ライブラリへ向き替えられたら、そこで止まる。**

    旧 epoch の `complete` は無効化されないので、進行中レコードの無効化では
    止まらない。preflight も固定した旧リビジョンを見るので、旧向き先が生きて
    いれば成功してしまう。
    """
    world.repoint_during_first_get()
    world.stacker().run(world.ctx, "dest-1")
    assert ("POST", "/api/stacks") not in world.immich.requests
    assert all(r["stack_state"] is None for r in world.records())


def test_a_profile_edit_during_the_get_stops_before_the_post(world):
    world.edit_the_profile_during_first_get()
    world.stacker().run(world.ctx, "dest-1")
    assert ("POST", "/api/stacks") not in world.immich.requests


def test_a_slow_peer_does_not_lose_the_lease(world):
    """**相手待ちはすべて心拍で守る**（timeout は既定 86400 秒）."""
    world.immich.delay_seconds = 2
    world.with_lease_seconds(1)
    assert world.stacker().run(world.ctx, "dest-1").stacked == 1


def test_many_short_local_skips_do_not_lose_the_lease(world):
    """**行をまたいだ経過時間でも心拍を打つ。**

    1 件が短く終わると `with_lease_pulse` は 1 度も打たない
    （`thread.join(timeout=間隔)` が先に返る）。相手に触らない見送りが続くと積もる。
    """
    world.with_unpairable_records(500)
    world.with_lease_seconds(1)
    assert world.stacker().run(world.ctx, "dest-1").skipped == 500


def test_a_cancel_committed_between_the_post_and_the_put_stops_the_put(world):
    world.cancel_after_the_post()
    world.stacker().run(world.ctx, "dest-1")
    assert ("PUT", "/api/stacks") not in [(m, p.split("/")[0]) for m, p in world.immich.requests]


def test_the_preflight_runs_before_the_first_touch(world):
    """**向き先の再確認を飛ばさない**（§9.10 の `_guard` と同じ理由）.

    TTL を跨いだ後の `POST` が別のライブラリへ飛ぶと、UUID が偶然存在すれば
    他人の資産を束ねる。
    """
    assert world.immich.requests[0] == ("GET", "/api/users/me")


def test_the_second_pass_runs_after_an_approval(world):
    """承認で complete になった行も対象（§9.11）."""
    world.approve_the_pending_datetime()          # mode=approve のジョブを回す
    assert world.records()[0]["stack_state"] == "stacked"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_stacker.py -q`
Expected: FAIL（`Stacker` が無い）

- [ ] **Step 3: 最小実装**

```python
"""アップロードの第 2 パス —— RAW/JPEG のスタッキング（§9.11）.

**状態機械には状態を足さない。** 送り終わったレコードのうち未評価のものを
拾い、組が成立すれば相手のスタックを作る。組めない場合も見送りとして決着
させる（未評価のまま残すと、送信のたびにライブラリ全体を舐めることになる）。
"""

from __future__ import annotations

BATCH_SIZE = 50


@dataclass(frozen=True)
class StackOutcome:
    stacked: int
    skipped: int
    deferred: int      # 相手が落ちていて未評価のまま残した数


class StackGroupChanged(RuntimeError):
    """組の前提が、相手を待っている間に変わった. その組は諦める."""


class DestinationChanged(StackGroupChanged):
    """**宛先の向き先が変わった。** 固定した epoch 全体が無効なので打ち切る.

    組ごとの事情ではないので `continue` しても同じ失敗を繰り返すだけになる
    （旧 epoch の未評価行を末尾まで keyset 走査することになる）。
    """


class DestinationUnusable(RuntimeError):
    """組ではなく宛先の障害. **第 2 パスごと打ち切る.**"""


class Stacker:
    def __init__(self, conn, uploads, destinations, registry, open_client, preflight) -> None:
        ...

    def run(self, ctx: JobContext, destination_id: str) -> StackOutcome:
        # **開始時に現行リビジョンと epoch を固定する。** 抽出・相方の解決・記録の
        # すべてがこの epoch で動く（旧 epoch は別ライブラリへ送った履歴）。
        revision = self._destinations.current(destination_id)
        epoch, cursor = revision["target_epoch"], ""
        stacked = skipped = deferred = 0
        with self._open_client(revision) as client:
            while True:
                if ctx.cancelled():
                    break
                batch = self._uploads.unstacked_batch(destination_id, epoch, cursor, BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    cursor = row["id"]
                    if ctx.cancelled():
                        return StackOutcome(stacked, skipped, deferred)
                    try:
                        result = self._one(ctx, client, revision, epoch, row)
                    except DestinationUnusable as exc:
                        # **打ち切る。** 次の組へ進んでも同じ結果になるうえ、未評価の
                        # 全件ぶん要求を投げ続けることになる。残りは次の送信へ渡す。
                        ctx.emit("warning", f"宛先の障害でスタックを中断した: {exc}")
                        return StackOutcome(stacked, skipped, deferred + 1)
                    except DestinationChanged as exc:
                        # **打ち切る。** 続けても旧 epoch の未評価行を末尾まで
                        # 走査して、同じ失敗を繰り返すだけになる。
                        ctx.emit("info", f"宛先の向き先が変わったのでスタックを中断した: {exc}")
                        return StackOutcome(stacked, skipped, deferred)
                    except StackGroupChanged as exc:
                        # 前提が変わった。**記録もしない**（次の送信で組み直す）。
                        # 次の行では現行のプロファイル版を読み直すので、進んでよい。
                        ctx.emit("info", f"組が変わったのでスタックを見送った: {exc}")
                        continue
                    except LeaseLost:
                        # **利用者が押したキャンセルを失敗として記録しない**（§9.9）。
                        # `uploader.run` と同じ受け口にする。失効なら外へ出す。
                        if ctx.cancelled():
                            ctx.emit("info", "キャンセルを観測してスタックを中止した")
                            return StackOutcome(stacked, skipped, deferred)
                        raise
                    stacked += result == "stacked"
                    skipped += result == "skipped"
        return StackOutcome(stacked, skipped, deferred)
```

`_one` の骨子:

1. 対象の `media_file` と、**そのプロファイルの現行リビジョン**
   （`registry.by_id(media["profile_id"])`）を読む。`immich.tags` と同じ層の判断で、
   取り込み時の版に固定しない（§9.11）。**読んだ `revision_id` は組ごとに固定し、
   guard と最終 CAS で「まだ現行か」を確かめる**（プロファイルの編集は同期の API で
   行えるので、組を決めた後・送る前に規則が変わりうる）
2. `rule.enabled` が偽なら `mark_skipped("プロファイルがスタックを使わない")`
3. `sources_of` でカード上の観測を**すべて**得る。空なら
   `mark_skipped("カード上の観測が残っていない")`
4. 観測ごとに `siblings_on_card` で相方候補を引き、`record_for(destination_id, epoch,
   media_file_id)` で宛先側のレコードを付ける（**現行 epoch のものだけ**）
5. `resolve_group`。`Refusal` なら `mark_skipped(reason)`
6. **ここから相手に触る。** `_guard`（下）を通してから、各 member の
   `client.asset(remote_asset_id)` を読む
7. 全員 `stack_id is None` なら **`_guard` を通し直してから** `create_stack` →
   応答の primary が先頭 member でなければ **`_guard` を通し直してから**
   `set_stack_primary` → `mark_stacked`
8. 全員が同じ `stack_id` を持ち、その primary が組の中に居るなら
   `stack_by_primary` でメンバーを引き、集合が一致すれば **primary も検査し**
   （違えば `_guard` → `set_stack_primary` → 読み直して確認）`mark_stacked`（回収）、
   一致しなければ `mark_skipped("相手側に別のスタックがある")`
9. それ以外の形（一部だけスタック済み、別々のスタック）は
   `mark_skipped("相手側に別のスタックがある")`

`_guard` は §9.10 の `_guard` と同じ 2 段にする。**`complete` のレコードは claim を
持たないので `prepare_side_effect` は使えない**（Task 4 の `guard_stack_group`）。

```python
    def _guard(self, ctx, members, destination_id, epoch, revision_id, profile_revision_id):
        """**外部へ触る直前に必ず通す**（§9.10 の `_guard` と同じ作法）.

        prepare → preflight（相手待ちなので `with_lease_pulse` で囲む）→ prepare。
        前だけに置くと、向き先の再確認を待っている間に commit されたキャンセルを
        見落とす。**`POST` と `PUT` のそれぞれの直前で通す。**
        """
        self._uploads.guard_stack_group(
            ctx, members, destination_id, epoch, profile_revision_id
        )
        self._preflight.assert_target(
            revision_id, wait=lambda work: with_lease_pulse(ctx, work)
        )
        self._uploads.guard_stack_group(
            ctx, members, destination_id, epoch, profile_revision_id
        )
```

**相手待ちはすべて `with_lease_pulse` で囲む。** preflight だけでは足りない ——
`UPLOAD_TIMEOUT_SECONDS` は既定 86400 秒なので、`client.asset` も `create_stack` も
60 秒のリースを跨ぎうる。囲まないと、**`POST` が成功した直後の記録でリースを失って
正常なジョブが失敗になる。**

```python
    def _remote(self, ctx, work):
        """相手待ちを心拍で守る（`uploader` の送信と同じ形）."""
        return with_lease_pulse(ctx, work)
```

**行をまたいだ heartbeat も要る。** 1 件が短く終わると `with_lease_pulse` は 1 度も
打たない（`thread.join(timeout=間隔)` が先に返る）ので、相手に触らない見送りが
続くと積もる。`recompute._apply_batch` と同じく、**経過時間でも打つ。どちらも
`assert_lease` を先に呼ぶ**（`extend_lease` は `cancelling` でも延ばす）。

例外の対応:

```python
        except (ImmichUnavailable, ImmichAuthFailed, ImmichRedirected) as exc:
            # **未評価のまま残し、第 2 パスごと打ち切る。** これは組ではなく宛先の
            # 障害なので、次の組へ進んでも同じ結果になる（`run` が受ける）。
            raise DestinationUnusable(str(exc)) from exc
        except (ImmichRejected, ImmichProtocolError) as exc:
            # 再試行しても直らない。**理由を残して画面に出す。**
            self._uploads.mark_skipped(
                ctx, row, destination_id, epoch, profile_revision_id,
                f"相手が受け付けない: {exc}",
            )
            return "skipped"
```

`jobs_wiring.run_upload` は、3 つの mode すべての**あとで**第 2 パスを回す
（`approve` と `recheck` の早期 return を、共通の後処理へ落ちる形に直す）。

```python
        outcome = Stacker(
            conn, uploads, destinations, ProfileRegistry(conn), open_client, preflight
        ).run(ctx, destination_id)
        if outcome.stacked or outcome.skipped or outcome.deferred:
            ctx.emit(
                "info",
                f"スタック: {outcome.stacked} 組 / 見送り {outcome.skipped} 件"
                f" / 保留 {outcome.deferred} 件",
            )
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_stacker.py app/tests/test_uploader.py app/tests/test_upload_e2e.py -q`
Expected: PASS

- [ ] **Step 5: 変異試験**

`_guard` の 2 段目（`POST` 直前の取り直し）を消す、preflight を消す、
`with_lease_pulse` を外す、`ctx.cancelled()` の確認を消す、`cursor = row["id"]` を消す、
既存スタックの集合一致を `>=` に緩める、回収の経路から primary の検査を消す、
`unstacked_batch` から `epoch` を落とす、`DestinationUnusable` を `continue` に変える、
例外の 2 分岐を入れ替える、`mark_stacked` の `rowcount != 1` の検査を消す、
`_assert_current` から宛先の epoch の確認を外す／プロファイルの版の確認を外す、
`mark_skipped` を `_assert_current` の外で書く、`remote_asset_id` の比較を外す。

`BATCH_SIZE` を 1 にする変異は**検出できなくてよい**（挙動が同じ）。検出できないなら
その旨を記録する。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/jobs/stacker.py app/src/mediaferry/api/jobs_wiring.py app/tests/test_stacker.py
git commit -m "feat(uploads): アップロードの第 2 パスでスタックを作る"
```

---

## Task 6: 見送りを未評価へ戻す 2 つの経路

**見送りは「今は組めない」の記録であって、永久の拒否ではない。** 前提が変われば
未評価へ戻す。前提は 2 つある —— **`captured_at`**（4 条件の 1 つ）と、
**プロファイルの `stack` 節**（規則そのもの）。戻す経路が無ければ「現行版を使う」は
初回の評価にしか効かず、規則を有効にしても既に見送った行は二度と評価されない。

**Files:**
- Modify: `app/src/mediaferry/jobs/recompute.py:278-345`
- Modify: `app/src/mediaferry/db/profiles.py:73-112,220-232`
  （`_upsert_revision` と `_insert_revision` を共通の helper へまとめる）
- Test: `app/tests/test_recompute.py`、`app/tests/test_profile_registry.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_a_changed_capture_time_reopens_a_skipped_stack(world):
    """時刻がずれていて見送った組は、再計算で成立しうる（§6）."""
    world.record_skipped("相方と撮影時刻が一致しない")
    world.recompute()
    assert world.record()["stack_state"] is None
    assert world.record()["stack_reason"] is None


def test_an_unchanged_capture_time_leaves_the_skip_alone(world):
    world.record_skipped("相方が見つからない")
    world.recompute_without_change()
    assert world.record()["stack_state"] == "skipped"


def test_an_existing_stack_is_not_reopened(world):
    """**相手側に既にあるものを作り直さない**（§6）."""
    world.record_stacked("stack-1")
    world.recompute()
    assert world.record()["stack_state"] == "stacked"
```

プロファイル側（`app/tests/test_profile_registry.py`）:

```python
def test_a_new_revision_that_changes_the_stack_rule_reopens_the_skips(registry, db):
    """**規則そのものが変わったら組み直す。**

    `stack` を無効から有効にしても、拡張子や許容差を変えても、既に見送った行は
    未評価へ戻らなければ二度と評価されない。
    """
    record = _a_skipped_record(db, profile="my-camera")
    registry.update("my-camera", _definition(stack={"enabled": True,
                                                    "extensions": ["JPG", "CR2"],
                                                    "tolerance_seconds": 0}))
    assert _row(db, record)["stack_state"] is None
    assert _row(db, record)["stack_reason"] is None


def test_a_revision_that_leaves_the_stack_rule_alone_does_not_reopen(registry, db):
    """**名前やタグだけの編集で全件を再評価しない**（大きいライブラリでは重い）."""
    record = _a_skipped_record(db, profile="my-camera")
    registry.update("my-camera", _definition(name="別の名前"))
    assert _row(db, record)["stack_state"] == "skipped"


def test_a_builtin_sync_that_changes_the_stack_rule_reopens_the_skips(registry, db):
    """**ビルトインは `_insert_revision` を通らない**（`_upsert_revision` が直に書く）.

    共通の helper にまとめないと、この経路だけ戻らない。
    """
    record = _a_skipped_record(db, profile="canon-eos")
    _install_a_builtin_with_a_different_stack_rule()
    registry.sync_builtins()
    assert _row(db, record)["stack_state"] is None


def test_a_sync_without_a_definition_change_does_not_reopen(registry, db):
    record = _a_skipped_record(db, profile="canon-eos")
    registry.sync_builtins()
    assert _row(db, record)["stack_state"] == "skipped"


def test_an_omitted_stack_equals_an_explicit_disabled_one(registry, db):
    """**Phase 6 より前の定義は `stack` キーを持たない。**

    正規化せずに生の JSON で比べると、名前だけ変えた版で「省略 → 明示的な
    disabled」になり、規則は実質同じなのに全件を戻すことになる。
    """
    _install_a_revision_without_a_stack_section(db, profile="my-camera")
    record = _a_skipped_record(db, profile="my-camera")
    registry.update("my-camera", _definition(name="別の名前"))     # stack は disabled のまま
    assert _row(db, record)["stack_state"] == "skipped"


def test_going_from_omitted_to_enabled_reopens(registry, db):
    _install_a_revision_without_a_stack_section(db, profile="my-camera")
    record = _a_skipped_record(db, profile="my-camera")
    registry.update("my-camera", _definition(stack={"enabled": True,
                                                    "extensions": ["JPG", "CR2"],
                                                    "tolerance_seconds": 0}))
    assert _row(db, record)["stack_state"] is None


def test_a_stacked_record_is_never_reopened(registry, db):
    record = _a_stacked_record(db, profile="my-camera")
    registry.update("my-camera", _definition(stack={"enabled": False}))
    assert _row(db, record)["stack_state"] == "stacked"


def test_another_profile_is_untouched(registry, db):
    record = _a_skipped_record(db, profile="other-camera")
    registry.update("my-camera", _definition(stack={"enabled": False}))
    assert _row(db, record)["stack_state"] == "skipped"


def test_the_revision_and_the_reopen_are_one_transaction(registry, db):
    """**版だけ進んで見送りが残る窓を作らない。**

    reopen で落ちたら、新しいリビジョンごと巻き戻る。
    """
    with _reopen_raises():
        with pytest.raises(sqlite3.OperationalError):
            registry.update("my-camera", _definition(stack={"enabled": True,
                                                            "extensions": ["JPG", "CR2"],
                                                            "tolerance_seconds": 0}))
    assert registry.current("my-camera").revision == 1
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_recompute.py -q`
Expected: FAIL

- [ ] **Step 3: 最小実装**

`_apply_batch` の `tally.changed += 1` の隣（**同じトランザクションの中**）で:

```python
                tally.reopened += self._reopen_stack(row)
```

```python
    def _reopen_stack(self, row: sqlite3.Row) -> int:
        """見送りを未評価へ戻す（§6）.

        時刻が動くと、成立していなかった組が成立しうる。抽出は未評価しか拾わない
        ので、戻さないと二度と再評価されない。**`stacked` は戻さない**
        （相手側に既にあるものを作り直さない）。
        """
        return self._conn.execute(
            "UPDATE upload_record SET stack_state = NULL, stack_reason = NULL, updated_at = ?"
            " WHERE media_file_id = ? AND stack_state = 'skipped'",
            (now_iso(), row["id"]),
        ).rowcount
```

`RecomputeOutcome` に `reopened` を足し、ジョブの `emit` にも出す。

プロファイル側は、**2 つある版の発行経路を 1 つの helper にまとめてから**戻す。
`sync_builtins` は `_upsert_revision` の中で INSERT と `current_revision_id` の更新を
直に書いており、**`_insert_revision` を通らない**。まとめないと、ビルトインの版が
進んだときだけ戻らない。

```python
    def _publish_revision(
        self,
        profile_id: str,
        revision_id: str,
        revision: int,
        definition_json: str,
        previous_json: str | None,
    ) -> None:
        """新しいリビジョンを現行にする. **呼び出し側の取引の中で使う。**

        `stack` 節が変わったときは、そのプロファイルのメディアの**見送りを未評価へ
        戻す**（規則そのものが変わったので、前の判断は根拠を失っている）。
        **`stacked` は戻さない**（相手側に既にあるものを作り直さない）。

        **戻す範囲を `stack` の変化に限る。** 名前やタグだけの編集で全件を再評価
        すると、見送りの理由が foreign stack や 4xx の行まで相手へ問い合わせ直す
        ことになる。
        """
        self._conn.execute(
            "INSERT INTO profile_revision"
            " (id, profile_id, revision, definition_json, schema_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (revision_id, profile_id, revision, definition_json, PROFILE_SCHEMA_VERSION,
             now_iso()),
        )
        self._conn.execute(
            "UPDATE device_profile SET current_revision_id = ? WHERE id = ?",
            (revision_id, profile_id),
        )
        if previous_json is not None and _stack_rule_of(previous_json) != _stack_rule_of(
            definition_json
        ):
            self._conn.execute(
                "UPDATE upload_record SET stack_state = NULL, stack_reason = NULL,"
                " updated_at = ?"
                " WHERE stack_state = 'skipped' AND media_file_id IN ("
                "     SELECT id FROM media_file WHERE profile_id = ?)",
                (now_iso(), profile_id),
            )


def _stack_rule_of(definition_json: str) -> StackRule:
    """定義から `stack` 節を**正規化して**取り出す.

    **生の dict で比べてはいけない。** 旧リビジョンの JSON には `stack` キーが無く
    （Task 0 の契約で `STACK_DISABLED` として読む）、新しい JSON は
    `definition_to_json` の正規形で `{"enabled": false, ...}` を持つ。生で比べると
    **規則が実質変わっていないのに全件を戻す**ことになる。
    """
    return parse_definition(json.loads(definition_json)).stack
```

`previous_json is None`（新規作成）では比べない。新しいプロファイルにはメディアが
無いので戻す対象も無く、「旧 JSON の `stack` 省略」と混ぜないほうが読みやすい。

`_upsert_revision` と `update` / `create` の両方から `_publish_revision` を呼ぶ。
**どちらも既存の `immediate(self._conn)` の中にある**ので、helper は新しい
`BEGIN IMMEDIATE` を開かない（開くとネストになる）。索引は既存の
`media_file (profile_id, ...)`（`0013`）と `upload_record (media_file_id)` が使える形に
書く。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_recompute.py app/tests/test_profile_registry.py -q`
Expected: PASS

- [ ] **Step 5: 変異試験**

`stack_state = 'skipped'` の条件を外す（`stacked` も戻る）、`_reopen_stack` を
`_same` の側（変化なし）でも呼ぶ、トランザクションの外へ出す、
`_publish_revision` の `profile_id` の絞りを外す（他のプロファイルまで戻る）、
`_stack_rule_of` の比較を常に真にする（`stack` 以外の編集でも全件戻る）／常に偽にする
（規則を変えても戻らない）、`_upsert_revision` を helper から外す
（ビルトインの経路だけ戻らない）。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/jobs/recompute.py app/src/mediaferry/db/profiles.py app/tests
git commit -m "feat(uploads): 前提が変わったらスタックの見送りを未評価へ戻す"
```

---

## Task 7: API（一覧・フィルタ・サマリ）

**Files:**
- Modify: `app/src/mediaferry/api/routes_uploads.py:56-64,198-224`
- Modify: `app/src/mediaferry/api/routes_system.py:81-107`
- Modify: `app/src/mediaferry/db/uploads.py`（`list_records` に `stack_state`）
- Test: `app/tests/test_api_uploads.py`、`app/tests/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_a_record_shows_its_stack_state(client, world):
    body = client.get("/api/uploads").json()["records"][0]
    assert body["stack_state"] == "skipped"
    assert body["stack_reason"] == "相方が見つからない"
    assert body["remote_stack_id"] is None


def test_records_can_be_filtered_by_stack_state(client, world):
    body = client.get("/api/uploads?stack_state=skipped").json()
    assert [r["id"] for r in body["records"]] == [world.skipped_id]


def test_an_unknown_stack_state_is_refused(client):
    assert client.get("/api/uploads?stack_state=nonsense").status_code == 400


def test_the_dashboard_counts_stacks_per_destination(client, world):
    summary = client.get("/api/dashboard").json()["destinations"][0]
    assert summary["stacked"] == 2 and summary["stack_skipped"] == 1
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_api_uploads.py -q`
Expected: FAIL

- [ ] **Step 3: 最小実装**

`_view` に 3 列を足し、`list_records` に `stack_state: str | None` を足す
（`'stacked'` / `'skipped'` / `'unevaluated'` を受け、`unevaluated` は `IS NULL`。
**未知の値は 400** ——「絞ったつもりで全件が出る」を作らない）。
`_destination_summary` に 2 つのカウントを足す（**無効化された記録は数えない**）。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_api_uploads.py app/tests/test_api.py -q && npm --prefix web run typegen && uv run pytest app/tests/test_api_types_are_current.py -q`
Expected: PASS（型の再生成を忘れると最後のテストが落ちる）

- [ ] **Step 5: 変異試験**

未知の `stack_state` を素通しにする、`unevaluated` を `= 'unevaluated'` にする、
サマリの `invalidated_at IS NULL` を外す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/api app/src/mediaferry/db/uploads.py app/tests web/src/api/types.ts
git commit -m "feat(api): スタックの状態を一覧とサマリに出す"
```

---

## Task 8: 画面

**Files:**
- Modify: `web/src/screens/Destinations.tsx`
- Modify: `web/src/screens/Dashboard.tsx`
- Test: `web/src/screens/screens.test.tsx`

**注意:** **API はあるが画面から呼べない機能は、無いのと同じ**（Phase 4 の教訓）。
見送りの理由は「対象外のときだけ出す」形にしない —— 出ていないことが仕様に
見える（Phase 5 Task 8 の教訓）。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("宛先ごとにスタックの見送りと理由を出す", async () => {
  stubApi({
    "/destinations": destinations,
    "/uploads?destination_id=d1&stack_state=skipped": {
      records: [{ id: "u1", media_file_id: "m1", stack_reason: "相方が見つからない" }],
    },
  });
  render(<DestinationsScreen />);
  expect(await screen.findByText(/相方が見つからない/)).toBeInTheDocument();
});

it("見送りが無いときは、無いと書く", async () => {
  stubApi({ "/uploads?destination_id=d1&stack_state=skipped": { records: [] } });
  render(<DestinationsScreen />);
  expect(await screen.findByText(/見送りはありません/)).toBeInTheDocument();
});

it("ダッシュボードがスタックの件数を出す", async () => { ... });
```

- [ ] **Step 2: 失敗を確認する**

Run: `npm --prefix web test`
Expected: FAIL

- [ ] **Step 3: 最小実装** — `Destinations.tsx` に「スタック」節、`Dashboard.tsx` の
`DestinationSummary` 型に 2 つのフィールドと表示を足す。

- [ ] **Step 4: 通ることを確認する**

Run: `npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build`
Expected: すべて PASS

- [ ] **Step 5: コミット**

```bash
git add web/src
git commit -m "feat(web): スタックの結果と見送りの理由を画面に出す"
```

---

## Task 9: 受け入れ（E2E）と crash consistency

**Files:**
- Modify: `app/tests/exif_fixtures.py`（`a_tiff_with`）
- Modify: `app/tests/system/harness.py:74-88`（Canon カードに対を足す）
- Create: `web/e2e/phase6.spec.ts`
- Modify: `app/tests/test_crash_consistency.py`

- [ ] **Step 1: 合成 CR2 が EXIF を返すことを先に測る**

**推測で進めない。** `exifread` が TIFF ヘッダだけの `.CR2` を読めるかは、
実際に読ませて確かめる。

```python
def test_a_synthetic_cr2_yields_its_capture_time(tmp_path):
    path = tmp_path / "IMG_1234.CR2"
    path.write_bytes(a_tiff_with(b"2026:08:19 10:30:00"))
    assert read_datetime_original(path) == "2026:08:19 10:30:00"
```

Run: `uv run pytest app/tests/test_exif.py -q`
読めなければ、CR2 の形（TIFF マジックの後に `CR\x02\x00`）まで足して測り直す。
**読めないまま E2E を組むと、組が成立しない筋書きを「仕様どおり」と誤読する。**

- [ ] **Step 2: E2E の失敗を確認する**

`harness.py` の `_a_canon_card` に、**同じ EXIF 時刻を持つ** `IMG_0003.JPG` と
`IMG_0003.CR2` を足す。`web/e2e/phase6.spec.ts` を書く:

1. Canon カードを取り込む
2. 宛先を作り、ライブラリから 2 枚を送る
3. 送信後、**Immich（fake）側で 1 スタックになっている**
4. 画面（宛先）に「スタック済み」が出る
5. `pre_existing` の相方を混ぜた組では**理由の文言**が読める

Run: `npm --prefix web run test:e2e`
Expected: FAIL

- [ ] **Step 3: 通るまで直す**

Run: `npm --prefix web run test:e2e`
Expected: 3 spec すべて PASS（journey / phase5 / phase6）

- [ ] **Step 4: crash consistency**

`test_crash_consistency.py` に、第 2 パスの**3 点**で `os._exit` する筋書きを足す。

| 落とす場所 | 次の送信で起きるべきこと |
| --- | --- |
| `POST /stacks` の応答を受け取る前 | 相手にスタックがあるかは不明。集合一致なら回収、無ければ作る |
| `POST /stacks` の直後・`PUT` の前 | 集合一致で回収し、**primary を望みの側へ直す**（検査を落とすとここが落ちる） |
| `PUT /stacks/{id}` の直後・記録の前 | 集合も primary も一致するので、`PUT` を打たずに記録だけする |

**primary まで assert する。** 集合の一致だけを見ると、`PUT` 前に落ちた世界で
「回収できた」と誤読する。

Run: `uv run pytest app/tests/test_crash_consistency.py -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add app/tests web/e2e
git commit -m "test: スタッキングの受け入れと中断からの回収を通す"
```

---

## Task 10: 実 Immich とドキュメント

**Files:**
- Modify: `app/tests/test_immich_live.py`
- Modify: `docs/HANDOFF.md`
- Modify: `docs/phase6-plan.md`（この計画に「実装で分かったこと」を書き戻す）

- [ ] **Step 1: 実機のテストを書く**

既存の `test_the_whole_upload_path_works_against_a_real_server` と同じ作法で
（**作った資産とスタックは必ず後片付けし、消せなければ送出する**）:

```python
@pytest.mark.needs_immich
def test_the_stack_path_works_against_a_real_server(client, tmp_path):
    """作成 → 取得 → primary 差し替え → 削除まで実機で通す.

    **「既存スタックを吸収する」挙動そのものも測る。** こちらが触らないと決めた
    判断の前提なので、前提の側を確かめる（§9.11）。
    """
```

- [ ] **Step 2: 実機で流す**

```bash
set -a; . ~/.config/mediaferry/test-immich.env; set +a
uv run pytest -m needs_immich -q
```

Expected: PASS。**吸収の挙動が仕様と違ったら、`docs/design.md` §9.11 と
この計画へ実測を書き戻してから実装を直す。**

- [ ] **Step 3: 全体を流す**

```bash
uv run pytest && uv run pytest -m needs_system && uv run ruff check . && uv run ruff format --check .
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build && npm --prefix web run test:e2e
```

**テストの実行中にソースやテストを書き換えない**（`docs/HANDOFF.md` §1）。

- [ ] **Step 4: ドキュメント**

`docs/HANDOFF.md` を更新する。**数値は実測で取り直す**
（`git rev-list --count HEAD`、各テストの件数）。

- 現在地の表に Phase 6 を足す
- 「Phase 5 から Phase 6 へ送ったもの」を、**多重化はやらないと決めた**記録へ
  書き換える（3 度目の先送りにしない）
- 残件に「Canon の実カードで RAW+JPEG の組が成立するか」を足す
  （`phase1-manual-checklist.md` の 13〜16 番の隣）

- [ ] **Step 5: コミット**

```bash
git add -A docs app/tests
git commit -m "docs(mediaferry): Phase 6 の実測と引き継ぎを書き戻す"
```

---

## 実装の前に決めてあること

**いずれも既定を置いてある。異論は計画レビューで出す。**

| 項目 | 決め | 理由 |
| --- | --- | --- |
| 組の規則の版 | **プロファイルの現行リビジョン**（`ProfileRegistry.by_id`）を使い、**組の全員に同じ `profile_id` を要求する** | スタックは取り込みの記録ではなく、いま適用する操作。`immich.tags` と `fix_datetime_after_upload` が既に現行リビジョンを見ている（`by_id` は `current_revision_id` を join する）。取り込み時の版に固定すると、版ごとに規則が違う member でどちらが先に走るかで結果が変わり、`stack` を持たない旧版で取り込んだ既存ライブラリは永久に対象外になる |
| 相方の探索範囲 | **published な観測が 1 つでも同じ `volume_instance` で重なること** | 同じカードの同じシャッターだけを組にする。集合で見るのは、`observed_at` が再スキャンで動くため（順序で 1 つ選ぶと組が実行のたびに変わる） |
| 宛先の epoch | **現行 `target_epoch` に固定**（開始時に読み、抽出・相方の解決・記録すべてに使う） | 旧 epoch の `complete` は無効化されずに残る。絞らないと別ライブラリの資産 ID を現行の資格情報で送る |
| 宛先の障害 | **最初の `deferred` で第 2 パスを打ち切る** | 認証失敗・redirect・5xx は組ではなく宛先の障害。次の組へ進むと未評価の全件ぶん要求を投げ続ける（timeout は既定 86400 秒） |
| `BATCH_SIZE` | **50** | 再計算（Phase 5 Task 6）と同じ桁。1 件あたり相手への往復が 1〜3 回あるので、これ以上増やしてもリースの窓が伸びるだけ |
| 保留（5xx）の上限 | **持たない** | 未評価のまま残すだけで、相手にも DB にも痕跡が増えない。宛先が直れば次の送信で解決する |
| 手動の再スタック | **作らない** | 再評価の経路は「再計算」と「プロファイルの新リビジョン」の 2 つで足りる（YAGNI）。どちらも見送りを未評価へ戻す |
| 規則の版の固定 | **組ごとに読んだ `revision_id` を固定し、guard と最終 CAS で「まだ現行か」を見る** | プロファイルの編集は同期 API で行えるので、組を決めた後・送る前に規則が変わりうる |
| 宛先の epoch の固定 | 開始時に固定し、**guard で「宛先の現行 epoch と同じか」も見る** | epoch を進める編集は `complete` を無効化しないので、第 2 パスだけが既存の停止境界から外れる |
| 動画の組 | **作らない** | `MVI_*.MOV` は 1 ファイルで完結する。組にする根拠が無い |

## レビュー記録

**`--fresh` で回すこと** —— 継ぎ足すと、自分が提案した対処の周りが盲点になる
（`docs/HANDOFF.md` §5）。

### 計画レビュー 1 巡目（2026-08-19、codex `--fresh`。blocker 3 / major 5 / minor 1）

**6 件は全面的に妥当、2 件は部分採用、1 件は「実装では閉じられない」ことの明記へ。**
指摘はすべて実装を読んで裏を取った。

| # | 指摘 | 判定 | 反映 |
| --- | --- | --- | --- |
| blocker 1 | **第 2 パスが `target_epoch` を絞らない。** 向き先を変えた宛先では旧 epoch の `complete` が無効化されずに残るので、**別ライブラリへ送った資産 ID を現行の資格情報で送る** | **妥当。** `_invalidate_old_epoch_locked` は `state <> 'complete'` だけを無効化し、`records_for_recheck` は同じ理由で epoch を条件にしていた。こちらだけ外れていた | 開始時に現行リビジョンと epoch を固定し、抽出・`record_for`・guard・記録のすべてに使う。索引も `(destination_id, target_epoch, id)` へ。旧 epoch を触らない試験を追加 |
| blocker 2 | **外部副作用の手前が Phase 3 の契約を満たしていない。** 複数の GET の前に `assert_lease` を 1 回だけで、preflight も pulse も `POST`/`PUT` 直前の取り直しも無い。`mark_stacked` は一部だけ書ける | **妥当。** `uploader._guard` は prepare → preflight（pulse）→ prepare を**毎回の通信の直前**に通しており、コメントが「TTL を跨いだ後の tag が別ライブラリへ飛ぶ」危険まで書いている | `guard_stack_group`（リース + 組全員の現在値を 1 トランザクションで照合）を足し、`_guard` を `POST` と `PUT` の直前ごとに通す。`mark_stacked` は全員一致でなければ 1 行も書かない。GET 中・`POST` 前・`POST`〜`PUT` の間・記録前の競合を試験に追加 |
| blocker 3 | **「既存スタックには触らない」は TOCTOU で保証できない。** Immich に条件付きの作成が無く、読み直しと `POST` の間に作られたスタックは吸収される | **妥当。実装では閉じられない。** 保証を主張したままにはできない | §9.11 の文言を「保証ではなく、窓を最小にした最善」へ改め、**残余の競合として明記して受容**。窓を最小にする 2 つの手当（直前に読み直す／`created_by_us` の組だけ）を書いた |
| major 1 | **「同じカード」は証明にならない**（`volume_instance` は推測で、複製媒体は畳まれる）。`source_entry` は上書きされ、`observed_at` は再スキャンで動くので「最初の観測」は安定しない | **半分妥当。** 順序の不安定さは実在する（`scan.py::_touch`）。複製媒体の畳み込みも実在する | ① `source_of` を **`sources_of`（集合）** に変え、「published な観測が 1 つでも同じボリュームで重なる」を条件にした（順序に依らない）② 観測が残っていない場合は理由つきの見送り ③ **複製媒体は残余リスクとして明記して受容**。不変の観測 cohort を別表に持つ案は採らない —— 他の 3 条件（同じ stem・秒まで一致する `captured_at`・同じ `captured_at_source`）を別々のシャッターが同時に満たすことは実質的に無く、追加する表と provenance の維持コストが見合わない |
| major 2 | **規則の版が member ごとに違うと結果が変わる。** かつ `stack` を持たない旧版で取り込んだ既存ライブラリは永久に対象外 | **妥当。** 後者は実害が大きい | **対処は指摘の案（rule digest の分離）ではなく、規則を「プロファイルの現行リビジョン」から読む形にした。** `ProfileRegistry.by_id` は `current_revision_id` を join しており、`immich.tags` と `fix_datetime_after_upload` は既にそう振る舞っている。**組の全員に同じ `profile_id` を要求**すれば規則は 1 つに決まり、既存ライブラリも版が進んだ時点で対象になる |
| major 3 | **`POST` の直後・`PUT` の前に落ちると、望みの primary へ二度と直らない。** 集合一致の回収経路が primary を見ていない | **妥当** | 回収の経路でも primary を検査し、違えば guard → `PUT` → 読み直して確認。crash 試験の落とす場所を 3 点に増やし、**primary まで assert する** |
| major 4 | **宛先の障害で未評価の全件へ通信を繰り返す。** 認証失敗・redirect・5xx は組固有ではない | **妥当。** timeout は既定 86400 秒なので事実上終わらなくなる | `DestinationUnusable` を送出して**第 2 パスごと打ち切る**。残りは未評価のまま次の送信へ渡す |
| major 5 | **adapter の fail-closed が不足。** ① `POST`/`PUT` が `allow_redirect=False` でない ② 作成の応答が要求集合と一致するか、primary が member か、重複が無いかを見ていない | **② は妥当。① は退けた** —— `allow_redirect=False` の根拠は「ストリームが 1 回で EOF に達する」ことで（`upload_asset` のコメント）、JSON 本文の `tag_assets` / `set_date_time_original` は既定のまま。別 origin への redirect は `_same_origin_target` が必ず拒むので、鍵が外へ出る経路は閉じている | ② を全面採用（要求集合との全単射、primary ∈ members、重複の拒否、`PUT` 後の読み直し確認）。① は退けた理由を計画に明記 |
| minor 1 | `stack_by_primary` が `response.json()` を直に呼ぶので、非 JSON が `ValueError` のまま漏れて分岐に入らない | **妥当** | `_as_array` を `_as_object` の隣に足し、object でない要素も protocol error にする。`RemoteAsset.stack` は「キーが無い＝旧版で `None`」と「キーはあるが形が違う＝protocol error」を分ける |

**確認された「指摘なし」:** `0015` のトリガに `state = 'complete'` を入れない判断、
部分索引を足す判断そのもの、`UPLOAD_CONCURRENCY` の env / DB / UI からの撤去方針。

**止め時の判断:** レビュー側は「まだ逓減局面ではない。設計と計画を直した次巡は必須」
と述べている。次巡を回す。

### 計画レビュー 2 巡目（2026-08-19、codex `--fresh`。blocker 1 / major 4 / minor 2）

**8 件すべて妥当。反論できたものは無い。** 1 巡目の対処が作った境界から出た
（epoch の固定、guard の 2 段化、規則を現行版から読むこと、観測の集合化）。
**1 巡目で退けた `allow_redirect` は、こちらの根拠が誤っていた。**

| # | 指摘 | 判定 | 反映 |
| --- | --- | --- | --- |
| blocker 1 | **固定した epoch が「いまも現行か」を見ていない。** 実行中に別ライブラリへ向き替えられても、旧 epoch の `complete` は無効化されず、preflight は固定した旧リビジョンを見るので、**旧ライブラリへ副作用を続ける** | **妥当。** epoch を進める編集は `state <> 'complete'` しか無効化しない（`_invalidate_old_epoch_locked`）ので、**第 2 パスだけが既存の停止境界から外れていた** | `guard_stack_group` に `destination_id` を渡し、同じ `BEGIN IMMEDIATE` で「宛先の現行リビジョンの `target_epoch` = 固定した epoch」を確かめる。**最初の GET の最中に向き替える**競合試験を追加（前巡の試験は開始前に変えるだけで、この穴を通らない） |
| major 1 | **リース対策が preflight にしか掛かっていない。** 実通信（`asset` / `create_stack` / `PUT`）もローカルの見送りも守られていない。`cancel_during_first_get` の試験は `LeaseLost` の受け口を書いていない | **妥当。** timeout は既定 86400 秒なので、正常な 1 回の通信が 60 秒のリースを跨ぐ。**`POST` が成功した直後の記録で失敗する**経路が実在する | 相手待ちをすべて `with_lease_pulse` で囲む。`mark_skipped` も `ctx` と epoch を受けて `assert_lease` + CAS を 1 トランザクションで行う。行をまたいだ heartbeat も足す（`recompute._apply_batch` と同じ 2 つの仕掛け）。`run` に `LeaseLost` の受け口を書く（キャンセルなら正常に中止、失効なら外へ） |
| major 2 | **現行リビジョンを使う設計に、版の固定・競合検査・再評価の経路が無い。** 組を決めた後に編集されても guard は見ず、一度見送った行は規則を変えても戻らない | **妥当。** プロファイルの編集は同期 API（`routes_system`）で行える。**「現行版を使う」が初回の評価にしか効いていなかった** | 組ごとに `revision_id` を固定し、guard と最終 CAS で「まだ現行か」を確かめる。**新しいリビジョンを作る取引の中で、そのプロファイルのメディアの見送りを未評価へ戻す**（`sync_builtins` も同じ経路）。Task 6 を「戻す 2 つの経路」に作り直した |
| major 3 | **観測の集合化で、ボリュームと stem の相関が消えた。** 別々の観測でそれぞれが一致するだけの組が通る。同じ `media_file` が重複 member にもなりうる | **妥当。** 1 巡目の対処（集合化）が作った境界そのもの | `Candidate` を**観測 1 つぶん**にし、鍵を `(volume_instance_id, stem_prefix)` の**組**で持つ。member は `media_file_id` で一意化する。相関が消える筋書きと、同じ media が 2 つの観測を持つ筋書きを試験に追加 |
| major 4 | **`allow_redirect=False` を退けた根拠が `POST /stacks` には当たらない。** `_request` は 303 でも method を変えずに再送するので、**非冪等で吸収する要求を自動 replay する**。全単射の検査も入力側の重複を閉じていない | **妥当。こちらの誤り。** 根拠を「本文が再送できるか」だけで見ていた。非冪等性と吸収の仕様を合わせると replay は安全でない | `create_stack` の `POST` を `allow_redirect=False` にする（`PUT` は冪等なので既定のまま）。入力の重複も送る前に拒み、全単射は件数も見る |
| minor 1 | **epoch を落とした問い合わせの EXPLAIN 試験が、コメントどおりの検査になっていない。** 複合索引は先頭 prefix でも使われるので、索引名の不一致では捕まらない | **妥当** | 否定形の試験を消し、**正しい問い合わせが 2 本とも search key に入っていること**と、並べ替えが消えていることを見る形にした。epoch 抜けは旧 epoch の挙動試験で閉じる |
| minor 2 | Task 4 の提示テストと Interfaces が、更新後の signature に追随していない | **妥当** | `_candidate` の既定値、Interfaces、リポジトリの試験をすべて新しい形へそろえた |

**確認された「異論なし」:** TOCTOU の残余を明記して受容する判断、複製媒体の残余
リスクを受容する判断、`immediate` の例外で取引ごと巻き戻るなら「一部だけ commit」は
閉じられるという読み。

**止め時の判断:** レビュー側は「前巡の指摘はかなり収束した。上記を直した後は、
実装前レビューとしては逓減局面に入る見込み」と述べている。**3 巡目を回してから
実装に入る。**

### 計画レビュー 3 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 3 / minor 3）

**blocker は消えた。6 件すべて妥当。** 3 件は「2 巡目で採用した『現行版を使う』の
成立条件が、計画に落ち切っていなかった」もの。

**major 3 は、こちらの作業事故を捕まえた指摘だった。** 2 巡目の Task 6 の書き換えは
**保存されていなかった**（パッチの script が最後の置換で assert に失敗し、書き出す前に
中断していた）。レビュー記録だけが「作り直した」と述べ、本文には無い状態になっていた。
**文書の整合はレビューに頼らず、反映のたびに `grep` で確かめる。**

| # | 指摘 | 判定 | 反映 |
| --- | --- | --- | --- |
| major 1 | **`mark_skipped` が現行プロファイル版を CAS しない。** ローカルで決着する見送りは guard を通らないので、規則を読んだ後・書く前に版が進むと**旧規則の判断が新しい版の世界に残り、二度と評価されない**。`rowcount` も見ていない | **妥当。** 「規則を読む → 別接続が新版を出して見送りを戻す → こちらが書く」で成立する | `mark_skipped` に `destination_id` / `profile_revision_id` / 期待する `remote_asset_id` を渡し、guard と同じ `_assert_current` を通す。`rowcount != 1` は `StackGroupChanged`。編集と repoint の競合試験を追加 |
| major 2 | **`mark_stacked` の signature と SQL が、文書の約束（最終 CAS で現行版を確認）を実装できない形** | **妥当。** 散文にだけ書いて signature に落としていなかった | 同じく `_assert_current` を通し、member ごとの `remote_asset_id` も CAS に含める。外部副作用の後で落ちた場合は**書かずに次の送信の回収経路へ渡す** |
| major 3 | **2 巡目で採用した「プロファイル改版で見送りを戻す」実装・試験が Task 6 に無い。** かつ `sync_builtins` は `_upsert_revision` の中で直に書いており `_insert_revision` を通らないので、「同じ経路」は成立しない | **妥当。** 前者はこちらの保存事故、後者は実装を読んで確認した | Task 6 を「戻す 2 つの経路」に作り直し、**`_publish_revision` という共通 helper**（INSERT + `current_revision_id` の更新 + 戻し）を両経路から呼ぶ形にした。`test_profile_registry.py` の試験も具体化した |
| minor 1 | **「どの改版でも全 skipped を戻す」は範囲が広すぎる。** 名前やタグだけの編集で、相手側の事情で見送った行まで再評価する | **妥当** | 戻すのは**`stack` 節が変わったときだけ**にした（`_stack_rule_of` で比べる）。「`stack` 不変の編集では戻らない」試験も足した |
| minor 2 | 例外処理の提示コードが `mark_skipped` の新 signature に追随していない | **妥当**（これも保存事故の巻き添え） | 一本化した |
| minor 3 | `media_file_id` の一意化が O(n²)。観測の数に設計上の上限は無い | **妥当** | `seen` の集合を 1 つ持つ形にした。あわせて「短い見送りが多数続いてリースを跨ぐ」試験を足し、行をまたいだ heartbeat が実装漏れにならないようにした |

**確認された「異論なし」:** 2 巡目の epoch guard・通信中の pulse・観測単位の
`Candidate`・`POST` の redirect 禁止、TOCTOU と複製媒体の受容、プロファイル編集の
取引が `upload_record` を触ること（SQLite の単一 writer と同じ接続の取引の中で行い、
helper が別の `BEGIN IMMEDIATE` を開かなければデッドロックは生じない）。

**止め時の判断:** レビュー側は「blocker は消え、指摘は逓減局面。上記を反映したら
短くもう 1 巡し、新しい major が無ければ実装へ進んでよい」と述べている。
**4 巡目を短く回す。**

### 計画レビュー 4 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 0 / minor 2）

**新しい major は無し。「実装に着手してよい」との判定。** 計画レビューはここで
打ち切り、以後は実装差分を見せる側へ移す。

| # | 指摘 | 判定 | 反映 |
| --- | --- | --- | --- |
| minor 1 | **`_stack_rule_of` が「省略」と「明示的な disabled」を同一視していない。** Phase 6 より前の定義は `stack` キーを持たず、`definition_to_json` の正規形は `{"enabled": false, ...}` を書くので、名前だけ変えた版で**規則は実質同じなのに全件を戻す** | **妥当。** Task 0 の「省略は `STACK_DISABLED`」という契約と、比較の実装がずれていた | `parse_definition(...).stack` で**正規化してから**比べる。`previous_json is None`（新規作成）では比べない。「省略 → 明示的 disabled で戻らない」「省略 → enabled で戻る」の試験を足した |
| minor 2 | **repoint 後も旧 epoch の残り全件を走査する。** 無限ループにはならないが、`StackGroupChanged` を握って `continue` するので、各行で同じ失敗を繰り返す | **妥当**（安全性ではなく打ち切り効率） | `DestinationChanged(StackGroupChanged)` を分け、**固定した epoch 全体が無効なときは第 2 パスを正常中止する**。プロファイル編集は次の行で現行版を読み直すので `continue` のままでよい |

**確認された「指摘なし」:** `_assert_current` が 3 経路とも呼び出し側の
`BEGIN IMMEDIATE` の中にあること、`mark_skipped` の `StackGroupChanged` が
（cursor が先に進むので）同一実行の無限再評価を作らないこと、`mark_stacked` の
全員 CAS が例外で取引ごと巻き戻ること、`_publish_revision` を既存の取引の中の
共通 helper にする方針（`create` は media が無く 0 行、`duplicate` は `create` 経由）。

**止め時の判断:** レビュー側は「安全性・冪等性・リースに関する新しい major は
出ず、残りは実装中に局所テストで閉じられる minor。計画レビューを重ねるより、
Task ごとの実装差分で SQL の実行計画・例外型の流れ・実際の Immich 応答形を
確認する方が品質への寄与が大きい」と述べている。**計画レビューは 4 巡で打ち切り、
実装へ入る。**
