// 送る（§13）。**取り消せないので、件数・合計サイズ・送り先を出してから確認する。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardProvider } from "../../api/dashboard";
import { stubApi } from "../../test/api";
import { SendScreen, mergeMedia, summarise } from "./Send";

const DESTINATIONS = {
  destinations: [
    { id: "d1", name: "家の Immich", enabled: true },
    { id: "d2", name: "旅行用 Immich", enabled: false },
  ],
};

function renderSend(ids?: string[]) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/send", state: ids ? { ids } : undefined }]}>
      <SendScreen />
    </MemoryRouter>,
  );
}

/** `/sending` へ渡った `location.state` を画面に出すだけの受け皿。 */
function SendingProbe() {
  const location = useLocation();
  const state = location.state as { jobIds?: string[]; note?: string | null } | null;
  return (
    <div>
      <p data-testid="sending-note">{state?.note}</p>
      <p data-testid="sending-jobs">{(state?.jobIds ?? []).join(",")}</p>
    </div>
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("宛先ごとの未送信をまとめる", () => {
  const m = (id: string, captured_at: string) => ({
    id,
    rel_path: `${id}.JPG`,
    kind: "photo",
    captured_at,
    size_bytes: 1024,
  });

  it("同じ写真は 1 度だけにする", () => {
    const merged = mergeMedia([
      { media: [m("m1", "2026-08-18T10:00:00+09:00")] },
      { media: [m("m1", "2026-08-18T10:00:00+09:00")] },
    ]);
    expect(merged.map((media) => media.id)).toEqual(["m1"]);
  });

  it("並びは API と同じ（新しい撮影日時が先、同じなら id の大きい方が先）", () => {
    const merged = mergeMedia([
      { media: [m("a", "2026-08-17T09:00:00+09:00"), m("c", "2026-08-18T10:00:00+09:00")] },
      { media: [m("b", "2026-08-18T10:00:00+09:00")] },
    ]);
    expect(merged.map((media) => media.id)).toEqual(["c", "b", "a"]);
  });
});

describe("送る", () => {
  it("休止中の宛先は選べず、理由が出る", async () => {
    stubApi({ "/destinations": DESTINATIONS, "/media": { media: [], total: 0, page: 1, page_size: 50 } });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /旅行用 Immich/ })).toBeDisabled());
    expect(screen.getByText(/休止中なので選べません/)).toBeInTheDocument();
  });

  it("既定は「まだ送っていないもの、すべて」", async () => {
    stubApi({ "/destinations": DESTINATIONS, "/media": { media: [], total: 48, page: 1, page_size: 50 } });
    renderSend();
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /まだ送っていないもの、すべて/ })).toBeChecked(),
    );
  });

  it("写真の画面から来たときは、その選択が既定", async () => {
    // `/media/m1` `/media/m2`（詳細）は `/media`（一覧）と別の資源なので、
    // `stubApi` の厳密一致で正しい形を返せるように個別に登録しておく。
    stubApi({
      "/destinations": DESTINATIONS,
      "/media/m1": { id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "", size_bytes: 1024 },
      "/media/m2": { id: "m2", rel_path: "b.JPG", kind: "photo", captured_at: "", size_bytes: 1024 },
      "/media": { media: [], total: 48, page: 1, page_size: 50 },
    });
    renderSend(["m1", "m2"]);
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /選んだもの/ })).toBeChecked(),
    );
  });

  it("確認の前に API を叩かない", async () => {
    // **押しただけでは送らない**（§13。取り消せない操作は確認を経てから）。
    const { calls } = stubApi({
      "/destinations": DESTINATIONS,
      "/media": { media: [{ id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "", size_bytes: 1024 }], total: 1, page: 1, page_size: 50 },
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("この内容で送りますか");
    expect(calls().some((c) => c.method === "POST")).toBe(false);
  });

  it("確認には件数・合計サイズ・送り先を出す", async () => {
    stubApi({
      "/destinations": DESTINATIONS,
      "/media": { media: [{ id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "", size_bytes: 2048 }], total: 1, page: 1, page_size: 50 },
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("1 件");
    expect(dialog).toHaveTextContent("2 KiB");
    expect(dialog).toHaveTextContent("家の Immich");
  });

  it("送り先を選んでいなければ確認へ進めない", async () => {
    stubApi({ "/destinations": { destinations: [] }, "/media": { media: [], total: 0, page: 1, page_size: 50 } });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeDisabled());
  });
});

describe("送った結果の 1 文", () => {
  it("断られた組と、開始に失敗した宛先を隠さない", () => {
    expect(summarise(3, [{ reason: "結合中" }], ["旅行用"], 1)).toContain("送れない組が 1 件");
    expect(summarise(3, [{ reason: "結合中" }], ["旅行用"], 1)).toContain("旅行用");
  });

  // **案内は実在する導線を指す。** 設定 › 送り先のカードに「送り直す」がある
  // （`settings/Destinations.tsx`）。
  it("開始できなかったときは、実在する導線を案内する", () => {
    expect(summarise(1, [], ["旅行用"], 0)).toContain("設定 › 送り先の「送り直す」");
  });

  // **黙っているのも 1 つの結果。** 断られた組も失敗した宛先も無いときに、
  // 余計な但し書きを付け足さない。
  it("何も問題が無ければ、余計なことを言わない", () => {
    const message = summarise(2, [], [], 2);
    expect(message).toBe("2 組を作り、2 宛先で送信を始めました。");
  });
});

// ブリーフの Step 1 には無いが、preset ごとに叩く API が違うという実装判断
// （§10）そのものを覆うテストが無かったので補う。
describe("対象の解決", () => {
  it("「まだ送っていないもの」は、選んだ宛先の未送信を問い合わせる", async () => {
    const { calls } = stubApi({
      "/destinations": DESTINATIONS,
      "/media": {
        media: [
          { id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 },
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    expect(
      calls().some(
        (c) => c.path === "/media?destination_id=d1&status=unsent&page_size=200" && c.method === "GET",
      ),
    ).toBe(true);
  });

  it("「いちばん新しい撮影日のぶんだけ」は、最新の日付で絞り直す", async () => {
    const { calls } = stubApi({
      "/destinations": DESTINATIONS,
      "/media": {
        media: [
          { id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 },
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
    });
    renderSend();
    await userEvent.click(
      await screen.findByRole("radio", { name: /いちばん新しい撮影日のぶんだけ/ }),
    );
    await waitFor(() =>
      expect(
        calls().some(
          (c) =>
            c.method === "GET" &&
            c.path.includes("captured_from=2026-08-18T00") &&
            c.path.includes("captured_to=2026-08-18T23"),
        ),
      ).toBe(true),
    );
  });

  // `captured_at` は秒より細かい桁を持つことがある（`2026-08-18T10:00:00.123456+09:00`）。
  // **決め打ちの位置で切ると、時差の部分が壊れる** —— 絞り込みの端が別の時刻に
  // なり、その日のはずのものが外れる。
  it("秒より細かい桁があっても、その日の時差で絞る", async () => {
    const { calls } = stubApi({
      "/destinations": DESTINATIONS,
      "/media": {
        media: [
          {
            id: "m1",
            rel_path: "a.JPG",
            kind: "photo",
            captured_at: "2026-08-18T10:00:00.123456+09:00",
            size_bytes: 1024,
          },
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
    });
    renderSend();
    await userEvent.click(
      await screen.findByRole("radio", { name: /いちばん新しい撮影日のぶんだけ/ }),
    );
    await waitFor(() =>
      expect(
        calls().some((c) =>
          c.path.includes(`captured_from=${encodeURIComponent("2026-08-18T00:00:00+09:00")}`),
        ),
      ).toBe(true),
    );
    expect(
      calls().some((c) =>
        c.path.includes(`captured_to=${encodeURIComponent("2026-08-18T23:59:59+09:00")}`),
      ),
    ).toBe(true);
  });

  // 時差の違うカメラが混ざると、**文字列の順と時刻の順がずれる**（`captured_at` は
  // 現地の時差付き）。並びの先頭で選ぶと、古い方の日で絞ってしまう。
  it("時差が混ざっていても、いちばん新しい瞬間の日で絞る", async () => {
    const { calls } = stubApi({
      "/destinations": DESTINATIONS,
      "/media": {
        media: [
          // 文字列では "2026-08-19…" が先に来るが、瞬間は 08-18T23:00+09:00
          // （= 08-18T14:00Z）より **古い** 08-19T00:30+00:00（= 08-19T00:30Z）…
          // ではなく新しいので、時差を無視すると取り違える組み合わせを作る。
          {
            id: "m2",
            rel_path: "b.JPG",
            kind: "photo",
            captured_at: "2026-08-19T02:00:00+09:00",
            size_bytes: 1024,
          },
          {
            id: "m1",
            rel_path: "a.JPG",
            kind: "photo",
            captured_at: "2026-08-19T00:00:00-05:00",
            size_bytes: 1024,
          },
        ],
        total: 2,
        page: 1,
        page_size: 50,
      },
    });
    renderSend();
    await userEvent.click(
      await screen.findByRole("radio", { name: /いちばん新しい撮影日のぶんだけ/ }),
    );
    // 実際の瞬間は m1（08-19T05:00Z）が m2（08-18T17:00Z）より新しい。
    await waitFor(() =>
      expect(
        calls().some((c) =>
          c.path.includes(`captured_from=${encodeURIComponent("2026-08-19T00:00:00-05:00")}`),
        ),
      ).toBe(true),
    );
  });

  it("「選んだもの」は 1 件ずつ取得して合計を出す", async () => {
    stubApi({
      "/destinations": DESTINATIONS,
      "/media/m1": {
        id: "m1",
        rel_path: "a.JPG",
        kind: "photo",
        captured_at: "2026-08-18T10:00:00+09:00",
        size_bytes: 1024,
      },
      "/media/m2": {
        id: "m2",
        rel_path: "b.JPG",
        kind: "photo",
        captured_at: "2026-08-18T11:00:00+09:00",
        size_bytes: 1024,
      },
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
    });
    renderSend(["m1", "m2"]);
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("2 件");
    expect(dialog).toHaveTextContent("2 KiB");
  });

  it("対象があっても、送り先を選んでいなければ確認へ進めない", async () => {
    // 宛先が 2 つとも使えるので、自動選択は効かない（`Photos.tsx` と同じ
    // 「候補が 1 つだけなら黙って使う」規則）。対象は選択済みで届くので、
    // 「対象が無いから押せない」と混同していないことを確かめる。
    stubApi({
      "/destinations": {
        destinations: [
          { id: "d1", name: "家の Immich", enabled: true },
          { id: "d2", name: "旅行用 Immich", enabled: true },
        ],
      },
      "/media/m1": {
        id: "m1",
        rel_path: "a.JPG",
        kind: "photo",
        captured_at: "2026-08-18T10:00:00+09:00",
        size_bytes: 1024,
      },
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
    });
    renderSend(["m1"]);
    await screen.findByText(/1 件のうち、はじめの 1 件/);
    expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeDisabled();
  });

  it("「自分で選ぶ」は写真の画面へ移る", async () => {
    stubApi({ "/destinations": DESTINATIONS, "/media": { media: [], total: 0, page: 1, page_size: 50 } });
    render(
      <MemoryRouter initialEntries={["/send"]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
          <Route path="/photos" element={<p>写真の画面</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /旅行用 Immich/ })).toBeDisabled());
    await userEvent.click(screen.getByRole("radio", { name: /写真を自分で選ぶ/ }));
    expect(await screen.findByText("写真の画面")).toBeInTheDocument();
  });

  // **「すべて」と名乗る対象が、応答の上限（200 件）で黙って切れない**（裁定 20）。
  it("『すべて』が上限で切れているときは、残りがあることを言う", async () => {
    stubApi({
      "/destinations": DESTINATIONS,
      "/media": {
        media: [
          { id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 },
        ],
        // サーバ側には 500 件あるが、1 度に読めるのは 200 件まで（ここでは
        // スタブの都合で 1 件しか返していないが、`total` はそのまま読む）。
        total: 500,
        page: 1,
        page_size: 200,
      },
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    expect(await screen.findByText(/残り 499 件は次にもう一度/)).toBeInTheDocument();
  });

  // **1 件読めなくても、残りは対象にする**（`Promise.allSettled` で拾う）。
  // 1 件でも 404 だと全体が reject する作りだと、**1 件も送れなくなる**。
  it("選んだうち 1 件が読めなくても、残りは対象にする", async () => {
    const m1 = {
      id: "m1",
      rel_path: "a.JPG",
      kind: "photo",
      captured_at: "2026-08-18T10:00:00+09:00",
      size_bytes: 1024,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/media/m1") {
          return Promise.resolve(new Response(JSON.stringify(m1), { status: 200 }));
        }
        if (path === "/media/m2") {
          // m2 は選んだ後に消えた（または読めない）想定。
          return Promise.resolve(new Response(JSON.stringify({ message: "not found" }), { status: 404 }));
        }
        if (path.startsWith("/destinations")) {
          return Promise.resolve(new Response(JSON.stringify(DESTINATIONS), { status: 200 }));
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );
    renderSend(["m1", "m2"]);
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    expect(screen.getByText(/1 件のうち、はじめの 1 件/)).toBeInTheDocument();
    expect(await screen.findByText(/1 件は見つからないので外しました/)).toBeInTheDocument();
  });
});

// **宛先ごとに引く。** 1 宛先ぶんしか引かないと、「まだ送っていないもの、すべて」が
// もう片方の宛先について嘘になる。
describe("宛先を複数選んだときの対象", () => {
  const TWO = {
    destinations: [
      { id: "d1", name: "家の Immich", enabled: true },
      { id: "d2", name: "旅行用 Immich", enabled: true },
    ],
  };
  const page = (media: unknown[], total: number) => ({ media, total, page: 1, page_size: 200 });
  const m = (id: string, captured_at: string) => ({
    id,
    rel_path: `${id}.JPG`,
    kind: "photo",
    captured_at,
    size_bytes: 1024,
  });

  /** 宛先ごとに違う未送信を返す `fetch`。 */
  function stubPerDestination(byDestination: Record<string, unknown>) {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        for (const [id, body] of Object.entries(byDestination)) {
          if (path.startsWith("/media?") && path.includes(`destination_id=${id}`)) {
            return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
          }
        }
        if (path.startsWith("/destinations")) {
          return Promise.resolve(new Response(JSON.stringify(TWO), { status: 200 }));
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );
  }

  it("選んだ宛先すべての未送信を集め、同じ写真は 1 度だけ数える", async () => {
    stubPerDestination({
      d1: page([m("m1", "2026-08-18T10:00:00+09:00"), m("m2", "2026-08-18T09:00:00+09:00")], 2),
      d2: page([m("m2", "2026-08-18T09:00:00+09:00"), m("m3", "2026-08-18T08:00:00+09:00")], 2),
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /家の Immich/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /家の Immich/ }));
    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));

    // m1・m2・m3 の 3 件（m2 を二重に数えない）。1 宛先ぶんなら 2 件になる。
    await waitFor(() => expect(screen.getByText(/3 件のうち、はじめの 3 件/)).toBeInTheDocument());
  });

  it("宛先ごとに問い合わせる（片方だけで済ませない）", async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path.startsWith("/media?")) {
          seen.push(path);
          return Promise.resolve(new Response(JSON.stringify(page([], 0)), { status: 200 }));
        }
        if (path.startsWith("/destinations")) {
          return Promise.resolve(new Response(JSON.stringify(TWO), { status: 200 }));
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /家の Immich/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /家の Immich/ }));
    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));

    await waitFor(() =>
      expect(seen.some((path) => path.includes("destination_id=d2"))).toBe(true),
    );
    expect(seen.some((path) => path.includes("destination_id=d1"))).toBe(true);
  });

  it("宛先が 2 つ以上のときは、残りの件数を数で言わない（重複して数えるので）", async () => {
    stubPerDestination({
      d1: page([m("m1", "2026-08-18T10:00:00+09:00")], 300),
      d2: page([m("m1", "2026-08-18T10:00:00+09:00")], 300),
    });
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /家の Immich/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /家の Immich/ }));
    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));

    expect(
      await screen.findByText(/1 度に送れる分を超えています/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/残り 299 件/)).toBeNull();
  });

  it("宛先を 1 つも選んでいなければ、まず宛先を選ばせる", async () => {
    // 宛先が 2 つとも使えるので自動選択は効かない（`Photos.tsx` と同じ
    // 「候補が 1 つだけなら黙って使う」規則）。
    stubPerDestination({});
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /家の Immich/ })).toBeEnabled());
    // 「すべて」と「いちばん新しい撮影日」の両方が、宛先待ちだと言う。
    expect(screen.getAllByText("宛先を選んでください")).toHaveLength(2);
  });

  it("宛先を選ぶ前の説明は、宛先を単数と決めつけない", async () => {
    stubPerDestination({});
    renderSend();
    await waitFor(() => expect(screen.getByRole("button", { name: /家の Immich/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /家の Immich/ }));
    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));
    await userEvent.click(screen.getByRole("radio", { name: /いちばん新しい撮影日/ }));

    expect(screen.getByRole("radio", { name: /選んだ送り先へまだ送っていないもの/ })).toBeInTheDocument();
  });
});

// **写真の画面から戻ったときに、選んでいた宛先を巻き戻さない。**
describe("写真の画面から戻ったとき", () => {
  it("持ち帰った宛先を選んだ状態で始める", async () => {
    stubApi({
      "/destinations": {
        destinations: [
          { id: "d1", name: "家の Immich", enabled: true },
          { id: "d2", name: "旅行用 Immich", enabled: true },
        ],
      },
      "/media/m1": {
        id: "m1",
        rel_path: "a.JPG",
        kind: "photo",
        captured_at: "2026-08-18T10:00:00+09:00",
        size_bytes: 1024,
      },
      "/media": { media: [], total: 0, page: 1, page_size: 200 },
    });
    render(
      <MemoryRouter
        initialEntries={[{ pathname: "/send", state: { ids: ["m1"], destinationIds: ["d2"] } }]}
      >
        <SendScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /旅行用 Immich/ })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    expect(screen.getByRole("button", { name: /家の Immich/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByText("送り先：旅行用 Immich")).toBeInTheDocument();
  });
});

describe("送信そのもの", () => {
  // **1 本も始まらなくても、枠の数は直す。** 進捗のイベントが出ないので、
  // ここで取り直さないとホームの「N 件をまだ送っていません」が送る前のまま残る。
  it("送ったあと、枠の集計を取り直す", async () => {
    const api = stubApi({
      "/dashboard": {
        media_total: 0,
        destinations: [],
        running_jobs: 0,
        recent_imports: [],
        orphans: 0,
        missing: 0,
        warnings: [],
        merge_candidates: 0,
        merge_review_total: 0,
        unsent_total: 1,
        awaiting_total: 0,
      },
      "/destinations/d1/upload": { job_id: "job-1" },
      "/destinations": DESTINATIONS,
      "/media": {
        media: [{ id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 }],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/uploads": {
        pairs: [{ media_file_id: "m1", destination_id: "d1", result: "created", upload_record_id: "u1", reason: null }],
      },
    });
    render(
      <MemoryRouter initialEntries={["/send"]}>
        <DashboardProvider>
          <Routes>
            <Route path="/send" element={<SendScreen />} />
            <Route path="/sending" element={<SendingProbe />} />
          </Routes>
        </DashboardProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await waitFor(() => expect(api.calls().filter((c) => c.path === "/dashboard")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() =>
      expect(api.calls().filter((c) => c.path === "/dashboard").length).toBeGreaterThan(1),
    );
  });

  it("2 段階で進み、成功したら送信中の画面へジョブと結果の文を持って移る", async () => {
    const api = stubApi({
      "/destinations/d1/upload": { job_id: "job-1" },
      "/destinations": DESTINATIONS,
      "/media": {
        media: [{ id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 }],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/uploads": {
        pairs: [{ media_file_id: "m1", destination_id: "d1", result: "created", upload_record_id: "u1", reason: null }],
      },
    });
    render(
      <MemoryRouter initialEntries={["/send"]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
          <Route path="/sending" element={<SendingProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() => {
      const calls = api.calls();
      expect(calls.filter((c) => c.path === "/uploads" && c.method === "POST")).toHaveLength(1);
      expect(
        calls.filter((c) => c.path === "/destinations/d1/upload" && c.method === "POST"),
      ).toHaveLength(1);
    });
    expect(await screen.findByTestId("sending-note")).toHaveTextContent(
      "1 組を作り、1 宛先で送信を始めました。",
    );
    expect(await screen.findByTestId("sending-jobs")).toHaveTextContent("job-1");
  });

  // ブリーフが「変えてはいけない」と挙げた判断のうち、ブリーフ添付のテストだけでは
  // 壊せないもの（受け付けられた組がある宛先だけ送信を始める）を補う。
  it("受け付けられた組がある宛先だけ送信を始める", async () => {
    const api = stubApi({
      "/destinations/d1/upload": { job_id: "job-1" },
      "/destinations": {
        destinations: [
          { id: "d1", name: "家の Immich", enabled: true },
          { id: "d2", name: "旅行用 Immich", enabled: true },
        ],
      },
      "/media": {
        media: [
          { id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 },
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/uploads": {
        pairs: [
          { media_file_id: "m1", destination_id: "d1", result: "created", upload_record_id: "u1", reason: null },
          { media_file_id: "m1", destination_id: "d2", result: "rejected", upload_record_id: null, reason: "結合中" },
        ],
      },
    });
    render(
      <MemoryRouter initialEntries={["/send"]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
          <Route path="/sending" element={<SendingProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /家の Immich/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /家の Immich/ }));
    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() => {
      const calls = api.calls();
      expect(
        calls.filter((c) => c.path === "/destinations/d1/upload" && c.method === "POST"),
      ).toHaveLength(1);
      expect(calls.some((c) => c.path === "/destinations/d2/upload")).toBe(false);
    });
    expect(await screen.findByTestId("sending-note")).toHaveTextContent("送れない組が 1 件");
  });

  // `stubApi` は応答を常に 200 で返すので、**宛先ごとに成否を変えたいここだけ**
  // `fetch` を自前で差し替える。
  it("一部の宛先で開始に失敗しても、成功した分は進める", async () => {
    const destinations = {
      destinations: [
        { id: "d1", name: "家の Immich", enabled: true },
        { id: "d2", name: "旅行用 Immich", enabled: true },
      ],
    };
    const media = {
      media: [
        { id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    };
    const uploads = {
      pairs: [
        { media_file_id: "m1", destination_id: "d1", result: "created", upload_record_id: "u1", reason: null },
        { media_file_id: "m1", destination_id: "d2", result: "created", upload_record_id: "u2", reason: null },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/destinations/d1/upload") {
          return Promise.resolve(new Response(JSON.stringify({ job_id: "job-1" }), { status: 200 }));
        }
        if (path === "/destinations/d2/upload") {
          // d2 は開始に失敗する（例: 送り先が応答しない）。
          return Promise.resolve(new Response(JSON.stringify({ message: "unreachable" }), { status: 502 }));
        }
        if (path === "/uploads") {
          return Promise.resolve(new Response(JSON.stringify(uploads), { status: 200 }));
        }
        if (path.startsWith("/destinations")) {
          return Promise.resolve(new Response(JSON.stringify(destinations), { status: 200 }));
        }
        if (path.startsWith("/media")) {
          return Promise.resolve(new Response(JSON.stringify(media), { status: 200 }));
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );

    render(
      <MemoryRouter initialEntries={["/send"]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
          <Route path="/sending" element={<SendingProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /家の Immich/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /家の Immich/ }));
    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    // **1 件失敗しても、d1 の送信は始まっている**（全部やり直しにしない）。
    expect(await screen.findByTestId("sending-jobs")).toHaveTextContent("job-1");
    expect(await screen.findByTestId("sending-note")).toHaveTextContent("開始できなかった宛先: 旅行用");
    // **始まった数は、実際に始まった数。** 組が受け付けられただけの宛先を数えると、
    // 同じ 1 文で「2 宛先で始めた」と「1 宛先は始められなかった」を並べることになる。
    expect(await screen.findByTestId("sending-note")).toHaveTextContent(
      "1 宛先で送信を始めました",
    );
  });
});

// 送ったあとで取り消せない以上、**確認に出した内容と、実際に送るものが同じ**で
// なければならない。宛先を選び直すと対象は読み直しになるので、その間に確認へ
// 進ませない・開いている確認は閉じる。
describe("確認の内容と、実際に送るもの", () => {
  const TWO_ENABLED = {
    destinations: [
      { id: "d1", name: "家の Immich", enabled: true },
      { id: "d2", name: "旅行用 Immich", enabled: true },
    ],
  };
  const MEDIA = {
    media: [
      { id: "m1", rel_path: "a.JPG", kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", size_bytes: 1024 },
    ],
    total: 1,
    page: 1,
    page_size: 50,
  };

  it("宛先を選び直したら、開いている確認は閉じる", async () => {
    stubApi({ "/destinations": TWO_ENABLED, "/media": MEDIA });
    renderSend();
    await userEvent.click(await screen.findByRole("button", { name: /家の Immich/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("対象を読み直している間は、確認へ進めない", async () => {
    let release: (() => void) | undefined;
    let mediaCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path.startsWith("/media")) {
          mediaCalls += 1;
          const body = new Response(JSON.stringify(MEDIA), { status: 200 });
          if (mediaCalls === 1) {
            return Promise.resolve(body);
          }
          // 2 巡目（宛先を足したあと）は、テストが放すまで返さない。
          return new Promise<Response>((resolve) => {
            const previous: (() => void) | undefined = release;
            release = () => {
              previous?.();
              resolve(new Response(JSON.stringify(MEDIA), { status: 200 }));
            };
          });
        }
        if (path.startsWith("/destinations")) {
          return Promise.resolve(new Response(JSON.stringify(TWO_ENABLED), { status: 200 }));
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );
    renderSend();
    await userEvent.click(await screen.findByRole("button", { name: /家の Immich/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());

    await userEvent.click(screen.getByRole("button", { name: /旅行用 Immich/ }));
    // 前の宛先ぶんの一覧はまだ画面に残っているが、**確認に出せる内容ではない**。
    expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeDisabled();

    release?.();
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
  });
});
