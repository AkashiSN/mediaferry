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
import { LibraryScreen, summarise } from "./Library";
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
    stubApi({
      "/media": media,
      "/destinations": destinations,
      // **組ごとの結果を返す。** 受け付けられた宛先だけ送信を始める。
      "/uploads": {
        pairs: [
          {
            media_file_id: "m1",
            destination_id: "d1",
            result: "created",
            upload_record_id: "u1",
            reason: null,
          },
        ],
      },
    });
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

describe("送信の結果を隠さない", () => {
  it("断られた組と、開始に失敗した宛先を文に出す", () => {
    expect(summarise(4, [{ reason: "結合中のグループの構成ファイル" }], ["backup"], 1)).toContain(
      "送れない組が 1 件",
    );
    expect(summarise(4, [{ reason: "結合中のグループの構成ファイル" }], ["backup"], 1)).toContain(
      "backup",
    );
  });

  it("何も問題が無ければ、余計なことを言わない", () => {
    const message = summarise(2, [], [], 2);
    expect(message).toBe("2 組を作り、2 宛先で送信を始めました。");
  });
});

describe("選んだものの合計", () => {
  it("**絞り込みで隠れても、選択と合計は保つ**", async () => {
    const first = {
      media: [
        { id: "m1", rel_path: "library/big.MP4", kind: "video", captured_at: "2026-08-17", size_bytes: 30 * 1024 ** 3 },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    };
    const second = {
      media: [
        { id: "m2", rel_path: "library/small.MP4", kind: "video", captured_at: "2026-08-18", size_bytes: 1024 ** 2 },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    };
    let page = first;
    stubApi({ "/destinations": { destinations: [{ id: "d1", name: "home", enabled: true }] } });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path.startsWith("/media")) {
          return Promise.resolve(new Response(JSON.stringify(page), { status: 200 }));
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({ destinations: [{ id: "d1", name: "home", enabled: true }] }),
            { status: 200 },
          ),
        );
      }),
    );

    render(
      <MemoryRouter>
        <LibraryScreen />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByLabelText("library/big.MP4 を選ぶ"));
    // 絞り込みを変えて、選んだ行を隠す（条件が変わらないと取り直さない）。
    page = second;
    await userEvent.type(screen.getByLabelText("名前"), "small");
    await userEvent.click(screen.getByRole("button", { name: "絞り込む" }));
    await userEvent.click(await screen.findByLabelText("library/small.MP4 を選ぶ"));
    await userEvent.click(screen.getByRole("checkbox", { name: "home" }));
    await userEvent.click(screen.getByRole("button", { name: /送信する/ }));

    // 隠した 30 GiB を数え落とさない。
    expect(await screen.findByText(/合計 30 GiB/)).toBeInTheDocument();
    expect(screen.getByText("2 件")).toBeInTheDocument();
  });
});
