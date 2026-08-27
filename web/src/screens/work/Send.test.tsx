// 送る（§13）。**取り消せないので、件数・合計サイズ・送り先を出してから確認する。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardProvider } from "../../api/dashboard";
import { stubApi } from "../../test/api";
import { SendScreen, capturedRange, mergeMedia, summarise } from "./Send";

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

/** `/`（ホーム）へ渡った `location.state` を画面に出すだけの受け皿。 */
function HomeProbe() {
  const location = useLocation();
  const state = location.state as { jobIds?: string[]; note?: string | null } | null;
  return (
    <div>
      <p data-testid="home-note">{state?.note}</p>
      <p data-testid="home-jobs">{(state?.jobIds ?? []).join(",")}</p>
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

  it("並びは API と同じ（新しい撮影日時が先、同じなら rel_path の大きい方が先）", () => {
    // id と rel_path の大小をわざと逆にする（id: a < b < c、rel_path: c < b < a）。
    // id で比べる実装が残っていたら、この期待値では落ちる。
    const merged = mergeMedia([
      {
        media: [
          { ...m("a", "2026-08-17T09:00:00+09:00"), rel_path: "z-a.JPG" },
          { ...m("c", "2026-08-18T10:00:00+09:00"), rel_path: "x-c.JPG" },
        ],
      },
      { media: [{ ...m("b", "2026-08-18T10:00:00+09:00"), rel_path: "y-b.JPG" }] },
    ]);
    expect(merged.map((media) => media.id)).toEqual(["b", "c", "a"]);
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

  it("確認には、つないだ動画の内訳と撮影日の範囲も渡す", async () => {
    stubApi({
      "/destinations": DESTINATIONS,
      "/media?destination_id=d1&status=unsent&page_size=200": {
        media: [
          {
            id: "m1",
            rel_path: "derived/OUT.MP4",
            kind: "video",
            captured_at: "2026-08-17T14:31:00+09:00",
            size_bytes: 10,
            role: "derived",
          },
          {
            id: "m2",
            rel_path: "library/IMG_0001.JPG",
            kind: "photo",
            captured_at: "2026-02-05T10:00:00+09:00",
            size_bytes: 20,
            role: "original",
          },
        ],
        total: 2,
        page: 1,
        page_size: 200,
      },
    });
    renderSend();
    await userEvent.click(await screen.findByRole("button", { name: /家の Immich/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /内容を確かめる/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /内容を確かめる/ }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/つないだ動画が 1 件/);
    expect(dialog).toHaveTextContent(/2026年2月5日 〜 2026年8月17日/);
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

// 一覧・詳細では組（RAW+JPEG）が 1 タイルにまとまる。**この画面だけ 2 つに
// 割れて戻ると**、送るものの見え方が他の画面と食い違う（`utils/stacks.ts`）。
describe("組（RAW+JPEG）をタイルにまとめる", () => {
  it("両方が対象なら 1 タイルにまとめ、JPG+RAW と名乗る", async () => {
    // **一覧で 1 タイルだったものが、送る画面で 2 つに割れて戻らない。**
    const pair = [
      { id: "j", rel_path: "x/IMG_1.JPG", size_bytes: 3_000_000 },
      { id: "r", rel_path: "x/IMG_1.CR2", size_bytes: 22_000_000 },
    ];
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
      "/media/j": { ...pair[0], kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", stack: { members: pair } },
      "/media/r": { ...pair[1], kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", stack: { members: pair } },
    });
    render(
      <MemoryRouter initialEntries={[{ pathname: "/send", state: { ids: ["j", "r"], destinationIds: [] } }]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("JPG+RAW")).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: /IMG_1/ })).toHaveLength(1);
    // **件数はファイル数のまま**（利用者の「2 枚カウント」）。
    expect(screen.getByText(/2 件 ・/)).toBeInTheDocument();
    // **「はじめの N 件」もタイル数ではなくファイル数で数える。** 1 タイルに
    // まとめても、表示済みの実ファイル数は 2 のまま。
    expect(screen.getByText(/2 件のうち、はじめの 2 件/)).toBeInTheDocument();
  });

  it("相方が対象でなければ、単独のタイルで札も出さない", async () => {
    // JPG は前回すでに送った。CR2 だけが未送信 —— 送るのは CR2 の 1 枚。
    const pair = [
      { id: "j", rel_path: "x/IMG_1.JPG", size_bytes: 3_000_000 },
      { id: "r", rel_path: "x/IMG_1.CR2", size_bytes: 22_000_000 },
    ];
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
      "/media/r": { ...pair[1], kind: "photo", captured_at: "2026-08-18T10:00:00+09:00", stack: { members: pair } },
    });
    render(
      <MemoryRouter initialEntries={[{ pathname: "/send", state: { ids: ["r"], destinationIds: [] } }]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("img", { name: "IMG_1.CR2" });
    expect(screen.queryByText(/RAW/)).not.toBeInTheDocument();
    expect(screen.getByText(/1 件 ・/)).toBeInTheDocument();
  });

  it("「まだ送っていないもの」も組を教えてもらって引く", async () => {
    // **畳ませない**（`collapse=stack`）。畳むと未送信の RAW が送られなくなる。
    const { calls } = stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
      "/media": { media: [], total: 0, page: 1, page_size: 200 },
    });
    render(
      <MemoryRouter initialEntries={[{ pathname: "/send", state: null }]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      const asked = calls().filter((c) => c.path.startsWith("/media?"));
      expect(asked.length).toBeGreaterThan(0);
      expect(asked.every((c) => c.path.includes("stack=members"))).toBe(true);
      expect(asked.every((c) => !c.path.includes("collapse="))).toBe(true);
    });
  });

  // **下見に並べる上限は 16「タイル」で、16「ファイル」ではない。** 組を 1 つに
  // まとめたので、1 タイルが 2 ファイルを表しうる —— 上限いっぱいの 16 タイルは
  // 最大 32 ファイルになる。この単位を取り違えると、下見が減った（あるいは倍に
  // 増えた）ことに誰も気付かない。
  it("下見に並べるのは、はじめの 16 タイル（ファイルではない）", async () => {
    // 17 組 34 ファイル。上限を超える 1 組を用意して、切れる位置を見る。
    const pairs = Array.from({ length: 17 }, (_, index) => [
      { id: `j${index}`, rel_path: `x/IMG_${index}.JPG`, size_bytes: 1024 },
      { id: `r${index}`, rel_path: `x/IMG_${index}.CR2`, size_bytes: 1024 },
    ]);
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
      "/media": {
        // **組ごとに撮影時刻をずらす。** 並びは `captured_at DESC, rel_path DESC` なので、
        // 全部同じ時刻だと組の順が rel_path の文字列比較で決まり、どれが上限の外かが
        // 読みにくくなる。ここでは組 0 がいちばん新しい。
        media: pairs.flatMap((pair, index) =>
          pair.map((row) => ({
            ...row,
            kind: "photo",
            captured_at: `2026-08-18T10:${String(59 - index).padStart(2, "0")}:00+09:00`,
            stack: { members: pair },
          })),
        ),
        total: 34,
        page: 1,
        page_size: 200,
      },
    });
    render(
      <MemoryRouter initialEntries={[{ pathname: "/send", state: null }]}>
        <Routes>
          <Route path="/send" element={<SendScreen />} />
        </Routes>
      </MemoryRouter>,
    );

    // タイルは 16 個。**ファイル数で切っていれば 8 タイル（16 ファイル）になる。**
    await waitFor(() => expect(screen.getAllByRole("img", { name: /IMG_/ })).toHaveLength(16));
    // 並べた 16 タイルが表すファイルは 32 枚（34 枚のうち）。
    expect(screen.getByText(/34 件のうち、はじめの 32 件/)).toBeInTheDocument();
    // 17 組目は上限の外。
    expect(screen.queryByRole("img", { name: "IMG_16.JPG" })).toBeNull();
  });
});

describe("送った結果の 1 文", () => {
  /** `POST /uploads` が返す pair 1 つぶん。 */
  function pair(mediaId: string, destinationId: string, result = "created", reason: string | null = null) {
    return { media_file_id: mediaId, destination_id: destinationId, result, reason };
  }

  // **数えるのはファイルと宛先。** 画面のどこも「件」で数えていて、「組」は
  // RAW+JPEG のスタックを指す語なので、pair の数をそのまま「組」と呼ぶと
  // 1 組 2 枚を 1 宛先へ送っただけで「2 組を作った」と読める。
  it("組（RAW+JPEG）を送っても、枚数で数える", () => {
    const message = summarise([pair("m1", "d1"), pair("m2", "d1")], [], 1);
    expect(message).toBe("2 件を、1 宛先へ送り始めました。");
  });

  // **宛先の数だけ pair が増えても、写真は増えない。** 同じ 1 枚を 2 宛先へ
  // 送ったときに「2 件」と言うと、送った写真の枚数の報告として嘘になる。
  it("同じ写真を複数の宛先へ送っても、二重に数えない", () => {
    const message = summarise([pair("m1", "d1"), pair("m1", "d2")], [], 2);
    expect(message).toBe("1 件を、2 宛先へ送り始めました。");
  });

  it("断られた写真と、開始に失敗した宛先を隠さない", () => {
    const pairs = [pair("m1", "d1"), pair("m2", "d1"), pair("m3", "d1", "rejected", "結合中")];
    expect(summarise(pairs, ["旅行用"], 1)).toContain("送れない写真が 1 件");
    expect(summarise(pairs, ["旅行用"], 1)).toContain("旅行用");
  });

  // 断りも宛先の数だけ増える。**理由は同じでも、枚数は 1 枚。**
  it("断られた写真も、宛先の数で水増ししない", () => {
    const pairs = [pair("m1", "d1", "rejected", "結合中"), pair("m1", "d2", "rejected", "結合中")];
    expect(summarise(pairs, [], 0)).toContain("送れない写真が 1 件");
  });

  // **案内は実在する導線を指す。** 設定 › 送り先のカードに「送り直す」がある
  // （`settings/Destinations.tsx`）。
  it("開始できなかったときは、実在する導線を案内する", () => {
    expect(summarise([pair("m1", "d1")], ["旅行用"], 0)).toContain("設定 › 送り先の「送り直す」");
  });

  // **黙っているのも 1 つの結果。** 断られた写真も失敗した宛先も無いときに、
  // 余計な但し書きを付け足さない。
  it("何も問題が無ければ、余計なことを言わない", () => {
    const message = summarise([pair("m1", "d1"), pair("m1", "d2")], [], 2);
    expect(message).toBe("1 件を、2 宛先へ送り始めました。");
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
        (c) =>
          c.path === "/media?destination_id=d1&status=unsent&page_size=200&stack=members" &&
          c.method === "GET",
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
            <Route path="/" element={<HomeProbe />} />
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

  it("2 段階で進み、成功したらホームへジョブと結果の文を持って移る", async () => {
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
          <Route path="/" element={<HomeProbe />} />
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
    expect(await screen.findByTestId("home-note")).toHaveTextContent(
      "1 件を、1 宛先へ送り始めました。",
    );
    expect(await screen.findByTestId("home-jobs")).toHaveTextContent("job-1");
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
          <Route path="/" element={<HomeProbe />} />
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
    expect(await screen.findByTestId("home-note")).toHaveTextContent("送れない写真が 1 件");
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
          <Route path="/" element={<HomeProbe />} />
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
    expect(await screen.findByTestId("home-jobs")).toHaveTextContent("job-1");
    expect(await screen.findByTestId("home-note")).toHaveTextContent("開始できなかった宛先: 旅行用");
    // **始まった数は、実際に始まった数。** 組が受け付けられただけの宛先を数えると、
    // 同じ 1 文で「2 宛先で始めた」と「1 宛先は始められなかった」を並べることになる。
    expect(await screen.findByTestId("home-note")).toHaveTextContent(
      "1 宛先へ送り始めました",
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

describe("撮影日の幅", () => {
  const at = (id: string, when: string) => ({
    id,
    rel_path: `${id}.JPG`,
    kind: "photo",
    captured_at: when,
    size_bytes: 1,
  });

  it("いちばん古い日といちばん新しい日を返す", () => {
    expect(
      capturedRange([
        at("m1", "2026-08-17T14:31:00+09:00"),
        at("m2", "2026-02-05T10:00:00+09:00"),
        at("m3", "2026-05-01T00:00:00+09:00"),
      ]),
    ).toEqual({ from: "2026-02-05T10:00:00+09:00", to: "2026-08-17T14:31:00+09:00" });
  });

  it("読める値が 1 つも無ければ null（無い日付を作らない）", () => {
    expect(capturedRange([at("m1", "")])).toBeNull();
  });
});

describe("画面の組み立て", () => {
  const EMPTY = { media: [], total: 0, page: 1, page_size: 50 };

  it("「やめる」は行に包まれている（幅いっぱいに伸びない）", async () => {
    // **jsdom は幅を測れない。** 測れるのは構造だけなので、`.wrap` の直下に
    // 置かないことを見る —— 伸びるかどうかはそこで決まる（`styles.css` の
    // `.wrap` は縦並びで、`align-items` の既定が `stretch`）。
    stubApi({ "/destinations": DESTINATIONS, "/media": EMPTY });
    renderSend();
    const cancel = await screen.findByRole("button", { name: /やめる/ });
    expect(cancel.parentElement).toHaveClass("row");
  });

  it("宛先の名前と接続状況は、2 行に分かれる", async () => {
    // `.chip` は横並びなので、名前と状況が 1 行に押し込まれる。**縦並びの派生**を
    // 当てる（`.chip` そのものは写真タブの絞り込みが使うので変えない）。
    stubApi({ "/destinations": DESTINATIONS, "/media": EMPTY });
    renderSend();
    const chip = await screen.findByRole("button", { name: /家の Immich/ });
    expect(chip).toHaveClass("stacked");
  });

  it("「すでにある写真は〜」の説明は、アイコンと同じ行に入る", async () => {
    // `.rowtop` は `flex-wrap: wrap` で、**行に詰めるかを flex-basis で決める**。
    // `.grow` が無いと basis が max-content になり、縮む前に改行される。
    stubApi({ "/destinations": DESTINATIONS, "/media": EMPTY });
    renderSend();
    const note = await screen.findByText(/すでにある写真/);
    expect(note).toHaveClass("grow");
  });
});
