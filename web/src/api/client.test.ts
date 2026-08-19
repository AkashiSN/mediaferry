import { afterEach, describe, expect, it, vi } from "vitest";

import { csrfToken, request } from "./client";
import { ApiError } from "./errors";

function respond(status: number, body: unknown): Response {
  return new Response(body === null ? "" : JSON.stringify(body), { status });
}

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "XSRF-TOKEN=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

describe("CSRF の対", () => {
  it("状態を変える要求に Cookie の値をヘッダで載せる", async () => {
    document.cookie = "XSRF-TOKEN=abc123; path=/";
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    await request("/uploads", { method: "POST", body: { media_ids: [] } });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("abc123");
  });

  it("読み取りには載せない（Origin を持たない経路を壊さない）", async () => {
    document.cookie = "XSRF-TOKEN=abc123; path=/";
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { media: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await request("/media");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("Cookie が無ければ読み取れない", () => {
    expect(csrfToken()).toBeNull();
  });
});

describe("失敗の扱い", () => {
  it("封筒の code をそのまま持つ", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        respond(409, { error: { code: "not_retryable", detail: "だめ", meta: {} } }),
      ),
    );

    await expect(request("/uploads/x/retry", { method: "POST" })).rejects.toMatchObject({
      code: "not_retryable",
      status: 409,
    });
  });

  it("封筒でない応答でも例外の形を保つ", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(502, null)));

    const error = await request("/media").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("internal");
  });
});
