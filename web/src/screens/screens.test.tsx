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
import { DevicesScreen } from "./Devices";
import { LibraryScreen, summarise } from "./Library";
import { MergesScreen } from "./Merges";
import { SettingsScreen } from "./Settings";

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

describe("デバイスの信頼登録", () => {
  const base = {
    volume_instance_id: "v1",
    fs_label: "SD_Card",
    profile_slug: "dji-osmo",
    identity_confidence: "high",
    provisional: false,
    trusted: true,
    reason: null,
  };

  function stubDevices(volumes: unknown[], autoImport = "trusted", settingsStatus = 200) {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        calls.push({ path, method: init?.method ?? "GET" });
        if (path === "/settings" && settingsStatus !== 200) {
          return Promise.resolve(
            new Response(JSON.stringify({ error: { code: "internal", detail: "" } }), {
              status: settingsStatus,
            }),
          );
        }
        const body =
          path === "/settings"
            ? {
                warnings: [],
                settings: [
                  {
                    key: "AUTO_IMPORT",
                    value: autoImport,
                    source: "default",
                    locked: false,
                    tier: "runtime",
                    writable: true,
                  },
                ],
              }
            : path === "/devices"
              ? { volumes }
              : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
  }

  function renderDevices() {
    render(
      <MemoryRouter>
        <DevicesScreen />
      </MemoryRouter>,
    );
  }

  it("2 枚を並べ、それぞれ独立に操作できる", async () => {
    // Osmo は内蔵ストレージと SD の 2 つが同時に見える。
    stubDevices([
      base,
      { ...base, volume_instance_id: "v2", fs_label: "Pocket4", profile_slug: "canon-eos" },
    ]);
    renderDevices();

    await userEvent.click(await screen.findByRole("button", { name: "Pocket4 をスキャン" }));

    await waitFor(() => {
      expect(calls.some((call) => call.path === "/volumes/v2/scan")).toBe(true);
    });
    expect(calls.some((call) => call.path === "/volumes/v1/scan")).toBe(false);
  });

  it("「対象だが中身が無い」と「対象外」を区別して出す", async () => {
    stubDevices([
      { ...base, provisional: true },
      {
        ...base,
        volume_instance_id: "v2",
        fs_label: "USB_STICK",
        profile_slug: null,
        reason: "DCIM が無い",
      },
    ]);
    renderDevices();

    expect(await screen.findByText(/取り込む中身がまだありません/)).toBeInTheDocument();
    expect(screen.getByText(/DCIM が無い/)).toBeInTheDocument();
  });

  it("一致したボリュームでも、判定の理由を出す", async () => {
    // **理由は対象外だけのものではない**（§13）。「なぜこのプロファイルに
    // 決まったのか」が見えないと、プロファイルを直す手がかりが無い。
    stubDevices([{ ...base, reason: "DCIM に一致するファイルが 2 件" }]);
    renderDevices();

    expect(await screen.findByText(/DCIM に一致するファイルが 2 件/)).toBeInTheDocument();
  });

  it("承認は確認を経てから。ダイアログに信頼の限界を書く", async () => {
    stubDevices([{ ...base, trusted: false }]);
    renderDevices();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    // 確認を取る理由そのもの（§12.1 のプライバシー）と、信頼の限界の両方を書く。
    // **同意の対象には、いま挿してあるカードの中身が含まれる。**
    expect(dialog).toHaveTextContent(/NAS へコピー/);
    expect(dialog).toHaveTextContent(/いま入っている中身/);
    expect(dialog).toHaveTextContent(/取り違え/);
    expect(calls.some((call) => call.path.includes("trust"))).toBe(false);
  });

  it("未承認のカードには、いま挿してある中身も対象だと書く", async () => {
    // **承認すると、いま挿してあるこのカードの中身が次の監視周期で取り込まれる。**
    // watcher は毎 tick、現在 live な presence を候補に組み直すため（§12.1）。
    // 「次に挿したときから」と書くと、同意の対象を取り違えさせる。
    stubDevices([{ ...base, trusted: false }]);
    renderDevices();

    expect(await screen.findByText(/いま入っている中身も含めて/)).toBeInTheDocument();
    expect(screen.queryByText(/次にこのカードを挿したときから/)).toBeNull();
  });

  it("確度が低いだけなら、始まらないとは断言しない", async () => {
    // **初回の観測は必ず low。** その観測で指紋を憶えるので、画面が一覧を
    // 取り直すと同じ挿入のまま high になり、次の tick で積まれる
    // （`jobs/watcher.py` と `jobs/volumes.py::_identity_confidence`）。
    // 「いまは始まりません」と書くと、数秒後に始まる経路を否定してしまう。
    stubDevices([{ ...base, trusted: true, identity_confidence: "low" }]);
    renderDevices();

    expect(await screen.findByText(/確かめられた場合/)).toBeInTheDocument();
    expect(screen.queryByText(/いまは自動取り込みは始まりません/)).toBeNull();
  });

  it("確度が低い状態を「始まる」と約束しない", async () => {
    // **`low` には 2 種類ある。** `fs_uuid` が無い媒体や、同じ UUID の別 presence が
    // 併存している間は、何度観測しても `high` にならない
    // （`jobs/volumes.py::_identity_confidence`）。API は理由を返さないので画面は
    // 区別できない。**だから条件形で書く**（「確かめられた場合は」）。
    stubDevices([{ ...base, trusted: true, identity_confidence: "low" }]);
    renderDevices();

    expect(await screen.findByText(/確かめられた場合/)).toBeInTheDocument();
    expect(screen.queryByText(/確かめられしだい/)).toBeNull();
  });

  it("確度が低い未承認カードの確認は、同意の対象を示したまま条件を添える", async () => {
    stubDevices([{ ...base, trusted: false, identity_confidence: "low" }]);
    renderDevices();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    // **条件は文全体に掛ける。** 「以後このカードを挿すだけでコピーされます」を
    // 先に無条件で書いてから限定を付け足すと、`fs_uuid` の無い媒体では前半が
    // 成立せず、同じダイアログの中で矛盾する（§12.1 の同意として曖昧）。
    expect(dialog).toHaveTextContent(/確かめられた場合に限り/);
    expect(dialog).toHaveTextContent(/いま入っている中身も含めて/);
    expect(dialog).toHaveTextContent(/取り違え/);
    expect(dialog).not.toHaveTextContent(/承認の数秒後に始まります/);
  });

  it("確度が高いカードの確認だけが、条件なしで約束する", async () => {
    stubDevices([{ ...base, trusted: false }]);
    renderDevices();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/承認の数秒後に始まります/);
    expect(dialog).not.toHaveTextContent(/確かめられた場合に限り/);
  });

  it("watcher が積まない状態では、承認しても始まらないと書く", async () => {
    // CANDIDATES は identity_confidence = 'high' かつ provisional = 0 を要求する
    // （`jobs/watcher.py`）。断言すると、同意の内容が実挙動とずれる。
    stubDevices([{ ...base, trusted: false, provisional: true }]);
    renderDevices();

    expect(await screen.findByText(/いまは自動取り込みは始まりません/)).toBeInTheDocument();
    expect(screen.queryByText(/数秒後から自動で取り込みます/)).toBeNull();
  });

  it("確度が低いカードでも、承認したら始まる見込みだと書く", async () => {
    stubDevices([{ ...base, trusted: false, identity_confidence: "low" }]);
    renderDevices();

    expect(await screen.findByText(/確かめられた場合/)).toBeInTheDocument();
    expect(screen.queryByText(/いまは自動取り込みは始まりません/)).toBeNull();
  });

  it("AUTO_IMPORT が off なら、承認しても始まらないと書く", async () => {
    stubDevices([{ ...base, trusted: false }], "off");
    renderDevices();

    expect(await screen.findByText(/いまは自動取り込みは始まりません/)).toBeInTheDocument();
  });

  it("始まらない状態の確認ダイアログは、いま始まらないことを書く", async () => {
    stubDevices([{ ...base, trusted: false, provisional: true }]);
    renderDevices();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    // **同意の対象を偽らない。** 信頼は記録するが、いまはコピーが始まらない。
    expect(dialog).toHaveTextContent(/いまは自動取り込みは始まりません/);
    expect(dialog).toHaveTextContent(/取り違え/);
    expect(dialog).not.toHaveTextContent(/いま入っている中身も含めて/);
  });

  it("AUTO_IMPORT が off なら、その旨と設定への導線を出す", async () => {
    stubDevices([base], "off");
    renderDevices();

    expect(await screen.findByText(/自動取り込みは無効/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /設定/ })).toBeInTheDocument();
  });

  it("設定を読めていない間は、始まると断言せず信頼も押させない", async () => {
    // **`/settings` が未解決・失敗のときに `trusted` と仮定しない。** 実設定が
    // off でも「いまの中身を数秒後にコピー」と誤って同意を取ることになる。
    stubDevices([{ ...base, trusted: false }], "trusted", 500);
    renderDevices();

    expect(await screen.findByText(/設定をまだ読めていない/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SD_Card を信頼する" })).toBeDisabled();
  });

  it("設定の取得に失敗したら、その失敗も画面に出す", async () => {
    stubDevices([base], "trusted", 500);
    renderDevices();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("AUTO_IMPORT が有効なら、無効の案内は出さない", async () => {
    stubDevices([base]);
    renderDevices();

    expect(await screen.findByText(/挿すと自動で取り込みます/)).toBeInTheDocument();
    expect(screen.queryByText(/自動取り込みは無効/)).toBeNull();
  });
});
