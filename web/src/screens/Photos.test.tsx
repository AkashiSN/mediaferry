// 写真（§13）。日付でまとめ、1 枚ごとに状態の印を出す。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../test/api";
import { PhotosScreen, groupByDate } from "./Photos";

const media = (id: string, captured_at: string, extra = {}) => ({
  id,
  rel_path: `library/x/${id}.JPG`,
  kind: "photo",
  captured_at,
  size_bytes: 1024,
  ...extra,
});

describe("日付のまとめ", () => {
  it("同じ日を 1 つにまとめ、API の並びを崩さない", () => {
    const rows = [
      media("a", "2026-08-18T15:12:00+09:00"),
      media("b", "2026-08-18T14:03:00+09:00"),
      media("c", "2026-08-17T09:12:00+09:00"),
    ];
    expect(groupByDate(rows).map((g) => g.items.map((m) => m.id))).toEqual([["a", "b"], ["c"]]);
  });

  it("撮影日時が読めない行も落とさない", () => {
    // **落とすと、画面の件数と API の total が食い違う。**
    expect(groupByDate([media("a", "")]).flatMap((g) => g.items)).toHaveLength(1);
  });
});

describe("写真の画面", () => {
  beforeEach(() => {
    document.cookie = "XSRF-TOKEN=token; path=/";
  });
  afterEach(() => vi.restoreAllMocks());

  it("宛先ごとの絞り込みには destination_id を必ず付ける", async () => {
    const { calls } = stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /まだ送っていない/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /まだ送っていない/ }));
    await waitFor(() =>
      expect(
        calls().some(
          (c) => c.path === "/media?status=unsent&destination_id=d1&page_size=200" && c.method === "GET",
        ),
      ).toBe(true),
    );
  });

  it("選んだものは、絞り込みで隠れても覚える", async () => {
    // **表示中の行から合計を出さない**（隠した分が抜けて確認の数字が食い違う）。
    stubApi({
      "/media": {
        media: [media("a", "2026-08-18T14:03:00+09:00", { size_bytes: 2048 })],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /a\.JPG/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /a\.JPG/ }));
    expect(screen.getByText(/1 件を選択中/)).toBeInTheDocument();
    expect(screen.getByText(/2 KiB/)).toBeInTheDocument();
  });

  it("当てはまるものが無ければ、そう書く", async () => {
    stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByText(/当てはまる写真はありません/)).toBeInTheDocument(),
    );
  });

  // 宛先が 0 件のときの決定（ブリーフには無い。ここで自分で決めた）：
  // 宛先ごとの絞り込みは意味を作れないので、その 3 つのチップを押せなくする。
  it("宛先が無ければ、宛先ごとの絞り込みは選べない", async () => {
    stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: /まだ送っていない/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /確認が要る/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /送信済み/ })).toBeDisabled();
    // 宛先を伴わない絞り込みまでは巻き込まない。
    expect(screen.getByRole("button", { name: "すべて" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "動画" })).not.toBeDisabled();
  });

  it("宛先が 2 つ以上のときは、選ぶまで宛先ごとの絞り込みを問い合わせない", async () => {
    const { calls } = stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 50 },
      "/destinations": {
        destinations: [
          { id: "d1", name: "家", enabled: true },
          { id: "d2", name: "職場", enabled: true },
        ],
      },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /まだ送っていない/ }));
    // 宛先の選択肢が、絞り込みチップの隣に出る。
    expect(await screen.findByRole("button", { name: "家" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "職場" })).toBeInTheDocument();
    expect(screen.getByText(/宛先を選んでください/)).toBeInTheDocument();
    // 選ぶまでは、宛先を伴わない要求を送らない（400 を避ける）。
    expect(calls().some((c) => c.path.includes("status="))).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "職場" }));
    await waitFor(() =>
      expect(
        calls().some(
          (c) => c.path === "/media?status=unsent&destination_id=d2&page_size=200" && c.method === "GET",
        ),
      ).toBe(true),
    );
    // 選ばなかった方の宛先では問い合わせない。
    expect(calls().some((c) => c.path.includes("destination_id=d1"))).toBe(false);
  });

  it("宛先ごとの絞り込みが効いているときだけ、状態の印を出す", async () => {
    stubApi({
      "/media": {
        media: [media("a", "2026-08-18T14:03:00+09:00")],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    });
    const { container } = render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await screen.findByRole("button", { name: /a\.JPG/ });
    // 「すべて」では宛先ごとの状態が定まらないので、印を出さない。
    expect(container.querySelector(".tile .mark")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /送信済み/ }));
    await waitFor(() => expect(container.querySelector(".tile .mark.sent")).not.toBeNull());
  });

  it("選ぶ丸の aria-label は rel_path の末尾", async () => {
    stubApi({
      "/media": {
        media: [
          media("a", "2026-08-18T14:03:00+09:00", { rel_path: "library/nested/dir/photo.JPG" }),
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    // ディレクトリ部分を含まない、ファイル名だけの名前で掴める。
    expect(await screen.findByRole("button", { name: "選ぶ：photo.JPG" })).toBeInTheDocument();
  });

  it("動画は長さを右下に出す", async () => {
    stubApi({
      "/media": {
        media: [
          media("v", "2026-08-18T14:03:00+09:00", {
            kind: "video",
            rel_path: "library/x/v.MP4",
            duration_seconds: 125,
          }),
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("2:05")).toBeInTheDocument();
  });

  it("写真には長さを出さない", async () => {
    // `duration_seconds` は動画の見送り判定などで写真にも入りうる列だが、
    // 意味が無いので写真では出さない。
    stubApi({
      "/media": {
        media: [
          media("a", "2026-08-18T14:03:00+09:00", {
            rel_path: "library/x/a.JPG",
            duration_seconds: 125,
          }),
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await screen.findByRole("button", { name: /a\.JPG/ });
    expect(screen.queryByText("2:05")).toBeNull();
  });
});

// **絞り込みで隠れても、選んだものの合計は変わらない。** 表示中のタイルから
// 合計を出すと、絞り込みで隠した分が抜けて、確認に出す数字が実際と食い違う。
// 宛先を選んで確認ダイアログを開くところは `work/Send.tsx` の担当なので、
// ここでは見ない。
describe("選んだものの合計", () => {
  it("絞り込みで隠れても、選択と合計は保つ", async () => {
    const photos = {
      media: [
        media("big", "2026-08-17T09:00:00+09:00", {
          size_bytes: 30 * 1024 ** 3,
          rel_path: "library/x/big.JPG",
        }),
      ],
      total: 1,
      page: 1,
      page_size: 50,
    };
    const videos = {
      media: [
        media("small", "2026-08-18T09:00:00+09:00", {
          kind: "video",
          size_bytes: 1024 ** 2,
          rel_path: "library/x/small.MP4",
        }),
      ],
      total: 1,
      page: 1,
      page_size: 50,
    };
    let page = photos;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path.startsWith("/media")) {
          return Promise.resolve(new Response(JSON.stringify(page), { status: 200 }));
        }
        if (path.startsWith("/destinations")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({ destinations: [{ id: "d1", name: "家", enabled: true }] }),
              { status: 200 },
            ),
          );
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );

    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /big\.JPG/ }));
    // 絞り込みを「動画」に変える（写真の big.JPG が隠れる）。
    page = videos;
    await userEvent.click(screen.getByRole("button", { name: "動画" }));
    await userEvent.click(await screen.findByRole("button", { name: /small\.MP4/ }));

    // 隠れた big.JPG ぶんの 30 GiB を数え落とさない。
    expect(screen.getByText(/2 件を選択中/)).toBeInTheDocument();
    expect(screen.getByText(/30 GiB/)).toBeInTheDocument();
  });
});

// **1 度に読む件数を必ず渡す。** 渡さないと API の既定（50 件）で切れ、そのうえ
// 「すべて：50 件」と断言してしまう。
describe("読み込む件数と、切れていることの表示", () => {
  beforeEach(() => {
    document.cookie = "XSRF-TOKEN=token; path=/";
  });
  afterEach(() => vi.restoreAllMocks());

  it("宛先を伴わない絞り込みでも、1 度に読む件数を渡す", async () => {
    const { calls } = stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 200 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/media?page_size=200" && c.method === "GET")).toBe(
        true,
      ),
    );
  });

  it("件数は、読めた分とサーバ側の総数の両方を出す", async () => {
    stubApi({
      "/media": {
        media: [
          media("a", "2026-08-18T15:12:00+09:00"),
          media("b", "2026-08-18T14:03:00+09:00"),
        ],
        total: 3421,
        page: 1,
        page_size: 200,
      },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    // **「すべて：2 件」と断言しない。** 3421 件のうち 2 件しか読めていない。
    expect(await screen.findByText("すべて：2 / 3421 件")).toBeInTheDocument();
  });

  // **1 度に読むのは 200 件まで。** 検索が無いと、それより古いものはどの画面からも
  // 辿れない（API は `q` を受け付ける）。
  it("ファイル名でさがせる", async () => {
    const { calls } = stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 200 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.type(await screen.findByLabelText("ファイル名でさがす"), "DSC_0431");
    await userEvent.click(screen.getByRole("button", { name: "さがす" }));

    await waitFor(() =>
      expect(calls().some((c) => c.path.includes("q=DSC_0431"))).toBe(true),
    );
  });

  it("さがしている言葉は、絞り込みを変えても残る", async () => {
    const { calls } = stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 200 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter initialEntries={["/photos?q=DSC"]}>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /動画/ }));
    await waitFor(() =>
      expect(calls().some((c) => c.path.includes("kind=video") && c.path.includes("q=DSC"))).toBe(
        true,
      ),
    );
  });

  // 200 件で切れたまま次が無いと、**古いものはどの画面からも開けない**。
  it("上限で切れているときは、次の 200 件へ進める", async () => {
    const { calls } = stubApi({
      "/media": {
        media: [media("a", "2026-08-18T15:12:00+09:00")],
        total: 3421,
        page: 1,
        page_size: 200,
      },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "次の 200 件" }));

    await waitFor(() => expect(calls().some((c) => c.path.includes("page=2"))).toBe(true));
  });

  it("1 ページ目では、前へは戻れない", async () => {
    stubApi({
      "/media": {
        media: [media("a", "2026-08-18T15:12:00+09:00")],
        total: 3421,
        page: 1,
        page_size: 200,
      },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: "前の 200 件" })).toBeDisabled();
  });

  it("全部が 1 ページに収まっているときは、ページ送りを出さない", async () => {
    stubApi({
      "/media": {
        media: [media("a", "2026-08-18T15:12:00+09:00")],
        total: 1,
        page: 1,
        page_size: 200,
      },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await screen.findByText("すべて：1 / 1 件");
    expect(screen.queryByRole("button", { name: "次の 200 件" })).toBeNull();
  });

  // **前のページの行に、新しいページの番号を付けない。** 応答が返るまでは
  // 前の 200 件が並んだままなので、URL の `page` で数えると
  // 「201–400 / 250 件」のように総数を超えた案内が出る。
  it("次のページを読んでいる間は、いま出している行の番号を出す", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const first = {
      media: [media("a", "2026-08-18T15:12:00+09:00")],
      total: 250,
      page: 1,
      page_size: 200,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path.includes("page=2")) {
          await held;
          return new Response(
            JSON.stringify({ ...first, page: 2, media: [media("b", "2026-08-17T15:12:00+09:00")] }),
            { status: 200 },
          );
        }
        if (path.startsWith("/media")) {
          return new Response(JSON.stringify(first), { status: 200 });
        }
        return new Response(JSON.stringify({ destinations: [] }), { status: 200 });
      }),
    );
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("1–1 / 250 件")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "次の 200 件" }));

    // まだ 1 ページ目の行が並んでいる。番号もその行のもの。
    expect(screen.getByText("1–1 / 250 件")).toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.getByText("201–201 / 250 件")).toBeInTheDocument());
  });

  it("宛先を変えたら、1 ページ目に戻す", async () => {
    // 3 ページ目のまま別の宛先へ移ると、当てはまるものが 1 ページ分しか
    // 無いときに空の一覧だけが出る。
    const { calls } = stubApi({
      "/media": { media: [], total: 3421, page: 3, page_size: 200 },
      "/destinations": {
        destinations: [
          { id: "d1", name: "家", enabled: true },
          { id: "d2", name: "旅行", enabled: true },
        ],
      },
    });
    render(
      <MemoryRouter initialEntries={["/photos?status=failed&destination_id=d1&page=3"]}>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "旅行" }));

    await waitFor(() =>
      expect(calls().some((c) => c.path.includes("destination_id=d2"))).toBe(true),
    );
    expect(calls().some((c) => c.path.includes("destination_id=d2") && c.path.includes("page=3"))).toBe(
      false,
    );
  });

  // 絞り込みを変えた瞬間に印だけが変わると、送信済みの 200 枚に赤い × が付く。
  it("絞り込みを変えて読んでいる間は、前の行に新しい印を付けない", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const path = input.replace(/^\/api/, "");
        if (path.includes("status=failed")) {
          await held;
          return new Response(
            JSON.stringify({ media: [], total: 0, page: 1, page_size: 200 }),
            { status: 200 },
          );
        }
        if (path.startsWith("/media")) {
          return new Response(
            JSON.stringify({
              media: [media("a", "2026-08-18T15:12:00+09:00")],
              total: 1,
              page: 1,
              page_size: 200,
            }),
            { status: 200 },
          );
        }
        return new Response(
          JSON.stringify({ destinations: [{ id: "d1", name: "家", enabled: true }] }),
          { status: 200 },
        );
      }),
    );
    const { container } = render(
      <MemoryRouter initialEntries={["/photos?status=sent&destination_id=d1"]}>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(container.querySelector(".tile .mark.sent")).not.toBeNull());

    await userEvent.click(screen.getByRole("button", { name: "送れなかった" }));

    expect(container.querySelector(".tile .mark.failed")).toBeNull();
    release();
  });

  it("絞り込みを変えたら、1 ページ目に戻す", async () => {
    // 3 ページ目のまま別の絞り込みへ移ると、当てはまるものが 1 ページ分しか
    // 無いときに「ありません」と出る。
    const { calls } = stubApi({
      "/media": { media: [], total: 3421, page: 3, page_size: 200 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter initialEntries={["/photos?page=3"]}>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /動画/ }));
    await waitFor(() =>
      expect(calls().some((c) => c.path.includes("kind=video"))).toBe(true),
    );
    expect(calls().some((c) => c.path.includes("kind=video") && c.path.includes("page="))).toBe(
      false,
    );
  });

  it("宛先を選ぶ前は、まだ読んでいない総数を出さない", async () => {
    stubApi({
      "/media": { media: [], total: 3421, page: 1, page_size: 200 },
      "/destinations": {
        destinations: [
          { id: "d1", name: "家", enabled: true },
          { id: "d2", name: "職場", enabled: true },
        ],
      },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /まだ送っていない/ }));
    await screen.findByText(/宛先を選んでください/);
    expect(screen.queryByText(/3421/)).toBeNull();
  });
});

// **送信の失敗が見える唯一の絞り込み。** 失敗した記録は「まだ送っていない」にも
// 「送信済み」にも入らない（`docs/design.md` §10）。
describe("送れなかったものの絞り込み", () => {
  beforeEach(() => {
    document.cookie = "XSRF-TOKEN=token; path=/";
  });
  afterEach(() => vi.restoreAllMocks());

  it("「送れなかった」で絞ると、その宛先の失敗を問い合わせる", async () => {
    const { calls } = stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 200 },
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "送れなかった" }));
    await waitFor(() =>
      expect(
        calls().some(
          (c) => c.path === "/media?status=failed&destination_id=d1&page_size=200" && c.method === "GET",
        ),
      ).toBe(true),
    );
  });

  it("送れなかったものには、その印を出す", async () => {
    stubApi({
      "/media": {
        media: [media("a", "2026-08-18T14:03:00+09:00")],
        total: 1,
        page: 1,
        page_size: 200,
      },
      "/destinations": { destinations: [{ id: "d1", name: "家", enabled: true }] },
    });
    const { container } = render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "送れなかった" }));
    await waitFor(() => expect(container.querySelector(".tile .mark.failed")).not.toBeNull());
  });

  it("宛先が無ければ、「送れなかった」も選べない", async () => {
    stubApi({
      "/media": { media: [], total: 0, page: 1, page_size: 200 },
      "/destinations": { destinations: [] },
    });
    render(
      <MemoryRouter>
        <PhotosScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: "送れなかった" })).toBeDisabled();
  });
});

// **「写真を自分で選ぶ」で往復しても、選んだ宛先を落とさない。**
describe("送るへ戻すとき", () => {
  beforeEach(() => {
    document.cookie = "XSRF-TOKEN=token; path=/";
  });
  afterEach(() => vi.restoreAllMocks());

  /** `/send` へ渡った `location.state` を画面に出すだけの受け皿。 */
  function SendProbe() {
    const location = useLocation();
    const state = location.state as { ids?: string[]; destinationIds?: string[] } | null;
    return (
      <div>
        <p data-testid="send-ids">{(state?.ids ?? []).join(",")}</p>
        <p data-testid="send-destinations">{(state?.destinationIds ?? []).join(",")}</p>
      </div>
    );
  }

  it("選んだ写真と一緒に、絞り込んでいた宛先も持って帰る", async () => {
    stubApi({
      "/media": {
        media: [media("a", "2026-08-18T14:03:00+09:00")],
        total: 1,
        page: 1,
        page_size: 200,
      },
      "/destinations": {
        destinations: [
          { id: "d1", name: "家", enabled: true },
          { id: "d2", name: "職場", enabled: true },
        ],
      },
    });
    render(
      <MemoryRouter initialEntries={["/photos?status=unsent&destination_id=d2"]}>
        <Routes>
          <Route path="/photos" element={<PhotosScreen />} />
          <Route path="/send" element={<SendProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /a\.JPG/ }));
    await userEvent.click(screen.getByRole("button", { name: "送る" }));
    expect(await screen.findByTestId("send-ids")).toHaveTextContent("a");
    expect(screen.getByTestId("send-destinations")).toHaveTextContent("d2");
  });
});
