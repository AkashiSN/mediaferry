import { describe, expect, it } from "vitest";

import { ApiError, toApiError } from "./errors";

describe("日本語のメッセージ", () => {
  it("知っている code は、次に何をすべきかまで出す", () => {
    expect(new ApiError(403, "csrf_failed", "画面を再読み込みしてから操作する").message).toContain(
      "再読み込み",
    );
  });

  it("知らない code のときだけ detail を添える", () => {
    const error = new ApiError(500, "brand_new_code", "内部の文言");
    expect(error.message).toContain("内部の文言");
    expect(error.message).toContain("予期しない");
  });

  it("detail が無くても文になる", () => {
    expect(new ApiError(500, "brand_new_code", "").message).toBe("予期しないエラー");
  });

  it("封筒でない本文は internal として扱う", () => {
    expect(toApiError(500, "<html>error</html>").code).toBe("internal");
  });
});
