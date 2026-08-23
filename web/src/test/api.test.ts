// `stubApi` のパス解決。
//
// **厳密一致か、問い合わせの区切りまでの一致だけ。** 深いパスまで前方一致で拾うと、
// `/media`（一覧）の鍵が `/media/m1`（詳細）まで拾い、`PATCH /merge-groups/{id}` が
// 一覧の本文を 200 で受け取る。登録の順番に依存しないことも、ここで確かめる。

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

  it("問い合わせ文字列が付いても、同じ資源の鍵で拾う", async () => {
    stubApi({ "/media": { media: [], total: 0, page: 1, page_size: 50 } });
    const response = await fetch("/api/media?status=unsent&destination_id=d1");
    const body = await response.json();
    expect(body).toEqual({ media: [], total: 0, page: 1, page_size: 50 });
  });
});

describe("stubApi の門番", () => {
  it("登録した鍵より深いパスは、前方一致で拾わずに 404 を返す", async () => {
    // `PATCH /merge-groups/{id}?action=regroup` が一覧の本文を 200 で受け取ると、
    // 画面が一覧を「変更の結果」と思い込み、テストは中身を見ないまま緑になる。
    stubApi({ "/merge-groups": { groups: [] } });
    const response = await fetch("/api/merge-groups/g20?action=regroup", { method: "PATCH" });
    expect(response.status).toBe(404);
  });

  it("鍵に方式を書けば、同じパスでも方式ごとに本文を分けられる", async () => {
    stubApi({
      "/uploads": { records: [] },
      "POST /uploads": { created: { pairs: [] } },
    });
    expect(await (await fetch("/api/uploads")).json()).toEqual({ records: [] });
    const created = await fetch("/api/uploads", { method: "POST" });
    expect(await created.json()).toEqual({ created: { pairs: [] } });
  });

  it("方式を書いた鍵だけがあるとき、別の方式は 404 を返す", async () => {
    stubApi({ "POST /uploads": { created: { pairs: [] } } });
    expect((await fetch("/api/uploads")).status).toBe(404);
  });
});

describe("stubApi の問い合わせ文字列", () => {
  it("鍵に書いた問い合わせの続きに絞り込みが足されても拾う", async () => {
    stubApi({ "/uploads?state=awaiting_datetime_approval": { records: [] } });
    const response = await fetch("/api/uploads?state=awaiting_datetime_approval&limit=200");
    expect(await response.json()).toEqual({ records: [] });
  });
});
