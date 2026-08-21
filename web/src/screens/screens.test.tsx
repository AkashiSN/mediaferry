// **不可逆な操作は、呼び出し側それぞれで確認が出る**（§13、計画レビューの指摘）。
//
// 型（`Confirmation` の直和）だけでは「その画面で実際に出た」ことの証明にならない。
// 画面ごとに、押しても**確認の前には API を叩かない**ことを見る。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalsScreen } from "./Approvals";
import { LibraryScreen } from "./Library";
import { MergesScreen } from "./Merges";
import { DashboardScreen } from "./Dashboard";
import { JobsScreen } from "./Jobs";
import { stubApi } from "../test/api";

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
    const api = stubApi({ "/media": media, "/destinations": destinations });
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
    expect(api.calls().some((call) => call.method === "POST")).toBe(false);
  });

  it("送信は 2 段階（組を作ってから宛先ごとに開始する）", async () => {
    const api = stubApi({
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
      const calls = api.calls();
      expect(calls.filter((call) => call.path === "/uploads" && call.method === "POST")).toHaveLength(1);
      expect(
        calls.filter((call) => call.path === "/destinations/d1/upload" && call.method === "POST"),
      ).toHaveLength(1);
    });
  });
});

describe("破棄した組み合わせ", () => {
  it("既定の一覧には出さず、開いたときだけ見せる", async () => {
    const member = { media_file_id: "m1", rel_path: "library/a.MP4", size_bytes: 1, gap_seconds: null };
    stubApi({
      "/merge-groups?status=skipped": {
        groups: [
          {
            id: "old",
            status: "skipped",
            detected_by: "auto",
            input_digest: "d2",
            verification: null,
            superseded_by_id: null,
            members: [member],
          },
        ],
      },
      "/merge-groups": { groups: [] },
    });
    render(<MergesScreen />);

    const summary = await screen.findByText("破棄した組み合わせ（1 件）");
    // 畳んだ状態で開ける。中身は記録なので、操作のボタンは出さない。
    await userEvent.click(summary);
    expect(await screen.findByText("library/a.MP4")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "結合する" })).toBeNull();
    expect(screen.queryByRole("button", { name: "破棄する" })).toBeNull();
  });
});

describe("結合グループの破棄", () => {
  it("確認が出るまで破棄しない", async () => {
    const api = stubApi({
      // **前方一致なので、細かい方を先に置く**（`/merge-groups` が先だと履歴の
      // 問い合わせにも同じ本文が返り、同じグループが 2 度描かれる）。
      "/merge-groups?status=skipped": { groups: [] },
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
    expect(api.calls().some((call) => call.method === "PATCH")).toBe(false);
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
    const api = stubApi({ "/uploads": records });
    render(<ApprovalsScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "承認する" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls().some((call) => call.path.includes("approve"))).toBe(false);
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

// `summarise` の単体テストは `work/Send.test.tsx` にある（Task 7 で `Library.tsx` から
// `work/Send.tsx` へ移した）。

// 「選んだものは絞り込みで隠れても合計を保つ」は `Photos.test.tsx` が写真の
// グリッドに対して検証する。旧ライブラリの表（行のチェックボックス・「名前」の
// テキスト絞り込み）とは形が違うので、ここには持たない。


describe("スタックの結果（§9.11）", () => {
  it("ダッシュボードがスタックの件数を出す", async () => {
    stubApi({
      "/dashboard": {
        media_total: 1,
        destinations: [
          {
            destination_id: "d1",
            name: "home",
            enabled: true,
            complete: 3,
            failed: 0,
            awaiting_approval: 0,
            pending: 0,
            unsent: 0,
            stacked: 2,
            stack_skipped: 1,
          },
        ],
        running_jobs: 0,
        recent_imports: [],
        orphans: 0,
        missing: 0,
        warnings: [],
      },
      "/devices": { volumes: [] },
    });

    render(
      <MemoryRouter>
        <DashboardScreen />
      </MemoryRouter>,
    );

    const row = await screen.findByRole("row", { name: /home/ });
    expect(row).toHaveTextContent("2");
    expect(row).toHaveTextContent("1");
    expect(await screen.findByRole("columnheader", { name: "スタック" })).toBeInTheDocument();
  });
});


describe("ジョブの進捗", () => {
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
    render(<JobsScreen />);

    const line = await screen.findByText(/コピー中/);
    expect(line.textContent).toContain("3/29 件");
    expect(line.textContent).toContain("DJI_20260808125404_0002_D.MP4");
    expect(line.textContent).toContain("50%");
  });

  it("終わったジョブには進捗が無いので、最後のイベントを出す", async () => {
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
    render(<JobsScreen />);

    expect(await screen.findByText("完了")).toBeInTheDocument();
    expect(screen.queryByText(/コピー中|結合中/)).toBeNull();
  });
});


describe("破棄の記録を消す", () => {
  it("確認が出るまで消さない", async () => {
    const member = { media_file_id: "m1", rel_path: "library/a.MP4", size_bytes: 1, gap_seconds: null };
    const api = stubApi({
      "/merge-groups?status=skipped": {
        groups: [
          {
            id: "old",
            status: "skipped",
            detected_by: "auto",
            input_digest: "d2",
            verification: null,
            superseded_by_id: null,
            members: [member],
          },
        ],
      },
      "/merge-groups": { groups: [] },
    });
    render(<MergesScreen />);

    await userEvent.click(await screen.findByText("破棄した組み合わせ（1 件）"));
    await userEvent.click(await screen.findByRole("button", { name: "消す" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls().some((call) => call.method === "DELETE")).toBe(false);
  });
});

describe("同じ構成でやり直す", () => {
  it("結合済みのグループにだけ出る", async () => {
    const member = { media_file_id: "m1", rel_path: "library/a.MP4", size_bytes: 1, gap_seconds: null };
    const api = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": {
        groups: [
          {
            id: "g1",
            status: "merged",
            detected_by: "auto",
            input_digest: "d",
            verification: null,
            superseded_by_id: null,
            members: [member],
          },
        ],
      },
    });
    render(<MergesScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "同じ構成でやり直す" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls().some((call) => call.method === "PATCH")).toBe(false);
  });
});


describe("結合の出力", () => {
  const base = {
    id: "g1",
    detected_by: "auto",
    input_digest: "d",
    verification: null,
    superseded_by_id: null,
    members: [{ media_file_id: "m1", rel_path: "library/a.MP4", size_bytes: 1, gap_seconds: null }],
  };

  it("できたファイルを結合画面に出す", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": {
        groups: [
          {
            ...base,
            status: "merged",
            output: {
              media_file_id: "out1",
              rel_path: "derived/dji-osmo/DCIM/OUT.MP4",
              size_bytes: 1024,
              missing: false,
            },
          },
        ],
      },
    });
    render(<MergesScreen />);

    expect(await screen.findByText("derived/dji-osmo/DCIM/OUT.MP4")).toBeInTheDocument();
    // 現行のグループの出力は消せない。
    expect(screen.queryByRole("button", { name: "このファイルを消す" })).toBeNull();
  });

  it("もう使われていない出力を、置き換えられたグループが出なくても消せる", async () => {
    // **置き換えられたグループは `/merge-groups` に出ない**（API の既定）。
    // 出るのは残っているファイルの方で、削除ボタンはそこに付く。
    const api = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": { groups: [] },
      "/media/stale-derived": {
        stale: [
          {
            id: "out1",
            rel_path: "derived/dji-osmo/DCIM/OLD.MP4",
            size_bytes: 1024,
            captured_at: "2026-08-08T12:54:04+09:00",
            reason: "superseded",
          },
        ],
      },
    });
    render(<MergesScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "このファイルを消す" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls().some((call) => call.method === "DELETE")).toBe(false);
  });

  it("もう使われていない出力が無ければ、その節を出さない", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": { groups: [] },
      "/media/stale-derived": { stale: [] },
    });
    render(<MergesScreen />);

    expect(await screen.findByText(/結合/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "このファイルを消す" })).toBeNull();
  });
});
