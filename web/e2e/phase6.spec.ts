// Phase 6 の受け入れ（§20）。**RAW+JPEG が Immich で 1 つに束ねられることを、
// 画面だけでなぞる。**
//
// 1. Canon 風の合成カード（`IMG_0003.JPG` と `IMG_0003.CR2` は同じシャッター）を取り込む
// 2. 送り先を作り、その 2 枚を送る
// 3. 送信のあと、**相手側で 1 スタックになっている**（fake Immich の状態を見る）
// 4. 束ねられない組は、**理由が送り先の画面に出る**
//
// **組の数はホームに出さない**（§13 の送り先の行は「送信済み・未送信・送信中」）。
// 束ねられたことは相手側の状態で、束ねられなかったことは送り先の画面で見る。

import { expect, test } from "@playwright/test";

import { start, type Running } from "./harness";

let app: Running;

test.beforeAll(async () => {
  app = await start(undefined, ["--timezone-from-db"]);
});

test.afterAll(() => {
  app?.stop();
});

test("RAW+JPEG が 1 スタックになり、見送りの理由も画面に出る", async ({ page, request }) => {
  const crashes: string[] = [];
  page.on("pageerror", (error) => crashes.push(error.message));

  await page.goto(app.url);
  await expect(page.getByRole("heading", { name: "ホーム", exact: true })).toBeVisible();
  const nav = page.getByRole("navigation");

  // 0. 取り込みの前に TZ を決める（`force_offset` の DJI は無いと取り込めない）。
  await nav.getByRole("link", { name: "設定" }).click();
  await page.getByRole("link", { name: "詳しい設定" }).click();
  await page.getByLabel("DEFAULT_TIMEZONE の値").fill("Asia/Tokyo");
  await page.getByLabel("DEFAULT_TIMEZONE の値").blur();

  // 1. 送り先を 1 件作る。
  await nav.getByRole("link", { name: "設定" }).click();
  await page.getByRole("link", { name: "送り先を管理する" }).click();
  const form = page.locator("form");
  await form.getByLabel("名前").fill("immich-1");
  await form.getByLabel("接続先 URL").fill(app.immich[0]);
  await form.getByLabel("API キー").fill("test-api-key");
  await page.getByRole("button", { name: "接続を検証して追加する" }).click();
  await expect(page.getByRole("heading", { name: "immich-1" })).toBeVisible();
  // **この時点では見送りが 1 件も無い**（「無いこと」も画面に出る）。
  await expect(page.getByText("見送りはありません。")).toBeVisible();

  // 2. Canon のカードを取り込む。
  await page.goto(app.url + "/card");
  await page.getByRole("button", { name: "EOS_DIGITAL をスキャン" }).click();
  await page.getByRole("button", { name: "EOS_DIGITAL を取り込む" }).click();

  // 3. 写真に RAW+JPEG が並ぶまで待つ。**タイルはファイル名で名乗る**（§13）。
  await nav.getByRole("link", { name: "写真" }).click();
  const pair = page.getByRole("button", { name: /^IMG_0003\.(JPG|CR2)$/ });
  await expect(pair).toHaveCount(2, { timeout: 90_000 });

  // 4. 2 枚と、**組にならない 1 枚**（相方の無い JPG）を選んで送る。
  await pair.nth(0).click();
  await pair.nth(1).click();
  await page.getByRole("button", { name: "IMG_0001.JPG" }).click();
  await page.getByRole("button", { name: "送る", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Immich へ送る" })).toBeVisible();
  // 送り先が 1 つしか無ければ、黙ってそれを使う。
  await expect(page.getByText("送り先：immich-1")).toBeVisible();
  await page.getByRole("button", { name: "内容を確かめる" }).click();
  await expect(page.getByRole("dialog")).toContainText("3 件");
  await page.getByRole("button", { name: "実行する" }).click();
  await expect(page.getByRole("heading", { name: "送っています" })).toBeVisible({ timeout: 60_000 });

  // 5. **相手側で 1 スタックになる**（第 2 パスはアップロードの後に回る）。
  //    **どちらが先に送られるかは決まらない**ので、主資産の候補を両方引く。
  const membersOfTheStack = async (): Promise<number> => {
    for (const primary of ["asset-1", "asset-2"]) {
      const response = await request.get(`${app.immich[0]}/api/stacks?primaryAssetId=${primary}`, {
        headers: { "x-api-key": "test-api-key" },
      });
      const stacks = (await response.json()) as { assets: { id: string }[] }[];
      if (stacks.length > 0) {
        return stacks[0].assets.length;
      }
    }
    return 0;
  };
  await expect.poll(membersOfTheStack, { timeout: 60_000 }).toBe(2);

  // 6. **束ねられなかった 1 枚は、理由が送り先の画面に出る。**
  //    「対象外のときだけ出す」形にしないので、文言まで当てる（Phase 5 の教訓）。
  await page.goto(app.url + "/settings/destinations");
  await expect(page.getByText(/相方が見つからない/)).toBeVisible({ timeout: 60_000 });

  expect(crashes).toEqual([]);
});
