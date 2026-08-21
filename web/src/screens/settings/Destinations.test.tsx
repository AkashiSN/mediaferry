// 送り先（§12.3 / §13）。**送れなかったものを、ここから送り直せる。**
//
// **案内が指す先に、実物がある。** `work/Send.tsx` と `docs/user-guide.md` が
// 「設定 › 送り先の『送り直す』」を案内するので、この画面がその操作を持つ。

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { DestinationsScreen, resendNote } from "./Destinations";

const DESTINATION = {
  id: "d1",
  name: "家の Immich",
  enabled: true,
  base_url: "http://immich.invalid",
  public_url: null,
  remote_user_id: null,
  verified_at: null,
};

const FAILED_PATH = "/uploads?destination_id=d1&state=failed&limit=200";
const SKIPPED_PATH = "/uploads?destination_id=d1&stack_state=skipped&limit=50";

function routes(failed: { id: string }[], destination = DESTINATION) {
  return {
    "/destinations": { destinations: [destination] },
    [FAILED_PATH]: { records: failed },
    [SKIPPED_PATH]: { records: [] },
  };
}

function renderDestinations() {
  return render(
    <MemoryRouter>
      <DestinationsScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("送り直した結果の 1 文", () => {
  it("戻せた件数を出す", () => {
    expect(resendNote(3, 0)).toBe("3 件を送り直しています。");
  });

  it("戻せなかった分を隠さない", () => {
    expect(resendNote(3, 2)).toBe("3 件を送り直しています。2 件は送り直せませんでした。");
  });

  it("戻すものが無いときは、送信を始め直したとだけ書く", () => {
    expect(resendNote(0, 0)).toBe("送信を始め直しました。");
  });
});

describe("送れなかったものを送り直す", () => {
  /** 「送れなかったもの」の節だけを見る（「読み込み中…」は他の節にも出る）。 */
  function failedSection(): HTMLElement {
    const heading = screen.getByRole("heading", { name: "送れなかったもの" });
    const section = heading.closest("section");
    if (section === null) {
      throw new Error("送れなかったものの節が無い");
    }
    return section;
  }

  it("失敗した記録の件数を出す", async () => {
    stubApi(routes([{ id: "u1" }, { id: "u2" }]));
    renderDestinations();
    expect(await screen.findByText("送れなかったもの 2 件")).toBeInTheDocument();
  });

  // **読み上げと表示は別の式。** `aria-label` だけを見ていると、ボタンの文字が
  // 何に変わっても気付けない。
  it("ボタンには、読み上げにも表示にも「送り直す」と出す", async () => {
    stubApi(routes([{ id: "u1" }]));
    renderDestinations();
    const button = await screen.findByRole("button", { name: "送り直す：家の Immich" });
    expect(button).toHaveTextContent("送り直す");
  });

  it("失敗が無ければ、無いと書く", async () => {
    stubApi(routes([]));
    renderDestinations();
    expect(await screen.findByText("送れなかったものはありません。")).toBeInTheDocument();
  });

  // **「送れなかったものはありません」だけだと行き止まりに読める。** `送る`
  // 画面の「開始できなかった宛先…『送り直す』で始め直せます」の案内が指す先
  // なので、失敗ゼロでもボタンが効くことをここに書く。
  it("失敗が無くても、ここから始め直せると書く", async () => {
    stubApi(routes([]));
    renderDestinations();
    expect(
      await screen.findByText("失敗が無くても、止まっている送信をここから始め直せます。"),
    ).toBeInTheDocument();
  });

  // **`data === null` で読み込み中を判定すると、取得が失敗したときも真のまま
  // 残り、「読み込み中…」がバナーと同時に出て永久に消えない。** `loading` を
  // 見れば、失敗してもいずれ「読み込み中…」が消える。
  it("読み込みに失敗したら、読み込み中のままにしない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === FAILED_PATH) {
          return Promise.resolve(new Response(JSON.stringify({}), { status: 500 }));
        }
        const body = routes([])[path as keyof ReturnType<typeof routes>] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderDestinations();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(within(failedSection()).queryByText("読み込み中…")).toBeNull();
  });

  it("押すと、失敗した記録を戻してから送信を始め直す", async () => {
    const api = stubApi(routes([{ id: "u1" }, { id: "u2" }]));
    renderDestinations();
    await userEvent.click(await screen.findByRole("button", { name: /送り直す/ }));

    await waitFor(() =>
      expect(
        api.calls().some((c) => c.path === "/destinations/d1/upload" && c.method === "POST"),
      ).toBe(true),
    );
    const posts = api.calls().filter((c) => c.method === "POST");
    // **戻すのが先。** 送信を先に始めても、`failed` のままの記録は拾われない。
    expect(posts.map((c) => c.path)).toEqual([
      "/uploads/u1/retry",
      "/uploads/u2/retry",
      "/destinations/d1/upload",
    ]);
    expect(await screen.findByText("2 件を送り直しています。")).toBeInTheDocument();
  });

  it("失敗が無くても押せる（開始できなかった送信を動かし直すため）", async () => {
    const api = stubApi(routes([]));
    renderDestinations();
    await userEvent.click(await screen.findByRole("button", { name: /送り直す/ }));

    await waitFor(() =>
      expect(
        api.calls().some((c) => c.path === "/destinations/d1/upload" && c.method === "POST"),
      ).toBe(true),
    );
    expect(api.calls().some((c) => c.path.includes("/retry"))).toBe(false);
    expect(await screen.findByText("送信を始め直しました。")).toBeInTheDocument();
  });

  it("一部が戻せなくても、戻せた分は送る", async () => {
    // **全部やり直しにしない**（`work/Send.tsx` の「一部の宛先が失敗しても
    // 進める」と同じ考え方）。
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/uploads/u2/retry") {
          return Promise.resolve(
            new Response(
              JSON.stringify({ error: { code: "not_retryable", detail: "", meta: {} } }),
              { status: 409 },
            ),
          );
        }
        const body =
          routes([{ id: "u1" }, { id: "u2" }])[path as keyof ReturnType<typeof routes>] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderDestinations();
    await userEvent.click(await screen.findByRole("button", { name: /送り直す/ }));

    expect(
      await screen.findByText("1 件を送り直しています。1 件は送り直せませんでした。"),
    ).toBeInTheDocument();
    const posts = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock
      .calls;
    expect(posts.some(([path]) => path.includes("/destinations/d1/upload"))).toBe(true);
  });

  it("休止中の送り先では押せない", async () => {
    stubApi(routes([{ id: "u1" }], { ...DESTINATION, enabled: false }));
    renderDestinations();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /送り直す/ })).toBeDisabled(),
    );
  });

  it("飛んでいる間は押させない（二重に送らない）", async () => {
    // **応答を握ったまま止める。** 飛んでいる間の `disabled` は、解決を
    // こちらで持たないと観測できない。
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/uploads/u1/retry") {
          await held;
          return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
        }
        const body = routes([{ id: "u1" }])[path as keyof ReturnType<typeof routes>] ?? {};
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    renderDestinations();
    const button = await screen.findByRole("button", { name: /送り直す/ });
    await userEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    release();
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("1 件も戻せなければ、送信は始めずにバナーへ出す", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/uploads/u1/retry") {
          return Promise.resolve(
            new Response(
              JSON.stringify({ error: { code: "not_retryable", detail: "", meta: {} } }),
              { status: 409 },
            ),
          );
        }
        const body = routes([{ id: "u1" }])[path as keyof ReturnType<typeof routes>] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderDestinations();
    await userEvent.click(await screen.findByRole("button", { name: /送り直す/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "失敗した状態ではないので、再試行できません。",
    );
    const posts = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock
      .calls;
    expect(posts.some(([path]) => path.includes("/destinations/d1/upload"))).toBe(false);
  });
});

// 退役の確認は不可逆な操作の入口。**`busy` を `false` に倒しても落ちないなら、
// 確認の「実行する」を連打できる。**
describe("退役の確認", () => {
  it("飛んでいる間は、確認の「実行する」も押せない", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/destinations/d1/archive") {
          await held;
          return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
        }
        const body = routes([])[path as keyof ReturnType<typeof routes>] ?? {};
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    renderDestinations();
    await userEvent.click(
      await screen.findByRole("button", { name: "退役させる：家の Immich" }),
    );
    const run = screen.getByRole("button", { name: "実行する" });
    await userEvent.click(run);
    await waitFor(() => expect(run).toBeDisabled());
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

// **読み込み中かどうかは `useQuery` の `loading` で見る。** `data === null` で
// 見ると、失敗したときも真になり続けるので「読み込み中…」が消えなくなる
// （§9.11「見送りを黙らない」に反する。取得が終わる前に「見送りはありません。」
// と断言することも避けたい）。
describe("スタックの見送り", () => {
  /** `SKIPPED_PATH` の応答だけを握って止める `fetch`。 */
  function heldSkips(records: unknown[]) {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === SKIPPED_PATH) {
          await held;
          return new Response(JSON.stringify({ records }), { status: 200 });
        }
        const body = routes([])[path as keyof ReturnType<typeof routes>] ?? {};
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    return release;
  }

  /** 「スタックの見送り」の節だけを見る（「読み込み中…」は他の節にも出る）。 */
  function skipsSection(): HTMLElement {
    const heading = screen.getByRole("heading", { name: "スタックの見送り" });
    const section = heading.closest("section");
    if (section === null) {
      throw new Error("スタックの見送りの節が無い");
    }
    return section;
  }

  it("読み込みが終わるまで、見送りが無いと断言しない", async () => {
    const release = heldSkips([{ id: "s1", media_file_id: "m1", stack_reason: "組が送信中" }]);
    renderDestinations();
    await waitFor(() => expect(within(skipsSection()).getByText("読み込み中…")).toBeInTheDocument());
    expect(within(skipsSection()).queryByText("見送りはありません。")).toBeNull();
    release();
    await waitFor(() => expect(screen.getByText(/組が送信中/)).toBeInTheDocument());
  });

  it("読み終えて 0 件なら、無いと書く", async () => {
    const release = heldSkips([]);
    renderDestinations();
    release();
    expect(await screen.findByText("見送りはありません。")).toBeInTheDocument();
  });

  // **`data === null` で読み込み中を判定すると、取得が失敗したときも真のまま
  // 残り、「読み込み中…」がバナーと同時に出て永久に消えない。** `loading` を
  // 見れば、失敗してもいずれ「読み込み中…」が消える。
  it("読み込みに失敗したら、読み込み中のままにしない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === SKIPPED_PATH) {
          return Promise.resolve(new Response(JSON.stringify({}), { status: 500 }));
        }
        const body = routes([])[path as keyof ReturnType<typeof routes>] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderDestinations();
    await waitFor(() =>
      expect(within(skipsSection()).getByRole("alert")).toBeInTheDocument(),
    );
    expect(within(skipsSection()).queryByText("読み込み中…")).toBeNull();
  });
});

// 送り先の接続設定（`base_url` / `public_url` / `api_key` / `same_library`）を
// ここから入れ直せる。**`name` と `enabled` だけの本文はリビジョンを作らない短絡路**
// なので、画面は接続の欄を必ず送る —— `docs/development.md` の `0007` の回復手順
// （宛先を保存し直すと新しい観測がリビジョンに入る）が動く唯一の入口である。
describe("接続の設定を変える", () => {
  /** 要求の本文を記録する `stubApi` の代役。 */
  function stubWithBodies(overrides: Record<string, unknown> = {}) {
    const bodies: { path: string; method: string; body: Record<string, unknown> }[] = [];
    const api = stubApi({ ...routes([]), ...overrides }, (path, init) => {
      if (init?.body) {
        bodies.push({
          path,
          method: init.method ?? "GET",
          body: JSON.parse(init.body as string) as Record<string, unknown>,
        });
      }
    });
    return { ...api, bodies };
  }

  async function save() {
    await userEvent.click(
      await screen.findByRole("button", { name: "接続の設定を保存する：家の Immich" }),
    );
  }

  it("ボタンには、読み上げにも表示にも「保存する」と出す", async () => {
    stubWithBodies();
    renderDestinations();
    const button = await screen.findByRole("button", {
      name: "接続の設定を保存する：家の Immich",
    });
    expect(button).toHaveTextContent("保存する");
  });

  it("欄の見出しを画面にも出す（読み上げの名前とは別の式）", async () => {
    stubWithBodies();
    renderDestinations();
    const section = (await screen.findByRole("heading", { name: "接続の設定" })).closest("section");
    if (section === null) {
      throw new Error("接続の設定の節が無い");
    }
    expect(within(section).getByText("接続先 URL")).toBeInTheDocument();
    expect(within(section).getByText(/新しい API キー（変えないときは空のまま）/)).toBeInTheDocument();
  });

  it("form には送り先ごとの名前が付く（追加の form と読み上げで見分く）", async () => {
    stubWithBodies();
    renderDestinations();
    expect(
      await screen.findByRole("form", { name: "接続の設定：家の Immich" }),
    ).toBeInTheDocument();
  });

  it("同じライブラリだと決めても送れる", async () => {
    const { bodies } = stubWithBodies();
    renderDestinations();
    await userEvent.selectOptions(
      await screen.findByLabelText("向き先が同じライブラリかどうか：家の Immich"),
      "yes",
    );
    await save();
    await waitFor(() => expect(bodies.some((call) => call.method === "PATCH")).toBe(true));
    expect(bodies.find((call) => call.method === "PATCH")?.body.same_library).toBe(true);
  });

  it("何も変えずに保存しても、接続の欄を送る（短絡路に入らない）", async () => {
    const { bodies } = stubWithBodies();
    renderDestinations();
    await save();
    await waitFor(() => expect(bodies.some((call) => call.method === "PATCH")).toBe(true));
    const patch = bodies.find((call) => call.method === "PATCH");
    expect(patch?.path).toBe("/destinations/d1");
    expect(patch?.body.base_url).toBe("http://immich.invalid");
    expect(Object.keys(patch?.body ?? {}).some((key) => key === "name" || key === "enabled")).toBe(
      false,
    );
  });

  it("入れ直した接続先と鍵を送る", async () => {
    const { bodies } = stubWithBodies();
    renderDestinations();
    const baseUrl = await screen.findByLabelText("接続先 URL：家の Immich");
    await userEvent.clear(baseUrl);
    await userEvent.type(baseUrl, "http://immich.example.invalid:2283");
    await userEvent.type(screen.getByLabelText("表示用 URL：家の Immich"), "https://photos.example.invalid");
    await userEvent.type(screen.getByLabelText("新しい API キー：家の Immich"), "s3cret");
    await save();
    await waitFor(() => expect(bodies.some((call) => call.method === "PATCH")).toBe(true));
    expect(bodies.find((call) => call.method === "PATCH")?.body).toEqual({
      base_url: "http://immich.example.invalid:2283",
      public_url: "https://photos.example.invalid",
      api_key: "s3cret",
    });
  });

  it("API キーの欄は空から始まる（既存の鍵を画面に出さない）", async () => {
    stubWithBodies();
    renderDestinations();
    const key = await screen.findByLabelText("新しい API キー：家の Immich");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("type", "password");
  });

  it("空のままなら鍵は送らない（＝いまの鍵を変えない）", async () => {
    const { bodies } = stubWithBodies();
    renderDestinations();
    await save();
    await waitFor(() => expect(bodies.some((call) => call.method === "PATCH")).toBe(true));
    expect(Object.keys(bodies.find((call) => call.method === "PATCH")?.body ?? {})).not.toContain(
      "api_key",
    );
  });

  it("保存に成功したら、鍵の欄を空に戻す", async () => {
    stubWithBodies();
    renderDestinations();
    const key = await screen.findByLabelText("新しい API キー：家の Immich");
    await userEvent.type(key, "s3cret");
    expect(key).toHaveValue("s3cret");
    await save();
    await waitFor(() => expect(key).toHaveValue(""));
  });

  it("同じライブラリかどうかを決めて送れる（§12.3 の epoch の分かれ目）", async () => {
    const { bodies } = stubWithBodies();
    renderDestinations();
    await userEvent.selectOptions(
      await screen.findByLabelText("向き先が同じライブラリかどうか：家の Immich"),
      "no",
    );
    await save();
    await waitFor(() => expect(bodies.some((call) => call.method === "PATCH")).toBe(true));
    expect(bodies.find((call) => call.method === "PATCH")?.body.same_library).toBe(false);
  });

  it("決めていなければ same_library を送らない（サーバに聞かせる）", async () => {
    const { bodies } = stubWithBodies();
    renderDestinations();
    await save();
    await waitFor(() => expect(bodies.some((call) => call.method === "PATCH")).toBe(true));
    expect(Object.keys(bodies.find((call) => call.method === "PATCH")?.body ?? {})).not.toContain(
      "same_library",
    );
  });

  it("失敗はバナーに出す。鍵は出さない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/destinations/d1" && init?.method === "PATCH") {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                error: { code: "same_library_undecided", detail: "", meta: {} },
              }),
              { status: 409 },
            ),
          );
        }
        const body = routes([])[path as keyof ReturnType<typeof routes>] ?? {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderDestinations();
    await userEvent.type(await screen.findByLabelText("新しい API キー：家の Immich"), "s3cret");
    await save();
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("同じライブラリを指しているかどうかを選んでください。");
    expect(banner.textContent).not.toContain("s3cret");
  });

  it("飛んでいる間は押させない（二重に保存しない）", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/destinations/d1" && init?.method === "PATCH") {
          await held;
          return new Response(JSON.stringify({ id: "d1" }), { status: 200 });
        }
        const body = routes([])[path as keyof ReturnType<typeof routes>] ?? {};
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    renderDestinations();
    const button = await screen.findByRole("button", {
      name: "接続の設定を保存する：家の Immich",
    });
    await userEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    release();
    await waitFor(() => expect(button).toBeEnabled());
  });
});
