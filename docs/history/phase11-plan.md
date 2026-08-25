# Phase 11 の実装計画 —— 実機レビューの 13 件を閉じる

> **実装する人へ:** この計画は 1 タスクずつ進める。各タスクは**失敗するテストを
> 書き、失敗を確認してから**最小実装する（`CLAUDE.md`）。変異試験を省かない。

**目標:** 実機で触った利用者が挙げた 13 件と、その確認で見つかった 4 件を閉じる。

**設計の正本:** [`phase11-design.md`](phase11-design.md)。**この計画は設計から論を
借りているので、両方を読む。** 症状の ID（R1〜R13・N1〜N4）は設計の表と同じ。

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

## Phase 10 と当たらないための境界

**Phase 10（RAW+JPEG のスタッキング）が並行して走っている。** その計画の
「ファイルの地図」（`phase10-plan.md`）と突き合わせた結果、**重なるファイルは
`web/src/styles.css` の 1 つだけ**で、触る箇所も離れている。

| | Phase 10 | Phase 11 |
| --- | --- | --- |
| `web/src/styles.css` | `.madeof` の隣に `RAW` の札（`:267` 付近） | リセット（`:81-84`）・`.chip`（`:232`）・`.dialog`（`:303`） |
| E2E | `web/e2e/phase6.spec.ts` | `web/e2e/journey.spec.ts` |
| `docs/` | design §6・§9.11、decisions の「RAW/JPEG」 | design §13、decisions の画面の判断 |

**Phase 11 は次のファイルに触らない。** どれも触る必要が無く、触ると Phase 10 と
正面から当たる。

```
app/src/mediaferry/jobs/scan.py            app/src/mediaferry/jobs/stacker.py
app/src/mediaferry/core/uploads/stacking.py  app/src/mediaferry/db/uploads.py
app/src/mediaferry/core/listing.py         app/src/mediaferry/api/routes_media.py
app/src/mediaferry/core/profiles/**        web/src/components/MediaTile.tsx
web/src/screens/Photos.tsx
```

- **移行（migration）を足さない。** `0024` は Phase 10 が取る。この Phase の
  変更は列を要求しない（リセットは行を消すだけ）。**足したくなったら、それは
  設計から外れた合図**
- `source_entry` は**読むだけ**（Task 8）。Phase 10 は同じ表に列を 2 つ足すが、
  足す側と読む側なので順序を問わない
- `docs/history/README.md` の表には両方が行を足す。**並びだけ確かめる**

### `scan.py` の掃除は、この Phase の外で進んでいる

**Task 9 の前提（カードから消えた `source_entry` を外す）は `5db4dff` で main に
入っている。** Phase 11 は**この件に触らない** —— 設計の 6 章が「枚数のずれは
リセットではなくスキャン側で塞ぐ」と書いているのは、その実装を指している。

**ただし `jobs/scan.py` では、まだ作業が続いている**（`5db4dff` の上に、存在の
確かめを三値にして「在るか分からない」を消さずに残す変更が乗っている）。
**Phase 10 も同じファイルを大きく書き換える。** どちらも Phase 11 の外の話だが、
**`scan.py` は 2 つの作業が同時に触っている**ことは把握しておく。

---

## ファイルの地図

| ファイル | 役目 | Task |
| --- | --- | --- |
| `web/src/styles.css` | リセット（`ul` の padding・`a` の下線）・縦並びの chip・`.dialog` の間隔 | 1 |
| `web/src/screens/work/Send.tsx` | 「やめる」を包む・プリセットの密度・info に `.grow` | 2 |
| `web/src/screens/PhotoDetail.tsx` | 見出しを `.sechead` へ・宛先の行・採用の入口 | 3, 6 |
| `web/src/components/ConfirmDialog.tsx` | `upload` の本文を厚くする・文言 | 4, 5 |
| `web/src/screens/work/CardDetail.tsx` | 文言・戻り先・カードの中身の一覧 | 5, 7, 8 |
| `web/src/screens/Home.tsx` | 文言（札の本文） | 5 |
| `web/src/test/vocabulary.ts` | 禁止語に「承認」 | 5 |
| `web/src/screens/work/Merge.tsx` | 未結合だけを出す・本文の幅・戻り先 | 6, 7 |
| `web/src/screens/work/Approve.tsx` | 戻り先 | 7 |
| `web/src/screens/Settings.tsx` | 「詳しい情報」を性質で割る・遷移元を渡す | 7 |
| `app/src/mediaferry/api/routes_devices.py` | カード 1 枚の中身を返す | 8 |
| `app/src/mediaferry/db/sources.py` | 取り込み待ちの一覧と合計 | 8 |
| `app/src/mediaferry/api/routes_system.py` | リセットの経路 | 9 |
| `web/src/screens/settings/Reset.tsx` | リセットの画面（新規） | 9 |
| `web/src/screens/details/JobHistory.tsx` | 終わった作業の要約 | 10 |
| `web/e2e/journey.spec.ts` | 網を広げる | 11 |

---

## Task 1: 土台 —— リセットと、縦並びの chip

**ファイル:**
- 変更: `web/src/styles.css:81-84`, `:232-238`, `:303-307`
- 試験: `web/e2e/journey.spec.ts`（Task 11 で本格的に。ここでは既存が緑のまま）

**この先が使うもの:** `.chip.stacked`（縦並び）、`.dialog` の縦の間隔

**危険:** **`.chip` そのものを縦にしない。** 写真タブの絞り込み（`Photos.tsx`）が
同じクラスを使っており、**そちらは 1 行が正しい**。派生クラスを足す。

- [ ] **Step 1: 失敗するテストを書く**

CSS は jsdom が解析しないので、**単体では錠を掛けられない**（`../development.md`
の持ち越し）。ここは Task 11 の E2E に預け、この Task では**既存の E2E が緑のまま**
であることだけを確かめる。**「テストを書かない」ではなく「どこで見るかを決める」**
——見る場所は `journey.spec.ts` の幅の検査。

- [ ] **Step 2: 実装**

```css
/* ul の既定のインデントを消す。**margin だけのリセットでは残る** ——
   40px の padding が、狭い欄（つなぐ画面の本文・確認の箇条書き）から取られる。 */
ul { margin: 0; padding: 0; }

/* リンクの下線を消し、色はトークンから取る。**ボタンの形をしたリンクにも
   下線が付いていた**（`.btn` は色しか指定していない）。 */
a { color: var(--accent-deep); text-decoration: none; }
a:hover { text-decoration: underline; }

/* 名前と状況を 2 行で見せる chip。**`.chip` は 1 行のまま**（写真タブの
   絞り込みが使う）。 */
.chip.stacked {
  flex-direction: column; align-items: flex-start; justify-content: center;
  gap: 2px; height: 56px;
}

/* タイトルと本文が地続きに見えない間隔。**`h2` に margin を足さない** ——
   リセットで全体を 0 にしてあるので、ここだけ例外を作ると他の見出しとずれる。 */
.dialog { display: flex; flex-direction: column; gap: 14px; }
```

- [ ] **Step 3: 目で確かめる**

実サーバを立てて `/send` と確認ダイアログを見る（手順は `../development.md`)。
**`ul { padding: 0 }` は箇条書きの黒丸も左端へ寄せる。** 確認ダイアログの
本文が読めるか見て、必要なら `.dialog ul { padding-left: 1.2em }` を足す。

- [ ] **Step 4: 受け入れ 5 本**

---

## Task 2: 送る画面（R1・R3・R4）

**ファイル:**
- 変更: `web/src/screens/work/Send.tsx:372-377`（やめる）, `:394-404`（chip）,
  `:437-460`（プリセット）, `:473-480`（info）
- 試験: `web/src/screens/work/Send.test.tsx`

**危険:** **`.wrap` の直下に置いたボタンは幅いっぱいに伸びる**（`align-items` の
既定が `stretch`）。他の 3 画面と同じく `div.row` で包む。**この形は他所にも
効くので、包むこと自体を規則として書く。**

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// Send.test.tsx の末尾に足す
it("「やめる」は行に包まれている（幅いっぱいに伸びない）", async () => {
  // **jsdom は幅を測れない。** 測れるのは構造だけなので、「.wrap の直下に
  // 置かない」を見る —— 伸びるかどうかはそこで決まる（`styles.css` の .wrap は
  // 縦並びで align-items が stretch）。
  renderSend();
  const cancel = await screen.findByRole("button", { name: /やめる/ });
  expect(cancel.parentElement).toHaveClass("row");
});

it("「すでにある写真は〜」の説明は、アイコンと同じ行に入る", async () => {
  // `.rowtop` は flex-wrap: wrap で、行に詰めるかを flex-basis で決める。
  // `.grow` が無いと basis が max-content になり、**縮む前に改行される**。
  renderSend();
  const note = await screen.findByText(/すでにある写真/);
  expect(note).toHaveClass("grow");
});
```

- [ ] **Step 2: 落ちることを確かめる**

実行: `npm --prefix web run test -- --run Send`
期待: 2 本とも FAIL

- [ ] **Step 3: 実装**

- 「やめる」を `<div className="row">` で包む
- chip に `stacked` を足し、名前と状況を子 2 つのまま縦に並べる
- プリセットの `padding` を下げ、`minmax(230px, 1fr)` を詰める（**送るものの
  タイルとの落差を減らす**。数値は実サーバで見て決める）
- info の `<p className="muted">` を `muted grow` にする

- [ ] **Step 4: 変異試験**

`row` の包みを外す／`grow` を消す／`stacked` を消す。**前 2 つは上の 2 本が
検出する。`stacked` は検出できない**（jsdom は class の有無しか見ない）ので、
**検出できないことを記録に残す**（Task 11 の E2E が幅で見る）。

---

## Task 3: 写真詳細の見出しと、宛先ごとの行（R5）

**ファイル:**
- 変更: `web/src/screens/PhotoDetail.tsx:136-150`, `:153-170`
- 試験: `web/src/screens/PhotoDetail.test.tsx`

**危険:** **`.sechead h2` は `.sechead` の子にしか効かない。** 素の `<h2>` は
ブラウザ既定の **22.5px** で描かれ、`h1.page.title-lg`（24px）とほぼ同じになる。
**見出しの階層がつぶれる。**

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("節の見出しは、画面の見出しと同じ階層に見えない", async () => {
  // **クラスの付け忘れを見る。** 大きさは jsdom で測れないので、`.sechead` で
  // 包まれていること（＝ 15px の規則が当たること）を見る。
  renderDetail();
  for (const name of ["宛先ごとの状況", "元になったファイル"]) {
    const heading = await screen.findByRole("heading", { name });
    expect(heading.parentElement).toHaveClass("sechead");
  }
});

it("宛先の名前と状況は、同じ行の要素として並ぶ", async () => {
  renderDetail();
  const row = (await screen.findByText("immich-1")).closest("li");
  expect(row).toHaveClass("row");     // rowtop ではない（上端揃えだと状況が落ちる）
  expect(within(row!).getByText(/まだ送っていません/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 落ちることを確かめる** — `npm --prefix web run test -- --run PhotoDetail`

- [ ] **Step 3: 実装**

- 2 つの `<h2>` を `<div className="sechead">` で包む
- 宛先の行を `.rowtop` から `.row` にし、名前に本文の大きさを与える
  （`fontSize: "13.5px"`。他の一覧と揃える）
- 狭い画面で名前と状況が 2 行に割れても**対応が読めるように**、状況を
  名前の直下に置く（`.grow` の中に 2 行）

- [ ] **Step 4: 変異試験** — `.sechead` を外す／`.row` を `.rowtop` に戻す。
  どちらも上の 2 本が検出する。

---

## Task 4: 確認の本文を厚くする（R6 の後半）

**ファイル:**
- 変更: `web/src/components/ConfirmDialog.tsx:29-40`（`upload` の枝）
- 試験: `web/src/components/ConfirmDialog.test.tsx`

**危険:** **一覧を出さない。** 200 件のこともあるので、ダイアログが一覧の画面に
なる（設計の「採らなかった案」）。**内訳の数だけを出す。**

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("送る確認は、宛先ごとの件数と、原本／つないだ動画の内訳を出す", () => {
  const { body } = describe({
    kind: "upload",
    count: 5,
    totalBytes: 1024,
    destinationNames: ["immich-1", "immich-2"],
    perDestination: [{ name: "immich-1", count: 5 }, { name: "immich-2", count: 3 }],
    derivedCount: 2,
  });
  render(<>{body}</>);
  expect(screen.getByText(/immich-1.*5/)).toBeInTheDocument();
  expect(screen.getByText(/immich-2.*3/)).toBeInTheDocument();
  expect(screen.getByText(/つないだ動画 2/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 落ちることを確かめる**

- [ ] **Step 3: 実装** —— `Confirmation` の `upload` に 2 欄足し、`Send.tsx` が
  既に持っている値から作る（**新しい API を呼ばない**）。

- [ ] **Step 4: 変異試験** —— 宛先ごとの件数を全体の件数に置き換える／内訳を消す。

---

## Task 5: 語彙を「信頼」に寄せる（R7）

**ファイル:**
- 変更: `web/src/screens/work/CardDetail.tsx:150-170`（`autoImportState`）
- 変更: `web/src/components/ConfirmDialog.tsx:147`
- 変更: `web/src/screens/Home.tsx:144` 付近の札の本文
- 変更: `web/src/test/vocabulary.ts`（`FORBIDDEN` に「承認」）
- 試験: 上記の各 `*.test.tsx`

**危険:** **`/approve`（日時の確認）のコードは `approve` のままでよい。** 直すのは
**画面に出る言葉**であって、内部の名前ではない。`FORBIDDEN` は画面の本文を見る網
なので、コードの識別子には掛からない。

- [ ] **Step 1: 失敗するテストを書く**

```ts
// vocabulary.ts の FORBIDDEN に "承認" を足すと、既存の 3 本が落ちる。
// **その落ち方こそが検出**なので、先に足してから本文を直す。
```

- [ ] **Step 2: 落ちることを確かめる** —— `Home.test.tsx` の
  `/未承認です/`、`CardDetail.test.tsx` の 5 本、E2E の禁止語の巡回。

- [ ] **Step 3: 実装** —— 「未承認です」→「まだ信頼していません」、
  「承認すると」→「信頼すると」、「承認の数秒後」→「信頼した数秒後」。
  **テストの期待も一緒に直す**（`docs/development.md` の「テストが思い違いを
  仕様として固定する」に注意。**直すのは文言であって、条件ではない**）。

- [ ] **Step 4: 変異試験** —— `FORBIDDEN` から「承認」を抜く。E2E と
  `ConfirmDialog.test.tsx` が落ちること。

---

## Task 6: つなぐ画面は「まだつないでいないもの」だけ（R10・N1）

**ファイル:**
- 変更: `web/src/screens/work/Merge.tsx:246-420`（`renderGroup`）, `:424-440`
- 変更: `web/src/screens/PhotoDetail.tsx`（採用・やり直し・構成を変える）
- 変更: `app/src/mediaferry/api/routes_merges.py`（1 件のくわしくが持ち主の
  グループを返す。**`routes_media.py` は触らない** —— Phase 10 の領分）
- 試験: `web/src/screens/work/Merge.test.tsx`,
  `web/src/screens/PhotoDetail.test.tsx`, `app/tests/test_api.py`

**この Task は割ってはいけない。** 「中身を見て、これを使う」は**検証に落ちた
結合済みグループにしか出ない唯一の入口**で、`SENDABLE_CLAUSE` は `passed` か
`adopted_at` しか見ない。`/merge` から `merged` を外す変更だけを先に入れると、
**検証不合格の動画を送る手段が消えた状態が中間に生まれる。**

**危険:** `failed`（結合に失敗）は**未結合の側**なので `/merge` に残す。裁定 12 の
「個別に送る」もここにしかない。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// Merge.test.tsx
it("結合済みのグループは、つなぐ画面に出ない", async () => {
  renderMerge({ groups: [aGroup({ status: "merged", output: anOutput() })] });
  expect(await screen.findByRole("heading", { name: "つなぐものはありません" })).toBeInTheDocument();
  // **行き止まりにしない。** 出さない代わりに、どこにあるかを書く。
  expect(screen.getByRole("link", { name: /つないだ動画/ })).toHaveAttribute(
    "href", expect.stringContaining("role=derived"),
  );
});

it("結合に失敗したグループは残る（送る手段がここにしかない）", async () => {
  renderMerge({ groups: [aGroup({ status: "failed" })] });
  expect(await screen.findByRole("button", { name: "再試行する" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "個別に送る" })).toBeInTheDocument();
});
```

```tsx
// PhotoDetail.test.tsx
it("検証に落ちたつないだ動画は、くわしくから採用できる", async () => {
  renderDetail({ role: "derived", group: { verification: { passed: false, checks: [...] }, adopted_at: null } });
  await userEvent.click(await screen.findByRole("button", { name: /中身を見て、これを使う/ }));
  expect(await screen.findByRole("dialog")).toHaveTextContent(/検証に通っていない/);
});
```

- [ ] **Step 2: 落ちることを確かめる**

- [ ] **Step 3: 実装**

- `MergeScreen` の問い合わせを `?status=` の 2 回、または受け取った一覧の
  絞り込みに変える。**`superseded_by_id` が入った組も出さない**（操作できない）
- `renderGroup` の操作を本文の下へ落とし、メンバーのファイル名に `.ident` を
  当てる（**`/` では折り返さないので、`overflow-wrap` が要る**）
- くわしく画面に「中身を見て、これを使う」「同じ構成でやり直す」「構成を変える」
  「これは別々」を移す。**確認の本文（`Confirmation`）は作り直さない** —— 既存の
  種類をそのまま使う

- [ ] **Step 4: 変異試験**

`merged` を外す条件を消す／`failed` も一緒に外す／`.ident` を外す。**3 つ目は
jsdom では検出できない**ので、Task 11 の E2E に預けて記録に残す。

---

## Task 7: 「設定」の中身を割り、戻り先を渡す（R11・R12）

**ファイル:**
- 変更: `web/src/screens/Settings.tsx:28-56`（`DETAILS`）, `:200-225`
- 変更: `web/src/screens/work/CardDetail.tsx:207-212`,
  `web/src/screens/work/Merge.tsx:426-432`,
  `web/src/screens/work/Approve.tsx:83-88`
- 試験: `web/src/screens/Settings.test.tsx` と上記 3 画面の試験

**危険:** **ナビの「設定」という名前は変えない。** 3 項目は §13 の骨格。直すのは
中の並び。**URL も動かさない**（`/merge` と `/approve` はホームからも入るので、
設定の下へ動かすとホームの導線が設定の中を指す）。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// Settings.test.tsx
it("詳しい情報は、作業・記録・設定を別の見出しに分ける", async () => {
  renderSettings();
  const work = await screen.findByRole("region", { name: /ふだんは使わない操作/ });
  expect(within(work).getByRole("link", { name: /つなぐ/ })).toBeInTheDocument();
  const records = screen.getByRole("region", { name: /記録/ });
  expect(within(records).getByRole("link", { name: /作業の履歴/ })).toBeInTheDocument();
});

// Merge.test.tsx（他の 2 画面も同じ形）
it("設定から来たときは、設定へ戻る", async () => {
  renderMergeAt("/merge", { state: { from: "/settings" } });
  expect(await screen.findByRole("button", { name: "設定へ" })).toBeInTheDocument();
});

it("遷移元が無ければホームへ戻る（URL を直接開いた場合）", async () => {
  renderMergeAt("/merge");
  expect(await screen.findByRole("button", { name: "ホームへ" })).toBeInTheDocument();
});
```

- [ ] **Step 2: 落ちることを確かめる**

- [ ] **Step 3: 実装** —— 設定側は `<Link to={...} state={{ from: "/settings" }}>`。
  受け側は `useLocation().state?.from` を読み、無ければ `/`。
  **`document.referrer` と履歴は使わない**（URL 直打ちと区別できない）。

- [ ] **Step 4: 変異試験** —— `state` を渡さない／既定を `/settings` にする。
  2 本目と 3 本目がそれぞれ検出する。

---

## Task 8: カードの中身に、何が入っているかを出す（R8・R9）

**ファイル:**
- 変更: `app/src/mediaferry/db/sources.py`（取り込み待ちの一覧と合計）
- 変更: `app/src/mediaferry/api/routes_devices.py:19-38`
- 変更: `web/src/screens/work/CardDetail.tsx`
- 試験: `app/tests/test_api.py`, `app/tests/test_sources.py`,
  `web/src/screens/work/CardDetail.test.tsx`

**危険:** **`captured_at` は出せない。** `source_entry` が持つ時刻は `mtime_ns`
だけで、撮影時刻は取り込んで probe を通したあとにしか無い。**「カード上の時刻」
として出し、撮影時刻と名乗らせない。**

**危険:** 数万件のカードがある。**上限を置き、切ったことを画面に書く**
（`MANIFEST_LIMIT = 500` と同じ考え方。裁定 20）。

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_api.py
def test_card_contents_lists_pending_entries_with_a_total(client, seeded_volume):
    """取り込み待ちの一覧と、その合計サイズを返す.

    **ボリュームの総容量とは別の欄**にする。片方だけだと、画面が
    「どちらのサイズか」を書けない。
    """
    body = client.get(f"/volumes/{seeded_volume}/contents").json()
    assert [entry["rel_path"] for entry in body["entries"]] == ["DCIM/100CANON/IMG_0001.JPG"]
    assert body["pending_bytes"] == 78
    assert body["truncated"] is False
```

- [ ] **Step 2: 落ちることを確かめる** — 404

- [ ] **Step 3: 実装** —— `PENDING_CLAUSE`（`jobs/volumes.py`）を再利用する。
  **同じ条件を書き直さない** —— ホームの「N 件を取り込む」と食い違う。

- [ ] **Step 4: 変異試験** —— `PENDING_CLAUSE` を `state = 'seen'` だけにする／
  上限を外す／合計をボリュームの総容量に差し替える。

---

## Task 9: リセット（R13）

**前提:** カードから消えた行の掃除は `5db4dff` で入っている（設計の 6 章）。
**リセットでその掃除をやり直さない。**

**ファイル:**
- 変更: `app/src/mediaferry/api/routes_system.py`
- 新規: `app/src/mediaferry/jobs/reset.py`（または `db/reset.py`）
- 新規: `web/src/screens/settings/Reset.tsx`, `web/src/App.tsx`（経路）
- 変更: `web/src/components/ConfirmDialog.tsx`（種類を 4 つ足す）
- 試験: `app/tests/test_reset.py`（新規）, `web/src/screens/settings/Reset.test.tsx`

**危険:** **Phase 9 の削除の規則は掛けない**（設計の 6 章）。ただし
**送信の記録を捨てると `origin` が `pre_existing` に落ち、二度と戻らない**
（`first_check_result` は不変）。**確認の本文に、Immich が無傷であることと、
証明が戻らないことの両方を書く。**

**危険:** **`ON DELETE RESTRICT` が張ってある。** `artifact_staging` が指している
`source_entry` は消せない（`scan.py` の掃除が避けているのと同じ理由）。**走っている
仕事がある間はリセットを受け付けない。**

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_resetting_the_upload_records_keeps_the_library(reset_world):
    """送信の記録だけを捨てる段。**ファイルは消えない。**"""
    reset_world.reset(scope="uploads")
    assert reset_world.count("upload_record") == 0
    assert reset_world.count("media_file") > 0
    assert reset_world.library_files() != []


def test_a_reset_is_refused_while_a_job_is_running(reset_world):
    """走っている仕事の足元を外さない（`ON DELETE RESTRICT` に当たる）."""
    with reset_world.a_running_job():
        with pytest.raises(ApiError) as caught:
            reset_world.reset(scope="all")
    assert caught.value.status == 409
```

- [ ] **Step 2: 落ちることを確かめる**

- [ ] **Step 3: 実装** —— 4 段（作業の記録／送信の記録／取り込んだファイル／
  すべて）。**段は積み上げにする**（上の段は下の段を含む）。ファイルの削除は
  `DATA_ROOT` の下だけを対象にし、**絶対パスを組み立てない**（`CLAUDE.md`）。

- [ ] **Step 4: 変異試験** —— 段の包含関係を崩す／走っている仕事の検査を外す／
  `library/` の外を消せるようにする。**3 つ目は必ず検出できる形で書く。**

---

## Task 10: 作業の履歴が、終わった作業の要約を出す（N4）

**ファイル:**
- 変更: `web/src/screens/details/JobHistory.tsx`
- 変更: `app/src/mediaferry/api/routes_system.py`（`GET /jobs` に最後のイベント）
- 試験: `app/tests/test_api.py`, `web/src/screens/details/JobHistory.test.tsx`

**危険:** **いまの一覧は SSE で受けた分しか要約を出せない。** 画面を開く前に
終わった作業は「完了」としか出ない。**サーバから最後のイベントを返す**のが筋で、
画面側の工夫では直らない。

- [ ] **Step 1: 失敗するテストを書く** —— 画面を開く前に終わったジョブの要約が
  一覧に出ること（`スキャン完了: 新規 0 件 / 取込済 4 件 / 消えた 2 件`）。

- [ ] **Step 2〜4** —— 同上。**秘密をイベントに出さない**規則は既存のまま
  （`CLAUDE.md`）。

---

## Task 11: 網を広げ、受け入れる

**ファイル:**
- 変更: `web/e2e/journey.spec.ts`（**`phase6.spec.ts` は触らない** —— Phase 10 の領分）
- 新規: `docs/history/phase11-record.md`

**この Task が、今回の 17 件のうち 16 件を最初に捕まえる網になる。** 既存の網は
1 件も捕まえていない。

- [ ] **Step 1: 画面の状態を作ってから測る**

幅の検査（`spilling` と 44px）は、**つなぐ画面にグループが並んだ状態を一度も
見ていない**。`mergeTwoParts` で状態を作ってから巡る 1 本を足す。

- [ ] **Step 2: 親の箱からのはみ出しを見る**

`spilling` は `scrollWidth > clientWidth`、つまり**自分の箱**しか見ない。N1 は
「子の矩形が親の矩形を超える」形なので掛からない。**子と親の矩形を比べる検査**を
足す（`crossingCardEdges` の隣に置く）。

- [ ] **Step 3: `crossingCardEdges` を `article.card` にも広げる**

いまは `main section.card` だけ。つなぐ画面のグループは `article.card`。

- [ ] **Step 4: 見出しの大きさを見る**

R5 は「クラスの付け忘れ」。**`main h2` が既定の大きさのまま描かれていない**ことを
1 本で見る（実ブラウザなら測れる）。

- [ ] **Step 5: 受け入れコマンドを 5 つ全部回す**

**E2E を飛ばさない。** Phase 8 では 8 タスクぶん 4 本が赤のまま誰も気づかなかった。

- [ ] **Step 6: 記録と、現在形の仕様への反映**

- `phase11-record.md`（巡数・変異試験・計画の誤り・検出できなかったもの）
- `docs/design.md` §13 に、つなぐ画面と設定の並び、リセットの段
- `docs/decisions.md` に、リセットが Phase 9 の規則を迂回してよい理由
- `docs/user-guide.md` にリセットの節
- `docs/history/README.md` に `phase11-plan.md` と `phase11-record.md`

---

## 自己レビュー（計画を書いた後に確かめたこと）

- **Task 6 を割らないこと**を、計画の中で 2 度書いた。分けると「検証不合格の
  動画を送れない中間状態」が生まれる。**レビューで最初に崩されそうな判断**なので、
  理由（`SENDABLE_CLAUSE` が `passed` か `adopted_at` しか見ない）を添えた
- **Task 8 で `captured_at` を出せないこと**は、設計を書いた時点では見落として
  いた。`source_entry` の列を確かめて気づき、設計側も直した
- **Task 1 と Task 2 の一部は、単体では錠を掛けられない。** jsdom が CSS を
  解析しないため。**「テストを書かない」で済ませず、Task 11 のどの検査が見るかを
  各 Task に書いた**
- **Phase 10 との重なりは `styles.css` の 1 ファイルだけ**。触る箇所は
  `:81-84` / `:232` / `:303` と `:267` 付近で離れている
