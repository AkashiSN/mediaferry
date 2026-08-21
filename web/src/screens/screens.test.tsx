// **不可逆な操作は、呼び出し側それぞれで確認が出る**（§13、計画レビューの指摘）。
//
// 型（`Confirmation` の直和）だけでは「その画面で実際に出た」ことの証明にならない。
// 画面ごとに、押しても**確認の前には API を叩かない**ことを見る。

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalsScreen } from "./Approvals";
import { DestinationsScreen } from "./Destinations";
import { LibraryScreen } from "./Library";
import { MergesScreen } from "./Merges";
import { DashboardScreen } from "./Dashboard";
import { JobsScreen } from "./Jobs";
import { SettingsScreen } from "./Settings";
import { stubApi } from "../test/api";

// `stubProfiles`（このファイルの下の方）は `stubApi` を使わず、自前で `fetch` を
// 差し替えて、この `calls` に記録する。
let calls: { path: string; method: string }[] = [];

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

describe("転送先の退役", () => {
  it("確認が出るまで退役させない", async () => {
    const api = stubApi({
      "/destinations": {
        destinations: [
          { id: "d1", name: "home", enabled: true, base_url: "http://x", public_url: null, same_library_as: [] },
        ],
      },
    });
    render(<DestinationsScreen />);

    await userEvent.click(await screen.findByRole("button", { name: "退役させる" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls().some((call) => call.path.includes("archive"))).toBe(false);
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

describe("プロファイルの編集", () => {
  const builtin = {
    slug: "dji-osmo",
    name: "DJI Osmo Pocket",
    revision: 1,
    revision_id: "r1",
    builtin: true,
    archived: false,
  };
  const mine = {
    slug: "my-camera",
    name: "私のカメラ",
    revision: 1,
    revision_id: "r2",
    builtin: false,
    archived: false,
  };
  const definition = {
    slug: "my-camera",
    name: "私のカメラ",
    hints: { usb_ids: [], volume_labels: [] },
    require: { roots: ["DCIM"], filename_pattern: "^IMG_\\d{4}\\.JPG$", min_matching_files: 1 },
    scan: { roots: ["DCIM"], extensions: ["JPG"] },
    timestamp: {
      source: "exif",
      pattern: null,
      format: null,
      fallback: "mtime",
      timezone_policy: "none",
      timezone: null,
    },
    merge: {
      enabled: false,
      tolerance_seconds: 5,
      min_part_size_gib: 4,
      sequence_pattern: "",
      output_name: "",
      keep_streams: { video: "primary", audio: "all", timecode: false, data: false },
    },
    immich: { tags: [], tag_pre_existing: true, fix_datetime_after_upload: false },
  };

  /** path と method で応答を選ぶ。`startsWith` だと `/profiles` が全部に当たる。 */
  function stubProfiles(reply: (path: string, method: string) => [number, unknown] | undefined) {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        calls.push({ path, method });
        const chosen = reply(path, method);
        const [status, body] = chosen ?? [
          200,
          path === "/settings"
            ? { settings: [], warnings: [] }
            : path === "/profiles"
              ? { profiles: [builtin, mine] }
              : path === "/devices"
                ? { volumes: [{ volume_instance_id: "v1", fs_label: "SD_Card" }] }
                : {},
        ];
        return Promise.resolve(new Response(JSON.stringify(body), { status }));
      }),
    );
  }

  function renderSettings() {
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
  }

  it("ビルトインには編集ボタンが出ず、「複製して編集」が出る", async () => {
    stubProfiles(() => undefined);
    renderSettings();

    expect(await screen.findByRole("button", { name: "dji-osmo を複製して編集" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "dji-osmo を編集" })).toBeNull();
    expect(screen.getByRole("button", { name: "my-camera を編集" })).toBeInTheDocument();
  });

  it("保存すると PUT が飛び、上がったリビジョン番号が出る", async () => {
    stubProfiles((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") return [200, { ...mine, definition }];
      if (path === "/profiles/my-camera" && method === "PUT") {
        return [200, { ...mine, revision: 2, definition }];
      }
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera を編集" }));
    await screen.findByLabelText("プロファイル定義（YAML）");
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/版 2/)).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/profiles/my-camera" && call.method === "PUT")).toHaveLength(1);
  });

  it("新規作成は POST を呼び、作られた slug を出す", async () => {
    stubProfiles((path, method) => {
      if (path === "/profiles" && method === "POST") {
        return [200, { ...mine, slug: "brand-new", revision: 1, definition }];
      }
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "プロファイルを新規作成" }));
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/brand-new/)).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/profiles" && call.method === "POST")).toHaveLength(1);
  });

  it("サーバの検証エラーは、どこが悪いかが分かる形で出る", async () => {
    stubProfiles((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") return [200, { ...mine, definition }];
      if (path === "/profiles/my-camera" && method === "PUT") {
        return [
          400,
          {
            error: {
              code: "validation_failed",
              detail: "timestamp.pattern は名前付きグループ ts を持つ必要がある",
            },
          },
        ];
      }
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera を編集" }));
    await screen.findByLabelText("プロファイル定義（YAML）");
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/timestamp.pattern は名前付きグループ/)).toBeInTheDocument();
  });

  it("YAML として読めないときは行が分かる形で出し、送らない", async () => {
    stubProfiles((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") return [200, { ...mine, definition }];
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera を編集" }));
    const editor = await screen.findByLabelText("プロファイル定義（YAML）");
    fireEvent.change(editor, { target: { value: "slug: my-camera\n  name: [壊れた" } });
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/行目/)).toBeInTheDocument();
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
  });

  it("接続中のカードで判定を試せる", async () => {
    stubProfiles((path, method) => {
      if (path.includes("/test") && method === "POST") {
        return [200, { matched: false, reason: "DCIM が無い" }];
      }
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera を SD_Card で試す" }));

    expect(await screen.findByText(/DCIM が無い/)).toBeInTheDocument();
  });

  it("候補から外すのは確認を経てから", async () => {
    stubProfiles(() => undefined);
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera を候補から外す" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(calls.some((call) => call.path.includes("archive"))).toBe(false);
  });

  it("ビルトインには「候補から外す」を出さない", async () => {
    // `sync_builtins` は `archived_at` を戻さないので、一度外すと再起動しても
    // 復活しない（§6）。API も 409 で拒むが、**画面に出さないのが一段目**。
    stubProfiles(() => undefined);
    renderSettings();

    await screen.findByRole("button", { name: "my-camera を候補から外す" });
    expect(screen.queryByRole("button", { name: "dji-osmo を候補から外す" })).toBeNull();
  });

  it("複製は slug を決めてから作り、そのまま編集に入る", async () => {
    // slug は作成後 immutable（ライブラリのパスに使う）。**作る前に決めさせる。**
    stubProfiles((path, method) => {
      if (path === "/profiles/dji-osmo/duplicate" && method === "POST") {
        return [200, { ...mine, slug: "my-dji", definition }];
      }
      if (path === "/profiles/my-dji" && method === "GET") {
        return [200, { ...mine, slug: "my-dji", definition }];
      }
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "dji-osmo を複製して編集" }));
    const slug = screen.getByLabelText("新しい slug");
    fireEvent.change(slug, { target: { value: "my-dji" } });
    await userEvent.click(screen.getByRole("button", { name: "複製する" }));

    expect(await screen.findByLabelText("プロファイル定義（YAML）")).toBeInTheDocument();
    expect(
      calls.filter((call) => call.path === "/profiles/dji-osmo/duplicate" && call.method === "POST"),
    ).toHaveLength(1);
  });

  it("撮影日時の再計算を起動でき、ジョブ画面への導線が出る", async () => {
    stubProfiles((path, method) => {
      if (path === "/profiles/my-camera/recompute" && method === "POST") return [200, { job_id: "j1" }];
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera の撮影日時を再計算する" }));

    expect(
      await screen.findByRole("link", { name: /ジョブ/ }),
    ).toBeInTheDocument();
    expect(
      calls.filter((call) => call.path === "/profiles/my-camera/recompute" && call.method === "POST"),
    ).toHaveLength(1);
  });

  it("timestamp を変えた保存の後は、再計算を促す", async () => {
    stubProfiles((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") return [200, { ...mine, definition }];
      if (path === "/profiles/my-camera" && method === "PUT") {
        return [200, { ...mine, revision: 2, definition }];
      }
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera を編集" }));
    const editor = await screen.findByLabelText("プロファイル定義（YAML）");
    fireEvent.change(editor, {
      target: {
        value: JSON.stringify({
          ...definition,
          timestamp: { ...definition.timestamp, timezone: "Asia/Tokyo" },
        }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/自動では直りません/)).toBeInTheDocument();
  });

  it("timestamp を変えていない保存では、再計算を促さない", async () => {
    stubProfiles((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") return [200, { ...mine, definition }];
      if (path === "/profiles/my-camera" && method === "PUT") {
        return [200, { ...mine, revision: 2, definition }];
      }
      return undefined;
    });
    renderSettings();

    await userEvent.click(await screen.findByRole("button", { name: "my-camera を編集" }));
    const editor = await screen.findByLabelText("プロファイル定義（YAML）");
    fireEvent.change(editor, {
      target: { value: JSON.stringify({ ...definition, name: "名前だけ変えた" }) },
    });
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/版 2/)).toBeInTheDocument();
    expect(screen.queryByText(/自動では直りません/)).toBeNull();
  });
});


describe("スタックの結果（§9.11）", () => {
  const destinations = {
    destinations: [{ id: "d1", name: "home", enabled: true, base_url: "http://immich.invalid" }],
  };

  it("宛先ごとに見送りの理由を出す", async () => {
    stubApi({
      "/destinations": destinations,
      "/uploads": {
        records: [
          {
            id: "u1",
            media_file_id: "m1",
            stack_state: "skipped",
            stack_reason: "相方が見つからない",
          },
        ],
      },
    });

    render(
      <MemoryRouter>
        <DestinationsScreen />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/相方が見つからない/)).toBeInTheDocument();
  });

  it("見送りが無いときは、無いと書く", async () => {
    // **出ていないことが仕様に見える**のを避ける（Phase 5 Task 8 の教訓）。
    stubApi({ "/destinations": destinations, "/uploads": { records: [] } });

    render(
      <MemoryRouter>
        <DestinationsScreen />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/見送りはありません/)).toBeInTheDocument();
  });

  it("見送りだけを問い合わせる", async () => {
    const api = stubApi({ "/destinations": destinations, "/uploads": { records: [] } });

    render(
      <MemoryRouter>
        <DestinationsScreen />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(
        api
          .calls()
          .some((call) => call.path === "/uploads?destination_id=d1&stack_state=skipped&limit=50"),
      ).toBe(true),
    );
  });

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

describe("見送りの打ち切り", () => {
  const destinations = {
    destinations: [{ id: "d1", name: "home", enabled: true, base_url: "http://immich.invalid" }],
  };

  it("打ち切ったことを黙らない", async () => {
    // **201 件目以降が「存在しない」ように見えるのを避ける。**
    const records = Array.from({ length: 50 }, (_, index) => ({
      id: `u${index}`,
      media_file_id: `m${index}`,
      stack_state: "skipped",
      stack_reason: "相方が見つからない",
    }));
    stubApi({ "/destinations": destinations, "/uploads": { records } });

    render(
      <MemoryRouter>
        <DestinationsScreen />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/ほかにもあります/)).toBeInTheDocument();
  });

  it("収まっているときは何も言わない", async () => {
    stubApi({
      "/destinations": destinations,
      "/uploads": {
        records: [{ id: "u1", media_file_id: "m1", stack_reason: "相方が見つからない" }],
      },
    });

    render(
      <MemoryRouter>
        <DestinationsScreen />
      </MemoryRouter>,
    );

    await screen.findByText(/相方が見つからない/);
    expect(screen.queryByText(/ほかにもあります/)).toBeNull();
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
