// 作業の札（§13）。**押した操作の名前で出す。**
//
// `upload` 型は `params.mode` で 3 つの別の仕事を兼ねている（送る・再確認する・
// 日時の承認）ので、`type` だけでは 3 つとも「送信」になる。

import { render, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { JobCard } from "./JobCard";
import type { Job } from "./JobProgress";

function job(extra: Partial<Job>): Job {
  return {
    id: "j1",
    type: "upload",
    status: "succeeded",
    created_at: "2026-08-31T00:00:00Z",
    ...extra,
  };
}

/** **1 件描いて、題だけを読んで畳む。** 1 つのテストで何度も呼ぶので、
 * 畳まないと前の描画が残り、狙いと違う原因（題が 2 つある）で落ちる。 */
function title(extra: Partial<Job>): string {
  const view = render(<JobCard job={job(extra)} rate={null} />);
  const text = within(view.container).getByRole("heading").textContent ?? "";
  view.unmount();
  return text;
}

describe("作業の札", () => {
  it("送信は「送信」", () => {
    expect(title({})).toBe("送信");
  });

  it("再確認を「送信」と呼ばない", () => {
    expect(title({ mode: "recheck" })).toBe("再確認");
  });

  it("日時の承認を「送信」と呼ばない", () => {
    expect(title({ mode: "approve" })).toBe("日時の承認");
  });

  it("送信は mode を持つ（保険ではなく、名指しで「送信」と出す）", () => {
    // **送る経路は `mode: "send"` を積む**（`api/routes_destinations.py`）。
    // 未知の値へ落ちる保険が拾うのに任せると、mode が増えたとき黙って
    // 「送信」と出る。
    expect(title({ mode: "send" })).toBe("送信");
  });

  it("知らない mode は、その種別の札に落ちる（**札を空にしない**）", () => {
    expect(title({ mode: "wat" })).toBe("送信");
  });

  it("mode を持たない種別は、これまでどおり種別の札で出る", () => {
    expect(title({ type: "import", mode: null })).toBe("取り込み");
    expect(title({ type: "scan" })).toBe("スキャン");
  });

  it("知らない種別は、そのまま出す（黙って消さない）", () => {
    expect(title({ type: "weird" })).toBe("weird");
  });

  it("mode が読めない形でも落ちない（**古い形の行が混ざる**）", () => {
    // 履歴は過去の行を出す画面なので、`mode` が無い行・null の行・
    // 文字列でない行が混ざりうる。**一覧ごと落とさない。**
    expect(title({ mode: null })).toBe("送信");
    expect(title({ mode: undefined })).toBe("送信");
    expect(title({ mode: 7 as unknown as string })).toBe("送信");
  });
});
