// 1 件のくわしく（§13）。「それが何かを知り、いらなければ消す」がここで完結する。

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../test/api";
import { PhotoDetailScreen } from "./PhotoDetail";

type Source = { media_file_id: string; rel_path: string; position: number; missing: boolean };
type Destination = {
  destination_id: string;
  name: string;
  state: string | null;
  presence: string;
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: "m1",
    role: "derived",
    rel_path: "derived/dji-osmo/DCIM/OUT.MP4",
    size_bytes: 12_345_678,
    kind: "video",
    captured_at: "2026-01-02T03:04:00+09:00",
    captured_at_source: "probe",
    duration_seconds: 125,
    probe_state: "ok",
    missing_at: null,
    sources: [] as Source[],
    destinations: [] as Destination[],
    deletable: true,
    delete_blocked_reason: null,
    delete_frees_sources: true,
    ...overrides,
  };
}

/** `/send` に着いた先で、渡された `location.state` を文字として出す（往査用）。 */
function SendProbe() {
  const location = useLocation();
  return <div data-testid="send-state">{JSON.stringify(location.state)}</div>;
}

function renderDetail(overrides: Record<string, unknown> = {}) {
  const { calls } = stubApi({ "/media/m1": detail(overrides) });
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos" element={<div>写真一覧</div>} />
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
        <Route path="/send" element={<SendProbe />} />
      </Routes>
    </MemoryRouter>,
  );
  return { calls };
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("1 件のくわしく", () => {
  it("何から作られたかを、順番どおりに出す", async () => {
    renderDetail({
      sources: [
        { media_file_id: "s1", rel_path: "library/dji-osmo/DCIM/A.MP4", position: 0, missing: false },
        { media_file_id: "s2", rel_path: "library/dji-osmo/DCIM/B.MP4", position: 1, missing: false },
      ],
    });
    await waitFor(() => expect(screen.getByText("A.MP4")).toBeInTheDocument());
    const names = screen.getAllByRole("link").map((a) => a.textContent);
    expect(names).toEqual(expect.arrayContaining(["A.MP4", "B.MP4"]));
  });

  it("元になったファイルは、逆順に記録されていても position 順に出す", async () => {
    renderDetail({
      sources: [
        { media_file_id: "s2", rel_path: "library/dji-osmo/DCIM/B.MP4", position: 1, missing: false },
        { media_file_id: "s1", rel_path: "library/dji-osmo/DCIM/A.MP4", position: 0, missing: false },
      ],
    });
    await waitFor(() => expect(screen.getByText("A.MP4")).toBeInTheDocument());
    const names = screen
      .getAllByRole("link")
      .map((a) => a.textContent)
      .filter((name) => name === "A.MP4" || name === "B.MP4");
    expect(names).toEqual(["A.MP4", "B.MP4"]);
  });

  it("元になったファイルは、1 件ずつ /photos/{id} へ辿れる", async () => {
    renderDetail({
      sources: [{ media_file_id: "s1", rel_path: "library/dji-osmo/DCIM/A.MP4", position: 0, missing: false }],
    });
    const link = await screen.findByRole("link", { name: "A.MP4" });
    expect(link).toHaveAttribute("href", "/photos/s1");
  });

  it("Immich に入っているものは消せず、理由を出す", async () => {
    renderDetail({ deletable: false, delete_blocked_reason: "Immich に入っている" });
    await waitFor(() => expect(screen.getByRole("button", { name: "消す" })).toBeDisabled());
    expect(screen.getByText(/Immich に入っている/)).toBeInTheDocument();
  });

  it("消す前に確認を出し、承諾したら DELETE する", async () => {
    const { calls } = renderDetail({ deletable: true });
    await userEvent.click(await screen.findByRole("button", { name: "消す" }));
    // **確認ダイアログを挟む**（§13）。押した瞬間には消さない。
    expect(calls().filter((c) => c.method === "DELETE")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() => expect(calls()).toContainEqual({ method: "DELETE", path: "/media/m1" }));
  });

  it("消したら写真タブへ戻る（消したものの画面に留まらない）", async () => {
    renderDetail({ deletable: true });
    await userEvent.click(await screen.findByRole("button", { name: "消す" }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    expect(await screen.findByText("写真一覧")).toBeInTheDocument();
  });

  it.each([
    ["not_sent", "まだ送っていません"],
    ["sending", "送っている最中です"],
    ["present", "Immich に入っています"],
    ["trashed", "Immich のゴミ箱にあります"],
    ["gone", "Immich にはもうありません"],
    ["unknown", "Immich にあるか確かめていません"],
    ["failed", "送れませんでした"],
  ])("宛先ごとの状況を §13 の言葉で出す（%s）", async (presence, expected) => {
    renderDetail({
      destinations: [{ destination_id: "d1", name: "家", state: null, presence }],
    });
    await waitFor(() => expect(screen.getByText(expected)).toBeInTheDocument());
  });

  it("つないだ動画は、元になったファイルの本数を出す", async () => {
    renderDetail({
      sources: [
        { media_file_id: "s1", rel_path: "library/dji-osmo/DCIM/A.MP4", position: 0, missing: false },
        { media_file_id: "s2", rel_path: "library/dji-osmo/DCIM/B.MP4", position: 1, missing: false },
      ],
    });
    expect(await screen.findByText("つないだ動画（2 本から）")).toBeInTheDocument();
  });

  it("消すと元が「まだ送っていない」に戻ることを、現行グループの出力のときだけ予告する", async () => {
    renderDetail({ deletable: true, delete_frees_sources: true, sources: [{ media_file_id: "s1", rel_path: "a/A.MP4", position: 0, missing: false }] });
    await userEvent.click(await screen.findByRole("button", { name: "消す" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("「まだ送っていない」に戻ります");
  });

  it("使っていない出力を消すときは、起きないことを予告しない", async () => {
    renderDetail({
      deletable: true,
      delete_frees_sources: false,
      sources: [{ media_file_id: "s1", rel_path: "a/A.MP4", position: 0, missing: false }],
    });
    await userEvent.click(await screen.findByRole("button", { name: "消す" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).not.toHaveTextContent("「まだ送っていない」に戻ります");
    expect(dialog).toHaveTextContent("元になった 1 件は残ります");
  });

  it("「送る」は既存の送る動線へ渡す", async () => {
    renderDetail({});
    await userEvent.click(await screen.findByRole("button", { name: "送る" }));
    const state = await screen.findByTestId("send-state");
    expect(JSON.parse(state.textContent ?? "null")).toEqual({ ids: ["m1"], destinationIds: [] });
  });

  it("上の帯から写真タブへ戻れる", async () => {
    renderDetail({});
    const link = await screen.findByRole("link", { name: /写真へ/ });
    expect(link).toHaveAttribute("href", "/photos");
  });

  it("内部の相対パスをそのまま出さない", async () => {
    renderDetail({ rel_path: "derived/dji-osmo/DCIM/OUT.MP4" });
    await waitFor(() => expect(screen.getByText("OUT.MP4")).toBeInTheDocument());
    expect(screen.queryByText(/derived\/dji-osmo/)).not.toBeInTheDocument();
  });
});

describe("節の組み立て", () => {
  it("ファイル名は、狭い画面でも箱に収まる形で置く", async () => {
    // **`_` は折り返しの機会にならない。** `DJI_20260817143100_0002_D.MP4` は
    // 1 語として扱われるので、`.ident`（`overflow-wrap: anywhere`）が無いと
    // 狭い画面で画面の外へ流れ出る（実機で確認）。
    renderDetail({ rel_path: "library/dji-osmo/DCIM/DJI_20260817143100_0002_D.MP4" });
    const title = await screen.findByRole("heading", { name: /DJI_20260817143100_0002_D\.MP4/ });
    expect(title).toHaveClass("ident");
  });

  it("節の見出しは、画面の見出しと同じ階層に見えない", async () => {
    // **`.sechead h2` は `.sechead` の子にしか効かない。** 素の `<h2>` は
    // ブラウザ既定の大きさで描かれ、画面の見出し（`h1.page.title-lg`）と
    // ほとんど変わらなくなる。大きさは jsdom で測れないので、**規則が当たる
    // 形になっていること**を見る。
    renderDetail({ role: "derived", sources: [] });
    for (const name of ["宛先ごとの状況", "元になったファイル"]) {
      const heading = await screen.findByRole("heading", { name });
      expect(heading.parentElement).toHaveClass("sechead");
    }
  });

  it("宛先の名前と状況は、同じ塊に入る（どれの状況か読める）", async () => {
    // 名前と状況を兄弟として左右に置くと、狭い画面では状況だけが次の行へ落ち、
    // **どの宛先の状況なのかが読めなくなる**。名前の直下に置く。
    renderDetail({
      destinations: [
        { destination_id: "d1", name: "家の Immich", state: null, presence: "not_sent" },
        { destination_id: "d2", name: "旅行用 Immich", state: "complete", presence: "present" },
      ],
    });
    const name = await screen.findByText("家の Immich");
    const block = name.closest(".grow");
    expect(block).not.toBeNull();
    expect(within(block as HTMLElement).getByText("まだ送っていません")).toBeInTheDocument();
    expect(within(block as HTMLElement).queryByText("Immich に入っています")).toBeNull();
  });
});
