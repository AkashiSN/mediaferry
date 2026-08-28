# Phase 15 実装計画 — 送り直しをやめ、再確認から通常経路へ戻す

> **エージェント向け:** このリポジトリは TDD で進める。**各タスクは「落ちるテストを
> 書く → 落ちることを確認する → 最小実装 → 通ることを確認する → コミット」**の順で行う。
> 手順は `- [ ]` のチェックボックスで追う。

**目標:** 送り直し専用の経路（`POST /uploads/{id}/requeue` と「送り直す」ボタン）を無くし、
**再確認が「リモートに無い」と判定した記録をその場で無効化して、メディアを通常の
「まだ送っていない」へ戻す**。あわせて、再確認がスタックの現存とメンバー集合も照合する。

**設計（正本）:** [`phase15-design.md`](phase15-design.md)。仕様は
[`../design.md`](../design.md)、判断は [`../decisions.md`](../decisions.md)。

**アーキテクチャ:** 状態機械には状態を足さない。`invalidated_at` は状態機械と直交する
既存のフラグで、「この記録はもう有効ではない」を表す。消滅した送信記録にこれを立てると、
`GET /media?status=unsent` の定義（「この宛先の**有効な**記録がまだ無く、いま送れるもの」）に
そのまま当てはまり、通常の選択経路へ戻る。**送信そのものは依然として利用者の明示操作。**

**技術:** Python 3.14 / FastAPI / SQLite（`BEGIN IMMEDIATE` + CAS）/ React + TypeScript /
pytest / vitest。

## 全体の制約

- `uv sync --all-packages` が必須（素の `sync` ではメンバーが入らない）
- テストは `uv run pytest`、lint は `uv run ruff check .` と `uv run ruff format --check .`
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付ける**
- **`docs/` は ruff の対象外**（`extend-exclude`）
- コメントと docstring は**日本語**。**過去の経緯はコードに書かない**（`docs/` に残す）
- コミットは Conventional Commits + 日本語の本文。**なぜそうしたか**を本文に残す
- **コミット本文に Claude のセッション URL を入れない**
- 環境固有の値（IP・ホスト名・鍵・タイムゾーンの実値）をコードにもテストにも書かない
- DB へ入れるシステム時刻は UTC の ISO-8601。生成は `mediaferry.clock` の関数だけ

## ファイルの見取り図

| ファイル | この計画での役割 |
| --- | --- |
| `app/src/mediaferry/db/uploads.py` | `stamp_many` に無効化を足す。`_pair` と `record_for` に `invalidated_at IS NULL` を足す。`stacked_groups` / `reopen_stacks` を新設 |
| `app/src/mediaferry/jobs/recheck.py` | `_reconcile_stacks` を新設し、`RecheckOutcome` に `unstacked` を足す |
| `app/src/mediaferry/adapters/immich.py` | `stacks()` を新設（`GET /api/stacks`、絞り込み無し） |
| `app/src/mediaferry/api/jobs_wiring.py` | 再確認の結果の 1 行に「組を戻した数」を足す |
| `app/src/mediaferry/api/routes_uploads.py` | `requeue_upload` を削除 |
| `app/src/mediaferry/api/errors.py` | `ErrorCode.NOT_REQUEUEABLE` を削除 |
| `app/tests/fake_immich.py` | `GET /api/stacks`（絞り込み無し）の経路を足す |
| `web/src/screens/PhotoDetail.tsx` | `requeue()` と「送り直す」ボタンと `missing_at` の但し書きを削除 |
| `web/src/api/errors.ts` | `not_requeueable` の文言を削除 |
| `web/src/api/types.ts` | 再生成 |
| `docs/design.md` / `docs/decisions.md` / `docs/user-guide.md` / `docs/development.md` | §6 の表のとおり |

## タスクの並び

| # | 中身 | 依存 |
| --- | --- | --- |
| 2A | **実行中に足した。** 表制約の UNIQUE を部分 UNIQUE 索引へ（移行） | 1 |
| 1〜3 | 土台。無効化と、それを跨ぐ 2 か所（`_pair` / `record_for`） | 2A |
| 4 | 土台の通し（一覧に戻る・`origin` がやり直しになる） | 1〜3 |
| 5〜6 | スタックの照合（adapter と再確認の新しい段） | — |
| 7 | 通しテスト用に `World` を広げる | — |
| 8 | 片方だけ消えた組が、送り直しで組み直る | 1〜3, 7 |
| 9 | 解けた組・崩れた組の決着 | 5, 6, 7 |
| 10〜11 | 画面と API の削除 | — |
| 12 | 記録 | 全部 |
| 13 | 全体の確認と PR | 全部 |

1〜4 と 5〜6 は独立している。**8 は 1〜3 の全部が要る**ので、そこより後に置く。

---

### Task 1: 消滅した記録を、その場で無効化する

**ファイル:**
- 変更: `app/src/mediaferry/db/uploads.py`（`stamp_many` の UPDATE）
- 変更: `app/src/mediaferry/jobs/recheck.py`（警告の文言）
- テスト: `app/tests/test_upload_recheck.py`

**インタフェース:**
- 消費: 既存の `Stamp` / `stamp_many(ctx, stamps, checked_at) -> set[str]`
- 提供: 消滅した行は `invalidated_at` が入り `invalidated_reason = 'remote_missing'` になる。
  **署名は変えない**（Task 3〜9 はこの前提で書く）

- [ ] **Step 1: 落ちるテストを書く**

`app/tests/test_upload_recheck.py` の `test_a_vanished_asset_is_shown_as_missing_not_resent` を
次で置き換える。**古いテストは「自動では送り直さない」を確かめており、その判断はこの回で
覆したので残さない。**

```python
def test_a_vanished_asset_is_invalidated_so_it_returns_to_unsent(world):
    """消えた資産の記録は無効化する。**送り直し専用の状態を持たない。**

    無効化された記録は「この宛先の有効な記録」ではなくなるので、そのメディアは
    通常の「まだ送っていない」へ戻る（§9.10）。送信そのものは利用者の明示操作の
    ままなので、「意図的に消したものを黙って送り直さない」は保てる。
    """
    server, rechecker, ctx, destination_id, db = world
    server.assets.clear()  # 保持期限を過ぎて完全に消えた

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.vanished == 1
    assert row["remote_asset_id"] is None
    assert row["remote_checked_at"] is not None
    assert row["invalidated_at"] is not None
    assert row["invalidated_reason"] == "remote_missing"
    # **この場では送らない。** 送るのは利用者が通常経路で選んだとき。
    assert server.uploads == []


def test_an_asset_in_the_trash_is_not_invalidated(world):
    """ゴミ箱に在るのは「無い」の証明ではない。無効化すると二重に上がる."""
    server, rechecker, ctx, destination_id, db = world
    server.trashed.add("asset-1")

    rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert row["remote_is_trashed"] == 1
    assert row["invalidated_at"] is None
```

**同じ Step で、CAS の作法が無効化にも効くことを固定する。**

```python
def test_a_row_that_moved_during_the_check_is_not_invalidated_either(world):
    """**照合したときの行にしか書かない**（§9.10）。無効化も同じ条件で守る.

    照合の最中に他の書き手が `remote_asset_id` を動かしていたら、こちらの
    「消えていた」は古い観測である。それで無効化すると、在る資産の記録を
    未送信へ戻して二重に上げる。
    """
    server, rechecker, ctx, destination_id, db = world
    server.assets.clear()
    original = server.route

    def hooked(method, path, body, headers):
        result = original(method, path, body, headers)
        if path == "/api/assets/bulk-upload-check":
            # 照合の応答を返した直後に、別の書き手が行を動かした。
            db.execute("UPDATE upload_record SET remote_asset_id = 'asset-moved'")
        return result

    server.route = hooked

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.checked == 0
    assert row["remote_asset_id"] == "asset-moved"
    assert row["invalidated_at"] is None
```

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_upload_recheck.py -k "invalidated" -v
```

期待: `test_a_vanished_asset_is_invalidated_so_it_returns_to_unsent` が
`assert None is not None` で FAIL。`test_an_asset_in_the_trash_is_not_invalidated` は PASS
（まだ誰も無効化しないので）。

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/db/uploads.py` の `stamp_many` の UPDATE を次にする。

```python
                updated = self._conn.execute(
                    "UPDATE upload_record SET remote_asset_id = ?, remote_is_trashed = ?,"
                    " remote_checked_at = ?, updated_at = ?,"
                    # **消えていたら、その場で無効化する**（§9.10）。無効化された
                    # 記録は「この宛先の有効な記録」ではなくなるので、メディアは
                    # 通常の「まだ送っていない」へ戻る。**観測と無効化を別の取引に
                    # 分けない** —— 分けると「消えたと記録したが未送信に戻って
                    # いない」中途半端な状態が残る。
                    #
                    # **`COALESCE` で書く。** 在る側には NULL を渡すので、既存の
                    # 値をそのまま残す（消し戻さない）。
                    " invalidated_at = COALESCE(invalidated_at, ?),"
                    " invalidated_reason = COALESCE(invalidated_reason, ?)"
                    " WHERE id = ? AND state = 'complete'"
                    "   AND remote_asset_id IS ? AND remote_checked_at IS ?",
                    (
                        stamp.asset_id,
                        stamp.is_trashed,
                        checked_at,
                        now_iso(),
                        # **消滅の判定は呼び出し側で済んでいる。** `Stamp` に旗を
                        # 足さず、`asset_id` が無いことをそのまま条件にする。
                        checked_at if stamp.asset_id is None else None,
                        "remote_missing" if stamp.asset_id is None else None,
                        stamp.record_id,
                        stamp.expect_asset_id,
                        stamp.expect_checked_at,
                    ),
                )
```

`stamp_many` の docstring の次の一文を差し替える。

```
        **照合したときの行にしか書かない。** `complete` は終端ではない: 消滅と
        判定された行を利用者が requeue でき、送り直しが済めばまた `complete` に
        戻る。
```

を

```
        **照合したときの行にしか書かない。** 観測と現在の姿がずれていれば、
        こちらの結果は古い。id と現在の状態だけを条件にすると、他の照合が書いた
        新しい `remote_asset_id` を古い観測（消滅＝NULL）で消す。
```

に。

`app/src/mediaferry/jobs/recheck.py` の警告を差し替える。

```python
        for row in vanished:
            if row["id"] not in written:
                continue
            # **無効化して「まだ送っていない」へ戻す。** 送るのは利用者の明示操作。
            ctx.emit(
                "warning",
                "リモートに存在しないので、まだ送っていないものに戻した",
                {"upload_record_id": row["id"]},
            )
```

モジュールの docstring からも古い説明を落とす。

```python
"""送信済みレコードの状態を確かめ直す（§9.10「ゴミ箱と消滅の追跡」）.

`remote_is_trashed` は `checking` 時点のスナップショットにすぎない。ゴミ箱の
保持期限を過ぎて資産が消えても「送信済み」のまま残るので、宛先ごとの明示操作で
照合し直す。

**消えていた資産の記録は無効化する。** 無効化された記録は「この宛先の有効な
記録」ではなくなるので、そのメディアは通常の「まだ送っていない」へ戻る。
**この場では送らない** —— 送るのは利用者が通常経路で選んだときだけ。
"""
```

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest app/tests/test_upload_recheck.py -v
```

期待: 全部 PASS。

- [ ] **Step 5: 変異試験**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_upload_recheck.py -v
```

1 つずつ壊して、落ちるテストを確認してから戻す。

| 壊し方 | 落ちるはず |
| --- | --- |
| `checked_at if stamp.asset_id is None else None` を `checked_at` にする | `test_an_asset_in_the_trash_is_not_invalidated` |
| `"remote_missing" if ... else None` を `None` にする | `test_a_vanished_asset_is_invalidated_so_it_returns_to_unsent` |
| `invalidated_at` の項を UPDATE から外す | 同上 |
| `WHERE` から `AND remote_asset_id IS ?` を外す | `test_a_row_that_moved_during_the_check_is_not_invalidated_either` |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/uploads.py app/src/mediaferry/jobs/recheck.py app/tests/test_upload_recheck.py
git commit -m "$(cat <<'MSG'
feat(recheck): 消えた資産の記録を、その場で無効化する

送り直し専用の状態遷移を持たない形にする。無効化された記録は「この宛先の
有効な記録」ではなくなるので、そのメディアは `GET /media?status=unsent` の
定義にそのまま当てはまり、通常の「まだ送っていない」へ戻る。

観測と無効化は同じトランザクションで書く。分けると「消えたと記録したが
未送信に戻っていない」中途半端な状態が残る。

ゴミ箱は無効化しない。ゴミ箱に在るのは「無い」の証明ではないので、
無効化すると同じ資産を二重に上げることになる。
MSG
)"
```

---

### Task 2A: 表制約の `UNIQUE` を、部分 UNIQUE 索引へ置き換える

**この節は実行中に足した。** Task 2 の実装で、設計の安全性の主張
（「UNIQUE 制約は無い」）が誤りだと分かったため。`upload_record` には `0004` の**表制約**

```sql
UNIQUE (destination_id, target_epoch, media_file_id)
```

が在り、**無効化された行の隣に新しい行を作れない**（`sqlite3.IntegrityError`）。
Task 2 / 3 / 4 / 8 が全部これに当たる。詳細は設計の §2.3。

**ファイル:**
- 作成: `app/src/mediaferry/db/migrations/0027_live_identity_is_unique.sql`
- テスト: `app/tests/test_schema_uploads.py`

**インタフェース:**
- 提供: 同じ `(destination_id, target_epoch, media_file_id)` に、**無効化された行と
  有効な行が共存できる**。有効な行は依然として高々 1 つ

- [ ] **Step 1: 落ちるテストを書く**

`app/tests/test_schema_uploads.py` に足す。`a_destination` / `an_upload` / `a_media_file` は
このファイルが既に持っている。

```python
def test_an_invalidated_row_can_sit_beside_a_live_one(db):
    """**守る不変条件は「有効な記録は 1 組につき高々 1 つ」。**

    消滅を無効化して送り直すと、同じ (宛先, epoch, メディア) に行が 2 つ並ぶ。
    無効化された方は監査履歴で、有効なのは新しい方だけ。
    """
    dest = a_destination(db)
    media = a_media_file(db)
    old = an_upload(db, dest, media, state="complete", destination_revision_id=dest[1])
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'remote_missing'"
        " WHERE id = ?",
        (now_iso(), old),
    )

    fresh = an_upload(db, dest, media)

    live = db.execute(
        "SELECT id FROM upload_record WHERE invalidated_at IS NULL"
    ).fetchall()
    assert [row["id"] for row in live] == [fresh]


def test_two_live_rows_for_the_same_triple_are_refused(db):
    """**有効な行が 2 つは許さない。** 片方が無効化されていることが条件."""
    dest = a_destination(db)
    media = a_media_file(db)
    an_upload(db, dest, media)

    with pytest.raises(sqlite3.IntegrityError):
        an_upload(db, dest, media)


def test_the_upload_record_schema_survives_the_rebuild(db):
    """**作り直しで guard を落としていないこと。**

    表を作り直すと trigger も索引も一緒に消える。**明示の一覧と突き合わせる**
    ——「たぶん全部作り直した」では、消えた guard に気づけない。
    """
    found = {
        (row["type"], row["name"])
        for row in db.execute(
            "SELECT type, name FROM sqlite_master WHERE tbl_name = 'upload_record'"
            "   AND type IN ('trigger', 'index') AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert found == {
        ("trigger", "upload_record_epoch_must_exist"),
        ("trigger", "upload_record_identity_is_immutable"),
        ("trigger", "upload_record_selection_rule_immutable"),
        ("trigger", "upload_record_first_check_immutable"),
        ("trigger", "upload_record_stack_shape_insert"),
        ("trigger", "upload_record_stack_shape_update"),
        ("trigger", "upload_record_stacked_needs_its_asset"),
        ("trigger", "upload_record_stacked_needs_its_asset_insert"),
        ("index", "upload_record_by_media"),
        ("index", "upload_record_claimable"),
        ("index", "upload_record_unstacked"),
        ("index", "upload_record_live_pair"),
        ("index", "upload_record_live_identity"),
    }
```

**import を足す**（無ければ）: `import sqlite3`、`pytest`、`from mediaferry.clock import now_iso`。

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_schema_uploads.py -k "invalidated_row_can_sit or survives_the_rebuild" -v
```

期待: 1 つ目が `sqlite3.IntegrityError: UNIQUE constraint failed` で FAIL。
3 つ目が `upload_record_live_identity` が無いので FAIL。
2 つ目は**いまも通る**（表制約が効いているので）。**通ったままでよい** ——
移行の後も同じ結論であることを固定するのがこのテストの目的。

- [ ] **Step 3: 移行を書く**

`app/src/mediaferry/db/migrations/0027_live_identity_is_unique.sql` を作る。
**`0026_media_file_container.sql` が同じ 12 手順の前例**なので、その形にそろえる。

1. 先頭行に `-- mediaferry:foreign-keys-off` を置く
2. なぜ作り直すのかを日本語のコメントで書く（表制約は後から落とせない、守る不変条件は
   「有効な記録が 1 組につき高々 1 つ」であること）
3. `CREATE TABLE upload_record_new (...)` —— **`0004` の定義に `0009` の
   `remote_datetime_original` と `0015` の `stack_state` / `remote_stack_id` /
   `stack_reason` を足した、現在の姿**。**表制約の `UNIQUE (...)` だけを外す**。
   CHECK 4 個と FK 5 本（`destination_id` / `media_file_id` / `merge_group_id` /
   `claim_job_id` / 複合 `(destination_id, target_epoch, destination_revision_id)`）は
   そのまま写す
4. 列を明示した `INSERT INTO upload_record_new (...) SELECT ... FROM upload_record`
5. `DROP TABLE upload_record;` → `ALTER TABLE upload_record_new RENAME TO upload_record;`
6. **trigger 8 個と索引 4 個を作り直す**（`0004` / `0015` / `0016` / `0019` から
   一字一句写す。部分索引は述語が一致しないと使われないので、特に写し間違えない）
7. 新しい部分 UNIQUE 索引を足す

```sql
CREATE UNIQUE INDEX upload_record_live_identity
    ON upload_record (destination_id, target_epoch, media_file_id)
    WHERE invalidated_at IS NULL;
```

**`DROP TABLE` の前に trigger を作らない**（copy の最中に発火する）。順序は
`0026` と同じく「作る → 写す → 落とす → 改名 → 索引と trigger」。

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest app/tests/test_schema_uploads.py -v
uv run pytest
```

- [ ] **Step 5: 変異試験**

| 壊し方 | 落ちるはず |
| --- | --- |
| 新しい索引から `WHERE invalidated_at IS NULL` を外す | `test_an_invalidated_row_can_sit_beside_a_live_one` |
| 新しい索引を `UNIQUE` でなくする | `test_two_live_rows_for_the_same_triple_are_refused` |
| trigger を 1 つ作り忘れる | `test_the_upload_record_schema_survives_the_rebuild` |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/migrations/0027_live_identity_is_unique.sql app/tests/test_schema_uploads.py docs/history/phase15-design.md docs/history/phase15-plan.md
git commit -m "$(cat <<'MSG'
feat(db): 同一性の UNIQUE を、有効な行だけの部分索引にする

消滅した送信記録を無効化して通常経路へ戻す形にすると、同じ
(宛先, epoch, メディア) に無効化された行と新しい行が並ぶ。`0004` の
表制約 UNIQUE はそれを許さないので、送り直しが IntegrityError になる。

守りたい不変条件は「**有効な**記録は 1 組につき高々 1 つ」であって
「行は 1 つ」ではない。条件を `WHERE invalidated_at IS NULL` に付け替える。

表制約は後から落とせないので、`0026` と同じ 12 手順でテーブルを作り直す。
作り直すと trigger も索引も消えるため、`sqlite_master` を明示の一覧と
突き合わせるテストを置いて、guard の消し忘れを落とす。

行を使い回す案は採らない。first_check_result が immutable なので origin の
判定をやり直せず、「なぜ最初に送信を許可したか」が失われる。行を消す案も
採らない。相手が誤って accept を返しただけで送信記録が消え、再確認が
破壊的な操作になる。
MSG
)"
```

---

### Task 2: `POST /uploads` が無効化された記録を跨ぐ

**ファイル:**
- 変更: `app/src/mediaferry/db/uploads.py`（`_pair` の既存レコード探索）
- テスト: `app/tests/test_upload_pairs.py`

**インタフェース:**
- 消費: Task 1 の `invalidated_reason = 'remote_missing'`
- 提供: 無効化済みの行しか無い `(宛先, epoch, メディア)` に、`create_pairs` が
  **`result = "created"` の新しい行**を作る

- [ ] **Step 1: 落ちるテストを書く**

`app/tests/test_upload_pairs.py` の末尾に足す。

```python
def test_an_invalidated_record_does_not_block_a_new_one(db, uploads, destinations, profile):
    """**無効化された記録は無いものとして扱う。**

    再確認が消滅を無効化すると（§9.10）、そのメディアは「まだ送っていない」に
    戻る。ここで古い行を拾って断ると、画面には「まだ送っていない」と出るのに
    送れない。`design.md` §10 の遷移表が既に「再利用しない」と書いている。
    """
    destination_id = a_destination(destinations)
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    first = uploads.create_pairs([media_id], [destination_id])[0]
    db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = NULL,"
        " remote_checked_at = ?, invalidated_at = ?, invalidated_reason = 'remote_missing'"
        " WHERE id = ?",
        (now_iso(), now_iso(), first.record_id),
    )

    again = uploads.create_pairs([media_id], [destination_id])[0]

    assert again.result == "created"
    assert again.record_id != first.record_id
    live = db.execute(
        "SELECT id FROM upload_record WHERE media_file_id = ? AND invalidated_at IS NULL",
        (media_id,),
    ).fetchall()
    assert [row["id"] for row in live] == [again.record_id]
```

**import を足す**（ファイル先頭）:

```python
from mediaferry.clock import now_iso
```

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_upload_pairs.py::test_an_invalidated_record_does_not_block_a_new_one -v
```

期待: FAIL。`again.result` は `"rejected"`（`_existing` が「無効化されている: remote_missing」を返す）。

- [ ] **Step 3: 最小実装**

`_pair` の既存レコード探索に条件を足す。

```python
        existing = self._conn.execute(
            # **無効化された行は無いものとして扱う**（§10 の遷移表「再利用しない」）。
            # 拾うと `_existing` が断り、「まだ送っていない」と出ているのに送れない。
            "SELECT * FROM upload_record WHERE destination_id = ? AND target_epoch = ?"
            "   AND media_file_id = ? AND invalidated_at IS NULL",
            (destination_id, revision["target_epoch"], media["id"]),
        ).fetchone()
```

`_existing` の先頭にある `invalidated_at` の分岐は**残す**（この経路では到達しないが、
他の呼び出しが増えたときの fail-closed）。docstring に一行足す。

```python
    def _existing(self, media: sqlite3.Row, destination_id: str, row: sqlite3.Row) -> PairResult:
        """§10「既存レコードがある場合の遷移」.

        **`invalidated_at` の分岐は保険。** `_pair` は無効化された行を引かないので
        構造的に到達しない。**それでも残す**（無効化された行を再利用しない、という
        判断をコードから消さない）。
        """
```

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest app/tests/test_upload_pairs.py -v
```

期待: 全部 PASS。既存の「無効化されている」を確かめるテストがあれば、`_existing` を
直接呼ぶ形に直すか、到達しなくなった旨を書いて落とす。

- [ ] **Step 5: 変異試験**

`_pair` から `AND invalidated_at IS NULL` を外す →
`test_an_invalidated_record_does_not_block_a_new_one` が落ちること。

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/uploads.py app/tests/test_upload_pairs.py
git commit -m "$(cat <<'MSG'
fix(uploads): POST /uploads が無効化された記録を跨いで新しい行を作る

再確認が消滅を無効化すると、そのメディアは「まだ送っていない」に戻る。
だが `_pair` は `invalidated_at` を見ずに既存行を拾っていたので、
`_existing` が「無効化されている」で断っていた。画面には「まだ送って
いない」と出るのに送れない、という食い違いになる。

design.md §10 の遷移表は既に「無効化された記録は再利用しない」と書いて
いる。書いてあるとおりにする修正。
MSG
)"
```

---

### Task 3: `record_for` が有効な行を返す

**ファイル:**
- 変更: `app/src/mediaferry/db/uploads.py`（`record_for`）
- テスト: `app/tests/test_stack_repository.py`

**インタフェース:**
- 消費: Task 2（同じ組に行が 2 つ並ぶようになったこと）
- 提供: `record_for(destination_id, target_epoch, media_file_id)` は
  **無効化されていない行だけ**を返す。無ければ `None`

- [ ] **Step 1: 落ちるテストを書く**

`app/tests/test_stack_repository.py` に足す。

```python
def test_record_for_returns_the_live_row_not_the_invalidated_one(world, db):
    """**送り直した後は同じ組に行が 2 つ並ぶ。**

    `record_for` は `ORDER BY` を持たないので、無効化を除かないと古い方を
    返す。第 2 パスはこれで相方を引くので、返し間違えると「相方が無効化
    済み」と読んで、送り直しても永久に組めない（§9.11）。
    """
    repo, _, destination_id, _, records = world
    old = records["CR2"]
    media_id = db.execute(
        "SELECT media_file_id FROM upload_record WHERE id = ?", (old,)
    ).fetchone()["media_file_id"]
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'remote_missing'"
        " WHERE id = ?",
        (now_iso(), old),
    )
    fresh = an_upload(
        db,
        (destination_id, None, None),
        media_id,
        state="complete",
        origin="created_by_us",
        remote_asset_id="asset-CR2-again",
    )

    found = repo.record_for(destination_id, EPOCH, media_id)

    assert found is not None
    assert found["id"] == fresh


def test_record_for_returns_none_when_only_an_invalidated_row_is_left(world, db):
    """消えたまま送り直していない相方は「この宛先へ送っていない」と同じ扱い."""
    repo, _, destination_id, _, records = world
    old = records["CR2"]
    media_id = db.execute(
        "SELECT media_file_id FROM upload_record WHERE id = ?", (old,)
    ).fetchone()["media_file_id"]
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'remote_missing'"
        " WHERE id = ?",
        (now_iso(), old),
    )

    assert repo.record_for(destination_id, EPOCH, media_id) is None
```

`an_upload` は `dest` を `(dest_id, rev_id, _)` の 3 要素で分解するので、
`destination_revision_id` を渡さない呼び出しでは `(destination_id, None, None)` を渡す。

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_stack_repository.py -k record_for -v
```

期待: 1 つ目が FAIL（古い方の id が返る）、2 つ目が FAIL（行が返る）。

- [ ] **Step 3: 最小実装**

```python
    def record_for(
        self, destination_id: str, target_epoch: int, media_file_id: str
    ) -> sqlite3.Row | None:
        """**現行 epoch の有効なレコードだけ**を返す.

        旧 epoch は別ライブラリの履歴。**無効化された行も返さない** ——
        消滅を無効化して送り直すと、同じ組に行が 2 つ並ぶ。`ORDER BY` が
        無いので、除かないと古い方を返し、第 2 パスが「相方が無効化済み」と
        読んで永久に組めなくなる。
        """
        return self._conn.execute(
            "SELECT * FROM upload_record"
            " WHERE destination_id = ? AND target_epoch = ? AND media_file_id = ?"
            "   AND invalidated_at IS NULL",
            (destination_id, target_epoch, media_file_id),
        ).fetchone()
```

`jobs/stacker.py` の `Candidate.invalidated` は**残す**。**コードは変えず**、
`_candidate_of` の `invalidated=record["invalidated_at"] is not None,` の行の直前に
コメントだけを足す。

```python
            # **保険。** `unstacked_batch` も `record_for` も無効化を除くので、
            # ここへ来る行は必ず有効。それでも「無効化された行とは組まない」と
            # いう判断をコードから消さない。
```

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest app/tests/test_stack_repository.py app/tests/test_stacker.py -v
```

- [ ] **Step 5: 変異試験**

`record_for` から `AND invalidated_at IS NULL` を外す → 上の 2 件が落ちること。

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/uploads.py app/src/mediaferry/jobs/stacker.py app/tests/test_stack_repository.py
git commit -m "$(cat <<'MSG'
fix(uploads): record_for が無効化された行を返さないようにする

送り直しで同じ (宛先, epoch, メディア) に行が 2 つ並ぶようになった。
`record_for` は `ORDER BY` を持たないので、除かないと古い無効化済みを
返す。第 2 パスはこれで組の相方を引くので、返し間違えると
「相方が無効化済み」と読み、送り直しても永久に組めない。

`Candidate.invalidated` は到達不能になるが残す。「無効化された行とは
組まない」という fail-closed をコードから消さないため。
MSG
)"
```

---

### Task 4: 通常経路へ戻ることを、一覧と送信で確かめる

**ファイル:**
- テスト: `app/tests/test_api_media.py`
- テスト: `app/tests/test_uploader.py`

**インタフェース:**
- 消費: Task 1〜3
- 提供: なし（通しの確認）

- [ ] **Step 1: 落ちるテストを 2 つ書く**

`app/tests/test_api_media.py`（このファイルは `db` / `client` / `canon_pair` /
`a_destination` / `an_upload` / `now_iso` を既に使っている）:

```python
def test_a_record_invalidated_by_recheck_returns_to_unsent(client, db, canon_pair):
    """再確認が無効化した記録は「この宛先の有効な記録」ではなくなる（§9.10）.

    `_status_clause` の「まだ送っていない」＝「この宛先の**有効な**記録がまだ
    無く、いま送れるもの」に、無効化がそのまま噛み合う。
    """
    destination = a_destination(db, name="recheck-unsent")
    an_upload(
        db,
        destination,
        canon_pair.media_ids["JPG"],
        state="complete",
        destination_revision_id=destination[1],
        remote_checked_at=now_iso(),
        invalidated_at=now_iso(),
        invalidated_reason="remote_missing",
    )
    db.commit()

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()

    assert canon_pair.media_ids["JPG"] in [item["id"] for item in body["media"]]
```

`app/tests/test_uploader.py`（**先頭に `from mediaferry.clock import now_iso` を足す**。
`JobStore` は既に import 済み）:

```python
def test_a_resent_record_is_created_by_us_again(world, db):
    """**送り直しは新しい記録から始まる**（§9.10）.

    古い記録を使い回すと `first_check_result` が前回の観測のまま残り、自作の
    資産が `pre_existing` として承認待ちに積まれる。無効化して通常経路へ戻す
    形なら、`origin` の判定も最初からやり直しになる。
    """
    server, uploader, ctx, uploads, _, destination_id, media_id = world
    uploader.run(ctx, destination_id)
    first = record_of(db)
    assert first["origin"] == "created_by_us"

    # 再確認が「リモートに無い」と判定して無効化した状態（§9.10）。
    server.assets.clear()
    server.datetimes.clear()
    db.execute(
        "UPDATE upload_record SET remote_asset_id = NULL, remote_checked_at = ?,"
        " invalidated_at = ?, invalidated_reason = 'remote_missing' WHERE id = ?",
        (now_iso(), now_iso(), first["id"]),
    )
    # 1 本目のジョブを畳んでから、送り直しのジョブを積む。
    db.execute("UPDATE job SET status = 'succeeded'")

    again = uploads.create_pairs([media_id], [destination_id])[0]
    assert again.result == "created"
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    uploader.run(store.claim_next(), destination_id)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (again.record_id,)).fetchone()
    assert row["state"] == "complete"
    assert row["origin"] == "created_by_us"
    assert row["first_check_result"] == "accept"
    # 古い記録は監査履歴として残る（消さない）。
    assert db.execute("SELECT count(*) AS n FROM upload_record").fetchone()["n"] == 2
```

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_api_media.py -k invalidated_by_recheck -v
uv run pytest app/tests/test_uploader.py -k created_by_us_again -v
```

期待: 1 つ目は **Task 1〜3 の後なら通る**（`_status_clause` は元から
`invalidated_at IS NULL` を持っている）。**通ったらそのまま記録して進む** ——
このテストの目的は新しい振る舞いを足すことではなく、`_status_clause` の定義と
無効化が噛み合っていることを固定して、将来どちらかを書き換えたときに気づける
ようにすることである。

2 つ目は `again.result == "created"` の手前で FAIL する（Task 2 を戻すと
`"rejected"` になる）。**Task 2 の変更を一時的に戻して落ちることを確認し、
戻したら元へ戻す。**

- [ ] **Step 3: 実装は不要**（Task 1〜3 で足りている）

- [ ] **Step 4: 全体が通ることを確認する**

```bash
uv run pytest
```

- [ ] **Step 5: コミット**

```bash
git add app/tests/test_api_media.py app/tests/test_uploader.py
git commit -m "$(cat <<'MSG'
test: 無効化された記録が通常経路へ戻ることを、一覧と送信で固定する

「まだ送っていない」の定義（この宛先の有効な記録がまだ無く、いま送れる
もの）に、再確認の無効化がそのまま噛み合うことを確かめる。

送り直しが新しい記録から始まることも押さえる。古い記録を使い回すと
first_check_result が前回の観測のまま残り、自作の資産が pre_existing と
して承認待ちに積まれてしまう。
MSG
)"
```

---

### Task 5: `ImmichClient.stacks()` と、fake の経路

**ファイル:**
- 変更: `app/src/mediaferry/adapters/immich.py`（`stacks()` を追加）
- 変更: `app/tests/fake_immich.py`（`GET /api/stacks` の経路を追加）
- テスト: `app/tests/test_adapter_immich.py`

**インタフェース:**
- 提供: `ImmichClient.stacks() -> list[RemoteStack]`。`RemoteStack` は
  `stack_id: str` / `primary_asset_id: str` / `asset_ids: tuple[str, ...]`

- [ ] **Step 1: 落ちるテストを書く**

`app/tests/test_adapter_immich.py` に足す（このファイルの既存の client 生成の形に合わせる）。

```python
def test_stacks_lists_every_stack(immich):
    immich.stacks["stack-1"] = {"primary": "asset-a", "assets": ["asset-a", "asset-b"]}
    immich.stacks["stack-2"] = {"primary": "asset-c", "assets": ["asset-c", "asset-d"]}
    with ImmichClient(immich.url, API_KEY) as client:
        found = client.stacks()

    assert {stack.stack_id for stack in found} == {"stack-1", "stack-2"}
    assert {stack.stack_id: set(stack.asset_ids) for stack in found}["stack-1"] == {
        "asset-a",
        "asset-b",
    }


def test_a_broken_stack_list_is_a_protocol_error(immich):
    """**壊れた応答を DB へ確定させない。** 黙って「スタックが無い」と読むと、
    組み直しの判断が全部ひっくり返る（在る組を解けていると読む）。"""
    immich.stacks["stack-1"] = {"primary": "asset-a", "assets": ["asset-a", "asset-b"]}
    immich.stack_response_without_assets = True
    with ImmichClient(immich.url, API_KEY) as client, pytest.raises(ImmichProtocolError):
        client.stacks()
```

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_adapter_immich.py -k stacks -v
```

期待: `AttributeError: 'ImmichClient' object has no attribute 'stacks'` で FAIL。

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/adapters/immich.py`、`stack_by_primary` の**すぐ上**に足す。

```python
    def stacks(self) -> list[RemoteStack]:
        """相手が持っているスタックを全部読む（再確認の照合）.

        **絞り込まない。** こちらが持つ `remote_stack_id` の現存とメンバー集合を
        まとめて照合するので、1 要求で足りる。**件数の上限は置かない** ——
        打ち切ると「照合した」が嘘になる（`records_for_recheck` と同じ）。

        **形が違えば protocol error にする**（`_stack_from`）。黙って
        「スタックが無い」と読むと、在る組を解けていると判断して作り直す。
        """
        response = self._request("GET", "/api/stacks")
        return [
            self._stack_from(item, "GET /api/stacks")
            for item in _as_array(response, "GET /api/stacks")
        ]
```

`app/tests/fake_immich.py` の `route` に、`POST /api/stacks` の**次**、
`GET /api/stacks?` の**手前**に足す。

```python
        if method == "GET" and path == "/api/stacks":
            # 絞り込み無しの一覧（再確認の照合が使う）。
            return 200, [self._stack_view(stack_id) for stack_id in self.stacks]
```

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest app/tests/test_adapter_immich.py -v
```

- [ ] **Step 5: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/adapters/immich.py app/tests/fake_immich.py app/tests/test_adapter_immich.py
git commit -m "$(cat <<'MSG'
feat(immich): スタックを絞り込み無しで一覧する stacks() を足す

再確認がスタックの現存とメンバー集合を照合するのに使う。1 要求で
両方が見られるので、組ごとに引き直さない。応答の形は `_stack_from` が
これまでどおり厳密に検める —— 黙って「スタックが無い」と読むと、
在る組を解けていると判断して作り直すことになる。
MSG
)"
```

---

### Task 6: 再確認が、崩れた組を未評価へ戻す

**ファイル:**
- 変更: `app/src/mediaferry/db/uploads.py`（`stacked_groups` / `reopen_stacks` を追加）
- 変更: `app/src/mediaferry/jobs/recheck.py`（`_reconcile_stacks` と `RecheckOutcome.unstacked`）
- 変更: `app/src/mediaferry/api/jobs_wiring.py`（結果の 1 行）
- テスト: `app/tests/test_upload_recheck.py`

**インタフェース:**
- 消費: Task 5 の `ImmichClient.stacks()`
- 提供:
  - `UploadRepository.stacked_groups(destination_id, target_epoch) -> dict[str, frozenset[str]]`
  - `UploadRepository.reopen_stacks(ctx, destination_id, target_epoch, stack_ids) -> int`
  - `RecheckOutcome(checked, trashed, vanished, restored, unstacked)`

- [ ] **Step 1: 落ちるテストを書く**

`app/tests/test_upload_recheck.py` に足す。**照合は組の単位なので、組が 1 つ要る。**
先に定数とヘルパを置く。

ファイル先頭に足す import と定数:

```python
from mediaferry.clock import now_iso
from mediaferry.ids import new_id

PAYLOAD_2 = b"video-bytes-2"
# `upload_record.checksum` は sha1 の 16 進。adapter が base64 へ直して送るので、
# fake の `assets` には base64 の方を鍵として入れる。
SHA1_2 = hashlib.sha1(PAYLOAD_2, usedforsecurity=False).hexdigest()
CHECKSUM_2 = base64.b64encode(hashlib.sha1(PAYLOAD_2, usedforsecurity=False).digest()).decode()
```

ヘルパ:

```python
def a_stacked_pair(world_tuple):
    """`world` の 1 件に相方を足して、両方を同じ組の `stacked` にする.

    資産はどちらも相手に在る状態にする（消滅とスタックの照合を混ぜない）。
    """
    server, _, _, destination_id, db = world_tuple
    profile = ProfileRegistry(db).current("dji-osmo")
    second = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/B.MP4",
        sha1=SHA1_2,
    )
    revision_id = db.execute(
        "SELECT destination_revision_id FROM upload_record"
    ).fetchone()["destination_revision_id"]
    db.execute(
        "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
        " selection_rule, origin, checksum, remote_asset_id, remote_is_trashed,"
        " destination_revision_id, created_at, updated_at)"
        " VALUES (?, ?, 1, ?, 'complete', 'default', 'created_by_us', ?, 'asset-2', 0, ?, ?, ?)",
        (new_id(), destination_id, second, SHA1_2, revision_id, now_iso(), now_iso()),
    )
    server.assets[CHECKSUM_2] = "asset-2"
    # `0015` / `0016` の trigger が形を守っているので、3 列を一緒に書く。
    db.execute("UPDATE upload_record SET stack_state = 'stacked', remote_stack_id = 'stack-1'")
```

テスト本体:

```python
def test_a_stack_that_is_gone_on_the_server_is_reopened(world):
    """**解けた組を `stacked` のまま残さない。** 設定 › 送り先の「N 組」が嘘になる."""
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    # 相手にはスタックが無い（利用者が解除した）。資産はどちらも在る。

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 1
    rows = db.execute("SELECT stack_state, remote_stack_id FROM upload_record").fetchall()
    assert [row["stack_state"] for row in rows] == [None, None]
    assert [row["remote_stack_id"] for row in rows] == [None, None]


def test_a_stack_whose_members_changed_is_reopened(world):
    """集合が一致しない組も戻す。§9.11 が「触らない」と決めている状態へ落とすため."""
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    server.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "someone-else"]}

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 1
    assert (
        db.execute("SELECT count(*) AS n FROM upload_record WHERE stack_state IS NULL").fetchone()[
            "n"
        ]
        == 2
    )


def test_a_stack_that_still_matches_is_not_touched(world):
    """一致している組には触らない."""
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    server.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "asset-2"]}

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 0
    rows = db.execute("SELECT stack_state, remote_stack_id FROM upload_record").fetchall()
    assert all(row["stack_state"] == "stacked" for row in rows)
    assert all(row["remote_stack_id"] == "stack-1" for row in rows)


def test_no_stacked_rows_means_no_request_for_stacks(world):
    """**空振りの要求を出さない。** `stacked` が 0 件なら相手に聞かない."""
    server, rechecker, ctx, destination_id, db = world

    rechecker.run(ctx, destination_id)

    assert ("GET", "/api/stacks") not in server.requests
```

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_upload_recheck.py -k "stack" -v
```

期待: `AttributeError: 'RecheckOutcome' object has no attribute 'unstacked'` で FAIL。

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/db/uploads.py` に 2 つ足す（`unstacked_batch` の隣）。

```python
    def stacked_groups(self, destination_id: str, target_epoch: int) -> dict[str, frozenset[str]]:
        """組んだと記録している組を、`remote_stack_id` → 資産 ID の集合で返す.

        再確認の照合が使う。**無効化された行は数えない。** `stacked` の行は
        `0015` と `0016` の trigger が `remote_stack_id` と `remote_asset_id` の
        実在を強制しているので、どちらも NULL にならない。

        **`target_epoch` を必ず絞る。** 旧 epoch は別ライブラリの履歴で、
        現行の資格情報で読んだスタック一覧とは突き合わせられない。
        """
        groups: dict[str, set[str]] = {}
        for row in self._conn.execute(
            "SELECT remote_stack_id, remote_asset_id FROM upload_record"
            " WHERE destination_id = ? AND target_epoch = ? AND state = 'complete'"
            "   AND invalidated_at IS NULL AND stack_state = 'stacked'",
            (destination_id, target_epoch),
        ):
            groups.setdefault(row["remote_stack_id"], set()).add(row["remote_asset_id"])
        return {stack_id: frozenset(assets) for stack_id, assets in groups.items()}

    def reopen_stacks(
        self,
        ctx: JobContext,
        destination_id: str,
        target_epoch: int,
        stack_ids: Sequence[str],
    ) -> int:
        """崩れた組を未評価へ戻す. **戻した組の数**を返す.

        `_reopen_stack_of` と同じ形の CAS を、**1 つのトランザクション**で当てる
        （`assert_lease` も同じ取引に入れる）。**組ごとに数える** —— 組は互いに
        独立なので、片方が動いても他方の照合結果は古くならない。
        """
        reopened = 0
        with immediate(self._conn):
            ctx.assert_lease()
            for stack_id in stack_ids:
                updated = self._conn.execute(
                    "UPDATE upload_record SET stack_state = NULL, remote_stack_id = NULL,"
                    " stack_reason = NULL, updated_at = ?"
                    " WHERE destination_id = ? AND target_epoch = ? AND remote_stack_id = ?"
                    "   AND stack_state = 'stacked' AND invalidated_at IS NULL",
                    (now_iso(), destination_id, target_epoch, stack_id),
                )
                reopened += updated.rowcount > 0
        return reopened
```

`Sequence` の import が無ければ足す（`from collections.abc import Sequence`）。

`app/src/mediaferry/jobs/recheck.py`:

```python
@dataclass(frozen=True)
class RecheckOutcome:
    checked: int
    trashed: int
    vanished: int
    restored: int
    # 相手側で解けていた／崩れていたので未評価へ戻した組の数（§9.11）。
    unstacked: int = 0
```

`run` の `except LeaseLost:` の**直前**（`stamp_many` の後）に足す。

```python
            unstacked = self._reconcile_stacks(ctx, revision)
        except LeaseLost:
            return self._cancelled_or_raise(ctx)
```

`return RecheckOutcome(...)` に `unstacked=unstacked` を足す。**途中で降りる
`return RecheckOutcome(0, 0, 0, 0)` は既定値のまま**（引数を増やさない）。

新しいメソッド:

```python
    def _reconcile_stacks(self, ctx: JobContext, revision: sqlite3.Row) -> int:
        """スタックの現存とメンバー集合を照合し、崩れた組を未評価へ戻す（§9.11）.

        **資産の照合の後に走らせる。** 消滅した資産は `stamp_many` の
        `_reopen_stack_of` で既に組を開いており、開いた行は `stack_state` が
        NULL なのでここの対象から外れる。逆順だと同じ組を 2 度開く。

        **「無い」と「集合が違う」を 1 つの条件で見る。** どちらも
        「こちらが記録している組は現在の姿ではない」であり、戻した先の第 2 パスが
        相手を読み直して、作り直すか見送るかを決める（§9.11 の表）。
        """
        destination_id = revision["destination_id"]
        epoch = revision["target_epoch"]
        groups = self._uploads.stacked_groups(destination_id, epoch)
        if not groups:
            # **空振りの要求を出さない。**
            return 0
        # 相手へ触る前に、キャンセルとリースの両方を見る（batch の照合と同じ）。
        if ctx.cancelled():
            return 0
        ctx.assert_lease()
        ctx.heartbeat()
        with self._open_client(revision) as client:
            live = {
                stack.stack_id: frozenset(stack.asset_ids)
                for stack in with_lease_pulse(ctx, client.stacks)
            }
        # **照合の最中にキャンセルされていたら、結果を書かずに降りる。**
        if ctx.cancelled():
            return 0
        broken = [
            stack_id for stack_id, members in groups.items() if live.get(stack_id) != members
        ]
        if not broken:
            return 0
        return self._uploads.reopen_stacks(ctx, destination_id, epoch, broken)
```

`app/src/mediaferry/api/jobs_wiring.py` の再確認の 1 行に足す。

```python
            ctx.emit(
                "info",
                f"再確認: {outcome.checked} 件 / ゴミ箱 {outcome.trashed} 件"
                f" / 消滅 {outcome.vanished} 件 / 復元 {outcome.restored} 件"
                f" / 組を戻した {outcome.unstacked} 組",
            )
```

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest app/tests/test_upload_recheck.py -v
```

- [ ] **Step 5: キャンセルのテストを足す**

```python
def test_a_cancel_during_the_stack_check_writes_nothing(world):
    """照合の最中にキャンセルされたら、1 組も戻さない.

    「キャンセルした」と表示しながら組を開いていた、という状態を残さない
    （`stamp_many` の後のキャンセル確認と同じ考え方）。
    """
    server, rechecker, ctx, destination_id, db = world
    a_stacked_pair(world)
    original = server.route

    def hooked(method, path, body, headers):
        result = original(method, path, body, headers)
        if path == "/api/stacks":
            # 一覧を返した直後に、利用者がキャンセルを commit した。
            db.execute("UPDATE job SET status = 'cancelling'")
        return result

    server.route = hooked

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.unstacked == 0
    rows = db.execute("SELECT stack_state FROM upload_record").fetchall()
    assert all(row["stack_state"] == "stacked" for row in rows)
```

**`route` に割り込む形は `test_stacker.py` の `on_preflight` と同じ。** 新しい
仕掛けは作らない。

- [ ] **Step 6: 変異試験**

| 壊し方 | 落ちるはず |
| --- | --- |
| `live.get(stack_id) != members` を `stack_id not in live` にする | `test_a_stack_whose_members_changed_is_reopened` |
| `if not groups: return 0` を消す | `test_no_stacked_rows_means_no_request_for_stacks` |
| `_reconcile_stacks` を `stamp_many` の**前**に置く | Task 8 の通しで観測する（ここでは落ちない。**落ちないことを記録に残す**） |
| 2 つ目の `ctx.cancelled()` を消す | キャンセルの試験 |

- [ ] **Step 7: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/uploads.py app/src/mediaferry/jobs/recheck.py app/src/mediaferry/api/jobs_wiring.py app/tests/test_upload_recheck.py
git commit -m "$(cat <<'MSG'
feat(recheck): スタックの現存とメンバー集合も照合する

いままで再確認は資産の有無しか見ていなかったので、Immich 側でスタックが
解除されても気づかなかった。`stack_state = 'stacked'` と、もう存在しない
`remote_stack_id` が残り、設定 › 送り先の「N 組」が嘘になっていた。

`GET /api/stacks` を 1 回叩けば現存と集合の両方が照合できる。「無い」と
「集合が違う」は同じ条件（こちらの記録が現在の姿ではない）なので 1 つに
まとめ、崩れた組は未評価へ戻す。戻した先の第 2 パスが相手を読み直して、
作り直すか見送るかを決める。

資産の照合の後に走らせる。消滅した資産は `_reopen_stack_of` で既に組を
開いているので、逆順だと同じ組を 2 度開く。
MSG
)"
```

---

### Task 7: `test_stacker.py` の `World` に、再確認と送り直しを足す

**ファイル:**
- 変更: `app/tests/test_stacker.py`（`World` にメソッドを 3 つ追加）

**インタフェース:**
- 提供: `World.rechecker()` / `World.make_checkable()` / `World.resend(name, asset_id)`。
  Task 8 と Task 9 の通しテストがこれを使う

- [ ] **Step 1: `World` にメソッドを足す**

`app/tests/test_stacker.py` の `World` に、`stacker()` の隣へ。**import を足す**:

```python
import hashlib

from mediaferry.adapters.immich import ImmichClient, to_base64_checksum
from mediaferry.jobs.recheck import Rechecker
```

```python
    def rechecker(self):
        def open_client(revision):
            return ImmichClient(revision["base_url"], API_KEY)

        return Rechecker(
            self.uploads,
            self.destinations,
            open_client,
            PreflightCache(self.destinations, open_client),
        )

    def make_checkable(self):
        """再確認の対象にする. **`checksum` が無い行は照合しない**（`recheck.py`）.

        `checksum` は sha1 の 16 進で、adapter が base64 へ直して送る
        （`to_base64_checksum`）。fake はその base64 を鍵に持つ。
        """
        for name, record_id in self.records.items():
            digest = hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()
            self.db.execute(
                "UPDATE upload_record SET checksum = ? WHERE id = ?", (digest, record_id)
            )
            self.immich.assets[to_base64_checksum(digest)] = self.assets[name]

    def forget_on_the_server(self, name):
        """相手からその資産を消す（保持期限を過ぎて完全に消えた状態）."""
        digest = hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()
        del self.immich.assets[to_base64_checksum(digest)]

    def resend(self, name, asset_id):
        """無効化された記録のメディアを、通常経路で送り直した姿にする.

        `create_pairs` は無効化された行を跨いで新しい行を作る（§10）。
        **送信そのものは走らせない** —— ここで見たいのは第 2 パスの組み直し
        なので、`complete` まで進んだ姿を直接作る。
        """
        media_id = self.db.execute(
            "SELECT media_file_id FROM upload_record WHERE id = ?", (self.records[name],)
        ).fetchone()["media_file_id"]
        pair = self.uploads.create_pairs([media_id], [self.destination_id])[0]
        assert pair.result == "created", pair.reason
        self.db.execute(
            "UPDATE upload_record SET state = 'complete', origin = 'created_by_us',"
            " remote_asset_id = ?, destination_revision_id = ?, updated_at = ?"
            " WHERE id = ?",
            (
                asset_id,
                self.destinations.current(self.destination_id)["id"],
                now_iso(),
                pair.record_id,
            ),
        )
        self.records[name] = pair.record_id
        self.assets[name] = asset_id
        return pair.record_id
```

- [ ] **Step 2: 既存のテストが壊れていないことを確認する**

```bash
uv run pytest app/tests/test_stacker.py -v
```

期待: すべて PASS（メソッドを足しただけ）。

- [ ] **Step 3: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/tests/test_stacker.py
git commit -m "$(cat <<'MSG'
test(stacker): World に再確認・照合可能化・送り直しを足す

第 2 パスの通しを書くのに、再確認を同じ世界で走らせる必要が出た。
送り直しは create_pairs が無効化された行を跨ぐことに依存するので、
そこも World の側に置いて、テスト本体は経路だけを書けるようにする。
MSG
)"
```

---

### Task 8: 片方だけ消えた組が、送り直しで組み直ることを通しで確かめる

**ファイル:**
- テスト: `app/tests/test_stacker.py`

**インタフェース:**
- 消費: Task 1〜3、Task 7 の `World` のメソッド
- 提供: なし（通しの確認）

- [ ] **Step 1: 落ちるテストを書く**

設計 §3.1 の 1〜7 をそのまま通す。

```python
def test_a_pair_is_restacked_after_one_side_is_resent(world):
    """**片方だけ消えた組は、送り直せば組み直る**（§3.1）.

    1. 再確認で CR2 が消滅と判定される
    2. `_reopen_stack_of` が組の両方を未評価へ戻す
    3. CR2 の記録が無効化される
    4. 第 2 パスが JPG を拾い、相方が居ないので見送り
    5. 利用者が通常経路で CR2 を送り直す（新しい記録）
    6. 第 2 パスが新しい CR2 を拾い、見送りの JPG を引き上げて組み直す

    **6 が通るのは `record_for` が有効な行を返すようになった後だけ。**
    """
    world.make_checkable()
    world.run()
    assert world.row("IMG_1234.CR2")["stack_state"] == "stacked"

    # 利用者が Immich でスタックを解除し、CR2 だけを完全に削除した。
    world.immich.stacks.clear()
    world.forget_on_the_server("IMG_1234.CR2")
    world.rechecker().run(world.ctx, world.destination_id)

    assert world.row("IMG_1234.CR2")["invalidated_at"] is not None
    assert world.row("IMG_1234.CR2")["invalidated_reason"] == "remote_missing"

    world.run()
    assert world.row("IMG_1234.JPG")["stack_state"] == "skipped"

    world.resend("IMG_1234.CR2", "asset-CR2-again")
    world.run()

    jpg = world.row("IMG_1234.JPG")
    cr2 = world.row("IMG_1234.CR2")
    assert jpg["stack_state"] == "stacked"
    assert cr2["stack_state"] == "stacked"
    assert jpg["remote_stack_id"] == cr2["remote_stack_id"]
```

- [ ] **Step 2: 落ちることを確認する**

**Task 3 の変更を一時的に戻して**（`record_for` から `AND invalidated_at IS NULL` を外す）、
このテストが FAIL することを確認する。最後の `stack_state == "stacked"` で落ちるはず
（`skipped` のまま動かない）。**確認したら戻す。**

```bash
uv run pytest app/tests/test_stacker.py -k restacked -v
```

- [ ] **Step 3: 実装は不要**（Task 1〜3 で足りている）

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest app/tests/test_stacker.py -v
```

- [ ] **Step 5: 変異試験**

`_reconcile_stacks` を `stamp_many` の**前**へ移す → この通しで異常が観測できるか
確かめる。**観測できなければ、できないことを `docs/development.md` に記録する**
（Task 12 の表にその欄がある）。

- [ ] **Step 6: コミット**

```bash
git add app/tests/test_stacker.py
git commit -m "$(cat <<'MSG'
test(stacker): 片方だけ消えた組が送り直しで組み直ることを通しで固定する

再確認 → 組を開く → 無効化 → 相方が見送り → 送り直し → 相方を引き上げて
組み直す、という 3 つの仕掛けの連携で成立している経路。どこか 1 つが
欠けると「送り直しても永久に組めない」に落ちるので、通しで押さえる。
MSG
)"
```

---

### Task 9: 解けた組・崩れた組が、第 2 パスでどう決着するかを確かめる

**ファイル:**
- テスト: `app/tests/test_stacker.py`

**インタフェース:**
- 消費: Task 5、Task 6、Task 7 の `World` のメソッド
- 提供: なし（通しの確認）

- [ ] **Step 1: 落ちるテストを書く**

```python
def test_a_dissolved_stack_is_created_again(world):
    """**利用者が Immich で解除した組も、次の再確認で作り直す**（利用者の判断）.

    表示と実体が食い違ったまま残る方を避ける。この緊張は `decisions.md` に
    中身ごと残してある。
    """
    world.make_checkable()
    world.run()
    # 利用者がスタックだけを解除した（資産は両方そのまま在る）。
    world.immich.stacks.clear()
    world.immich.requests.clear()

    outcome = world.rechecker().run(world.ctx, world.destination_id)
    assert outcome.unstacked == 1

    world.run()

    jpg = world.row("IMG_1234.JPG")
    cr2 = world.row("IMG_1234.CR2")
    assert jpg["stack_state"] == "stacked"
    assert cr2["stack_state"] == "stacked"
    assert jpg["remote_stack_id"] == cr2["remote_stack_id"]
    assert ("POST", "/api/stacks") in world.immich.requests


def test_a_stack_absorbed_by_someone_else_settles_as_skipped(world):
    """崩れた組は、戻した先で見送りに落ちる（§9.11 の「触らない」）.

    戻すことと作り直すことは別。相手側に別の組があるなら触らない、という
    既存の判断が、戻す経路を足しても変わらないことを見る。
    """
    world.make_checkable()
    world.run()
    stack_id = world.row("IMG_1234.JPG")["remote_stack_id"]
    # 利用者が第三の資産を組に足し、CR2 を外した（集合が一致しなくなる）。
    world.immich.stacks[stack_id]["assets"] = [
        world.assets["IMG_1234.JPG"],
        "someone-else",
    ]

    outcome = world.rechecker().run(world.ctx, world.destination_id)
    assert outcome.unstacked == 1

    world.run()

    rows = [world.row("IMG_1234.JPG"), world.row("IMG_1234.CR2")]
    assert all(row["stack_state"] == "skipped" for row in rows)
    assert all("別のスタック" in (row["stack_reason"] or "") for row in rows)
```

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_stacker.py -k "dissolved or absorbed" -v
```

期待: Task 6 を入れる前なら `outcome.unstacked` で FAIL。入れた後は PASS。
**Task 6 の `_reconcile_stacks` の呼び出しを一時的に外して落ちることを確認し、戻す。**

- [ ] **Step 3: 実装は不要**

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest
```

- [ ] **Step 5: コミット**

```bash
git add app/tests/test_stacker.py
git commit -m "$(cat <<'MSG'
test(stacker): 解けた組が組み直され、崩れた組は見送りに落ちることを固定する

再確認が戻した組を、第 2 パスが相手を読み直してから決める。全員が
`stack: null` なら作り直し、誰かが別のスタックに入っていれば
「相手側に別のスタックがある」で見送る。§9.11 の既存の判断が、
戻す経路を足しても変わらないことを押さえる。
MSG
)"
```

---

### Task 10: くわしくから「送り直す」を消す

**ファイル:**
- 変更: `web/src/screens/PhotoDetail.tsx`
- テスト: `web/src/screens/PhotoDetail.test.tsx`

**インタフェース:**
- 提供: `/photos/:id` の宛先ごとの行に残る操作は「サーバを確かめる」だけ

- [ ] **Step 1: 落ちるテストを書く**

`web/src/screens/PhotoDetail.test.tsx` の `describe("送り直す・再確認")` から、
**「送り直す」に関する 4 件を消して 1 件に置き換える**（286〜341 行あたり）。
「サーバを確かめる」のテストは残す。

```typescript
  it("リモートから消えた宛先にも「送り直す」は出さない", async () => {
    // 送り直し専用の経路は無くした。消えた記録は再確認が無効化して
    // 「まだ送っていない」へ戻すので、通常の送る画面から送る（§9.10）。
    renderDetail({
      destinations: [
        { destination_id: "d1", name: "家", state: "complete", presence: "gone", upload_id: "u1" },
      ],
    });

    expect(await screen.findByRole("button", { name: /^サーバを確かめる/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^送り直す/ })).not.toBeInTheDocument();
  });
```

**`renderDetail` の名前と引数は、このファイルの既存のヘルパに合わせること。**

- [ ] **Step 2: 落ちることを確認する**

```bash
cd web && npm test -- PhotoDetail
```

期待: 「送り直す」ボタンが見つかって FAIL。

- [ ] **Step 3: 最小実装**

`web/src/screens/PhotoDetail.tsx` から次を消す。

1. `requeue()` 関数（157〜163 行あたり、docstring ごと）
2. `presence === "gone"` の但し書き（328〜330 行あたり）
3. `presence === "gone"` の「送り直す」ボタン（332〜348 行あたり、コメントごと）

`acting` の docstring を直す（「送り直す・サーバを確かめる」→「サーバを確かめる」）。

```typescript
  // 宛先ごとの操作（サーバを確かめる）。**消す・グループへの操作とは
  // 別の状態で持つ** —— 確認を挟まない即時操作なので、他の busy と混ぜると
  // 無関係な操作までボタンが押せなくなる。
  // **busy は全宛先で 1 つを共有する。** どれか 1 つを操作している間は、
  // 他の宛先のボタンも押せなくする（連打で同じ操作が二重に飛ぶのを防ぐのが
  // 目的で、宛先ごとに分けるほどの重さの操作ではない）。
  const acting = useMutation();
```

`PRESENCE` の `gone` は**残す**。

```typescript
const PRESENCE: Record<string, string> = {
  not_sent: "まだ送っていません",
  sending: "送っている最中です",
  present: "Immich に入っています",
  trashed: "Immich のゴミ箱にあります",
  // **再確認が無効化して未送信へ戻すので、この状態に留まる記録は出ない。**
  // 出たらどこかが壊れているので、生の enum ではなく本当のことを言う。
  gone: "Immich にはもうありません",
  unknown: "Immich にあるか確かめていません",
  failed: "送れませんでした",
};
```

- [ ] **Step 4: 通ることを確認する**

```bash
cd web && npm test -- PhotoDetail && npx tsc --noEmit
```

- [ ] **Step 5: コミット**

```bash
cd web && npm run lint
cd .. && git add web/src/screens/PhotoDetail.tsx web/src/screens/PhotoDetail.test.tsx
git commit -m "$(cat <<'MSG'
fix(web): くわしくから「送り直す」を消す

押しても送信ジョブを積まないので、`pending` に戻るだけで何も起きず、
画面は「送っている最中です」と表示し続けていた。送っていないのに
送っていると言う状態だった。

消えた記録は再確認が無効化して「まだ送っていない」へ戻すので、
通常の送る画面から送る。宛先ごとの行に残る操作は「サーバを確かめる」
だけになる。

`missing_at` の但し書きも消える。元ファイルが見当たらないメディアは
`SENDABLE_CLAUSE` が未送信の候補から外すので、条件を 2 か所に
持たなくてよくなった。
MSG
)"
```

---

### Task 11: `POST /uploads/{id}/requeue` を消す

**ファイル:**
- 変更: `app/src/mediaferry/api/routes_uploads.py`
- 変更: `app/src/mediaferry/api/errors.py`
- 変更: `web/src/api/errors.ts`
- 変更: `web/src/api/types.ts`（再生成）
- テスト: `app/tests/test_api_uploads.py`

- [ ] **Step 1: 落ちるテストを書く**

`app/tests/test_api_uploads.py` の requeue に関するテスト（147・164・379 行あたり）を
**1 件に置き換える**。**先頭に `from mediaferry.clock import now_iso` を足す。**

```python
def test_the_requeue_route_is_gone(world, client):
    """送り直し専用の経路は無くした（§9.10）.

    消えた記録は再確認が無効化して「まだ送っていない」へ戻すので、通常の
    送る経路から送る。**以前ならこの形が 200 を返していた**（リモートに無いと
    確認できた `complete`）ので、経路が本当に消えたことが見える。
    """
    _, destination_id, media_id, api_db = world
    record_id = client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    ).json()["pairs"][0]["upload_record_id"]
    api_db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = NULL,"
        " remote_checked_at = ? WHERE id = ?",
        (now_iso(), record_id),
    )
    api_db.commit()

    assert client.post(f"/api/uploads/{record_id}/requeue").status_code == 404
```

- [ ] **Step 2: 落ちることを確認する**

```bash
uv run pytest app/tests/test_api_uploads.py -k requeue_route_is_gone -v
```

期待: `assert 200 == 404` で FAIL（いまはこの条件が requeue の成功条件そのもの）。

- [ ] **Step 3: 最小実装**

1. `app/src/mediaferry/api/routes_uploads.py` から `requeue_upload`（99〜136 行）を削除
2. `app/src/mediaferry/api/errors.py` から `NOT_REQUEUEABLE = "not_requeueable"`（55 行）を削除
3. `web/src/api/errors.ts` から `not_requeueable: "この記録は送り直せません。",`（23 行）を削除
4. `web/src/api/types.ts` を再生成（このリポジトリの生成手順に従う。
   `docs/development.md` に手順がある）

`UploadRepository.check_eligibility` は**残す**（`claim_next` と `jobs/uploader.py` が使う）。

- [ ] **Step 4: 通ることを確認する**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
cd web && npm test && npx tsc --noEmit
```

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/api/routes_uploads.py app/src/mediaferry/api/errors.py app/tests/test_api_uploads.py web/src/api/errors.ts web/src/api/types.ts
git commit -m "$(cat <<'MSG'
refactor(api): POST /uploads/{id}/requeue を消す

画面からこれを叩くボタンが無くなった。「画面から呼べない API は、
機能が無いのと同じ」の裏返しで、ボタンを消したら API も消す。

`check_eligibility` は残す。claim と uploader も使っているので、
呼び出し元が 1 つ減るだけ。
MSG
)"
```

---

### Task 12: 記録を直す

**ファイル:**
- 変更: `docs/design.md` / `docs/decisions.md` / `docs/user-guide.md` / `docs/development.md`

**`docs/` は ruff の対象外**なので、整形の心配は要らない。

- [ ] **Step 1: `docs/design.md`**

| 節 | 直すところ |
| --- | --- |
| §9.10「ゴミ箱と消滅の追跡」 | requeue を消し、**消滅は無効化して未送信へ戻す**に書き換え。再確認の段に**スタックの照合**を足す |
| §9.10 の presence の説明 | `gone` は「変更後は原理的に出ない。出たらどこかが壊れている」と明記 |
| §9.11 | 「相手の状態 → すること」の表の手前に、**解けた組・崩れた組を未評価へ戻す**経路を足す |
| §10 の遷移表 | 「`invalidated_at` が入っている」の但し書きから「epoch を進めた場合は」を外す |
| §10 の API 一覧（1707 行あたり） | `POST /uploads/{id}/requeue` の行を削除 |
| §13（1968 行あたり） | 「送り直す操作は 設定 › 送り先」の但し書きを見直す |

- [ ] **Step 2: `docs/decisions.md`**

2 件。

1. 「**再確認は送り直さない。見えるようにするだけ**」を**覆した判断として書き換える**。
   理由: 専用経路は動線が閉じておらず（送信ジョブを積まない）、`pending` のまま
   「送っている最中です」と嘘をつく画面になっていた。無効化して通常経路へ戻す形でも、
   **送信そのものは利用者の明示操作**なので「黙って戻さない」は保てる。
2. **解けた組を作り直す判断**を足す。緊張の中身ごと残す ——
   §9.11 が `origin` の条件を厳しくしている理由（「利用者が手で作った組を作り直しうる」）と
   衝突すること、この回で決めた「外部への副作用は明示操作でしか起こさない」とも
   衝突すること、**それでも表示と実体が食い違ったまま残る方を避けた**こと（利用者の判断）、
   1 組だけ外す手段は無くプロファイルの `stack` 節を切るしかないこと。

- [ ] **Step 3: `docs/user-guide.md`**

307 / 326 / 332 行あたり。「設定 › 送り先の『送り直す』で送り直します」
「本当に送り直したい場合は、Immich 側で完全に削除してから…」を、
**「サーバを確かめる」を押すと未送信に戻る**形へ書き換える。
**Immich で組を解除しても再確認で組み直ることも書く。**

- [ ] **Step 4: `docs/development.md`**

| 行 | どうする |
| --- | --- |
| 「送り直せない理由が、`missing_at` 以外では消える」 | **落とす**（ボタンごと無くなった） |
| 「§9.10 の『ゴミ箱にある』『リモートに存在しない』を、送り先の一覧が出していない」 | **残すが縮める** —— `gone` は未送信へ戻るので、**ゴミ箱の分だけ**にする |
| 新規 | `Candidate.invalidated` が到達不能になったことを、**検出できない変異**として記録する |
| 新規 | Task 6 の変異「`_reconcile_stacks` を `stamp_many` の前に置く」が検出できなかった場合、それも記録する |

- [ ] **Step 5: コミット**

```bash
git add docs/
git commit -m "$(cat <<'MSG'
docs: 送り直しをやめた判断と、スタックを組み直す判断を記録する

「再確認は送り直さない。見えるようにするだけ」を覆した。専用経路は
動線が閉じておらず、pending のまま「送っている最中です」と嘘をつく
画面になっていたため。無効化して通常経路へ戻す形でも、送信そのものは
利用者の明示操作のままなので、「黙って戻さない」は保てる。

解けた組を作り直す判断は、緊張の中身ごと残した。§9.11 が origin の
条件を厳しくしている理由とも、この回で決めた「外部への副作用は明示
操作でしか起こさない」とも衝突する。それでも表示と実体が食い違った
まま残る方を避けた（利用者の判断）。
MSG
)"
```

---

### Task 13: 全体の確認

- [ ] **Step 1: 全部通す**

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
cd web && npm test && npx tsc --noEmit && npm run lint
```

- [ ] **Step 2: 死んだ参照が残っていないか探す**

```bash
grep -rn "requeue\|NOT_REQUEUEABLE\|not_requeueable" app/ web/src/ docs/ | grep -v docs/history/
```

期待: `docs/history/` 以外に出てこない（履歴の文書は書き換えない）。

- [ ] **Step 3: PR を出す**

```bash
git push -u origin fix/recheck-to-unsent
gh pr create --title "fix: 送り直しをやめ、再確認から通常経路へ戻す" --body-file -
```

本文は**段落ごとに 1 行**で書く（`~/.claude/CLAUDE.md` の規約）。
**セッション URL を入れない。**
