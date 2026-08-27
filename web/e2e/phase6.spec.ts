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
  // 追加の欄に限る（既にある送り先の「接続の設定」にも同じ見出しの欄がある）。
  const form = page.getByRole("form", { name: "送り先を追加する" });
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

  // 3. 写真に RAW+JPEG が並ぶまで待つ。**写真タブは組を畳むので、選ぶ丸は
  //    IMG_0003 につき 1 つだけ**（CR2 の行は一覧に出ない）。畳まれた行には
  //    `JPG+RAW` の札が付く（**2 枚あることが札から読める**）。
  await nav.getByRole("link", { name: "写真" }).click();
  const combined = page.getByRole("button", { name: "選ぶ：IMG_0003.JPG" });
  await expect(combined).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: /^選ぶ：IMG_0003\.(JPG|CR2)$/ })).toHaveCount(1);
  // **`exact: true` を外さない。** Playwright の `getByText` は既定が部分一致なので、
  // `RAW` で当てると `JPG+RAW` にも素通りし、札の中身を何も確かめないテストになる。
  await expect(
    page.locator(".tile", { has: combined }).getByText("JPG+RAW", { exact: true }),
  ).toBeVisible();

  // 3b. **日付の丸で、その日をまとめて選べる。** 押すと組の相方も一緒に入る。
  const day = page.getByRole("checkbox", { name: /をまとめて選ぶ/ }).first();
  await day.click();
  await expect(day).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText(/件を選択中/)).toBeVisible();
  await page.getByRole("button", { name: "やめる" }).click();

  // 3c. **詳細でも組が見える。** 一覧で見えたものが、押した先で消えない。
  await page
    .locator(".tile", { has: combined })
    .getByRole("link", { name: "IMG_0003.JPG" })
    .click();
  await expect(page.getByRole("heading", { name: "この 1 枚を作っているファイル" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "送る：IMG_0003.JPG" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "送る：IMG_0003.CR2" })).toBeChecked();
  await expect(page.getByText(/2 枚 ・ .* を送ります/)).toBeVisible();
  await nav.getByRole("link", { name: "写真" }).click();

  // 4. 畳んだタイルは 1 枚で組の両方（JPG+CR2）を表すので、**1 つ選ぶだけで
  //    相方も一緒に選ばれる。** これに、組にならない 1 枚（相方の無い
  //    IMG_0001.JPG）を選んで送る。
  await combined.click();
  await page.getByRole("button", { name: "選ぶ：IMG_0001.JPG" }).click();
  await page.getByRole("button", { name: "送る", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Immich へ送る" })).toBeVisible();
  // **送る画面でも 1 タイル・JPG+RAW。** 一覧で 1 枚だったものが割れて戻らない。
  await expect(page.getByText("JPG+RAW", { exact: true })).toBeVisible();
  // 送り先が 1 つしか無ければ、黙ってそれを使う。
  await expect(page.getByText("送り先：immich-1")).toBeVisible();
  await page.getByRole("button", { name: "内容を確かめる" }).click();
  await expect(page.getByRole("dialog")).toContainText("3 件");
  await page.getByRole("button", { name: "実行する" }).click();
  // **送るは押した瞬間にホームへ遷移する。** 進捗の置き場はホーム 1 本。
  await expect(page.getByRole("heading", { name: "ホーム", exact: true })).toBeVisible();

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

  // 6. **束ねられなかった 1 枚（IMG_0001）は、理由が送り先の画面に出る。**
  //    「対象外のときだけ出す」形にしないので、文言まで当てる（Phase 5 の教訓）。
  await page.goto(app.url + "/settings/destinations");
  await expect(page.getByText(/相方が見つからない/)).toBeVisible({ timeout: 60_000 });

  expect(crashes).toEqual([]);
});
