// OpenAPI から TypeScript の型を作る。
//
// **型は手書きしない。** API を変えたら `web/src/api/types.ts` に差分が出るように、
// 生成物をコミットする（差分が出ないなら、UI と API のずれが起きていない証拠になる）。
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..");
const out = resolve(here, "..", "src", "api", "types.ts");

// スキーマはアプリ自身から取り出す（起動せずに openapi() を呼ぶ）。
const schema = execFileSync(
  "uv",
  [
    "run",
    "python",
    "-c",
    "import json;from mediaferry.api.app import create_app;print(json.dumps(create_app().openapi()))",
  ],
  { cwd: repo, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
);

const schemaPath = resolve(here, "..", "openapi.json");
writeFileSync(schemaPath, schema);
const types = execFileSync("npx", ["openapi-typescript", schemaPath], {
  cwd: resolve(here, ".."),
  encoding: "utf8",
  maxBuffer: 32 * 1024 * 1024,
});
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, types);
console.log(`書き出した: ${out}`);
