// ルート表そのものの試験（§13）。**個々の画面の試験は画面を直に描くので、
// ここでは踏まない。** `App` が実際に配線している経路・ナビの現在地・
// `taskCount` の配線・ログイン前に叩かないことの 4 つだけを見る。
//
// 各画面は `<section aria-label>` を持つので、accessible name が付いた
// `<section>` は `region` ロールになる。パスの誤字も、隣の画面の element と
// 取り違える書き間違いも、どちらもこの `region` の名前で捕まる。

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { stubApi } from "./test/api";
import { emitJob } from "./test/setup";

const AUTHENTICATED = { required: false, authenticated: true };

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

/** どの画面を開いても落ちないだけの最小限の応答。個々の中身は問わない。 */
const BASE_ROUTES = {
  "/auth/session": AUTHENTICATED,
  "/settings": { settings: [], warnings: [] },
  "/dashboard": EMPTY_DASHBOARD,
  "/devices": { volumes: [] },
  "/jobs": { jobs: [] },
  "/profiles": { profiles: [] },
  "/destinations": { destinations: [] },
  "/media": { media: [], total: 0, page: 1, page_size: 50 },
  "/merge-groups?status=skipped": { groups: [] },
  "/merge-groups": { groups: [] },
  "/media/stale-derived": { stale: [] },
  "/uploads?state=awaiting_datetime_approval": { records: [] },
  "/uploads": { records: [] },
};

/** ルート表（brief のとおり 13 本）。 */
const ROUTES: readonly [string, string][] = [
  ["/", "ホーム"],
  ["/card", "カードの中身"],
  ["/merge", "つなぐ"],
  ["/approve", "確認"],
  ["/send", "送る"],
  ["/sending", "送信中"],
  ["/photos", "写真"],
  ["/settings", "設定"],
  ["/settings/destinations", "送り先"],
  ["/settings/profiles", "カメラの種類"],
  ["/settings/general", "詳しい設定"],
  ["/settings/jobs", "作業の履歴"],
  ["/settings/merge-history", "つないだ後の後片付け"],
];

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("ルート表", () => {
  it.each(ROUTES)("%s は「%s」の画面を描く", async (path, label) => {
    stubApi(BASE_ROUTES);
    window.history.pushState({}, "", path);
    render(<App />);
    expect(await screen.findByRole("region", { name: label })).toBeInTheDocument();
  });
});

describe("知らないパス", () => {
  // **本文が空のまま止まらない**（§13「何が起きて次に何をすべきか」）。ルート表に
  // 無いパスは `Layout` だけが描かれ、中身が何も出ないまま終わる。
  it("その画面が無いことを書き、ホームへ戻る道を置く", async () => {
    stubApi(BASE_ROUTES);
    window.history.pushState({}, "", "/no-such-page");
    render(<App />);
    expect(await screen.findByRole("region", { name: "その画面はありません" })).toBeInTheDocument();
    // **読み上げの名前と、見えている見出しは別の式。** 片方だけ直しても気づける
    // ように、両方を見る。
    expect(
      screen.getByRole("heading", { level: 1, name: "その画面はありません" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
  });
});

describe("ナビの現在地（App の配線として）", () => {
  // `Layout.test.tsx` は `Layout` を直に描くので、`App` 側でそのルートが実在
  // することまでは見ていない。ここでは実際のルート経由で確かめる。

  it("/merge を開いている間もホームが現在地のまま", async () => {
    stubApi(BASE_ROUTES);
    window.history.pushState({}, "", "/merge");
    render(<App />);
    await screen.findByRole("region", { name: "つなぐ" });
    // **ナビの中で見る。** 作業の画面の戻る先もリンクで「ホームへ」と名乗るので、
    // 画面全体から `/ホーム/` で引くと 2 つ当たる。
    const nav = screen.getByRole("navigation", { name: "画面" });
    expect(within(nav).getByRole("link", { name: /ホーム/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("/sending を開いている間もホームが現在地のまま", async () => {
    stubApi(BASE_ROUTES);
    window.history.pushState({}, "", "/sending");
    render(<App />);
    await screen.findByRole("region", { name: "送信中" });
    expect(screen.getByRole("link", { name: /ホーム/ })).toHaveAttribute("aria-current", "page");
  });

  it("/photos を開いていると写真が現在地", async () => {
    stubApi(BASE_ROUTES);
    window.history.pushState({}, "", "/photos");
    render(<App />);
    await screen.findByRole("region", { name: "写真" });
    expect(screen.getByRole("link", { name: /写真/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /ホーム/ })).not.toHaveAttribute("aria-current", "page");
  });

  it("/settings/jobs を開いていると設定が現在地で、ホームは外れる", async () => {
    stubApi(BASE_ROUTES);
    window.history.pushState({}, "", "/settings/jobs");
    render(<App />);
    await screen.findByRole("region", { name: "作業の履歴" });
    // **完全一致で見る。** `JobHistoryScreen` 自身が「設定へ」という別のリンクを
    // 持つので、正規表現の部分一致だと 2 件ヒットして曖昧になる。
    expect(screen.getByRole("link", { name: "設定" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "ホーム" })).not.toHaveAttribute("aria-current", "page");
  });
});

describe("taskCount の配線", () => {
  it("やることの種類数（合計でも、いずれか 1 つの値でもない）をピルへ渡す", async () => {
    // merge_candidates: 5, unsent_total: 7, awaiting_total: 0 → 種類は 2 つ
    // （つなぐ・送る）。合計の 12、いずれか単独の 5 や 7 と区別する。
    stubApi({
      ...BASE_ROUTES,
      "/dashboard": { ...EMPTY_DASHBOARD, merge_candidates: 5, unsent_total: 7, awaiting_total: 0 },
    });
    window.history.pushState({}, "", "/");
    render(<App />);
    const home = await screen.findByRole("link", { name: /ホーム/ });
    await waitFor(() => expect(home).toHaveTextContent("2"));
    expect(home).not.toHaveTextContent("5");
    expect(home).not.toHaveTextContent("7");
    expect(home).not.toHaveTextContent("12");
  });

  // 却下・破棄・送り先の入り切りは進捗のイベントを出さない。**枠は画面を移っても
  // 再マウントしない**ので、画面側から直しに行かないとバッジが古いまま残る。
  it("ジョブにならない操作の後も、ピルの数を直す", async () => {
    let awaiting = 2;
    const api = stubApi(
      {
        ...BASE_ROUTES,
        "/uploads?state=awaiting_datetime_approval": {
          records: [
            {
              id: "r1",
              destination_id: "d1",
              media_file_id: "m1",
              origin: "pre_existing",
              remote_current: null,
              proposed: "2026-08-14 20:02",
              remote_checked_at: null,
              identical: false,
            },
          ],
        },
        "/uploads/r1/reject": { status: "ok" },
      },
      (path, init) => {
        if (path === "/uploads/r1/reject" && init?.method === "POST") {
          awaiting = 0;
        }
      },
    );
    // 応答は毎回いまの `awaiting` を映す（`stubApi` の表は固定なので、
    // /dashboard だけ差し替える）。
    const inner = globalThis.fetch as unknown as (input: string, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
      if (input.replace(/^\/api/, "") === "/dashboard") {
        void api;
        return Promise.resolve(
          new Response(JSON.stringify({ ...EMPTY_DASHBOARD, awaiting_total: awaiting }), {
            status: 200,
          }),
        );
      }
      return inner(input, init);
    });

    window.history.pushState({}, "", "/approve");
    render(<App />);
    // **ピルはナビの項目に付く。** 画面の戻る先も「ホームへ」と名乗るので、
    // ナビの中で引く。
    const nav = await screen.findByRole("navigation", { name: "画面" });
    const home = within(nav).getByRole("link", { name: /ホーム/ });
    await waitFor(() => expect(home).toHaveTextContent("1"));

    await userEvent.click(await screen.findByRole("button", { name: "却下する" }));

    await waitFor(() => expect(home.textContent).not.toMatch(/[0-9]/));
  });

  it("やることが 1 つも無ければ、ピルを出さない", async () => {
    stubApi(BASE_ROUTES); // merge_candidates / unsent_total / awaiting_total はすべて 0
    window.history.pushState({}, "", "/");
    render(<App />);
    await screen.findByRole("region", { name: "ホーム" });
    const home = screen.getByRole("link", { name: /ホーム/ });
    await waitFor(() => expect(home.textContent).not.toMatch(/[0-9]/));
  });
});

describe("ログイン前は叩かない", () => {
  it("認証が要って未ログインなら、/dashboard も /settings も叩かない", async () => {
    const api = stubApi({ ...BASE_ROUTES, "/auth/session": { required: true, authenticated: false } });
    window.history.pushState({}, "", "/");
    render(<App />);
    await screen.findByRole("region", { name: "ログイン" });
    expect(api.calls().some((c) => c.path === "/dashboard")).toBe(false);
    expect(api.calls().some((c) => c.path === "/settings")).toBe(false);
  });
});

// **枠も進捗で取り直す**（§13「画面を再読み込みせずに進む」）。ナビのバッジと
// 警告バナーは `BrowserRouter` の外側にあり、画面を移っても再マウントしない。
//
// `emitJob` は 1 タブに 1 本だけ開く共有の接続（`hooks/useEvents.ts`）へ配るので、
// 枠と画面の両方が同じイベントを受け取る。
describe("進捗が届いたら、枠も取り直す", () => {
  /** 応答を差し替えられる `fetch`。 */
  function stubMutable(state: { dashboard: unknown; settings: unknown }) {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        const body =
          path === "/dashboard"
            ? state.dashboard
            : path === "/settings"
              ? state.settings
              : path === "/auth/session"
                ? AUTHENTICATED
                : path.startsWith("/destinations")
                  ? { destinations: [] }
                  : path.startsWith("/profiles")
                    ? { profiles: [] }
                    : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
  }

  it("やることが増えたら、ナビのバッジも増える", async () => {
    const state = {
      dashboard: EMPTY_DASHBOARD as unknown,
      settings: { settings: [], warnings: [] } as unknown,
    };
    stubMutable(state);
    window.history.pushState({}, "", "/settings");
    render(<App />);
    await screen.findByRole("region", { name: "設定" });
    const home = screen.getByRole("link", { name: /ホーム/ });
    await waitFor(() => expect(home.textContent).not.toMatch(/[0-9]/));

    state.dashboard = { ...EMPTY_DASHBOARD, unsent_total: 7 };
    emitJob({ job_id: "j1", seq: 1, level: "info", message: "送った", data: null, at: "" });

    await waitFor(() => expect(home).toHaveTextContent("1"));
  });

  it("警告が消えたら、バナーも消える", async () => {
    const state = {
      dashboard: EMPTY_DASHBOARD as unknown,
      settings: {
        settings: [],
        warnings: [{ code: "timezone_unset", message: "時間帯が決まっていません" }],
      } as unknown,
    };
    stubMutable(state);
    window.history.pushState({}, "", "/settings");
    render(<App />);
    expect(await screen.findByText("時間帯が決まっていません")).toBeInTheDocument();

    state.settings = { settings: [], warnings: [] };
    emitJob({ job_id: "j1", seq: 1, level: "info", message: "直した", data: null, at: "" });

    await waitFor(() => expect(screen.queryByText("時間帯が決まっていません")).toBeNull());
  });
});
