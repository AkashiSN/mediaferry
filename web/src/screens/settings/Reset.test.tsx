// リセット（§13）。**段ごとに、何が消えて何が残るかを確認で言う。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { ResetScreen } from "./Reset";

function renderReset() {
  const { calls } = stubApi({ "POST /reset": { status: "ok", removed: {} } });
  render(
    <MemoryRouter>
      <ResetScreen />
    </MemoryRouter>,
  );
  return { calls };
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("リセット", () => {
  it("段を浅い順に並べる（積み上げだと分かるように）", async () => {
    renderReset();
    const buttons = await screen.findAllByRole("button", { name: /消す$/ });
    expect(buttons.map((button) => button.textContent)).toEqual([
      "作業の記録を消す",
      "送信の記録を消す",
      "取り込んだファイルを消す",
      "すべて消す",
    ]);
  });

  it("押しただけでは消さない（確認を経てから）", async () => {
    const { calls } = renderReset();
    await userEvent.click(await screen.findByRole("button", { name: "作業の記録を消す" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(calls().some((call) => call.method === "POST")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((call) => call.path === "/reset" && call.method === "POST")).toBe(true),
    );
  });

  it("送信の記録の確認は、Immich が無傷なことと、戻らない代償の両方を書く", async () => {
    // **片方だけだと読み違える。** 「Immich からは何も消えません」だけなら代償が
    // 無いように読め、代償だけなら Immich の写真が消えると読める。
    renderReset();
    await userEvent.click(await screen.findByRole("button", { name: "送信の記録を消す" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/Immich からは何も消えません/);
    expect(dialog).toHaveTextContent(/戻せません/);
    expect(dialog).toHaveTextContent(/日時/);
  });

  it("取り込んだファイルの確認は、カードから取り込み直せることを書く", async () => {
    renderReset();
    await userEvent.click(await screen.findByRole("button", { name: "取り込んだファイルを消す" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(/カードに元があれば/);
  });

  it("すべての確認は、残るものも書く（設定は消えない）", async () => {
    renderReset();
    await userEvent.click(await screen.findByRole("button", { name: "すべて消す" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/送り先とカメラの種類は残ります/);
  });

  it("走っている作業があって断られたら、理由を出す", async () => {
    // `stubApi` は 200 しか返せないので、この 1 本だけ自前で立てる。
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "job_in_flight",
                detail: "走っている作業があるので、いまはリセットできない",
                meta: {},
              },
            }),
            { status: 409 },
          ),
        ),
      ),
    );
    render(
      <MemoryRouter>
        <ResetScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "作業の記録を消す" }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    // **generic な「いまの状態ではできません」で済ませない。** 何を待てばよいのかが
    // 読めないと、押し直すしかなくなる。
    expect(await screen.findByRole("alert")).toHaveTextContent(/走っている作業があります/);
  });
});
