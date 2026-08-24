// 進捗の 1 行（§13）。**サーバが返す phase を、そのまま出さない。**

import { describe, expect, it } from "vitest";

import { progressLine, statusLabel } from "./JobProgress";

describe("進捗の 1 行", () => {
  it("phase を §13 の言葉に写す（結合 → つなぐ）", () => {
    const line = progressLine({ phase: "merge", rel_path: "a/b/DJI_0001.MP4" }, null);
    expect(line).toContain("つないでいます");
    expect(line).not.toContain("結合");
    expect(line).not.toContain("merge");
  });

  it("コピーもそのまま出さない", () => {
    expect(progressLine({ phase: "copy" }, null)).toContain("コピー中");
  });

  it("写せない phase は、そのまま出すしかない（黙って消さない）", () => {
    expect(progressLine({ phase: "verify" }, null)).toContain("verify");
  });

  it("ファイル名は末尾だけを出す（内部の相対パスを出さない）", () => {
    const line = progressLine({ phase: "copy", rel_path: "DCIM/DJI_001/DJI_0001.MP4" }, null);
    expect(line).toContain("DJI_0001.MP4");
    expect(line).not.toContain("DCIM/DJI_001");
  });

  it("件数と割合を出す", () => {
    const line = progressLine(
      { phase: "copy", file_index: 3, file_count: 29, bytes_done: 512, bytes_total: 1024 },
      null,
    );
    expect(line).toContain("3/29 件");
    expect(line).toContain("50%");
  });

  it("速度が分かるときは、残りも出す", () => {
    const line = progressLine({ phase: "copy", bytes_done: 512, bytes_total: 1024 }, 512);
    expect(line).toContain("512 B/秒");
    expect(line).toContain("残り約 1 秒");
  });

  it("送信の進捗を、内部の名前のまま出さない", () => {
    expect(
      progressLine(
        { phase: "upload", rel_path: "library/2026/DJI_0042.MP4", file_index: 12,
          file_count: 35, bytes_done: 1_000, bytes_total: 4_000 },
        null,
      ),
    ).toContain("送信中");
  });

  it("状態も内部の値をそのまま出さない", () => {
    expect(statusLabel("running")).toBe("実行中");
    expect(statusLabel("cancelling")).toBe("キャンセル中");
    // 知らない状態だけは、そのまま出す（黙って消さない）。
    expect(statusLabel("weird")).toBe("weird");
  });
});
