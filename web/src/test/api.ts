// テスト用の `fetch` の代役。`/api` を剥がしたパスと前方一致で `routes` から本体を返す。
//
// 記録した呼び出しは返り値の `calls()` から読む。モジュールの変数に記録すると、
// ファイルを分けたときに前のテストの記録が混ざる。

import { vi } from "vitest";

export type Call = { path: string; method: string };

export function stubApi(
  routes: Record<string, unknown>,
  onCall?: (path: string, init?: RequestInit) => unknown,
): { calls: () => Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const path = input.replace(/^\/api/, "");
      calls.push({ path, method: init?.method ?? "GET" });
      onCall?.(path, init);
      const key = Object.keys(routes).find((candidate) => path.startsWith(candidate));
      return Promise.resolve(
        new Response(JSON.stringify(key === undefined ? {} : routes[key]), { status: 200 }),
      );
    }),
  );
  return { calls: () => [...calls] };
}
