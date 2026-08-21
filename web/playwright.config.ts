import { defineConfig } from "@playwright/test";

// **本物を通す。** ビルド済み資産を配る実プロセス（fake broker と fake Immich 2 台つき）を
// 立ち上げてから、ブラウザで操作する。mock の fetch では静的配信も Cookie も SSE も
// 通らない。
export default defineConfig({
  testDir: "e2e",
  // **`npm run build` を毎回やり直してから通す。** ビルドを忘れた 1 回で、E2E が
  // 唯一検出している §13 の画面横断規則とサーバ由来の文字列の錠が、まとめて
  // 前のビルドを試験することになる（`e2e/globalSetup.ts`）。
  globalSetup: "./e2e/globalSetup.ts",
  // **spec の中の待ちより長くする。** いちばん遅いテストでも 10 秒で終わるが、
  // `{ timeout: 60_000 }` の待ちが 1 本でも満了すると 60 秒の予算に収まらず、
  // 「どの待ちで詰まったか」ではなく「テストが時間切れ」としか出なくなる。
  timeout: 120_000,
  expect: { timeout: 10_000 },
  use: { trace: "off" },
  reporter: [["list"]],
  workers: 1,
});
