// 設定（§13）。トップは要約と入口だけを持ち、中身は送り先・カメラの種類・env 由来の
// 設定に分かれる。
//
// **env 由来は錠前付きで変えられない**、**ビルトインには複製しか出さない**、**YAML の
// 構文エラーはサーバへ送る前に落とす**、**既存の API キーは画面に出さない**を、
// それぞれの画面を描画して確かめる。

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../test/api";
import { SettingsScreen } from "./Settings";
import { DestinationsScreen } from "./settings/Destinations";
import { GeneralScreen } from "./settings/General";
import { ProfilesScreen } from "./settings/Profiles";

const AUTO_ON = {
  key: "AUTO_IMPORT",
  value: "trusted",
  source: "default",
  locked: false,
  tier: "runtime",
  writable: true,
};
const AUTO_LOCKED = { ...AUTO_ON, source: "env", locked: true, writable: false };

const BUILTIN = {
  slug: "dji-osmo",
  name: "DJI Osmo Pocket",
  revision: 1,
  revision_id: "r1",
  builtin: true,
  archived: false,
};
const MINE = {
  slug: "my-camera",
  name: "私のカメラ",
  revision: 1,
  revision_id: "r2",
  builtin: false,
  archived: false,
};
const DEFINITION = {
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

type Sent = { path: string; method: string; body: unknown };

/** path と method で応答（状態コードと本文）を選ぶ代役。
 *
 * `stubApi` はいつも 200 を返すので、**失敗した操作が画面に出るか**を見るテストと、
 * `GET /profiles` と `GET /profiles/{slug}` のように前方一致では区別できない経路が
 * ここを使う。送った本文も記録する。 */
function stubRoutes(
  reply: (path: string, method: string) => [number, unknown] | undefined,
  fallback: Record<string, unknown> = {},
): { sent: () => Sent[] } {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const path = input.replace(/^\/api/, "");
      const method = init?.method ?? "GET";
      sent.push({
        path,
        method,
        body: init?.body === undefined ? null : (JSON.parse(String(init.body)) as unknown),
      });
      const [status, body] = reply(path, method) ?? [200, fallback[path] ?? {}];
      return Promise.resolve(new Response(JSON.stringify(body), { status }));
    }),
  );
  return { sent: () => [...sent] };
}

/** その文字列を含む行。**行ごとの表示は、その行の中だけで確かめる。** */
function rowOf(text: string | RegExp): HTMLElement {
  const found = screen.getByText(text).closest("li");
  if (!(found instanceof HTMLElement)) {
    throw new Error("行が見つからない");
  }
  return found;
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("設定のトップ", () => {
  const ROUTES = {
    "/settings": { settings: [AUTO_ON], warnings: [] },
    "/destinations": { destinations: [] },
    "/profiles": { profiles: [] },
  };

  function renderTop() {
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
  }

  it("送信は常に手動だと、自動取り込みの説明に書く", async () => {
    stubApi({
      "/settings": { settings: [], warnings: [] },
      "/destinations": { destinations: [] },
      "/profiles": { profiles: [] },
    });
    renderTop();

    await waitFor(() =>
      expect(screen.getByText(/送信はどちらの設定でも常に手動/)).toBeInTheDocument(),
    );
  });

  it("送り先は名前と、いま使えるかどうかを出す", async () => {
    stubApi({
      ...ROUTES,
      "/destinations": {
        destinations: [
          { id: "d1", name: "居間の Immich", enabled: true },
          { id: "d2", name: "物置の控え", enabled: false },
        ],
      },
    });
    renderTop();

    await screen.findByText("居間の Immich");
    expect(within(rowOf("居間の Immich")).getByText(/使えます/)).toBeInTheDocument();
    expect(within(rowOf("物置の控え")).getByText(/休止中/)).toBeInTheDocument();
  });

  it("カメラの種類は表示名で出し、候補から外したものは出さない", async () => {
    stubApi({
      ...ROUTES,
      "/profiles": {
        profiles: [BUILTIN, { ...MINE, name: "昔のカメラ", archived: true }],
      },
    });
    renderTop();

    expect(await screen.findByText("DJI Osmo Pocket")).toBeInTheDocument();
    expect(screen.queryByText("昔のカメラ")).toBeNull();
  });

  it("自動取り込みを切り替えると、切り替えた先の値で保存する", async () => {
    const sent: Sent[] = [];
    stubApi(ROUTES, (path, init) => {
      if (init?.body !== undefined) {
        sent.push({
          path,
          method: init.method ?? "GET",
          body: JSON.parse(String(init.body)) as unknown,
        });
      }
    });
    renderTop();

    await waitFor(() => expect(screen.getByRole("switch")).toBeEnabled());
    await userEvent.click(screen.getByRole("switch"));

    await waitFor(() =>
      expect(sent).toEqual([
        { path: "/settings", method: "PUT", body: { key: "AUTO_IMPORT", value: "off" } },
      ]),
    );
  });

  it("env で固定されているときは、自動取り込みを切り替えられない", async () => {
    stubApi({ ...ROUTES, "/settings": { settings: [AUTO_LOCKED], warnings: [] } });
    renderTop();

    await waitFor(() => expect(screen.getByRole("switch")).toBeDisabled());
    expect(screen.getByText(/アプリ設定で固定されています/)).toBeInTheDocument();
  });

  it("保存に失敗したら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path === "/settings" && method === "PUT"
          ? [409, { error: { code: "setting_locked", detail: "" } }]
          : undefined,
      ROUTES,
    );
    renderTop();

    await waitFor(() => expect(screen.getByRole("switch")).toBeEnabled());
    await userEvent.click(screen.getByRole("switch"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/環境変数で固定されています/);
  });

  it("それぞれの中身への入口を出す", async () => {
    stubApi(ROUTES);
    renderTop();

    const entries: [RegExp, string][] = [
      [/送り先/, "/settings/destinations"],
      [/カメラの種類/, "/settings/profiles"],
      [/詳しい設定/, "/settings/general"],
      [/作業の履歴/, "/settings/jobs"],
      [/つないだ動画の記録/, "/settings/merge-history"],
      [/接続中のカード/, "/card"],
    ];
    for (const [name, href] of entries) {
      expect(await screen.findByRole("link", { name })).toHaveAttribute("href", href);
    }
  });
});

describe("env 由来の設定", () => {
  const DATA_ROOT = {
    key: "DATA_ROOT",
    value: "/data",
    source: "env",
    locked: true,
    tier: "bootstrap",
    writable: false,
  };
  const LOG_LEVEL = {
    key: "LOG_LEVEL",
    value: "info",
    source: "db",
    locked: false,
    tier: "runtime",
    writable: true,
  };
  const HTTP_PORT = {
    key: "HTTP_PORT",
    value: "8080",
    source: "default",
    locked: false,
    tier: "restart",
    writable: true,
  };

  function renderGeneral() {
    render(
      <MemoryRouter>
        <GeneralScreen />
      </MemoryRouter>,
    );
  }

  it("env 由来の設定は錠前付きで、変えられない", async () => {
    stubApi({
      "/settings": {
        settings: [
          { key: "DATA_ROOT", value: "/data", source: "env", locked: true, tier: "a", writable: false },
        ],
        warnings: [],
      },
      "/profiles": { profiles: [] },
      "/devices": { volumes: [] },
      "/destinations": { destinations: [] },
    });
    renderGeneral();

    await waitFor(() => expect(screen.getByLabelText(/DATA_ROOT/)).toBeDisabled());
  });

  it("書ける設定は、変えると保存する", async () => {
    const api = stubRoutes(() => undefined, {
      "/settings": { settings: [LOG_LEVEL], warnings: [] },
    });
    renderGeneral();

    const field = await screen.findByLabelText(/LOG_LEVEL/);
    fireEvent.change(field, { target: { value: "debug" } });
    fireEvent.blur(field);

    await waitFor(() =>
      expect(api.sent().filter((call) => call.method === "PUT")).toEqual([
        { path: "/settings", method: "PUT", body: { key: "LOG_LEVEL", value: "debug" } },
      ]),
    );
  });

  it("どこから来た値か、変えるといつ効くかを日本語で出す", async () => {
    stubApi({ "/settings": { settings: [DATA_ROOT, LOG_LEVEL, HTTP_PORT], warnings: [] } });
    renderGeneral();

    await screen.findByText("DATA_ROOT");
    const locked = within(rowOf("DATA_ROOT"));
    expect(locked.getByText(/アプリ設定で固定/)).toBeInTheDocument();
    expect(locked.getByText(/アプリ設定でだけ変えられます/)).toBeInTheDocument();
    const stored = within(rowOf("LOG_LEVEL"));
    expect(stored.getByText(/この画面で設定/)).toBeInTheDocument();
    expect(stored.getByText(/すぐに効きます/)).toBeInTheDocument();
    const fallback = within(rowOf("HTTP_PORT"));
    expect(fallback.getByText(/既定のまま/)).toBeInTheDocument();
    expect(fallback.getByText(/次にアプリを起動したときから効きます/)).toBeInTheDocument();
  });

  it("錠前は env で固定されている項目にだけ出す", async () => {
    stubApi({ "/settings": { settings: [DATA_ROOT, LOG_LEVEL], warnings: [] } });
    renderGeneral();

    await screen.findByText("DATA_ROOT");
    expect(within(rowOf("DATA_ROOT")).getByRole("img", { name: "固定されています" })).toBeInTheDocument();
    expect(within(rowOf("LOG_LEVEL")).queryByRole("img", { name: "固定されています" })).toBeNull();
  });

  it("値が無い設定は、空欄だと分かるようにする", async () => {
    stubApi({
      "/settings": { settings: [{ ...LOG_LEVEL, key: "DEFAULT_TIMEZONE", value: null }], warnings: [] },
    });
    renderGeneral();

    const field = await screen.findByLabelText(/DEFAULT_TIMEZONE/);
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("placeholder", "（未設定）");
  });

  it("保存に失敗したら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path === "/settings" && method === "PUT"
          ? [409, { error: { code: "setting_locked", detail: "" } }]
          : undefined,
      { "/settings": { settings: [LOG_LEVEL], warnings: [] } },
    );
    renderGeneral();

    const field = await screen.findByLabelText(/LOG_LEVEL/);
    fireEvent.change(field, { target: { value: "debug" } });
    fireEvent.blur(field);

    expect(await screen.findByRole("alert")).toHaveTextContent(/環境変数で固定されています/);
  });

  it("設定へ戻れる", async () => {
    stubApi({ "/settings": { settings: [LOG_LEVEL], warnings: [] } });
    renderGeneral();

    expect(await screen.findByRole("link", { name: /設定/ })).toHaveAttribute("href", "/settings");
  });
});

describe("カメラの種類", () => {
  const VOLUMES = { volumes: [{ volume_instance_id: "v1", fs_label: "SD_CARD" }] };
  const FALLBACK = {
    "/profiles": { profiles: [BUILTIN, MINE] },
    "/devices": VOLUMES,
  };

  function renderProfiles() {
    render(
      <MemoryRouter>
        <ProfilesScreen />
      </MemoryRouter>,
    );
  }

  it("ビルトインには複製しか出さない", async () => {
    stubApi({
      "/profiles": { profiles: [BUILTIN] },
      "/settings": { settings: [], warnings: [] },
      "/devices": { volumes: [] },
    });
    renderProfiles();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /複製/ })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /^編集/ })).not.toBeInTheDocument();
  });

  it("ビルトインには「候補から外す」を出さない", async () => {
    // `sync_builtins` は `archived_at` を戻さないので、一度外すと再起動しても
    // 復活しない（§6）。API も 409 で拒むが、**画面に出さないのが一段目**。
    stubRoutes(() => undefined, FALLBACK);
    renderProfiles();

    await screen.findByRole("button", { name: "候補から外す：私のカメラ" });
    expect(screen.queryByRole("button", { name: "候補から外す：DJI Osmo Pocket" })).toBeNull();
  });

  it("候補から外したものは、そう分かる形で出し、外す操作を出さない", async () => {
    stubRoutes(() => undefined, {
      ...FALLBACK,
      "/profiles": { profiles: [{ ...MINE, archived: true }] },
    });
    renderProfiles();

    await screen.findByText("私のカメラ");
    expect(screen.getByText(/候補から外してあります/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "候補から外す：私のカメラ" })).toBeNull();
  });

  it("保存すると PUT が飛び、上がった版が出る", async () => {
    const api = stubRoutes((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") {
        return [200, { ...MINE, definition: DEFINITION }];
      }
      if (path === "/profiles/my-camera" && method === "PUT") {
        return [200, { ...MINE, revision: 2, definition: DEFINITION }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));
    await screen.findByLabelText(/カメラの種類の定義（YAML）/);
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/版 2/)).toBeInTheDocument();
    expect(
      api.sent().filter((call) => call.path === "/profiles/my-camera" && call.method === "PUT"),
    ).toHaveLength(1);
  });

  it("保存している間は、もう一度押せない", async () => {
    // **二重に送らせない。** 保存は版を 1 つ増やすので、2 回走れば版が 2 つ増える。
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        if (path === "/profiles/my-camera" && method === "PUT") {
          await held;
          return new Response(JSON.stringify({ ...MINE, revision: 2, definition: DEFINITION }));
        }
        if (path === "/profiles/my-camera") {
          return new Response(JSON.stringify({ ...MINE, definition: DEFINITION }));
        }
        return new Response(JSON.stringify(FALLBACK[path as keyof typeof FALLBACK] ?? {}));
      }),
    );
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));
    await screen.findByLabelText(/カメラの種類の定義（YAML）/);
    const save = screen.getByRole("button", { name: "保存する" });
    await userEvent.click(save);

    await waitFor(() => expect(save).toBeDisabled());
    release();
  });

  it("新規作成は POST を呼び、作られたものを出す", async () => {
    const api = stubRoutes((path, method) => {
      if (path === "/profiles" && method === "POST") {
        return [200, { ...MINE, slug: "brand-new", revision: 1, definition: DEFINITION }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "新しく作る" }));
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/brand-new/)).toBeInTheDocument();
    expect(
      api.sent().filter((call) => call.path === "/profiles" && call.method === "POST"),
    ).toHaveLength(1);
  });

  it("サーバの検証エラーは、どこが悪いかが分かる形で出る", async () => {
    stubRoutes((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") {
        return [200, { ...MINE, definition: DEFINITION }];
      }
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
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));
    await screen.findByLabelText(/カメラの種類の定義（YAML）/);
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/timestamp.pattern は名前付きグループ/)).toBeInTheDocument();
  });

  it("YAML として読めないときは行が分かる形で出し、送らない", async () => {
    const api = stubRoutes((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") {
        return [200, { ...MINE, definition: DEFINITION }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));
    const editor = await screen.findByLabelText(/カメラの種類の定義（YAML）/);
    fireEvent.change(editor, { target: { value: "slug: my-camera\n  name: [壊れた" } });
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/2 行目/)).toBeInTheDocument();
    expect(api.sent().some((call) => call.method === "PUT")).toBe(false);
  });

  it("複製は slug を決めてから作り、そのまま編集に入る", async () => {
    // slug は作成後 immutable（ライブラリのパスに使う）。**作る前に決めさせる。**
    const api = stubRoutes((path, method) => {
      if (path === "/profiles/dji-osmo/duplicate" && method === "POST") {
        return [200, { ...MINE, slug: "my-dji", definition: DEFINITION }];
      }
      if (path === "/profiles/my-dji" && method === "GET") {
        return [200, { ...MINE, slug: "my-dji", definition: DEFINITION }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(
      await screen.findByRole("button", { name: "複製して変える：DJI Osmo Pocket" }),
    );
    fireEvent.change(screen.getByLabelText("新しい slug"), { target: { value: "my-dji" } });
    await userEvent.click(screen.getByRole("button", { name: "複製する" }));

    expect(await screen.findByLabelText(/カメラの種類の定義（YAML）/)).toBeInTheDocument();
    expect(
      api
        .sent()
        .filter((call) => call.path === "/profiles/dji-osmo/duplicate" && call.method === "POST"),
    ).toEqual([
      {
        path: "/profiles/dji-osmo/duplicate",
        method: "POST",
        body: { slug: "my-dji", name: "DJI Osmo Pocket の複製" },
      },
    ]);
  });

  it("候補から外すのは確認を経てから", async () => {
    const api = stubRoutes(() => undefined, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "候補から外す：私のカメラ" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.sent().some((call) => call.path.includes("archive"))).toBe(false);
  });

  it("確認して実行すると 1 回だけ外し、確認を閉じる", async () => {
    const api = stubRoutes(() => undefined, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "候補から外す：私のカメラ" }));
    await userEvent.click(await screen.findByRole("button", { name: "実行する" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      api
        .sent()
        .filter((call) => call.path === "/profiles/my-camera/archive" && call.method === "POST"),
    ).toHaveLength(1);
  });

  it("接続中のカードで判定を試せる", async () => {
    stubRoutes((path, method) => {
      if (path.includes("/test") && method === "POST") {
        return [200, { matched: false, reason: "DCIM が無い" }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "私のカメラ を SD_CARD で試す" }));

    expect(await screen.findByText(/DCIM が無い/)).toBeInTheDocument();
  });

  it("名前の無いカードでも、どのカードで試すのかが分かる", async () => {
    // API の `fs_label` は空文字であって `null` ではない（`??` は素通りする）。
    stubRoutes(() => undefined, {
      ...FALLBACK,
      "/devices": { volumes: [{ volume_instance_id: "v1", fs_label: "" }] },
    });
    renderProfiles();

    expect(
      await screen.findByRole("button", { name: "私のカメラ を 名前の無いカード で試す" }),
    ).toBeInTheDocument();
  });

  it("撮影日時の再計算を起動でき、進み具合への導線が出る", async () => {
    const api = stubRoutes((path, method) => {
      if (path === "/profiles/my-camera/recompute" && method === "POST") {
        return [200, { job_id: "j1" }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(
      await screen.findByRole("button", { name: "撮影日時を再計算する：私のカメラ" }),
    );

    expect(await screen.findByRole("link", { name: /進み具合/ })).toHaveAttribute(
      "href",
      "/settings/jobs",
    );
    expect(
      api
        .sent()
        .filter((call) => call.path === "/profiles/my-camera/recompute" && call.method === "POST"),
    ).toHaveLength(1);
  });

  it("timestamp を変えた保存の後は、再計算を促す", async () => {
    stubRoutes((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") {
        return [200, { ...MINE, definition: DEFINITION }];
      }
      if (path === "/profiles/my-camera" && method === "PUT") {
        return [200, { ...MINE, revision: 2, definition: DEFINITION }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));
    const editor = await screen.findByLabelText(/カメラの種類の定義（YAML）/);
    fireEvent.change(editor, {
      target: {
        value: JSON.stringify({
          ...DEFINITION,
          timestamp: { ...DEFINITION.timestamp, fallback: "exif" },
        }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/自動では直りません/)).toBeInTheDocument();
  });

  it("timestamp を変えていない保存では、再計算を促さない", async () => {
    stubRoutes((path, method) => {
      if (path === "/profiles/my-camera" && method === "GET") {
        return [200, { ...MINE, definition: DEFINITION }];
      }
      if (path === "/profiles/my-camera" && method === "PUT") {
        return [200, { ...MINE, revision: 2, definition: DEFINITION }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));
    const editor = await screen.findByLabelText(/カメラの種類の定義（YAML）/);
    fireEvent.change(editor, {
      target: { value: JSON.stringify({ ...DEFINITION, name: "名前だけ変えた" }) },
    });
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/版 2/)).toBeInTheDocument();
    expect(screen.queryByText(/自動では直りません/)).toBeNull();
  });
});

describe("送り先", () => {
  const HOME = {
    id: "d1",
    name: "居間の Immich",
    enabled: true,
    base_url: "http://immich.invalid",
    public_url: null,
    remote_user_id: "u1",
    verified_at: "2026-08-18T05:03:00Z",
  };
  const NO_SKIPS = { records: [] };

  function renderDestinations() {
    render(
      <MemoryRouter>
        <DestinationsScreen />
      </MemoryRouter>,
    );
  }

  it("退役は確認を経てから", async () => {
    const api = stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "退役させる：居間の Immich" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.calls().some((call) => call.path.includes("archive"))).toBe(false);
  });

  it("確認して実行すると 1 回だけ退役させ、確認を閉じる", async () => {
    const api = stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "退役させる：居間の Immich" }));
    await userEvent.click(await screen.findByRole("button", { name: "実行する" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      api
        .calls()
        .filter((call) => call.path === "/destinations/d1/archive" && call.method === "POST"),
    ).toHaveLength(1);
  });

  it("休止と再開は、いまの状態の逆を送る", async () => {
    const sent: Sent[] = [];
    stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS }, (path, init) => {
      if (init?.body !== undefined) {
        sent.push({
          path,
          method: init.method ?? "GET",
          body: JSON.parse(String(init.body)) as unknown,
        });
      }
    });
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "休止する：居間の Immich" }));

    await waitFor(() =>
      expect(sent).toEqual([
        { path: "/destinations/d1", method: "PATCH", body: { enabled: false } },
      ]),
    );
  });

  it("休止中の送り先には、再開する操作を出す", async () => {
    stubApi({
      "/destinations": { destinations: [{ ...HOME, enabled: false }] },
      "/uploads": NO_SKIPS,
    });
    renderDestinations();

    expect(await screen.findByRole("button", { name: "使う：居間の Immich" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "休止する：居間の Immich" })).toBeNull();
  });

  it("操作が失敗したら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path === "/destinations/d1" && method === "PATCH"
          ? [404, { error: { code: "not_found", detail: "" } }]
          : undefined,
      { "/destinations": { destinations: [HOME] } },
    );
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "休止する：居間の Immich" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/見つかりませんでした/);
  });

  it("つながるか確かめる操作は、その送り先だけを叩く", async () => {
    const api = stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    await userEvent.click(
      await screen.findByRole("button", { name: "つながるか確かめる：居間の Immich" }),
    );

    await waitFor(() =>
      expect(
        api
          .calls()
          .filter((call) => call.path === "/destinations/d1/verify" && call.method === "POST"),
      ).toHaveLength(1),
    );
  });

  it("最後に確かめた日時を、読める形で出す", async () => {
    stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    expect(await screen.findByText(/2026年8月18日 05:03/)).toBeInTheDocument();
  });

  it("同じライブラリを指す送り先があれば知らせる", async () => {
    stubApi({
      "/destinations": {
        destinations: [HOME, { ...HOME, id: "d2", name: "同じ Immich", remote_user_id: "u1" }],
      },
      "/uploads": NO_SKIPS,
    });
    renderDestinations();

    expect(await screen.findAllByText(/同じライブラリを指している/)).toHaveLength(2);
  });

  it("向き先が違う送り先には、何も言わない", async () => {
    stubApi({
      "/destinations": {
        destinations: [HOME, { ...HOME, id: "d2", name: "別の Immich", remote_user_id: "u2" }],
      },
      "/uploads": NO_SKIPS,
    });
    renderDestinations();

    await screen.findByText("別の Immich");
    expect(screen.queryByText(/同じライブラリを指している/)).toBeNull();
  });

  it("追加は API キーを送るが、既存のキーは画面に出さない", async () => {
    const sent: Sent[] = [];
    stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS }, (path, init) => {
      if (init?.body !== undefined) {
        sent.push({
          path,
          method: init.method ?? "GET",
          body: JSON.parse(String(init.body)) as unknown,
        });
      }
    });
    renderDestinations();

    const key = await screen.findByLabelText(/API キー/);
    // **読み出しの API が無い**ので、欄は常に空から始まる（§12.3）。
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("type", "password");

    fireEvent.change(screen.getByLabelText("名前"), { target: { value: "新しい送り先" } });
    fireEvent.change(screen.getByLabelText("接続先 URL"), {
      target: { value: "http://immich.invalid" },
    });
    fireEvent.change(key, { target: { value: "秘密" } });
    await userEvent.click(screen.getByRole("button", { name: /接続を検証して追加する/ }));

    await waitFor(() =>
      expect(sent).toEqual([
        {
          path: "/destinations",
          method: "POST",
          body: {
            name: "新しい送り先",
            base_url: "http://immich.invalid",
            public_url: null,
            api_key: "秘密",
          },
        },
      ]),
    );
  });

  it("宛先ごとに見送りの理由を出す", async () => {
    stubApi({
      "/destinations": { destinations: [HOME] },
      "/uploads": {
        records: [{ id: "u1", media_file_id: "m1", stack_reason: "相方が見つからない" }],
      },
    });
    renderDestinations();

    expect(await screen.findByText(/相方が見つからない/)).toBeInTheDocument();
  });

  it("見送りが無いときは、無いと書く", async () => {
    // **出ていないことが仕様に見える**のを避ける。
    stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    expect(await screen.findByText(/見送りはありません/)).toBeInTheDocument();
  });

  it("見送りだけを問い合わせる", async () => {
    const api = stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    await waitFor(() =>
      expect(
        api
          .calls()
          .some((call) => call.path === "/uploads?destination_id=d1&stack_state=skipped&limit=50"),
      ).toBe(true),
    );
  });

  it("打ち切ったことを黙らない", async () => {
    // **51 件目以降が「存在しない」ように見えるのを避ける。**
    const records = Array.from({ length: 50 }, (_, index) => ({
      id: `u${index}`,
      media_file_id: `m${index}`,
      stack_reason: "相方が見つからない",
    }));
    stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": { records } });
    renderDestinations();

    expect(await screen.findByText(/ほかにもあります/)).toBeInTheDocument();
  });

  it("収まっているときは何も言わない", async () => {
    stubApi({
      "/destinations": { destinations: [HOME] },
      "/uploads": {
        records: [{ id: "u1", media_file_id: "m1", stack_reason: "相方が見つからない" }],
      },
    });
    renderDestinations();

    await screen.findByText(/相方が見つからない/);
    expect(screen.queryByText(/ほかにもあります/)).toBeNull();
  });
});
