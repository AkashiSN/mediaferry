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
  hold: (path: string, method: string) => boolean = () => false,
): { sent: () => Sent[]; release: () => void } {
  const sent: Sent[] = [];
  let open: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const path = input.replace(/^\/api/, "");
      const method = init?.method ?? "GET";
      sent.push({
        path,
        method,
        body: init?.body === undefined ? null : (JSON.parse(String(init.body)) as unknown),
      });
      const [status, body] = reply(path, method) ?? [200, fallback[path] ?? {}];
      // **応答を握ったままにできる。** 要求が飛んでいる間に何が押せるかを見る。
      if (hold(path, method)) {
        await gate;
      }
      return new Response(JSON.stringify(body), { status });
    }),
  );
  return { sent: () => [...sent], release: () => open() };
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
  // `window.prompt` も差し替えるので、テストごとに戻す。
  vi.unstubAllGlobals();
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
    // 切り替えが何の設定なのかと、切ったときにどうなるかも画面に出す。
    expect(screen.getByText("信頼したカードを自動で取り込む")).toBeInTheDocument();
    expect(screen.getByText(/オフにすると、信頼したカードでも毎回/)).toBeInTheDocument();
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

  // **`data === null` で読み込み中を判定すると、取得が失敗したときも真のまま
  // 残り、「読み込み中…」がバナーと同時に出て永久に消えない。** `loading` を
  // 見れば、失敗してもいずれ「読み込み中…」が消える。
  it("送り先の読み込みに失敗したら、読み込み中のままにしない", async () => {
    stubRoutes((path) => (path === "/destinations" ? [500, {}] : undefined), { ...ROUTES });
    renderTop();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const section = screen.getByRole("heading", { name: "送り先" }).closest("section");
    if (section === null) {
      throw new Error("送り先の節が無い");
    }
    expect(within(section).queryByText("読み込み中…")).toBeNull();
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
    expect(
      screen.getByText(/挿したカードがどの機種かを見分けるための決まりです/),
    ).toBeInTheDocument();
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

  it("入り切りの見た目は、いまの値のとおりにする", async () => {
    // **読めていない値や off を「オン」に倒さない**（挿すだけでコピーされると
    // 誤解させる）。
    stubApi(ROUTES);
    renderTop();

    await waitFor(() => expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true"));
  });

  it("オフのときはオフに見せる", async () => {
    stubApi({ ...ROUTES, "/settings": { settings: [{ ...AUTO_ON, value: "off" }], warnings: [] } });
    renderTop();

    await waitFor(() =>
      expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false"),
    );
  });

  it("それぞれの中身への入口を出す", async () => {
    stubApi(ROUTES);
    renderTop();

    const entries: [RegExp, string][] = [
      [/送り先/, "/settings/destinations"],
      [/カメラの種類/, "/settings/profiles"],
      [/詳しい設定/, "/settings/general"],
      [/作業の履歴/, "/settings/jobs"],
      // **つなぐへの常設の入口。** ホームの「やること」に出るのは現行の候補が
      // あるときだけなので、候補が 0 件だとつなぐ画面へ入る道が無くなる。
      [/^つなぐ/, "/merge"],
      [/つないだ動画の記録/, "/settings/merge-history"],
      // 裁定 42: 押した側がどちらの節に着いたか分かるよう、節の錨（#stale）へ飛ばす。
      [/使っていないファイル/, "/settings/merge-history#stale"],
      [/接続中のカード/, "/card"],
    ];
    for (const [name, href] of entries) {
      expect(await screen.findByRole("link", { name })).toHaveAttribute("href", href);
    }
    expect(screen.getByText(/ふだんは見なくて大丈夫です/)).toBeInTheDocument();
  });

  it("つなぐへの入口は、候補が 1 つも無くても出る", async () => {
    // ホームの「やること」は `merge_candidates > 0` のときしか出ないので、
    // **候補を作る画面へ入る道が消える。** ここは数を見ずに常設する。
    stubApi({ ...ROUTES, "/dashboard": { merge_candidates: 0 } });
    renderTop();

    expect(await screen.findByRole("link", { name: /^つなぐ/ })).toHaveAttribute("href", "/merge");
  });

  // 同じ理由で「日時の確認」も常設にする。ホームの札は待っているものがある
  // ときだけ出るので、0 件になるとこの画面へ入る道が無くなる。
  it("日時の確認への入口が、詳しい情報に常設されている", async () => {
    stubApi(ROUTES);
    renderTop();
    expect(await screen.findByRole("link", { name: /^日時の確認/ })).toHaveAttribute(
      "href",
      "/approve",
    );
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
    expect(field).toHaveValue("info");
    fireEvent.change(field, { target: { value: "debug" } });
    fireEvent.blur(field);

    await waitFor(() =>
      expect(api.sent().filter((call) => call.method === "PUT")).toEqual([
        { path: "/settings", method: "PUT", body: { key: "LOG_LEVEL", value: "debug" } },
      ]),
    );
  });

  // 保存は欄から離れたときに走る。**画面ぜんぶを disabled にすると、Tab で
  // 移った先の欄がその瞬間に無効化され、焦点が body へ飛んで打った文字が消える。**
  it("保存している間も、次の欄に打てる", async () => {
    const api = stubRoutes(
      () => undefined,
      { "/settings": { settings: [LOG_LEVEL, HTTP_PORT], warnings: [] } },
      (path, method) => path === "/settings" && method === "PUT",
    );
    renderGeneral();

    const level = await screen.findByLabelText(/LOG_LEVEL/);
    fireEvent.change(level, { target: { value: "debug" } });
    fireEvent.blur(level);
    await waitFor(() => expect(api.sent().some((call) => call.method === "PUT")).toBe(true));

    const port = screen.getByLabelText(/HTTP_PORT/);
    expect(port).toBeEnabled();
    fireEvent.change(port, { target: { value: "9090" } });
    expect(port).toHaveValue("9090");
    api.release();
  });

  // 欄が打った文字を持ったままだと、**サーバが正規化・拒否した値と画面が食い違う。**
  it("保存できたら、サーバが持っている値を出す", async () => {
    let stored = "info";
    const api = stubRoutes((path, method) => {
      if (path === "/settings" && method === "PUT") {
        stored = "debug";
        return [200, { status: "ok", applies: "runtime" }];
      }
      if (path === "/settings") {
        return [200, { settings: [{ ...LOG_LEVEL, value: stored }], warnings: [] }];
      }
      return undefined;
    });
    renderGeneral();

    const field = await screen.findByLabelText(/LOG_LEVEL/);
    fireEvent.change(field, { target: { value: "DEBUG" } });
    fireEvent.blur(field);

    await waitFor(() => expect(api.sent().some((call) => call.method === "PUT")).toBe(true));
    await waitFor(() => expect(screen.getByLabelText(/LOG_LEVEL/)).toHaveValue("debug"));
  });

  it("値を変えていなければ保存しない", async () => {
    // 送ると DB に行ができ、出所が「既定のまま」から「この画面で設定」に変わる。
    // **欄を通り過ぎただけで出所が動いて見えるのを避ける。**
    const api = stubRoutes(() => undefined, {
      "/settings": { settings: [LOG_LEVEL], warnings: [] },
    });
    renderGeneral();

    fireEvent.blur(await screen.findByLabelText(/LOG_LEVEL/));

    await waitFor(() => expect(api.sent().length).toBeGreaterThan(0));
    expect(api.sent().filter((call) => call.method === "PUT")).toEqual([]);
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
    // **`aria-label` と表示は別々の式**なので、読み上げだけ直っていても意味が無い。
    expect(screen.getByRole("button", { name: /複製/ })).toHaveTextContent("複製して変える");
  });

  it("設定へ戻れて、版の決まりを書く", async () => {
    stubRoutes(() => undefined, FALLBACK);
    renderProfiles();

    expect(await screen.findByRole("link", { name: /設定へ/ })).toHaveAttribute(
      "href",
      "/settings",
    );
    // **画面の名乗り**（§13「プロファイル」は出さない）。見出しと、読み上げが
    // 画面の範囲を掴むための節の名前の両方を見る。
    expect(screen.getByRole("heading", { level: 1, name: "カメラの種類" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "カメラの種類" })).toBeInTheDocument();
    // **過去の解釈は変わらない**（§6）。保存が既存データに触らないことの説明。
    expect(
      screen.getByText(/挿したカードがどの機種かを見分けるための決まりです/),
    ).toBeInTheDocument();
    expect(screen.getByText(/そのとき使った版のまま変わりません/)).toBeInTheDocument();
  });

  // **1 つのカメラ ＝ 1 つのまとまり。** 区切りの無い 1 枚の板に見出しと操作を
  // 積むと、ボタンが上下どちらのカメラのものか読めない（実機で「ボタンがどこか
  // 分かりにくい」と言われた）。見出しをカメラの名前の `h2` にし、そのカメラの
  // 操作をその節の中だけに置く。
  it("1 つのカメラの操作は、そのカメラのまとまりの中にだけ置く", async () => {
    stubRoutes(() => undefined, FALLBACK);
    renderProfiles();

    const heading = await screen.findByRole("heading", { name: "私のカメラ" });
    const group = heading.closest("section");
    expect(group).not.toBeNull();
    const mine = within(group!);
    expect(mine.getByRole("button", { name: "撮影日時を再計算する：私のカメラ" })).toBeInTheDocument();
    expect(mine.getByRole("button", { name: "私のカメラ を SD_CARD で試す" })).toBeInTheDocument();
    // **隣のカメラの操作は入らない。** ここが緩いと、1 枚の板に全部並んだ
    // ままでも通ってしまう。
    expect(mine.queryByRole("button", { name: /DJI Osmo Pocket/ })).toBeNull();
    expect(mine.queryByRole("heading", { name: "DJI Osmo Pocket" })).toBeNull();
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
    // **「試す」も出さない。** API は候補にあるものだけを見るので、押すと必ず
    // 「見つかりませんでした」になる（押しても無駄なボタンを置かない）。
    expect(screen.queryByRole("button", { name: /私のカメラ を .* で試す/ })).toBeNull();
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

    const edit = await screen.findByRole("button", { name: "編集：私のカメラ" });
    expect(edit).toHaveTextContent("編集");
    await userEvent.click(edit);
    // **開いた定義そのものを見る。** 1 件だけ読み直す経路が変わると中身が空になる。
    expect(await screen.findByLabelText(/カメラの種類の定義（YAML）/)).toHaveDisplayValue(
      /filename_pattern/,
    );
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByText(/版 2/)).toBeInTheDocument();
    expect(
      api.sent().filter((call) => call.path === "/profiles/my-camera" && call.method === "PUT"),
    ).toHaveLength(1);
  });

  it("保存している間は、どのボタンも押せない", async () => {
    // **二重に送らせない。** 保存は版を 1 つ増やすので、2 回走れば版が 2 つ増える。
    // 行の操作も同じ `busy` で閉じる（保存中に複製や再計算が走ると版が入れ違う）。
    const api = stubRoutes(
      (path, method) => {
        if (path === "/profiles/my-camera" && method === "GET") {
          return [200, { ...MINE, definition: DEFINITION }];
        }
        if (path === "/profiles/my-camera" && method === "PUT") {
          return [200, { ...MINE, revision: 2, definition: DEFINITION }];
        }
        return undefined;
      },
      FALLBACK,
      (path, method) => path === "/profiles/my-camera" && method === "PUT",
    );
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));
    await screen.findByLabelText(/カメラの種類の定義（YAML）/);
    await userEvent.click(screen.getByRole("button", { name: "保存する" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "保存する" })).toBeDisabled());
    for (const name of [
      "やめる",
      "編集：私のカメラ",
      "候補から外す：私のカメラ",
      "撮影日時を再計算する：私のカメラ",
      "私のカメラ を SD_CARD で試す",
      "複製して変える：DJI Osmo Pocket",
      "新しく作る",
    ]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }

    api.release();
    expect(await screen.findByText(/版 2/)).toBeInTheDocument();
  });

  it("複製している間は、もう一度押せない", async () => {
    const api = stubRoutes(
      (path, method) =>
        path === "/profiles/dji-osmo/duplicate" && method === "POST"
          ? [200, { ...MINE, slug: "my-dji", definition: DEFINITION }]
          : undefined,
      FALLBACK,
      (path, method) => path === "/profiles/dji-osmo/duplicate" && method === "POST",
    );
    renderProfiles();

    await userEvent.click(
      await screen.findByRole("button", { name: "複製して変える：DJI Osmo Pocket" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "複製する" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "複製する" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "やめる" })).toBeDisabled();

    api.release();
    await waitFor(() => expect(screen.queryByRole("button", { name: "複製する" })).toBeNull());
  });

  it("編集を開けなかったら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path === "/profiles/my-camera" && method === "GET"
          ? [404, { error: { code: "not_found", detail: "" } }]
          : undefined,
      FALLBACK,
    );
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "編集：私のカメラ" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/見つかりませんでした/);
  });

  it("複製できなかったら、その理由を画面に出す", async () => {
    // slug が重なると 409 が返る。**利用者がふつうに踏む経路。**
    stubRoutes(
      (path, method) =>
        path === "/profiles/dji-osmo/duplicate" && method === "POST"
          ? [409, { error: { code: "conflict", detail: "" } }]
          : undefined,
      FALLBACK,
    );
    renderProfiles();

    await userEvent.click(
      await screen.findByRole("button", { name: "複製して変える：DJI Osmo Pocket" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "複製する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/いまの状態ではこの操作はできません/);
  });

  it("候補から外せなかったら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path === "/profiles/my-camera/archive" && method === "POST"
          ? [409, { error: { code: "conflict", detail: "" } }]
          : undefined,
      FALLBACK,
    );
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "候補から外す：私のカメラ" }));
    await userEvent.click(await screen.findByRole("button", { name: "実行する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/いまの状態ではこの操作はできません/);
  });

  it("再計算を起動できなかったら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path === "/profiles/my-camera/recompute" && method === "POST"
          ? [409, { error: { code: "conflict", detail: "" } }]
          : undefined,
      FALLBACK,
    );
    renderProfiles();

    await userEvent.click(
      await screen.findByRole("button", { name: "撮影日時を再計算する：私のカメラ" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/いまの状態ではこの操作はできません/);
  });

  it("判定を試せなかったら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path.includes("/test") && method === "POST"
          ? [404, { error: { code: "not_found", detail: "" } }]
          : undefined,
      FALLBACK,
    );
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "私のカメラ を SD_CARD で試す" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/見つかりませんでした/);
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

    // **1 か所にだけ出す。** 同じ文をバナーと `role="status"` の両方へ出すと、
    // 失敗が 2 度読み上げられる。行番号を持つ側（バナー）だけを残す。
    expect(await screen.findByRole("alert")).toHaveTextContent("YAML として読めません（2 行目）");
    expect(screen.getAllByText(/2 行目/)).toHaveLength(1);
    expect(screen.queryByText(/予期しないエラー/)).toBeNull();
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
    // **どちらの欄かを名前で見分けられる。** 複製の欄と定義の欄は続けて開くので、
    // 読み上げでは節の名前が唯一の手がかりになる。
    const panel = screen.getByRole("region", { name: "複製" });
    expect(within(panel).getByRole("heading", { name: "複製して変える" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("新しい slug"), { target: { value: "my-dji" } });
    expect(screen.getByLabelText("表示名")).toHaveValue("DJI Osmo Pocket の複製");
    await userEvent.click(screen.getByRole("button", { name: "複製する" }));

    expect(await screen.findByLabelText(/カメラの種類の定義（YAML）/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "定義の編集" })).toBeInTheDocument();
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

    const drop = await screen.findByRole("button", { name: "候補から外す：私のカメラ" });
    expect(drop).toHaveTextContent("候補から外す");
    await userEvent.click(drop);

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

  it("外している間は、確認をもう一度押せない", async () => {
    const api = stubRoutes(
      () => undefined,
      FALLBACK,
      (path, method) => path === "/profiles/my-camera/archive" && method === "POST",
    );
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "候補から外す：私のカメラ" }));
    await userEvent.click(await screen.findByRole("button", { name: "実行する" }));

    // **2 度押しで 2 回外れない。** 確認は要求が終わるまで閉じないので、閉じる
    // までの間ボタンを止めておく必要がある。
    await waitFor(() => expect(screen.getByRole("button", { name: "実行する" })).toBeDisabled());
    api.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      api.sent().filter((call) => call.path === "/profiles/my-camera/archive"),
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

  it("一致したときは一致したと出し、どのカードで試したかを送る", async () => {
    // **一致・不一致は別の枝**なので、片方だけでは文言の取り違えが残る。
    // **どのカードで試したかは問い合わせの一部**（`volume_instance_id`）で、
    // 落ちるとサーバは別のカードを見る。
    const api = stubRoutes((path, method) => {
      if (path.includes("/test") && method === "POST") {
        return [200, { matched: true, reason: null }];
      }
      return undefined;
    }, FALLBACK);
    renderProfiles();

    await userEvent.click(await screen.findByRole("button", { name: "私のカメラ を SD_CARD で試す" }));

    expect(await screen.findByText("「私のカメラ」と SD_CARD: 一致します")).toBeInTheDocument();
    expect(api.sent().filter((call) => call.path.includes("/test"))).toEqual([
      {
        path: "/profiles/my-camera/test?volume_instance_id=v1",
        method: "POST",
        body: null,
      },
    ]);
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

    const again = await screen.findByRole("button", { name: "撮影日時を再計算する：私のカメラ" });
    expect(again).toHaveTextContent("撮影日時を再計算する");
    await userEvent.click(again);

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

  /** 「送り先を追加する」の form。既にある送り先の「接続の設定」と欄の見出しが同じ。 */
  function addForm(): HTMLElement {
    return screen.getByRole("form", { name: "送り先を追加する" });
  }

  it("設定へ戻れる", async () => {
    stubApi({ "/destinations": { destinations: [] } });
    renderDestinations();

    expect(await screen.findByRole("link", { name: /設定へ/ })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("退役は確認を経てから", async () => {
    const api = stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    const retire = await screen.findByRole("button", { name: "退役させる：居間の Immich" });
    expect(retire).toHaveTextContent("退役させる");
    await userEvent.click(retire);

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

    const resume = await screen.findByRole("button", { name: "使う：居間の Immich" });
    // **`aria-label` と表示は別々の式。** 読み上げだけ直っていると、画面には
    // 逆の言葉が出たままになる。
    expect(resume).toHaveTextContent("使う");
    expect(screen.queryByRole("button", { name: "休止する：居間の Immich" })).toBeNull();
    // **休止中を「使えます」と出さない**（状態の印には言葉を添える。§13）。
    expect(screen.getByText(/休止中：送り先の候補に出ません/)).toBeInTheDocument();
    expect(screen.queryByText(/使えます/)).toBeNull();
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

    const check = await screen.findByRole("button", { name: "つながるか確かめる：居間の Immich" });
    expect(check).toHaveTextContent("つながるか確かめる");
    await userEvent.click(check);

    await waitFor(() =>
      expect(
        api
          .calls()
          .filter((call) => call.path === "/destinations/d1/verify" && call.method === "POST"),
      ).toHaveLength(1),
    );
  });

  it("使える送り先には、使えると書く", async () => {
    stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    expect(await screen.findByText(/使えます/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "休止する：居間の Immich" })).toHaveTextContent(
      "休止する",
    );
  });

  it("最後に確かめた日時を、読める形で出す", async () => {
    stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    // システム時刻（clock が作る UTC）はそのまま出すと現地時刻に見えるので、印を添える。
    expect(await screen.findByText(/2026年8月18日 05:03（UTC）/)).toBeInTheDocument();
  });

  it("まだ確かめていない送り先には、そう書く", async () => {
    stubApi({
      "/destinations": { destinations: [{ ...HOME, verified_at: null }] },
      "/uploads": NO_SKIPS,
    });
    renderDestinations();

    expect(await screen.findByText(/まだ確かめていません/)).toBeInTheDocument();
  });

  it("名前を変えると、新しい名前だけを送る", async () => {
    vi.stubGlobal("prompt", vi.fn(() => "新しい名前"));
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

    const rename = await screen.findByRole("button", { name: "名前を変える：居間の Immich" });
    expect(rename).toHaveTextContent("名前を変える");
    await userEvent.click(rename);

    await waitFor(() =>
      expect(sent).toEqual([
        { path: "/destinations/d1", method: "PATCH", body: { name: "新しい名前" } },
      ]),
    );
  });

  it("名前の変更をやめたら、何も送らない", async () => {
    vi.stubGlobal("prompt", vi.fn(() => null));
    const api = stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "名前を変える：居間の Immich" }));

    expect(api.calls().some((call) => call.method === "PATCH")).toBe(false);
  });

  it("状態の再確認は、再確認の口を叩く", async () => {
    // **接続の検証（`verify`）と取り違えない。** 見る先が違う。
    const api = stubApi({ "/destinations": { destinations: [HOME] }, "/uploads": NO_SKIPS });
    renderDestinations();

    const recheck = await screen.findByRole("button", { name: "状態を再確認する：居間の Immich" });
    expect(recheck).toHaveTextContent("状態を再確認する");
    await userEvent.click(recheck);

    await waitFor(() =>
      expect(
        api
          .calls()
          .filter((call) => call.path === "/destinations/d1/recheck" && call.method === "POST"),
      ).toHaveLength(1),
    );
    expect(api.calls().some((call) => call.path.endsWith("/verify"))).toBe(false);
  });

  it("送り先を操作している間は、もう一度押せない", async () => {
    const api = stubRoutes(
      () => undefined,
      { "/destinations": { destinations: [HOME] } },
      (path, method) => path === "/destinations/d1" && method === "PATCH",
    );
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "休止する：居間の Immich" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "休止する：居間の Immich" })).toBeDisabled(),
    );
    for (const name of [
      "名前を変える：居間の Immich",
      "つながるか確かめる：居間の Immich",
      "状態を再確認する：居間の Immich",
      "退役させる：居間の Immich",
      "接続を検証して追加する",
    ]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }

    api.release();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "休止する：居間の Immich" })).toBeEnabled(),
    );
  });

  it("追加に失敗したら、その理由を画面に出す", async () => {
    // URL が拒まれる（400）、届かない（502）、鍵が違う（401）はどれもここへ来る。
    stubRoutes(
      (path, method) =>
        path === "/destinations" && method === "POST"
          ? [502, { error: { code: "destination_unreachable", detail: "" } }]
          : undefined,
      { "/destinations": { destinations: [] } },
    );
    renderDestinations();

    fireEvent.change(await screen.findByLabelText("名前"), { target: { value: "新しい送り先" } });
    fireEvent.change(within(addForm()).getByLabelText("接続先 URL"), {
      target: { value: "http://immich.invalid" },
    });
    fireEvent.change(within(addForm()).getByLabelText("API キー"), { target: { value: "秘密" } });
    await userEvent.click(screen.getByRole("button", { name: /接続を検証して追加する/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/送り先に接続できません/);
  });

  it("退役を始めるときは、古い失敗の表示を消す", async () => {
    // 前の操作の赤いバナーが残ったままだと、いま何が失敗しているのか読めない。
    stubRoutes(
      (path, method) =>
        path === "/destinations/d1" && method === "PATCH"
          ? [404, { error: { code: "not_found", detail: "" } }]
          : undefined,
      { "/destinations": { destinations: [HOME] } },
    );
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "休止する：居間の Immich" }));
    await screen.findByRole("alert");

    await userEvent.click(screen.getByRole("button", { name: "退役させる：居間の Immich" }));
    await userEvent.click(await screen.findByRole("button", { name: "実行する" }));

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("退役に失敗したら、その理由を画面に出す", async () => {
    stubRoutes(
      (path, method) =>
        path === "/destinations/d1/archive" && method === "POST"
          ? [409, { error: { code: "conflict", detail: "" } }]
          : undefined,
      { "/destinations": { destinations: [HOME] } },
    );
    renderDestinations();

    await userEvent.click(await screen.findByRole("button", { name: "退役させる：居間の Immich" }));
    await userEvent.click(await screen.findByRole("button", { name: "実行する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/いまの状態ではこの操作はできません/);
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

    const key = within(await screen.findByRole("form", { name: "送り先を追加する" })).getByLabelText(
      "API キー",
    );
    // **読み出しの API が無い**ので、欄は常に空から始まる（§12.3）。
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("type", "password");

    fireEvent.change(within(addForm()).getByLabelText("名前"), {
      target: { value: "新しい送り先" },
    });
    fireEvent.change(within(addForm()).getByLabelText("接続先 URL"), {
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
    // **送った後、鍵を欄に残さない**（同じ画面を開いたままにする人がいる）。
    await waitFor(() => expect(key).toHaveValue(""));
  });

  it("表示用 URL を入れたら、その値も送る", async () => {
    const sent: Sent[] = [];
    stubApi({ "/destinations": { destinations: [] } }, (path, init) => {
      if (init?.body !== undefined) {
        sent.push({
          path,
          method: init.method ?? "GET",
          body: JSON.parse(String(init.body)) as unknown,
        });
      }
    });
    renderDestinations();

    fireEvent.change(await screen.findByLabelText("名前"), { target: { value: "送り先" } });
    fireEvent.change(within(addForm()).getByLabelText("接続先 URL"), {
      target: { value: "http://immich.invalid" },
    });
    fireEvent.change(within(addForm()).getByLabelText("表示用 URL（任意）"), {
      target: { value: "http://immich.invalid/see" },
    });
    fireEvent.change(within(addForm()).getByLabelText("API キー"), { target: { value: "秘密" } });
    await userEvent.click(screen.getByRole("button", { name: /接続を検証して追加する/ }));

    await waitFor(() =>
      expect(sent).toEqual([
        {
          path: "/destinations",
          method: "POST",
          body: {
            name: "送り先",
            base_url: "http://immich.invalid",
            public_url: "http://immich.invalid/see",
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
