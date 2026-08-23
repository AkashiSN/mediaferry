import { describe, expect, it } from "vitest";

import { tasksFrom } from "./useTasks";

const NONE = {
  merge_candidates: 0,
  merge_review_total: 0,
  unsent_total: 0,
  awaiting_total: 0,
};

describe("やることの導出", () => {
  it("在るものだけを、つなぐ → 確かめる → 送る → 確認 の順で出す", () => {
    expect(
      tasksFrom({
        merge_candidates: 3,
        merge_review_total: 1,
        unsent_total: 48,
        awaiting_total: 2,
      }),
    ).toEqual([
      { kind: "merge", count: 3 },
      { kind: "merge_review", count: 1 },
      { kind: "send", count: 48 },
      { kind: "approve", count: 2 },
    ]);
  });

  it("0 のものは出さない", () => {
    expect(tasksFrom({ ...NONE, unsent_total: 48 })).toEqual([{ kind: "send", count: 48 }]);
  });

  // つないだが検証に落ちた組は、送る候補にも構成ファイルにも出ない。**ここで
  // 出さないと、画面のどこからも辿れないまま残る。**
  it("中身を見て決めるだけの組も、やることとして出す", () => {
    expect(tasksFrom({ ...NONE, merge_review_total: 2 })).toEqual([
      { kind: "merge_review", count: 2 },
    ]);
  });

  it("全部 0 なら空", () => {
    expect(tasksFrom(NONE)).toEqual([]);
  });

  it("まだ読めていない間は空。**0 件と混ぜない**", () => {
    // 読み込み中に「やることはありません」と出すと、直後に 3 件現れて驚かせる。
    expect(tasksFrom(null)).toEqual([]);
  });
});
