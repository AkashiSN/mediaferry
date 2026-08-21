// Phase 5 の受け入れ（§20）。**汎用化を、画面だけでなぞる。**
//
// 1. Canon 風の合成カードが `Canon EOS` と判定され、理由が画面に出る
// 2. 2 枚が並び、独立にスキャンできる
// 3. 信頼すると、**画面を操作せずに**取り込みが積まれる
// 4. カメラの種類を複製して編集すると新しい版ができ、試した判定が変わる
// 5. 撮影日時を再計算すると一覧の日時が変わり、**ファイルは動かない**
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

/** ホームの「さっき取り込んだもの」の 1 行（`rel_path`（`captured_at`））。 */
function recentRow(text: string): { relPath: string; capturedAt: string } {
  const [relPath, rest] = text.split("（");
  return { relPath, capturedAt: (rest ?? "").replace("）", "") };
}

test("汎用化の受け入れが画面から通る", async ({ page }) => {
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

  // 1 と 2. カードの中身に 2 枚並び、Canon 側は Canon EOS と判定され、理由が出る。
  //    **画面には slug ではなく表示名を出す**（§13）。
  await page.goto(app.url + "/card");
  await expect(page.getByRole("heading", { name: "SD_Card" })).toBeVisible({ timeout: 60_000 });
  const canon = page.locator("section.card").filter({ hasText: "EOS_DIGITAL" });
  await expect(canon).toContainText("Canon EOS");
  await expect(canon).toContainText("一致するファイルが");

  // 2. それぞれ独立にスキャンできる。
  await page.getByRole("button", { name: "EOS_DIGITAL をスキャン" }).click();
  await page.getByRole("button", { name: "SD_Card をスキャン" }).click();

  // 3. 信頼すると、**取り込みボタンを押さずに**取り込みが始まる（AUTO_IMPORT=trusted）。
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

  // **取り込みは誰も押していない。** 作業の履歴に積まれたことを確かめる。
  await page.goto(app.url + "/settings/jobs");
  await expect(page.getByText("取り込み").first()).toBeVisible({ timeout: 90_000 });

  // ホームの「さっき取り込んだもの」に、取り込んだ順に出る。
  await nav.getByRole("link", { name: "ホーム" }).click();
  const dji = page.getByText(/^library\/dji-osmo\//).first();
  await expect(dji).toBeVisible({ timeout: 90_000 });
  const before = recentRow((await dji.textContent()) ?? "");
  expect(before.capturedAt).toContain("+09:00");

  // 1（続き）. **Canon 風のカードも取り込める。** `timestamp.source: exif` なので、
  // 撮影日時は公開済みファイルの EXIF から来る（`timezone_policy: none` で +00:00）。
  await page.goto(app.url + "/card");
  await page.getByRole("button", { name: "EOS_DIGITAL を取り込む" }).click();
  await page.getByRole("button", { name: "ホームへ" }).click();
  await expect(page.getByText(/^library\/canon-eos\//).first()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/2026-02-03T04:05:06\+00:00/)).toBeVisible();

  // 4. 複製して編集 → 新しい版 → 試した判定が変わる。
  await nav.getByRole("link", { name: "設定" }).click();
  await page.getByRole("link", { name: "カメラの種類を変える" }).click();
  await page.getByRole("button", { name: "複製して変える：Canon EOS" }).click();
  await page.getByLabel("新しい slug").fill("my-canon");
  await page.getByLabel("表示名").fill("わたしの Canon");
  await page.getByRole("button", { name: "複製する" }).click();
  const editor = page.getByLabel("カメラの種類の定義（YAML）");
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

  await page.getByRole("button", { name: "わたしの Canon を EOS_DIGITAL で試す" }).click();
  await expect(page.getByText(/「わたしの Canon」と EOS_DIGITAL: 一致しません/)).toBeVisible();
  await page.getByRole("button", { name: "Canon EOS を EOS_DIGITAL で試す" }).click();
  await expect(page.getByText(/「Canon EOS」と EOS_DIGITAL: 一致します/)).toBeVisible();

  // 5. TZ を変えて再計算 → 撮影日時が変わり、**ファイルは動かない**。
  await nav.getByRole("link", { name: "設定" }).click();
  await page.getByRole("link", { name: "詳しい設定" }).click();
  await page.getByLabel("DEFAULT_TIMEZONE の値").fill("Europe/Berlin");
  await page.getByLabel("DEFAULT_TIMEZONE の値").blur();
  await nav.getByRole("link", { name: "設定" }).click();
  await page.getByRole("link", { name: "カメラの種類を変える" }).click();
  await page.getByRole("button", { name: "撮影日時を再計算する：DJI Osmo Pocket" }).click();
  await expect(page.getByRole("link", { name: "作業の進み具合を見る" })).toBeVisible();

  await nav.getByRole("link", { name: "ホーム" }).click();
  const after = page.getByText(/^library\/dji-osmo\/.*\+02:00/).first();
  await expect(after).toBeVisible({ timeout: 60_000 });
  // ライブラリのパスは `captured_at` を含まない（§7）ので、名前は 1 つも動かない。
  expect(recentRow((await after.textContent()) ?? "").relPath).toBe(before.relPath);

  expect(crashes).toEqual([]);
});
