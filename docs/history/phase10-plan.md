# Phase 10 の実装計画 —— 送る前に「どれとどれが 1 枚か」を見せる

> **実装する人へ:** この計画は 1 タスクずつ進める。各タスクは**失敗するテストを
> 書き、失敗を確認してから**最小実装する（`CLAUDE.md`）。変異試験を省かない。

**目標:** 写真タブで RAW+JPEG を 1 タイルに見せ、`RAW` の札を出す。組の判定は
カード上の事実だけで決まり、Immich で実際に作る組と同じ関数が決める。

**設計の正本:** [`phase10-design.md`](phase10-design.md)。**この計画は設計から
論を借りているので、両方を読む。**

**触る層:** Python 3.14 / SQLite / FastAPI / React + TypeScript / Playwright

## 全体の制約

- `from __future__ import annotations` で始める。コメントと docstring は日本語
- ruff（`line-length = 100`、`E,F,I,UP,B,SIM,ANN,S`）。`docs/` は対象外
- **環境固有の値をコードにもテストにも書かない**
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付ける。`git checkout` を使わない**
  （scratchpad に控えを取ってから壊す）
- 受け入れコマンドは 5 つ全部:
  `uv run pytest` / `uv run ruff check .` / `uv run ruff format --check .` /
  `npm --prefix web run test -- --run` / `npm --prefix web run test:e2e`

## codex のレビューで直した点（2026-08-25）

**blocker 2 件・major 2 件が計画に対して出た。** 全部こちらで裏を取って受け入れた。

| | 何が壊れていたか | どう直したか |
| --- | --- | --- |
| B | 証拠を捨てる引き金が `if not same` で、この `same` は `quick_fingerprint`（サイズと 16 窓）の**確率的**判定。標本窓の外だけが変わった同サイズのファイルは `same=True` のまま印を持ち越し、**新しい JPG が古い RAW と組む** | **引き金を構造的なものに変える** —— `media_file_id` を NULL にするとき（＝この行がもう前の media_file を代表しないと決めたとき）に消す。指紋の強さに依らない |
| A | 「同じ拡張子の相方が 2 つ」は**到達する**。`iter_media_files` は `{ext.upper()}` で突き合わせるので `IMG_0001.JPG` と `IMG_0001.jpg` が同じ正規化拡張子になる | **曖昧なら組まない。** 利用者が最初に選んだ「組まずに送り、画面に理由を出す」を実装する。一覧も畳まない |
| —— | `_touch` 経路で `extension` を埋めないので、**移行対象そのもの**（既存の published で同内容の行）が従判定されない | `_touch` でも埋める（Task 4） |
| —— | `_members_of` が現行の `stack` 規則で絞らないので、`extensions` を変えたときに `identity_partners` と食い違う | 順位表へ join して現行対象の拡張子だけに限る（Task 6） |

**B の直し方が効く理由。** 危ないのは「撮り直したファイルが新しい `media_file` として
取り込まれ、古い相方の行が `published` のまま残る」場合だけ。そのとき行は必ず
UPDATE 経路を通って `media_file_id = NULL` になる。**そこで消せば取りこぼしが無い。**
一方 `_touch`（`published` のまま変わっていない）経路は印を保つので、「一度証明された
同席は消えない」も守られる。`seen` / `failed` の行で消えても害が無い —— まだ
`media_file` が無く、相方が居るなら同じスキャンの `_mark_copresence` が書き直す。

## 設計からの変更（実装で分かったこと）

**同席の印を「スキャンの `job_id` だけ」にすると足りない。** 1 回のスキャンは
カード上のすべての組に印を書くので、**別々の組どうしが同じ印を持つ**。一覧の
SQL は「同じ印なら同じ組」と読むので、無関係な写真が 1 タイルに畳まれる。

**印は `<job_id>:<stem_prefix>` にする。** これで印の等しさが「同じスキャンで、
同じ stem の下で、同時に見えた」をそのまま表す。SQL は等値比較 1 つで済み、
`rel_path` から stem を切り出す文字列操作が要らなくなる。

**併せて `source_entry.extension` を持つ。** 「自分より順位が上の相方が居るか」を
SQL で見るのに要る。`rel_path` から SQL で拡張子を切り出すのは読めない式になる。
**既存の行は両方 NULL のまま**（設計の「無いものを在ったことにしない」）。

---

## ファイルの地図

| ファイル | 役目 |
| --- | --- |
| `app/src/mediaferry/core/profiles/model.py` | `StackRule` から `tolerance_seconds` を落とす |
| `app/src/mediaferry/core/profiles/builtin/canon-eos.yaml` | 同上 |
| `app/src/mediaferry/core/uploads/stacking.py` | **身元**（純粋関数）と資格を割る |
| `app/src/mediaferry/db/migrations/0024_source_entry_copresence.sql` | 列を 2 つ足す |
| `app/src/mediaferry/jobs/scan.py` | 同席の印を書く／中身が変わったら消す／`extension` を書く |
| `app/src/mediaferry/db/uploads.py` | `siblings_on_card` が印を返す |
| `app/src/mediaferry/jobs/stacker.py` | `Candidate` に印を載せる |
| `app/src/mediaferry/core/listing.py` | 拡張子の順位表を組み立てる（新規の関数） |
| `app/src/mediaferry/api/routes_media.py` | `collapse=stack` |
| `web/src/components/MediaTile.tsx` | `RAW` の札 |
| `web/src/screens/Photos.tsx` | `collapse=stack` を渡す |
| `web/src/styles.css` | 札の位置 |

---

## Task 1: `StackRule` から `tolerance_seconds` を落とす

**ファイル:**
- 変更: `app/src/mediaferry/core/profiles/model.py:94-109`, `:360-381`
- 変更: `app/src/mediaferry/core/profiles/builtin/canon-eos.yaml`
- 試験: `app/tests/test_profile_model.py`

**この先が使うもの:** `StackRule(enabled: bool, extensions: tuple[str, ...])`

**危険:** **既存リビジョンの `definition_json` には `tolerance_seconds` が入っている。**
`_reject_unknown` の許す集合から外すと `ProfileInvalid` で**適用済みの DB が開けなく
なる**（`0005` を版を足さずに書き換えて blocker になった前例がある）。**キーは
許したまま、値を読まない。**

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_profile_model.py の末尾に足す
def test_an_old_definition_with_a_tolerance_is_still_readable():
    """既存リビジョンの JSON には `tolerance_seconds` が入っている.

    弾くと、適用済みの DB のリビジョンを 1 つも開けなくなる。読み飛ばす。
    """
    defn = parse_definition(
        a_definition(stack={"enabled": True, "extensions": ["JPG", "CR2"], "tolerance_seconds": 5})
    )
    assert defn.stack.extensions == ("JPG", "CR2")
    assert not hasattr(defn.stack, "tolerance_seconds")
```

`a_definition` は同ファイルに既にあるヘルパ。無ければ `test_profile_model.py:200`
付近の作り方に合わせる。

- [ ] **Step 2: 落ちることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_profile_model.py -k tolerance_is_still_readable -v`
期待: FAIL（`hasattr` が True）

- [ ] **Step 3: 最小の実装**

```python
# model.py
@dataclass(frozen=True)
class StackRule:
    """RAW+JPEG の組の規則（§6）.

    `extensions` は**先頭ほど primary**。**撮影時刻は見ない** —— 組の身元は
    カード上の原名と同席の証拠で決まる（`docs/history/phase10-design.md`）。
    """

    enabled: bool
    extensions: tuple[str, ...]


STACK_DISABLED = StackRule(enabled=False, extensions=())
```

```python
# model.py の _parse_stack
def _parse_stack(data: Mapping[str, Any], scan: ScanRule) -> StackRule:
    # **`tolerance_seconds` は許すが読まない。** 既存リビジョンの
    # `definition_json` に入っているので、弾くと適用済みの DB を開けなくなる。
    _reject_unknown(data, {"enabled", "extensions", "tolerance_seconds"}, "stack")
    if not _bool(data, "enabled"):
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
    return StackRule(enabled=True, extensions=extensions)
```

`canon-eos.yaml` の `stack:` から `tolerance_seconds: 0` の行と、その上の
「**実カードを見ていないので緩めない**」のコメントを消し、代わりにこう書く。

```yaml
stack:
  # RAW+JPEG の同時記録。**先頭が primary**（Immich の一覧で代表になる方）。
  # **撮影時刻は見ない。** 組の身元はカード上の原名と同席の証拠で決まる。
  enabled: true
  extensions: ["JPG", "CR2"]
```

- [ ] **Step 4: 通ることを確かめ、巻き添えを直す**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/ -q`

`tolerance_seconds=` を渡している既存テストが落ちる。**`StackRule(...)` の呼び出しから
引数を消すだけ**にする（`test_stacking_rules.py:20`, `test_stacker.py:671`,
`test_profile_registry.py:118,144,276`）。`test_profile_model.py:229` の
`assert defn.stack.tolerance_seconds == 0` は消す。`test_api.py:203` と
`test_profile_registry.py:32,175` は「定義 JSON が変わると版が進む」ことの試験なので、
**別の値の書き換えに差し替える**（例: `'"tag_pre_existing":true'` → `'"tag_pre_existing":false'`）。

`test_profile_model.py:279` の `"tolerance_seconds": -1` は、値を読まなくなったので
`ProfileInvalid` にならない。**このテストは消す**（守るものが無くなった）。

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/core/profiles/ app/tests/
git commit -m "refactor(stack): 組の規則から tolerance_seconds を落とす"
```

---

## Task 2: 身元を純粋関数として切り出す

**ファイル:**
- 変更: `app/src/mediaferry/core/uploads/stacking.py`
- 試験: `app/tests/test_stacking_rules.py`

**この先が使うもの:**
`identity_partners(primary: Candidate, candidates: Sequence[Candidate], rule: StackRule) -> Identity`
—— 身元だけで相方を決める。資格（`origin` / `remote_asset_id` / `state` / `profile_id`）は見ない。

```python
@dataclass(frozen=True)
class Identity:
    """身元だけで決まる組。**曖昧さを潰さずに返す。**"""

    partners: tuple[Candidate, ...]
    # 同じ鍵に**同じ正規化拡張子**が 2 つ以上ある。`iter_media_files` は
    # `{ext.upper()}` で突き合わせるので、case-sensitive な FS では
    # `IMG_0001.JPG` と `IMG_0001.jpg` がこれになる。**自動では決められない。**
    ambiguous: bool
```

**曖昧なら組まない。** `resolve_group` は `Refusal("同じ拡張子の相方が複数ある。
自動では決められない")` を返し、一覧も畳まない（Task 6）。**「どちらかを選ぶ」を
機械にやらせない** —— 利用者の判断（送信は止めず、理由を画面に出す）。

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_stacking_rules.py の末尾に足す
def test_the_identity_does_not_look_at_the_time():
    """**組の身元は時刻で決まらない。**

    一括で日時を入れ直すと JPG だけ書き換わって CR2 が元のままになりうる
    （RAW に書ける道具の方が少ない）。時刻で切ると 1 組も組めなくなる。
    """
    late = replace(a_cr2(), captured_at="2026-08-17T14:31:00+00:00")

    identity = identity_partners(a_jpg(), [a_jpg(), late], RULE)

    assert [c.rel_path for c in identity.partners] == [late.rel_path]


def test_the_identity_does_not_look_at_where_the_time_came_from():
    """`exifread` が JPG は読めて CR2 は読めなくても、組は同じ 1 枚である."""
    by_mtime = replace(a_cr2(), captured_at_source="mtime")

    identity = identity_partners(a_jpg(), [a_jpg(), by_mtime], RULE)

    assert [c.rel_path for c in identity.partners] == [by_mtime.rel_path]


def test_the_identity_needs_the_same_card_and_stem():
    """別のカードの同じ名前は相方ではない（連番は一周する）."""
    other_card = replace(a_cr2(), volume_instance_id="another")

    assert identity_partners(a_jpg(), [a_jpg(), other_card], RULE).partners == ()
```

`a_jpg()` / `a_cr2()` / `RULE` は同ファイルに既にある。`replace` は
`dataclasses.replace`（既に import 済み）。

- [ ] **Step 2: 落ちることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stacking_rules.py -k identity -v`
期待: FAIL（`identity_partners` が無い＝`ImportError`）

- [ ] **Step 3: 最小の実装**

`stacking.py` の `resolve_group` から相方の抽出を切り出し、`_refused` から時刻の
2 条件を消す。

```python
def identity_partners(
    primary: Candidate, candidates: Sequence[Candidate], rule: StackRule
) -> list[Candidate]:
    """**身元だけ**で相方を返す（カード上の事実。宛先を見ない）.

    一覧（送る前）と第 2 パス（送った後）の両方がこれを呼ぶ。**予測と事実を
    別の関数で決めない**（`docs/history/phase10-design.md`）。
    """
    if not rule.enabled:
        return []
    if extension_of(primary.rel_path) not in rule.extensions:
        return []
    keys = {c.source_key for c in candidates if c.media_file_id == primary.media_file_id}
    partners: list[Candidate] = []
    seen: set[str] = set()
    by_extension: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.media_file_id == primary.media_file_id:
            continue
        # **鍵は組で比べる**（同じカードの、同じディレクトリの、同じ stem）。
        if candidate.source_key not in keys:
            continue
        extension = extension_of(candidate.rel_path)
        if extension not in rule.extensions:
            continue
        # **同じ資産を 2 回送らない。** 1 つの media_file が複数の観測で候補に入る。
        if candidate.media_file_id in seen:
            continue
        seen.add(candidate.media_file_id)
        by_extension.setdefault(extension, set()).add(candidate.media_file_id)
        partners.append(candidate)
    # **1 つの拡張子に 2 つ以上の media_file が来たら曖昧。** 自分の拡張子も数える
    # （自分と同じ拡張子の別ファイルが相方に来る場合がある）。
    by_extension.setdefault(extension_of(primary.rel_path), set()).add(primary.media_file_id)
    ambiguous = any(len(ids) > 1 for ids in by_extension.values())
    return Identity(partners=tuple(partners), ambiguous=ambiguous)
```

**曖昧さを試験する 1 本も同じ Step で書く。**

```python
def test_two_partners_with_the_same_extension_are_ambiguous():
    """`iter_media_files` は拡張子を大文字化して突き合わせるので、
    case-sensitive な FS では `IMG_0001.JPG` と `IMG_0001.jpg` が同じ拡張子になる。
    **どちらが相方かは機械には決められない。**
    """
    # **stem は `a_jpg()` の既定に合わせる。** 合わないと `source_key` の門で
    # 先に落ちて、曖昧さの判定に届かない（`ambiguous` が常に False になる）。
    lower = replace(a_jpg(), media_file_id="other", rel_path="DCIM/100CANON/IMG_1234.jpg")

    assert identity_partners(a_cr2(), [a_cr2(), a_jpg(), lower], RULE).ambiguous
```

**primary 自身が衝突する側の 1 つになる場合も、別に 1 本書く。** 上のテストは
*相方どうし*の衝突しか見ておらず、「自分と同じ拡張子の別ファイルが相方に来る」
という設計意図そのものを守らない。

```python
def test_a_partner_sharing_the_primarys_extension_is_ambiguous():
    """primary 自身も曖昧さの数え上げに入る."""
    lower = replace(a_jpg(), media_file_id="other", rel_path="DCIM/100CANON/IMG_1234.jpg")

    assert identity_partners(a_jpg(), [a_jpg(), lower], RULE).ambiguous
```

**`identity_partners` を無効な規則で直接呼ぶテストも 1 本書く。** `resolve_group`
にも同じ門があるので、経由すると隠れて検出できない。**Task 6 の一覧側は
`resolve_group` を経由しない**ので、この門が単独で効く入口ができる。

```python
def test_a_profile_without_stacking_has_no_partners():
    disabled = StackRule(enabled=False, extensions=("JPG", "CR2"))

    assert identity_partners(a_jpg(), [a_jpg(), a_cr2()], disabled).partners == ()
```

```python
def resolve_group(
    primary: Candidate, candidates: Sequence[Candidate], rule: StackRule
) -> Group | Refusal:
    """身元で相方を決め、**資格**を確かめる. 同じ組はどの member から呼んでも同じ."""
    if not rule.enabled:
        return Refusal("カメラの種類がスタックを使わない")
    if extension_of(primary.rel_path) not in rule.extensions:
        return Refusal("この拡張子は組の対象ではない")
    identity = identity_partners(primary, candidates, rule)
    if identity.ambiguous:
        # **「どちらかを選ぶ」を機械にやらせない。** 送信は止めず、理由を出す。
        return Refusal("同じ拡張子の相方が複数ある。自動では決められない")
    partners = list(identity.partners)
    if not partners:
        return Refusal("相方が見つからない")
    refusal = _refused(primary, partners, rule)
    if refusal is not None:
        return refusal
    members = sorted(
        [primary, *partners], key=lambda c: rule.extensions.index(extension_of(c.rel_path))
    )
    return Group(members=tuple(members))
```

`_refused` の末尾から次の 2 つを消す（`_within` も、使われなくなるので消す）。

```python
        if partner.captured_at_source != primary.captured_at_source:
            return Refusal("相方と時刻の根拠が違う（EXIF と mtime を突き合わせない）")
        if not _within(primary.captured_at, partner.captured_at, rule.tolerance_seconds):
            return Refusal("相方と撮影時刻が一致しない")
```

`Candidate` の `captured_at` / `captured_at_source` は**残す**（第 2 パスの
`fix_datetime_after_upload` が使う）。

- [ ] **Step 4: 通ることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stacking_rules.py app/tests/test_stacker.py -q`

時刻の条件を試験していた既存テスト（`test_stacking_rules.py:147,153` 付近の
`tolerance_seconds=2` を使うもの）は**消す**。守るものが無くなった。

- [ ] **Step 5: 変異試験**

scratchpad に `stacking.py` の控えを取り、**1 つずつ壊して**対応するテストが落ちる
ことを確かめる。壊すもの: `rule.enabled` の門 / `source_key not in keys` /
`extension_of(...) not in rule.extensions` / `media_file_id in seen` の重複除け。
**検出できなかったものは記録に残す。**

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core/uploads/stacking.py app/tests/test_stacking_rules.py app/tests/test_stacker.py
git commit -m "refactor(stack): 組の身元を純粋関数へ切り出し、時刻の 2 条件を外す"
```

---

## Task 3: `0024` —— 同席の印と拡張子の列

**ファイル:**
- 新規: `app/src/mediaferry/db/migrations/0024_source_entry_copresence.sql`
- 試験: `app/tests/test_schema_sources.py`

**この先が使うもの:** `source_entry.copresent_key TEXT`（NULL 可）、
`source_entry.extension TEXT`（NULL 可）

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_schema_sources.py の末尾に足す
def test_source_entry_carries_the_copresence_of_a_stack(db):
    """**同席の印は「どのスキャンで、どの stem の下で同時に見えたか」.**

    スキャンの id だけだと、1 回のスキャンが書いた**別々の組が同じ印**になる。
    """
    columns = {r["name"] for r in db.execute("PRAGMA table_info(source_entry)")}
    assert {"copresent_key", "extension"} <= columns


def test_existing_rows_have_no_copresence(db):
    """**無いものを在ったことにしない。** 過去に同席したかは記録に無い."""
    row = db.execute("PRAGMA table_info(source_entry)").fetchall()
    by_name = {r["name"]: r for r in row}
    assert by_name["copresent_key"]["dflt_value"] is None
    assert by_name["copresent_key"]["notnull"] == 0
```

- [ ] **Step 2: 落ちることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_schema_sources.py -k copresence -v`
期待: FAIL（列が無い）

- [ ] **Step 3: マイグレーションを書く**

```sql
-- 0024_source_entry_copresence.sql
--
-- 組の身元に「同席の証拠」を足す（docs/history/phase10-design.md）。
--
-- **印は <scan の job_id>:<stem prefix>。** スキャンの id だけにすると、1 回の
-- スキャンが書いた別々の組が同じ印になり、一覧が無関係な写真を 1 タイルに畳む。
-- prefix を含めることで、印の等しさが「同じスキャンで、同じ stem の下で、同時に
-- 見えた」をそのまま表す。SQL 側で rel_path から stem を切り出さずに済む。
--
-- **既存の行は NULL のまま。** 過去に同席したかどうかは記録に無いので、埋められ
-- ない。既存のライブラリは次にそのカードをスキャンしたときに印が付く。
ALTER TABLE source_entry ADD COLUMN copresent_key TEXT;

-- 「自分より順位が上の相方が居るか」を SQL で見るのに要る。rel_path から SQL で
-- 拡張子を切り出すと読めない式になる。**書くのはスキャンで、既存の行は NULL。**
ALTER TABLE source_entry ADD COLUMN extension TEXT;

-- 一覧の従外しが引く経路（同じカードの、同じ印の、別のメディア）。
CREATE INDEX source_entry_copresent
    ON source_entry (volume_instance_id, copresent_key)
    WHERE copresent_key IS NOT NULL;
```

- [ ] **Step 4: 通ることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_schema_sources.py -q`

`GET /api/health` の `schema_version` が 24 を返すことも確かめる:
`PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/ -k schema_version -q`

- [ ] **Step 5: コミット**

```bash
git add app/src/mediaferry/db/migrations/0024_source_entry_copresence.sql app/tests/test_schema_sources.py
git commit -m "feat(db): source_entry に同席の印と拡張子を足す（0024）"
```

---

## Task 4: スキャンが同席の印を書く

**ファイル:**
- 変更: `app/src/mediaferry/jobs/scan.py`
- 試験: `app/tests/test_scanner.py`

**消費するもの:** `source_entry.copresent_key` / `.extension`（Task 3）、
`stem_prefix` / `extension_of`（`core/uploads/stacking.py`）

**この先が使うもの:** 完走したスキャンの後、同席した行の `copresent_key` が
`f"{ctx.job_id}:{stem_prefix(rel_path)}"` になっている

**注意:** テストの fixture は `dji-osmo`（`stack` 無効）なので、このタスクの
テストは `canon-eos` のプロファイルとカードを使う fixture を新しく作る。

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_scanner.py の末尾に足す
@pytest.fixture
def canon_scanning(db, tmp_path):
    """`stack` が有効なプロファイルと、RAW+JPEG が並ぶカード."""
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("canon-eos")
    store = JobStore(db)
    store.enqueue("scan", {})
    ctx = store.claim_next()
    card = tmp_path / "canon"
    (card / "DCIM" / "100CANON").mkdir(parents=True)
    (card / "DCIM" / "100CANON" / "IMG_0001.JPG").write_bytes(b"j" * 100)
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").write_bytes(b"r" * 200)
    fd = os.open(card, os.O_RDONLY | os.O_DIRECTORY)
    volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
    yield Scanner(db), ctx, fd, volume_id, profile, card
    os.close(fd)


def _keys(db) -> dict[str, str | None]:
    return {
        r["rel_path"]: r["copresent_key"]
        for r in db.execute("SELECT rel_path, copresent_key FROM source_entry")
    }


def test_two_files_seen_together_get_the_same_mark(canon_scanning, db):
    """**同席の証拠。** 同じスキャンで、同じ stem の下に 2 つ見えたときだけ書く."""
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning

    scanner.scan(ctx, fd, volume_id, profile)

    keys = _keys(db)
    expected = f"{ctx.job_id}:DCIM/100CANON/IMG_0001."
    assert keys["DCIM/100CANON/IMG_0001.JPG"] == expected
    assert keys["DCIM/100CANON/IMG_0001.CR2"] == expected


def test_a_lone_file_gets_no_mark(canon_scanning, db):
    """相方が居なければ同席していない。**印を書かない。**"""
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()

    scanner.scan(ctx, fd, volume_id, profile)

    assert _keys(db)["DCIM/100CANON/IMG_0001.JPG"] is None


def test_different_stems_do_not_share_a_mark(canon_scanning, db):
    """**印はスキャンごとではなく組ごと。**

    スキャンの id だけにすると、1 回のスキャンが書いた別々の組が同じ印になり、
    一覧が無関係な写真を 1 タイルに畳む。
    """
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    (card / "DCIM" / "100CANON" / "IMG_0002.JPG").write_bytes(b"a" * 100)
    (card / "DCIM" / "100CANON" / "IMG_0002.CR2").write_bytes(b"b" * 200)

    scanner.scan(ctx, fd, volume_id, profile)

    keys = _keys(db)
    assert keys["DCIM/100CANON/IMG_0001.JPG"] != keys["DCIM/100CANON/IMG_0002.JPG"]


def test_a_mark_survives_the_partner_leaving_the_card(canon_scanning, db):
    """**一度証明された同席は消えない。** 送信は取り込みよりずっと後になりうる."""
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    scanner.scan(ctx, fd, volume_id, profile)
    before = _keys(db)["DCIM/100CANON/IMG_0001.JPG"]
    db.execute("UPDATE source_entry SET state = 'published'")
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()

    scanner.scan(ctx, fd, volume_id, profile)

    assert _keys(db)["DCIM/100CANON/IMG_0001.JPG"] == before


def test_a_changed_file_loses_its_mark(canon_scanning, db):
    """中身が変わった行は、前の中身での同席を引き継がない.

    引き継ぐと、撮り直した JPG が**無関係な古い RAW と組む**
    （`docs/history/phase10-design.md` の 3）。
    """
    scanner, ctx, fd, volume_id, profile, card = canon_scanning
    scanner.scan(ctx, fd, volume_id, profile)
    db.execute("UPDATE source_entry SET state = 'published'")
    (card / "DCIM" / "100CANON" / "IMG_0001.CR2").unlink()
    (card / "DCIM" / "100CANON" / "IMG_0001.JPG").write_bytes(b"z" * 100)

    scanner.scan(ctx, fd, volume_id, profile)

    assert _keys(db)["DCIM/100CANON/IMG_0001.JPG"] is None


def test_a_cancelled_scan_writes_no_mark(canon_scanning, db):
    """途中で降りたスキャンは、見ていないだけの相方を「居なかった」と読む."""
    scanner, ctx, fd, volume_id, profile, _ = canon_scanning
    JobStore(db).request_cancel(ctx.job_id)

    scanner.scan(ctx, fd, volume_id, profile)

    assert all(v is None for v in _keys(db).values())
```

- [ ] **Step 2: 落ちることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_scanner.py -k "mark or copresen" -v`
期待: FAIL（`copresent_key` が常に None）

- [ ] **Step 3: 最小の実装**

`scan.py` に import を足す。

```python
from ..core.uploads.stacking import extension_of, stem_prefix
```

`scan` の中で、`stack.extensions` に当たるものだけ集める。

```python
        started = now_iso()
        total = new = imported = ambiguous = vanished = 0
        counted = True
        # 同席の証拠を書く対象。**組の対象になる拡張子だけ**集める。
        eligible: list[str] = []
        for found in iter_media_files(dirfd, defn.scan.roots, defn.scan.extensions):
            ...
            ctx.emit("info", f"{found.rel_path}: {verdict}", {"size_bytes": found.size_bytes})
            if defn.stack.enabled and extension_of(found.rel_path) in defn.stack.extensions:
                eligible.append(found.rel_path)
        if counted:
            vanished = self._sweep_vanished(ctx, dirfd, volume_instance_id, started)
            self._mark_copresence(ctx, volume_instance_id, eligible)
            mark_scanned(self._conn, volume_instance_id)
```

```python
    def _mark_copresence(
        self, ctx: JobContext, volume_instance_id: str, eligible: list[str]
    ) -> None:
        """同じ stem の下で 2 つ以上見えた行に、同じ印を書く.

        **印は `<job_id>:<stem prefix>`。** スキャンの id だけにすると、1 回の
        スキャンが書いた別々の組が同じ印になる（一覧が無関係な写真を畳む）。

        **拡張子が 2 種類以上あることを要求する。** 大小文字違い（`IMG_0001.JPG` と
        `IMG_0001.jpg`）は同じ正規化拡張子になるので、条件を「2 件以上」と書くと
        その 2 つを組と読んでしまう。組めるかは `identity_partners` が `ambiguous`
        として決める。
        """
        by_prefix: dict[str, list[str]] = {}
        for rel_path in eligible:
            by_prefix.setdefault(stem_prefix(rel_path), []).append(rel_path)
        for prefix, paths in by_prefix.items():
            if len({extension_of(path) for path in paths}) < 2:
                continue
            for rel_path in paths:
                # **1 行ごとに fsync が起きる**（接続は `synchronous = FULL` の
                # autocommit）。1488 件のカードでは `_sweep_vanished` と同じ規模に
                # なるので、同じように心拍を打ち続ける。
                ctx.heartbeat()
                self._conn.execute(
                    "UPDATE source_entry SET copresent_key = ?"
                    " WHERE volume_instance_id = ? AND rel_path = ?",
                    (f"{ctx.job_id}:{prefix}", volume_instance_id, rel_path),
                )
```

`_reconcile_entry` の INSERT に `extension` を足す（列と値を 1 つずつ増やす）。

```python
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
```

既存の UPDATE 経路にも `extension = ?` を足し（値は `extension_of(found.rel_path)`）、
**その直後に、中身が変わったときだけ印を消す**。

```python
既存の UPDATE 経路（`media_file_id = NULL` を書いている文）に
`copresent_key = NULL` を**同じ UPDATE の中で**足す。

```python
        self._conn.execute(
            "UPDATE source_entry SET size_bytes = ?, mtime_ns = ?, quick_fingerprint = ?,"
            " fingerprint_version = ?, state = 'seen', media_file_id = NULL,"
            # **`media_file_id` を外すのと同じ拍で証拠も外す。** この行がもう前の
            # media_file を代表しないと決めた瞬間だから。**`same` では判断しない**
            # —— quick_fingerprint はサイズと 16 窓しか見ない確率的な判定で、
            # 標本窓の外だけが変わったファイルを取りこぼす（core/fingerprint.py の
            # docstring）。取りこぼすと、撮り直した JPG が古い RAW と組む。
            " copresent_key = NULL, extension = ?, observed_at = ?"
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
```

**`_touch` 経路（`published` のまま変わっていない）では消さない。** 「一度証明された
同席は消えない」はここで守られる。`seen` / `failed` の行で消えても害は無い ——
まだ `media_file` が無く、相方が居るなら同じスキャンの `_mark_copresence` が書き直す。

**`_touch` でも `extension` を埋める。** 埋めないと、**移行対象そのもの**（既存の
`published` で内容も mtime も変わっていない行）が NULL のままになり、Task 6 の
`rank` への join が外れて従判定されない。

```python
    def _touch(self, entry_id: str, extension: str) -> None:
        self._conn.execute(
            "UPDATE source_entry SET observed_at = ?, extension = ? WHERE id = ?",
            (now_iso(), extension, entry_id),
        )
```
```

- [ ] **Step 4: 通ることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_scanner.py -q`
期待: 全部 PASS（Task 4 より前のテストも含めて）

- [ ] **Step 5: 変異試験**

**このタスクのテストは、書いた本数では足りない。** 実測では下の 5 つのうち 4 つが
ブリーフ付属のテストでは検出できなかった（`counted` の門・`< 2` の閾値・`same` での
分岐・`_touch` の `extension`）。**変異を当ててから、区別できるテストを足すこと。**

壊すもの: `counted` の門 / `< 2` の閾値 / 印から `prefix` を落として `job_id` だけに
する / `if not same` を落として常に消す / `if not same` を落として一度も消さない。
**5 つとも落ちるテストがあることを確かめる。**

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/jobs/scan.py app/tests/test_scanner.py
git commit -m "feat(scan): 同席した行に印を書き、中身が変わったら消す"
```

---

## Task 5: 身元が同席の印を要求する

**ファイル:**
- 変更: `app/src/mediaferry/db/uploads.py:888-897`（`siblings_on_card`）
- 変更: `app/src/mediaferry/jobs/stacker.py:223-247`（`_candidate_of`）
- 変更: `app/src/mediaferry/core/uploads/stacking.py`（`Candidate` と `identity_partners`）
- 試験: `app/tests/test_stacking_rules.py`, `app/tests/test_stacker.py`

**消費するもの:** `identity_partners`（Task 2）、`copresent_key`（Task 3・4）

**この先が使うもの:** `Candidate.copresent_key: str | None`

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_stacking_rules.py の末尾に足す
def test_a_partner_without_proof_of_copresence_is_not_a_partner():
    """**同席の証拠が無ければ組まない。**

    片方だけ撮り直すと、古い published な行が相方として残る。鍵だけで組むと
    新しい JPG が無関係な古い RAW と組む（`phase10-design.md` の 3）。
    """
    stale = replace(a_cr2(), copresent_key=None)

    assert identity_partners(a_jpg(), [a_jpg(), stale], RULE).partners == ()


def test_a_partner_from_another_copresence_is_not_a_partner():
    """別の機会に同席した相手とは組まない."""
    other = replace(a_cr2(), copresent_key="job2:DCIM/100CANON/IMG_0001.")

    assert identity_partners(a_jpg(), [a_jpg(), other], RULE).partners == ()


def test_the_primary_without_proof_has_no_partner():
    """自分の側に証拠が無ければ、相方が持っていても組まない."""
    mine = replace(a_jpg(), copresent_key=None)

    assert identity_partners(mine, [mine, a_cr2()], RULE).partners == ()
```

`a_jpg()` / `a_cr2()` に `copresent_key="job1:DCIM/100CANON/IMG_0001."` を足す
（同じ値にする）。

- [ ] **Step 2: 落ちることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stacking_rules.py -k copresence -v`
期待: FAIL（`Candidate` に `copresent_key` が無い＝`TypeError`）

- [ ] **Step 3: 最小の実装**

```python
# stacking.py の Candidate に足す
    copresent_key: str | None
```

```python
# identity_partners の中、keys を作る行の直後に足す
    # **鍵ごとの同席の証拠。** 1 つの鍵の下に自分の行は 1 つしか無い
    # （UNIQUE (volume_instance_id, rel_path)）ので、対応は一意に決まる。
    proofs = {
        c.source_key: c.copresent_key
        for c in candidates
        if c.media_file_id == primary.media_file_id
    }
```

```python
# ループの中、extension の門の直後（`seen` の重複除けより前）に足す
        proof = proofs.get(candidate.source_key)
        # **「同じ時点でカードに在った」を要求する。** 鍵だけでは、片方だけ
        # 撮り直したときに古い published な行と組む。**曖昧さの数え上げより
        # 前で落とす** —— 証拠の無い相手は相方候補ですらない。
        if proof is None or candidate.copresent_key != proof:
            continue
```

```python
# db/uploads.py の siblings_on_card
                "SELECT rel_path, media_file_id, copresent_key FROM source_entry"
```

```python
# jobs/stacker.py の _candidate_of の Candidate(...) に足す
            copresent_key=sibling["copresent_key"],
```

- [ ] **Step 4: 通ることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_stacking_rules.py app/tests/test_stacker.py -q`

`test_stacker.py` の既存テストは `source_entry` を直接作っているものがある。
**組が成立することを期待しているテストには `copresent_key` を同じ値で入れる。**
入れ忘れると「相方が見つからない」で落ちるので、失敗の文言から辿れる。

- [ ] **Step 5: 変異試験**

壊すもの: `proof is None` の門 / `candidate.copresent_key != proof` の比較 /
`proofs` を `source_key` ごとではなく 1 つの集合に潰す。

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/core/uploads/stacking.py app/src/mediaferry/db/uploads.py app/src/mediaferry/jobs/stacker.py app/tests/
git commit -m "feat(stack): 組の身元に同席の証拠を要求する"
```

---

## Task 6: `GET /media?collapse=stack`

**ファイル:**
- 変更: `app/src/mediaferry/core/listing.py`（順位表を組む関数を足す）
- 変更: `app/src/mediaferry/api/routes_media.py:26-64`
- 試験: `app/tests/test_api_media.py`（無ければ `app/tests/test_api.py`）

**消費するもの:** `copresent_key` / `extension`（Task 3・4）、`ProfileRegistry.all()`

**この先が使うもの:** 応答の行に `stack: {"members": [{"id", "rel_path", "size_bytes"}]}`
（組でない行には**キーごと出さない**）

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_api_media.py（新規なら先頭に conftest の client を使う import を書く）
def test_collapsing_hides_the_raw_and_names_it_on_the_jpeg(client, canon_pair):
    """**1 行 = 1 タイル。** 従を隠し、主に組の中身を付ける."""
    body = client.get("/api/media?collapse=stack").json()

    paths = [m["rel_path"] for m in body["media"]]
    assert [p for p in paths if p.endswith(".CR2")] == []
    jpeg = next(m for m in body["media"] if m["rel_path"].endswith(".JPG"))
    assert [m["rel_path"].split("/")[-1] for m in jpeg["stack"]["members"]] == [
        "IMG_0001.JPG",
        "IMG_0001.CR2",
    ]


def test_collapsing_counts_only_what_it_shows(client, canon_pair):
    """`total` が見える件数と食い違うと、ページ送りが空のページを作る."""
    body = client.get("/api/media?collapse=stack").json()

    assert body["total"] == len(body["media"])


def test_without_collapsing_both_files_are_listed(client, canon_pair):
    """**既定は畳まない。** 選んで送る画面とホームの契約を変えない."""
    paths = [m["rel_path"] for m in client.get("/api/media").json()["media"]]

    assert len([p for p in paths if p.endswith(".CR2")]) == 1


def test_a_profile_without_stacking_is_never_hidden(client, canon_pair, dji_media):
    """`stack.enabled` が false のプロファイルの行は 1 つも外れない."""
    paths = [m["rel_path"] for m in client.get("/api/media?collapse=stack").json()["media"]]

    assert any("dji-osmo" in p for p in paths)


def test_a_file_without_proof_is_not_collapsed(client, canon_pair_without_proof):
    """同席の証拠が無ければ組ではない。**2 タイルのまま出す。**"""
    paths = [m["rel_path"] for m in client.get("/api/media?collapse=stack").json()["media"]]

    assert len([p for p in paths if p.endswith(".CR2")]) == 1
```

fixture は `app/tests/conftest.py` に足す。`canon_pair` は `canon-eos` の
`media_file` を 2 行と、`copresent_key` が同じ `source_entry` を 2 行作る。
`canon_pair_without_proof` は `copresent_key` を NULL にしたもの。`dji_media` は
既存の `client` fixture が作る DJI の 1 件をそのまま使う。

- [ ] **Step 2: 落ちることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_api_media.py -v`
期待: FAIL（`collapse` が未知の引数として無視され、CR2 が出る）

- [ ] **Step 3: 最小の実装**

```python
# core/listing.py に足す
def stack_extension_ranks(profiles: Iterable[ProfileRef]) -> list[tuple[str, str, int]]:
    """`(profile_id, 拡張子, 順位)` の一覧. **順位は現行リビジョンから読む。**

    取り込んだ版ではなく現行版を使うのは、組が「取り込みの記録」ではなく
    「いま適用する操作」だから（`decisions.md`）。`stack` が無効なプロファイルは
    1 行も出さない —— 出さなければ、その行は決して従にならない。
    """
    ranks: list[tuple[str, str, int]] = []
    for profile in profiles:
        rule = profile.definition.stack
        if not rule.enabled:
            continue
        for position, extension in enumerate(rule.extensions):
            ranks.append((profile.profile_id, extension, position))
    return ranks
```

```python
# routes_media.py
# **従を外す節。** 「同じカードで、同じ同席の印を持ち、自分より順位が上の
# 拡張子の兄弟が居る」行は主ではないので一覧に出さない。組がページの境目を
# またがないよう、束ねずに隠す（`docs/history/phase10-design.md` の 4）。
# **曖昧な組は畳まない**（`identity_partners` の `ambiguous` と同じ判断）。
# 同じ順位の兄弟が 2 つあると、どちらが主か決まらない。畳むと片方が消える。
_AMBIGUOUS_EXISTS = """
EXISTS (
  SELECT 1
    FROM source_entry me
    JOIN source_entry a
      ON a.volume_instance_id = me.volume_instance_id
     AND a.copresent_key = me.copresent_key
     AND a.media_file_id IS NOT NULL AND a.state = 'published'
    JOIN source_entry b
      ON b.volume_instance_id = me.volume_instance_id
     AND b.copresent_key = me.copresent_key
     AND b.media_file_id IS NOT NULL AND b.state = 'published'
     AND b.media_file_id <> a.media_file_id
     AND b.extension = a.extension
   WHERE me.media_file_id = m.id
     AND me.state = 'published'
     AND me.copresent_key IS NOT NULL
)
"""

_SECONDARY_EXISTS = """
EXISTS (
  SELECT 1
    FROM source_entry me
    JOIN rank mine ON mine.profile_id = m.profile_id AND mine.extension = me.extension
    JOIN source_entry sib
      ON sib.volume_instance_id = me.volume_instance_id
     AND sib.copresent_key = me.copresent_key
     AND sib.media_file_id IS NOT NULL
     AND sib.media_file_id <> m.id
     AND sib.state = 'published'
    JOIN media_file sm ON sm.id = sib.media_file_id
    JOIN rank theirs ON theirs.profile_id = sm.profile_id AND theirs.extension = sib.extension
   WHERE me.media_file_id = m.id
     AND me.state = 'published'
     AND me.copresent_key IS NOT NULL
     AND theirs.rank < mine.rank
)
"""
```

`list_media` に `collapse: str | None = None` を足し、`collapse == "stack"` の
ときだけ `WITH rank(profile_id, extension, rank) AS (VALUES ...)` を前置して
`AND NOT (<_SECONDARY_EXISTS>) AND NOT (<_AMBIGUOUS_EXISTS>)` を `where` に足す。
**両方入れる** —— 曖昧な組を畳むと、同じ順位の 2 つのうち片方が黙って消える。`ranks` が空なら**何もしない**
（`VALUES` は 0 行を書けない）。`collapse` が `"stack"` でも `None` でもなければ
`ApiError(400, ErrorCode.INVALID, "collapse は stack だけ")`。

組の中身は、主の行 1 つにつき 1 回だけ引く。

```python
def _members_of(conn, media_id: str, ranks_sql: str, ranks_params: tuple) -> list | None:
    """主から見た組の中身（**主を先頭に**、順位の順）. 組でなければ None.

    **現行の `stack` 規則で絞る。** `copresent_key` は残り続けるのに順位は現行版
    なので、絞らないと `extensions` を変えた後に `identity_partners` と食い違う
    （「同じ関数が決める」が崩れ、順位の dict 引きも KeyError になる）。
    """
    rows = conn.execute(
        f"WITH rank(profile_id, extension, rank) AS (VALUES {ranks_sql})"  # noqa: S608
        " SELECT DISTINCT sm.id AS id, sm.rel_path AS rel_path,"
        "        sm.size_bytes AS size_bytes, r.rank AS rank"
        "   FROM source_entry me"
        "   JOIN source_entry sib"
        "     ON sib.volume_instance_id = me.volume_instance_id"
        "    AND sib.copresent_key = me.copresent_key"
        "    AND sib.media_file_id IS NOT NULL AND sib.state = 'published'"
        "   JOIN media_file sm ON sm.id = sib.media_file_id"
        "   JOIN rank r ON r.profile_id = sm.profile_id AND r.extension = sib.extension"
        "  WHERE me.media_file_id = ? AND me.state = 'published'"
        "    AND me.copresent_key IS NOT NULL"
        "  ORDER BY r.rank",
        (*ranks_params, media_id),
    ).fetchall()
    if len(rows) < 2:  # noqa: PLR2004 - 1 つでは組にならない
        return None
    return rows
```

**応答に出すのは `id` / `rel_path` / `size_bytes` の 3 つだけ**（`rank` は並べ替えの
ための内部の値なので落とす。画面の `StackMember` 型と一致させる）。

```python
    members = [
        {"id": row["id"], "rel_path": row["rel_path"], "size_bytes": row["size_bytes"]}
        for row in rows
    ]
```

**並べ替えは SQL の `ORDER BY r.rank` で行う**（Python 側で順位の dict を引くと、
現行規則から外れた拡張子で `KeyError` になる）。**主が先頭に来ることをテストで
固定する**（上の Step 1 の 1 本目）。

**`_members_of` を現行規則で絞ることの試験も、この Step で書く。**

```python
def test_members_follow_the_current_rule(client, canon_pair, narrowed_stack_rule):
    """`extensions` から CR2 を外した後は、CR2 は組の中身に出ない.

    `copresent_key` は残り続けるので、絞らないと `identity_partners`（現行規則で
    CR2 を外す）と食い違い、「同じ関数が決める」という設計の要が崩れる。
    """
    body = client.get("/api/media?collapse=stack").json()

    assert all("stack" not in m or len(m["stack"]["members"]) >= 2 for m in body["media"])
    assert not any(m["rel_path"].endswith(".CR2") for m in body["media"] if m.get("stack"))
```

- [ ] **Step 4: 通ることを確かめる**

実行: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/ -q`

- [ ] **Step 5: 速さを測る**

**Phase 9 で索引を入れ違えて他経路が 57.7 ms → 74 ms へ退行した前例がある。**
`collapse` **無し**の一覧が遅くなっていないことを、`EXPLAIN QUERY PLAN` と
実測の両方で確かめ、値を `phase10-record.md` に書く。

- [ ] **Step 6: 変異試験**

壊すもの: `theirs.rank < mine.rank` を `<=` / `me.copresent_key IS NOT NULL` を外す /
`sib.media_file_id <> m.id` を外す / `rule.enabled` の門を外す / `total` を
畳む前の件数にする / **`_AMBIGUOUS_EXISTS` の除外を外す** / **`_members_of` の
`rank` への join を外す**。

- [ ] **Step 7: コミット**

```bash
git add app/src/mediaferry/core/listing.py app/src/mediaferry/api/routes_media.py app/tests/
git commit -m "feat(api): GET /media に collapse=stack を足す"
```

---

## Task 7: タイルの `RAW` の札

**ファイル:**
- 変更: `web/src/components/MediaTile.tsx:14-27`（型）, `:58-66`（中身）
- 変更: `web/src/screens/Photos.tsx:77-100`（`buildMediaQuery`）
- 変更: `web/src/styles.css:266` 付近
- 試験: `web/src/components/MediaTile.test.tsx`, `web/src/screens/Photos.test.tsx`

**消費するもの:** `GET /media?collapse=stack` の `stack.members`（Task 6）

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/components/MediaTile.test.tsx に足す
it("RAW も一緒にあると分かる", () => {
  render(
    <MediaTile
      media={{
        id: "1",
        rel_path: "library/canon-eos/DCIM/100CANON/IMG_0001.JPG",
        stack: {
          members: [
            { id: "1", rel_path: ".../IMG_0001.JPG", size_bytes: 100 },
            { id: "2", rel_path: ".../IMG_0001.CR2", size_bytes: 200 },
          ],
        },
      }}
      selected={false}
    />,
  );
  expect(screen.getByText("RAW")).toBeInTheDocument();
});

it("組でなければ RAW とは書かない", () => {
  render(
    <MediaTile
      media={{ id: "1", rel_path: "library/canon-eos/DCIM/100CANON/IMG_0002.JPG" }}
      selected={false}
    />,
  );
  expect(screen.queryByText("RAW")).not.toBeInTheDocument();
});
```

```tsx
// web/src/screens/Photos.test.tsx に足す
it("写真タブは組を畳んで取りに行く", () => {
  // 既存の buildMediaQuery のテストと同じ形で、query に collapse=stack が
  // 入っていることを確かめる。**入れ忘れると 2 タイルに割れて出る。**
});
```

- [ ] **Step 2: 落ちることを確かめる**

実行: `npm --prefix web run test -- --run MediaTile`
期待: FAIL（`RAW` が無い）

- [ ] **Step 3: 最小の実装**

```tsx
// MediaTile.tsx
export type StackMember = { id: string; rel_path: string; size_bytes: number };

export type Media = {
  // ...既存のまま
  /** 組（RAW+JPEG）。**主の行にだけ付く**（`GET /media?collapse=stack`）。 */
  stack?: { members: StackMember[] } | null;
};

export type TileMedia = Pick<Media, "id" | "rel_path"> &
  Partial<Pick<Media, "kind" | "duration_seconds" | "status" | "role" | "stack">>;
```

```tsx
      {media.role === "derived" && <span className="madeof">つないだ</span>}
      {media.stack && <span className="madeof raw">RAW</span>}
```

**`つないだ` と同時には出ない**（`derived` は結合した動画で、組は写真）。並ぶ
場合の重なりを避けるため CSS は `.madeof.raw` に別の `left` を与えず、
**`madeof` と同じ位置でよい**。

```tsx
// Photos.tsx の buildMediaQuery の末尾、page_size を入れる直前に足す
  // **写真タブは組を畳む。** 畳まないと同じ 1 枚が 2 タイルに割れて並ぶ。
  query.set("collapse", "stack");
```

- [ ] **Step 4: 通ることを確かめる**

実行: `npm --prefix web run test -- --run`

- [ ] **Step 5: コミット**

```bash
git add web/src
git commit -m "feat(web): 組のタイルに RAW の札を出す"
```

---

## Task 8: 受け入れ

**ファイル:**
- 変更: `web/e2e/phase6.spec.ts`（RAW+JPEG の E2E がここにある）
- 新規: `docs/history/phase10-record.md`

- [ ] **Step 1: E2E を足す**

`phase6.spec.ts` の「RAW+JPEG が 1 スタックになり、見送りの理由も画面に出る」の
隣に、**写真タブで 1 タイルになり `RAW` の札が出る**ことを踏む 1 本を足す。

- [ ] **Step 2: 受け入れコマンドを 5 つ全部回す**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest
uv run ruff check .
uv run ruff format --check .
npm --prefix web run test -- --run
npm --prefix web run test:e2e
```

**E2E を飛ばさない。** Phase 8 では 8 タスクぶん 4 本が赤のまま誰も気づかなかった。

- [ ] **Step 3: 記録を書く**

`phase10-record.md` に、巡数・変異試験の結果（当てた数／生き残った数／構造的に
検出できないものとその理由）・計画の誤り・Task 6 で測った値を書く。

- [ ] **Step 4: docs を現在形に直す**

- `docs/design.md` §6・§9.11 の組の 4 条件を、**時刻を含まない 3 条件＋同席**に直す
- `docs/decisions.md`「RAW/JPEG のスタッキング」に、時刻を外した理由と同席の印を足す
- `docs/history/README.md` の表に `phase10-plan.md` と `phase10-record.md` を足す

- [ ] **Step 5: コミット**

```bash
git add web/e2e docs
git commit -m "docs: Phase 10 の記録と、現在形の仕様への反映"
```

---

## 自己レビュー（計画を書いた後に確かめたこと）

- **設計の 6 節すべてにタスクがある** —— 1→Task 2 / 2→Task 1,2 / 3→Task 3,4,5 /
  4→Task 6 / 5→Task 7 / 6→Task 6 の Step 3・5
- **移行**は Task 1 の Step 3（`tolerance_seconds` を許して読まない）と Task 3 の
  「既存の行は NULL」で閉じている
- **範囲の外**（CR2 のサムネイルが真っ黒）にはタスクを置いていない。**意図した
  とおり**。別の不具合として直す
- 型の名前は Task をまたいで一致している（`identity_partners` / `copresent_key` /
  `extension` / `stack.members`）
