// ホーム（§13）。**やることが無いときは、無いと書く。**

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardProvider } from "../api/dashboard";
import { emitJob, openStream, failStream } from "../test/setup";
import { stubApi } from "../test/api";
import { SETTLE_MS } from "../hooks/useReloadOnEvents";
import { HomeScreen } from "./Home";

const EMPTY_DASHBOARD = {
  media_total: 0,
  destinations: [],
  running_jobs: 0,
  recent_imports: [],
  orphans: 0,
  missing: 0,
  warnings: [],
  merge_candidates: 0,
  merge_review_total: 0,
  unsent_total: 0,
  awaiting_total: 0,
};

/** ホームが必ず引く経路。個別のテストで上書きできるよう、先に広げる。 */
const BASE_ROUTES: Record<string, unknown> = {
  "/settings": { settings: [], warnings: [] },
  "/profiles": { profiles: [] },
};

/** `stubApi` に既定を足す。**登録し忘れは 404 になる**ので、常に引く経路はここに置く。 */
function stubHome(
  routes: Record<string, unknown>,
  onCall?: (path: string, init?: RequestInit) => unknown,
) {
  return stubApi({ ...BASE_ROUTES, ...routes }, onCall);
}

function renderHome() {
  return render(
    <MemoryRouter>
      {/* 集計は枠（`App.tsx`）が引いて配る。ホームだけを描くときも同じ形にする。 */}
      <DashboardProvider>
        <HomeScreen />
      </DashboardProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ホーム", () => {
  it("やることを、在るものだけ出す", async () => {
    stubHome({
      "/dashboard": { ...EMPTY_DASHBOARD, merge_candidates: 3, unsent_total: 48 },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/分かれている動画を 3 本つなぐ/)).toBeInTheDocument());
    expect(screen.getByText(/48 件をまだ送っていません/)).toBeInTheDocument();
    expect(screen.queryByText(/確認があります/)).not.toBeInTheDocument();
  });

  // §13「内部の名前をそのまま出さない」「日時は人が読める形で出す」。相対パスも
  // 生の ISO 文字列も内部の表現で、どちらも画面に出すものではない。
  it("さっき取り込んだものは、ファイル名と読める日時で出す", async () => {
    stubHome({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        recent_imports: [
          { id: "m1", rel_path: "2026/08/21/DJI_0043.MP4", captured_at: "2026-08-21T14:05:33+09:00" },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() =>
      expect(screen.getByText("DJI_0043.MP4（2026年8月21日 14:05）")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/2026\/08\/21\/DJI_0043\.MP4/)).toBeNull();
    expect(screen.queryByText(/14:05:33/)).toBeNull();
    // 「すべて」は写真の画面へ行く。
    expect(screen.getByRole("link", { name: "すべて" })).toHaveAttribute("href", "/photos");
  });

  it("やることが 1 つも無ければ、無いと書く", async () => {
    stubHome({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await waitFor(() =>
      expect(screen.getByText("いま、やることはありません")).toBeInTheDocument(),
    );
  });

  it("読み込み中は「やることはありません」を出さない", () => {
    // **0 件と読み込み中を混ぜない。** 直後に 3 件現れると驚かせる。
    stubHome({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    expect(screen.queryByText("いま、やることはありません")).not.toBeInTheDocument();
  });

  // **`data === null` で読み込み中を判定すると、取得が失敗したときも真のまま
  // 残り、「読み込み中…」がバナーと同時に出て永久に消えない。** `loading` を
  // 見れば、失敗してもいずれ「読み込み中…」が消える。
  it("読み込みに失敗したら、読み込み中のままにしない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/dashboard") {
          return Promise.resolve(new Response(JSON.stringify({}), { status: 500 }));
        }
        const body =
          {
            "/devices": { volumes: [] },
            "/jobs": { jobs: [] },
          }[path] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderHome();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByText("読み込み中…")).toBeNull();
  });

  // **承認と取り込みを、別々の仕事に見せない**（§1）。取り込む残りがあるカードは
  // 「やること」の札になり、未信頼ならその同じ札に信頼の入口も乗る。
  // **読めていないものを「無い」と言わない。** 失敗のバナーと「やることは
  // ありません」が並ぶ画面は、いま直している食い違いと同じ形（画面が嘘をつく）。
  it("カードの一覧を読めなかったら、やることはありませんと書かない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/devices") {
          return Promise.resolve(new Response(JSON.stringify({}), { status: 500 }));
        }
        const body =
          {
            "/dashboard": EMPTY_DASHBOARD,
            "/jobs": { jobs: [] },
            "/settings": { settings: [], warnings: [] },
            "/profiles": { profiles: [] },
          }[path] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderHome();

    await screen.findByRole("alert");
    expect(screen.queryByText("いま、やることはありません")).toBeNull();
  });

  // **押さなくても切り替わる、の土台**（§3）。`CardStanding` が「抜かないで
  // ください」から「いま抜いて大丈夫です」へ自分で変わるのは、この拍が回って
  // いるから。進捗の接続が切れている間は、これが唯一の自動更新になる。
  it("走っている作業があれば、押さなくても取り直す", async () => {
    vi.useFakeTimers();
    const api = stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [{ id: "j1", type: "import", status: "running", created_at: "2026-08-24T00:00:00Z" }],
      },
    });
    renderHome();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(api.calls().filter((call) => call.path === "/jobs").length).toBeGreaterThan(1);
  });

  it("走っている作業が無ければ、取り直しは回らない", async () => {
    vi.useFakeTimers();
    const api = stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(api.calls().filter((call) => call.path === "/jobs")).toHaveLength(1);
  });

  it("挿さっているカードを、信頼していなければそう書く", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "OSMO",
            size_bytes: 512_711_688_192,
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM がある",
            pending_count: 38,
            scanned_at: "2026-08-24T00:00:00Z",
            busy: false,
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/未承認です/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "このカードを信頼する" })).toBeInTheDocument();
  });

  // **カードは、どれが目の前のどれなのかが分かる形で出す。** 同じカメラの
  // カードが 2 枚挿さっていると、カメラの種類だけでは区別が付かない。
  it("挿さっているカードを、ラベルと容量で見分けられる", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "SD_Card",
            size_bytes: 512_711_688_192,
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM に一致するファイルが 1 件",
            pending_count: 0,
            scanned_at: "2026-08-24T00:00:00Z",
            busy: false,
          },
          {
            volume_instance_id: "v2",
            fs_label: "Pocket4",
            size_bytes: 116_047_982_592,
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: true,
            trusted: false,
            reason: "DCIM はあるが一致するファイルが無い（空）",
            pending_count: 0,
            scanned_at: "2026-08-24T00:00:00Z",
            busy: false,
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByText("SD_Card は初めて見るカードです")).toBeInTheDocument();
    expect(screen.getByText("Pocket4 は初めて見るカードです")).toBeInTheDocument();
    expect(screen.getByText("477 GiB")).toBeInTheDocument();
    expect(screen.getByText("108 GiB")).toBeInTheDocument();
    // 挿さっているカードは「いまの様子」にしか出ていないが、それでも空ではない。
    expect(screen.queryByText("いま、やることはありません")).toBeNull();
  });

  it("容量は、生のバイト数では出さない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "SD_Card",
            size_bytes: 512_711_688_192,
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM に一致するファイルが 1 件",
            pending_count: 0,
            scanned_at: "2026-08-24T00:00:00Z",
            busy: false,
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    await screen.findByText("SD_Card は初めて見るカードです");
    expect(screen.queryByText(/512711688192/)).toBeNull();
  });

  // ラベルが無いカードは `volumeLabel` が既定名を作る。見出しにもその名前が入る。
  it("ラベルの無いカードは、既定の名前で見出しに出る", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "",
            size_bytes: 116_047_982_592,
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM がある",
            pending_count: 0,
            scanned_at: "2026-08-24T00:00:00Z",
            busy: false,
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByText("名前の無いカード は初めて見るカードです")).toBeInTheDocument();
  });

  it("カメラの種類は、生の slug ではなく表示名を出す（§13）", async () => {
    // `work/CardDetail.tsx` の `profileDisplayName` と同じ引き当てを使う。
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "OSMO",
            size_bytes: 512_711_688_192,
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: true,
            trusted: false,
            reason: "DCIM がある",
            pending_count: 0,
            scanned_at: "2026-08-24T00:00:00Z",
            busy: false,
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
      "/profiles": { profiles: [{ slug: "dji-osmo", name: "DJI Osmo Pocket" }] },
    });
    renderHome();

    expect(await screen.findByText("DJI Osmo Pocket のカードのようです。")).toBeInTheDocument();
    expect(
      await screen.findByText("DJI Osmo Pocket の対象ですが、取り込む中身がまだありません。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/dji-osmo/)).toBeNull();
  });

  it("カメラの種類は、登録が無い slug だけフォールバックで出す", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "OSMO",
            size_bytes: 512_711_688_192,
            profile_slug: "unknown-cam",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM がある",
            pending_count: 0,
            scanned_at: "2026-08-24T00:00:00Z",
            busy: false,
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
      "/profiles": { profiles: [{ slug: "dji-osmo", name: "DJI Osmo Pocket" }] },
    });
    renderHome();

    expect(await screen.findByText("unknown-cam のカードのようです。")).toBeInTheDocument();
  });

  // **押した先の配線を、押して確かめる。** カードの札のボタンは
  // `work/CardDetail.tsx` と同じ操作（`trust` / `scan` / `import`）を叩くが、
  // **この画面だけに入った書き間違い**（`import` を `scan` と書き違える等）は、
  // そちらの試験では捕まらない。
  const actionableVolume = {
    volume_instance_id: "v1",
    fs_label: "OSMO",
    size_bytes: 512_711_688_192,
    profile_slug: "dji-osmo",
    identity_confidence: "high",
    provisional: false,
    trusted: true,
    reason: "DCIM がある",
    pending_count: 38,
    scanned_at: "2026-08-24T00:00:00Z",
    busy: false,
  };

  it("「いま取り込む」を押すと、数えてから取り込み、分かれた動画まで探す", async () => {
    const api = stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
      "/volumes/v1/scan": { job_id: "j-scan" },
      "/volumes/v1/import": { job_id: "j-import" },
      "/merge-groups/detect": { job_id: "j-detect" },
    });
    renderHome();

    await userEvent.click(await screen.findByRole("button", { name: "いま取り込む" }));

    // **数えるのが先。** 取り込みのジョブは前のスキャンが残した記録を読むので、
    // 数えずに取り込むとジョブは成功のまま 1 件も取り込まない。
    await waitFor(() =>
      expect(
        api.calls().some((call) => call.path === "/volumes/v1/import" && call.method === "POST"),
      ).toBe(true),
    );
    const posts = api.calls().filter((call) => call.method === "POST");
    expect(posts.map((call) => call.path)).toEqual([
      "/volumes/v1/scan",
      "/volumes/v1/import",
      "/merge-groups/detect?profile_slug=dji-osmo",
    ]);
  });

  it("対象外のカードには「いま取り込む」を出さない（探す先も無い）", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, profile_slug: null }] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    await waitFor(() =>
      expect(screen.getByText("対象外の理由: DCIM がある")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "いま取り込む" })).toBeNull();
  });

  it("対象外の理由が分からないときは、空欄にしない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, profile_slug: null, reason: null }] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByText("対象外の理由: 不明")).toBeInTheDocument();
  });

  // **数える前の 0 件は「空」ではない**（§1）。挿した直後のカードを「取り込む
  // ものはありません」と断定すると、自動スキャンが終わるまで画面が嘘をつく。
  it("まだ数えていないカードを、空だとは書かない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [{ ...actionableVolume, pending_count: 0, scanned_at: null }],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByText("中身を数えています。")).toBeInTheDocument();
    expect(screen.queryByText("取り込むものはありません。")).toBeNull();
    // 抜いていいかは、押さずに読める（§3）。
    expect(screen.getByText("いま抜いて大丈夫です。")).toBeInTheDocument();
  });

  // **信頼は許可なので、確認を取ってから記録する**（§12.1）。同じ札に置いても、
  // 押した先が確認を飛ばしてしまえば意味が無い。
  it("「このカードを信頼する」は、確認を取ってから信頼する", async () => {
    const api = stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, trusted: false }] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
      "/volumes/v1/trust": { status: "ok" },
    });
    renderHome();

    await userEvent.click(await screen.findByRole("button", { name: "このカードを信頼する" }));
    // 同意の内容は、いま挿さっているこのカードに何が起きるかで書く。
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("OSMO");
    expect(api.calls().some((call) => call.path === "/volumes/v1/trust")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() =>
      expect(
        api.calls().some((call) => call.path === "/volumes/v1/trust" && call.method === "POST"),
      ).toBe(true),
    );
    // 信頼したら一覧を引き直す（札の見え方が変わる）。
    await waitFor(() =>
      expect(api.calls().filter((call) => call.path === "/devices").length).toBeGreaterThan(1),
    );
  });

  // **読めていない設定を `trusted` と仮定しない。** 何が起きるかを書けないまま
  // 同意を取ることになる。
  it("設定を読めていない間は、信頼を押させない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, trusted: false }] },
      "/jobs": { jobs: [] },
    });
    renderHome();

    expect(await screen.findByRole("button", { name: "このカードを信頼する" })).toBeDisabled();
  });

  // **集計だけが返ってきた時点で「ありません」と書かない。** カードの一覧が
  // まだなら、次に何が出るかはまだ決まっていない。
  it("カードの一覧を読んでいる間は、やることはありませんと書かない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/devices") {
          // 返ってこない経路。読み込み中のまま留める。
          return new Promise(() => {});
        }
        const body =
          {
            "/dashboard": { ...EMPTY_DASHBOARD, orphans: 3 },
            "/jobs": { jobs: [] },
            "/settings": { settings: [], warnings: [] },
            "/profiles": { profiles: [] },
          }[path] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderHome();

    // 集計は届いている（届いたことが読み取れる行で確かめる）。
    await screen.findByText(/どこにも結び付いていないファイル 3 件/);
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(screen.queryByText("いま、やることはありません")).toBeNull();
  });

  // **競合に備えた 2 重目の錠**（§1）。別のタブから取り込みが始まっていて、
  // まだこの画面の作業一覧に現れていない間も、同じカードを掴ませない。
  it("掴まれているカードは、札が残っていても取り込ませない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, busy: true }] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByRole("button", { name: "いま取り込む" })).toBeDisabled();
    expect(screen.getByText("作業中です。終わるまで抜かないでください。")).toBeInTheDocument();
  });

  it("ラベルが無いカードが複数あると、見出しを連番で見分けられるようにする", async () => {
    // カードの札は `work/CardDetail.tsx` の `volumeLabel` を使う。一覧全体を
    // 渡さないと、複数枚が同時にラベル無しのとき見分けが付かない。
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          { ...actionableVolume, fs_label: "" },
          { ...actionableVolume, volume_instance_id: "v2", fs_label: "" },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByText("名前の無いカード 1 から 38 件を取り込む")).toBeInTheDocument();
    expect(screen.getByText("名前の無いカード 2 から 38 件を取り込む")).toBeInTheDocument();
  });

  // **実機で出た場面そのもの。** カードが挿さっていて、集計はすべて 0 だった。
  // カードを「状態」ではなく「仕事」として扱うので、札と空表示が同時に出ることは
  // 形の上で起こらない。
  it("カードが挿さっている場面で「やることはありません」と書かない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByText(/38 件を取り込む/)).toBeInTheDocument();
    expect(screen.queryByText("いま、やることはありません")).not.toBeInTheDocument();
  });

  // **押せてしまう問題を、ボタンごと消して塞ぐ。** 取り込みが走っているカードは
  // 「いま動いていること」の側で見えているので、「やること」には出ない。
  it("取り込みが走っている間は、取り込むボタンを出さない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, busy: true }] },
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-24T00:00:00Z",
            volume_instance_id: "v1",
          },
        ],
      },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(await screen.findByText("取り込み")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "いま取り込む" })).not.toBeInTheDocument();
    // どのカードの作業かは、見出しに添える（§1）。
    expect(screen.getByText("OSMO")).toBeInTheDocument();
  });

  // **危ないのは掴まれている間だけ**（§3）。抜いていいかを安全なときにしか
  // 出さないなら、この知らせは半分しか実装されていない。
  it("取り込みが走っているカードは、抜かないでと言う", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, busy: true }] },
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-24T00:00:00Z",
            volume_instance_id: "v1",
          },
        ],
      },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    // **その作業の箱の中に出す。** 外に並べると、カードの縁からはみ出して見える。
    const box = (await screen.findByText("取り込み")).closest("section");
    expect(box).toHaveTextContent("作業中です。終わるまで抜かないでください。");
  });

  /** 掴まれているカードと、それを掴んでいる作業。 */
  const busyCard = {
    "/dashboard": EMPTY_DASHBOARD,
    "/devices": { volumes: [{ ...actionableVolume, busy: true }] },
    "/jobs": {
      jobs: [
        {
          id: "j1",
          type: "import",
          status: "running",
          created_at: "2026-08-24T00:00:00Z",
          volume_instance_id: "v1",
        },
      ],
    },
    "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
  };

  // **抜いていいかの出所は `/devices` だけ**（§13）。2 秒の拍は `/jobs` しか
  // 取り直さないので、進捗の知らせが来たらカードの写しも取り直す。
  //
  // **ここは知らせの経路だけを見る。** 作業の一覧は走ったままにしてあるので、
  // 下の「空になった縁」では答えが出ない。
  it("進捗の知らせが届いたら、抜いていいかも取り直す", async () => {
    vi.useFakeTimers();
    stubHome(busyCard);
    renderHome();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(screen.getByText("作業中です。終わるまで抜かないでください。")).toBeInTheDocument();

    // サーバ側では作業が決着してカードを離した（失敗でも合図は届く）。
    stubHome({
      ...busyCard,
      "/devices": { volumes: [{ ...actionableVolume, busy: false }] },
    });
    act(() => {
      emitJob({
        job_id: "j1",
        seq: 1,
        level: "error",
        message: "作業が失敗した: ffprobe が見つからない",
        data: null,
        at: "2026-08-24T00:00:05Z",
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SETTLE_MS + 100);
    });

    expect(screen.getByText("いま抜いて大丈夫です。")).toBeInTheDocument();
  });

  // **知らせが届かなくても切り替わる**（§13）。走っている作業が空になった縁で
  // 一度だけ取り直す。**拍のたびには叩かない** —— `/devices` はブローカーへの
  // 問い合わせを伴うので、2 秒ごとに叩くと、カードを挿している限りマウントと
  // アンマウントが続く（`jobs/watcher.py`）。
  it("走っている作業が無くなったら、抜いていいかを取り直す", async () => {
    vi.useFakeTimers();
    stubHome(busyCard);
    renderHome();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(screen.getByText("作業中です。終わるまで抜かないでください。")).toBeInTheDocument();

    const api = stubHome({
      ...busyCard,
      "/devices": { volumes: [{ ...actionableVolume, busy: false }] },
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "failed",
            created_at: "2026-08-24T00:00:00Z",
            volume_instance_id: "v1",
          },
        ],
      },
    });
    // 拍が `/jobs` を取り直し、走っている作業が空になる。知らせは 1 件も来ない。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("いま抜いて大丈夫です。")).toBeInTheDocument();
    // **縁で 1 回だけ。** 走っている作業が無い間、`/devices` を叩き続けない。
    const before = api.calls().filter((call) => call.path === "/devices").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(api.calls().filter((call) => call.path === "/devices")).toHaveLength(before);
  });

  // **押しても何も起きないボタンは置かない**（§3）。抜いていいかは
  // `CardStanding` が常時の表示で答えるので、押して確かめる入口は要らない。
  it("「取り外す」ボタンは無い", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    await screen.findByText(/38 件を取り込む/);
    expect(screen.queryByRole("button", { name: /取り外す/ })).toBeNull();
  });

  // 抜く相手がいない作業（送信など）には出さない。
  it("カードに紐づかない作業には、抜いていいかを出さない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [{ id: "j1", type: "upload", status: "running", created_at: "2026-08-24T00:00:00Z" }],
      },
    });
    renderHome();

    await screen.findByText("送信");
    expect(screen.queryByText(/抜/)).toBeNull();
  });

  it("待機中の作業も残らず出す", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [
          { id: "a", type: "import", status: "running", created_at: "2026-08-24T00:00:00Z" },
          { id: "b", type: "detect_groups", status: "queued", created_at: "2026-08-24T00:00:01Z" },
        ],
      },
    });
    renderHome();

    expect(await screen.findByText("取り込み")).toBeInTheDocument();
    expect(screen.getByText("候補の検出")).toBeInTheDocument();
  });

  it("「中身を見る」を押すと、カードの中身のページへ行く（裁定 30）", async () => {
    // ホームのカードの札からカードの中身へ行ける（§13）。**ここは札の配線だけを
    // 見る** —— ルート表そのものは `App.test.tsx` が受け持つ。
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <DashboardProvider>
          <Routes>
            <Route path="/" element={<HomeScreen />} />
            <Route path="/card" element={<div>カードの中身のページ</div>} />
          </Routes>
        </DashboardProvider>
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("link", { name: "中身を見る" }));

    expect(await screen.findByText("カードの中身のページ")).toBeInTheDocument();
  });

  it("進行中の作業があれば、ファイル名と件数で出す", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-18T05:00:00Z",
            started_at: "2026-08-18T05:00:00Z",
            progress: {
              phase: "copy",
              rel_path: "DCIM/100MEDIA/DJI_0043.MP4",
              file_index: 12,
              file_count: 87,
              bytes_done: 1024,
              bytes_total: 4096,
            },
          },
        ],
      },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/12\/87 件/)).toBeInTheDocument());
    expect(screen.getByText(/DJI_0043\.MP4/)).toBeInTheDocument();
  });

  // 「いま取り込む」は スキャン → コピー → 候補の検出 の 3 本を積む。**1 本だけ
  // 選ぶと残りが画面から消える**ので、全部出したうえで動いているものを先に置く。
  it("動いている作業を先に置き、待っている作業も消さない", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [
          {
            id: "j3",
            type: "detect_groups",
            status: "queued",
            created_at: "2026-08-18T05:00:02Z",
          },
          {
            id: "j2",
            type: "import",
            status: "running",
            created_at: "2026-08-18T05:00:01Z",
            started_at: "2026-08-18T05:00:01Z",
            progress: {
              phase: "copy",
              rel_path: "DCIM/100MEDIA/DJI_0043.MP4",
              file_index: 12,
              file_count: 87,
              bytes_done: 1024,
              bytes_total: 4096,
            },
          },
        ],
      },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText("取り込み")).toBeInTheDocument());
    expect(screen.getByText("候補の検出")).toBeInTheDocument();
    expect(screen.getByText(/12\/87 件/)).toBeInTheDocument();
    // 動いているものが先。**並び順は一覧の順ではなく、この画面が決める。**
    const titles = screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent);
    expect(titles).toEqual(["取り込み", "候補の検出"]);
  });

  it("中止するのは、そのボタンが乗っている作業", async () => {
    // 出しているのと違う作業を止めると、コピーは走り続けたまま別の予定が消える。
    const api = stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [
          { id: "j3", type: "detect_groups", status: "queued", created_at: "2026-08-18T05:00:02Z" },
          {
            id: "j2",
            type: "import",
            status: "running",
            created_at: "2026-08-18T05:00:01Z",
            started_at: "2026-08-18T05:00:01Z",
            progress: { phase: "copy", bytes_done: 1024, bytes_total: 4096 },
          },
        ],
      },
      "/jobs/j2/cancel": { status: "ok" },
    });
    renderHome();
    // 動いているものが先に並ぶので、先頭のボタンは走っているコピーのもの。
    await screen.findByText("取り込み");
    await userEvent.click(screen.getAllByRole("button", { name: "中止する" })[0]);
    await waitFor(() =>
      expect(api.calls().some((call) => call.path === "/jobs/j2/cancel")).toBe(true),
    );
    expect(api.calls().some((call) => call.path === "/jobs/j3/cancel")).toBe(false);
    await waitFor(() =>
      expect(api.calls().filter((call) => call.path === "/jobs").length).toBeGreaterThan(1),
    );
  });

  it("まだどれも動いていなければ、次に走る作業から順に出す", async () => {
    stubHome({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [
          { id: "j3", type: "detect_groups", status: "queued", created_at: "2026-08-18T05:00:02Z" },
          { id: "j1", type: "scan", status: "queued", created_at: "2026-08-18T05:00:00Z" },
        ],
      },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText("スキャン")).toBeInTheDocument());
    expect(screen.getByText("候補の検出")).toBeInTheDocument();
    const titles = screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent);
    expect(titles).toEqual(["スキャン", "候補の検出"]);
  });

  // 裁定 8: 積んだまま送信が始まっていない `pending` は「まだ送っていない」から
  // 消えたので、止まった送信に気づけるよう送り先の行に別枠で出す。
  it("宛先に積んだまま止まっているものを「送信中」で出す", async () => {
    stubHome({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        destinations: [
          {
            destination_id: "d1",
            name: "home",
            enabled: true,
            complete: 5,
            failed: 0,
            awaiting_approval: 0,
            pending: 3,
            unsent: 0,
            stacked: 0,
            stack_skipped: 0,
          },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/送信中 3 件/)).toBeInTheDocument());
  });

  // **送れなかったものは、ここに出さないとどの画面にも出ない。**「まだ送って
  // いない」にも「送信済み」にも入らない（`docs/design.md` §10）。
  it("送れなかったものがあれば、送り先の行に件数を出す", async () => {
    stubHome({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        destinations: [
          {
            destination_id: "d1",
            name: "home",
            enabled: true,
            complete: 5,
            failed: 2,
            awaiting_approval: 0,
            pending: 0,
            unsent: 0,
            stacked: 0,
            stack_skipped: 0,
          },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/送れなかった 2 件/)).toBeInTheDocument());
  });

  it("送れなかったものが無ければ、その枠は出さない", async () => {
    stubHome({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        destinations: [
          {
            destination_id: "d1",
            name: "home",
            enabled: true,
            complete: 5,
            failed: 0,
            awaiting_approval: 0,
            pending: 0,
            unsent: 0,
            stacked: 0,
            stack_skipped: 0,
          },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/送信済み 5/)).toBeInTheDocument());
    expect(screen.queryByText(/送れなかった/)).toBeNull();
  });

  // `docs/decisions.md` の「孤立ファイルは報告するだけ」の **報告** にあたる。
  // 消す操作は置かないが、黙ってもいけない。
  it("行き場の無いファイルと、見つからないファイルを報告する", async () => {
    stubHome({
      "/dashboard": { ...EMPTY_DASHBOARD, orphans: 3, missing: 1 },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    const note = await screen.findByRole("status");
    expect(note).toHaveTextContent("どこにも結び付いていないファイル 3 件");
    expect(note).toHaveTextContent("見つからないファイル 1 件");
    // **削除の操作は足さない**（自動削除はデータを失う経路になる）。
    expect(note).toHaveTextContent("自動では消しません");
  });

  it("どちらも 0 件なら、報告そのものを出さない", async () => {
    stubHome({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await waitFor(() => expect(screen.getByText("いま、やることはありません")).toBeInTheDocument());
    expect(screen.queryByText(/どこにも結び付いていないファイル/)).toBeNull();
  });

  it("開いた直後は、接続が切れているとは出さない（まだ繋がったことが無いだけなので）", async () => {
    stubHome({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await waitFor(() => expect(screen.getByText("いま、やることはありません")).toBeInTheDocument());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("接続が切れたら、そう出す", async () => {
    stubHome({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    failStream();
    expect(await screen.findByRole("status")).toHaveTextContent("進捗の接続が切れています");
  });

  it("つながったら、表示を消す", async () => {
    stubHome({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    failStream();
    await screen.findByRole("status");
    openStream();
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });
});
