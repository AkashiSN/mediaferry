import { defineConfig } from "@playwright/test";

// **本物を通す。** ビルド済み資産を配る実プロセス（fake broker と fake Immich 2 台つき）を
// 立ち上げてから、ブラウザで操作する。mock の fetch では静的配信も Cookie も SSE も
// 通らない。
export default defineConfig({
  testDir: "e2e",
  // 主要動線は取り込み・つなぐ・送るを通すので、1 本が長い。
  timeout: 120_000,
  expect: { timeout: 10_000 },
  use: { trace: "off" },
  reporter: [["list"]],
  workers: 1,
});
