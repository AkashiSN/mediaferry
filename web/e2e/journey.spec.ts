// §13 の主要動線を、**空の DB から** CLI に触れずになぞる。
//
// ホーム → 取り込む → カードを信頼 → つなぐ → 送る → 確認。
//
// **E2E でしか捕まらないものがある。** vitest は画面の一部しか見ないので、
// 「無いこと」が仕様に見える。画面をまたぐ規則（内部の名前を出さない、押せる
// 領域の大きさ、ライトとダーク、確認ダイアログが前面に重なること）は、実際に
// 並べて描いてみないと確かめられない。

import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

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

/** `library/` の下にある動画ファイル（拡張子 `.MP4`）を再帰的に探す。 */
function findVideoFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...findVideoFiles(full));
    } else if (entry.name.toUpperCase().endsWith(".MP4")) {
      found.push(full);
    }
  }
  return found;
}

/**
 * 手でグループを作り、実際につなぐ（§6「結合物が要る」）。
 *
 * **検出は合成カードでは候補を作れない。** `min_part_size_gib`（15）に対して
 * 合成カードの動画は 100 バイトなので、「分かれた動画を探す」を押しても候補は
 * 0 件のまま。「手でグループを作る」経路で組む。
 *
 * **合成カードの動画は ffmpeg では読めない。** 100 バイトの中身は本物のコンテナ
 * ではないので、そのまま「つなぐ」を押しても結合が失敗して終わる（`role = derived`
 * が 1 件も生まれない）。グループを作ったあと、NAS 上の該当ファイルを ffmpeg で
 * 作った小さな本物の動画に差し替えてから「つなぐ」を押す —— これは NAS 上のファイル
 * を直接書き換えるだけで、mediaferry 側には何も足さない。
 */
async function mergeTwoParts(page: Page): Promise<void> {
  // 先行するテストが同じ 2 件を組んで「これは別々」で破棄していると、
  // `input_digest` の番人がまだその構成を塞いでいる（破棄しても枠は空かない）。
  // 設定 › つないだ後の後片付けで破棄の記録を先に消し、枠を空ける。
  await page.goto(app.url + "/settings/merge-history");
  await settled(page);
  for (const discard of await page.getByRole("button", { name: /^消す：/ }).all()) {
    await discard.click();
    await page.getByRole("button", { name: "実行する" }).click();
  }

  await page.goto(app.url + "/merge");
  await settled(page);
  await page.getByRole("group").first().click(); // 「手でグループを作る」を開く
  const parts = page.getByRole("checkbox");
  await expect(parts.first()).toBeVisible({ timeout: 60_000 });
  await parts.nth(0).check();
  await parts.nth(1).check();
  await page.getByRole("button", { name: /選んだ 2 件でグループを作る/ }).click();
  const mergeButton = page.getByRole("button", { name: "つなぐ", exact: true });
  await expect(mergeButton).toBeVisible({ timeout: 60_000 });

  // NAS 上のダミー動画を、ffmpeg で読める小さな本物の動画に差し替える。
  const clip = join(app.dataRoot, ".e2e-clip.mp4");
  execFileSync("ffmpeg", [
    "-y",
    "-f",
    "lavfi",
    "-i",
    "color=c=black:size=32x32:d=0.2",
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    clip,
  ]);
  const bytes = readFileSync(clip);
  for (const path of findVideoFiles(join(app.dataRoot, "library"))) {
    writeFileSync(path, bytes);
  }

  await mergeButton.click();
  // **つないだ結果は、つなぐ画面には出ない**（Phase 11）。この画面が出すのは
  // 「まだつないでいないもの」だけなので、済んだことは**組がここから消えること**と、
  // **写真 › つないだ動画に出ること**で確かめる。
  await expect(page.getByRole("heading", { name: "つなぐものはありません" })).toBeVisible({
    timeout: 60_000,
  });
  await page.goto(app.url + "/photos?role=derived");
  await settled(page);
  await expect(page.locator("main .tile").first()).toBeVisible({ timeout: 60_000 });
}

/**
 * 押せるものの網（§13「押せる領域は 44px 以上」）。
 *
 * **`<a>` を外さない。** 画面の移動はほとんどがリンクなので、`main button` だけを
 * 見ると本文にあるリンクが 1 つも測られない。
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

  const dialog = page.getByRole("dialog");

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

  // 3. カードは「状態」ではなく「仕事」として出る（§13）。**2 枚挿してある**ので、
  //    機種名で見分ける。
  //
  //    **誰もスキャンを押していないのに件数が出る。** 挿した接続に watcher が
  //    `scan` を積むからで（§12.1）、積まなければここは永久に「0 件」に見える。
  await nav.getByRole("link", { name: "ホーム" }).click();
  const dji = page.locator("article.card").filter({ hasText: "DJI Osmo Pocket" });
  await expect(dji.getByRole("heading", { name: "SD_Card から 2 件を取り込む" })).toBeVisible({
    timeout: 60_000,
  });

  // **カードが挿さっている場面で「いま、やることはありません」は出ない**（§13）。
  // 取り込む残りがあるカードは「やること」の札になるので、3 つの並びが同時に空に
  // なることは構造上あり得ない。**この錠は E2E でしか掛からない** —— カードの札と
  // 空表示は別の場所で描かれるので、片方だけを見ていると「同時に出ないこと」が
  // 仕様に見える。
  await expect(page.getByText("いま、やることはありません")).toHaveCount(0);

  // **抜いていいかは、押さずに読める**（§13）。「取り外す」は画面に置かない。
  await expect(dji).toContainText("いま抜いて大丈夫です");
  await expect(page.getByRole("button", { name: /取り外す/ })).toHaveCount(0);

  // 4. 取り込む。**数えてからコピーする**ので、この 1 手で中身がライブラリに入る。
  //    取り込む残りが無くなると、札は「やること」から「いまの様子」へ移る。
  await dji.getByRole("button", { name: "いま取り込む" }).click();
  // **`settled()` と混ぜない**ので別の名前にする（同名の待ちが上にある）。
  const doneCard = page
    .locator("article.card")
    .filter({ hasText: "SD_Card は初めて見るカードです" });
  await expect(doneCard).toContainText("取り込むものはありません。", { timeout: 60_000 });

  // 5. 信頼する（§12.1 の同意）。**取り込み終わったカードは「やること」に居ない**
  //    ので、承認は「カードの中身」から取る。**この順でしか確かめられない** ——
  //    信頼してから取り込むと、承認した瞬間に自動取り込みが始まりうるので、
  //    「いま取り込む」を押せるかどうかが watcher の周期との競争になる。
  await doneCard.getByRole("link", { name: "中身を見る" }).click();
  await expect(page.getByRole("heading", { name: "カードの中身" })).toBeVisible();
  const detail = page.locator("section.card").filter({ hasText: "SD_Card をスキャン" });
  // **ここにも「取り外す」は無い**（§13）。抜いていいかは常に文で出る。
  await expect(detail).toContainText("いま抜いて大丈夫です");
  await expect(page.getByRole("button", { name: /取り外す/ })).toHaveCount(0);
  await detail.getByRole("button", { name: "SD_Card を信頼する" }).click();
  // **同意の対象には、いま挿してあるカードの中身が含まれる。**
  await expect(dialog).toContainText("いま入っている中身");
  await expect(dialog).toContainText("取り違え");
  await page.getByRole("button", { name: "実行する" }).click();
  await expect(detail).toContainText("信頼済み", { timeout: 60_000 });
  await page.getByRole("button", { name: "ホームへ" }).click();

  // 6. つなぐ。**候補が 0 件でも入れることを、画面の導線で確かめる。**
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

  // 7. 送る。**宛先 → 対象 → 確認**の 3 段（§13）。
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

  // 8. 送信中。**閉じても送信は続く。**
  await expect(page.getByRole("heading", { name: "送っています" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("この画面を閉じても送信は続きます。")).toBeVisible();
  await page.getByRole("button", { name: "閉じる" }).click();
  await expect(page.getByRole("heading", { name: "ホーム", exact: true })).toBeVisible();

  // 9. 確認。**閉じたあとも送信は進み、結果がホームに出る。**
  //    合成カードの動画は 100 バイトで送信が一瞬で終わるので、「進行中の作業が
  //    まだ出ている」ことは掴めない。掴めるのは「閉じても最後まで進むこと」で、
  //    止めていればここが「送信済み 0」のまま止まる。
  await expect(page.getByText("送信済み 2 ・ 未送信 0").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/送信済み 2 ・ 未送信 0/)).toHaveCount(2, { timeout: 60_000 });

  expect(crashes).toEqual([]);

  // 10. **秘密がどこにも出ない**（DOM・ネットワーク応答・コンソール）。
  const dom = await page.content();
  expect(dom).not.toContain("test-api-key");
  expect(dom).not.toContain(PASSWORD);
  expect(responses.join("\n")).not.toContain("test-api-key");
  expect(logs.join("\n")).not.toContain("test-api-key");
});

/**
 * 下に貼り付く帯が、ナビや他の固定要素に隠れていないかを見る。
 *
 * **矩形の重なりでしか捕まらない。** 44px の検査は「小さすぎないか」しか見ず、
 * はみ出しの検査は要素 1 つの中しか見ないので、**別の固定要素が上に乗って
 * 押せなくなっている**状態はどちらの網にも掛からない。帯とナビの矩形が重なって
 * いないかを見て、そのうえで帯の中の押せるものの中心に何がいちばん手前にあるかを
 * 見る（ナビ以外の固定要素も、これで捕まる）。
 *
 * **角では測らない。** 帯には 14px の丸みがあり、角の内側 4px は帯の外なので、
 * そこを突くと背後の要素が返って嘘の重なりになる。
 */
async function covering(page: Page, selector: string): Promise<string[]> {
  return page.evaluate((target) => {
    const bar = document.querySelector(target);
    const nav = document.querySelector("nav");
    if (bar === null || nav === null) {
      return [`${target} かナビが無い`];
    }
    const rect = bar.getBoundingClientRect();
    const navRect = nav.getBoundingClientRect();
    const problems: string[] = [];
    const box = (r: DOMRect) =>
      `top ${Math.round(r.top)} / bottom ${Math.round(r.bottom)}`;
    if (rect.top < 0 || rect.bottom > window.innerHeight) {
      problems.push(`${target} が画面の外にある（${box(rect)}）`);
    }
    // 辺が接するだけは重なりではない（`<` で見る）。
    const overlaps =
      rect.left < navRect.right &&
      navRect.left < rect.right &&
      rect.top < navRect.bottom &&
      navRect.top < rect.bottom;
    if (overlaps) {
      problems.push(`${target}（${box(rect)}）がナビ（${box(navRect)}）と重なっている`);
    }
    for (const control of bar.querySelectorAll("button, a")) {
      const spot = control.getBoundingClientRect();
      const x = spot.left + spot.width / 2;
      const y = spot.top + spot.height / 2;
      const front = document.elementFromPoint(x, y);
      const label = (control.textContent ?? "").trim().slice(0, 12);
      if (front === null) {
        problems.push(`${target} の「${label}」が画面の外`);
      } else if (!bar.contains(front)) {
        problems.push(
          `${target} の「${label}」が <${front.tagName.toLowerCase()} ` +
            `class="${front.className}"> に覆われている`,
        );
      }
    }
    return problems;
  }, selector);
}

// **狭い画面のナビは画面の上に 1 行で置く**（裁定は `docs/decisions.md`）。
//
// 高さの上限を 72px にする根拠: 中身は 44px の押せる領域が 1 行だけで、上下 6px の
// 余白と 1px の境界を足して 57px（ホームに件数のバッジが出ると 58px）。1 行に
// 収まらなくなると 44px の行が 2 つと余白で 100px を超える。72px はその間に取って
// あり、書体の差で数 px 動いても 1 行のうちは通る。
test("狭い画面のナビは画面の上にあり、1 行に収まる", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  const nav = await page.evaluate(() => {
    const element = document.querySelector("nav")!;
    const rect = element.getBoundingClientRect();
    const main = document.querySelector("main")!.getBoundingClientRect();
    const links = [...element.querySelectorAll("a")].map((link) => {
      const box = link.getBoundingClientRect();
      return { top: Math.round(box.top), height: Math.round(box.height) };
    });
    return {
      position: getComputedStyle(element).position,
      top: Math.round(rect.top),
      bottom: Math.round(rect.bottom),
      height: Math.round(rect.height),
      mainTop: Math.round(main.top),
      links,
    };
  });

  // 1. 画面の上にあり、本文より前に場所を取る。
  expect(nav.top).toBe(0);
  expect(nav.bottom).toBeLessThanOrEqual(nav.mainTop);
  // **流れの中に置いたまま貼り付く**ので、本文の側にナビの高さぶんの余白が要らない。
  expect(nav.position).toBe("sticky");

  // 2. 1 行に収まっている（上端が 3 つとも同じで、帯の高さが 1 行ぶん）。
  expect(nav.links).toHaveLength(3);
  expect(new Set(nav.links.map((link) => link.top)).size).toBe(1);
  expect(nav.height, `ナビの高さが ${nav.height}px`).toBeLessThanOrEqual(72);
  // 3. 押せる領域は 44px 以上のまま（§13）。
  for (const link of nav.links) {
    expect(link.height).toBeGreaterThanOrEqual(44);
  }

  // 4. 巻いても上に残る。**画面が短くて巻けないときは 0 のままなので、この行は
  //    何も主張しない**（巻ける画面でだけ `top` のずれを捕まえる）。
  const scrolled = await page.evaluate(() => {
    window.scrollTo(0, 400);
    return Math.round(document.querySelector("nav")!.getBoundingClientRect().top);
  });
  expect(scrolled).toBe(0);
});

test("狭い画面で、下に貼り付く操作バーが隠れない", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await page.goto(app.url + "/photos");
  await settled(page);

  // **実際に 1 枚選んでバーを出してから測る。** 選んでいない間はバーが無い。
  // タイルは押すと開くので、**選ぶのは隅の丸**を押す。
  const pick = page.locator("main button.pick").first();
  await expect(pick).toBeVisible({ timeout: 60_000 });
  await pick.click();
  await expect(page.locator(".actionbar")).toBeVisible();
  const overActionbar = await covering(page, ".actionbar");
  expect(overActionbar, overActionbar.join(" / ")).toEqual([]);

  await page.goto(app.url + "/send");
  await settled(page);
  await expect(page.locator(".sendbar")).toBeVisible();
  const overSendbar = await covering(page, ".sendbar");
  expect(overSendbar, overSendbar.join(" / ")).toEqual([]);
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

/**
 * **押せるものが押せると見えるか。** 色は計算後の値でしか分からないので、実ブラウザ
 * でしか捕まらない —— RTL はクラス名が付いていることしか見ず、44px の検査は大きさ
 * しか測らない。**枠も地も透明なボタンは、本文と同じ「灰色の文字」に見える。**
 *
 * 手がかりを 2 つのどちらかに求める: **地が背後と違う色で塗ってある**か、
 * **幅のある枠が透明でない**か。どちらも無いものを返す。
 *
 * 背後の色は、`background-color` が透明でない最初の祖先から取る（透明な入れ物を
 * いくつ挟んでも、実際に見えている地はその色）。
 */
async function flatControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const clear = (color: string) => color === "transparent" || /,\s*0\)$/.test(color);
    const ground = (element: Element): string => {
      for (let node = element.parentElement; node !== null; node = node.parentElement) {
        const color = getComputedStyle(node).backgroundColor;
        if (!clear(color)) {
          return color;
        }
      }
      return "";
    };
    const found: string[] = [];
    for (const control of document.querySelectorAll<HTMLElement>("main button")) {
      const rect = control.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) {
        continue;
      }
      const style = getComputedStyle(control);
      const filled = !clear(style.backgroundColor) && style.backgroundColor !== ground(control);
      const framed =
        parseFloat(style.borderTopWidth) > 0 && !clear(style.borderTopColor);
      if (!filled && !framed) {
        const label = (control.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 24);
        found.push(
          `「${label}」は 地 ${style.backgroundColor} / 枠 ${style.borderTopWidth} ` +
            `${style.borderTopColor} で、背後（${ground(control)}）と見分けが付かない`,
        );
      }
    }
    return found;
  });
}

// **`.quiet` は 7 画面が使う**ので、カメラの種類だけを見ても足りない。全画面を巡り、
// 押せるものが本文の灰色の文字に埋もれていないかを見る。ライトとダークの両方で
// 測るのは、手がかりをトークンから取っているため（片方だけ潰れる直し方がある）。
for (const colorScheme of ["light", "dark"] as const) {
  test(`${colorScheme} で、押せるものが押せると分かる`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await signIn(page);
    // 44px の検査と同じく、**最初の 1 件で止めない**。全画面ぶんを集めて見せる。
    const flat: string[] = [];
    let profileButtons = 0;
    for (const path of SCREENS) {
      await page.goto(app.url + path);
      await settled(page);
      if (path === "/settings/profiles") {
        profileButtons = await page.locator("main button").count();
      }
      // **下に貼り付く帯は、1 枚選ぶまで描かれない。** 帯の地はトークンの外
      // （暗い帯）なので、選ばずに通り過ぎると帯の上の弱い操作が一度も測られない。
      // タイルは押すと開くので、**選ぶのは隅の丸**を押す。
      if (path === "/photos") {
        await page.locator("main button.pick").first().click({ timeout: 60_000 });
        await expect(page.locator(".actionbar")).toBeVisible();
      }
      for (const problem of await flatControls(page)) {
        flat.push(`${path} の ${problem}`);
      }
    }
    // **空振りで緑にしない。** カメラの種類にはビルトインが 3 つあり、1 つにつき
    // 「複製して変える」「撮影日時を再計算する」と、挿してあるカードのぶんの
    // 「試す」が並ぶ。ここが 0 のまま通ると、何も測らずに緑になる。
    expect(profileButtons, "カメラの種類の画面に操作が無い").toBeGreaterThanOrEqual(6);
    expect(flat, flat.join("\n")).toEqual([]);
  });
}

/**
 * 文字が入れ物からはみ出している要素を集める。
 *
 * **これは実ブラウザでしか捕まらない。** RTL はレイアウトを見ないので、見出しに
 * 12px 四方の丸のクラスが当たっても「要素があり、文字も入っている」で緑になる。
 * 44px の検査はボタンとリンクしか測らず、禁止語の検査は `innerText` を読むだけ
 * なので、**文字が箱の外へ流れ出る壊れ方はどの網にも掛からない。**
 *
 * 見るのは末端の文字要素だけ。自分で横スクロールする箱（省略記号を出す欄など）は
 * はみ出して当たり前なので外す。
 */
async function spilling(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    for (const element of document.querySelectorAll<HTMLElement>("main *, nav *")) {
      const text = (element.innerText ?? "").trim();
      if (text === "" || element.children.length > 0) {
        continue;
      }
      if (getComputedStyle(element).overflowX !== "visible") {
        continue;
      }
      // 1px は端数の丸め。それを超えたら文字が箱に収まっていない。
      if (element.scrollWidth > element.clientWidth + 1) {
        const label = text.replace(/\s+/g, " ").slice(0, 24);
        found.push(
          `<${element.tagName.toLowerCase()} class="${element.className}">「${label}」` +
            `が 幅 ${element.clientWidth}px の箱に ${element.scrollWidth}px 必要`,
        );
      }
    }
    return found;
  });
}

// **狭いところと、柱が出たばかりのところの両方で測る。** 390px は帯のとき、
// 900px は左の柱が出て本文の幅がいちばん狭くなるとき、1280px はふだんの幅。
for (const width of [390, 900, 1280]) {
  test(`${width}px で文字が入れ物からはみ出さない`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await signIn(page);
    // 44px の検査と同じく、**最初の 1 件で止めない**。全画面ぶんを集めて見せる。
    const spills: string[] = [];
    for (const path of SCREENS) {
      await page.goto(app.url + path);
      await settled(page);
      for (const spill of await spilling(page)) {
        spills.push(`${path} の ${spill}`);
      }
    }
    expect(spills, spills.join("\n")).toEqual([]);
  });
}

/**
 * **箱の中身のはずのものが、箱の外に出ていないか。**
 *
 * 上の「はみ出し」の検査は、**子が親の箱に収まっているか**しか見ない。カードの
 * 外へ**兄弟として**置かれた行は親が違うので対象外で、負の余白でカードの下の縁へ
 * 引き上げても素通りする。44px の検査は大きさしか測らず、禁止語の検査は
 * `innerText` を読むだけなので、**カードに属する行がカードの縁を跨いで描かれる
 * 壊れ方**はどの網にも掛からない（裁定は `docs/decisions.md`）。
 *
 * 流れの中に並ぶ兄弟は、ふつう重ならない。重なるのは負の余白で引き寄せたときか、
 * 位置指定で持ち上げたときだけ。そこで、**カードの矩形と、同じ親を持つ他の要素の
 * 矩形が重なっていないこと**を見る。
 *
 * **貼り付く要素は外す。** ナビ・下の帯・確認の scrim は重なるのが仕事なので、
 * 流れの中にあるもの（`static` と `relative`）だけを測る。
 */
async function crossingCardEdges(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const problems: string[] = [];
    const inFlow = (element: Element) => {
      const position = getComputedStyle(element).position;
      return position === "static" || position === "relative";
    };
    let cards = 0;
    for (const card of document.querySelectorAll("main section.card")) {
      const rect = card.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0 || card.parentElement === null) {
        continue;
      }
      cards += 1;
      for (const sibling of card.parentElement.children) {
        if (sibling === card || !inFlow(sibling)) {
          continue;
        }
        const other = sibling.getBoundingClientRect();
        if (other.width === 0 || other.height === 0) {
          continue;
        }
        // 辺が接するだけは重なりではない（`<` で見る）。
        const overlaps =
          rect.left < other.right &&
          other.left < rect.right &&
          rect.top < other.bottom &&
          other.top < rect.bottom;
        if (overlaps) {
          const label = (sibling.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 24);
          problems.push(
            `<${sibling.tagName.toLowerCase()} class="${sibling.className}">「${label}」` +
              `（top ${Math.round(other.top)}）がカード` +
              `（top ${Math.round(rect.top)} / bottom ${Math.round(rect.bottom)}）と重なっている`,
          );
        }
      }
    }
    // 測るカードが 1 枚も無ければ、この画面では何も主張していない。
    return cards === 0 ? [] : problems;
  });
}

// **狭い画面で測る。** 390px はカードの中身が 1 行に収まらなくなる幅で、縁を跨ぐ
// 行がいちばん目立つ。
test("カードの中身が、カードの箱の外に出ていない", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  // 44px の検査と同じく、**最初の 1 件で止めない**。全画面ぶんを集めてから見せる。
  const crossing: string[] = [];
  let historyCards = 0;
  for (const path of SCREENS) {
    await page.goto(app.url + path);
    await settled(page);
    if (path === "/settings/jobs") {
      historyCards = await page.locator("main section.card").count();
    }
    for (const problem of await crossingCardEdges(page)) {
      crossing.push(`${path} の ${problem}`);
    }
  }
  // **空振りで緑にしない。** 終わった作業に補足（終わった日時・最後の文言）を
  // 添えるのは作業の履歴なので、そこにカードが 1 枚も無いまま通ると何も測らずに
  // 緑になる（このファイルの先頭の動線が取り込みと送信を走らせている）。
  expect(historyCards, "作業の履歴にカードが無い").toBeGreaterThan(0);
  expect(crossing, crossing.join("\n")).toEqual([]);
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

test("つないだ動画を開いて、消せる", async ({ page }) => {
  await signIn(page);
  // 手でグループを作ってつなぐ（検出は合成カードでは候補を作れない）。
  await mergeTwoParts(page);

  await page.goto(app.url + "/photos?role=derived");
  await settled(page);
  const tile = page.locator("main .tile").first();
  await expect(tile).toBeVisible({ timeout: 60_000 });
  // **見出しの照合をファイル名で絞る。** 「くわしく」も写真タブも `<h1>` を
  // 1 つ持つので、階層（level）だけで見ると、遷移がまだ終わっていない
  // （住所は変わったが描画が追いついていない）瞬間の写真タブの見出し
  // 「写真」を誤って合格にしてしまう。
  const fileLabel = await tile.locator(".tilehit").getAttribute("aria-label");
  expect(fileLabel).not.toBeNull();
  await tile.locator(".tilehit").click();

  // くわしくが開き、**何から作られたかが出る**。
  await expect(page.getByRole("heading", { level: 1, name: fileLabel! })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/つないだ動画/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "元になったファイル" })).toBeVisible({
    timeout: 60_000,
  });
  // **つないだ結果への操作は、この画面にある**（Phase 11）。つなぐ画面から
  // 移したので、ここに無いと検証に落ちた動画を送る手段が消える。
  await expect(page.getByRole("heading", { name: "つないだ結果" })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByRole("button", { name: "これは別々" })).toBeVisible();

  // 一度も送っていないので消せる。確認ダイアログを経て消す。
  await page.getByRole("button", { name: "消す" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "実行する" }).click();

  // 消したら写真タブへ戻る。**「元になった 2 件」は既にほかのテストで送信済み**
  // なので、絞り込み無しの一覧には残る —— 消えたことを見るのは、いま居た
  // 「つないだ動画」の一覧（`role=derived`）に対してでなければならない。
  await expect(page).toHaveURL(/\/photos$/);
  await page.goto(app.url + "/photos?role=derived");
  await settled(page);
  await expect(page.locator("main .tile")).toHaveCount(0);
});

// ---- ログイン画面 ----
//
// **上のどの網にも掛かっていない。** 44px もコントラストもはみ出しも、`signIn()`
// を済ませてから `main` の中だけを測る。ログインは枠（`Layout`）の外に出る唯一の
// 画面で `main` も `nav` も持たないので、ここで別に見る。

test("ログイン画面の押せるものも 44px 以上", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(app.url);
  await expect(page.getByRole("heading", { name: "ログイン" })).toBeVisible();

  const tooSmall: string[] = [];
  const targets: [string, ReturnType<Page["getByLabel"]>][] = [
    ["パスワード", page.getByLabel("パスワード")],
    ["ログイン", page.getByRole("button", { name: "ログイン" })],
  ];
  for (const [name, locator] of targets) {
    const box = await locator.boundingBox();
    if (box !== null && box.height < 44) {
      tooSmall.push(`「${name}」が ${Math.round(box.height)}px`);
    }
  }
  expect(tooSmall, tooSmall.join(" / ")).toEqual([]);
});

test("ログイン画面は 1 枚のカードを中央に置く", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(app.url);
  await expect(page.getByRole("heading", { name: "ログイン" })).toBeVisible();

  const panel = await page.evaluate(() => {
    const card = document.querySelector("form")?.closest(".card") ?? null;
    if (card === null) {
      return null;
    }
    const rect = card.getBoundingClientRect();
    return {
      left: Math.round(rect.left),
      right: Math.round(window.innerWidth - rect.right),
      width: Math.round(rect.width),
      viewport: window.innerWidth,
    };
  });

  expect(panel, "ログインのフォームがカードに入っていない").not.toBeNull();
  // **本文の幅いっぱいに広げない。** 読む列としても押す的としても広すぎる。
  expect(panel!.width, `カードが ${panel!.width}px`).toBeLessThanOrEqual(panel!.viewport / 2);
  // 左右の余りが等しい ＝ 中央にある。
  expect(Math.abs(panel!.left - panel!.right), "中央に無い").toBeLessThanOrEqual(2);
});
