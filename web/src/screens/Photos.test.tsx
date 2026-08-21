// 写真（§13）。日付でまとめ、1 枚ごとに状態の印を出す。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
          (c) => c.path === "/media?status=unsent&destination_id=d1" && c.method === "GET",
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
          (c) => c.path === "/media?status=unsent&destination_id=d2" && c.method === "GET",
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

  it("タイルの aria-label は rel_path の末尾", async () => {
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
    expect(await screen.findByRole("button", { name: "photo.JPG" })).toBeInTheDocument();
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

// 旧ライブラリ（`screens.test.tsx` の「選んだものの合計」）から移す。
// 表の行チェックボックスではなくグリッドのタイルになったので、`role="row"` や
// 「名前」テキスト入力による絞り込みは成り立たない。**「絞り込みで選択が隠れても
// 合計を保つ」という意味だけを引き継ぎ**、写真の画面が持つ「動画」チップで
// 絞り込みを変える形に書き換える。宛先を選んで確認ダイアログを開くところまでは
// 「送る」画面（後続タスク）の担当なので、ここでは検証しない。
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
