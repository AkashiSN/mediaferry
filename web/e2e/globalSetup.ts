// E2E の前にビルドし直す。
//
// **E2E は `web/dist` を配るだけで、ソースを直接は読まない。** ビルドを忘れると、
// 直したつもりのソースが古いビルド成果物のまま残り、§13 の画面横断規則（内部の
// 名前・44px・ライト/ダーク・確認ダイアログ）と、`stack_reason` 等サーバ由来の
// 文字列を検出する Python 側の錠が無い変更を、E2E だけが唯一検出できていたのに、
// そのまま緑で通してしまう。`npm run test:e2e` からだけでなく `npx playwright
// test` を直接叩いても効くよう、package.json のスクリプトではなくここに置く。

import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export default function globalSetup(): void {
  const web = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  execFileSync("npm", ["run", "build"], { cwd: web, stdio: "inherit" });
}
