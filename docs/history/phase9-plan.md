# Phase 9 の実装計画 —— 写真タブで 1 件を知って消せるようにする

> **エージェントで回す場合:** `superpowers:subagent-driven-development`（推奨）か
> `superpowers:executing-plans` を使い、タスクごとに実装する。手順は `- [ ]` で追える。

**ゴール:** 写真タブで 1 件を開いて、それが何かを知り、Immich に無い結合物なら消せるようにする。

**方針:** 消せるかどうかの規則を**サーバの 1 か所**に置き、一覧・詳細・削除の 3 つが
同じ定義を使う。画面は「押す＝開く／隅の丸＝選ぶ」に変え、`/photos/:id` を新設する。
成功した結合は写真タブへ寄せ、設定側の画面は中身どおりの名前へ改める。

**技術:** Python 3.14 / FastAPI / SQLite、React 19 + react-router / Vitest / Playwright。

**設計（spec）:** [`phase9-design.md`](phase9-design.md)。**この計画は spec から論を
起こしている。実装者は両方を読む。**

## Global Constraints

- Python は `>=3.14`。すべてのモジュールは `from __future__ import annotations` で始める
- ruff: `line-length = 100`、`select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`
- **コメントと docstring は日本語で書く。** 過去の経緯はコードに書かず `docs/` に残す
- **環境固有の値をリポジトリに含めない**（IP・ホスト名・データセットのパス・API キー）
- **DB に絶対パスを保存しない。** `DATA_ROOT` からの相対パスのみが正規形
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**
- 時刻は **UTC の ISO-8601 文字列**で、生成は `mediaferry.clock` の関数だけを使う
- **DB 接続はスコープごとに 1 本。** `immediate()` は**入れ子にできない**
  （`BEGIN IMMEDIATE` が二重になる）
- **画面に内部の名前を出さない**（§13）。`rel_path` は `fileName()` を通す
- **実装より先に失敗するテストを書き、失敗を確認してから**最小実装する
- **変異試験を省かない。** `PYTHONDONTWRITEBYTECODE=1` を付け、`git checkout` は使わず
  scratchpad に控えを取る。**検出できない変異は記録に残す**

**各タスクの受け入れコマンド（全部通ること）:**

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
npm --prefix web run test:e2e      # **省かない。** Phase 8 で 4 本が赤のまま 8 タスク進んだ
```

E2E の後は孤児サーバを片付ける：`pkill -f '\.venv/bin/python3 -m mediaferry'`
（`ps -eo args | awk '$1=="python3"'` は `argv[0]` が venv の絶対パスなので**常に 0 を返す**）。

---

## 触るファイル

| ファイル | 役目 |
| --- | --- |
| `app/src/mediaferry/db/media.py` | **消せるかの規則**と削除。`LIVING_REMOTE_CLAUSE`・`deletion_blocker`・`delete_derived` |
| `app/src/mediaferry/db/merges.py` | `discard()` を「トランザクションを開く版」と「開いている前提の版」に割る |
| `app/src/mediaferry/api/routes_media.py` | `role` の絞り込み、詳細の応答、DELETE の差し替え |
| `web/src/components/MediaTile.tsx` | 押す＝開く（Link）／隅の丸（button）／「つないだ」の印 |
| `web/src/styles.css` | 丸と印の置き場所（四隅の割り当て） |
| `web/src/screens/Photos.tsx` | 「つないだ動画」の絞り込み、タイルの配線 |
| `web/src/screens/PhotoDetail.tsx` | **新規。** くわしく画面 |
| `web/src/App.tsx` | `/photos/:id` のルート |
| `web/src/components/ConfirmDialog.tsx` | 削除の確認（新しい `kind`） |
| `web/src/screens/details/MergeHistory.tsx` | 「つないだ後の後片付け」へ改名、案内の 1 行 |
| `web/src/screens/Settings.tsx` | `DETAILS` の入口の名前 |
| `web/e2e/journey.spec.ts` | タイルの押し方の直しと、新しい錠 1 本 |
| `docs/design.md` | §13 の正本 |

**四隅の割り当て**（`.tile` の中で的と印がぶつからないように固定する）:

| 位置 | 中身 |
| --- | --- |
| 左上 | **「つないだ」の印**（`role === "derived"`） |
| 右上 | **選ぶ丸**（`.pick`。選択中は `.check` と同じ見た目） |
| 左下 | 宛先ごとの状態の印（`.mark`。いまのまま） |
| 右下 | 動画の尺（`.dur`。いまのまま） |

---

## Task 1: 消せない理由を 1 か所で決める

**Files:**
- Modify: `app/src/mediaferry/db/media.py`
- Test: `app/tests/test_stale_derived.py`

**Interfaces:**
- Produces: `mediaferry.db.media.LIVING_REMOTE_CLAUSE`（`upload_record u` に当てる SQL 断片）、
  `MediaRepository.deletion_blocker(media_file_id: str) -> str | None`
  （消せるなら `None`、消せないなら**画面にそのまま出せる日本語**）

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_stale_derived.py` の末尾に足す。冒頭の import に
`from .test_schema_uploads import a_destination, an_upload` を加える。

```python
def test_a_derived_never_sent_can_be_deleted(world, db, data_root):
    """一度も送っていない結合物は消せる."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    assert repo.deletion_blocker(media_id) is None


def test_a_derived_living_in_immich_is_kept(world, db, data_root):
    """**Immich に実在するものは消せない.** 何を送ったのかが分からなくなる."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="complete", remote_asset_id="asset-1",
              remote_is_trashed=0, remote_checked_at=now_iso())
    assert repo.deletion_blocker(media_id) == "Immich に入っている"


def test_a_derived_in_the_immich_trash_can_be_deleted(world, db, data_root):
    """**ゴミ箱は「無い」扱い**（利用者の判断）. Immich で捨てたのだから消せる."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="complete", remote_asset_id="asset-1",
              remote_is_trashed=1, remote_checked_at=now_iso())
    assert repo.deletion_blocker(media_id) is None


def test_a_derived_that_vanished_from_immich_can_be_deleted(world, db, data_root):
    """再確認でサーバに無いと分かった記録（`remote_asset_id` が外れている）."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="complete", remote_asset_id=None,
              remote_checked_at=now_iso())
    assert repo.deletion_blocker(media_id) is None


def test_an_unobserved_complete_is_kept(world, db, data_root):
    """**「無い」には観測を要求する.**

    `0007` を適用した DB には「向き先の記録が無い complete」が残っている。
    **Immich に在るのに識別子を捨てただけ**かもしれないので消さない
    （`POST /uploads/{id}/requeue` が同じ 2 列で選んでいるのと条件をそろえる）。
    """
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="complete", remote_asset_id=None,
              remote_checked_at=None)
    assert repo.deletion_blocker(media_id) == "Immich にあるかどうかを確かめていない"


def test_a_derived_being_sent_is_kept(world, db, data_root):
    """送信中は決着していない."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="pending")
    assert repo.deletion_blocker(media_id) == "送信中か、確認を待っている記録がある"


def test_an_invalidated_record_does_not_keep_a_derived(world, db, data_root):
    """**無効化された記録は数えない**（§10）."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="complete", remote_asset_id="asset-1",
              remote_is_trashed=0, remote_checked_at=now_iso(),
              invalidated_at=now_iso(), invalidated_reason="試験")
    assert repo.deletion_blocker(media_id) is None


def test_an_original_can_never_be_deleted(world, db, data_root):
    """カードから取り込んだ元ファイルは対象外."""
    repo, _, ref = world
    media_id = a_media_file(db, ref, role="original")
    assert repo.deletion_blocker(media_id) == "取り込んだ元ファイルは消せない"


def test_a_failed_record_does_not_keep_a_derived(world, db, data_root):
    """送れなかった記録は、リモートに何も残していない."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="failed", last_error="つながらない")
    assert repo.deletion_blocker(media_id) is None
```

`now_iso` を使うので、import に `from mediaferry.clock import now_iso` を足す。

- [ ] **Step 2: 落ちることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stale_derived.py -k deletion_blocker -v
```

期待: `AttributeError: 'MediaRepository' object has no attribute 'deletion_blocker'`。

- [ ] **Step 3: 最小の実装を書く**

`app/src/mediaferry/db/media.py` の `MediaRepository` の上に定数を置く。

```python
# **「Immich に生きている」記録の条件**（`upload_record u` に当てる）。
# 消せるかの判定・詳細の応答・DELETE の 3 つがこの 1 つの定義を使う。**写しを作らない。**
#
# 3 つのどれかに当てはまれば「生きている」＝消させない。
#
#   * 決着していない（送信中・確認待ち）
#   * 相手に実在する（識別子があり、ゴミ箱でもない）
#   * **在るかどうかを観測していない**（`complete` なのに識別子も確認時刻も無い）
#
# **`remote_is_trashed` が NULL は「在る」側に倒す。** 観測していないだけで、
# 無いことの証明ではない。
_IN_FLIGHT = (
    "'pending', 'checking', 'uploading', 'asset_known', 'tagging',"
    " 'fixing_datetime', 'awaiting_datetime_approval', 'needs_recheck'"
)

LIVING_REMOTE_CLAUSE = (
    "u.invalidated_at IS NULL AND ("
    f" u.state IN ({_IN_FLIGHT})"
    " OR (u.remote_asset_id IS NOT NULL AND coalesce(u.remote_is_trashed, 0) = 0)"
    " OR (u.state = 'complete' AND u.remote_asset_id IS NULL"
    "     AND u.remote_checked_at IS NULL))"
)
```

`MediaRepository` にメソッドを足す。

```python
    def deletion_blocker(self, media_file_id: str) -> str | None:
        """消せない理由を返す（消せるなら `None`）.

        **画面にそのまま出せる日本語を返す。** 押しても 409 で断られるボタンを
        並べないため、一覧・詳細・DELETE がこの 1 つの判定を使う。

        理由を 1 つに絞れるよう、当てはまりの強い順に見る。
        """
        row = self._conn.execute(
            "SELECT role FROM media_file WHERE id = ?", (media_file_id,)
        ).fetchone()
        if row is None:
            return "そのファイルは無い"
        if row["role"] != "derived":
            return "取り込んだ元ファイルは消せない"
        for clause, reason in (
            (f"u.invalidated_at IS NULL AND u.state IN ({_IN_FLIGHT})",
             "送信中か、確認を待っている記録がある"),
            ("u.invalidated_at IS NULL AND u.remote_asset_id IS NOT NULL"
             " AND coalesce(u.remote_is_trashed, 0) = 0",
             "Immich に入っている"),
            ("u.invalidated_at IS NULL AND u.state = 'complete'"
             " AND u.remote_asset_id IS NULL AND u.remote_checked_at IS NULL",
             "Immich にあるかどうかを確かめていない"),
        ):
            found = self._conn.execute(
                f"SELECT 1 FROM upload_record u WHERE u.media_file_id = ? AND {clause}",  # noqa: S608
                (media_file_id,),
            ).fetchone()
            if found is not None:
                return reason
        return None
```

- [ ] **Step 4: 通ることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stale_derived.py -v
```

期待: 全部 PASS（既存の 3 本も落ちないこと）。

- [ ] **Step 5: 変異試験**

scratchpad に控えを取ってから、1 つずつ壊して**対応するテストが落ちること**を見る。

| 壊すもの | 落ちるべきテスト |
| --- | --- |
| `coalesce(u.remote_is_trashed, 0) = 0` → `= 1` | `test_a_derived_living_in_immich_is_kept` |
| `u.remote_checked_at IS NULL` → `IS NOT NULL` | `test_an_unobserved_complete_is_kept` と `test_a_derived_that_vanished_from_immich_can_be_deleted` |
| `u.invalidated_at IS NULL` を落とす | `test_an_invalidated_record_does_not_keep_a_derived` |
| `row["role"] != "derived"` → `== "derived"` | `test_an_original_can_never_be_deleted` |

```bash
cp app/src/mediaferry/db/media.py /tmp/claude-1000/.../scratchpad/media.py.bak
# 1 つ壊す → PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stale_derived.py
cp /tmp/claude-1000/.../scratchpad/media.py.bak app/src/mediaferry/db/media.py
```

- [ ] **Step 6: commit**

```bash
git add app/src/mediaferry/db/media.py app/tests/test_stale_derived.py
git commit -m "feat(media): 消せない理由を 1 か所で決める"
```

---

## Task 2: 削除の規則を差し替え、現行グループは「別々にした」にする

**Files:**
- Modify: `app/src/mediaferry/db/merges.py`（`discard` を割る）
- Modify: `app/src/mediaferry/db/media.py`（`delete_stale_derived` → `delete_derived`）
- Modify: `app/src/mediaferry/api/routes_media.py`（呼び先の名前）
- Test: `app/tests/test_stale_derived.py`

**Interfaces:**
- Consumes: Task 1 の `deletion_blocker`
- Produces: `MergeRepository.discard_locked(group_id: str) -> None`
  （**トランザクションが開いている前提**）、`MediaRepository.delete_derived(media_file_id: str) -> str`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_deleting_a_live_groups_output_discards_the_group(world, db, data_root):
    """**現行グループの出力を消したら、グループごと「別々にした」にする.**

    `merged` のまま出力だけ外すと `merge_member` が active に残り、再検出も
    組み直しも塞がって**二度とつなげなくなる**（`0017` に実機で詰まった記録がある）。
    """
    repo, _, ref = world
    media_id, group_id, path = a_derived_with_group(db, data_root, ref)
    member = a_media_file(db, ref, role="original")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group_id, member))

    repo.delete_derived(media_id)

    assert not path.exists()
    group = db.execute("SELECT status FROM merge_group WHERE id = ?", (group_id,)).fetchone()
    assert group["status"] == "skipped"
    # **元になったファイルが解放される** —— trigger が active を落とす。
    active = db.execute(
        "SELECT active FROM merge_member WHERE merge_group_id = ?", (group_id,)
    ).fetchone()["active"]
    assert active == 0


def test_deleting_a_sent_but_vanished_derived_removes_its_upload_records(world, db, data_root):
    """**記録も一緒に消す.**

    `upload_record.media_file_id` は `ON DELETE RESTRICT`（`0004`）なので、
    記録を残したまま `media_file` の行は消せない。Immich にも NAS にも無いものの
    記録なので、指す先の無い記録を残さない。
    """
    repo, _, ref = world
    media_id, _, path = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    record_id = an_upload(db, dest, media_id, state="complete", remote_asset_id=None,
                          remote_checked_at=now_iso())

    repo.delete_derived(media_id)

    assert not path.exists()
    assert db.execute(
        "SELECT count(*) FROM upload_record WHERE id = ?", (record_id,)
    ).fetchone()[0] == 0


def test_a_derived_living_in_immich_is_not_deleted(world, db, data_root):
    """**判定と削除で規則がずれていないこと.** 消せない理由をそのまま上げる."""
    repo, _, ref = world
    media_id, _, path = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="complete", remote_asset_id="asset-1",
              remote_is_trashed=0, remote_checked_at=now_iso())

    with pytest.raises(GroupNotEditable, match="Immich に入っている"):
        repo.delete_derived(media_id)
    assert path.exists()
```

既存の `test_the_output_of_a_live_group_is_kept` は**この設計で意味が変わる**
（現行グループの出力は消せるようになった）。**消すのではなく、新しい挙動をより直接的に
書く形へ変える** —— 上の `test_deleting_a_live_groups_output_discards_the_group` が
その置き換えになるので、古いほうは削除し、コミット本文に理由を書く。

- [ ] **Step 2: 落ちることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stale_derived.py -v
```

期待: `AttributeError: ... 'delete_derived'`。

- [ ] **Step 3: `discard` を割る**

`app/src/mediaferry/db/merges.py`。**`immediate()` は入れ子にできない**ので、
「開く版」と「開いている前提の版」に分ける。

```python
    def discard(self, group_id: str) -> None:
        """グループを捨てる（`skipped` にする）.

        **公開済みの派生物はここでは消さない。** 消すかどうかは呼ぶ側が決める
        （写真タブの削除は `MediaRepository.delete_derived` が両方を 1 つの
        トランザクションで行う）。
        """
        with immediate(self._conn):
            self.discard_locked(group_id)

    def discard_locked(self, group_id: str) -> None:
        """`discard` の中身。**トランザクションが開いている前提。**

        `immediate()` は入れ子にできないので、同じトランザクションで他の書き込みも
        行う呼び手（削除）はこちらを使う。
        """
        self._assert_editable(group_id)
        # **無効化は skipped にする前に行う**（`supersede` と同じ理由）。
        # `status` を立てた trigger が member を `active = 0` にするので、
        # 後だと「active な member」を条件にした無効化が 1 件も当たらない。
        self._invalidate_pending(group_id, "結合グループを破棄した")
        self._conn.execute(
            "UPDATE merge_group SET status = 'skipped', updated_at = ? WHERE id = ?",
            (now_iso(), group_id),
        )
```

- [ ] **Step 4: `delete_derived` を書く**

`app/src/mediaferry/db/media.py`。`from .merges import GroupNotEditable, MergeRepository` に変える。

```python
    def delete_derived(self, media_file_id: str) -> str:
        """つないだ動画を消す（写真タブの「消す」）.

        **消してよいのは、Immich に生きていない `derived` だけ**
        （規則は `deletion_blocker`）。元ファイルは対象外。

        **持ち主が現行のグループなら、一緒に「別々にした」にする。** `merged` の
        まま出力だけ外すと `merge_member` が active に残り、再検出も組み直しも
        塞がって二度とつなげなくなる。同じトランザクションで行うので、
        `MergeRepository` の**開いている前提の版**を呼ぶ。

        **DB を先に消し、実体は後で消す。** 逆にすると、途中で落ちたときに
        「レコードはあるのに実体が無い」状態になり、失ったように見える。
        この順なら、実体だけが残っても孤立として画面に出る（回収できる）。
        """
        with immediate(self._conn):
            # **トランザクションの中で見直す。** 判定と削除の間に送信が始まりうる。
            blocker = self.deletion_blocker(media_file_id)
            if blocker is not None:
                raise GroupNotEditable(blocker)
            row = self._conn.execute(
                "SELECT rel_path FROM media_file WHERE id = ?", (media_file_id,)
            ).fetchone()
            group = self._conn.execute(
                "SELECT id, status, superseded_by_id FROM merge_group"
                " WHERE output_media_file_id = ?",
                (media_file_id,),
            ).fetchone()
            if group is None:
                # 出所が分からない。孤立と同じ扱いで、判断はユーザに委ねる。
                raise GroupNotEditable("この派生物の出所が分からない")
            if group["superseded_by_id"] is None and group["status"] == "merged":
                MergeRepository(self._conn).discard_locked(group["id"])
            # 実体が無いのに指し続けない。
            self._conn.execute(
                "UPDATE merge_group SET output_media_file_id = NULL WHERE id = ?", (group["id"],)
            )
            # **記録を先に消す。** `media_file_id` は ON DELETE RESTRICT。
            self._conn.execute(
                "DELETE FROM upload_record WHERE media_file_id = ?", (media_file_id,)
            )
            self._conn.execute("DELETE FROM media_file WHERE id = ?", (media_file_id,))
            rel_path = row["rel_path"]
        with contextlib.suppress(OSError):
            (self._data_root / rel_path).unlink()
        return rel_path
```

**古い `delete_stale_derived` は消す。** 呼び手は `routes_media.py` の 1 か所だけ
（`repo.delete_derived(media_id)` に変える）。

- [ ] **Step 5: 通ることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/ -v
```

期待: 全部 PASS。`test_merge_repository.py` の `discard` のテストも通ること
（`discard()` の外向きの振る舞いは変えていない）。

- [ ] **Step 6: 変異試験**

| 壊すもの | 落ちるべきテスト |
| --- | --- |
| `discard_locked` の呼び出しを消す | `test_deleting_a_live_groups_output_discards_the_group` |
| `DELETE FROM upload_record` を消す | `test_deleting_a_sent_but_vanished_derived_removes_its_upload_records`（外部キーで落ちる） |
| `group["status"] == "merged"` → `!= "merged"` | 同上の 1 本目 |
| トランザクション内の `deletion_blocker` を消す | `test_a_derived_living_in_immich_is_not_deleted` |

- [ ] **Step 7: commit**

```bash
git add app/src/mediaferry/db/ app/src/mediaferry/api/routes_media.py app/tests/test_stale_derived.py
git commit -m "feat(media): Immich に無い結合物を消せるようにする"
```

---

## Task 3: `GET /media` に `role` の絞り込みを足す

**Files:**
- Modify: `app/src/mediaferry/api/routes_media.py:24-100`
- Test: `app/tests/test_api_listing.py`

**Interfaces:**
- Produces: `GET /media?role=derived`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_api_listing.py` に足す（既存の fixture の作法に合わせる）。

```python
def test_media_can_be_filtered_to_merged_videos(client, db, ref):
    """写真タブの「つないだ動画」の絞り込み."""
    a_media_file(db, ref, role="original", rel_path="library/dji-osmo/DCIM/A.MP4")
    a_media_file(db, ref, role="derived", rel_path="derived/dji-osmo/DCIM/OUT.MP4")

    body = client.get("/api/media?role=derived").json()

    assert body["total"] == 1
    assert [row["role"] for row in body["media"]] == ["derived"]


def test_an_unknown_role_matches_nothing(client, db, ref):
    """**知らない値で全件を返さない.** 絞ったつもりが絞れていない、を作らない."""
    a_media_file(db, ref, role="original")
    assert client.get("/api/media?role=nonsense").json()["total"] == 0
```

- [ ] **Step 2: 落ちることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_api_listing.py -k role -v
```

期待: 1 本目が `total == 2` で落ちる（`role` が無視されている）。

- [ ] **Step 3: 実装する**

`list_media` の引数に `role: str | None = None` を足し、`_filters` へ渡す。
`_filters` の引数にも足し、節を 1 つ加える。

```python
    if role is not None:
        # **値の検査はしない。** 知らない値は 0 件になるだけで、`kind` と同じ扱い。
        clauses.append("m.role = ?")
        params.append(role)
```

- [ ] **Step 4: 通ることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_api_listing.py -v
```

- [ ] **Step 5: 走査量を測る**

**索引を足すかどうかは測ってから決める**（`decisions.md` の「送信の記録の一覧の索引」と
同じ判断の仕方）。`role = 'derived'` は `0014`（`profile_id, captured_at DESC, id DESC`）
の経路から外れる。

```bash
uv run python - <<'PY'
# 実データに近い行数（original 60,000 / derived 200）を入れて EXPLAIN QUERY PLAN と
# 実測時間を取る。結果は計画の記録（phase9-record.md）に残す。
PY
```

**0.5 ms を超えるようなら `0022` として `media_file (role, captured_at DESC, id DESC)` を
足す。** 超えないなら足さず、**測った値を記録に残す**（後から「なぜ無いのか」が読めるように）。

- [ ] **Step 6: commit**

```bash
git add app/src/mediaferry/api/routes_media.py app/tests/test_api_listing.py
git commit -m "feat(api): 一覧をつないだ動画だけに絞れるようにする"
```

---

## Task 4: `GET /media/{id}` を厚くする

**Files:**
- Modify: `app/src/mediaferry/api/routes_media.py:140-152`
- Test: `app/tests/test_api_listing.py`

**Interfaces:**
- Consumes: Task 1 の `deletion_blocker`、`LIVING_REMOTE_CLAUSE`
- Produces: `GET /media/{id}` の応答に `sources` / `destinations` / `deletable` /
  `delete_blocked_reason` が加わる

**`presence` の語彙**（画面が日本語に訳す。**サーバは日本語を返さない**）:

| 値 | 意味 |
| --- | --- |
| `not_sent` | この宛先へ有効な記録が無い |
| `sending` | 送信中・確認待ち |
| `present` | 識別子があり、ゴミ箱でもない |
| `trashed` | Immich のゴミ箱にある |
| `gone` | 再確認でサーバに無いと分かった |
| `unknown` | `complete` なのに識別子も確認時刻も無い（`0007` で洗った記録） |
| `failed` | 送れなかった |

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_media_detail_lists_the_files_it_was_made_from(client, db, data_root, ref):
    """くわしく画面は、元になったファイルを **`position` 順** に出す."""
    first = a_media_file(db, ref, rel_path="library/dji-osmo/DCIM/A.MP4")
    second = a_media_file(db, ref, rel_path="library/dji-osmo/DCIM/B.MP4")
    output = a_media_file(db, ref, role="derived", rel_path="derived/dji-osmo/DCIM/OUT.MP4")
    group = a_merge_group(db, ref, "digest-1", status="merged")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    # **わざと逆順に入れる** —— 挿入順で通ってしまう試験にしない。
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group, second))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, first))

    body = client.get(f"/api/media/{output}").json()

    assert [s["rel_path"] for s in body["sources"]] == [
        "library/dji-osmo/DCIM/A.MP4",
        "library/dji-osmo/DCIM/B.MP4",
    ]


def test_media_detail_says_whether_it_can_be_deleted(client, db, data_root, ref):
    """**押しても 409 で断られるボタンを並べない.** 判定はサーバが返す."""
    output = a_media_file(db, ref, role="derived", rel_path="derived/dji-osmo/DCIM/OUT.MP4")
    group = a_merge_group(db, ref, "digest-1", status="merged")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    dest = a_destination(db)
    an_upload(db, dest, output, state="complete", remote_asset_id="asset-1",
              remote_is_trashed=0, remote_checked_at=now_iso())

    body = client.get(f"/api/media/{output}").json()

    assert body["deletable"] is False
    assert body["delete_blocked_reason"] == "Immich に入っている"
    assert [d["presence"] for d in body["destinations"]] == ["present"]


def test_media_detail_marks_a_trashed_asset(client, db, ref):
    output = a_media_file(db, ref, role="derived")
    dest = a_destination(db)
    an_upload(db, dest, output, state="complete", remote_asset_id="asset-1",
              remote_is_trashed=1, remote_checked_at=now_iso())

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["trashed"]
    assert body["deletable"] is True


def test_media_detail_of_an_original_has_no_sources(client, db, ref):
    """元ファイルは何からも作られていない."""
    body = client.get(f"/api/media/{a_media_file(db, ref)}").json()
    assert body["sources"] == []
    assert body["deletable"] is False
```

- [ ] **Step 2: 落ちることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_api_listing.py -k detail -v
```

期待: `KeyError: 'sources'`。

- [ ] **Step 3: 実装する**

`routes_media.py` の `get_media` を差し替える。

```python
@router.get("/media/{media_id}")
def get_media(  # noqa: ANN201
    media_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
):
    """1 件のくわしく（§13 の「くわしく」画面）.

    **画面が要るものを 1 本で返す。** 複数の API を継ぎ足すと、片方だけ古い状態が出る。
    """
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い")
    repo = MediaRepository(conn, state.settings.data_root)
    blocker = repo.deletion_blocker(media_id)
    return {
        **_media(row),
        "sources": _sources(conn, media_id),
        "destinations": _destinations(conn, media_id),
        "deletable": blocker is None,
        "delete_blocked_reason": blocker,
    }


def _sources(conn, media_id: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """この 1 件の元になったファイル. **`position` 順**（つないだ順）."""
    rows = conn.execute(
        "SELECT mm.position, m.id, m.rel_path, m.missing_at"
        " FROM merge_group g JOIN merge_member mm ON mm.merge_group_id = g.id"
        " JOIN media_file m ON m.id = mm.media_file_id"
        " WHERE g.output_media_file_id = ? ORDER BY mm.position",
        (media_id,),
    )
    return [
        {
            "media_file_id": row["id"],
            "rel_path": row["rel_path"],
            "position": row["position"],
            "missing": row["missing_at"] is not None,
        }
        for row in rows
    ]


def _destinations(conn, media_id: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """宛先ごとの状況. **日本語にはしない** —— 画面が §13 の語彙で訳す."""
    rows = conn.execute(
        "SELECT d.id, d.name, u.state, u.remote_asset_id, u.remote_is_trashed,"
        "       u.remote_checked_at"
        " FROM upload_destination d"
        " LEFT JOIN upload_record u ON u.destination_id = d.id"
        "   AND u.media_file_id = ? AND u.invalidated_at IS NULL"
        " ORDER BY d.name",
        (media_id,),
    )
    return [
        {"destination_id": row["id"], "name": row["name"], "state": row["state"],
         "presence": _presence(row)}
        for row in rows
    ]


def _presence(row) -> str:  # noqa: ANN001
    """**`LIVING_REMOTE_CLAUSE` と同じ判断を、1 行ぶんの語彙にほどく.**

    片方を変えたらもう片方も変える（`deletion_blocker` が正）。
    """
    if row["state"] is None:
        return "not_sent"
    if row["state"] in IN_FLIGHT_STATES:
        return "sending"
    if row["remote_asset_id"] is not None:
        return "trashed" if row["remote_is_trashed"] else "present"
    if row["state"] == "complete":
        return "gone" if row["remote_checked_at"] is not None else "unknown"
    return "failed"
```

`db/media.py` に、SQL の断片と同じ語彙を Python 側にも 1 つ置いて両方が使う。

```python
# `LIVING_REMOTE_CLAUSE` の「決着していない」と同じ集合。**片方を変えたら両方変える。**
IN_FLIGHT_STATES = frozenset({
    "pending", "checking", "uploading", "asset_known", "tagging",
    "fixing_datetime", "awaiting_datetime_approval", "needs_recheck",
})

_IN_FLIGHT = ", ".join(f"'{state}'" for state in sorted(IN_FLIGHT_STATES))
```

- [ ] **Step 4: 通ることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/ -v
```

`test_api_types_are_current.py` が型の写しを見ているなら、web 側の型も一緒に直す。

- [ ] **Step 5: 変異試験**

| 壊すもの | 落ちるべきテスト |
| --- | --- |
| `ORDER BY mm.position` を落とす | `test_media_detail_lists_the_files_it_was_made_from`（逆順に入れてある） |
| `_presence` の `trashed` と `present` を入れ替える | `test_media_detail_marks_a_trashed_asset` |
| `u.invalidated_at IS NULL` を `LEFT JOIN` の条件から落とす | 新しく 1 本足す（無効な記録が状況として出ないこと） |

- [ ] **Step 6: commit**

```bash
git add app/src/mediaferry/api/routes_media.py app/src/mediaferry/db/media.py app/tests/
git commit -m "feat(api): 1 件のくわしくを 1 本の応答で返す"
```

---

## Task 5: タイルを「押す＝開く／隅の丸＝選ぶ」にする

**Files:**
- Modify: `web/src/components/MediaTile.tsx`
- Modify: `web/src/styles.css:227-249`
- Test: `web/src/components/MediaTile.test.tsx`（**新規**）

**Interfaces:**
- Produces: `MediaTile` の props に `to?: string` が加わる。`onToggle` があるときは
  **隅の丸だけ**が選択の的になる

**なぜ入れ子にしないか:** `<button>` の中に `<button>`、`<a>` の中に `<button>` は
不正な HTML。**兄弟に並べる** —— `.tile` は `<div>` にし、全面を覆う `<Link className="tilehit">`
と、その上に載る `<button className="pick">` を並べる。

- [ ] **Step 1: 失敗するテストを書く**

`web/src/components/MediaTile.test.tsx` を新規で作る。

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { MediaTile } from "./MediaTile";

const media = { id: "m1", rel_path: "library/dji-osmo/DCIM/A.MP4", kind: "video" };

function renderTile(props: Record<string, unknown>) {
  return render(
    <MemoryRouter>
      <MediaTile media={media} selected={false} {...props} />
    </MemoryRouter>,
  );
}

describe("MediaTile", () => {
  it("押すと開き、選ぶのは隅の丸", async () => {
    const onToggle = vi.fn();
    renderTile({ to: "/photos/m1", onToggle });

    // **タイル本体は開く道**（リンク）。押しても選択にはならない。
    expect(screen.getByRole("link", { name: "A.MP4" })).toHaveAttribute("href", "/photos/m1");
    await userEvent.click(screen.getByRole("link", { name: "A.MP4" }));
    expect(onToggle).not.toHaveBeenCalled();

    // **選ぶのは丸だけ。**
    await userEvent.click(screen.getByRole("button", { name: "選ぶ：A.MP4" }));
    expect(onToggle).toHaveBeenCalledWith("m1");
  });

  it("選ぶ丸は、選んでいるかどうかを名乗る", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn(), selected: true });
    expect(screen.getByRole("button", { name: "選ぶ：A.MP4" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("つないだ動画だと分かる", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn(), media: { ...media, role: "derived" } });
    expect(screen.getByText("つないだ")).toBeInTheDocument();
  });

  it("元ファイルには印を出さない", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn(), media: { ...media, role: "original" } });
    expect(screen.queryByText("つないだ")).toBeNull();
  });

  it("操作を渡さないときは絵のまま", () => {
    renderTile({});
    expect(screen.getByRole("img", { name: "A.MP4" })).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
```

- [ ] **Step 2: 落ちることを確認する**

```bash
npm --prefix web run test -- MediaTile
```

期待: `Unable to find role="link"`。

- [ ] **Step 3: 実装する**

`MediaTile.tsx` の `TileMedia` に `role` を足し、描き分けを差し替える。

```tsx
export type TileMedia = Pick<Media, "id" | "rel_path"> &
  Partial<Pick<Media, "kind" | "duration_seconds" | "status" | "role">>;

export function MediaTile({
  media,
  selected,
  onToggle,
  to,
}: {
  media: TileMedia;
  selected: boolean;
  onToggle?: (id: string) => void;
  /** 押したときに開く先。**選ぶのとは別の的**（隅の丸が選ぶ）。 */
  to?: string;
}) {
  const name = fileName(media.rel_path);
  const hasDuration = media.kind === "video" && media.duration_seconds != null;
  const inside = (
    <>
      <img src={`/api/media/${media.id}/thumbnail`} alt="" loading="lazy" className="tileimg" />
      {media.role === "derived" && <span className="madeof">つないだ</span>}
      <StatusMark status={media.status ?? null} />
      {hasDuration && <span className="dur">{formatClipLength(media.duration_seconds as number)}</span>}
    </>
  );

  // **押せないタイルをボタンにしない。** `disabled` なボタンは読み上げの木から
  // 外れるので、確認の下見（`work/Send.tsx`）に並ぶ写真のファイル名が、目で
  // 見ている人にしか届かなくなる。押せないなら、それは絵である。
  if (!to && !onToggle) {
    return (
      <span className={`tile${selected ? " sel" : ""}`} title={name} role="img" aria-label={name}>
        {inside}
      </span>
    );
  }
  // **的を入れ子にしない。** `<a>` の中の `<button>` は不正な HTML なので、
  // 全面を覆うリンクと、その上に載る丸を**兄弟**に並べる。
  return (
    <div className={`tile${selected ? " sel" : ""}`} title={name}>
      {inside}
      {to && <Link className="tilehit" to={to} aria-label={name} />}
      {onToggle && (
        <button
          type="button"
          className={`pick${selected ? " on" : ""}`}
          aria-label={`選ぶ：${name}`}
          aria-pressed={selected}
          onClick={() => onToggle(media.id)}
        >
          {selected && <Icon name="check" size={12} />}
        </button>
      )}
    </div>
  );
}
```

`import { Link } from "react-router-dom";` を足す。**`.check` の描画は `pick` に統合**
（選択中は丸が塗られる）。

`web/src/styles.css` を直す。**四隅の割り当てを固定する。**

```css
.tile { position: relative; aspect-ratio: 1; border-radius: 9px; border: none; padding: 0;
  display: flex; align-items: center; justify-content: center; color: rgba(128, 128, 128, .6); }
.tile.sel { box-shadow: 0 0 0 3px var(--accent); }
.tileimg { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;
  border-radius: inherit; }
/* 全面を覆う「開く」の的。丸より下に敷く。 */
.tilehit { position: absolute; inset: 0; border-radius: inherit; z-index: 1; }
/* 右上＝選ぶ丸。**44px の当たり判定**を見た目の丸の外へ広げる（§13）。 */
.pick { position: absolute; top: 0; right: 0; width: 44px; height: 44px; z-index: 2;
  border: none; background: none; padding: 0;
  display: flex; align-items: flex-start; justify-content: flex-end; }
.pick::before { content: ""; position: absolute; top: 7px; right: 7px; width: 21px; height: 21px;
  border-radius: 50%; border: 2px solid #fff; box-shadow: 0 0 0 1px rgba(0, 0, 0, .28); }
.pick.on::before { background: var(--accent); border-color: var(--accent); }
.pick svg { position: relative; margin: 11px 11px 0 0; color: var(--accent-ink); z-index: 1; }
/* 左上＝つないだ動画の印。 */
.madeof { position: absolute; top: 7px; left: 7px; z-index: 1; font-size: 10.5px; color: #fff;
  background: var(--accent); border-radius: 4px; padding: 1px 5px; }
```

`.tile .check` の規則は消す（`pick` に統合したため）。`.tile .mark`（左下）と
`.tile .dur`（右下）はそのまま。

- [ ] **Step 4: 通ることを確認する**

```bash
npm --prefix web run test -- MediaTile
npm --prefix web run lint && npm --prefix web run build
```

- [ ] **Step 5: 変異試験**

| 壊すもの | 落ちるべきテスト |
| --- | --- |
| `aria-pressed={selected}` を落とす | 「選ぶ丸は、選んでいるかどうかを名乗る」 |
| `media.role === "derived"` → `!== "derived"` | 「つないだ動画だと分かる」と「元ファイルには印を出さない」の両方 |
| `<Link>` の `to` を落として `onClick` にする | 「押すと開き、選ぶのは隅の丸」（`href` を見ている） |

- [ ] **Step 6: commit**

```bash
git add web/src/components/MediaTile.tsx web/src/components/MediaTile.test.tsx web/src/styles.css
git commit -m "feat(web): タイルは押すと開き、選ぶのは隅の丸にする"
```

---

## Task 6: 写真タブを新しい操作に合わせる

**Files:**
- Modify: `web/src/screens/Photos.tsx:23-33,360-375`
- Test: `web/src/screens/Photos.test.tsx`

**Interfaces:**
- Consumes: Task 3 の `role=derived`、Task 5 の `MediaTile` の `to`

- [ ] **Step 1: 失敗するテストを書く**

`Photos.test.tsx` に足す。

```tsx
it("つないだ動画だけに絞れる", async () => {
  renderPhotos();
  await userEvent.click(screen.getByRole("button", { name: "つないだ動画" }));
  await waitFor(() => expect(lastPath()).toContain("role=derived"));
});

it("つないだ動画は宛先を選ばなくても絞れる", async () => {
  // **宛先ごとの絞り込みではない.** 送り先が 0 件でも押せる。
  renderPhotos({ destinations: [] });
  expect(screen.getByRole("button", { name: "つないだ動画" })).not.toBeDisabled();
});

it("タイルは 1 件の画面へつながる", async () => {
  renderPhotos();
  await waitFor(() => expect(screen.getByRole("link", { name: /a\.JPG/ })).toBeInTheDocument());
  expect(screen.getByRole("link", { name: /a\.JPG/ })).toHaveAttribute("href", "/photos/m1");
});
```

**既存のテストのうち、タイルを押して選んでいるものは丸を押す形へ書き換える**
（`Photos.test.tsx:79-80` ほか）。`screen.getByRole("button", { name: /a\.JPG/ })` を
`screen.getByRole("button", { name: /選ぶ：a\.JPG/ })` に変える。**書き換えた
テストが守るものを緩めていないか、変異で確かめる**（Phase 8 で 2 件緩んでいた）。

- [ ] **Step 2: 落ちることを確認する**

```bash
npm --prefix web run test -- Photos
```

- [ ] **Step 3: 実装する**

`FILTERS` に 1 つ足す。

```tsx
type FilterKey = "all" | "unsent" | "awaiting" | "video" | "derived" | "sent" | "failed";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "すべて" },
  { key: "unsent", label: "まだ送っていない" },
  { key: "awaiting", label: "確認が要る" },
  { key: "video", label: "動画" },
  // **宛先ごとの絞り込みではない**ので `DESTINATION_SCOPED` には入れない。
  { key: "derived", label: "つないだ動画" },
  { key: "sent", label: "送信済み" },
  { key: "failed", label: "送れなかった" },
];
```

`filterFromParams` に `role=derived` の読み取りを、`buildMediaQuery` に書き出しを、
`selectFilter` に `nextParams.delete("role")` と `role` の設定を足す（`kind` と同じ形）。

`MediaTile` の呼び出しに `to` を足す。

```tsx
                <MediaTile
                  key={item.id}
                  media={item}
                  to={`/photos/${item.id}`}
                  selected={selected.has(item.id)}
                  onToggle={() => toggle(item.id, item.size_bytes)}
                />
```

- [ ] **Step 4: 通ることを確認する**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
```

- [ ] **Step 5: commit**

```bash
git add web/src/screens/Photos.tsx web/src/screens/Photos.test.tsx
git commit -m "feat(web): 写真タブにつないだ動画の絞り込みと 1 件への道を足す"
```

---

## Task 7: くわしく画面（`/photos/:id`）

**Files:**
- Create: `web/src/screens/PhotoDetail.tsx`
- Create: `web/src/screens/PhotoDetail.test.tsx`
- Modify: `web/src/App.tsx:71`
- Modify: `web/src/components/ConfirmDialog.tsx:14-24`

**Interfaces:**
- Consumes: Task 4 の `GET /media/{id}`、Task 2 の `DELETE /media/{id}`

**確認ダイアログの新しい `kind`:**

```tsx
  | { kind: "delete_merged_video"; name: string; sourceCount: number }
```

```tsx
    case "delete_merged_video":
      return {
        title: "このつないだ動画を消しますか",
        body: (
          <p>
            {confirmation.name} を消します。<strong>元になった {confirmation.sourceCount} 件は
            残り、「まだ送っていない」に戻ります</strong>。この動画は NAS から消え、
            <strong>元に戻せません</strong>。
          </p>
        ),
      };
```

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("何から作られたかを、順番どおりに出す", async () => {
  renderDetail({ sources: [
    { media_file_id: "s1", rel_path: "library/dji-osmo/DCIM/A.MP4", position: 0, missing: false },
    { media_file_id: "s2", rel_path: "library/dji-osmo/DCIM/B.MP4", position: 1, missing: false },
  ] });
  await waitFor(() => expect(screen.getByText("A.MP4")).toBeInTheDocument());
  const names = screen.getAllByRole("link").map((a) => a.textContent);
  expect(names).toEqual(expect.arrayContaining(["A.MP4", "B.MP4"]));
});

it("Immich に入っているものは消せず、理由を出す", async () => {
  renderDetail({ deletable: false, delete_blocked_reason: "Immich に入っている" });
  await waitFor(() => expect(screen.getByRole("button", { name: "消す" })).toBeDisabled());
  expect(screen.getByText(/Immich に入っている/)).toBeInTheDocument();
});

it("消す前に確認を出し、承諾したら DELETE する", async () => {
  const { calls } = renderDetail({ deletable: true });
  await userEvent.click(await screen.findByRole("button", { name: "消す" }));
  // **確認ダイアログを挟む**（§13）。押した瞬間には消さない。
  expect(calls().filter((c) => c.method === "DELETE")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "消す" , exact: true}));
  await waitFor(() => expect(calls()).toContainEqual({ method: "DELETE", path: "/media/m1" }));
});

it("宛先ごとの状況を §13 の言葉で出す", async () => {
  renderDetail({ destinations: [
    { destination_id: "d1", name: "家", state: "complete", presence: "trashed" },
  ] });
  await waitFor(() =>
    expect(screen.getByText("Immich のゴミ箱にあります")).toBeInTheDocument());
});
```

- [ ] **Step 2: 落ちることを確認する**

```bash
npm --prefix web run test -- PhotoDetail
```

- [ ] **Step 3: 実装する**

`web/src/screens/PhotoDetail.tsx` を作る。**`presence` の訳をここに 1 つだけ置く。**

```tsx
// 1 件のくわしく（§13）。**サーバは語彙を返し、日本語にするのはここだけ。**

const PRESENCE: Record<string, string> = {
  not_sent: "まだ送っていません",
  sending: "送っている最中です",
  present: "Immich に入っています",
  trashed: "Immich のゴミ箱にあります",
  gone: "Immich にはもうありません",
  unknown: "Immich にあるか確かめていません",
  failed: "送れませんでした",
};
```

画面の骨:

- 上の帯: 「← 写真へ」（`<Link to="/photos">`）
- 絵（`/api/media/{id}/thumbnail`）、ファイル名（`fileName`）
- `role === "derived"` なら「つないだ動画（N 本から）」
- 撮影日時（`formatDateTime`）・長さ（`formatClipLength` 相当）・大きさ（`formatBytes`）
- 宛先ごとの状況（`PRESENCE[presence]`）
- 元になったファイル（`sources` を `position` 順に、それぞれ `/photos/{media_file_id}` へリンク）
- 操作: 「送る」（`navigate("/send", { state: { ids: [id], destinationIds: [] } })`）と
  「消す」（`deletable` が偽なら `disabled`＋理由を添える）
- 消したら `navigate("/photos")` へ戻す（**消したものの画面に留まらない**）

`App.tsx` にルートを足す。**`/photos` より後ろに置く**（`:id` が先だと `/photos` を飲む
—— routes_media の並びと同じ注意）。

```tsx
          <Route path="/photos" element={<PhotosScreen />} />
          <Route path="/photos/:id" element={<PhotoDetailScreen />} />
```

- [ ] **Step 4: 通ることを確認する**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
```

- [ ] **Step 5: 変異試験**

| 壊すもの | 落ちるべきテスト |
| --- | --- |
| `disabled={!deletable}` を落とす | 「Immich に入っているものは消せず、理由を出す」 |
| 確認ダイアログを飛ばして直に DELETE | 「消す前に確認を出し、承諾したら DELETE する」 |
| `PRESENCE` の `trashed` と `gone` を入れ替える | 「宛先ごとの状況を §13 の言葉で出す」 |

- [ ] **Step 6: commit**

```bash
git add web/src/screens/PhotoDetail.tsx web/src/screens/PhotoDetail.test.tsx web/src/App.tsx web/src/components/ConfirmDialog.tsx
git commit -m "feat(web): 1 件のくわしくを開けるようにする"
```

---

## Task 8: 「つないだ後の後片付け」への改名

**Files:**
- Modify: `web/src/screens/details/MergeHistory.tsx:85-95`
- Modify: `web/src/screens/Settings.tsx`（`DETAILS` の入口）
- Test: `web/src/screens/details/MergeHistory.test.tsx`

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("名前が中身と合っていて、つないだ動画の在り処を案内する", async () => {
  renderHistory();
  expect(screen.getByRole("heading", { name: "つないだ後の後片付け" })).toBeInTheDocument();
  // **探しに来た人を迷子にしない.** 成功した結合は写真タブにある。
  expect(screen.getByRole("link", { name: /つないだ動画/ })).toHaveAttribute(
    "href",
    "/photos?role=derived",
  );
});
```

- [ ] **Step 2: 落ちることを確認する**

```bash
npm --prefix web run test -- MergeHistory
```

- [ ] **Step 3: 実装する**

- `<section aria-label>` と `<h1>` を「つないだ後の後片付け」に変える
- 見出しの下に案内を 1 行置く

```tsx
      <p className="small">
        つないだ動画そのものは <Link to="/photos?role=derived">写真 › つないだ動画</Link> で
        見られます。ここにあるのは、置き換わったり別々にしたりした後の片付けです。
      </p>
```

- `Settings.tsx` の `DETAILS` の項目名と説明も揃える
- **`/settings/merge-history` の住所は変えない**（改名は画面の名前だけ。住所を
  変えると `#stale` で来る既存の道が切れる）

- [ ] **Step 4: 通ることを確認する**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
```

- [ ] **Step 5: commit**

```bash
git add web/src/screens/details/MergeHistory.tsx web/src/screens/Settings.tsx web/src/screens/details/MergeHistory.test.tsx web/src/screens/Settings.test.tsx
git commit -m "feat(web): 記録の画面を中身どおりの名前にする"
```

---

## Task 9: E2E —— 押し方の直しと、新しい錠

**Files:**
- Modify: `web/e2e/journey.spec.ts:363-365,484`

**E2E の spec ファイルは増やさない**（サーバを回収しないので、1 本増やすと孤児が 1 つ増える）。

- [ ] **Step 1: 壊れている 2 か所を直す**

`main button.tile` は**もう選択の的ではない**（タイルは `<div>`、選ぶのは `.pick`）。

```ts
  // **選ぶのは隅の丸。** タイル本体を押すと 1 件の画面へ移ってしまう。
  const pick = page.locator("main .tile .pick").first();
  await expect(pick).toBeVisible({ timeout: 60_000 });
  await pick.click();
```

`:484` も同じ形に直す。

- [ ] **Step 2: 赤いことを確認する**

```bash
npm --prefix web run test:e2e
```

期待: 直す前は「操作バーが隠れない」と全画面巡回が赤。直した後は緑。

- [ ] **Step 3: 錠を 1 本足す**

既存の巡回 spec に足す。**結合物が要る**ので、「手でグループを作る」経路で用意する
（`min_part_size_gib` が 15 で合成カードの動画は 100 バイトなので、**検出は候補を作れない**）。

```ts
test("つないだ動画を開いて、消せる", async ({ page }) => {
  await signIn(page);
  // 手でグループを作ってつなぐ（検出は合成カードでは候補を作れない）。
  await mergeTwoParts(page);            // 既存の journey の手順を関数に切り出す

  await page.goto(app.url + "/photos?role=derived");
  await settled(page);
  const tile = page.locator("main .tile").first();
  await expect(tile).toBeVisible({ timeout: 60_000 });
  await tile.locator(".tilehit").click();

  // くわしくが開き、**何から作られたかが出る**。
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText(/つないだ動画/)).toBeVisible();

  // 一度も送っていないので消せる。
  await page.getByRole("button", { name: "消す" }).click();
  await page.getByRole("button", { name: "消す", exact: true }).click();

  await expect(page).toHaveURL(/\/photos/);
  await expect(page.locator("main .tile")).toHaveCount(0);
});
```

- [ ] **Step 4: 押せる的の錠に丸を含める**

`flatControls` は `main button` を見るので `.pick` は既に入る。**44px の錠が
`.pick` を測っていること**を確認し、測っていなければ選択子を広げる。

- [ ] **Step 5: 緑を確認して片付ける**

```bash
npm --prefix web run test:e2e
pkill -f '\.venv/bin/python3 -m mediaferry'
```

- [ ] **Step 6: commit**

```bash
git add web/e2e/journey.spec.ts
git commit -m "test(e2e): 新しいタイルの押し方に直し、消す動線に錠を掛ける"
```

---

## Task 10: 仕様と記録を更新する

**Files:**
- Modify: `docs/design.md`（§13）
- Modify: `docs/development.md`（持ち越しの表）
- Create: `docs/history/phase9-record.md`

**`design.md` §13 の更新は実装と同じ PR で行う**（仕様の正本と画面が食い違う時間を作らない）。

- [ ] **Step 1: §13 を直す**

- 画面の一覧に **`/photos/:id`（くわしく）** を足す
- 語彙表に **「つないだ後の後片付け」** を足す
- 写真タブの操作（押す＝開く／隅の丸＝選ぶ）を書く
- **消せるものの規則**を書く（Immich に生きていない `derived` だけ、「無い」には観測を要求する）

- [ ] **Step 2: 持ち越しの表から、閉じた行を消す**

`docs/development.md` の「**写真タブで、選んだ 1 件が何なのかが分からず、消せない**」の
行を、**取り消し線＋「塞いだ（Phase 9）」**の形に変える（他の閉じた行と同じ書き方）。

**閉じていない行は残す** —— §9.10 の「ゴミ箱にある／リモートに存在しない」を
**送り先の画面**が出していない件は、この設計ではくわしく画面にだけ出した。持ち越しの
文言を「くわしく画面には出るが、送り先の一覧と `requeue` の入口はまだ無い」に更新する。

- [ ] **Step 3: `phase9-record.md` を書く**

Phase 8 と同じ形で残す（`phase8-record.md` を型にする）。

- タスクごとの巡数と見つかったもの
- **変異試験の記録** —— 当てた数・生き残った数・足したテスト。**検出できない変異は
  検出できないことを書く**
- **Task 3 で測った走査量の実測値**と、索引を足した／足さなかった判断
- 書き換えた既存テストが**守るものを緩めていないか**の確認結果

- [ ] **Step 4: 索引の判断を `decisions.md` に移す**

索引を足さなかった場合、**測った値と「だから足さない」を `decisions.md` に書く**
（`development.md` の持ち越しではなく、決着済みの判断として）。

- [ ] **Step 5: commit**

```bash
git add docs/
git commit -m "docs: 写真タブの動線を仕様と記録に反映する"
```

---

## 自己確認（この計画を書いた後に見たもの）

**spec の網羅:**

| spec の節 | 実装するタスク |
| --- | --- |
| §1 写真タブの操作・「つないだ」の印・絞り込み | Task 3・5・6 |
| §2 くわしく画面 | Task 4・7 |
| §3 消せる／消せないの規則と、消したときに起きること | Task 1・2 |
| §4 「つないだ後の後片付け」への改名 | Task 8 |
| §5 API の変更（`role`・詳細・DELETE・索引） | Task 3・4・2 |
| §6 どう確かめるか | 各タスクの Step と Task 9 |

**型の一貫性:** `deletion_blocker` は Task 1 で定義し、Task 2・4 が同じ名前で使う。
`presence` の 7 語は Task 4 で定義し、Task 7 の `PRESENCE` が同じ 7 語を訳す。
`MediaTile` の `to` は Task 5 で足し、Task 6 が渡す。

**穴だったので足したもの:** Task 2 の `discard_locked`。spec は「既存の `discard()` を
そのまま使う」と書いていたが、**`immediate()` が入れ子にできない**ので、そのままでは
呼べない。spec の意図（同じ規則を再利用し、`_assert_editable` も効かせる）は保ったまま、
トランザクションの持ち方だけを割った。
