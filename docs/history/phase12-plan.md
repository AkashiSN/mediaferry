# Phase 12 の実装計画 —— 組を 3 画面で通し、まとめて選べるようにする

> **エージェントで回す場合:** `superpowers:subagent-driven-development`（推奨）か
> `superpowers:executing-plans` を使い、1 タスクずつ実装する。手順はチェックボックス
> （`- [ ]`）で追跡する。

**目標:** 組（RAW+JPEG）が一覧・詳細・送るの 3 画面で同じ 1 枚として振る舞い、
写真タブで日付ごと・範囲でまとめて選べるようにする。

**作り:** backend は追加のみ —— `GET /media/{id}` に `stack` を足し、`GET /media` に
「隠さずに組だけ教える」`stack=members` を足す。画面側は組の扱い（まとめる・
ラベル・数える）を `web/src/utils/stacks.ts` の純粋関数 1 か所に置き、3 画面が
そこを呼ぶ。

**技術:** Python 3.14 / FastAPI / SQLite、React 19 / TypeScript / Vite、
pytest / vitest / Playwright。

**設計:** [`phase12-design.md`](phase12-design.md)（この計画はそこから議論する。
実装者は両方読む）

---

## 全体の制約

設計とリポジトリ全体の決まり。**すべてのタスクの要件に、暗黙にこの節が含まれる。**

- **Python は `>=3.14`。** 実際に使う版は `.python-version` の 3.14
- **すべての Python モジュールは `from __future__ import annotations` で始める**
- **ruff の設定は `pyproject.toml`**（`line-length = 100`、
  `select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`）。`docs/` は対象外
- **コメントと docstring は日本語で書く。** コードのコメントは**いま書かれている
  コードを現在形で説明する**。過去の経緯（「以前は〜だった」「〜へ移行した」）は
  書かない —— それは `docs/` に残す
- **環境固有の値をリポジトリに含めない**（IP・ホスト名・データセットのパス・
  API キー・タイムゾーンの実値）
- **DB に絶対パスを保存しない。** `DATA_ROOT` からの相対パスだけが正規形
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**
- **実装より先に失敗するテストを書き、失敗を確認してから最小実装する**
- **変異試験を省かない。** 実装の判断を 1 つずつ壊し、対応するテストが落ちることを
  確認してから戻す。**`PYTHONDONTWRITEBYTECODE=1` を付ける**（バイト数の変わらない
  書き換えで古い `.pyc` が使われる）。**検出できない変異は、検出できないことを
  記録に残す**
- **コミットは Conventional Commits + 日本語の本文。なぜそうしたかを本文に残す**
- **公開される場所（PR・issue・コミット）に Claude のセッション URL を書かない**

### 受け入れコマンド

```bash
uv sync --all-packages          # --all-packages が必須
uv run pytest
uv run ruff check .
uv run ruff format --check .
npm --prefix web ci             # 初回のみ
npm --prefix web test
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run build
```

**画面（`web/src`）を触るタスクは、これに E2E を足す**（`phase8-record.md` の教訓 ——
入れないと黙って腐る）。

```bash
npm --prefix web run test:e2e
pkill -f '\.venv/bin/python3 -m mediaferry'   # E2E はサーバを回収しない。毎回掃除する
```

### ファイルの見取り図

| ファイル | 役目 | タスク |
| --- | --- | --- |
| `app/src/mediaferry/api/routes_media.py` | `_ranks` / `_stack_json` の切り出し、`get_media` の `stack`、`list_media` の `stack=members` | 1, 2 |
| `app/tests/test_api_media.py` | 上の両方のテスト（`collapse` のテストが既にここ） | 1, 2 |
| `web/src/api/types.ts` | OpenAPI からの生成物。**手で書かない** | 1, 2 |
| `web/src/utils/stacks.ts` | **新規。** 組をまとめる・ラベルを作る（純粋関数） | 3 |
| `web/src/utils/stacks.test.ts` | **新規。** 上の単体テスト | 3 |
| `web/src/components/MediaTile.tsx` | 札を `RAW` から `JPG+RAW` へ、`onToggle` に修飾キー | 4, 6 |
| `web/src/screens/Photos.tsx` | 日付の丸、Shift の範囲、アンカーの失効 | 5, 6 |
| `web/src/screens/PhotoDetail.tsx` | 組のセクションと、選んだぶんだけ送る | 7 |
| `web/src/screens/work/Send.tsx` | `stack=members` を付けて引き、返った集合の中だけで組む | 8 |
| `web/src/styles.css` | 札が伸びたぶんの幅 | 4 |
| `web/e2e/phase6.spec.ts` | 受け入れ（RAW+JPEG の動線が既にここにある） | 9 |

---

## Task 1: `GET /media/{id}` が組を返す

**ファイル:**
- 変更: `app/src/mediaferry/api/routes_media.py`（`_members_of` の周り、`get_media`）
- 変更: `app/tests/test_api_media.py`
- 変更: `web/src/api/types.ts`（生成物。`npm --prefix web run typegen` で作り直す）

**インタフェース:**
- 使うもの: `_members_of(conn, media_id, ranks_sql, ranks_params)`（既にある）、
  `stack_extension_ranks(profiles)`（`core/listing.py`）、
  `ProfileRegistry(conn).all()`
- 出すもの:
  - `_ranks(conn) -> tuple[str, tuple[Any, ...]]` —— `(ranks_sql, ranks_params)`。
    順位表が空なら `("", ())`
  - `_stack_json(member_rows) -> dict[str, Any]` —— `{"members": [{"id", "rel_path",
    "size_bytes"}, …]}`
  - `_stack_of(conn, media_id) -> dict[str, Any] | None`
  - `GET /media/{id}` の応答に `stack` が増える（組でなければ `null`）

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_api_media.py` の末尾に足す。**`canon_pair` / `canon_pair_without_proof`
/ `ambiguous_sibling` は `app/tests/conftest.py` にあるフィクスチャ**で、
canon-eos（`stack.extensions = ["JPG", "CR2"]`）の 2 枚組を作る。

```python
def test_the_detail_names_the_members_of_the_pair(client, canon_pair):
    """**詳細も組を知る。** 一覧でだけ組が見えると、押した先で消える."""
    jpeg = canon_pair.media_ids["JPG"]

    body = client.get(f"/api/media/{jpeg}").json()

    assert [m["rel_path"].split("/")[-1] for m in body["stack"]["members"]] == [
        "IMG_0001.JPG",
        "IMG_0001.CR2",
    ]
    assert [m["size_bytes"] for m in body["stack"]["members"]] == [
        body["size_bytes"],
        client.get(f"/api/media/{canon_pair.media_ids['CR2']}").json()["size_bytes"],
    ]


def test_the_secondary_sees_the_same_pair_with_the_primary_first(client, canon_pair):
    """**従から開いても並びは同じ**（主が先頭）. どちらから見ても同じ組."""
    raw = canon_pair.media_ids["CR2"]

    body = client.get(f"/api/media/{raw}").json()

    assert [m["rel_path"].split("/")[-1] for m in body["stack"]["members"]] == [
        "IMG_0001.JPG",
        "IMG_0001.CR2",
    ]


def test_a_lone_file_has_no_stack(client, canon_pair_without_proof):
    """同席の証拠が無ければ組ではない. **`None` を返す**（空の組を作らない）."""
    jpeg = canon_pair_without_proof.media_ids["JPG"]

    assert client.get(f"/api/media/{jpeg}").json()["stack"] is None


def test_an_ambiguous_pair_has_no_stack_in_the_detail(client, canon_pair, ambiguous_sibling):
    """**曖昧な組は詳細でも組にしない.**

    `identity_partners` は同じ状況を `ambiguous=True` と判定し、`resolve_group` は
    組を作らない。一覧と同じ `_members_of` を通すので、判断は自動でそろう ——
    ここが割れると、画面が Immich には作らない組を宣言する。
    """
    jpeg = canon_pair.media_ids["JPG"]

    assert client.get(f"/api/media/{jpeg}").json()["stack"] is None
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_api_media.py -k "detail_names_the_members or secondary_sees or lone_file or ambiguous_pair_has_no_stack" -v
```

期待: 4 本とも `KeyError: 'stack'` で FAIL。

- [ ] **Step 3: 最小実装**

`routes_media.py` の `_members_of` の**すぐ下**に足す。

```python
def _ranks(conn) -> tuple[str, tuple[Any, ...]]:  # noqa: ANN001
    """順位表を `VALUES` の並びにする. **空なら `("", ())`**（`VALUES` は 0 行を書けない）.

    一覧と詳細が同じ表を作る。**2 か所で組み立てると、片方だけが古い規則を読む。**
    """
    ranks = stack_extension_ranks(ProfileRegistry(conn).all())
    if not ranks:
        return "", ()
    return ", ".join(["(?, ?, ?)"] * len(ranks)), tuple(
        value for row in ranks for value in row
    )


def _stack_json(member_rows: list[Any]) -> dict[str, Any]:
    """組を API の形にする（一覧と詳細で同じ形）."""
    return {
        "members": [
            {
                "id": member["id"],
                "rel_path": member["rel_path"],
                "size_bytes": member["size_bytes"],
            }
            for member in member_rows
        ]
    }


def _stack_of(conn, media_id: str) -> dict[str, Any] | None:  # noqa: ANN001
    """1 件から見た組（**主が先頭**）. 組でなければ None.

    **一覧と同じ `_members_of` を通す。** 曖昧な組を組にしない判断を 2 か所に
    書かない（`docs/history/phase10-design.md` の「画面に出す組と Immich が作る組は、
    同じ関数が決める」）。
    """
    ranks_sql, ranks_params = _ranks(conn)
    if not ranks_sql:
        return None
    member_rows = _members_of(conn, media_id, ranks_sql, ranks_params)
    if member_rows is None:
        return None
    return _stack_json(member_rows)
```

`get_media` の返す辞書に 1 行足す（`"sources"` の前）。

```python
    return {
        **_media(row),
        # **この 1 件が属する組**（RAW+JPEG。組でなければ `None`）。画面は
        # 送るものをここから選ぶので、**一覧と同じ判断で返す**。
        "stack": _stack_of(conn, media_id),
        "sources": _sources(conn, media_id),
```

`list_media` の中の組み立ても `_stack_json` を通すように直す（同じ形を 2 度書かない）。

```python
        if ranks_sql:
            # 組の中身は、主の行 1 つにつき 1 回だけ引く。
            member_rows = _members_of(conn, row["id"], ranks_sql, ranks_params)
            if member_rows is not None:
                item["stack"] = _stack_json(member_rows)
```

`list_media` の中の順位表の組み立ても `_ranks` を使う。

```python
    if collapse == "stack":
        ranks_sql, ranks_params = _ranks(conn)
        if ranks_sql:
            prefix = f"WITH rank(profile_id, extension, rank) AS (VALUES {ranks_sql}) "
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_api_media.py -v
uv run pytest
```

期待: すべて PASS。**既存の `collapse=stack` のテストも通ること**（`_ranks` /
`_stack_json` への切り出しで振る舞いを変えていない証拠）。

- [ ] **Step 5: 型を作り直す**

```bash
npm --prefix web run typegen
git diff --stat web/src/api/types.ts
```

期待: `web/src/api/types.ts` に差分が出る（`get_media` の応答に `stack` が増えた）。

- [ ] **Step 6: 変異試験**

`PYTHONDONTWRITEBYTECODE=1` を付けて、1 つずつ壊して戻す。

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `_stack_of` の `if member_rows is None: return None` を `return {"members": []}` に | `test_a_lone_file_has_no_stack` |
| `_ranks` の `if not ranks: return "", ()` を消す | `_members_of` の SQL が壊れて全テストが落ちる（**等価変異ではない**ことを確かめる） |
| `_members_of` の `ORDER BY r.rank` を消す | `test_the_secondary_sees_the_same_pair_with_the_primary_first` —— **落ちなければ**、並びを固定するテストが足りていない。SQLite が偶然その順を返しているだけなので、テストを足す |
| `get_media` の `"stack": _stack_of(...)` を `"stack": None` に | 上の 2 本 |

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_api_media.py -q
```

**検出できなかった変異は、理由と一緒に `phase12-record.md` へ書く**（このタスクでは
まだファイルを作らず、メモとして残して Task 9 でまとめる）。

- [ ] **Step 7: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/api/routes_media.py app/tests/test_api_media.py web/src/api/types.ts
git commit
```

コミット本文（例）:

```
feat(api): 1 件のくわしくが組（RAW+JPEG）を返す

詳細 API は stack を一切返しておらず、画面には組を知る手段が無かった。
一覧でだけ組が見え、押した先で消える。

一覧と同じ _members_of を通す。曖昧な組を組にしない判断を 2 か所に書くと、
画面が Immich には作らない組を宣言することになる（Phase 10 の「画面に出す
組と Immich が作る組は、同じ関数が決める」）。順位表の組み立てと API の形は
_ranks / _stack_json に切り出して、一覧と詳細で同じものを使う。
```

---

## Task 2: `GET /media?stack=members`（隠さずに組だけ教える）

**ファイル:**
- 変更: `app/src/mediaferry/api/routes_media.py`（`list_media` と `_KNOWN_*`）
- 変更: `app/tests/test_api_media.py`
- 変更: `web/src/api/types.ts`（生成物）

**インタフェース:**
- 使うもの: Task 1 の `_ranks` / `_stack_json`
- 出すもの: `GET /media` の新しい引数 `stack`（`"members"` だけ。他は 400）。
  **`collapse=stack` と併用したときは `collapse` が勝つ**（隠す）

**なぜ要るか（設計 §2 の要約。実装者はここだけで判断できる）:**
送る画面が `collapse=stack` を使うと、JPG も CR2 も未送信のとき **CR2 の行が隠れて
送られない**（Immich でスタックが組まれない）。逆に「主の `members` を全部送る」と、
JPG だけ既に送ったカードで**送信済みを数え直す**。だから送る画面は**平らに引いて、
返ってきた行の集合の中だけで組む**。そのために「隠さないが組は教える」が要る。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_stack_members_names_the_pair_without_hiding_the_raw(client, canon_pair):
    """**隠さない。** 送る画面は返ってきた行を送るので、従を隠すと RAW が送られない."""
    body = client.get("/api/media?stack=members").json()

    names = [m["rel_path"].split("/")[-1] for m in body["media"]]
    assert "IMG_0001.CR2" in names
    assert "IMG_0001.JPG" in names
    for item in body["media"]:
        assert [m["rel_path"].split("/")[-1] for m in item["stack"]["members"]] == [
            "IMG_0001.JPG",
            "IMG_0001.CR2",
        ]


def test_stack_members_leaves_a_lone_file_without_a_stack(client, canon_pair_without_proof):
    """組でない行には**キーごと付けない**（空の組を作らない）.

    **一覧は「無いこと」を `null` では言わない。** 既にある 4 本
    （同ファイルの `assert "stack" not in …`）がそれを仕様として固定している ——
    行に `null` を足すと `collapse=stack` の契約が変わる。詳細（Task 1）は 1 件の
    応答で欄が固定なので、そちらだけ `"stack": None` を明示する。
    """
    body = client.get("/api/media?stack=members").json()

    assert len(body["media"]) == 2
    for item in body["media"]:
        assert "stack" not in item


def test_collapse_wins_over_stack_members(client, canon_pair):
    """**両方来たら `collapse` が勝つ**（隠す）. 新設の 400 にしない —— 既存の
    呼び出し元が壊れる."""
    body = client.get("/api/media?collapse=stack&stack=members").json()

    names = [m["rel_path"].split("/")[-1] for m in body["media"]]
    assert "IMG_0001.CR2" not in names


def test_an_unknown_stack_value_is_a_bad_request(client):
    """`stack` は `members` だけ受け付ける."""
    response = client.get("/api/media?stack=bogus")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_stack_members_does_not_change_the_total(client, canon_pair):
    """**総数は変わらない。** 隠さないのだから、`collapse` 無しと同じ数."""
    plain = client.get("/api/media").json()["total"]

    assert client.get("/api/media?stack=members").json()["total"] == plain
```

> `test_an_unknown_stack_value_is_a_bad_request` の `error.code` の形は、
> 既にある `test_an_unknown_collapse_value_is_a_bad_request`（同ファイル
> `app/tests/test_api_media.py:123`）に合わせる。**そちらを読んでから書く。**

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_api_media.py -k "stack_members or collapse_wins or unknown_stack_value" -v
```

期待: `stack=members` が未知の引数として無視され、`stack` キーが無いので FAIL
（`test_an_unknown_stack_value_is_a_bad_request` は 200 が返って FAIL）。

- [ ] **Step 3: 最小実装**

`_KNOWN_COLLAPSE_VALUES` の下に足す。

```python
_KNOWN_STACK_VALUES = frozenset({"members"})
```

`list_media` の署名に `stack: str | None = None` を足し（`collapse` の隣）、
docstring に 1 段落足す。

```python
    `stack=members` は**従を隠さずに**、組に属する行すべてへ `stack` を付ける。
    送る画面が使う —— 送る対象は絞り込みが返した行そのもので、畳むと未送信の
    RAW が送られなくなる（`docs/history/phase12-design.md` の 2）。**`collapse=stack`
    と併せて来たら `collapse` が勝つ**（隠す）。片方だけを 400 にすると、既存の
    呼び出し元が壊れる。
```

検証と順位表の組み立てを直す。

```python
    if collapse is not None and collapse not in _KNOWN_COLLAPSE_VALUES:
        raise ApiError(400, ErrorCode.BAD_REQUEST, "collapse は stack だけ", {"collapse": collapse})
    if stack is not None and stack not in _KNOWN_STACK_VALUES:
        raise ApiError(400, ErrorCode.BAD_REQUEST, "stack は members だけ", {"stack": stack})
```

```python
    # **組を教えるのと、従を隠すのは別。** 隠すのは写真タブ（`collapse=stack`）
    # だけで、送る画面は隠されると未送信の RAW を送れなくなる。順位表はどちらでも
    # 要るので、先に 1 度だけ作る。
    ranks_sql = ""
    ranks_params: tuple[Any, ...] = ()
    prefix = ""
    prefix_params: tuple[Any, ...] = ()
    if collapse == "stack" or stack == "members":
        ranks_sql, ranks_params = _ranks(conn)
    if collapse == "stack" and ranks_sql:
        prefix = f"WITH rank(profile_id, extension, rank) AS (VALUES {ranks_sql}) "
        prefix_params = ranks_params
        # （ここから下は既存のまま。`_AMBIGUOUS_EXISTS` の長いコメントも残す）
        sibling_clause, sibling_params = _filters(
            "sm", kind, role, profile, captured_from, captured_to, q, destination_id, status
        )
        secondary = _secondary_exists_sql(sibling_clause or "1")
        hide_as_secondary = f"({secondary}) AND NOT ({_AMBIGUOUS_EXISTS})"
        exclude = f"NOT ({hide_as_secondary})"
        where = f"{where} AND {exclude}" if where else f"WHERE {exclude}"
        params = (*params, *sibling_params)
```

2 本のクエリの束縛変数を `ranks_params` から `prefix_params` へ替える。

```python
    total = conn.execute(
        f"{prefix}SELECT count(*) AS n FROM media_file m {where}",  # noqa: S608
        (*prefix_params, *params),
    ).fetchone()["n"]
    rows = conn.execute(
        f"{prefix}SELECT m.* FROM media_file m {where}"  # noqa: S608
        " ORDER BY m.captured_at DESC, m.id DESC LIMIT ? OFFSET ?",
        (*prefix_params, *params, limit, offset),
    )
```

> **`prefix_params` を分けるのが要点。** `_members_of` は自分で `WITH` を書くので
> `ranks_sql` / `ranks_params` を要るが、**主のクエリは `collapse` のときしか
> `WITH` を持たない**。同じ変数を使い回すと、`stack=members` のときに主のクエリへ
> 余分な束縛変数が積まれて `sqlite3.ProgrammingError` になる。

行ごとの組み立ては既に `if ranks_sql:` を見ているので**そのままでよい**
（`stack=members` でも `ranks_sql` が立つ）。

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_api_media.py -v
uv run pytest
```

- [ ] **Step 5: 費用を測る（設計 §8 の危険）**

`stack=members` は行ごとに 1 クエリ増える。**送る画面は最大 200 行**を引く。
Phase 9 で索引を入れ違えて他経路が退行した前例があるので、**測ってから進む**。

```bash
uv run python - <<'PY'
# 同じ DB に対して 3 つの形を各 20 回、経過時間の中央値を出す。
# 使う DB とデータの作り方は app/tests/conftest.py の client フィクスチャに合わせる。
PY
```

実装者への指示: **`app/tests/system/` にある実プロセスの起こし方**を使い、
canon-eos の合成カードを 200 行ぶん取り込んだうえで、

- `GET /media?page_size=200`
- `GET /media?page_size=200&stack=members`
- `GET /media?page_size=200&collapse=stack`

の 3 つを各 20 回叩き、**中央値をミリ秒で記録する**。

**判定:** `stack=members` が `collapse=stack` と同程度（±20%）なら進む。
**2 倍以上遅ければ止まって報告する** —— そのときは `_members_of` を行ごとではなく
1 本の SQL でまとめて引く形に落とす（設計 §8）。**測った値は必ず記録に残す**
（`phase9-record.md` の「測る対象が足りていなかった」教訓）。

- [ ] **Step 6: 型を作り直す**

```bash
npm --prefix web run typegen
```

- [ ] **Step 7: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `collapse == "stack" or stack == "members"` の `or` の右辺を消す | `test_stack_members_names_the_pair_without_hiding_the_raw` |
| `if collapse == "stack" and ranks_sql:` の `collapse == "stack" and` を消す | `test_stack_members_names_the_pair_without_hiding_the_raw`（隠れてしまう） |
| `prefix_params` を `ranks_params` に戻す | `test_stack_members_names_the_pair_without_hiding_the_raw`（束縛変数の数が合わない） |
| `_KNOWN_STACK_VALUES` を `frozenset({"members", "bogus"})` に | `test_an_unknown_stack_value_is_a_bad_request` |

- [ ] **Step 8: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/api/routes_media.py app/tests/test_api_media.py web/src/api/types.ts
git commit
```

---

## Task 3: 組の扱いを 1 か所に置く（`web/src/utils/stacks.ts`）

**ファイル:**
- 新規: `web/src/utils/stacks.ts`
- 新規: `web/src/utils/stacks.test.ts`

**インタフェース:**
- 使うもの: `StackMember`（`components/MediaTile.tsx` が既に export している）
- 出すもの:
  - `stackLabel(members: { rel_path: string }[]): string | null`
  - `groupIntoStacks<T extends StackRow>(rows: T[]): StackTile<T>[]`
  - `type StackTile<T> = { primary: T; rows: T[] }`
  - `type StackRow = { id: string; stack?: { members: StackMember[] } | null }`

**この関数が守る約束（設計 §6）:** 一覧・詳細・送るが**同じ組**を作る。
3 画面が別々の規則で組むと、どれか 1 つだけが古くなり、**画面によって枚数が違う**
という最悪の形になる。

- [ ] **Step 1: 失敗するテストを書く**

`web/src/utils/stacks.test.ts`:

```ts
// 組の扱い（まとめる・名乗る）。**3 画面が同じものを呼ぶ**ので、ここが唯一の規則。

import { describe, expect, it } from "vitest";

import { groupIntoStacks, stackLabel } from "./stacks";

const member = (id: string, rel_path: string, size_bytes = 100) => ({ id, rel_path, size_bytes });

const row = (id: string, rel_path: string, members?: { id: string; rel_path: string; size_bytes: number }[]) => ({
  id,
  rel_path,
  stack: members ? { members } : null,
});

describe("組の名乗り", () => {
  it("2 枚の組は 主の拡張子 + RAW", () => {
    expect(stackLabel([member("a", "x/IMG_1.JPG"), member("b", "x/IMG_1.CR2")])).toBe("JPG+RAW");
  });

  it("主が JPG でなくても、主の拡張子をそのまま名乗る", () => {
    // **画面が実在しないファイル名を名乗らない。** `stack.extensions` は
    // 利用者が編集できるので、主が HEIC の組がありうる。
    expect(stackLabel([member("a", "x/IMG_1.HEIC"), member("b", "x/IMG_1.DNG")])).toBe("HEIC+RAW");
  });

  it("主以外が 2 つ以上なら枚数を添える", () => {
    // **組の枚数を黙って隠さない。**
    expect(
      stackLabel([member("a", "x/I.JPG"), member("b", "x/I.CR2"), member("c", "x/I.HIF")]),
    ).toBe("JPG+RAW ×2");
  });

  it("1 枚では組にならないので名乗らない", () => {
    expect(stackLabel([member("a", "x/IMG_1.JPG")])).toBeNull();
    expect(stackLabel([])).toBeNull();
  });

  it("拡張子は大文字にそろえる", () => {
    expect(stackLabel([member("a", "x/IMG_1.jpg"), member("b", "x/IMG_1.cr2")])).toBe("JPG+RAW");
  });
});

describe("組にまとめる", () => {
  const jpeg = member("j", "x/IMG_1.JPG");
  const raw = member("r", "x/IMG_1.CR2");

  it("同じ組の 2 行を 1 タイルにし、主を先頭にする", () => {
    const tiles = groupIntoStacks([row("r", "x/IMG_1.CR2", [jpeg, raw]), row("j", "x/IMG_1.JPG", [jpeg, raw])]);

    expect(tiles).toHaveLength(1);
    expect(tiles[0].primary.id).toBe("j");
    expect(tiles[0].rows.map((r) => r.id)).toEqual(["j", "r"]);
  });

  it("**集合に来ていない相方は、タイルに入れない**", () => {
    // 送る画面は「返ってきた行」しか送らない。JPG が送信済みで CR2 だけ
    // 未送信のとき、members には JPG が居るが、送るのは CR2 だけ。
    const tiles = groupIntoStacks([row("r", "x/IMG_1.CR2", [jpeg, raw])]);

    expect(tiles).toHaveLength(1);
    expect(tiles[0].primary.id).toBe("r");
    expect(tiles[0].rows.map((r) => r.id)).toEqual(["r"]);
  });

  it("組でない行は単独のタイルになる", () => {
    const tiles = groupIntoStacks([row("a", "x/A.JPG"), row("b", "x/B.JPG")]);

    expect(tiles.map((t) => t.rows.map((r) => r.id))).toEqual([["a"], ["b"]]);
  });

  it("**並びは入力順を保つ**（最初に現れた行の位置にタイルを置く）", () => {
    // API の並び（captured_at DESC, id DESC）を崩すと、日付のまとまりが割れる。
    const tiles = groupIntoStacks([
      row("a", "x/A.JPG"),
      row("r", "x/IMG_1.CR2", [jpeg, raw]),
      row("j", "x/IMG_1.JPG", [jpeg, raw]),
      row("z", "x/Z.JPG"),
    ]);

    expect(tiles.map((t) => t.primary.id)).toEqual(["a", "j", "z"]);
  });

  it("別々の組が混ざらない", () => {
    const other = [member("p", "y/IMG_2.JPG"), member("q", "y/IMG_2.CR2")];
    const tiles = groupIntoStacks([
      row("j", "x/IMG_1.JPG", [jpeg, raw]),
      row("p", "y/IMG_2.JPG", other),
      row("r", "x/IMG_1.CR2", [jpeg, raw]),
      row("q", "y/IMG_2.CR2", other),
    ]);

    expect(tiles.map((t) => t.rows.map((r) => r.id))).toEqual([
      ["j", "r"],
      ["p", "q"],
    ]);
  });
});
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web test -- src/utils/stacks.test.ts
```

期待: `Failed to resolve import "./stacks"`。

- [ ] **Step 3: 最小実装**

`web/src/utils/stacks.ts`:

```ts
// 組（RAW+JPEG）の扱い。**一覧・詳細・送るの 3 画面がここだけを呼ぶ。**
//
// **3 画面が別々に組むと、画面によって枚数が違うことになる。** 数え方（枚数・
// 合計サイズ）はファイル単位、見え方（タイル・札）は組単位、という約束は
// 規約ではなくこの関数で守る。

import type { StackMember } from "../components/MediaTile";

/** 組にまとめられる行が満たすべき最小の形。 */
export type StackRow = { id: string; stack?: { members: StackMember[] } | null };

/** 1 タイル。**`rows` は「このタイルが表すファイル」**で、渡された集合に居るものだけ。 */
export type StackTile<T> = { primary: T; rows: T[] };

/** `rel_path` の拡張子（ドット無しの大文字）。 */
function extensionOf(relPath: string): string {
  const name = relPath.split("/").pop() ?? relPath;
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toUpperCase();
}

/**
 * 組の名乗り（`JPG+RAW`）。**1 枚では組にならない**ので `null`。
 *
 * **主の拡張子はファイル名から取り、相方は `RAW` と呼ぶ。** `stack.extensions` は
 * 利用者が編集できるので、主が HEIC の組がありうる —— 画面が実在しない
 * ファイル名を名乗らないための保険。相方が 2 つ以上あるときは枚数を添える
 * （**組の枚数を黙って隠さない**）。
 */
export function stackLabel(members: { rel_path: string }[]): string | null {
  if (members.length < 2) {
    return null;
  }
  const base = `${extensionOf(members[0].rel_path)}+RAW`;
  return members.length === 2 ? base : `${base} ×${members.length - 1}`;
}

/**
 * 行を組にまとめる。**渡された集合の中だけで組む。**
 *
 * 集合に来ていない相方はタイルに入れない —— 送る画面は「絞り込みが返した行」
 * しか送らないので、`stack.members` に居るだけの相方を数えると、送信済みの
 * ファイルを数え直すことになる（`docs/history/phase12-design.md` の 2）。
 *
 * **並びは入力順を保つ**（最初に現れた行の位置にタイルを置く）。API の並び
 * （`captured_at DESC, id DESC`）を崩すと、日付のまとまりが割れる。
 */
export function groupIntoStacks<T extends StackRow>(rows: T[]): StackTile<T>[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const tiles: StackTile<T>[] = [];
  const placed = new Set<string>();
  for (const row of rows) {
    if (placed.has(row.id)) {
      continue;
    }
    const members = row.stack?.members;
    if (members === undefined || members === null) {
      placed.add(row.id);
      tiles.push({ primary: row, rows: [row] });
      continue;
    }
    // **主は `stack.members` の並び**（先頭が主）のうち、集合に実際に居る先頭。
    const present = members
      .map((member) => byId.get(member.id))
      .filter((one): one is T => one !== undefined);
    const mine = present.length === 0 ? [row] : present;
    for (const one of mine) {
      placed.add(one.id);
    }
    tiles.push({ primary: mine[0], rows: mine });
  }
  return tiles;
}
```

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web test -- src/utils/stacks.test.ts
npm --prefix web run lint && npm --prefix web run typecheck
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `members.length < 2` を `< 1` に | 「1 枚では組にならないので名乗らない」 |
| `members.length === 2 ? base : …` の 2 を 3 に | 「主以外が 2 つ以上なら枚数を添える」 |
| `toUpperCase()` を消す | 「拡張子は大文字にそろえる」 |
| `.filter((one) => one !== undefined)` を消す | 「集合に来ていない相方は、タイルに入れない」 |
| `placed.add` を `mine[0]` だけにする | 「同じ組の 2 行を 1 タイルにし、主を先頭にする」（従が単独タイルで残る） |
| `tiles.push({ primary: mine[0] …})` を `primary: row` に | 「同じ組の 2 行を 1 タイルにし、主を先頭にする」 |

- [ ] **Step 6: コミット**

```bash
git add web/src/utils/stacks.ts web/src/utils/stacks.test.ts
git commit
```

---

## Task 4: タイルの札を `JPG+RAW` にする

**ファイル:**
- 変更: `web/src/components/MediaTile.tsx`
- 変更: `web/src/components/MediaTile.test.tsx`
- 変更: `web/src/styles.css`（`.madeof` の幅）

**インタフェース:**
- 使うもの: Task 3 の `stackLabel`
- 出すもの: **`MediaTile` の `media.stack.members` は「このタイルが表すファイル」**
  という不変条件。一覧では組の全員、送る画面では対象になっているぶんだけ。
  **この約束のおかげで、`MediaTile` に札を渡す props を足さずに済む。**

- [ ] **Step 1: 既存のテストを新しい仕様へ直し、1 本足す**

`web/src/components/MediaTile.test.tsx` には **`RAW` を当てている既存のテストが
3 本ある**（`grep -n "RAW" web/src/components/MediaTile.test.tsx` で確かめる）。
`renderTile` ヘルパが同ファイルの上にあるので、**それを使う**。

`RAW` を `JPG+RAW` に直す（2 か所）。

```tsx
  it("組なら JPG+RAW と名乗る", () => {
    // **`RAW` の 1 語では 2 枚あることが読めない**（利用者のレビュー）。
    renderTile({
      to: "/photos/m1",
      onToggle: vi.fn(),
      media: {
        ...media,
        stack: {
          members: [
            { id: "m1", rel_path: "library/dji-osmo/DCIM/A.JPG", size_bytes: 100 },
            { id: "m2", rel_path: "library/dji-osmo/DCIM/A.CR2", size_bytes: 200 },
          ],
        },
      },
    });
    expect(screen.getByText("JPG+RAW")).toBeInTheDocument();
  });
```

```tsx
    const badges = [...container.querySelectorAll(".madeofs > .madeof")];
    expect(badges.map((badge) => badge.textContent)).toEqual(["つないだ", "JPG+RAW"]);
```

「組でなければ RAW とは書かない」は `queryByText("RAW")` のままでよい（**`JPG+RAW`
も出ないこと**を見るために `queryByText(/RAW/)` へ広げる）。

```tsx
  it("組でなければ RAW とは書かない", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn() });
    expect(screen.queryByText(/RAW/)).toBeNull();
  });
```

そのうえで**新しく 1 本足す**。

```tsx
  it("タイルが 1 枚しか表さないなら札を出さない", () => {
    // 送る画面では、相方が送信済みで CR2 だけが対象になることがある。
    // **`media.stack.members` は「このタイルが表すファイル」**なので、1 つなら
    // 組ではない。
    renderTile({
      to: "/photos/m1",
      onToggle: vi.fn(),
      media: {
        ...media,
        stack: { members: [{ id: "m1", rel_path: "library/x/A.CR2", size_bytes: 200 }] },
      },
    });
    expect(screen.queryByText(/RAW/)).toBeNull();
  });
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web test -- src/components/MediaTile.test.tsx
```

期待: 「組なら JPG+RAW と名乗る」が `Unable to find an element with the text:
JPG+RAW` で、「つないだ動画が組でもあれば…」が `["つないだ", "RAW"]` との
不一致で、「タイルが 1 枚しか表さないなら…」が `RAW` を見つけて FAIL（3 本）。

- [ ] **Step 3: 最小実装**

`MediaTile.tsx` の import に足す。

```ts
import { stackLabel } from "../utils/stacks";
```

`inside` の中の札を差し替える。**コメントの「どちらかを捨てない」は残す。**

```tsx
  const name = fileName(media.rel_path);
  const hasDuration = media.kind === "video" && media.duration_seconds != null;
  // **札は「このタイルが表すファイル」から作る。** 一覧では組の全員、送る画面では
  // 対象になっているぶんだけ —— `media.stack.members` がその集合そのものである。
  const label = media.stack ? stackLabel(media.stack.members) : null;
  const inside = (
    <>
      <img src={`/api/media/${media.id}/thumbnail`} alt="" loading="lazy" className="tileimg" />
      {/* **「つないだ」と組の札は独立に立つ。** 出す条件が `role` と `stack` で
          別々なので、両方立つ行がありうる。同じ座標へ重ねると片方が読めなくなる
          ため、1 つの入れ物へ入れて横に並べる（`styles.css` の `.madeofs`）。
          **どちらかを捨てない** —— 「つないだ動画」と「組」は別の事実で、
          片方を隠すとタイルがその行を名乗り損ねる。 */}
      {(media.role === "derived" || label !== null) && (
        <span className="madeofs">
          {media.role === "derived" && <span className="madeof">つないだ</span>}
          {label !== null && <span className="madeof raw">{label}</span>}
        </span>
      )}
```

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web test -- src/components/MediaTile.test.tsx
npm --prefix web test
```

**他の画面のテストが `RAW` を当てていたら、そこも `JPG+RAW` に直す。**

```bash
grep -rn '"RAW"\|>RAW<\|/RAW/' web/src web/e2e
```

- [ ] **Step 5: 札がタイルからはみ出さないことを確かめる**

送る画面のタイルは 58px（`work/Send.tsx` のグリッドの `minmax(58px, 1fr)`）。
`RAW` の 3 文字から `JPG+RAW` の 7 文字へ伸びる。

```bash
npm --prefix web run dev
# ブラウザで /send を開き、組のタイルを 320px 幅で見る
```

はみ出すなら `web/src/styles.css` の `.madeof` を詰める。

```css
.madeof { font-size: 10.5px; color: var(--accent-ink);
  /* 既存の指定はそのまま。**組の札は文字が長いので、タイルの幅で切る** ——
     読めなくなるより、はみ出さない方を採る。 */
}
.madeofs { position: absolute; top: 7px; left: 7px; z-index: 1; display: flex; gap: 4px;
  max-width: calc(100% - 14px); }
.madeof { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

**読めなくなるくらい切れるなら、送る画面のタイルを `minmax(58px, 1fr)` から
`minmax(74px, 1fr)` へ広げる**（送る前に確かめる画面なので、そちらを優先する）。
どちらにしたかを、コミット本文に理由付きで書く。

- [ ] **Step 6: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `label !== null` を `media.stack != null` に | 「タイルが 1 枚しか表さないなら札を出さない」 |
| `media.role === "derived" \|\| label !== null` の `\|\|` の右辺を消す | 「組のタイルは JPG+RAW と名乗る」 |
| `{label}` を `RAW` の固定文字に | 「組のタイルは JPG+RAW と名乗る」 |

- [ ] **Step 7: 受け入れとコミット**

```bash
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run test:e2e ; pkill -f '\.venv/bin/python3 -m mediaferry'
git add web/src/components/MediaTile.tsx web/src/components/MediaTile.test.tsx web/src/styles.css
git commit
```

---

## Task 5: 一覧の日付見出しに「その日をまとめて選ぶ」丸

**ファイル:**
- 変更: `web/src/screens/Photos.tsx`
- 変更: `web/src/screens/Photos.test.tsx`

**インタフェース:**
- 使うもの: 既存の `selected: Map<string, number>`（id → size_bytes）
- 出すもの:
  - `membersOf(item: Media): { id: string; size_bytes: number }[]` —— 組なら
    members、組でなければその行 1 つ。**Task 6 も使う**
  - 日付見出しの `role="checkbox"`。アクセシブル名は `` `${group.label} をまとめて選ぶ` ``

- [ ] **Step 1: 失敗するテストを書く**

`web/src/screens/Photos.test.tsx` の `describe("写真の画面")` に足す。

```tsx
it("日付の丸で、その日を全部選ぶ", async () => {
  stubApi({
    "/media": {
      media: [
        media("a", "2026-08-18T15:12:00+09:00", { size_bytes: 100 }),
        media("b", "2026-08-18T14:03:00+09:00", { size_bytes: 200 }),
        media("c", "2026-08-17T09:12:00+09:00", { size_bytes: 400 }),
      ],
      total: 3,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  // `formatDate("2026-08-18…")` は `"2026年8月18日"`（`utils/formatDateTime.ts`）。
  const day = await screen.findByRole("checkbox", { name: "2026年8月18日 をまとめて選ぶ" });
  await userEvent.click(day);

  expect(await screen.findByText("2 件を選択中")).toBeInTheDocument();
  expect(screen.getByText(/合計 300 B/)).toBeInTheDocument();
});

it("全部選ばれている日の丸を押すと、その日を全部外す", async () => {
  // **押した結果が予想できる**ようにする（全 → 無、それ以外 → 全）。
  stubApi({
    "/media": {
      media: [media("a", "2026-08-18T15:12:00+09:00"), media("b", "2026-08-18T14:03:00+09:00")],
      total: 2,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  const day = await screen.findByRole("checkbox", { name: /をまとめて選ぶ/ });
  await userEvent.click(day);
  expect(day).toHaveAttribute("aria-checked", "true");
  await userEvent.click(day);

  expect(day).toHaveAttribute("aria-checked", "false");
  expect(screen.queryByText(/件を選択中/)).not.toBeInTheDocument();
});

it("一部だけ選ばれている日の丸は mixed になる", async () => {
  // **`aria-pressed` のボタンにしない。** 「一部」は押下状態では表せず、
  // 読み上げで全選択と区別が付かない。
  stubApi({
    "/media": {
      media: [media("a", "2026-08-18T15:12:00+09:00"), media("b", "2026-08-18T14:03:00+09:00")],
      total: 2,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "選ぶ：a.JPG" }));

  expect(screen.getByRole("checkbox", { name: /をまとめて選ぶ/ })).toHaveAttribute(
    "aria-checked",
    "mixed",
  );
});

it("日付の丸は、その日の組の相方も一緒に選ぶ", async () => {
  // **単位はタイル（＝組）。** タイル 1 つで 2 ファイルを表す。
  stubApi({
    "/media": {
      media: [
        media("j", "2026-08-18T15:12:00+09:00", {
          size_bytes: 100,
          stack: {
            members: [
              { id: "j", rel_path: "x/IMG_1.JPG", size_bytes: 100 },
              { id: "r", rel_path: "x/IMG_1.CR2", size_bytes: 900 },
            ],
          },
        }),
      ],
      total: 1,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("checkbox", { name: /をまとめて選ぶ/ }));

  expect(await screen.findByText("2 件を選択中")).toBeInTheDocument();
  expect(screen.getByText(/合計 1000 B/)).toBeInTheDocument();
});
```

> **`formatBytes` は 2 進の単位を出す**（`web/src/utils/formatBytes.ts`）。
> `formatBytes(300)` は `"300 B"`、`formatBytes(1000)` も **`"1000 B"`**（1024 未満は
> 繰り上がらない）。**`KB` と書かない。**

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web test -- src/screens/Photos.test.tsx
```

期待: `Unable to find an accessible element with the role "checkbox"` で FAIL。

- [ ] **Step 3: 最小実装**

`Photos.tsx` の `toggle` の**すぐ上**に足す。

```tsx
/** この行が表すファイル（組なら members、組でなければその行 1 つ）。 */
function membersOf(item: Media): { id: string; size_bytes: number }[] {
  return item.stack?.members ?? [{ id: item.id, size_bytes: item.size_bytes }];
}
```

`toggle` の中の同じ式を `membersOf(item)` に置き換える（**規則を 2 か所に書かない**）。

`PhotosScreen` の中に足す。

```tsx
  /** その日がどれだけ選ばれているか。**見えている行だけを数える。** */
  function dayState(items: Media[]): "all" | "some" | "none" {
    const ids = items.flatMap((item) => membersOf(item).map((member) => member.id));
    const on = ids.filter((id) => selected.has(id)).length;
    if (on === 0) {
      return "none";
    }
    return on === ids.length ? "all" : "some";
  }

  /**
   * その日をまとめて選ぶ／外す。**全部選ばれているときだけ外し、それ以外は選ぶ。**
   *
   * **触るのは、いま画面に並んでいる行だけ。** 絞り込みで隠れているぶんや次の
   * ページのぶんは触らない —— 見えていないものを選ぶ丸は、押した結果が
   * 確かめられない。
   */
  function toggleDay(items: Media[]) {
    const clearing = dayState(items) === "all";
    setSelected((current) => {
      const next = new Map(current);
      for (const item of items) {
        for (const member of membersOf(item)) {
          if (clearing) {
            next.delete(member.id);
          } else {
            next.set(member.id, member.size_bytes);
          }
        }
      }
      return next;
    });
  }
```

日付の見出しを差し替える。

```tsx
          <section key={group.label} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="sechead">
              {/* **`role="checkbox"` の 3 状態にする。** 「一部選ばれている」は
                  押下状態（`aria-pressed`）では表せず、読み上げで全選択と
                  区別が付かない。`mixed` はチェックボックスにしかない。 */}
              <button
                type="button"
                role="checkbox"
                className={`pick day${dayState(group.items) === "none" ? "" : " on"}`}
                aria-checked={
                  dayState(group.items) === "all"
                    ? "true"
                    : dayState(group.items) === "some"
                      ? "mixed"
                      : "false"
                }
                aria-label={`${group.label} をまとめて選ぶ`}
                onClick={() => toggleDay(group.items)}
              >
                {dayState(group.items) === "all" && <Icon name="check" size={12} />}
                {dayState(group.items) === "some" && <span className="dash" />}
              </button>
              <h2 style={{ fontSize: 14 }}>{group.label}</h2>
              <span className="small">{group.items.length} 件</span>
            </div>
```

`web/src/styles.css` に足す（`.pick` の下）。

```css
/* 日付の見出しの丸。タイルの丸（`.pick`）と同じ見た目で、位置だけ流し込みにする。 */
.pick.day { position: static; flex: 0 0 auto; }
.pick .dash { width: 8px; height: 2px; background: var(--accent-ink); border-radius: 1px;
  position: relative; z-index: 1; }
```

> `.pick` の既存の指定（`position: absolute` を含む）は `styles.css:268` にある。
> **`.pick.day` は上書きだけで済むか、実際に読んでから決める。** 済まないなら
> 共通部分を別のクラスへ切り出す（**タイルの丸の見た目を変えない**こと）。

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web test -- src/screens/Photos.test.tsx
npm --prefix web test
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `dayState` の `on === ids.length` を `on > 0` に | 「一部だけ選ばれている日の丸は mixed になる」 |
| `toggleDay` の `clearing` を常に `false` に | 「全部選ばれている日の丸を押すと、その日を全部外す」 |
| `membersOf` の `item.stack?.members ??` を消す | 「日付の丸は、その日の組の相方も一緒に選ぶ」 |
| `aria-checked` の `"mixed"` を `"false"` に | 「一部だけ選ばれている日の丸は mixed になる」 |

- [ ] **Step 6: 受け入れとコミット**

```bash
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run test:e2e ; pkill -f '\.venv/bin/python3 -m mediaferry'
git add web/src/screens/Photos.tsx web/src/screens/Photos.test.tsx web/src/styles.css
git commit
```

---

## Task 6: 一覧の Shift+クリックで範囲を選ぶ

**ファイル:**
- 変更: `web/src/components/MediaTile.tsx`（`onToggle` の型）
- 変更: `web/src/screens/Photos.tsx`
- 変更: `web/src/screens/Photos.test.tsx`
- 変更: `web/src/screens/work/*.tsx` のうち `onToggle` を渡している画面（**先に
  `grep -rn "onToggle" web/src` で全部数える**）

**インタフェース:**
- 使うもの: Task 5 の `membersOf`
- 出すもの: `MediaTile` の `onToggle?: (id: string, modifiers: { shift: boolean }) => void`

**なぜイベントそのものを渡さないか:** 呼ぶ側が `preventDefault` などを触れてしまう。
**部品が渡すのは「利用者が何を押したか」だけ**にする。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("Shift で、直前に押したものから範囲を選ぶ", async () => {
  stubApi({
    "/media": {
      media: [
        media("a", "2026-08-18T15:12:00+09:00"),
        media("b", "2026-08-18T14:03:00+09:00"),
        media("c", "2026-08-18T13:03:00+09:00"),
        media("d", "2026-08-18T12:03:00+09:00"),
      ],
      total: 4,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "選ぶ：a.JPG" }));
  await userEvent.keyboard("{Shift>}");
  await userEvent.click(screen.getByRole("button", { name: "選ぶ：c.JPG" }));
  await userEvent.keyboard("{/Shift}");

  expect(await screen.findByText("3 件を選択中")).toBeInTheDocument();
});

it("Shift の範囲は日付のまとまりをまたぐ", async () => {
  // **利用者が見ている並びは 1 本の流れ**で、まとまりは見出しにすぎない。
  stubApi({
    "/media": {
      media: [
        media("a", "2026-08-18T15:12:00+09:00"),
        media("b", "2026-08-17T14:03:00+09:00"),
        media("c", "2026-08-16T13:03:00+09:00"),
      ],
      total: 3,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "選ぶ：a.JPG" }));
  await userEvent.keyboard("{Shift>}");
  await userEvent.click(screen.getByRole("button", { name: "選ぶ：c.JPG" }));
  await userEvent.keyboard("{/Shift}");

  expect(await screen.findByText("3 件を選択中")).toBeInTheDocument();
});

it("Shift の範囲は選ぶ側に倒す（すでに選ばれているものを外さない）", async () => {
  stubApi({
    "/media": {
      media: [
        media("a", "2026-08-18T15:12:00+09:00"),
        media("b", "2026-08-18T14:03:00+09:00"),
        media("c", "2026-08-18T13:03:00+09:00"),
      ],
      total: 3,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "選ぶ：b.JPG" }));
  await userEvent.click(screen.getByRole("button", { name: "選ぶ：a.JPG" }));
  await userEvent.keyboard("{Shift>}");
  await userEvent.click(screen.getByRole("button", { name: "選ぶ：c.JPG" }));
  await userEvent.keyboard("{/Shift}");

  expect(await screen.findByText("3 件を選択中")).toBeInTheDocument();
});

it("アンカーが無ければ、Shift は 1 つだけ選ぶ", async () => {
  stubApi({
    "/media": {
      media: [media("a", "2026-08-18T15:12:00+09:00"), media("b", "2026-08-18T14:03:00+09:00")],
      total: 2,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  await userEvent.keyboard("{Shift>}");
  await userEvent.click(await screen.findByRole("button", { name: "選ぶ：b.JPG" }));
  await userEvent.keyboard("{/Shift}");

  expect(await screen.findByText("1 件を選択中")).toBeInTheDocument();
});

it("絞り込みを変えたらアンカーを捨てる", async () => {
  // **並びが変わったあとのアンカーは、利用者の見ていたものと違う範囲を指す。**
  // 選んだものは覚えたまま（Phase 7 の判断）でも、アンカーは並びに属する状態。
  const { calls } = stubApi({
    "/media": {
      media: [
        media("a", "2026-08-18T15:12:00+09:00"),
        media("b", "2026-08-18T14:03:00+09:00"),
        media("c", "2026-08-18T13:03:00+09:00"),
      ],
      total: 3,
      page: 1,
      page_size: 200,
    },
    "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
  });
  render(
    <MemoryRouter>
      <PhotosScreen />
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "選ぶ：a.JPG" }));
  await userEvent.click(screen.getByRole("button", { name: /^動画$/ }));
  await waitFor(() => expect(calls().some((c) => c.path.includes("kind=video"))).toBe(true));
  await userEvent.keyboard("{Shift>}");
  await userEvent.click(await screen.findByRole("button", { name: "選ぶ：c.JPG" }));
  await userEvent.keyboard("{/Shift}");

  // a（絞り込み前に選んだもの）は覚えたまま、c が 1 つ増えるだけ。b は入らない。
  expect(await screen.findByText("2 件を選択中")).toBeInTheDocument();
});
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web test -- src/screens/Photos.test.tsx
```

期待: 範囲のテストが「1 件を選択中」で FAIL（Shift が効かない）。

- [ ] **Step 3: 最小実装**

`MediaTile.tsx`:

```tsx
  /** 隅の丸を押したとき。**修飾キーは「利用者が何を押したか」だけを渡す**
   * —— イベントそのものを渡すと、呼ぶ側が `preventDefault` を触れてしまう。 */
  onToggle?: (id: string, modifiers: { shift: boolean }) => void;
```

```tsx
          onClick={(event) => onToggle(media.id, { shift: event.shiftKey })}
```

`Photos.tsx` に足す。

```tsx
  // **Shift の範囲の起点。** `selected` と違って、これは「並び」に属する状態
  // なので、並びが変われば無効になる（下の効果で捨てる）。
  const [anchor, setAnchor] = useState<string | null>(null);
```

`mediaQuery` を組み立てた後に足す。

```tsx
  // **並びが変わったらアンカーを捨てる。** `mediaQuery` は絞り込み・探している
  // 言葉・ページ・宛先をすべて含むので、これが変わることは並びが変わること。
  // 捨てないと、変わったあとのアンカーが利用者の見ていたものと違う範囲を指す。
  useEffect(() => {
    setAnchor(null);
  }, [mediaQuery]);
```

`toggle` を割る。

```tsx
  /** 1 タイルぶんを選ぶ／外す（**組は全員まとめて**）。 */
  function toggleOne(item: Media) {
    const members = membersOf(item);
    setSelected((current) => {
      const next = new Map(current);
      const allSelected = members.every((member) => next.has(member.id));
      for (const member of members) {
        if (allSelected) {
          next.delete(member.id);
        } else {
          next.set(member.id, member.size_bytes);
        }
      }
      return next;
    });
  }

  /**
   * アンカーから今回のタイルまでを**選ぶ**（外さない）。
   *
   * **選ぶ側に倒す。** アンカーの選択状態に合わせて外す作りもあるが、
   * 「シフトで選び、要らないものを個別に外す」の方が手数が少なく、押した
   * 結果が予想しやすい。
   *
   * **並びは `rows`**（API の `captured_at DESC, id DESC` そのまま）。日付の
   * まとまりはまたぐ —— 利用者が見ている並びは 1 本の流れで、まとまりは
   * 見出しにすぎない。
   */
  function selectRange(fromId: string, toId: string) {
    const from = rows.findIndex((row) => row.id === fromId);
    const to = rows.findIndex((row) => row.id === toId);
    if (from === -1 || to === -1) {
      return;
    }
    const span = rows.slice(Math.min(from, to), Math.max(from, to) + 1);
    setSelected((current) => {
      const next = new Map(current);
      for (const item of span) {
        for (const member of membersOf(item)) {
          next.set(member.id, member.size_bytes);
        }
      }
      return next;
    });
  }

  /**
   * **畳んだタイルは「1 枚（RAW+JPEG）」を表す**（`GET /media?collapse=stack`）。
   * 選ぶ丸も送るのもその単位に揃えないと、主（JPG）しか積まれず、相方（CR2）が
   * 送られないまま Immich でスタックが組まれない
   * （`docs/history/phase10-design.md` §4「選んで送る画面の契約はそのまま」）。
   */
  function toggle(item: Media, modifiers: { shift: boolean }) {
    if (modifiers.shift && anchor !== null) {
      selectRange(anchor, item.id);
    } else {
      toggleOne(item);
    }
    setAnchor(item.id);
  }
```

タイルの呼び出しを直す。

```tsx
                  onToggle={(_id, modifiers) => toggle(item, modifiers)}
```

**非テストの呼び出し元は `Photos.tsx:394` の 1 か所だけ**（実測済み。`work/` の
どの画面も `onToggle` を渡していない）。他を探しに行かなくてよい。

**既存のテストを 1 行直す。** `web/src/components/MediaTile.test.tsx` の
「押すと開き、選ぶのは隅の丸」が、引数を 1 つで当てている。

```tsx
    await userEvent.click(screen.getByRole("button", { name: "選ぶ：A.MP4" }));
    // **修飾キーは、押していなければ `false` で渡る。** ここでしか固定されない。
    expect(onToggle).toHaveBeenCalledWith("m1", { shift: false });
```

**この行を消して通さない。** 消すと「Shift を押していないときに `false` が渡る」を
守るものが 1 つも無くなる。

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web test -- src/screens/Photos.test.tsx
npm --prefix web test && npm --prefix web run typecheck
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `modifiers.shift && anchor !== null` の `anchor !== null` を消す | 「アンカーが無ければ、Shift は 1 つだけ選ぶ」（`selectRange(null, …)` で例外か無反応） |
| `selectRange` の `next.set` を `toggleOne` 相当（外しも起きる）に | 「Shift の範囲は選ぶ側に倒す」 |
| `setAnchor(item.id)` を `if (!modifiers.shift)` の中だけに | **落ちない可能性が高い。** 落ちなければ「Shift を続けて 2 回押すと、2 回目はどこからになるか」を固定するテストを足す |
| `useEffect(… [mediaQuery])` を消す | 「絞り込みを変えたらアンカーを捨てる」 |
| `rows.slice(Math.min…)` の `+ 1` を消す | 「Shift で、直前に押したものから範囲を選ぶ」（2 件になる） |

- [ ] **Step 6: 受け入れとコミット**

```bash
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run test:e2e ; pkill -f '\.venv/bin/python3 -m mediaferry'
git add web/src/components/MediaTile.tsx web/src/screens/Photos.tsx web/src/screens/Photos.test.tsx
git commit
```

---

## Task 7: 詳細に組を出し、選んだぶんだけ送る

**ファイル:**
- 変更: `web/src/screens/PhotoDetail.tsx`
- 変更: `web/src/screens/PhotoDetail.test.tsx`

**インタフェース:**
- 使うもの: Task 1 の `GET /media/{id}` の `stack`、`fileName`、`formatBytes`
- 出すもの: 「送る」が `navigate("/send", { state: { ids, destinationIds: [] } })` へ
  渡す `ids` が、**チェックの付いた member の id だけ**になる

**設計からの変更（1 件）:** 設計 §4 のモックは「ファイル名・大きさ・チェック」まで
だったが、**ファイル名はその member の `/photos/:id` へのリンクにする**。従（CR2）は
一覧に出ないので、リンクが無いとその 1 件の「宛先ごとの状況」や「消す」へ辿り着く
道が無くなる（§13 の「行き止まりにしない」）。**チェックとリンクは別の的**にする ——
タイルが「丸で選ぶ・絵で開く」と分けているのと同じ形（`MediaTile.tsx` の
「的を入れ子にしない」）。

- [ ] **Step 1: 失敗するテストを書く**

`PhotoDetail.test.tsx` の `detail()` ヘルパに `stack: null` を足し（既定は組でない）、
テストを足す。

```tsx
/** 組（`GET /media/{id}` の `stack`）。**主が先頭。** */
function aPair() {
  return {
    members: [
      { id: "m1", rel_path: "library/canon/IMG_1.JPG", size_bytes: 3_000_000 },
      { id: "m2", rel_path: "library/canon/IMG_1.CR2", size_bytes: 22_000_000 },
    ],
  };
}

it("組の中身をファイル名と大きさで出す", async () => {
  stubApi({ "/media/m1": detail({ role: "original", rel_path: "library/canon/IMG_1.JPG", stack: aPair() }) });
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  const section = (await screen.findByRole("heading", { name: "この 1 枚を作っているファイル" }))
    .closest("section") as HTMLElement;
  expect(within(section).getByText("IMG_1.JPG")).toBeInTheDocument();
  expect(within(section).getByText("IMG_1.CR2")).toBeInTheDocument();
  expect(within(section).getByText("21 MiB")).toBeInTheDocument();
});

it("既定では組の全部が選ばれている", async () => {
  // **一覧の丸が組ごとに選ぶのと揃える。** 外したい人だけが操作する。
  stubApi({ "/media/m1": detail({ role: "original", rel_path: "library/canon/IMG_1.JPG", stack: aPair() }) });
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByRole("checkbox", { name: "送る：IMG_1.JPG" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "送る：IMG_1.CR2" })).toBeChecked();
  expect(screen.getByText("2 枚 ・ 24 MiB を送ります")).toBeInTheDocument();
});

it("外した 1 枚は送るへ渡らない", async () => {
  stubApi({ "/media/m1": detail({ role: "original", rel_path: "library/canon/IMG_1.JPG", stack: aPair() }) });
  let passed: unknown = null;
  function Spy() {
    passed = useLocation().state;
    return <p>送る画面</p>;
  }
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
        <Route path="/send" element={<Spy />} />
      </Routes>
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("checkbox", { name: "送る：IMG_1.CR2" }));
  await userEvent.click(screen.getByRole("button", { name: "送る" }));

  expect(passed).toEqual({ ids: ["m1"], destinationIds: [] });
});

it("全部外したら送れない", async () => {
  stubApi({ "/media/m1": detail({ role: "original", rel_path: "library/canon/IMG_1.JPG", stack: aPair() }) });
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("checkbox", { name: "送る：IMG_1.JPG" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "送る：IMG_1.CR2" }));

  expect(screen.getByRole("button", { name: "送る" })).toBeDisabled();
});

it("組でない写真には、このセクションを出さない", async () => {
  stubApi({ "/media/m1": detail({ role: "original", stack: null }) });
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  await screen.findByRole("button", { name: "送る" });
  expect(screen.queryByRole("heading", { name: "この 1 枚を作っているファイル" })).not.toBeInTheDocument();
});

it("組でない写真の「送る」は、いままでどおり 1 件を渡す", async () => {
  stubApi({ "/media/m1": detail({ role: "original", stack: null }) });
  let passed: unknown = null;
  function Spy() {
    passed = useLocation().state;
    return <p>送る画面</p>;
  }
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
        <Route path="/send" element={<Spy />} />
      </Routes>
    </MemoryRouter>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "送る" }));

  expect(passed).toEqual({ ids: ["m1"], destinationIds: [] });
});

it("相方のファイル名から、その 1 件のくわしくへ行ける", async () => {
  // **行き止まりにしない**（§13）。従は一覧に出ないので、ここが唯一の道。
  stubApi({ "/media/m1": detail({ role: "original", rel_path: "library/canon/IMG_1.JPG", stack: aPair() }) });
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByRole("link", { name: "IMG_1.CR2" })).toHaveAttribute(
    "href",
    "/photos/m2",
  );
});
```

> **`formatBytes` は 2 進の単位を出す**（`B` / `KiB` / `MiB` / …）。
> `formatBytes(25_000_000)` は `"24 MiB"`、`formatBytes(22_000_000)` は `"21 MiB"`。
> **`MB` と書かない。**

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web test -- src/screens/PhotoDetail.test.tsx
```

- [ ] **Step 3: 最小実装**

`PhotoDetail.tsx` の `MediaDetail` 型に足す。

```ts
  /** この 1 件が属する組（RAW+JPEG。組でなければ `null`）。**主が先頭。** */
  stack: { members: { id: string; rel_path: string; size_bytes: number }[] } | null;
```

import に `useEffect` と `formatBytes`（既にある）を足す。状態を足す。

```tsx
  // 送るものとして選んでいる member。**組でなければ `null`**（この 1 件を送る）。
  // **既定は全部オン** —— 一覧の丸が組ごとに選ぶのと揃える。外したい人だけが
  // 操作する。送るまでの一時的な選択なので、URL には持たない。
  const [included, setIncluded] = useState<Set<string> | null>(null);
  const members = data?.stack?.members ?? null;
  // **id の並びで見る。** `members` は毎回新しい配列なので、そのまま依存に置くと
  // 描画のたびに選択が既定へ戻る。
  const memberKey = members === null ? "" : members.map((member) => member.id).join(",");
  useEffect(() => {
    setIncluded(memberKey === "" ? null : new Set(memberKey.split(",")));
  }, [memberKey]);
```

送るものを導く。

```tsx
  /** 「送る」へ渡す id。**組ならチェックの付いたぶんだけ**、組でなければこの 1 件。 */
  const sendingIds =
    data === null ? [] : included === null ? [data.id] : [...included];
  const sendingBytes =
    members === null
      ? (data?.size_bytes ?? 0)
      : members
          .filter((member) => included?.has(member.id))
          .reduce((sum, member) => sum + member.size_bytes, 0);
```

セクションを「宛先ごとの状況」の**すぐ上**に置く（何を送るかが先に読める）。

```tsx
          {members !== null && (
            <section className="card pad">
              <div className="sechead" style={{ marginBottom: 12 }}>
                <h2>この 1 枚を作っているファイル</h2>
              </div>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
                {members.map((member) => (
                  // **選ぶ的と開く的を分ける**（タイルの「丸で選ぶ・絵で開く」と
                  // 同じ形）。従は一覧に出ないので、名前からその 1 件へ行けないと
                  // 「宛先ごとの状況」も「消す」も届かなくなる（§13）。
                  <li key={member.id} className="row">
                    <input
                      type="checkbox"
                      checked={included?.has(member.id) ?? false}
                      aria-label={`送る：${fileName(member.rel_path)}`}
                      onChange={() =>
                        setIncluded((current) => {
                          const next = new Set(current ?? []);
                          if (next.has(member.id)) {
                            next.delete(member.id);
                          } else {
                            next.add(member.id);
                          }
                          return next;
                        })
                      }
                    />
                    <Link to={`/photos/${member.id}`} className="ident grow">
                      {fileName(member.rel_path)}
                    </Link>
                    <span className="small">{formatBytes(member.size_bytes)}</span>
                  </li>
                ))}
              </ul>
              <p className="small" style={{ marginTop: 10 }}>
                {sendingIds.length} 枚 ・ {formatBytes(sendingBytes)} を送ります
              </p>
            </section>
          )}
```

「送る」ボタンを直す。

```tsx
            <button
              type="button"
              className="btn primary"
              disabled={sendingIds.length === 0}
              onClick={() =>
                navigate("/send", {
                  state: { ids: sendingIds, destinationIds: [] },
                })
              }
            >
              送る
            </button>
```

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web test -- src/screens/PhotoDetail.test.tsx
npm --prefix web test && npm --prefix web run typecheck
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `new Set(memberKey.split(","))` を `new Set()` に | 「既定では組の全部が選ばれている」 |
| `included === null ? [data.id] : [...included]` の三項を `[data.id]` に | 「外した 1 枚は送るへ渡らない」 |
| `disabled={sendingIds.length === 0}` を消す | 「全部外したら送れない」 |
| `members !== null &&` を `data.stack !== undefined &&` に | 「組でない写真には、このセクションを出さない」 |
| `useEffect` の依存を `[]` に | **落ちない可能性が高い**（1 画面 1 件しか開かないため）。落ちなければ「別の写真へ移ったときに選択が引き継がれないこと」のテストを足す |

- [ ] **Step 6: 受け入れとコミット**

```bash
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run test:e2e ; pkill -f '\.venv/bin/python3 -m mediaferry'
git add web/src/screens/PhotoDetail.tsx web/src/screens/PhotoDetail.test.tsx
git commit
```

---

## Task 8: 送る画面が、返ってきた集合の中だけで組む

**ファイル:**
- 変更: `web/src/screens/work/Send.tsx`
- 変更: `web/src/screens/work/Send.test.tsx`

**インタフェース:**
- 使うもの: Task 2 の `stack=members`、Task 3 の `groupIntoStacks`、Task 4 の
  「`media.stack.members` はこのタイルが表すファイル」という不変条件

**件数と合計サイズは変わらない。** `targetMedia` はいまもファイル単位で、
`targetMedia.length` と `totalBytes` はそのまま使える —— 利用者の言う「2 枚カウント」は
**すでに満たされている**。このタスクで変わるのは**タイルの並べ方と札**だけ。

- [ ] **Step 1: 失敗するテストを書く**

`Send.test.tsx` に足す。**既存のテストの `stubApi` の形と `Media` の作り方を先に
読む**（同ファイル冒頭）。

```tsx
it("両方が対象なら 1 タイルにまとめ、JPG+RAW と名乗る", async () => {
  // **一覧で 1 タイルだったものが、送る画面で 2 つに割れて戻らない。**
  const pair = [
    { id: "j", rel_path: "x/IMG_1.JPG", size_bytes: 3_000_000 },
    { id: "r", rel_path: "x/IMG_1.CR2", size_bytes: 22_000_000 },
  ];
  stubApi({
    "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    "/media/j": { ...pair[0], kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", stack: { members: pair } },
    "/media/r": { ...pair[1], kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", stack: { members: pair } },
  });
  render(
    <MemoryRouter initialEntries={[{ pathname: "/send", state: { ids: ["j", "r"], destinationIds: [] } }]}>
      <Routes>
        <Route path="/send" element={<SendScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByText("JPG+RAW")).toBeInTheDocument();
  expect(screen.getAllByRole("img", { name: /IMG_1/ })).toHaveLength(1);
  // **件数はファイル数のまま**（利用者の「2 枚カウント」）。
  expect(screen.getByText(/2 件 ・/)).toBeInTheDocument();
});

it("相方が対象でなければ、単独のタイルで札も出さない", async () => {
  // JPG は前回すでに送った。CR2 だけが未送信 —— 送るのは CR2 の 1 枚。
  const pair = [
    { id: "j", rel_path: "x/IMG_1.JPG", size_bytes: 3_000_000 },
    { id: "r", rel_path: "x/IMG_1.CR2", size_bytes: 22_000_000 },
  ];
  stubApi({
    "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    "/media/r": { ...pair[1], kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", stack: { members: pair } },
  });
  render(
    <MemoryRouter initialEntries={[{ pathname: "/send", state: { ids: ["r"], destinationIds: [] } }]}>
      <Routes>
        <Route path="/send" element={<SendScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  await screen.findByRole("img", { name: "IMG_1.CR2" });
  expect(screen.queryByText(/RAW/)).not.toBeInTheDocument();
  expect(screen.getByText(/1 件 ・/)).toBeInTheDocument();
});

it("「まだ送っていないもの」も組を教えてもらって引く", async () => {
  // **畳ませない**（`collapse=stack`）。畳むと未送信の RAW が送られなくなる。
  const { calls } = stubApi({
    "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    "/media": { media: [], total: 0, page: 1, page_size: 200 },
  });
  render(
    <MemoryRouter initialEntries={[{ pathname: "/send", state: null }]}>
      <Routes>
        <Route path="/send" element={<SendScreen />} />
      </Routes>
    </MemoryRouter>,
  );

  await waitFor(() => {
    const asked = calls().filter((c) => c.path.startsWith("/media?"));
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((c) => c.path.includes("stack=members"))).toBe(true);
    expect(asked.every((c) => !c.path.includes("collapse="))).toBe(true);
  });
});
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web test -- src/screens/work/Send.test.tsx
```

- [ ] **Step 3: 最小実装**

import に足す。

```ts
import { groupIntoStacks } from "../../utils/stacks";
```

`unsentPages` のクエリに 1 行足す。

```ts
          const query = new URLSearchParams({
            destination_id: destinationId,
            status: "unsent",
            page_size: String(PAGE_SIZE),
            // **組を教えてもらうが、畳ませない。** 畳むと未送信の RAW が一覧から
            // 消え、送られないまま Immich でスタックが組まれない
            // （`docs/history/phase12-design.md` の 2）。
            stack: "members",
          });
```

タイルの組み立てを足す（`totalBytes` の下）。

```tsx
  // **タイルは組単位、数はファイル単位。** 返ってきた行の集合の中だけで組む
  // ので、相方が対象でなければ単独のタイルになる。
  const tiles = useMemo(() => groupIntoStacks(targetMedia), [targetMedia]);
  const shown = tiles.slice(0, 16);
  const shownFiles = shown.reduce((count, tile) => count + tile.rows.length, 0);
```

「送るもの」の節を差し替える。

```tsx
        <div className="sechead" style={{ marginBottom: 11 }}>
          <h2 style={{ fontSize: "14.5px" }}>送るもの</h2>
          <span className="small">
            {targetMedia.length} 件のうち、はじめの {shownFiles} 件
          </span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(58px, 1fr))",
            gap: 7,
          }}
        >
          {shown.map((tile) => (
            // **タイルの `stack.members` は「このタイルが表すファイル」。**
            // 一覧では組の全員だが、ここでは対象になっているぶんだけ ——
            // 相方が送信済みなら、札を出さずに 1 枚として並べる。
            <MediaTile
              key={tile.primary.id}
              media={{
                ...tile.primary,
                stack: {
                  members: tile.rows.map((row) => ({
                    id: row.id,
                    rel_path: row.rel_path,
                    size_bytes: row.size_bytes,
                  })),
                },
              }}
              selected={false}
            />
          ))}
        </div>
```

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web test -- src/screens/work/Send.test.tsx
npm --prefix web test && npm --prefix web run typecheck
```

**既存のテストが「タイルが 2 つ」を固定していたら、それは古い仕様なので直す。**
直したことと理由を、コミット本文に書く（`phase7-record.md` の「テストが思い違いを
仕様として固定していた」）。

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `stack: "members"` を消す | 「まだ送っていないものも組を教えてもらって引く」 |
| `stack: "members"` を `collapse: "stack"` に | 同上 |
| `tile.rows.map(…)` を `tile.primary.stack?.members ?? []` に | 「相方が対象でなければ、単独のタイルで札も出さない」 |
| `groupIntoStacks(targetMedia)` を `targetMedia.map(m => ({primary: m, rows: [m]}))` に | 「両方が対象なら 1 タイルにまとめ、JPG+RAW と名乗る」 |
| `shownFiles` を `shown.length` に | 「両方が対象なら…」の件数の行 —— **落ちなければ**、はじめの N 件の数え方を固定するテストを足す |

- [ ] **Step 6: 受け入れとコミット**

```bash
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run test:e2e ; pkill -f '\.venv/bin/python3 -m mediaferry'
git add web/src/screens/work/Send.tsx web/src/screens/work/Send.test.tsx
git commit
```

---

## Task 9: 受け入れ（E2E）と記録

**ファイル:**
- 変更: `web/e2e/phase6.spec.ts`（RAW+JPEG の動線が既にここにある）
- 新規: `docs/history/phase12-record.md`
- 変更: `docs/history/README.md`（`phase12-plan.md` と `phase12-record.md` の行）

**インタフェース:**
- 使うもの: Task 1〜8 のすべて

**なぜ E2E に入れるか:** 受け入れの経路に入っていない機能は、無いのと同じ
（`docs/development.md`）。Phase 8 では E2E が受け入れコマンドに入っておらず、
**8 タスクぶん赤のまま進んだ**。

- [ ] **Step 1: 失敗するテストを書く**

`web/e2e/phase6.spec.ts` の 3 の節（写真タブ）を直し、4 の前に節を足す。

```ts
  // 3. 写真に RAW+JPEG が並ぶまで待つ。**写真タブは組を畳むので、選ぶ丸は
  //    IMG_0003 につき 1 つだけ**（CR2 の行は一覧に出ない）。畳まれた行には
  //    `JPG+RAW` の札が付く（**2 枚あることが札から読める**）。
  await nav.getByRole("link", { name: "写真" }).click();
  const combined = page.getByRole("button", { name: "選ぶ：IMG_0003.JPG" });
  await expect(combined).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: /^選ぶ：IMG_0003\.(JPG|CR2)$/ })).toHaveCount(1);
  await expect(page.locator(".tile", { has: combined }).getByText("JPG+RAW")).toBeVisible();

  // 3b. **日付の丸で、その日をまとめて選べる。** 押すと組の相方も一緒に入る。
  const day = page.getByRole("checkbox", { name: /をまとめて選ぶ/ }).first();
  await day.click();
  await expect(day).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText(/件を選択中/)).toBeVisible();
  await page.getByRole("button", { name: "やめる" }).click();

  // 3c. **詳細でも組が見える。** 一覧で見えたものが、押した先で消えない。
  await page.locator(".tile", { has: combined }).getByRole("link", { name: "IMG_0003.JPG" }).click();
  await expect(page.getByRole("heading", { name: "この 1 枚を作っているファイル" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "送る：IMG_0003.JPG" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "送る：IMG_0003.CR2" })).toBeChecked();
  await expect(page.getByText(/2 枚 ・ .* を送ります/)).toBeVisible();
  await nav.getByRole("link", { name: "写真" }).click();
```

4 の節（送る画面）に足す。

```ts
  await expect(page.getByRole("heading", { name: "Immich へ送る" })).toBeVisible();
  // **送る画面でも 1 タイル・JPG+RAW。** 一覧で 1 枚だったものが割れて戻らない。
  await expect(page.getByText("JPG+RAW")).toBeVisible();
  // 送り先が 1 つしか無ければ、黙ってそれを使う。
  await expect(page.getByText("送り先：immich-1")).toBeVisible();
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web run test:e2e -- phase6
pkill -f '\.venv/bin/python3 -m mediaferry'
```

**Task 1〜8 が入っていれば通るはず。** ここで落ちたら、落ちた画面のタスクへ戻る
（E2E は単体では見えない抜けを捕まえる）。

- [ ] **Step 3: E2E をすべて回す**

```bash
npm --prefix web run test:e2e
pkill -f '\.venv/bin/python3 -m mediaferry'
ps aux | grep -c '[m]ediaferry'   # 0 になっていること
```

**`journey.spec.ts` と `phase5.spec.ts` も緑であること。** 札が `RAW` から
`JPG+RAW` へ伸びたぶん、狭い画面のはみ出しを見ている test（「カードの中身が、
カードの箱の外に出ていない」など）が落ちうる。落ちたら Task 4 の Step 5 へ戻る。

- [ ] **Step 4: 記録を書く**

`docs/history/phase12-record.md` に、**この 9 タスクを回して分かったこと**を書く。
`phase10-record.md` / `phase11-record.md` と同じ節立てにする。

- 各タスクの巡数と、そこで見つかったもの
- **変異試験の記録** —— 当てた数、生き残った数、**検出できなかったものそれぞれの
  理由**（構造的に到達しないのか、テストが足りないのか）
- **計画の誤り** —— この計画が外したところと、どう直したか
- **Task 2 Step 5 で測った値**（3 つの形の中央値、ミリ秒）
- **既存の網が 5 件のレビューを 1 件も捕まえなかった理由**と、足した網

- [ ] **Step 5: 索引を直す**

`docs/history/README.md` の表に 2 行足す（`phase12-design.md` の行は既にある）。

```
| `phase12-plan.md` | Phase 12 の実装計画。9 タスクのブリーフ、`stack=members` を足す SQL の割り方（`prefix_params` を `ranks_params` と分ける理由）、`groupIntoStacks` が「渡された集合の中だけで組む」規則、日付の丸を `role="checkbox"` にする理由、E2E を `phase6.spec.ts` に足した理由 |
| `phase12-record.md` | **Phase 12 をどう回したか**。… |
```

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
npm --prefix web test && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run test:e2e ; pkill -f '\.venv/bin/python3 -m mediaferry'
git add web/e2e/phase6.spec.ts docs/history/phase12-record.md docs/history/README.md
git commit
```

---

## この計画の自己点検

**設計の網羅（`phase12-design.md` の各節 → タスク）:**

| 設計の節 | タスク |
| --- | --- |
| 1. 「一覧だけ」を反転する（3 画面で同じ 1 枚） | 4, 7, 8（約束は 3 の関数が守る） |
| 2. 送る画面は API で畳んではいけない / `stack=members` | 2, 8 |
| 3. 日付の全選択と Shift の範囲 | 5, 6 |
| 4. 詳細に組を出して選ぶ | 1, 7 |
| 5. ラベルを `JPG+RAW` に | 3, 4 |
| 6. 数え方を 1 か所に | 3 |
| 7. 触らないもの | 全タスク（`resolve_group` / 第 2 パス / `collapse` の意味 / ホーム / DB に触れる手順が 1 つも無い） |
| 8. 危険（費用の実測、`collapse` と `stack` の併用） | 2（Step 5 と Step 1 のテスト） |
| 9. 試験の方針 | 各タスクの Step 1 と Step 5、Task 9 |
| 10. 採らなかった案 | —（実装の対象ではない） |

**設計からの変更（1 件）:** Task 7 で、組の行のファイル名を**リンクにした**。
設計 §4 のモックはチェックと名前と大きさだけだったが、従（CR2）は一覧に出ないので、
リンクが無いとその 1 件の「宛先ごとの状況」と「消す」へ辿り着けなくなる
（§13 の「行き止まりにしない」）。

**型と名前の一貫性（タスクをまたいで同じものを同じ名で呼んでいるか）:**

| 名前 | 定義 | 使う側 |
| --- | --- | --- |
| `_ranks(conn)` | Task 1 | Task 1（`_stack_of`）、Task 2（`list_media`） |
| `_stack_json(member_rows)` | Task 1 | Task 1（`_stack_of` と `list_media`） |
| `stackLabel(members)` | Task 3 | Task 4 |
| `groupIntoStacks(rows)` | Task 3 | Task 8 |
| `membersOf(item)` | Task 5 | Task 5（`toggleDay`）、Task 6（`toggleOne` / `selectRange`） |
| `onToggle(id, modifiers)` | Task 6 | Task 6（`Photos.tsx`）、`onToggle` を渡す他の画面 |
| `media.stack.members` は「このタイルが表すファイル」 | Task 4 | Task 8 |
