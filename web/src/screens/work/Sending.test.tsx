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

  // レビュー指摘（Important #3）: 「やること」は残っている仕事しか出さないので、
  // 送り終えると空になる。結果があるのは 設定 › 詳しい情報 › 作業の履歴。
  it("結果を見る先は、実在する画面を指す", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    renderSending({ jobIds: ["j1"] });
    const link = screen.getByRole("link", { name: "作業の履歴" });
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

  it("「送るのをやめる」はキャンセルを叩く", async () => {
    const { calls } = stubApi({
      "/jobs": {
        jobs: [{ id: "j1", type: "upload", status: "running", created_at: "2026-08-18T00:00:00+00:00" }],
      },
    });
    renderSending({ jobIds: ["j1"] });
    await userEvent.click(await screen.findByRole("button", { name: "送るのをやめる" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/jobs/j1/cancel" && c.method === "POST")).toBe(true),
    );
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

  // レビュー指摘（Minor→Important #5）: 「閉じても送信は続く」と書いてある以上、
  // 「閉じる」を押してもジョブを止めてはいけない。
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
