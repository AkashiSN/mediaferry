// 読み取りの共通形（`useQuery`）。**取り直しの間、画面を空にしない。**

import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useQuery } from "./hooks";

afterEach(() => vi.restoreAllMocks());

function Probe() {
  const query = useQuery<{ n: number }>("/thing");
  return (
    <p>
      <span data-testid="state">{query.loading ? "読み込み中" : "済み"}</span>
      <span data-testid="value">{query.data === null ? "なし" : query.data.n}</span>
      <button type="button" onClick={query.reload}>
        取り直す
      </button>
    </p>
  );
}

describe("useQuery", () => {
  it("最初の 1 回は読み込み中を出す", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({ n: 1 }), { status: 200 }))),
    );
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("読み込み中"));
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("1"));
  });

  // 取り込み中は進捗のたびに取り直す（`useReloadOnEvents`）。そのたびに
  // `loading` を立てると、**ホームの「やること」が 1 秒おきに「読み込み中…」へ
  // 化ける**。手元に出せる値があるなら、それを出したまま取り直す。
  it("取り直しの間は、前の値を出したままにする", async () => {
    let release!: () => void;
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls > 1) {
          await new Promise<void>((resolve) => {
            release = resolve;
          });
        }
        return new Response(JSON.stringify({ n: calls }), { status: 200 });
      }),
    );
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("1"));

    act(() => screen.getByRole("button", { name: "取り直す" }).click());
    await waitFor(() => expect(calls).toBe(2));

    expect(screen.getByTestId("state")).toHaveTextContent("済み");
    expect(screen.getByTestId("value")).toHaveTextContent("1");
    act(() => release());
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("2"));
  });
});
