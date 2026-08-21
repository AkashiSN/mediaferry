// `stubApi` のパス解決。レビュー指摘（Task 7 修正ラウンド 1・Important #3）。
//
// **厳密一致を優先し、無ければ前方一致。** 前方一致だけだと、`/media`（一覧）の
// 鍵が `/media/m1`（詳細）のような別の資源まで拾ってしまう（`Send.tsx` が実際に
// 踏んだ）。登録の順番に依存しないことをここで確かめる。

import { afterEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("stubApi のパス解決", () => {
  it("より広い鍵を先に登録しても、厳密一致する鍵を優先する", async () => {
    // **登録順をわざと逆にする。** 前方一致だけの実装だと、最初に見つかった
    // "/media" が先に当たってしまい、この順序では検出できない。
    stubApi({
      "/media": { media: [{ id: "list-item" }], total: 1, page: 1, page_size: 50 },
      "/media/m1": { id: "m1", rel_path: "a.JPG" },
    });
    const response = await fetch("/api/media/m1");
    const body = await response.json();
    expect(body).toEqual({ id: "m1", rel_path: "a.JPG" });
  });

  it("厳密一致する鍵が無ければ、今までどおり前方一致で拾う", async () => {
    stubApi({ "/media": { media: [], total: 0, page: 1, page_size: 50 } });
    const response = await fetch("/api/media?status=unsent&destination_id=d1");
    const body = await response.json();
    expect(body).toEqual({ media: [], total: 0, page: 1, page_size: 50 });
  });
});
