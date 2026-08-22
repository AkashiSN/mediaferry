import { describe, expect, it } from "vitest";

import { tasksFrom } from "./useTasks";

describe("やることの導出", () => {
  it("在るものだけを、つなぐ → 送る → 確認 の順で出す", () => {
    expect(tasksFrom({ merge_candidates: 3, unsent_total: 48, awaiting_total: 2 })).toEqual([
      { kind: "merge", count: 3 },
      { kind: "send", count: 48 },
      { kind: "approve", count: 2 },
    ]);
  });

  it("0 のものは出さない", () => {
    expect(tasksFrom({ merge_candidates: 0, unsent_total: 48, awaiting_total: 0 })).toEqual([
      { kind: "send", count: 48 },
    ]);
  });

  it("全部 0 なら空", () => {
    expect(tasksFrom({ merge_candidates: 0, unsent_total: 0, awaiting_total: 0 })).toEqual([]);
  });

  it("まだ読めていない間は空。**0 件と混ぜない**", () => {
    // 読み込み中に「やることはありません」と出すと、直後に 3 件現れて驚かせる。
    expect(tasksFrom(null)).toEqual([]);
  });
});
