// 送り先（§12.3 / §13）。**送れなかったものを、ここから送り直せる。**
//
// レビュー指摘（Critical #2）: 「設定 › 送り先から送り直せます」と案内しておきながら、
// この画面に送り直す操作が無かった。案内が指す先に実物を置く。

import { render, screen, waitFor } from "@testing-library/react";
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
