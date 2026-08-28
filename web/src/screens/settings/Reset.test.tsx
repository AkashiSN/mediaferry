// リセット（§13）。**段ごとに、何が消えて何が残るかを確認で言う。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { ResetScreen } from "./Reset";

function renderReset(removed: Record<string, number> = {}) {
  const { calls } = stubApi({ "POST /reset": { status: "ok", removed } });
  render(
    <MemoryRouter>
      <ResetScreen />
    </MemoryRouter>,
  );
  return { calls };
}

/** 段を 1 つ実行し（確認 → 実行する）、結果の帯が出るまで待つ。 */
async function runStage(buttonName: string) {
  await userEvent.click(await screen.findByRole("button", { name: buttonName }));
  await userEvent.click(screen.getByRole("button", { name: "実行する" }));
  return screen.findByRole("status");
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

  it("消した件数を、訳と件数で出す", async () => {
    renderReset({ job: 12, job_event: 340 });
    const banner = await runStage("作業の記録を消す");
    expect(banner).toHaveTextContent(/12\s*件/);
    expect(banner).toHaveTextContent(/340\s*件/);
    // 内部の表の名前をそのまま出さない（§13）。
    expect(banner).not.toHaveTextContent("job_event");
  });

  it("0 件のものは書かない", async () => {
    renderReset({ job: 12, job_event: 0 });
    const banner = await runStage("作業の記録を消す");
    expect(banner).toHaveTextContent(/12\s*件/);
    expect(banner).not.toHaveTextContent(/0\s*件/);
  });

  it("全部 0 件なら、件数の羅列ではなく「消すものはありませんでした。」と書く", async () => {
    renderReset({ job: 0, job_event: 0 });
    const banner = await runStage("作業の記録を消す");
    expect(banner).toHaveTextContent("消すものはありませんでした。");
  });

  it("訳の無いキーは黙って落とす（内部名を画面に出さない）", async () => {
    renderReset({ unknown_table: 3, job: 1 });
    const banner = await runStage("作業の記録を消す");
    expect(banner).not.toHaveTextContent("unknown_table");
    expect(banner).toHaveTextContent(/1\s*件/);
  });

  it("結果の帯は × で閉じられる", async () => {
    renderReset({ job: 5 });
    await runStage("作業の記録を消す");
    await userEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
