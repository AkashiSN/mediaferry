// §20 の完了条件を、**空の DB から** CLI に触れずになぞる。
//
// 認証を有効にしてログイン → 転送先を 2 件作る → デバイスを信頼 → スキャン →
// 取り込み → 2 宛先へ送信 → ジョブ履歴で確認。

import { expect, test } from "@playwright/test";

import { start, type Running } from "./harness";

let app: Running;

test.beforeAll(async () => {
  app = await start("correct horse");
});

test.afterAll(() => {
  app?.stop();
});

test("空の DB から一連の操作が通る", async ({ page }) => {
  // **画面の例外を見逃さない。** React が落ちると画面が消えるだけで、
  // 「要素が見つからない」としか分からなくなる。
  const crashes: string[] = [];
  page.on("pageerror", (error) => crashes.push(error.message));
  const secrets: string[] = [];
  page.on("console", (message) => secrets.push(message.text()));
  const responses: string[] = [];
  page.on("response", async (response) => {
    if (response.url().includes("/api/")) {
      const body = await response.text().catch(() => "");
      responses.push(body);
      console.log(`API: ${response.status()} ${response.request().method()} ${response.url()}`);
      if (!response.ok()) {
        console.log(`API 失敗: ${response.status()} ${response.url()} ${body.slice(0, 200)}`);
      }
    }
  });

  // 1. 認証。**未ログインでは画面が使えない。**
  await page.goto(app.url);
  await expect(page.getByRole("heading", { name: "ログイン" })).toBeVisible();
  await page.getByLabel("パスワード").fill("correct horse");
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("heading", { name: "ダッシュボード" })).toBeVisible();

  // 2. 転送先を 2 件作る（**空の DB から画面だけで作れること**）。
  await page.getByRole("navigation").getByRole("link", { name: "転送先" }).click();
  for (const [index, url] of app.immich.entries()) {
    await page.getByLabel("名前").fill(`immich-${index + 1}`);
    await page.getByLabel("接続先 URL").fill(url);
    await page.getByLabel("API キー").fill("test-api-key");
    await page.getByRole("button", { name: "接続を検証して追加する" }).click();
    await expect(page.getByText(`immich-${index + 1}`)).toBeVisible();
  }

  // 3. デバイス（信頼 → スキャン → 取り込み）。
  await page.getByRole("navigation").getByRole("link", { name: "デバイス" }).click();
  const trust = page.getByRole("button", { name: "このカードを信頼する" });
  if (await trust.count()) {
    await trust.first().click();
  }
  await page.getByRole("button", { name: "スキャン" }).first().click();
  await page.getByRole("button", { name: "取り込む" }).first().click();

  // 4. ライブラリに出るまで待つ（取り込みのジョブが終わるまで）。
  const library = page.getByRole("navigation").getByRole("link", { name: "ライブラリ" });
  await library.click();
  // **画面を再読み込みせずに出る**（SSE で進捗が届いたら一覧を取り直す）。
  const media = page.getByRole("checkbox", { name: /を選ぶ$/ });
  await expect(media.first()).toBeVisible({ timeout: 60_000 });

  // 5. 2 宛先へ送信（確認ダイアログを通る）。
  await media.first().check();
  await page.getByRole("checkbox", { name: "immich-1" }).check();
  await page.getByRole("checkbox", { name: "immich-2" }).check();
  await page.getByRole("button", { name: /送信する/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("宛先:")).toContainText("immich-1");
  await page.getByRole("button", { name: "実行する" }).click();
  await expect(page.getByRole("status")).toContainText("送信を始めました");

  // 6. 結合の画面が使える（候補の検出と、手で組む導線）。
  await page.getByRole("navigation").getByRole("link", { name: "結合" }).click();
  await page.getByRole("button", { name: "候補を検出する" }).click();
  await expect(page.getByRole("heading", { name: "結合" })).toBeVisible();
  await page.getByRole("group").first().click(); // 「手でグループを作る」を開く
  await expect(page.getByText("検出が拾えなかった並びを")).toBeVisible();

  // 7. 承認待ちの画面が開く（この筋書きでは 0 件）。
  await page.getByRole("navigation").getByRole("link", { name: "承認待ち" }).click();
  await expect(page.getByRole("heading", { name: "承認待ち" })).toBeVisible();

  // 8. ジョブの履歴に出る。
  await page.getByRole("navigation").getByRole("link", { name: "ジョブ" }).click();
  await expect(page.getByText("upload").first()).toBeVisible({ timeout: 30_000 });

  expect(crashes).toEqual([]);

  // 9. **秘密がどこにも出ない**（DOM・ネットワーク応答・コンソール）。
  const dom = await page.content();
  expect(dom).not.toContain("test-api-key");
  expect(dom).not.toContain("correct horse");
  expect(responses.join("\n")).not.toContain("test-api-key");
  expect(secrets.join("\n")).not.toContain("test-api-key");
});
