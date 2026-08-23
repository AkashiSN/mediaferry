// 送信中（§13）。**閉じても送信は続く。断られた組と、開始に失敗した宛先を隠さない。**
//
// ブリーフの Files には `Sending.test.tsx` は挙がっていないが、この画面が守るべき
// こと（閉じる手段・進捗の 2 秒間隔の取り直し・キャンセル）は無視できないので補う。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { SendingScreen } from "./Sending";

function renderSending(state?: { jobIds?: string[]; note?: string | null }) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/sending", state }]}>
      <SendingScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("送信中", () => {
  it("閉じても送信は続くと書き、閉じる手段がある", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    renderSending({ jobIds: ["j1"] });
    expect(screen.getByText(/この画面を閉じても送信は続きます/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "閉じる" })).toBeInTheDocument();
  });

  // 「やること」は残っている仕事しか出さないので、送り終えると空になる。結果が
  // あるのは 設定 › 詳しい情報 › 作業の履歴で、そこへの行き先を**文の途中の
  // リンクではなくボタンで**置く（§13「押せる領域は 44px 以上」）。
  it("結果を見る先は、実在する画面を指す", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    renderSending({ jobIds: ["j1"] });
    const link = screen.getByRole("link", { name: "作業の履歴を見る" });
    expect(link).toHaveAttribute("href", "/settings/jobs");
    expect(screen.queryByText(/「やること」から結果/)).toBeNull();
  });

  it("断られた組と、開始に失敗した宛先を隠さない", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    renderSending({
      jobIds: ["j1"],
      note: "1 組を作り、1 宛先で送信を始めました。送れない組が 1 件ありました（結合中）。",
    });
    expect(await screen.findByText(/送れない組が 1 件/)).toBeInTheDocument();
  });

  it("渡されたジョブだけを追い、状態を出す", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          { id: "j1", type: "upload", status: "running", created_at: "2026-08-18T00:00:00+00:00" },
          { id: "j2", type: "upload", status: "queued", created_at: "2026-08-18T00:00:00+00:00" },
        ],
      },
    });
    renderSending({ jobIds: ["j1"] });
    expect(await screen.findByText("実行中")).toBeInTheDocument();
    expect(screen.queryByText("待機中")).toBeNull();
  });

  it("jobIds が無ければ、進行中の送信ジョブをすべて出す", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          { id: "j1", type: "upload", status: "running", created_at: "2026-08-18T00:00:00+00:00" },
          { id: "j2", type: "import", status: "running", created_at: "2026-08-18T00:00:00+00:00" },
        ],
      },
    });
    renderSending();
    expect(await screen.findByText("実行中")).toBeInTheDocument();
    // import ジョブは対象外。「実行中」表示が 1 つだけであること。
    await waitFor(() => expect(screen.getAllByText("実行中")).toHaveLength(1));
  });

  // `GET /jobs` は状態を問わず直近 50 件を返す。type だけで絞ると、**終わった
  // 送信が「送っています」の下に並ぶ**（何も動いていないのに、完了・失敗の札が
  // 出る）。router の state 無しで来る経路（戻る・再読み込み・1 件も始まらな
  // かった送信）は珍しくない。
  it("jobIds が無いとき、終わった送信は出さない", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          { id: "j1", type: "upload", status: "succeeded", created_at: "2026-08-18T00:00:00+00:00" },
          { id: "j2", type: "upload", status: "failed", created_at: "2026-08-18T00:00:00+00:00" },
          { id: "j3", type: "upload", status: "cancelled", created_at: "2026-08-18T00:00:00+00:00" },
        ],
      },
    });
    renderSending();
    expect(await screen.findByText("いま送っているものはありません。")).toBeInTheDocument();
    expect(screen.queryByText("完了")).toBeNull();
    expect(screen.queryByText("失敗")).toBeNull();
  });

  it("「送るのをやめる」はキャンセルを叩く", async () => {
    const { calls } = stubApi({
      "/jobs": {
        jobs: [{ id: "j1", type: "upload", status: "running", created_at: "2026-08-18T00:00:00+00:00" }],
      },
      "/jobs/j1/cancel": { status: "ok" },
    });
    renderSending({ jobIds: ["j1"] });
    await userEvent.click(await screen.findByRole("button", { name: "送るのをやめる" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/jobs/j1/cancel" && c.method === "POST")).toBe(true),
    );
    // **止めた後は一覧を引き直す。** 叩いたところまでしか見ないと、画面が
    // 「実行中」のままでも気付けない。
    await waitFor(() =>
      expect(calls().filter((c) => c.path === "/jobs").length).toBeGreaterThan(1),
    );
  });

  // **二重に止めない。** 2 度目の要求は、1 度目で状態が動いた後に届くので
  // `409` で弾かれ、押した人にはバナーだけが残る。
  it("止めている間は、もう一度押せない", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const sent: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/jobs/j1/cancel") {
          sent.push(path);
          await held;
          return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
        }
        return new Response(
          JSON.stringify({
            jobs: [
              { id: "j1", type: "upload", status: "running", created_at: "2026-08-18T00:00:00+00:00" },
            ],
          }),
          { status: 200 },
        );
      }),
    );
    renderSending({ jobIds: ["j1"] });
    const button = await screen.findByRole("button", { name: "送るのをやめる" });
    await userEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(sent).toHaveLength(1);
    release();
  });

  it("終わっているジョブには「送るのをやめる」を出さない", async () => {
    stubApi({
      "/jobs": {
        jobs: [{ id: "j1", type: "upload", status: "succeeded", created_at: "2026-08-18T00:00:00+00:00" }],
      },
    });
    renderSending({ jobIds: ["j1"] });
    expect(await screen.findByText("完了")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "送るのをやめる" })).toBeNull();
  });

  // **「閉じても送信は続く」と書いてある以上、「閉じる」で止めてはいけない**（§13）。
  it("「閉じる」を押しても、キャンセルは呼ばれない", async () => {
    const { calls } = stubApi({
      "/jobs": {
        jobs: [{ id: "j1", type: "upload", status: "running", created_at: "2026-08-18T00:00:00+00:00" }],
      },
    });
    renderSending({ jobIds: ["j1"] });
    await userEvent.click(await screen.findByRole("button", { name: "閉じる" }));
    expect(calls().some((c) => c.path === "/jobs/j1/cancel")).toBe(false);
  });
});
