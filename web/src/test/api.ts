// テスト用の `fetch` の代役。`/api` を剥がしたパスで `routes` から本体を返す。
//
// 記録した呼び出しは返り値の `calls()` から読む。モジュールの変数に記録すると、
// ファイルを分けたときに前のテストの記録が混ざる。

import { vi } from "vitest";

export type Call = { path: string; method: string };

/** 鍵は `"/media"` か `"POST /uploads"`。方式を書いた鍵は、その方式だけに当たる。 */
function parseKey(key: string): { method?: string; path: string } {
  const space = key.indexOf(" ");
  if (space === -1) {
    return { path: key };
  }
  return { method: key.slice(0, space), path: key.slice(space + 1) };
}

/**
 * **厳密一致か、問い合わせの区切りまでの一致だけ。** `/media` の鍵で
 * `/media?status=unsent` は拾い、`/uploads?state=x` の鍵で `/uploads?state=x&limit=200`
 * も拾うが、`/media/m1`（詳細）は拾わない。深いパスまで前方一致で拾うと、
 * `PATCH /merge-groups/{id}` が一覧の本文を 200 で受け取り、画面が中身を見ないまま
 * テストが緑になる。
 *
 * 同じパスに複数の鍵が当たるときは、**方式を書いた鍵**、次に**厳密一致**を優先する。
 */
function matchRoute(
  path: string,
  method: string,
  routes: Record<string, unknown>,
): string | undefined {
  let best: { key: string; score: number } | undefined;
  for (const key of Object.keys(routes)) {
    const route = parseKey(key);
    if (route.method !== undefined && route.method !== method) {
      continue;
    }
    const exact = route.path === path;
    // 続きが `?` か `&` で始まるときだけ前方一致を許す（`/media` が `/media/m1` を
    // 拾わないようにする）。
    const rest = path.startsWith(route.path) ? path.slice(route.path.length) : null;
    if (!exact && (rest === null || !(rest.startsWith("?") || rest.startsWith("&")))) {
      continue;
    }
    const score = (route.method !== undefined ? 2 : 0) + (exact ? 1 : 0);
    if (best === undefined || score > best.score) {
      best = { key, score };
    }
  }
  return best?.key;
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
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      onCall?.(path, init);
      const key = matchRoute(path, method, routes);
      if (key === undefined) {
        // **知らないパスを 200 で返さない。** 空の本文を返すと、登録し忘れた
        // 経路が「空の応答」として素通りし、テストが中身を見ないまま緑になる。
        return Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "not_found",
                detail: `stubApi に ${method} ${path} の登録が無い`,
                meta: {},
              },
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
