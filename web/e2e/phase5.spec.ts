// Phase 5 の受け入れ（§20）。**汎用化を、画面だけでなぞる。**
//
// 1. Canon 風の合成カードが `canon-eos` と判定され、理由が画面に出る
// 2. 2 枚が並び、独立にスキャンできる
// 3. 承認して信頼登録すると、**画面を操作せずに**取り込みが積まれる
// 4. プロファイルを複製して編集すると新リビジョンができ、`test` の判定が変わる
// 5. `recompute_timestamps` で一覧の撮影日時が変わり、**ファイルは動かない**
//
// **`DEFAULT_TIMEZONE` を env に置かない状態で立てる。** env にあると `locked` に
// なって画面から変えられず、5 の筋書き（設定を変えてから直す）が経路として通らない。

import { expect, test } from "@playwright/test";

import { start, type Running } from "./harness";

let app: Running;

test.beforeAll(async () => {
  app = await start(undefined, ["--timezone-from-db"]);
});

test.afterAll(() => {
  app?.stop();
});

test("汎用化の受け入れが画面から通る", async ({ page }) => {
  const crashes: string[] = [];
  page.on("pageerror", (error) => crashes.push(error.message));

  await page.goto(app.url);
  await expect(page.getByRole("heading", { name: "ダッシュボード" })).toBeVisible();

  // 0. 取り込みの前に TZ を決める（`force_offset` の dji-osmo は無いと取り込めない）。
  await page.getByRole("navigation").getByRole("link", { name: "設定" }).click();
  await page.getByLabel("DEFAULT_TIMEZONE の値").fill("Asia/Tokyo");
  await page.getByLabel("DEFAULT_TIMEZONE の値").blur();

  // 1 と 2. デバイスに 2 枚並び、Canon 側は canon-eos と判定され、理由が出る。
  await page.getByRole("navigation").getByRole("link", { name: "デバイス" }).click();
  await expect(page.getByRole("heading", { name: "SD_Card" })).toBeVisible();
  const canon = page.getByRole("listitem").filter({ hasText: "EOS_DIGITAL" });
  await expect(canon).toContainText("canon-eos");
  await expect(canon).toContainText("一致するファイルが");

  // 2. それぞれ独立にスキャンできる。
  await page.getByRole("button", { name: "EOS_DIGITAL をスキャン" }).click();
  await page.getByRole("button", { name: "SD_Card をスキャン" }).click();

  // 3. 承認すると、**取り込みボタンを押さずに**取り込みが始まる（AUTO_IMPORT=trusted）。
  const trust = page.getByRole("button", { name: "SD_Card を信頼する" });
  await expect(trust).toBeVisible();
  await trust.click();
  const dialog = page.getByRole("dialog");
  // **同意の対象には、いま挿してあるカードの中身が含まれる**（数秒後に取り込まれる）。
  await expect(dialog).toContainText("NAS へコピー");
  await expect(dialog).toContainText("いま入っている中身");
  await expect(dialog).toContainText("取り違え");
  await page.getByRole("button", { name: "実行する" }).click();
  await expect(page.getByText("挿すと自動で取り込みます")).toBeVisible({ timeout: 60_000 });

  // **取り込みは誰も押していない。** ジョブが積まれたことをジョブ画面で確かめる。
  await page.getByRole("navigation").getByRole("link", { name: "ジョブ" }).click();
  await expect(page.getByText("import").first()).toBeVisible({ timeout: 90_000 });

  await page.getByRole("navigation").getByRole("link", { name: "ライブラリ" }).click();
  const rows = page.getByRole("checkbox", { name: /を選ぶ$/ });
  // watcher の tick（5 秒）を 2 回またぐことがあるので長めに待つ。
  await expect(rows.first()).toBeVisible({ timeout: 90_000 });
  const before = await page.getByText(/^library\//).first().textContent();
  const capturedBefore = await page.getByText(/\+09:00$/).first().textContent();
  expect(capturedBefore).toContain("+09:00");

  // 1（続き）. **Canon 風のカードも取り込める。** `timestamp.source: exif` なので、
  // 撮影日時は公開済みファイルの EXIF から来る（`timezone_policy: none` で +00:00）。
  await page.getByRole("navigation").getByRole("link", { name: "デバイス" }).click();
  await page.getByRole("button", { name: "EOS_DIGITAL を取り込む" }).click();
  await page.getByRole("navigation").getByRole("link", { name: "ライブラリ" }).click();
  await expect(page.getByText(/^library\/canon-eos\//).first()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("2026-02-03T04:05:06+00:00")).toBeVisible();

  // 4. 複製して編集 → 新リビジョン → `test` の判定が変わる。
  await page.getByRole("navigation").getByRole("link", { name: "設定" }).click();
  await page.getByRole("button", { name: "canon-eos を複製して編集" }).click();
  await page.getByLabel("新しい slug").fill("my-canon");
  await page.getByRole("button", { name: "複製する" }).click();
  const editor = page.getByLabel("プロファイル定義（YAML）");
  await expect(editor).toBeVisible();
  // 当たらない `require` にすると、同じカードで判定が変わる。
  await editor.fill(
    (await editor.inputValue()).replace(
      /filename_pattern:.*/,
      "filename_pattern: ^NOPE_\\d{4}\\.JPG$",
    ),
  );
  await page.getByRole("button", { name: "保存する" }).click();
  await expect(page.getByText("「my-canon」を保存しました（版 2）。")).toBeVisible();

  await page.getByRole("button", { name: "my-canon を EOS_DIGITAL で試す" }).click();
  await expect(page.getByText(/my-canon × EOS_DIGITAL: 一致しない/)).toBeVisible();
  await page.getByRole("button", { name: "canon-eos を EOS_DIGITAL で試す" }).click();
  await expect(page.getByText(/canon-eos × EOS_DIGITAL: 一致/)).toBeVisible();

  // 5. TZ を変えて再計算 → 撮影日時が変わり、**ファイルは動かない**。
  await page.getByLabel("DEFAULT_TIMEZONE の値").fill("Europe/Berlin");
  await page.getByLabel("DEFAULT_TIMEZONE の値").blur();
  await page.getByRole("button", { name: "dji-osmo の撮影日時を再計算する" }).click();
  await expect(page.getByRole("link", { name: /ジョブの進捗/ })).toBeVisible();

  await page.getByRole("navigation").getByRole("link", { name: "ライブラリ" }).click();
  await expect(page.getByText(/\+02:00$/).first()).toBeVisible({ timeout: 60_000 });
  // ライブラリのパスは `captured_at` を含まない（§7）ので、名前は 1 つも動かない。
  expect(await page.getByText(/^library\//).first().textContent()).toBe(before);

  expect(crashes).toEqual([]);
});
