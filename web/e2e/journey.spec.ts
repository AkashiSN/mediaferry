// §13 の主要動線を、**空の DB から** CLI に触れずになぞる。
//
// ホーム → カードを信頼 → 取り込む → つなぐ → 送る → 確認。
//
// **E2E でしか捕まらないものがある。** vitest は画面の一部しか見ないので、
// 「無いこと」が仕様に見える。画面をまたぐ規則（内部の名前を出さない、押せる
// 領域の大きさ、ライトとダーク、確認ダイアログが前面に重なること）は、実際に
// 並べて描いてみないと確かめられない。

import { expect, test, type Page } from "@playwright/test";

import { start, type Running } from "./harness";
import { FORBIDDEN } from "../src/test/vocabulary";

let app: Running;

const PASSWORD = "correct horse";

// 巡る画面。**ナビに出ないページも全部入れる** —— 作業ページ 5 つと設定の下位
// 5 つは、ナビからは開けないぶん、禁止語も小さすぎるボタンも見落とされやすい。
// 禁止語の一覧は `src/test/vocabulary.ts` にあり、確認ダイアログのテストと
// 共有する。
const SCREENS = [
  "/",
  "/photos",
  "/settings",
  "/card",
  "/merge",
  "/approve",
  "/send",
  "/sending",
  "/settings/destinations",
  "/settings/profiles",
  "/settings/jobs",
  "/settings/merge-history",
  "/settings/general",
];

test.beforeAll(async () => {
  app = await start(PASSWORD);
});

test.afterAll(() => {
  app?.stop();
});

/** ログインを済ませてホームを出す。**未ログインでは画面が使えない。** */
async function signIn(page: Page, path = "/"): Promise<void> {
  await page.goto(app.url + path);
  await page.getByLabel("パスワード").fill(PASSWORD);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
}

/**
 * 押せるものの網（§13「押せる領域は 44px 以上」）。
 *
 * **`<a>` を外さない。** 画面の移動はほとんどがリンクで、`main button` だけを
 * 見ると本文にあるリンクが 1 つも測られない（実測で対象の 3 分の 1 が網の外に
 * あった）。
 */
const TAPPABLE = "main button, main a, nav a";

/**
 * 画面が描き終わるのを待つ。
 *
 * **`goto` の直後は測れない。** 本文のほとんどは API の応答が返ってから生えるので、
 * そこで数えると「まだ無いもの」を見逃す。押せるものの数が 2 回続けて同じになった
 * ところを描き終わりとみなす（`nav` の 3 項目があるので 0 にはならない）。
 * SSE の接続が開いたままなので、`networkidle` は使えない。
 */
async function settled(page: Page): Promise<void> {
  await expect(page.locator("main")).toBeVisible();
  let previous = -1;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const count = await page.locator(TAPPABLE).count();
    if (count === previous) {
      return;
    }
    previous = count;
    await page.waitForTimeout(250);
  }
}

test("空の DB から、ホーム起点の主要動線が通る", async ({ page }) => {
  // **画面の例外を見逃さない。** React が落ちると画面が消えるだけで、
  // 「要素が見つからない」としか分からなくなる。
  const crashes: string[] = [];
  page.on("pageerror", (error) => crashes.push(error.message));
  const logs: string[] = [];
  page.on("console", (message) => logs.push(message.text()));
  const responses: string[] = [];
  page.on("response", async (response) => {
    if (response.url().includes("/api/")) {
      const body = await response.text().catch(() => "");
      responses.push(body);
      if (!response.ok()) {
        console.log(`API 失敗: ${response.status()} ${response.url()} ${body.slice(0, 200)}`);
      }
    }
  });

  // 1. 認証。**未ログインでは画面が使えない。**
  await page.goto(app.url);
  await expect(page.getByRole("heading", { name: "ログイン" })).toBeVisible();
  await page.getByLabel("パスワード").fill(PASSWORD);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("heading", { name: "ホーム", exact: true })).toBeVisible();

  // **ナビは 3 つだけ**（§13）。作業ページを増やしても項目は増えない。
  const nav = page.getByRole("navigation");
  await expect(nav.getByRole("link")).toHaveCount(3);
  for (const label of ["ホーム", "写真", "設定"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }

  // **やることが無いときは、無いと書く**（§13。空の表を出さない）。
  await expect(page.getByText("いま、やることはありません")).toBeVisible();

  // 2. 送り先を 2 件作る（**空の DB から画面だけで作れること**）。
  await nav.getByRole("link", { name: "設定" }).click();
  await page.getByRole("link", { name: "送り先を管理する" }).click();
  // 「名前」も「接続先 URL」も、追加の欄と既にある送り先の「接続の設定」の
  // 両方に当たるので、追加の form に限る。
  const form = page.getByRole("form", { name: "送り先を追加する" });
  for (const [index, url] of app.immich.entries()) {
    await form.getByLabel("名前").fill(`immich-${index + 1}`);
    await form.getByLabel("接続先 URL").fill(url);
    await form.getByLabel("API キー").fill("test-api-key");
    await page.getByRole("button", { name: "接続を検証して追加する" }).click();
    await expect(page.getByRole("heading", { name: `immich-${index + 1}` })).toBeVisible();
  }

  // 3. カードを信頼する。**2 枚挿してある**ので、機種名で見分ける。
  await nav.getByRole("link", { name: "ホーム" }).click();
  const unknownDji = page.locator("section.card").filter({ hasText: "DJI Osmo Pocket" });
  await expect(unknownDji.getByRole("button", { name: "このカードを信頼する" })).toBeVisible({
    timeout: 60_000,
  });
  await unknownDji.getByRole("button", { name: "このカードを信頼する" }).click();
  // **同意の対象には、いま挿してあるカードの中身が含まれる。**
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("いま入っている中身");
  await expect(dialog).toContainText("取り違え");
  await page.getByRole("button", { name: "実行する" }).click();

  // 信頼すると、帯はカードの名前で名乗る。
  const card = page.locator("section.card").filter({ hasText: "SD_Card のカードが挿さっています" });
  await expect(card).toContainText("信頼済み", { timeout: 60_000 });

  // 4. 取り込む。**数えてからコピーする**ので、この 1 手で中身がライブラリに入る。
  await card.getByRole("button", { name: "いま取り込む" }).click();
  await expect(page.getByRole("heading", { name: "やること", exact: true })).toBeVisible({
    timeout: 60_000,
  });

  // 5. つなぐ。**候補が 0 件でも入れることを、画面の導線で確かめる。**
  //    合成カードの動画は 100 バイトで `min_part_size_gib`（15 GiB）に遠く及ばず、
  //    検出は候補を 1 つも作らない。このとき「やること」につなぐは出ないので、
  //    設定 › 詳しい情報の常設の入口から入る（ここが無いと、候補を作る画面へ
  //    入る道が無くなる）。
  await nav.getByRole("link", { name: "設定" }).click();
  await page.getByRole("link", { name: /^つなぐ/ }).click();
  await expect(page.getByRole("heading", { name: "つなぐものはありません" })).toBeVisible();
  await page.getByRole("group").first().click(); // 「手でグループを作る」を開く
  const parts = page.getByRole("checkbox");
  await expect(parts.first()).toBeVisible({ timeout: 60_000 });
  await parts.nth(0).check();
  await parts.nth(1).check();
  await page.getByRole("button", { name: /選んだ 2 件でグループを作る/ }).click();
  await page.getByRole("button", { name: "ホームへ" }).click();

  // 候補ができたので、**ホームの「やること」からも**入れる（§13 の主要動線）。
  const toMerge = page.getByRole("link", { name: "つなぐ", exact: true });
  await expect(toMerge).toBeVisible({ timeout: 60_000 });
  await toMerge.click();
  await expect(page.getByText("2 つに分かれています（手動）")).toBeVisible();
  // **作業ページを開いても、現在地はホームのまま**（§13。ナビの項目を増やさない）。
  await expect(nav.getByRole("link", { name: "ホーム" })).toHaveAttribute("aria-current", "page");

  // 別々に戻す（構成ファイルは、グループがある間は送信の選択肢に出ない。§10）。
  await page.getByRole("button", { name: "これは別々" }).click();
  await page.getByRole("button", { name: "実行する" }).click();
  await expect(page.getByRole("heading", { name: "つなぐものはありません" })).toBeVisible({
    timeout: 60_000,
  });
  await page.getByRole("button", { name: "ホームへ" }).click();

  // 6. 送る。**宛先 → 対象 → 確認**の 3 段（§13）。
  const toSend = page.getByRole("link", { name: "送る", exact: true });
  await expect(toSend).toBeVisible({ timeout: 60_000 });
  await toSend.click();
  await expect(page.getByRole("heading", { name: "Immich へ送る" })).toBeVisible();
  await page.getByRole("button", { name: /immich-1/ }).click();
  await page.getByRole("button", { name: /immich-2/ }).click();
  await expect(page.getByText("送り先：immich-1 / immich-2")).toBeVisible();
  await page.getByRole("button", { name: "内容を確かめる" }).click();
  // **取り消せないので、件数・合計サイズ・宛先名を出して確認を取る**（§13）。
  await expect(dialog).toContainText("2 件");
  await expect(dialog).toContainText("immich-1 / immich-2");
  await page.getByRole("button", { name: "実行する" }).click();

  // 7. 送信中。**閉じても送信は続く。**
  await expect(page.getByRole("heading", { name: "送っています" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("この画面を閉じても送信は続きます。")).toBeVisible();
  await page.getByRole("button", { name: "閉じる" }).click();
  await expect(page.getByRole("heading", { name: "ホーム", exact: true })).toBeVisible();

  // 8. 確認。**閉じたあとも送信は進み、結果がホームに出る。**
  //    合成カードの動画は 100 バイトで送信が一瞬で終わるので、「進行中の作業が
  //    まだ出ている」ことは掴めない。掴めるのは「閉じても最後まで進むこと」で、
  //    止めていればここが「送信済み 0」のまま止まる。
  await expect(page.getByText("送信済み 2 ・ 未送信 0").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/送信済み 2 ・ 未送信 0/)).toHaveCount(2, { timeout: 60_000 });

  expect(crashes).toEqual([]);

  // 9. **秘密がどこにも出ない**（DOM・ネットワーク応答・コンソール）。
  const dom = await page.content();
  expect(dom).not.toContain("test-api-key");
  expect(dom).not.toContain(PASSWORD);
  expect(responses.join("\n")).not.toContain("test-api-key");
  expect(logs.join("\n")).not.toContain("test-api-key");
});

test("内部の名前と Markdown の記号を画面に出さない", async ({ page }) => {
  await signIn(page);
  for (const path of SCREENS) {
    await page.goto(app.url + path);
    await settled(page);
    const body = await page.locator("main").innerText();
    for (const word of FORBIDDEN) {
      expect(body, `${path} に「${word}」が出ている`).not.toContain(word);
    }
    // **JSX の中で Markdown は効かない。** `**強調**` と書くとアスタリスクが
    // そのまま描かれる。
    expect(body, `${path} に Markdown の記号が出ている`).not.toContain("**");
  }
});

test("狭い画面のボタンは 44px 以上", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  // **最初の 1 件で止めない。** 落ちるたびに 1 つずつ直すと、次に何が待って
  // いるかが分からないまま何度も回すことになる。全画面ぶんを集めてから見せる。
  const tooSmall: string[] = [];
  for (const path of SCREENS) {
    await page.goto(app.url + path);
    await settled(page);
    for (const button of await page.locator(TAPPABLE).all()) {
      // **隠れているものは対象外。** 閉じた `<details>` の中身は `boundingBox()`
      // が `null` ではなく 0×0 を返すので、可視かどうかで先に切る。
      if (!(await button.isVisible())) {
        continue;
      }
      const box = await button.boundingBox();
      if (box === null) {
        continue;
      }
      if (box.height < 44) {
        const label = (await button.innerText()).slice(0, 20);
        tooSmall.push(`${path} の「${label}」が ${box.height}px`);
      }
    }
  }
  expect(tooSmall, tooSmall.join(" / ")).toEqual([]);
});

for (const colorScheme of ["light", "dark"] as const) {
  test(`${colorScheme} で本文と背景が同じ色にならない`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await signIn(page);
    const [fg, bg] = await page.evaluate(() => {
      const style = getComputedStyle(document.body);
      return [style.color, style.backgroundColor];
    });
    expect(fg).not.toBe(bg);
    expect(bg).not.toBe("rgba(0, 0, 0, 0)"); // 背景を塗り忘れると透ける
  });
}

test("確認ダイアログは前面に重なり、背後を押させない", async ({ page }) => {
  // **これは実ブラウザでしか捕まらない。** RTL はレイアウトを見ないので、
  // scrim の CSS が落ちても「本文の末尾に生えているだけ」の状態を通してしまう。
  // 取り消せない操作の確認が画面外に出たり、背後が押せたりする。
  await signIn(page);
  const trust = page.getByRole("button", { name: "このカードを信頼する" }).first();
  await expect(trust).toBeVisible({ timeout: 60_000 });
  await trust.click();
  await expect(page.getByRole("dialog")).toBeVisible();

  const backdrop = page.locator(".dialog-backdrop");
  // 1. 位置指定が効いている。
  expect(await backdrop.evaluate((element) => getComputedStyle(element).position)).toBe("fixed");

  // 2. ビューポートを覆っている。
  const box = await backdrop.boundingBox();
  const viewport = page.viewportSize();
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(viewport!.width);
  expect(box!.height).toBeGreaterThanOrEqual(viewport!.height);

  // 3. 背後の要素が押せない（scrim が当たっている）。**ダイアログの外にある
  //    ボタンの真上に何があるか**を見る。
  const behind = await page.evaluate(() => {
    const outside = [...document.querySelectorAll("main button")].find(
      (candidate) => candidate.closest(".dialog-backdrop") === null,
    );
    if (outside === undefined) {
      return { found: false, inViewport: false, coveredByBackdrop: false };
    }
    const rect = outside.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const inViewport =
      x >= 0 && y >= 0 && x <= window.innerWidth && y <= window.innerHeight;
    const top = document.elementFromPoint(x, y);
    return {
      found: true,
      inViewport,
      coveredByBackdrop: top !== null && top.closest(".dialog-backdrop") !== null,
    };
  });
  expect(behind.found).toBe(true);
  expect(behind.inViewport).toBe(true);
  expect(behind.coveredByBackdrop).toBe(true);

  // 4. 確認の本文にも Markdown の記号を出さない。
  expect(await page.getByRole("dialog").innerText()).not.toContain("**");

  await page.getByRole("button", { name: "やめる" }).click();
});
