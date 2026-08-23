// 大きさの表示（§13）。**確認・送る・つなぐ・進捗が同じ書式を使う。**

import { describe, expect, it } from "vitest";

import { formatBytes } from "./formatBytes";

describe("大きさの表示", () => {
  it("人が読める単位にする", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(30 * 1024 ** 3)).toBe("30 GiB");
  });

  it("ちょうど 1024 で次の単位へ上がる", () => {
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1 KiB");
  });

  it("いちばん大きい単位で止める（単位表を踏み外さない）", () => {
    expect(formatBytes(2 * 1024 ** 5)).toBe("2048 TiB");
  });

  it("10 以上は小数を出さない（読みやすさ優先）", () => {
    expect(formatBytes(10752)).toBe("11 KiB");
    expect(formatBytes(9 * 1024 + 512)).toBe("9.5 KiB");
  });
});
