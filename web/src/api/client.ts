// API を叩く薄い層。
//
// **CSRF の対を必ず載せる**（二重送信 Cookie。§14）。状態を変える要求では、
// Cookie の値をそのままヘッダへ写す。トークンは画面と `/api/auth/session` が配る。

import { toApiError } from "./errors";

const CSRF_COOKIE = "XSRF-TOKEN";
const CSRF_HEADER = "X-CSRF-Token";
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function csrfToken(): string | null {
  const found = document.cookie
    .split("; ")
    .find((part) => part.startsWith(`${CSRF_COOKIE}=`));
  return found ? decodeURIComponent(found.slice(CSRF_COOKIE.length + 1)) : null;
}

export type RequestOptions = {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
};

/** API を叩いて JSON を返す。失敗は `ApiError`（code つき）で投げる。 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (UNSAFE.has(method)) {
    const token = csrfToken();
    if (token !== null) {
      headers[CSRF_HEADER] = token;
    }
  }
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    // 同一オリジンなので既定で Cookie は載るが、明示しておく。
    credentials: "same-origin",
    signal: options.signal,
  });
  const text = await response.text();
  const parsed = text ? (JSON.parse(text) as unknown) : null;
  if (!response.ok) {
    throw toApiError(response.status, parsed);
  }
  return parsed as T;
}
