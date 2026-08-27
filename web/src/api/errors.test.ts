import { describe, expect, it } from "vitest";

import { ApiError, toApiError } from "./errors";

describe("日本語のメッセージ", () => {
  it("知っている code は、次に何をすべきかまで出す", () => {
    expect(new ApiError(403, "csrf_failed", "画面を再読み込みしてから操作する").message).toContain(
      "再読み込み",
    );
  });

  it("staging_pending は job_in_flight と別の文になる", () => {
    // リセットが断られる理由は 2 つある。走っている作業があるときと、回収待ちの
    // 取り込みが残っているとき。文面が同じだと、履歴を見ても何も無い後者で
    // 「何を待てばよいか」が分からない。
    const staging = new ApiError(409, "staging_pending", "回収待ちの取り込みがある").message;
    const running = new ApiError(409, "job_in_flight", "走っている作業がある").message;
    expect(staging).not.toBe(running);
    expect(staging).toContain("再起動");
  });

  it("staging_pending は、再起動しても片付かない場合の道も書く", () => {
    // `jobs/reconcile.py` の `StagingLost` / `OSError` は行を消さずに残す
    // （実体が無い・データセットが一時的に見えないなど）。「再起動すれば
    // 片付く」だけを断定すると、この場合は再起動してもリセットが永久に
    // 断られ続ける。片付かないときに何を見ればよいかも書く。
    const staging = new ApiError(409, "staging_pending", "回収待ちの取り込みがある").message;
    expect(staging).toContain("作業の履歴");
  });

  it("upload_claim_pending は job_in_flight とも staging_pending とも別の文になる", () => {
    // `job` を先に消す手順が外部キーで止まるのは、走っている作業と回収待ちの
    // 公開だけではない。放置された送信の claim も同じ形で止まる。
    const claim = new ApiError(409, "upload_claim_pending", "回収待ちの送信がある").message;
    const staging = new ApiError(409, "staging_pending", "回収待ちの取り込みがある").message;
    const running = new ApiError(409, "job_in_flight", "走っている作業がある").message;
    expect(claim).not.toBe(staging);
    expect(claim).not.toBe(running);
    // **この場合は「再起動すると片付く」が本当に正しい**
    // （`release_interrupted` が起動時に claim を回収する）。
    expect(claim).toContain("再起動");
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

describe("どこが悪いかを落とさない", () => {
  it("検証の失敗は、どの項目が悪いかまで出す", () => {
    // ProfileInvalid の文言はこちらが書いた日本語で、項目名を含む（§13）。
    // 「入力の形式が正しくありません」だけでは、YAML のどこを直せばよいか分からない。
    const error = new ApiError(400, "validation_failed", "timestamp.pattern は名前付きグループ ts を持つ必要がある");
    expect(error.message).toContain("timestamp.pattern");
  });

  it("detail が無ければ、括弧だけの文にしない", () => {
    expect(new ApiError(400, "validation_failed", "").message).toBe("入力の形式が正しくありません。");
  });

  // `bad_request` は 400 の受け皿で、いちばんよく出る。文面を持たないと、
  // **こちらが意図して断った入力ミスまで「予期しないエラー」になる。**
  it("400 の受け皿にも文面があり、断った理由を添える", () => {
    const error = new ApiError(400, "bad_request", "slug は作成後に変更できない");
    expect(error.message).not.toContain("予期しない");
    expect(error.message).toContain("slug は作成後に変更できない");
  });

  it("bad_request で detail が無ければ、括弧だけの文にしない", () => {
    expect(new ApiError(400, "bad_request", "").message).toBe(
      "この操作は受け付けられません。入力を確かめてください。",
    );
  });
});
