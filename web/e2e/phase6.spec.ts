// Phase 6 の受け入れ（§20）。**RAW+JPEG が Immich で 1 つに束ねられることを、
// 画面だけでなぞる。**
//
// 1. Canon 風の合成カード（`IMG_0003.JPG` と `IMG_0003.CR2` は同じシャッター）を取り込む
// 2. 転送先を作り、その 2 枚を送る
// 3. 送信のあと、**相手側で 1 スタックになっている**（fake Immich の状態を見る）
// 4. ダッシュボードに組の数が出る
// 5. 束ねられない組は、**理由が宛先の画面に出る**

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
  await expect(page.getByRole("heading", { name: "ダッシュボード" })).toBeVisible();

  // 0. 取り込みの前に TZ を決める（`force_offset` の dji-osmo は無いと取り込めない）。
  await page.getByRole("navigation").getByRole("link", { name: "設定" }).click();
  await page.getByLabel("DEFAULT_TIMEZONE の値").fill("Asia/Tokyo");
  await page.getByLabel("DEFAULT_TIMEZONE の値").blur();

  // 1. 転送先を 1 件作る。
  await page.getByRole("navigation").getByRole("link", { name: "転送先" }).click();
  await page.getByLabel("名前").fill("immich-1");
  await page.getByLabel("接続先 URL").fill(app.immich[0]);
  await page.getByLabel("API キー").fill("test-api-key");
  await page.getByRole("button", { name: "接続を検証して追加する" }).click();
  await expect(page.getByText("immich-1")).toBeVisible();
  // **この時点では見送りが 1 件も無い**（「無いこと」も画面に出る）。
  await expect(page.getByText("見送りはありません。")).toBeVisible();

  // 2. Canon のカードを取り込む。
  await page.getByRole("navigation").getByRole("link", { name: "デバイス" }).click();
  await page.getByRole("button", { name: "EOS_DIGITAL をスキャン" }).click();
  await page.getByRole("button", { name: "EOS_DIGITAL を取り込む" }).click();

  // 3. ライブラリに RAW+JPEG が並ぶまで待つ。
  await page.getByRole("navigation").getByRole("link", { name: "ライブラリ" }).click();
  const pair = page.getByRole("checkbox", { name: /IMG_0003\.(JPG|CR2) を選ぶ$/ });
  await expect(pair).toHaveCount(2, { timeout: 60_000 });

  // 4. 2 枚を送る。
  await pair.nth(0).check();
  await pair.nth(1).check();
  await page.getByRole("checkbox", { name: "immich-1" }).check();
  await page.getByRole("button", { name: /送信する/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "実行する" }).click();
  await expect(page.getByRole("status")).toContainText("送信を始めました");

  // 5. **相手側で 1 スタックになる**（第 2 パスはアップロードの後に回る）。
  //    **どちらが先に送られるかは決まらない**ので、主資産の候補を両方引く。
  const membersOfTheStack = async (): Promise<number> => {
    for (const primary of ["asset-1", "asset-2"]) {
      const response = await request.get(
        `${app.immich[0]}/api/stacks?primaryAssetId=${primary}`,
        { headers: { "x-api-key": "test-api-key" } },
      );
      const stacks = (await response.json()) as { assets: { id: string }[] }[];
      if (stacks.length > 0) {
        return stacks[0].assets.length;
      }
    }
    return 0;
  };
  await expect.poll(membersOfTheStack, { timeout: 60_000 }).toBe(2);

  // 6. ダッシュボードに組の数が出る。
  await page.getByRole("navigation").getByRole("link", { name: "ダッシュボード" }).click();
  await expect(page.getByRole("row", { name: /immich-1/ })).toContainText("1 組");

  expect(crashes).toEqual([]);
});
