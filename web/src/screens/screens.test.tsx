// **不可逆な操作は、呼び出し側それぞれで確認が出る**（§13、計画レビューの指摘）。
//
// 型（`Confirmation` の直和）だけでは「その画面で実際に出た」ことの証明にならない。
// 画面ごとに、押しても**確認の前には API を叩かない**ことを見る。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalsScreen } from "./Approvals";
import { DestinationsScreen } from "./Destinations";
import { LibraryScreen } from "./Library";
import { MergesScreen } from "./Merges";

type Handler = (path: string, init?: RequestInit) => unknown;

let calls: { path: string; method: string }[] = [];

function stubApi(routes: Record<string, unknown>, onCall?: Handler) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const path = input.replace(/^\/api/, "");
      calls.push({ path, method: init?.method ?? "GET" });
      onCall?.(path, init);
      const key = Object.keys(routes).find((candidate) => path.startsWith(candidate));
      const body = key === undefined ? {} : routes[key];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    }),
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ライブラリの送信", () => {
  const media = {
    media: [{ id: "m1", rel_path: "library/a.MP4", kind: "video", captured_at: "2026-08-17", size_bytes: 1024 }],
    total: 1,
    page: 1,
    page_size: 50,
  };
  const destinations = { destinations: [{ id: "d1", name: "home", enabled: true }] };

  it("確認が出るまで送信しない", async () => {
    stubApi({ "/media": media, "/destinations": destinations });
    render(
      <MemoryRouter>
        <LibraryScreen />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByLabelText("library/a.MP4 を選ぶ"));
    const checkboxes = screen.getAllByRole("checkbox");
    await userEvent.click(checkboxes[checkboxes.length - 1]); // 宛先
    const send = await screen.findByRole("button", { name: /送信する/ });
    await userEvent.click(send);

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("送信は 2 段階（組を作ってから宛先ごとに開始する）", async () => {
    stubApi({ "/media": media, "/destinations": destinations, "/uploads": { pairs: [] } });
    render(
      <MemoryRouter>
        <LibraryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByLabelText("library/a.MP4 を選ぶ"));
    const checkboxes = screen.getAllByRole("checkbox");
    await userEvent.click(checkboxes[checkboxes.length - 1]);
    await userEvent.click(await screen.findByRole("button", { name: /送信する/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() => {
      expect(calls.filter((call) => call.path === "/uploads" && call.method === "POST")).toHaveLength(1);
      expect(
        calls.filter((call) => call.path === "/destinations/d1/upload" && call.method === "POST"),
      ).toHaveLength(1);
    });
  });
});

describe("転送先の退役", () => {
  it("確認が出るまで退役させない", async () => {
    stubApi({
      "/destinations": {
        destinations: [
          { id: "d1", name: "home", enabled: true, base_url: "http://x", public_url: null, same_library_as: [] },
        ],
      },
    });
    render(<DestinationsScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "退役させる" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(calls.some((call) => call.path.includes("archive"))).toBe(false);
  });
});

describe("結合グループの破棄", () => {
  it("確認が出るまで破棄しない", async () => {
    stubApi({
      "/merge-groups": {
        groups: [
          {
            id: "g1",
            status: "merged",
            detected_by: "auto",
            input_digest: "d",
            verification: null,
            superseded_by_id: null,
            members: [{ media_file_id: "m1", rel_path: "library/a.MP4", size_bytes: 1, gap_seconds: null }],
          },
        ],
      },
    });
    render(<MergesScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "破棄する" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
  });
});

describe("日時の承認", () => {
  const records = {
    records: [
      {
        id: "u1",
        destination_id: "d1",
        media_file_id: "m1",
        origin: "pre_existing",
        remote_current: "2020-01-01T00:00:00+00:00",
        proposed: "2026-08-17T14:30:00+09:00",
        remote_checked_at: "2026-08-18T00:00:00+00:00",
        identical: false,
      },
    ],
  };

  it("確認が出るまでリモートを書き換えない", async () => {
    stubApi({ "/uploads": records });
    render(<ApprovalsScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "承認する" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(calls.some((call) => call.path.includes("approve"))).toBe(false);
  });

  it("変更が無い行では承認を促さない", async () => {
    stubApi({
      "/uploads": {
        records: [{ ...records.records[0], identical: true, remote_current: records.records[0].proposed }],
      },
    });
    render(<ApprovalsScreen />);

    expect(await screen.findByText("変更なし")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "承認する" })).toBeNull();
  });

  it("読めなかった現在値は空欄にしない", async () => {
    stubApi({ "/uploads": { records: [{ ...records.records[0], remote_current: null }] } });
    render(<ApprovalsScreen />);

    expect(await screen.findByText("（不明）")).toBeInTheDocument();
  });
});
