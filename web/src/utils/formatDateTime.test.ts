import { describe, expect, it } from "vitest";

import { formatCapturedDateTime, formatDate, formatDateTime, formatSystemDateTime } from "./formatDateTime";

describe("formatDate / formatDateTime", () => {
  it("壁時計をそのまま出す（見ている人の時計で読み替えない）", () => {
    expect(formatDate("2026-08-26T12:33:05+09:00")).toBe("2026年8月26日");
    expect(formatDateTime("2026-08-26T12:33:05+09:00")).toBe("2026年8月26日 12:33");
  });

  it("読めない値は「不明」と書く（空欄にしない）", () => {
    expect(formatDate("いつか")).toBe("日付が不明");
    expect(formatDateTime("2026-08-26")).toBe("日時が不明");
  });
});

// **システム時刻は常に UTC で保存される**（`mediaferry.clock`）。設定した
// タイムゾーンへ直して出す。**印は必ず添える** —— 直した後の数字は、印が無いと
// どの時計のものか分からない。
describe("formatSystemDateTime", () => {
  it("設定したタイムゾーンへ直して、印を添える", () => {
    expect(formatSystemDateTime("2026-08-26T03:33:05+00:00", "Asia/Tokyo")).toBe(
      "2026年8月26日 12:33（JST）",
    );
  });

  it("略称の無いゾーンは、オフセットで出す", () => {
    expect(formatSystemDateTime("2026-08-26T03:33:05+00:00", "Europe/Berlin")).toBe(
      "2026年8月26日 05:33（GMT+2）",
    );
  });

  it("日をまたぐ直しも正しく出す", () => {
    expect(formatSystemDateTime("2026-08-25T23:30:00+00:00", "Asia/Tokyo")).toBe(
      "2026年8月26日 08:30（JST）",
    );
  });

  // **設定が無ければ直さない。** 勝手に見ている人の時計へ倒すと、どの時計の
  // 数字なのかが画面から決められなくなる。
  it("タイムゾーンが無ければ、UTC のまま印を添える", () => {
    expect(formatSystemDateTime("2026-08-26T03:33:05+00:00", null)).toBe(
      "2026年8月26日 03:33（UTC）",
    );
  });

  it("知らないゾーン名でも落ちない（UTC のまま出す）", () => {
    expect(formatSystemDateTime("2026-08-26T03:33:05+00:00", "Mars/Olympus")).toBe(
      "2026年8月26日 03:33（UTC）",
    );
  });

  it("読めない値は「不明」のまま", () => {
    expect(formatSystemDateTime("いつか", "Asia/Tokyo")).toBe("日時が不明");
  });
});

// **撮影日時は撮った土地の壁時計。直さない**（利用者の裁定、2026-08-28）。
// 数字はそのままで、どの時計のものかを印で言う。
describe("formatCapturedDateTime", () => {
  it("撮った土地の時刻はそのまま。印はその土地のゾーンから作る", () => {
    expect(formatCapturedDateTime("2026-08-26T12:33:05+09:00", "Asia/Tokyo", "Asia/Tokyo")).toBe(
      "2026年8月26日 12:33（JST）",
    );
  });

  // **海外で撮った 1 枚を JST へ直さない。** 直すと「現地で何時だったか」が
  // 読めなくなる（`force_offset` で復元した壁時計も同じ）。
  it("別の土地で撮ったものは、現地の時刻のまま出す", () => {
    expect(formatCapturedDateTime("2026-08-26T14:20:00+02:00", "Europe/Berlin", "Asia/Tokyo")).toBe(
      "2026年8月26日 14:20（GMT+2）",
    );
  });

  // **`timezone_policy: none` は `+00:00` で保存される**（カメラの壁時計に
  // UTC の札が貼られた形）。本当に UTC で撮ったものと区別できるのは
  // `captured_at_tz` が空かどうかだけなので、そこで既定のゾーンとみなす。
  it("ゾーンが決まっていない値は、既定のタイムゾーンとみなす", () => {
    expect(formatCapturedDateTime("2026-08-26T12:33:05+00:00", null, "Asia/Tokyo")).toBe(
      "2026年8月26日 12:33（JST）",
    );
  });

  it("ゾーンも既定も無ければ、値が持つオフセットで印を作る", () => {
    expect(formatCapturedDateTime("2026-08-26T12:33:05+09:00", null, null)).toBe(
      "2026年8月26日 12:33（GMT+9）",
    );
  });

  it("オフセットが 0 の値は UTC と書く", () => {
    expect(formatCapturedDateTime("2026-08-26T12:33:05Z", null, null)).toBe(
      "2026年8月26日 12:33（UTC）",
    );
  });

  it("30 分刻みのオフセットも書ける", () => {
    expect(formatCapturedDateTime("2026-08-26T12:33:05+05:30", null, null)).toBe(
      "2026年8月26日 12:33（GMT+5:30）",
    );
  });

  it("印を作れない値でも、日時は出す", () => {
    expect(formatCapturedDateTime("2026-08-26T12:33:05", null, null)).toBe("2026年8月26日 12:33");
  });

  it("読めない値は「不明」のまま", () => {
    expect(formatCapturedDateTime("いつか", null, "Asia/Tokyo")).toBe("日時が不明");
  });
});
