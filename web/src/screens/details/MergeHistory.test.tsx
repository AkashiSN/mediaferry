// つないだ動画の記録（§13）。破棄した組み合わせと、使っていない出力は操作できない記録
// なので、ここでは一覧と削除の確認だけを見る。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { MergeHistoryScreen } from "./MergeHistory";

const EMPTY_ROUTES = {
  "/merge-groups?status=skipped": { groups: [] },
  "/media/stale-derived": { stale: [] },
};

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("つないだ動画の記録", () => {
  it("使っていない出力を、ファイルとして消せる", async () => {
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": {
        stale: [{ id: "s1", rel_path: "derived/old.MP4", size_bytes: 1024, captured_at: "", reason: "superseded" }],
      },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/derived\/old\.MP4/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /このファイルを消す/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("元になったファイルは残ります");
    expect(calls().some((c) => c.method === "DELETE")).toBe(false);
  });

  it("使っていない出力が無ければ、その節を出さない", async () => {
    stubApi(EMPTY_ROUTES);
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("破棄した組み合わせ")).toBeInTheDocument());
    expect(screen.queryByText(/使っていない出力/)).not.toBeInTheDocument();
  });

  it("破棄した組み合わせを一覧で見せ、確認を経てから記録を消す", async () => {
    const { calls } = stubApi({
      ...EMPTY_ROUTES,
      "/merge-groups?status=skipped": {
        groups: [
          {
            id: "old1",
            status: "skipped",
            detected_by: "auto",
            input_digest: "d2",
            verification: null,
            superseded_by_id: null,
            output: null,
            members: [
              {
                position: 0,
                media_file_id: "m1",
                rel_path: "library/a.MP4",
                size_bytes: 1,
                duration_seconds: null,
                captured_at: "",
              },
            ],
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("library/a.MP4")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "消す" }));
    // 消す対象が分かるように、その組の 1 つ目のファイル名を確認に出す。
    expect(screen.getByRole("dialog")).toHaveTextContent("library/a.MP4");
    expect(screen.getByRole("dialog")).toHaveTextContent("もう一度「候補を検出する」を");
    expect(screen.getByRole("dialog")).toHaveTextContent("組み合わせがまた出ることがあります");
    expect(calls().some((c) => c.method === "DELETE")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/old1" && c.method === "DELETE")).toBe(true),
    );
  });

  it("破棄した組み合わせが無ければ、そう書く", async () => {
    stubApi(EMPTY_ROUTES);
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("破棄した組み合わせはありません。")).toBeInTheDocument();
  });

  it("破棄した組の出力は「破棄した組」と理由を出す（superseded 以外）", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": {
        stale: [{ id: "s2", rel_path: "derived/discarded.MP4", size_bytes: 1, captured_at: "", reason: "discarded" }],
      },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/1 B ・破棄した組$/)).toBeInTheDocument();
    expect(screen.queryByText(/組み直しで置き換わった/)).not.toBeInTheDocument();
  });

  it("確認したあと、使っていない出力をそのファイルの DELETE で消す", async () => {
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": {
        stale: [{ id: "s3", rel_path: "derived/old3.MP4", size_bytes: 1, captured_at: "", reason: "superseded" }],
      },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /このファイルを消す/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/media/s3" && c.method === "DELETE")).toBe(true),
    );
  });

  it("消している間は、どちらの「消す」ボタンも押せない（二重送信を防ぐ）", async () => {
    let open: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        if (method === "DELETE") {
          await gate;
          return new Response(JSON.stringify({}), { status: 200 });
        }
        const body =
          path === "/merge-groups?status=skipped"
            ? {
                groups: [
                  {
                    id: "old1",
                    status: "skipped",
                    detected_by: "auto",
                    input_digest: "d",
                    verification: null,
                    superseded_by_id: null,
                    output: null,
                    members: [
                      {
                        position: 0,
                        media_file_id: "m1",
                        rel_path: "library/a.MP4",
                        size_bytes: 1,
                        duration_seconds: null,
                        captured_at: "",
                      },
                    ],
                  },
                ],
              }
            : path === "/media/stale-derived"
              ? {
                  stale: [
                    { id: "s1", rel_path: "derived/old.MP4", size_bytes: 1, captured_at: "", reason: "superseded" },
                  ],
                }
              : {};
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "消す" }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "消す" })).toBeDisabled());
    expect(screen.getByRole("button", { name: /このファイルを消す/ })).toBeDisabled();

    open();
    await waitFor(() => expect(screen.getByRole("button", { name: "消す" })).toBeEnabled());
  });

  it("消すのに失敗したら、その旨を画面に出す", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        if (path === "/media/s1" && method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ error: { code: "internal", detail: "" } }), { status: 500 }),
          );
        }
        const body =
          path === "/merge-groups?status=skipped"
            ? { groups: [] }
            : path === "/media/stale-derived"
              ? {
                  stale: [
                    { id: "s1", rel_path: "derived/old.MP4", size_bytes: 1, captured_at: "", reason: "superseded" },
                  ],
                }
              : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /このファイルを消す/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("使っていない出力の節に、飛び先の錨（#stale）を置く（裁定 42）", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": {
        stale: [{ id: "s1", rel_path: "derived/old.MP4", size_bytes: 1024, captured_at: "", reason: "superseded" }],
      },
    });
    const { container } = render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/derived\/old\.MP4/)).toBeInTheDocument());
    expect(container.querySelector("#stale")).not.toBeNull();
  });
});
