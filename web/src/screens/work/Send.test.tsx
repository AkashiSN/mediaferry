// 送る（§13）。**取り消せないので、件数・合計サイズ・送り先を出してから確認する。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { SendScreen, summarise } from "./Send";

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
      expect(screen.getByRole("radio", { name: /写真の画面で選んだもの/ })).toBeChecked(),
    );
  });

  it("確認の前に API を叩かない", async () => {
    // **押しただけでは送らない**（screens.test.tsx が各画面に課していた規則）。
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

  // `screens.test.tsx` から移した（Ruling 2）。`LibraryScreen` を描画するテストでは
  // ないので、`Library.tsx` の回帰試験とは別にここへ置く。
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

  it("「写真の画面で選んだもの」は 1 件ずつ取得して合計を出す", async () => {
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

  // レビュー指摘（Important #2）: 「すべて」と名乗る対象が、応答の上限（200 件）で
  // 黙って切れないこと。
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

  // レビュー指摘（Important #3）: `isMedia` を落とした後も、1 件読めなくても
  // 残りは対象にできること（`Promise.allSettled` で拾う）。
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

describe("送信そのもの", () => {
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

  // `stubApi` は応答を常に 200 で返すので、ここだけ `fetch` を自前で差し替える
  // （`screens.test.tsx` 冒頭のコメントにある「`stubProfiles` と同じやり方」）。
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
          // d2 は開始に失敗する（例: 転送先が応答しない）。
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
  });
});
