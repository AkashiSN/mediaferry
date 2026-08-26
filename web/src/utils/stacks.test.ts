// 組の扱い（まとめる・名乗る）。**3 画面が同じものを呼ぶ**ので、ここが唯一の規則。

import { describe, expect, it } from "vitest";

import { groupIntoStacks, stackLabel } from "./stacks";

const member = (id: string, rel_path: string, size_bytes = 100) => ({ id, rel_path, size_bytes });

const row = (id: string, rel_path: string, members?: { id: string; rel_path: string; size_bytes: number }[]) => ({
  id,
  rel_path,
  stack: members ? { members } : null,
});

describe("組の名乗り", () => {
  it("2 枚の組は 主の拡張子 + RAW", () => {
    expect(stackLabel([member("a", "x/IMG_1.JPG"), member("b", "x/IMG_1.CR2")])).toBe("JPG+RAW");
  });

  it("主が JPG でなくても、主の拡張子をそのまま名乗る", () => {
    // **画面が実在しないファイル名を名乗らない。** `stack.extensions` は
    // 利用者が編集できるので、主が HEIC の組がありうる。
    expect(stackLabel([member("a", "x/IMG_1.HEIC"), member("b", "x/IMG_1.DNG")])).toBe("HEIC+RAW");
  });

  it("主以外が 2 つ以上なら枚数を添える", () => {
    // **組の枚数を黙って隠さない。**
    expect(
      stackLabel([member("a", "x/I.JPG"), member("b", "x/I.CR2"), member("c", "x/I.HIF")]),
    ).toBe("JPG+RAW ×2");
  });

  it("1 枚では組にならないので名乗らない", () => {
    expect(stackLabel([member("a", "x/IMG_1.JPG")])).toBeNull();
    expect(stackLabel([])).toBeNull();
  });

  it("拡張子は大文字にそろえる", () => {
    expect(stackLabel([member("a", "x/IMG_1.jpg"), member("b", "x/IMG_1.cr2")])).toBe("JPG+RAW");
  });
});

describe("組にまとめる", () => {
  const jpeg = member("j", "x/IMG_1.JPG");
  const raw = member("r", "x/IMG_1.CR2");

  it("同じ組の 2 行を 1 タイルにし、主を先頭にする", () => {
    const tiles = groupIntoStacks([row("r", "x/IMG_1.CR2", [jpeg, raw]), row("j", "x/IMG_1.JPG", [jpeg, raw])]);

    expect(tiles).toHaveLength(1);
    expect(tiles[0].primary.id).toBe("j");
    expect(tiles[0].rows.map((r) => r.id)).toEqual(["j", "r"]);
  });

  it("**集合に来ていない相方は、タイルに入れない**", () => {
    // 送る画面は「返ってきた行」しか送らない。JPG が送信済みで CR2 だけ
    // 未送信のとき、members には JPG が居るが、送るのは CR2 だけ。
    const tiles = groupIntoStacks([row("r", "x/IMG_1.CR2", [jpeg, raw])]);

    expect(tiles).toHaveLength(1);
    expect(tiles[0].primary.id).toBe("r");
    expect(tiles[0].rows.map((r) => r.id)).toEqual(["r"]);
  });

  it("組でない行は単独のタイルになる", () => {
    const tiles = groupIntoStacks([row("a", "x/A.JPG"), row("b", "x/B.JPG")]);

    expect(tiles.map((t) => t.rows.map((r) => r.id))).toEqual([["a"], ["b"]]);
  });

  it("**並びは入力順を保つ**（最初に現れた行の位置にタイルを置く）", () => {
    // API の並び（captured_at DESC, rel_path DESC）を崩すと、日付のまとまりが割れる。
    const tiles = groupIntoStacks([
      row("a", "x/A.JPG"),
      row("r", "x/IMG_1.CR2", [jpeg, raw]),
      row("j", "x/IMG_1.JPG", [jpeg, raw]),
      row("z", "x/Z.JPG"),
    ]);

    expect(tiles.map((t) => t.primary.id)).toEqual(["a", "j", "z"]);
  });

  it("別々の組が混ざらない", () => {
    const other = [member("p", "y/IMG_2.JPG"), member("q", "y/IMG_2.CR2")];
    const tiles = groupIntoStacks([
      row("j", "x/IMG_1.JPG", [jpeg, raw]),
      row("p", "y/IMG_2.JPG", other),
      row("r", "x/IMG_1.CR2", [jpeg, raw]),
      row("q", "y/IMG_2.CR2", other),
    ]);

    expect(tiles.map((t) => t.rows.map((r) => r.id))).toEqual([
      ["j", "r"],
      ["p", "q"],
    ]);
  });
});
