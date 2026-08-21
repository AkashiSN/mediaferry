// テスト用の `fetch` の代役。`/api` を剥がしたパスで `routes` から本体を返す。
//
// 記録した呼び出しは返り値の `calls()` から読む。モジュールの変数に記録すると、
// ファイルを分けたときに前のテストの記録が混ざる。

import { vi } from "vitest";

export type Call = { path: string; method: string };

/**
 * **厳密一致を優先し、無ければ前方一致。** 前方一致だけだと、`/media`（一覧）が
 * `/media/m1`（詳細）のような別の資源まで拾ってしまい、一覧の本文が詳細の代わりに
 * 返る（`Send.tsx` が個別に `GET /media/{id}` を叩く実装で実際に踏んだ）。**後方
 * 互換**: 厳密一致の鍵が無ければ、今までどおり前方一致（最初に登録した鍵）を返す。
 */
function matchRoute(path: string, routes: Record<string, unknown>): string | undefined {
  const keys = Object.keys(routes);
  const exact = keys.find((candidate) => candidate === path);
  if (exact !== undefined) {
    return exact;
  }
  return keys.find((candidate) => path.startsWith(candidate));
}

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
      const key = matchRoute(path, routes);
      return Promise.resolve(
        new Response(JSON.stringify(key === undefined ? {} : routes[key]), { status: 200 }),
      );
    }),
  );
  return { calls: () => [...calls] };
}
