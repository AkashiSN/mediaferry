// 作業の履歴（§13）。いまどのファイルをどこまで書いたか、終わった作業には何が
// 起きたかを出す。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { emitJob, failStream, openStream } from "../../test/setup";
import { stubApi } from "../../test/api";
import { JobHistoryScreen } from "./JobHistory";

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("作業の履歴", () => {
  it("いまどのファイルをどこまで書いたかを出す", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: "2026-08-21T00:00:00+00:00",
            progress: {
              phase: "copy",
              rel_path: "DCIM/DJI_001/DJI_20260808125404_0002_D.MP4",
              file_index: 3,
              file_count: 29,
              bytes_done: 8 * 1024 ** 3,
              bytes_total: 16 * 1024 ** 3,
              bytes_done_all: 40 * 1024 ** 3,
              bytes_total_all: 120 * 1024 ** 3,
            },
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );

    const line = await screen.findByText(/コピー中/);
    expect(line.textContent).toContain("3/29 件");
    expect(line.textContent).toContain("DJI_20260808125404_0002_D.MP4");
    expect(line.textContent).toContain("50%");
    // 内部の種類名をそのまま出さない（JOB_TYPE_LABELS で日本語化）。
    expect(screen.getByText("取り込み")).toBeInTheDocument();
  });

  it("終わったジョブには進捗が無いので、状態だけ出す", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "merge",
            status: "succeeded",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: "2026-08-21T00:00:00+00:00",
            progress: null,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );

    expect(await screen.findByText("完了")).toBeInTheDocument();
    expect(screen.queryByText(/コピー中|つないでいます/)).toBeNull();
  });

  it("進捗が無い（終わった）ジョブには、届いた最後のイベントの文言を添える", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "merge",
            status: "succeeded",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: "2026-08-21T00:00:00+00:00",
            progress: null,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    await screen.findByText("完了");
    emitJob({
      job_id: "j1",
      seq: 1,
      level: "info",
      message: "継ぎ目を検証しています",
      data: null,
      at: "2026-08-21T00:00:00+00:00",
    });
    expect(await screen.findByText("継ぎ目を検証しています")).toBeInTheDocument();
  });

  it("終わった作業には、終わった日時を出す（設定の入口が約束している）", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "merge",
            status: "succeeded",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: "2026-08-21T00:00:00+00:00",
            finished_at: "2026-08-21T00:10:00+00:00",
            progress: null,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );

    // システム時刻（UTC）をそのまま出すと現地時刻に見えるので、印を添える。
    expect(await screen.findByText(/終わった日時: 2026年8月21日 00:10（UTC）/)).toBeInTheDocument();
  });

  it("走っている作業には、終わった日時を出さない", async () => {
    stubApi({
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: "2026-08-21T00:00:00+00:00",
            finished_at: null,
            progress: null,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );

    await screen.findByText("実行中");
    expect(screen.queryByText(/終わった日時/)).not.toBeInTheDocument();
  });

  it("キャンセルできる状態のジョブだけキャンセルボタンを出し、押すと叩く", async () => {
    const { calls } = stubApi({
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: "2026-08-21T00:00:00+00:00",
            progress: null,
          },
          {
            id: "j2",
            type: "merge",
            status: "succeeded",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: "2026-08-21T00:00:00+00:00",
            progress: null,
          },
          {
            id: "j3",
            type: "upload",
            status: "queued",
            created_at: "2026-08-21T00:00:00+00:00",
            started_at: null,
            progress: null,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );

    // 待機中（j3）と実行中（j1）の 2 件だけキャンセルできる。完了済み（j2）には出さない。
    const buttons = await screen.findAllByRole("button", { name: "中止する" });
    expect(buttons).toHaveLength(2);
    await userEvent.click(buttons[0]);
    await vi.waitFor(() =>
      expect(calls().some((c) => c.path === "/jobs/j1/cancel" && c.method === "POST")).toBe(true),
    );
  });

  it("中止に失敗したら、その旨を画面に出す", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        if (path === "/jobs/j1/cancel" && method === "POST") {
          return Promise.resolve(
            new Response(JSON.stringify({ error: { code: "internal", detail: "" } }), { status: 500 }),
          );
        }
        const body =
          path === "/jobs"
            ? {
                jobs: [
                  {
                    id: "j1",
                    type: "import",
                    status: "running",
                    created_at: "2026-08-21T00:00:00+00:00",
                    started_at: "2026-08-21T00:00:00+00:00",
                    progress: null,
                  },
                ],
              }
            : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "中止する" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("記録が無ければ、そう書く", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("作業の記録はまだありません。")).toBeInTheDocument();
  });

  it("見出しと領域の名は「作業の履歴」（内部の名前を出さない）", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    const { container } = render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    await screen.findByText("作業の記録はまだありません。");
    expect(screen.getByRole("heading", { level: 1, name: "作業の履歴" })).toBeInTheDocument();
    expect(container.querySelector('section[aria-label="作業の履歴"]')).not.toBeNull();
  });

  it("設定へ戻る導線を出す", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("link", { name: /設定へ/ })).toHaveAttribute("href", "/settings");
  });

  it("進捗のイベントが届いたら、一覧を取り直す", async () => {
    const { calls } = stubApi({ "/jobs": { jobs: [] } });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    await screen.findByText("作業の記録はまだありません。");
    const before = calls().filter((c) => c.path === "/jobs" && c.method === "GET").length;
    emitJob({ job_id: "j1", seq: 1, level: "info", message: "更新", data: null, at: "" });
    await waitFor(
      () =>
        expect(calls().filter((c) => c.path === "/jobs" && c.method === "GET").length).toBeGreaterThan(
          before,
        ),
      { timeout: 2000 },
    );
  });

  it("開いた直後は、接続が切れているとは出さない（まだ繋がったことが無いだけなので）", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    await screen.findByText("作業の記録はまだありません。");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("接続が切れたら、そう出す", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    failStream();
    expect(await screen.findByRole("status")).toHaveTextContent("進捗の接続が切れています");
  });

  it("つながったら、表示を消す", async () => {
    stubApi({ "/jobs": { jobs: [] } });
    render(
      <MemoryRouter>
        <JobHistoryScreen />
      </MemoryRouter>,
    );
    failStream();
    await screen.findByRole("status");
    openStream();
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it(
    "実行中のジョブがある間は、進捗を取り直し続ける",
    async () => {
      const { calls } = stubApi({
        "/jobs": {
          jobs: [
            {
              id: "j1",
              type: "import",
              status: "running",
              created_at: "2026-08-21T00:00:00+00:00",
              started_at: "2026-08-21T00:00:00+00:00",
              progress: null,
            },
          ],
        },
      });
      render(
        <MemoryRouter>
          <JobHistoryScreen />
        </MemoryRouter>,
      );
      await screen.findByText("実行中");
      const before = calls().filter((c) => c.path === "/jobs" && c.method === "GET").length;
      await waitFor(
        () =>
          expect(calls().filter((c) => c.path === "/jobs" && c.method === "GET").length).toBeGreaterThan(
            before,
          ),
        { timeout: 3000 },
      );
    },
    6000,
  );

  it(
    "終わっているジョブしか無ければ、取り直さない",
    async () => {
      const { calls } = stubApi({
        "/jobs": {
          jobs: [
            {
              id: "j1",
              type: "merge",
              status: "succeeded",
              created_at: "2026-08-21T00:00:00+00:00",
              started_at: "2026-08-21T00:00:00+00:00",
              progress: null,
            },
          ],
        },
      });
      render(
        <MemoryRouter>
          <JobHistoryScreen />
        </MemoryRouter>,
      );
      await screen.findByText("完了");
      const before = calls().filter((c) => c.path === "/jobs" && c.method === "GET").length;
      await new Promise((resolve) => setTimeout(resolve, 2200));
      expect(calls().filter((c) => c.path === "/jobs" && c.method === "GET").length).toBe(before);
    },
    6000,
  );
});
