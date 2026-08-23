// テスト用の `fetch` の代役。`/api` を剥がしたパスで `routes` から本体を返す。
//
// 記録した呼び出しは返り値の `calls()` から読む。モジュールの変数に記録すると、
// ファイルを分けたときに前のテストの記録が混ざる。

import { vi } from "vitest";

export type Call = { path: string; method: string };

/**
 * **厳密一致を優先し、無ければ前方一致。** 前方一致だけだと、`/media`（一覧）が
 * `/media/m1`（詳細）のような別の資源まで拾ってしまい、一覧の本文が詳細の代わりに
 * 返る（`work/Send.tsx` は個別に `GET /media/{id}` を叩く）。**前方一致も残す**:
 * 厳密一致の鍵が無ければ、前方一致（最初に登録した鍵）を返す。
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
      if (key === undefined) {
        // **知らないパスを 200 で返さない。** 空の本文を返すと、登録し忘れた
        // 経路が「空の応答」として素通りし、テストが中身を見ないまま緑になる。
        return Promise.resolve(
          new Response(
            JSON.stringify({
              error: { code: "not_found", detail: `stubApi に ${path} の登録が無い`, meta: {} },
            }),
            { status: 404 },
          ),
        );
      }
      return Promise.resolve(new Response(JSON.stringify(routes[key]), { status: 200 }));
    }),
  );
  return { calls: () => [...calls] };
}
