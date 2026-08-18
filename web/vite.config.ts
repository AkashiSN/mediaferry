import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 開発中だけ /api をローカルのアプリへ転送する。
// **ホスト名とポートを焼き込まない**（環境固有の値をリポジトリに含めない）。
const apiTarget = process.env.MEDIAFERRY_DEV_API ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: apiTarget, changeOrigin: false },
    },
  },
  build: {
    // app イメージへ焼く成果物。Dockerfile が web/dist をコピーする。
    outDir: "dist",
    emptyOutDir: true,
  },
});
