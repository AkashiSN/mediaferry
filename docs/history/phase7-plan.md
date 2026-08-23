# Phase 7: 画面の作り直し（3 つのタブと「やること」）

> **エージェントで進める場合:** このファイルは 1 タスクずつ実装するための計画です。
> `superpowers:subagent-driven-development`（推奨）か `superpowers:executing-plans` を
> 使ってください。手順は `- [ ]` のチェックボックスで追えるようにしてあります。

**Goal:** 8 つ並んでいた画面を「ホーム / 写真 / 設定」の 3 つと、その下位の作業ページに
畳み直し、ホームが「いま何をすべきか」を状態から出すようにする。

**Architecture:** ナビゲーションは 1 つの `<nav>` を CSS で振り分ける（広いときはサイド
バー、狭いときは下部タブ）。ホームの「やること」は画面が一覧を持たず、`/dashboard` が
返す 3 つの数（結合候補・未送信・承認待ち）から毎回導く。作業ページ（カードの中身・
つなぐ・確認・送る・送信中）はホームの下位のルートで、ナビの項目は増やさない。旧
`Devices` / `Merges` / `Approvals` / `Jobs` / `Destinations` は、文言と体裁を直したうえで
それぞれの行き先へ移す。

**Tech Stack:** React 19 + TypeScript + Vite + react-router-dom 7 / vitest +
@testing-library/react / Playwright（E2E）/ FastAPI + SQLite（API 側）

**Spec:** [`../design.md`](../design.md) §13（画面）。判断の理由は
[`../decisions.md`](../decisions.md) の「Web UI と API」。

**見た目の参照物:** [`phase7-prototype.html`](phase7-prototype.html)。**この計画で
「プロトタイプの ○○ を写す」と書いてあるものは、すべてこのファイルの中にあります**
（ブラウザで開けます。自己完結で、外部を何も読みません）。色・間隔・文言・アイコンの
パスは、そこから取ってください。プロトタイプは動作の確認まで済ませてあります
（1440px / 390px、ライトとダークの両方で JS エラーなし・横スクロールなし）。

**プロトタイプと変える点が 1 つあります。** プロトタイプはサイドバーと下部タブを別々の
DOM で持っていますが、実装では **1 つの `<nav>` を CSS で振り分けます**。同じ項目の
`<nav>` が 2 つあると、アクセシブルな名前が重複してテストの `getByRole` が曖昧になり、
利用者にとっても読み上げが 2 度になります。

---

## Global Constraints

すべてのタスクの要件に、暗黙にこの節が含まれます。

**Python 側**

- Python は `>= 3.14`（`.python-version` で 3.14 に固定）
- ruff: `line-length = 100`、`select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`。
  **`docs/` は対象外**
- すべてのモジュールは `from __future__ import annotations` で始める
- **コメントと docstring は日本語。** いま書かれているコードを現在形で説明する。
  **過去の経緯はコメントに書かない**（`docs/` に残す）
- システム時刻は **UTC の ISO-8601 文字列**で DB に入れ、生成は `mediaferry.clock` の
  関数だけを使う
- **DB 接続はスコープごとに 1 本**
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**
- SQL の文字列連結は語彙を固定してから（既存の `# noqa: S608` の付け方に倣う）

**Web 側**

- **外部の書体もスクリプトも読まない。** `api/static.py` の CSP が
  `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'` なので、
  Web フォントもアイコンフォントも CDN も使えない。**書体は system-ui、アイコンは
  インライン SVG**
- **押せる領域は 44px 以上**（§13）。狭い画面では、本文を潰さずにボタンを次の行へ落とす
- **ライトとダークの両方で成立させる。** 色は CSS 変数で持ち、`:root` と
  `@media (prefers-color-scheme: dark)` の両方に定義する
- コメントと JSDoc は日本語
- 環境固有の値（IP・ホスト名・パス・鍵・タイムゾーンの実値）をコードにもテストにも
  書かない

**共通**

- **実装より先に失敗するテストを書き、失敗を確認してから**最小実装する
- **変異試験を省かない。** 実装の判断を 1 つずつ壊し、対応するテストが落ちることを
  確認してから戻す。**`PYTHONDONTWRITEBYTECODE=1` を付ける**（バイト数の変わらない
  書き換えで古い `.pyc` が使われる）
- **検出できない変異は、検出できないことを記録に残す**
- コミットは Conventional Commits + 日本語の本文。**なぜそうしたか**を本文に残す

**確認コマンド**

```bash
uv sync --all-packages        # --all-packages が必須
uv run pytest
uv run ruff check . && uv run ruff format --check .
cd web && npm run test && npm run lint && npm run typecheck
cd web && npm run test:e2e    # 実プロセスとブラウザ
```

---

## ファイル構成

**新しく作るもの**

| ファイル | 責務 |
| --- | --- |
| `web/src/components/Icon.tsx` | インライン SVG のアイコン 1 箇所。**外部を読まない**ための唯一の入口 |
| `web/src/components/MediaTile.tsx` | 写真 1 枚のタイル（状態の印・動画の長さ・選択の印） |
| `web/src/components/JobCard.tsx` | 進行中の作業 1 件（ホームと作業ページで共有） |
| `web/src/hooks/useTasks.ts` | 「やること」を `/dashboard` から導く |
| `web/src/screens/Home.tsx` | ホーム |
| `web/src/screens/Photos.tsx` | 写真（旧 `Library.tsx`） |
| `web/src/screens/work/CardDetail.tsx` | カードの中身（旧 `Devices.tsx`） |
| `web/src/screens/work/Merge.tsx` | つなぐ（旧 `Merges.tsx` の「生きている候補」だけ） |
| `web/src/screens/work/Approve.tsx` | 確認（旧 `Approvals.tsx`） |
| `web/src/screens/work/Send.tsx` | 送る（新規。送り先 → 対象 → 確認） |
| `web/src/screens/work/Sending.tsx` | 送信中（進捗・閉じる） |
| `web/src/screens/settings/Destinations.tsx` | 設定 › 送り先（旧 `Destinations.tsx`） |
| `web/src/screens/settings/Profiles.tsx` | 設定 › カメラの種類（旧 `Settings.tsx` の YAML 編集） |
| `web/src/screens/settings/General.tsx` | 設定 › env 由来の設定一覧 |
| `web/src/screens/details/JobHistory.tsx` | 設定 › 詳しい情報 › 作業の履歴（旧 `Jobs.tsx`） |
| `web/src/screens/details/MergeHistory.tsx` | 設定 › 詳しい情報 › 結合の記録（旧 `Merges.tsx` の破棄済み・使っていない出力） |
| `web/src/test/api.ts` | テストの `stubApi`（いま `screens.test.tsx` に埋まっているものを切り出す） |

**書き直すもの**

| ファイル | 変更 |
| --- | --- |
| `web/src/styles.css` | 19 行 → 色・間隔・部品のトークン。ライトとダークの両方 |
| `web/src/components/Layout.tsx` | 8 項目 → 3 項目。1 つの `<nav>` を CSS で振り分ける |
| `web/src/App.tsx` | 3 タブ + 作業ページ + 設定の下位ページのルーティング |
| `web/src/screens/Settings.tsx` | 設定のトップ（要約と入口だけ）に縮める |
| `app/src/mediaferry/api/routes_media.py` | `status=unsent` に §10 の既定条件を効かせる |
| `app/src/mediaferry/api/routes_system.py` | `/dashboard` に「やること」の材料を足す |

**消すもの**（タスク 12 でまとめて）

`web/src/screens/Devices.tsx`、`Merges.tsx`、`Approvals.tsx`、`Jobs.tsx`、
`Library.tsx`、`Destinations.tsx`、`screens.test.tsx`（各画面のテストへ分割）

---

## Task 1: 土台（トークン・アイコン・3 項目のナビ）

**Files:**
- Create: `web/src/components/Icon.tsx`
- Create: `web/src/components/Layout.test.tsx`
- Create: `web/src/test/api.ts`
- Modify: `web/src/styles.css`（全面書き直し）
- Modify: `web/src/components/Layout.tsx`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces:
  - `Icon({ name, size }: { name: IconName; size?: number })` — `IconName` は
    `"home" | "photo" | "gear" | "card" | "merge" | "up" | "alert" | "info" | "check" | "lock" | "back" | "close" | "play" | "image" | "minus" | "list"`。
    既定の `size` は 20。`stroke="currentColor"` で描き、`aria-hidden="true"` を付ける
  - `Layout({ warnings, taskCount, children }: { warnings: Warning[]; taskCount: number; children: ReactNode })`
  - `stubApi(routes: Record<string, unknown>, onCall?: Handler): { calls: () => Call[] }`
    （`web/src/test/api.ts`）。`Call` は `{ path: string; method: string }`。
    **記録した呼び出しは返り値の `calls()` から読む** —— モジュールの変数を
    テストから直に触ると、ファイルを分けたときに前のテストの記録が混ざる

- [ ] **Step 1: `stubApi` を切り出す**

`web/src/screens/screens.test.tsx` の先頭にある `stubApi` と `calls` を
`web/src/test/api.ts` へ移す。振る舞いは変えない（`fetch` を差し替え、`/api` を
剥がしたパスを記録し、前方一致で `routes` から本体を返す）。**記録はモジュールの
変数ではなく `stubApi` の返り値に持たせる**：

```ts
export type Call = { path: string; method: string };

export function stubApi(
  routes: Record<string, unknown>,
  onCall?: (path: string, init?: RequestInit) => unknown,
): { calls: () => Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    const path = input.replace(/^\/api/, "");
    calls.push({ path, method: init?.method ?? "GET" });
    onCall?.(path, init);
    const key = Object.keys(routes).find((candidate) => path.startsWith(candidate));
    return Promise.resolve(new Response(JSON.stringify(key === undefined ? {} : routes[key]), { status: 200 }));
  }));
  return { calls: () => [...calls] };
}
```

`screens.test.tsx` は `import { stubApi } from "../test/api"` に書き換え、`calls` を
見ているところを返り値の `calls()` に直す。

- [ ] **Step 2: 失敗するテストを書く**

```tsx
// web/src/components/Layout.test.tsx
// ナビは 1 つ。**同じ項目の nav を 2 つ置かない**（読み上げが 2 度になる）。

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Layout } from "./Layout";

function renderAt(path: string, taskCount = 0) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Layout warnings={[]} taskCount={taskCount}>
        <p>中身</p>
      </Layout>
    </MemoryRouter>,
  );
}

describe("画面の枠", () => {
  it("ナビゲーションは 1 つで、項目は 3 つだけ", () => {
    renderAt("/");
    const navs = screen.getAllByRole("navigation");
    expect(navs).toHaveLength(1);
    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(screen.getByRole("link", { name: /ホーム/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /写真/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /設定/ })).toBeInTheDocument();
  });

  it("作業ページを開いている間も、ホームが現在地のまま", () => {
    renderAt("/merge");
    expect(screen.getByRole("link", { name: /ホーム/ })).toHaveAttribute("aria-current", "page");
  });

  it("やることの件数をホームに添える。0 件のときは出さない", () => {
    const { unmount } = renderAt("/", 3);
    expect(screen.getByRole("link", { name: /ホーム/ })).toHaveTextContent("3");
    unmount();
    renderAt("/", 0);
    expect(screen.getByRole("link", { name: /ホーム/ })).not.toHaveTextContent("0");
  });

  it("公開の警告はバナーで出す", () => {
    render(
      <MemoryRouter>
        <Layout warnings={[{ code: "w1", message: "危ない組み合わせ" }]} taskCount={0}>
          <p>中身</p>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("危ない組み合わせ");
  });
});
```

- [ ] **Step 3: 落ちることを確かめる**

Run: `cd web && npx vitest run src/components/Layout.test.tsx`
Expected: FAIL（`taskCount` を受け取らない、項目が 8 つある、`/merge` で `aria-current` が付かない）

- [ ] **Step 4: `Icon.tsx` を書く**

プロトタイプの `ic` オブジェクト（`svg(...)` を並べたところ）を TSX に写す。
**パスはプロトタイプからそのまま取る**（描き直さない）。

```tsx
// web/src/components/Icon.tsx
// アイコン（§13）。**外部の書体もスクリプトも読まない**ので、すべてインライン SVG。

const PATHS = {
  home: <><path d="M4 11l8-6 8 6" /><path d="M6 10v9h12v-9" /></>,
  // …プロトタイプの ic から残り 15 個を写す
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
```

- [ ] **Step 5: `Layout.tsx` を書き直す**

```tsx
const SCREENS = [
  { to: "/", label: "ホーム", icon: "home", match: (path: string) => !path.startsWith("/photos") && !path.startsWith("/settings") },
  { to: "/photos", label: "写真", icon: "photo", match: (path: string) => path.startsWith("/photos") },
  { to: "/settings", label: "設定", icon: "gear", match: (path: string) => path.startsWith("/settings") },
] as const;
```

`useLocation()` の `pathname` を `match` に渡し、真なら `aria-current="page"` を付ける。
**`NavLink` の `end` では足りない** —— 作業ページ（`/merge`、`/send` など）はホームの
下位なので、ホームが現在地のままである必要がある。

- [ ] **Step 6: `styles.css` を書き直す**

プロトタイプの `<style>` をそのまま持ってくる。**ただしナビは 1 つなので、
`.side` と `.tabbar` の 2 つに分かれている規則を `nav.nav` 1 つにまとめる**：

```css
/* 広いときは左の柱、狭いときは下の帯。DOM は 1 つ。 */
.nav { grid-area: nav; }
@media (min-width: 900px) {
  .layout { display: grid; grid-template-columns: 248px minmax(0, 1fr); grid-template-areas: "nav main"; }
  .nav { border-right: 1px solid var(--line); height: 100vh; position: sticky; top: 0; flex-direction: column; }
}
@media (max-width: 899px) {
  .layout { display: grid; grid-template-areas: "main" "nav"; }
  .nav { position: fixed; left: 0; right: 0; bottom: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-top: 1px solid var(--line); }
  .nav .navlabel { font-size: 11px; }
  .main { padding-bottom: 104px; }
}
```

色は**プロトタイプの `:root` と `@media (prefers-color-scheme: dark)` をそのまま**写す。

- [ ] **Step 7: 通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 8: 変異試験**

`match` の `!path.startsWith("/photos")` を消す → 「作業ページを開いている間も、ホームが
現在地のまま」が落ちること。`taskCount > 0` の判定を `>= 0` にする → 「0 件のときは
出さない」が落ちること。戻す。

- [ ] **Step 9: コミット**

```bash
git add web/src/components/Icon.tsx web/src/components/Layout.tsx web/src/components/Layout.test.tsx web/src/styles.css web/src/test/api.ts web/src/screens/screens.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): ナビを 3 項目にし、色と間隔のトークンを置く

同じ項目の nav をサイドバーと下部タブで 2 つ持つと、読み上げが 2 度になり、
テストの getByRole も曖昧になる。DOM は 1 つにして CSS で振り分ける。

現在地の判定を NavLink の end に任せない。作業ページ（つなぐ・送る・確認）は
ホームの下位なので、開いている間もホームが現在地でなければ迷子になる。

アイコンは 1 ファイルに閉じたインライン SVG にする。CSP が default-src 'self'
なので、アイコンフォントを前提にすると実装の途中で必ず作り直しになる。
EOF
)"
```

---

## Task 2: 「まだ送っていない」を §10 の既定条件に合わせる

**背景（実装前に確かめた事実）。** いま `unsent` の意味が 2 箇所で食い違っている。

| 場所 | いまの意味 |
| --- | --- |
| `routes_media._status_clause("unsent")` | `complete` の記録が無いもの（**`failed` や `pending` も未送信に数える**） |
| `routes_system._destination_summary` の `unsent` | **有効な記録がまったく無いもの** |

さらに、どちらも §10 の「既定で選択肢に出すもの」を見ていないので、**結合グループの
構成ファイルまで「まだ送っていない」に数えている**。それを既定にして送ると
`POST /uploads` が組を断り、画面は「送れない組が N 件ありました」と言うことになる。

**Files:**
- Modify: `app/src/mediaferry/api/routes_media.py:100-116`（`_status_clause`）
- Modify: `app/src/mediaferry/api/routes_system.py:117-124`（`_destination_summary` の `unsent`）
- Test: `app/tests/test_api_listing.py`（追記）

**Interfaces:**
- Consumes: なし
- Produces: `mediaferry.core.listing.SENDABLE_CLAUSE`（SQL の断片。`m` を別名にした
  `media_file` を前提にする定数）

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_api_listing.py に追記
def test_unsent_means_no_record_at_all(client, db):
    """**`failed` は「まだ送っていない」ではない**（再試行は別の操作、§10）."""
    profile = a_profile(db, slug="unsent-test")
    media = a_media_file(db, profile, rel_path="library/unsent/A.JPG")
    destination = a_destination(db)
    an_upload(db, media, destination, state="failed")
    body = client.get(f"/api/media?destination_id={destination}&status=unsent").json()
    assert body["total"] == 0


def test_unsent_excludes_members_of_a_live_merge_group(client, db):
    """**構成ファイルは送る候補に出ない**（§10）. 出すと POST /uploads が断る."""
    profile = a_profile(db, slug="unsent-members")
    part = a_media_file(db, profile, rel_path="library/unsent/PART1.MP4")
    group = a_merge_group(db, profile, "digest-1", status="detected")
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group, part),
    )
    destination = a_destination(db)
    body = client.get(f"/api/media?destination_id={destination}&status=unsent").json()
    assert body["total"] == 0


def test_unsent_includes_the_output_of_a_merged_group(client, db):
    profile = a_profile(db, slug="unsent-output")
    output = a_media_file(db, profile, rel_path="derived/unsent/OUT.MP4", role="derived")
    group = a_merge_group(db, profile, "digest-2", status="merged")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ?, verification_json = ? WHERE id = ?",
        (output, '{"passed": true}', group),
    )
    destination = a_destination(db)
    body = client.get(f"/api/media?destination_id={destination}&status=unsent").json()
    assert [row["id"] for row in body["media"]] == [output]


def test_unsent_excludes_a_derived_that_failed_verification(client, db):
    """**検証に落ちた結合結果は、採用するまで候補に出ない**（§10）."""
    profile = a_profile(db, slug="unsent-failed-verify")
    output = a_media_file(db, profile, rel_path="derived/unsent/BAD.MP4", role="derived")
    group = a_merge_group(db, profile, "digest-3", status="merged")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ?, verification_json = ? WHERE id = ?",
        (output, '{"passed": false}', group),
    )
    destination = a_destination(db)
    body = client.get(f"/api/media?destination_id={destination}&status=unsent").json()
    assert body["total"] == 0


def test_the_dashboard_counts_unsent_the_same_way(client, db):
    """**2 箇所で意味を変えない**（ホームと写真で数が食い違う）."""
    profile = a_profile(db, slug="unsent-dashboard")
    part = a_media_file(db, profile, rel_path="library/unsent/PART2.MP4")
    group = a_merge_group(db, profile, "digest-4", status="detected")
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group, part),
    )
    destination = a_destination(db)
    listed = client.get(f"/api/media?destination_id={destination}&status=unsent").json()["total"]
    summary = client.get("/api/dashboard").json()["destinations"][0]["unsent"]
    assert summary == listed == 0
```

`a_merge_group` は `app/tests/test_schema_artifacts.py` にある（`test_merge_supersede.py`
が同じ形で使っている）。`a_media_file` の `role` 引数の有無は実物を見て合わせる。

- [ ] **Step 2: 落ちることを確かめる**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_api_listing.py -k unsent -v`
Expected: FAIL（`failed` を未送信に数える／構成ファイルを数える／不合格の derived を数える）

- [ ] **Step 3: 共通の SQL 断片を置く**

```python
# app/src/mediaferry/core/listing.py に追記
# §10「既定で選択肢に出すもの」を SQL で表したもの. **`media_file` の別名は `m`.**
#
# digest の一致（§10 の derived 条件の最後の 1 つ）はここに入れない。現行の構成と
# プロファイルから計算し直す必要があり、SQL では書けない（`SelectionService`）。
# その結果、設定を変えた後の古い派生物が数に残ることがある。
SENDABLE_CLAUSE = (
    "m.missing_at IS NULL AND ("
    " (m.role = 'original' AND NOT EXISTS ("
    "   SELECT 1 FROM merge_member mm WHERE mm.media_file_id = m.id AND mm.active = 1))"
    " OR (m.role = 'derived' AND EXISTS ("
    "   SELECT 1 FROM merge_group g WHERE g.output_media_file_id = m.id"
    "    AND g.superseded_by_id IS NULL AND g.status = 'merged'"
    "    AND (g.adopted_at IS NOT NULL"
    "         OR json_extract(g.verification_json, '$.passed') = 1)))"
    ")"
)
```

**`passed` を読む。** `SelectionService._verification_passed` が見ているのはこの鍵で、
`json_extract` は真の bool にだけ `1` を返すので、`"passed": "false"` は合格にならない
（Python 側の判断と同じになる）。

- [ ] **Step 4: 2 箇所を直す**

`routes_media._status_clause`:

```python
    if status == "unsent":
        # **「まだ送っていない」＝ この宛先の有効な記録がまだ無く、いま送れるもの。**
        # `failed` は再試行という別の操作、`pending` は既に積んである。
        return f"NOT EXISTS ({existing}) AND {SENDABLE_CLAUSE}"  # noqa: S608 - 定数のみ
```

`routes_system._destination_summary` の `unsent`:

```python
        "unsent": conn.execute(
            "SELECT count(*) AS n FROM media_file m WHERE NOT EXISTS ("  # noqa: S608
            " SELECT 1 FROM upload_record u WHERE u.media_file_id = m.id"
            "  AND u.destination_id = ? AND u.invalidated_at IS NULL)"
            f" AND {SENDABLE_CLAUSE}",
            (row["id"],),
        ).fetchone()["n"],
```

- [ ] **Step 5: 通ることを確かめる**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS。**既存のテストが落ちたら、それは意味を変えた印なので、期待値の側を
直してよいか 1 件ずつ判断する**（`status=unsent` を使っている既存のテストがある）。

- [ ] **Step 6: 変異試験**

`SENDABLE_CLAUSE` の `mm.active = 1` を `mm.active = 0` にする →
「構成ファイルは候補に出ない」が落ちること。`json_extract(...) = 1` を
`IS NOT NULL` にする → 「検証に落ちた結合結果」が落ちること。`NOT EXISTS ({existing})`
に `AND u.state = 'complete'` を戻す → 「`failed` は未送信ではない」が落ちること。
それぞれ戻す。

- [ ] **Step 7: コミット**

```bash
git add app/src/mediaferry/core/listing.py app/src/mediaferry/api/routes_media.py app/src/mediaferry/api/routes_system.py app/tests/test_api_listing.py
git commit -m "$(cat <<'EOF'
fix(api): 「まだ送っていない」の意味を 1 つにし、送れないものを外す

/media の status=unsent は「complete が無いもの」、/dashboard の unsent は
「記録がまったく無いもの」で、同じ言葉が 2 つの意味を持っていた。ホームと写真で
数が食い違うので、後者に統一する。failed は再試行という別の操作、pending は
既に積んであるので、どちらも「まだ送っていない」ではない。

どちらも §10 の「既定で選択肢に出すもの」を見ていなかったため、結合グループの
構成ファイルと検証に落ちた派生物まで数えていた。それを既定にして送ると
POST /uploads が組を断り、画面が「送れない組がありました」と言うことになる。

digest の一致だけは SQL に持ち込まない。現行の構成とプロファイルから計算し直す
必要があるため。設定を変えた後の古い派生物が数に残ることがある。
EOF
)"
```

---

## Task 3: `/dashboard` に「やること」の材料を足す

ホームは「やること」を状態から導く（§13）。いまの `/dashboard` は**結合をまったく
数えていない**ので、画面が `/merge-groups` を別に叩くことになる。それは
`/dashboard` の docstring が書いている方針（「**画面ごとに数えさせない**」）と食い違う。

**Files:**
- Modify: `app/src/mediaferry/api/routes_system.py:40-70`（`dashboard`）
- Test: `app/tests/test_api_dashboard.py`（新規）

**Interfaces:**
- Consumes: `SENDABLE_CLAUSE`（Task 2）
- Produces: `/dashboard` の応答に 3 つの整数が増える
  - `merge_candidates: int` — いま「つなぐ」で操作できるグループの数
  - `unsent_total: int` — **有効な宛先のどれかに**まだ送っていないものの数
  - `awaiting_total: int` — 承認待ちの記録の数（宛先をまたいだ合計）

- [ ] **Step 1: 失敗するテストを書く**

```python
# app/tests/test_api_dashboard.py
"""ホームの「やること」の材料（§13）.

**画面ごとに数えさせない。** 3 つの数を 1 回で返す。
"""

from __future__ import annotations

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import a_profile
from .test_schema_uploads import a_destination, an_upload


def test_merge_candidates_counts_only_what_can_be_acted_on(client, db):
    """**操作できるものだけ数える。** merged と skipped と supersede 済みは出ない."""
    profile = a_profile(db, slug="dash-merge")
    a_merge_group(db, profile, "d-detected", status="detected")
    a_merge_group(db, profile, "d-failed", status="failed")
    a_merge_group(db, profile, "d-merged", status="merged")
    a_merge_group(db, profile, "d-skipped", status="skipped")
    assert client.get("/api/dashboard").json()["merge_candidates"] == 2


def test_merge_candidates_ignores_superseded_groups(client, db):
    profile = a_profile(db, slug="dash-superseded")
    newer = a_merge_group(db, profile, "d-newer", status="detected")
    older = a_merge_group(db, profile, "d-older", status="detected")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, older))
    assert client.get("/api/dashboard").json()["merge_candidates"] == 1


def test_unsent_total_does_not_double_count_across_destinations(client, db):
    """**和を取らない。** 2 つの宛先に未送信の 1 件は 1 件."""
    profile = a_profile(db, slug="dash-unsent")
    a_media_file(db, profile, rel_path="library/dash/A.JPG")
    a_destination(db, name="one")
    a_destination(db, name="two")
    assert client.get("/api/dashboard").json()["unsent_total"] == 1


def test_unsent_total_ignores_disabled_destinations(client, db):
    """**休止中の宛先しか残っていなければ、送るやることは無い**（送り先が選べない）."""
    profile = a_profile(db, slug="dash-disabled")
    a_media_file(db, profile, rel_path="library/dash/B.JPG")
    destination = a_destination(db, name="paused")
    db.execute("UPDATE upload_destination SET enabled = 0 WHERE id = ?", (destination,))
    assert client.get("/api/dashboard").json()["unsent_total"] == 0


def test_unsent_total_counts_a_file_sent_to_only_one_of_two(client, db):
    profile = a_profile(db, slug="dash-partial")
    media = a_media_file(db, profile, rel_path="library/dash/C.JPG")
    first = a_destination(db, name="first")
    a_destination(db, name="second")
    an_upload(db, media, first, state="complete")
    assert client.get("/api/dashboard").json()["unsent_total"] == 1


def test_awaiting_total_sums_across_destinations(client, db):
    profile = a_profile(db, slug="dash-awaiting")
    media = a_media_file(db, profile, rel_path="library/dash/D.JPG")
    for name in ("a", "b"):
        an_upload(db, media, a_destination(db, name=name), state="awaiting_datetime_approval")
    assert client.get("/api/dashboard").json()["awaiting_total"] == 2
```

- [ ] **Step 2: 落ちることを確かめる**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_api_dashboard.py -v`
Expected: FAIL（`KeyError: 'merge_candidates'`）

- [ ] **Step 3: `dashboard` に 3 つ足す**

```python
        # **「つなぐ」で操作できるグループの数**（§13 の「やること」）。merged は
        # 済み、skipped は破棄、supersede 済みは組み直しの旧版なので、どれも
        # 押せるボタンが無い。
        "merge_candidates": conn.execute(
            "SELECT count(*) AS n FROM merge_group"
            " WHERE status IN ('detected', 'failed') AND superseded_by_id IS NULL"
        ).fetchone()["n"],
        # **和を取らない。** 2 つの宛先に未送信の 1 件は 1 件。休止中の宛先は
        # 送り先に選べないので、それしか無ければ「やること」は無い。
        "unsent_total": conn.execute(
            "SELECT count(*) AS n FROM media_file m WHERE EXISTS ("  # noqa: S608
            " SELECT 1 FROM upload_destination d"
            "  WHERE d.archived_at IS NULL AND d.enabled = 1"
            "    AND NOT EXISTS (SELECT 1 FROM upload_record u"
            "                    WHERE u.media_file_id = m.id AND u.destination_id = d.id"
            "                      AND u.invalidated_at IS NULL))"
            f" AND {SENDABLE_CLAUSE}"
        ).fetchone()["n"],
        "awaiting_total": conn.execute(
            "SELECT count(*) AS n FROM upload_record"
            " WHERE state = 'awaiting_datetime_approval' AND invalidated_at IS NULL"
        ).fetchone()["n"],
```

- [ ] **Step 4: 通ることを確かめる**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests -q`
Expected: PASS

- [ ] **Step 5: 型を作り直す**

Run: `cd web && npm run typegen`
（`app` を起動して OpenAPI を取り直す。`web/scripts/typegen.mjs` の使い方は
`docs/development.md` を見る。差分は `web/src/api/types.ts` と `web/openapi.json`）

- [ ] **Step 6: 変異試験**

`status IN ('detected', 'failed')` に `'merged'` を足す → 「操作できるものだけ数える」が
落ちること。`superseded_by_id IS NULL` を消す → 「supersede 済みは無視」が落ちること。
`d.enabled = 1` を消す → 「休止中の宛先は無視」が落ちること。`EXISTS` を
`count(DISTINCT ...)` の和に変える → 「和を取らない」が落ちること。それぞれ戻す。

- [ ] **Step 7: コミット**

```bash
git add app/src/mediaferry/api/routes_system.py app/tests/test_api_dashboard.py web/src/api/types.ts web/openapi.json
git commit -m "$(cat <<'EOF'
feat(api): ホームの「やること」の材料を /dashboard で返す

ホームは「いま何をすべきか」を状態から導く（§13）。だが /dashboard は結合を
まったく数えていなかったので、画面が /merge-groups を別に叩くことになり、
「画面ごとに数えさせない」という同じ関数の方針と食い違っていた。

unsent_total は宛先ごとの和にしない。2 つの宛先に未送信の 1 件を 2 件と数えると、
ホームの「48 件」と送る画面の実際の件数が合わなくなる。休止中の宛先は送り先に
選べないので、それしか無ければやることとして出さない。
EOF
)"
```

---

## Task 4: 「やること」を導くフック

**Files:**
- Create: `web/src/hooks/useTasks.ts`
- Create: `web/src/hooks/useTasks.test.ts`

**Interfaces:**
- Consumes: `/dashboard` の `merge_candidates` / `unsent_total` / `awaiting_total`（Task 3）
- Produces:
  ```ts
  export type TaskKind = "merge" | "send" | "approve";
  export type Task = { kind: TaskKind; count: number };
  export type DashboardCounts = {
    merge_candidates: number;
    unsent_total: number;
    awaiting_total: number;
  };
  export function tasksFrom(counts: DashboardCounts | null): Task[];
  ```

- [ ] **Step 1: 失敗するテストを書く**

```ts
// web/src/hooks/useTasks.test.ts
// **やることは状態から導く**（§13）。画面が一覧を持たない。

import { describe, expect, it } from "vitest";

import { tasksFrom } from "./useTasks";

describe("やることの導出", () => {
  it("在るものだけを、つなぐ → 送る → 確認 の順で出す", () => {
    expect(tasksFrom({ merge_candidates: 3, unsent_total: 48, awaiting_total: 2 })).toEqual([
      { kind: "merge", count: 3 },
      { kind: "send", count: 48 },
      { kind: "approve", count: 2 },
    ]);
  });

  it("0 のものは出さない", () => {
    expect(tasksFrom({ merge_candidates: 0, unsent_total: 48, awaiting_total: 0 })).toEqual([
      { kind: "send", count: 48 },
    ]);
  });

  it("全部 0 なら空", () => {
    expect(tasksFrom({ merge_candidates: 0, unsent_total: 0, awaiting_total: 0 })).toEqual([]);
  });

  it("まだ読めていない間は空。**0 件と混ぜない**", () => {
    // 読み込み中に「やることはありません」と出すと、直後に 3 件現れて驚かせる。
    expect(tasksFrom(null)).toEqual([]);
  });
});
```

- [ ] **Step 2: 落ちることを確かめる**

Run: `cd web && npx vitest run src/hooks/useTasks.test.ts`
Expected: FAIL（`tasksFrom` が無い）

- [ ] **Step 3: 実装する**

```ts
// web/src/hooks/useTasks.ts
// ホームの「やること」（§13）。**画面が一覧を持たない**：3 つの数から毎回導く。
// 別々の場所で増減するものを画面側に持つと、片方だけ消し忘れる。

const ORDER = [
  { kind: "merge", of: (c: DashboardCounts) => c.merge_candidates },
  { kind: "send", of: (c: DashboardCounts) => c.unsent_total },
  { kind: "approve", of: (c: DashboardCounts) => c.awaiting_total },
] as const;

export function tasksFrom(counts: DashboardCounts | null): Task[] {
  if (counts === null) {
    return [];
  }
  return ORDER.map((row) => ({ kind: row.kind, count: row.of(counts) })).filter(
    (task) => task.count > 0,
  );
}
```

**読めていない間は空で返す。** ただし**ホーム側は `counts === null` を「読み込み中」
として扱い、「やることはありません」を出さない**（Task 5 でそのテストを書く）。

- [ ] **Step 4: 通ることを確かめる**

Run: `cd web && npm run test && npm run typecheck`
Expected: PASS

- [ ] **Step 5: 変異試験**

`filter((task) => task.count > 0)` を `>= 0` にする → 「0 のものは出さない」が落ちること。
`ORDER` の並びを入れ替える → 「つなぐ → 送る → 確認 の順」が落ちること。戻す。

- [ ] **Step 6: コミット**

```bash
git add web/src/hooks/useTasks.ts web/src/hooks/useTasks.test.ts
git commit -m "$(cat <<'EOF'
feat(web): やることを 3 つの数から導く

結合候補・未送信・承認待ちはそれぞれ別の場所で増減する。画面側に一覧を持つと
片方だけ消し忘れるので、/dashboard の数から毎回組み直す。

読めていない間は空を返すが、それを「やることはありません」と書いてはいけない。
直後に 3 件現れて驚かせるので、読み込み中との区別は呼び出し側で付ける。
EOF
)"
```

---

## Task 5: ホーム

**Files:**
- Create: `web/src/screens/Home.tsx`
- Create: `web/src/screens/Home.test.tsx`
- Create: `web/src/components/JobCard.tsx`
- Delete: `web/src/screens/Dashboard.tsx`（Task 12 でまとめて消す。ここでは残しておく）

**Interfaces:**
- Consumes: `tasksFrom`（Task 4）、`Icon`（Task 1）、`progressLine` / `statusLabel`
  （既存の `components/JobProgress.tsx`）
- Produces:
  - `HomeScreen()` — ルート `/`
  - `JobCard({ job, rate, onCancel }: { job: Job; rate: number | null; onCancel?: (jobId: string) => void })`
    — 進行中の作業 1 件。**`onCancel` を渡さなければ中止ボタンを出さない**
    （押した先の扱いを持たない場所に、押せるボタンを置かない）

**見た目:** プロトタイプの `homeScreen()` / `cardBanner()` / `taskCard()` /
`emptyState()` をそのまま写す。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/screens/Home.test.tsx
// ホーム（§13）。**やることが無いときは、無いと書く。**

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../test/api";
import { HomeScreen } from "./Home";

const EMPTY_DASHBOARD = {
  media_total: 0,
  destinations: [],
  running_jobs: 0,
  recent_imports: [],
  orphans: 0,
  missing: 0,
  warnings: [],
  merge_candidates: 0,
  unsent_total: 0,
  awaiting_total: 0,
};

function renderHome() {
  return render(
    <MemoryRouter>
      <HomeScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ホーム", () => {
  it("やることを、在るものだけ出す", async () => {
    stubApi({
      "/dashboard": { ...EMPTY_DASHBOARD, merge_candidates: 3, unsent_total: 48 },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/分かれている動画を 3 本つなぐ/)).toBeInTheDocument());
    expect(screen.getByText(/48 件をまだ送っていません/)).toBeInTheDocument();
    expect(screen.queryByText(/確認があります/)).not.toBeInTheDocument();
  });

  it("やることが 1 つも無ければ、無いと書く", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await waitFor(() =>
      expect(screen.getByText("いま、やることはありません")).toBeInTheDocument(),
    );
  });

  it("読み込み中は「やることはありません」を出さない", () => {
    // **0 件と読み込み中を混ぜない。** 直後に 3 件現れると驚かせる。
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    expect(screen.queryByText("いま、やることはありません")).not.toBeInTheDocument();
  });

  it("挿さっているカードを、信頼していなければそう書く", async () => {
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "OSMO",
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM がある",
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText("初めて見るカードです")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "このカードを信頼する" })).toBeInTheDocument();
  });

  it("進行中の作業があれば、ファイル名と件数で出す", async () => {
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-18T05:00:00Z",
            started_at: "2026-08-18T05:00:00Z",
            progress: {
              phase: "copy",
              rel_path: "DCIM/100MEDIA/DJI_0043.MP4",
              file_index: 12,
              file_count: 87,
              bytes_done: 1024,
              bytes_total: 4096,
            },
          },
        ],
      },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/12\/87 件/)).toBeInTheDocument());
    expect(screen.getByText(/DJI_0043\.MP4/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 落ちることを確かめる**

Run: `cd web && npx vitest run src/screens/Home.test.tsx`
Expected: FAIL（`Home` が無い）

- [ ] **Step 3: `JobCard.tsx` を書く**

`progressLine(progress, rate)` はそのまま使う（**「12 / 87 件」の規則は §13 で、
`test_api_listing.py` もそれを根拠にしている**）。速度は `Jobs.tsx` の
`averageRate` をそのまま持ってくる —— **描画中に `Date.now()` を呼ばない**
（呼ぶと、たまたま起きた再描画で値が変わる）。

- [ ] **Step 4: `Home.tsx` を書く**

- `useQuery<Dashboard>("/dashboard")`、`useQuery<Devices>("/devices")`、
  `useQuery<Jobs>("/jobs")`、`useQuery<Settings>("/settings")` を張る
- `useEvents()` + `useReloadOnEvents(received, reload)` を 4 つ全部に張る
  （§13 の「画面を再読み込みせずに進む」）
- カードの帯は `autoImportOutlook` / `autoImportState`（**既存の `Devices.tsx` から
  そのまま持ってくる**。Task 9 で `work/CardDetail.tsx` へ移すので、この時点では
  `Devices.tsx` から `import` してよい）
- 信頼のダイアログは既存の `ConfirmDialog` の `trust_volume`。**文言を変えない**
- やることの表示は `tasksFrom(dashboard.data)` の結果を `map` する。
  **`dashboard.data === null` のときは「やることはありません」を出さず、
  「読み込み中…」を出す**

- [ ] **Step 5: 通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 6: 変異試験**

`dashboard.data === null` の分岐を消す → 「読み込み中は出さない」が落ちること。
`tasksFrom` の結果を `length === 0` で判定しているところを `< 0` にする →
「無いと書く」が落ちること。戻す。

- [ ] **Step 7: コミット**

```bash
git add web/src/screens/Home.tsx web/src/screens/Home.test.tsx web/src/components/JobCard.tsx
git commit -m "$(cat <<'EOF'
feat(web): ホームを「いま何をすべきか」の画面にする

旧ダッシュボードは宛先ごとの 6 列の数字を出していたが、家族が読んで次の一手を
決められる形ではなかった。カードの帯・やること・進行中を上から順に置く。

読み込み中と 0 件を混ぜない。まだ読めていない間に「やることはありません」と
書くと、直後に 3 件現れて驚かせる。
EOF
)"
```

---

## Task 6: 写真

**Files:**
- Create: `web/src/screens/Photos.tsx`
- Create: `web/src/screens/Photos.test.tsx`
- Create: `web/src/components/MediaTile.tsx`

**Interfaces:**
- Consumes: `Icon`、`useQuery`、`useEvents`、`useReloadOnEvents`
- Produces:
  - `PhotosScreen()` — ルート `/photos`
  - `MediaTile({ media, selected, onToggle }: { media: Media; selected: boolean; onToggle?: (id: string) => void })`
  - `groupByDate(media: Media[]): { label: string; items: Media[] }[]` — `captured_at` の
    日付部分でまとめる。**並びは API の順（`captured_at DESC, id DESC`）を保つ**

**見た目:** プロトタイプの `photosScreen()` と `tile()`。

**絞り込み:** すべて / まだ送っていない / 確認が要る / 動画 / 送信済み。
**「まだ送っていない」「確認が要る」「送信済み」は宛先ごと**なので、
`destination_id` を伴わなければならない（API が 400 を返す）。**宛先が 2 つ以上
あるときは、チップの隣に宛先の選択を出す。1 つのときは黙って使う。**

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/screens/Photos.test.tsx
// 写真（§13）。日付でまとめ、1 枚ごとに状態の印を出す。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../test/api";
import { PhotosScreen, groupByDate } from "./Photos";

const media = (id: string, captured_at: string, extra = {}) => ({
  id,
  rel_path: `library/x/${id}.JPG`,
  kind: "photo",
  captured_at,
  size_bytes: 1024,
  ...extra,
});

describe("日付のまとめ", () => {
  it("同じ日を 1 つにまとめ、API の並びを崩さない", () => {
    const rows = [
      media("a", "2026-08-18T15:12:00+09:00"),
      media("b", "2026-08-18T14:03:00+09:00"),
      media("c", "2026-08-17T09:12:00+09:00"),
    ];
    expect(groupByDate(rows).map((g) => g.items.map((m) => m.id))).toEqual([["a", "b"], ["c"]]);
  });

  it("撮影日時が読めない行も落とさない", () => {
    // **落とすと、画面の件数と API の total が食い違う。**
    expect(groupByDate([media("a", "")]).flatMap((g) => g.items)).toHaveLength(1);
  });
});

describe("写真の画面", () => {
  beforeEach(() => {
    document.cookie = "XSRF-TOKEN=token; path=/";
  });
  afterEach(() => vi.restoreAllMocks());

  it("宛先ごとの絞り込みには destination_id を必ず付ける", async () => {
    const { calls } = stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /まだ送っていない/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /まだ送っていない/ }));
    await waitFor(() =>
      expect(
        calls().some((c) => c.path.includes("status=unsent") && c.path.includes("destination_id=d1")),
      ).toBe(true),
    );
  });

  it("選んだものは、絞り込みで隠れても覚える", async () => {
    // **表示中の行から合計を出さない**（隠した分が抜けて確認の数字が食い違う）。
    stubApi({
      "/media": {
        media: [media("a", "2026-08-18T14:03:00+09:00", { size_bytes: 2048 })],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /a\.JPG/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /a\.JPG/ }));
    expect(screen.getByText(/1 件を選択中/)).toBeInTheDocument();
    expect(screen.getByText(/2 KiB/)).toBeInTheDocument();
  });

  it("当てはまるものが無ければ、そう書く", async () => {
    stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByText(/当てはまる写真はありません/)).toBeInTheDocument(),
    );
  });
});
```

`stubApi` は `calls()` も返すように `web/src/test/api.ts` を直す（Task 1 で作った形に
`calls` を返り値へ足す）。

- [ ] **Step 2: 落ちることを確かめる**

Run: `cd web && npx vitest run src/screens/Photos.test.tsx`
Expected: FAIL

- [ ] **Step 3: `MediaTile.tsx` を書く**

- サムネイルは `<img src={`/api/media/${media.id}/thumbnail`} alt="" loading="lazy" />`
- **状態の印は形と文字の両方で伝える**（§13。色だけにしない）：未送信は白縁の丸、
  送信済みはチェック、確認が要るは `!`
- ボタンの `aria-label` は `rel_path` の末尾（テストがこれで掴む）
- 動画は長さを右下に出す

- [ ] **Step 4: `Photos.tsx` を書く**

- 絞り込みは `useSearchParams` に持つ（**URL に残す**。ホームの「どれか見る」から
  `/photos?status=unsent&destination_id=…` で飛べる）
- 選択は `Map<string, number>`（id → `size_bytes`）。**既存の `Library.tsx` の
  やり方をそのまま引き継ぐ**
- 凡例を絞り込みの下に常に出す（§13）
- 選択があるときだけ操作の帯を出し、「送る」で
  `navigate("/send", { state: { ids: [...selected.keys()] } })`

- [ ] **Step 5: 通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 6: 変異試験**

合計を `selected` ではなく表示中の行から計算するように変える → 「隠れても覚える」が
落ちること。`destination_id` を付けずに `status` を送るようにする → 「必ず付ける」が
落ちること。`groupByDate` で `captured_at` が空の行を捨てる → 「落とさない」が落ちること。
戻す。

- [ ] **Step 7: コミット**

```bash
git add web/src/screens/Photos.tsx web/src/screens/Photos.test.tsx web/src/components/MediaTile.tsx web/src/test/api.ts
git commit -m "$(cat <<'EOF'
feat(web): 写真を日付のグリッドにし、1 枚ごとに状態を出す

旧ライブラリは 64px のサムネイルを表の中に置いていたので、写真を選ぶ画面として
機能していなかった。日付でまとめたグリッドにして、宛先ごとの状態を印で出す。

印は色だけで伝えない。形（丸・チェック・!）を変え、凡例を常に見えるところに置く。

選んだものは絞り込みで隠れても覚える。表示中の行から合計を出すと、隠した分が
抜けて確認の数字が実際と食い違う（旧ライブラリから引き継ぐ判断）。
EOF
)"
```

---

## Task 7: 送る・送信中

**この計画でいちばん壊してはいけないところ。** 送信は取り消せない（§13）。

**Files:**
- Create: `web/src/screens/work/Send.tsx`
- Create: `web/src/screens/work/Send.test.tsx`
- Create: `web/src/screens/work/Sending.tsx`

**Interfaces:**
- Consumes: `ConfirmDialog` の `upload`（**既存の文言をそのまま使う**）、`summarise`
  （既存の `Library.tsx` から `Send.tsx` へ移す）
- Produces:
  - `SendScreen()` — ルート `/send`。`useLocation().state?.ids` があれば
    「写真の画面で選んだもの」が既定
  - `SendingScreen()` — ルート `/sending`
  - `summarise(total, rejected, failures, started): string`（移設。**中身を変えない**）

**見た目:** プロトタイプの `sendScreen()` / `sendingScreen()` / `dialogView()` の `send`。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/screens/work/Send.test.tsx
// 送る（§13）。**取り消せないので、件数・合計サイズ・送り先を出してから確認する。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { SendScreen, summarise } from "./Send";

const DESTINATIONS = {
  destinations: [
    { id: "d1", name: "家の Immich", enabled: true },
    { id: "d2", name: "旅行用 Immich", enabled: false },
  ],
};

function renderSend(ids?: string[]) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/send", state: ids ? { ids } : undefined }]}>
      <SendScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("送る", () => {
  it("休止中の宛先は選べず、理由が出る", async () => {
    stubApi({ "/destinations": DESTINATIONS, "/media": { media: [], total: 0, page: 1, page_size: 50 } });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /旅行用 Immich/ })).toBeDisabled());
    expect(screen.getByText(/休止中なので選べません/)).toBeInTheDocument();
  });

  it("既定は「まだ送っていないもの、すべて」", async () => {
    stubApi({ "/destinations": DESTINATIONS, "/media": { media: [], total: 48, page: 1, page_size: 50 } });
    renderSend();
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /まだ送っていないもの、すべて/ })).toBeChecked(),
    );
  });

  it("写真の画面から来たときは、その選択が既定", async () => {
    stubApi({ "/destinations": DESTINATIONS, "/media": { media: [], total: 48, page: 1, page_size: 50 } });
    renderSend(["m1", "m2"]);
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /写真の画面で選んだもの/ })).toBeChecked(),
    );
  });

  it("確認の前に API を叩かない", async () => {
    // **押しただけでは送らない**（screens.test.tsx が各画面に課していた規則）。
    const { calls } = stubApi({
      "/destinations": DESTINATIONS,
      "/media": { media: [{ id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "", size_bytes: 1024 }], total: 1, page: 1, page_size: 50 },
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("この内容で送りますか");
    expect(calls().some((c) => c.method === "POST")).toBe(false);
  });

  it("確認には件数・合計サイズ・送り先を出す", async () => {
    stubApi({
      "/destinations": DESTINATIONS,
      "/media": { media: [{ id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "", size_bytes: 2048 }], total: 1, page: 1, page_size: 50 },
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("1 件");
    expect(dialog).toHaveTextContent("2 KiB");
    expect(dialog).toHaveTextContent("家の Immich");
  });

  it("送り先を選んでいなければ確認へ進めない", async () => {
    stubApi({ "/destinations": { destinations: [] }, "/media": { media: [], total: 0, page: 1, page_size: 50 } });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeDisabled());
  });
});

describe("送った結果の 1 文", () => {
  it("断られた組と、開始に失敗した宛先を隠さない", () => {
    expect(summarise(3, [{ reason: "結合中" }], ["旅行用"], 1)).toContain("送れない組が 1 件");
    expect(summarise(3, [{ reason: "結合中" }], ["旅行用"], 1)).toContain("旅行用");
  });
});
```

- [ ] **Step 2: 落ちることを確かめる**

Run: `cd web && npx vitest run src/screens/work/Send.test.tsx`
Expected: FAIL

- [ ] **Step 3: `summarise` を移す**

`Library.tsx` から `Send.tsx` へ**中身を変えずに**移す。既存の
`screens.test.tsx` の `summarise` のテストも `Send.test.tsx` へ移す。

- [ ] **Step 4: `Send.tsx` を書く**

3 段は 1 画面に縦に並べる（別ページにしない。**戻る操作が増えるだけで、選び直しが
しにくくなる**）。

- 対象の解決：`preset === "selection"` なら `location.state.ids`、`"unsent"` なら
  `/media?destination_id=<最初に選んだ宛先>&status=unsent&page_size=200` の `media`、
  `"day0"` なら同じものを `captured_from` / `captured_to` で当日に絞る、
  `"pick"` は `/photos?status=unsent&destination_id=…` へ `navigate` する
- **送信そのものは既存の 2 段階をそのまま使う**（`POST /uploads` → 宛先ごとの
  `POST /destinations/{id}/upload`）。`Library.tsx` の `send()` を移し、
  **組ごとの結果を読む・受け付けられた組がある宛先だけ送信を始める・一部の宛先で
  失敗しても成功した分は進める**を変えない
- 成功したら `navigate("/sending")`

- [ ] **Step 5: `Sending.tsx` を書く**

- `/jobs` を 2 秒ごとに引き直す（**進捗はイベントではない**。既存の `Jobs.tsx` の
  コメントの理由をそのまま引き継ぐ）
- **「この画面を閉じても送信は続きます」と書き、閉じるボタンを置く**（§13）。
  閉じると `/` へ戻る。ジョブは止めない
- 「送るのをやめる」は `POST /jobs/{id}/cancel`

- [ ] **Step 6: 通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 7: 変異試験**

確認ダイアログを経ずに送るようにする → 「確認の前に API を叩かない」が落ちること。
`disabled` の条件から送り先の有無を外す → 「選んでいなければ進めない」が落ちること。
`summarise` から `rejected` の節を消す → 「隠さない」が落ちること。戻す。

- [ ] **Step 8: コミット**

```bash
git add web/src/screens/work/Send.tsx web/src/screens/work/Send.test.tsx web/src/screens/work/Sending.tsx
git commit -m "$(cat <<'EOF'
feat(web): 送るを「宛先 → 対象 → 確認」の 3 段にする

取り違えたまま送ると取り消せないので、宛先を先に決める。対象を先に選ばせると、
宛先は最後の惰性で決まる。

対象の既定を「まだ送っていないもの、すべて」に置く。旧ライブラリには全選択が
無く、48 件を送るのに 48 回のチェックが要った。

送信そのものの 2 段階（POST /uploads → 宛先ごとの upload）は変えない。断られた
組と開始に失敗した宛先を隠さない 1 文も、そのまま持ってきた。

送信中の画面に閉じるボタンを置く。「閉じても続きます」と書いてある以上、
閉じられないと矛盾する。
EOF
)"
```

---

## Task 8: つなぐ・確認

**Files:**
- Create: `web/src/screens/work/Merge.tsx`（旧 `Merges.tsx` の**生きている候補だけ**）
- Create: `web/src/screens/work/Approve.tsx`（旧 `Approvals.tsx`）
- Create: `web/src/screens/work/Merge.test.tsx`
- Create: `web/src/screens/work/Approve.test.tsx`

**Interfaces:**
- Consumes: `ConfirmDialog` の `discard_merge_group` / `adopt_failed_merge` /
  `remerge_group` / `approve_datetime`（**文言を変えない**）
- Produces: `MergeScreen()` — `/merge`、`ApproveScreen()` — `/approve`

**移すもの・置いていくもの（`Merges.tsx` の 374 行の分割）**

| 旧 `Merges.tsx` の部分 | 行き先 |
| --- | --- |
| 候補の一覧（`status` が `detected` / `failed` / `merged`）、手でグループを作る | `work/Merge.tsx` |
| 破棄した組み合わせ（`?status=skipped`） | `details/MergeHistory.tsx`（Task 11） |
| もう使われていない出力（`/media/stale-derived`） | `details/MergeHistory.tsx`（Task 11） |
| 構成を変えるダイアログ | `work/Merge.tsx` |

**文言の言い換え（§13 の「画面に出す言葉」）**

| いま | これから |
| --- | --- |
| 「破棄する」 | **「これは別々」** |
| 「候補を検出する」 | **「分かれた動画を探す」** |
| 「不合格でも採用する」 | **「中身を見て、これを使う」** |

**確認ダイアログの本文は変えない。** `discard_merge_group` の文（「公開済みの
ファイル N 件は消えませんが、選択肢には出なくなります」）が、言い換えの根拠に
なっている。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/screens/work/Merge.test.tsx
// つなぐ（§13）。**なぜまとまったかが分かるようにする。**

it("なぜこの並びなのかを、構成とギャップで出す", async () => {
  stubApi({
    "/merge-groups?status=skipped": { groups: [] },
    "/merge-groups": {
      groups: [
        {
          id: "g1",
          status: "detected",
          detected_by: "auto",
          input_digest: "d",
          verification: null,
          superseded_by_id: null,
          output: null,
          members: [
            { position: 0, media_file_id: "m1", rel_path: "library/DJI_0001.MP4", size_bytes: 4294967296, duration_seconds: 600, captured_at: "2026-08-18T14:03:00+09:00" },
            { position: 1, media_file_id: "m2", rel_path: "library/DJI_0002.MP4", size_bytes: 4294967296, duration_seconds: 600, captured_at: "2026-08-18T14:13:00+09:00" },
          ],
        },
      ],
    },
    "/media": { media: [] },
    "/media/stale-derived": { stale: [] },
  });
  render(<MemoryRouter><MergeScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/2 つに分かれています/)).toBeInTheDocument());
  expect(screen.getByText(/DJI_0001\.MP4/)).toBeInTheDocument();
  expect(screen.getByText(/4 GiB/)).toBeInTheDocument();
});

it("「これは別々」は確認を取ってから API を叩く", async () => {
  // 本文は既存の discard_merge_group をそのまま使う。
  const { calls } = stubApi({ /* 上と同じ */ });
  render(<MemoryRouter><MergeScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("button", { name: "これは別々" })).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "これは別々" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("公開済みのファイル");
  expect(calls().some((c) => c.method === "PATCH")).toBe(false);
});

it("破棄した組み合わせと、使っていない出力は、ここには出さない", async () => {
  // **操作できないものを混ぜると「いま何が起きるのか」が読めなくなる**（設定 › 詳しい情報へ）。
  stubApi({
    "/merge-groups?status=skipped": { groups: [{ id: "g9", status: "skipped", members: [], detected_by: "auto", input_digest: "d", verification: null, superseded_by_id: null, output: null }] },
    "/merge-groups": { groups: [] },
    "/media": { media: [] },
    "/media/stale-derived": { stale: [{ id: "s1", rel_path: "derived/old.MP4", size_bytes: 1, captured_at: "", reason: "superseded" }] },
  });
  render(<MemoryRouter><MergeScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/つなぐものはありません/)).toBeInTheDocument());
  expect(screen.queryByText(/破棄した組み合わせ/)).not.toBeInTheDocument();
  expect(screen.queryByText(/derived\/old\.MP4/)).not.toBeInTheDocument();
});
```

```tsx
// web/src/screens/work/Approve.test.tsx
it("読めなかった値を空欄にしない", async () => {
  // **空欄は「変更なし」に見える。**
  stubApi({
    "/uploads?state=awaiting_datetime_approval": {
      records: [{ id: "r1", destination_id: "d1", media_file_id: "m1", origin: "pre_existing", remote_current: null, proposed: "2026-08-14 20:02", remote_checked_at: null, identical: false }],
    },
  });
  render(<MemoryRouter><ApproveScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText("（読めませんでした）")).toBeInTheDocument());
});

it("却下はリモートに触らないと画面に書く", async () => {
  stubApi({ "/uploads?state=awaiting_datetime_approval": { records: [] } });
  render(<MemoryRouter><ApproveScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/Immich には何も起きません/)).toBeInTheDocument());
});
```

- [ ] **Step 2: 落ちることを確かめる**

Run: `cd web && npx vitest run src/screens/work`
Expected: FAIL

- [ ] **Step 3: `Merge.tsx` を書く**

旧 `Merges.tsx` から候補まわりを移す。**`useQuery("/merge-groups?status=skipped")` と
`useQuery("/media/stale-derived")` は張らない**（Task 11 が持つ）。

- [ ] **Step 4: `Approve.tsx` を書く**

旧 `Approvals.tsx` を移し、表を 1 件ずつのカードにする（プロトタイプの
`approvalsScreen()`）。**「（不明）」を「（読めませんでした）」に変える以外、
判断は何も変えない。**

- [ ] **Step 5: 通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 6: 変異試験**

`Merge.tsx` に `status=skipped` の一覧を戻す → 「ここには出さない」が落ちること。
`remote_current ?? "（読めませんでした）"` を `remote_current ?? ""` にする →
「空欄にしない」が落ちること。戻す。

- [ ] **Step 7: コミット**

```bash
git add web/src/screens/work/Merge.tsx web/src/screens/work/Approve.tsx web/src/screens/work/Merge.test.tsx web/src/screens/work/Approve.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): つなぐと確認を、やることから開く作業ページにする

旧 Merges.tsx は 374 行で 3 つのことをしていた（生きている候補・破棄した記録・
使っていない出力）。操作できないものが同じ画面に並ぶと「いま何が起きるのか」が
読めないので、記録の 2 つは設定 › 詳しい情報へ移す。

画面の言葉を「破棄する」から「これは別々」に変える。破棄が消すのは候補であって
ファイルではない（確認ダイアログの本文がそう書いている）。

確認では、読めなかった日時を空欄にしない。空欄は「変更なし」に見える。
EOF
)"
```

---

## Task 9: カードの中身

**Files:**
- Create: `web/src/screens/work/CardDetail.tsx`（旧 `Devices.tsx`）
- Create: `web/src/screens/work/CardDetail.test.tsx`

**Interfaces:**
- Consumes: `ConfirmDialog` の `trust_volume`
- Produces:
  - `CardDetailScreen()` — `/card`
  - `autoImportOutlook(volume, autoImport): Outlook` と `autoImportState(volume, autoImport): string`
    を**この場所へ移す**（`Home.tsx` がここから `import` するように直す）

**変えないもの（ここは触ると事故る）**

- `autoImportOutlook` の 3 状態（`starts` / `pending` / `blocked`）と、**画面と確認
  ダイアログが同じ関数を見る**こと
- `pending` を `blocked` と混ぜないこと、**条件形で書く**こと
- `/settings` が未解決の間は `autoImport` を `null` のまま持ち、**信頼の操作を
  させない**こと
- **判定の理由は、一致したボリュームでも出す**こと
- 「対象だが中身が無い」（`provisional`）を「対象外」と書かないこと

- [ ] **Step 1: 既存のテストを移す**

`screens.test.tsx` の「デバイス」に関する `describe` を丸ごと
`CardDetail.test.tsx` へ移す。**アサーションを変えない**（文言を変えるのは見出しと
ボタンのラベルだけで、`autoImportState` の文は変えない）。

- [ ] **Step 2: 失敗することを確かめる**

Run: `cd web && npx vitest run src/screens/work/CardDetail.test.tsx`
Expected: FAIL（`CardDetail` が無い）

- [ ] **Step 3: `CardDetail.tsx` を書く**

旧 `Devices.tsx` をそのまま移し、体裁だけプロトタイプの `cardScreen()` に合わせる。
複数のボリュームが同時に見えることがある（**Osmo は内蔵ストレージと SD が同じ形で
見える**）ので、**一覧のままにする**。1 枚だけを前提にしない。

- [ ] **Step 4: 通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 5: 変異試験**

`autoImport === null` のとき信頼ボタンを押せるようにする → 対応するテストが落ちること。
`identity_confidence !== "high"` を `blocked` に倒す → `pending` の文言のテストが
落ちること。戻す。

- [ ] **Step 6: コミット**

```bash
git add web/src/screens/work/CardDetail.tsx web/src/screens/work/CardDetail.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): デバイスの画面を「カードの中身」に移す

判定の理由・確度・信頼の同意はそのまま。autoImportOutlook の 3 状態も、画面と
確認ダイアログが同じ関数を見る作りも変えていない（同意の内容と実挙動がずれる）。

複数のボリュームを同時に扱う作りも残す。Osmo は内蔵ストレージと SD カードが
同じ形で見えるので、1 枚だけを前提にすると片方が操作できない。
EOF
)"
```

---

## Task 10: 設定

**Files:**
- Modify: `web/src/screens/Settings.tsx`（トップだけに縮める）
- Create: `web/src/screens/settings/Destinations.tsx`（旧 `Destinations.tsx`）
- Create: `web/src/screens/settings/Profiles.tsx`（旧 `Settings.tsx` の YAML 編集）
- Create: `web/src/screens/settings/General.tsx`（旧 `Settings.tsx` の設定一覧）
- Create: `web/src/screens/Settings.test.tsx`

**Interfaces:**
- Produces: `SettingsScreen()` — `/settings`、`DestinationsScreen()` — `/settings/destinations`、
  `ProfilesScreen()` — `/settings/profiles`、`GeneralScreen()` — `/settings/general`

**設定のトップに置くもの**（プロトタイプの `settingsScreen()`）

1. 「信頼したカードを自動で取り込む」のトグル（`AUTO_IMPORT`）。**「送信はどちらの
   設定でも常に手動です」を添える**
2. 送り先の要約 + `/settings/destinations` への入口
3. カメラの種類の要約 + `/settings/profiles` への入口
4. 詳しい情報（作業の履歴 / 結合の記録 / 使っていない派生物 / 接続中のカード）への入口
5. env 由来の設定一覧への入口（`/settings/general`）

**変えないもの**

- **env 由来は錠前アイコン付きの読み取り専用**（`locked` / `writable`）
- **ビルトインのプロファイルには編集も archive も出さない。「複製して変える」だけ**
- **複製は slug を先に決めさせる**（作成後は変えられない）
- **YAML の構文エラーはサーバへ送る前に落とす**
- **既存の API キーは出さない**（読み出しの API を作らない）
- **同じライブラリを指す宛先があれば知らせる**（`sharesLibrary`）

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/screens/Settings.test.tsx
it("env 由来の設定は錠前付きで、変えられない", async () => {
  stubApi({
    "/settings": { settings: [{ key: "DATA_ROOT", value: "/data", source: "env", locked: true, tier: "a", writable: false }], warnings: [] },
    "/profiles": { profiles: [] },
    "/devices": { volumes: [] },
    "/destinations": { destinations: [] },
  });
  render(<MemoryRouter><GeneralScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByLabelText(/DATA_ROOT/)).toBeDisabled());
});

it("送信は常に手動だと、自動取り込みの説明に書く", async () => {
  stubApi({ "/settings": { settings: [], warnings: [] }, "/destinations": { destinations: [] }, "/profiles": { profiles: [] } });
  render(<MemoryRouter><SettingsScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/送信はどちらの設定でも常に手動/)).toBeInTheDocument());
});

it("ビルトインには複製しか出さない", async () => {
  stubApi({
    "/profiles": { profiles: [{ slug: "dji-osmo", name: "DJI", revision: 1, revision_id: "r1", builtin: true, archived: false }] },
    "/settings": { settings: [], warnings: [] },
    "/devices": { volumes: [] },
  });
  render(<MemoryRouter><ProfilesScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("button", { name: /複製/ })).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /^編集/ })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 落ちることを確かめる**

Run: `cd web && npx vitest run src/screens/Settings.test.tsx`
Expected: FAIL

- [ ] **Step 3: 3 つに切り出す**

旧 `Settings.tsx`（422 行）を `Profiles.tsx` と `General.tsx` に割り、旧
`Destinations.tsx`（237 行）を `settings/Destinations.tsx` へ移す。**判断は何も
変えない。** 文言だけ「転送先」→「送り先」に直す（`StackSkips` の見出しは
「スタックの見送り」のまま —— これは中身の説明であって、内部の名前ではない）。

- [ ] **Step 4: `Settings.tsx` をトップだけに縮める**

- [ ] **Step 5: 通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck`
Expected: PASS

- [ ] **Step 6: 変異試験**

ビルトインにも編集ボタンを出す → 「複製しか出さない」が落ちること。`locked` を
無視して `disabled` を外す → 「変えられない」が落ちること。戻す。

- [ ] **Step 7: コミット**

```bash
git add web/src/screens/Settings.tsx web/src/screens/Settings.test.tsx web/src/screens/settings/
git commit -m "$(cat <<'EOF'
feat(web): 設定を入口だけにし、中身を 3 つに割る

旧 Settings.tsx は 422 行で設定一覧とプロファイル編集を両方持ち、そこへ転送先
（237 行）を足すと 1 ファイルで 600 行を超える。トップは要約と入口だけにして、
送り先・カメラの種類・env 由来の設定に割る。

env 由来は錠前付きの読み取り専用、ビルトインは複製だけ、YAML の構文エラーは
送る前に落とす、という判断はどれも変えていない。
EOF
)"
```

---

## Task 11: 詳しい情報（作業の履歴・結合の記録）

**Files:**
- Create: `web/src/screens/details/JobHistory.tsx`（旧 `Jobs.tsx`）
- Create: `web/src/screens/details/MergeHistory.tsx`（旧 `Merges.tsx` の記録の部分）
- Create: `web/src/screens/details/MergeHistory.test.tsx`

**Interfaces:**
- Produces: `JobHistoryScreen()` — `/settings/jobs`、`MergeHistoryScreen()` —
  `/settings/merge-history`

**変えないもの**

- **破棄の記録を消す確認**（`delete_merge_history`）の本文。「もう一度探すと同じ
  組み合わせがまた出ることがあります」が、消してよいかの判断材料になっている
- **使っていない出力の一覧は `/media/stale-derived`**。実機で 66 GiB がそこに
  残っていた経路なので、**入口を消さない**
- 進捗の接続が切れていることを画面に出す（`connected`）

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/screens/details/MergeHistory.test.tsx
it("使っていない出力を、ファイルとして消せる", async () => {
  const { calls } = stubApi({
    "/merge-groups?status=skipped": { groups: [] },
    "/media/stale-derived": { stale: [{ id: "s1", rel_path: "derived/old.MP4", size_bytes: 1024, captured_at: "", reason: "superseded" }] },
  });
  render(<MemoryRouter><MergeHistoryScreen /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/derived\/old\.MP4/)).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: /このファイルを消す/ }));
  expect(screen.getByRole("dialog")).toHaveTextContent("元になったファイルは残ります");
  expect(calls().some((c) => c.method === "DELETE")).toBe(false);
});
```

- [ ] **Step 2〜4:** 落ちることを確かめ、旧 `Jobs.tsx` と `Merges.tsx` の該当部分を
      移し、通ることを確かめる（`cd web && npm run test`）

- [ ] **Step 5: 変異試験**

確認を経ずに `DELETE` するようにする → テストが落ちること。戻す。

- [ ] **Step 6: コミット**

```bash
git add web/src/screens/details/
git commit -m "$(cat <<'EOF'
feat(web): 履歴と記録を設定の下へ移す

ふだんは見なくてよいが、困ったときに要る。使っていない出力の一覧は入口を
消さない。実機ではそこに 66 GiB が残っていた。
EOF
)"
```

---

## Task 12: ルーティングを差し替え、旧画面を消す

**Files:**
- Modify: `web/src/App.tsx`
- Delete: `web/src/screens/Dashboard.tsx`、`Devices.tsx`、`Library.tsx`、`Merges.tsx`、
  `Approvals.tsx`、`Jobs.tsx`、`Destinations.tsx`、`screens.test.tsx`

**Interfaces:**
- Consumes: Task 5〜11 のすべての画面
- Produces: ルート表

| パス | 画面 | ナビの現在地 |
| --- | --- | --- |
| `/` | `HomeScreen` | ホーム |
| `/card` | `CardDetailScreen` | ホーム |
| `/merge` | `MergeScreen` | ホーム |
| `/approve` | `ApproveScreen` | ホーム |
| `/send` | `SendScreen` | ホーム |
| `/sending` | `SendingScreen` | ホーム |
| `/photos` | `PhotosScreen` | 写真 |
| `/settings` | `SettingsScreen` | 設定 |
| `/settings/destinations` | `DestinationsScreen` | 設定 |
| `/settings/profiles` | `ProfilesScreen` | 設定 |
| `/settings/general` | `GeneralScreen` | 設定 |
| `/settings/jobs` | `JobHistoryScreen` | 設定 |
| `/settings/merge-history` | `MergeHistoryScreen` | 設定 |

- [ ] **Step 1: `App.tsx` を差し替える**

`Layout` に `taskCount` を渡すため、`App` で `/dashboard` を 1 回引く。
**`Layout` の中で引かない** —— 画面ごとに数えさせない方針と、ログイン前に叩かない
ことの両方が要る。

- [ ] **Step 2: 旧画面を消す**

```bash
git rm web/src/screens/Dashboard.tsx web/src/screens/Devices.tsx web/src/screens/Library.tsx \
       web/src/screens/Merges.tsx web/src/screens/Approvals.tsx web/src/screens/Jobs.tsx \
       web/src/screens/Destinations.tsx web/src/screens/screens.test.tsx
```

**消す前に、`screens.test.tsx` のアサーションが 1 つ残らず新しいテストへ移って
いることを確かめる。** 移し先が無いものが 1 つでもあれば、それは消してよい
テストではない。

- [ ] **Step 3: 全部通ることを確かめる**

Run: `cd web && npm run test && npm run lint && npm run typecheck && npm run build`
Expected: PASS（`build` まで通す。未使用の import が残っていると `tsc -b` で落ちる）

- [ ] **Step 4: コミット**

```bash
git add -A web/src
git commit -m "$(cat <<'EOF'
refactor(web): ルーティングを 3 タブに差し替え、旧画面を消す

作業ページはホームの下位のルートに置く。ナビの現在地はパスの前方一致で決める
ので、/merge や /send を開いている間もホームが現在地のままになる。

taskCount は App で 1 回引いて Layout へ渡す。Layout の中で引くと、画面ごとに
数えることになり、ログイン前にも叩いてしまう。
EOF
)"
```

---

## Task 13: E2E とドキュメント

**Files:**
- Modify: `web/e2e/journey.spec.ts`、`phase5.spec.ts`、`phase6.spec.ts`
- Modify: `docs/user-guide.md`
- Modify: `docs/development.md`（画面の一覧に触れている箇所）
- Modify: `README.md`

- [ ] **Step 1: E2E を新しい動線に合わせる**

E2E は**実プロセスとブラウザ**で動く（`web/e2e/harness.ts`）。画面の名前で掴んで
いるところを直す。**通す筋を「ホーム → カードを信頼 → 取り込む → つなぐ → 送る →
確認」に変える** —— これが §13 の主要動線そのものなので、E2E がそれを通ることに
意味がある。

**E2E でしか捕まらないものがある**（vitest は画面の一部しか見ていないので、
無いことが仕様に見える。「判定の理由が一致したボリュームでも出る」がそれで
見つかった）。**次の 3 つは E2E で見る:**

1. ナビの項目が 3 つで、作業ページを開いてもホームが現在地のまま
2. やることが空のときに「いま、やることはありません」が出る
3. 送信中の画面を閉じても、ホームの「進行中」に残っている

- [ ] **Step 2: 通ることを確かめる**

Run: `cd web && npm run test:e2e`
Expected: PASS

- [ ] **Step 3: `docs/user-guide.md` を直す**

**旧画面の名前で書かれているところを全部直す。**「はじめの一周」は次の形になる：

1. **設定 › 送り先**で Immich を登録する
2. カードを挿す。**ホーム**に出るので「いま取り込む」
3. そのカードを**信頼する**（以後は挿すだけ）
4. ホームの**やること**に出た「送る」を押す

「2 回目からは『カードを挿す → 送る』の 2 手だけ」はそのまま成り立つ。

- [ ] **Step 4: `README.md` の「できること」の表を直す**

画面の名前が出てくるところだけ。**機能の説明は変えない。**

- [ ] **Step 5: 通ることを確かめる**

Run: `uv run pytest && cd web && npm run test && npm run test:e2e`

- [ ] **Step 6: コミット**

```bash
git add web/e2e docs/user-guide.md docs/development.md README.md
git commit -m "$(cat <<'EOF'
docs: 使い方ガイドと E2E を新しい画面に合わせる

E2E の筋を §13 の主要動線（カードを信頼 → 取り込む → つなぐ → 送る → 確認）
そのものにする。vitest は画面の一部しか見ないので、「無いこと」が仕様に見える
（判定の理由が一致したボリュームでも出る、という抜けが E2E で見つかった）。
EOF
)"
```

---

## この計画を書いたときに確かめたこと

**実装前にコードを読んで分かった、計画に影響した事実。**

| 事実 | どう効いたか |
| --- | --- |
| `/media?status=unsent` は「`complete` が無いもの」、`/dashboard` の `unsent` は「記録がまったく無いもの」。**同じ言葉が 2 つの意味を持っていた** | Task 2 を独立したタスクにした |
| どちらも §10 の「既定で選択肢に出すもの」を見ていない。**結合グループの構成ファイルまで未送信に数える** | 同上。数えたまま送ると `POST /uploads` が組を断る |
| `Library.tsx` に**全選択が無い**（行ごとのチェックボックスだけ） | 送るの既定を「まだ送っていないもの、すべて」にする根拠 |
| `/dashboard` は結合をまったく数えていない | Task 3。画面から `/merge-groups` を別に叩かせない |
| `SelectionService._verification_passed` が見ているのは `passed`（真の bool）。`Merges.tsx` は `verification.verdict` を読んでいる | Task 2 の SQL は `passed` を見る。**`verdict` との食い違いはこの計画では触らない**（別の話） |
| `Merges.tsx` は 374 行で 3 つのことをしている | Task 8 と 11 で割る |
| `Settings.tsx` 422 行 + `Destinations.tsx` 237 行 | Task 10 で 3 つに割る |
| digest の一致（§10 の derived 条件）は Python での再計算が要る | Task 2 の SQL から外し、**過大計上が残ることを記録した** |

## 積み残し（この計画では触らない）

- **`verification_json` の `passed` と `verdict` の食い違い。** 画面は `verdict` を
  読み、選択の判定は `passed` を読んでいる。どちらが正かは結合の実装を読まないと
  決められないので、この計画では**触らずに、両方が現状のまま動くようにする**
- **`/media` の pagination。** 写真の画面は `page_size=200` で足りるが、数万件に
  なると足りない。無限スクロールは別の話
- **サムネイルの取りこぼし。** `MediaTile` は `<img>` の失敗を握らない。実機で
  サムネイルが無い媒体が出たら、そこで考える
- **閾値スライダで結合候補を再計算**（§13 に書いてあるが、いまも未実装）

---

## Self-review で見つけて足したもの

計画を書き終えてから §13 と突き合わせ、**タスクが 1 つも受け持っていない要件**が
3 つ見つかった。どれも「1 画面のテスト」では捕まらないので、Task 13 の E2E に足す。

- [ ] **Task 13 に追加 — 内部の名前が画面に出ていないこと**

§13 の「画面に出す言葉」は画面をまたぐ規則なので、画面ごとのテストでは守れない。
E2E で全画面を巡り、禁じた語が本文に出ていないことを見る。

```ts
// web/e2e/journey.spec.ts に追記
const FORBIDDEN = ["ジョブ", "転送先", "承認待ち", "ボリューム", "プロファイル", "マージ"];

test("内部の名前を画面に出さない", async ({ page }) => {
  for (const path of ["/", "/photos", "/settings", "/merge", "/approve", "/send", "/card"]) {
    await page.goto(path);
    const body = await page.locator("main").innerText();
    for (const word of FORBIDDEN) {
      expect(body, `${path} に「${word}」が出ている`).not.toContain(word);
    }
  }
});
```

**「破棄」を禁止語に入れない。** 確認ダイアログの本文（`delete_merge_history`）が
「この破棄の記録」と書いており、そこは記録の説明なので正しい。禁じるのはボタンの
ラベルとしての「破棄する」だけで、それは Task 8 のテストが見ている。

- [ ] **Task 13 に追加 — 狭い画面で押せる領域が 44px 以上あること**

```ts
test("狭い画面のボタンは 44px 以上", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  for (const button of await page.locator("main button, nav a").all()) {
    const box = await button.boundingBox();
    if (box === null) continue;   // 隠れているものは対象外
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
});
```

- [ ] **Task 13 に追加 — ライトとダークの両方で本文が読めること**

```ts
for (const colorScheme of ["light", "dark"] as const) {
  test(`${colorScheme} で本文と背景が同じ色にならない`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await page.goto("/");
    const [fg, bg] = await page.evaluate(() => {
      const style = getComputedStyle(document.body);
      return [style.color, style.backgroundColor];
    });
    expect(fg).not.toBe(bg);
    expect(bg).not.toBe("rgba(0, 0, 0, 0)");   // 背景を塗り忘れると透ける
  });
}
```

**この 3 つは「無いことが仕様に見える」たぐいの抜け**なので、E2E に置く。
`docs/development.md` の「よくある 3 パターン」に当てはまる。
