import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    // Playwright の E2E は vitest から拾わない（走らせ方が違う）。
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    globals: true,
    // **差し替えたグローバルを、テストごとに戻す。** `vi.restoreAllMocks()` は
    // `vi.stubGlobal` を戻さないので、これが無いと前のファイルが差し替えた
    // `fetch` が次のファイルへ residual として残る（自分では差し替えていない
    // テストが、前のテストの応答で緑になる）。
    unstubGlobals: true,
  },
});
