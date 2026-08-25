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
    group: null,
    ...overrides,
  };
}

/** 検証の結果（`core/merge/verify.py` の `to_json` と同じ形）。 */
function verification(passed: boolean) {
  return {
    passed,
    route: "concat",
    checks: [
      { name: "duration", verdict: "pass" },
      { name: "size", verdict: passed ? "pass" : "fail" },
    ],
    route_dropped_streams: [],
  };
}

/** この出力を持っているグループ。**つなぐ画面と同じ形**（`group_view`）。 */
function aGroup(overrides: Record<string, unknown> = {}) {
  return {
    id: "g1",
    status: "merged",
    detected_by: "auto",
    input_digest: "d",
    verification: verification(true),
    adopted_at: null,
    superseded_by_id: null,
    profile_changed: false,
    output: { media_file_id: "m1", rel_path: "derived/OUT.MP4", size_bytes: 1, missing: false },
    members: [],
    ...overrides,
  };
}

/** `/send` に着いた先で、渡された `location.state` を文字として出す（往査用）。 */
function SendProbe() {
  const location = useLocation();
  return <div data-testid="send-state">{JSON.stringify(location.state)}</div>;
}

function renderDetail(overrides: Record<string, unknown> = {}, extraRoutes: Record<string, unknown> = {}) {
  // 送った本文も記録する（グループへの操作は `media_ids` を渡すので、叩いた
  // ところまでしか見ないと、何を渡したかが試せない）。
  const bodies: { path: string; body: unknown }[] = [];
  const { calls } = stubApi(
    { "/media/m1": detail(overrides), ...extraRoutes },
    (path, init) => {
      if (init?.body) {
        bodies.push({ path, body: JSON.parse(init.body as string) as unknown });
      }
    },
  );
  render(
    <MemoryRouter initialEntries={["/photos/m1"]}>
      <Routes>
        <Route path="/photos" element={<div>写真一覧</div>} />
        <Route path="/photos/:id" element={<PhotoDetailScreen />} />
        <Route path="/send" element={<SendProbe />} />
      </Routes>
    </MemoryRouter>,
  );
  return { calls, bodies };
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

// つないだ動画への操作は、この画面にある（Phase 11）。つなぐ画面は「まだつないで
// いないもの」だけを出すので、**採用の入口がここに無いと、検証に落ちた動画を送る
// 手段が消える**（`SENDABLE_CLAUSE` は `passed` か `adopted_at` しか見ない）。
describe("つないだ動画への操作", () => {
  const SOURCES = [
    { media_file_id: "s1", rel_path: "library/A.MP4", position: 0, missing: false },
    { media_file_id: "s2", rel_path: "library/B.MP4", position: 1, missing: false },
  ];

  it("検証の結果を、検査ごとに出す", async () => {
    renderDetail({ sources: SOURCES, group: aGroup({ verification: verification(false) }) });
    expect(await screen.findByText(/検証: 不合格/)).toBeInTheDocument();
    expect(screen.getByText(/ファイルの大きさ: 合いません/)).toBeInTheDocument();
  });

  it("検証に落ちた動画は、中身を見て採用できる（ここが唯一の入口）", async () => {
    renderDetail({ sources: SOURCES, group: aGroup({ verification: verification(false) }) });
    await userEvent.click(await screen.findByRole("button", { name: "中身を見て、これを使う" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent(/検証に通っていない/);
  });

  it("検証に通った動画には、採用の操作を出さない（押しても何も変わらない）", async () => {
    renderDetail({ sources: SOURCES, group: aGroup() });
    await screen.findByText(/検証: 合格/);
    expect(screen.queryByRole("button", { name: "中身を見て、これを使う" })).toBeNull();
  });

  it("構成を変える・別々にする・同じ構成でやり直すが、ここから行える", async () => {
    renderDetail({ sources: SOURCES, group: aGroup() });
    for (const label of ["構成を変える", "これは別々", "同じ構成でやり直す"]) {
      expect(await screen.findByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("元のファイルには、グループの操作を出さない", async () => {
    renderDetail({ role: "original", group: null });
    await screen.findByRole("button", { name: "送る" });
    expect(screen.queryByRole("button", { name: "これは別々" })).toBeNull();
    expect(screen.queryByText(/検証:/)).toBeNull();
  });

  it("置き換えられたグループの出力には、操作を出さない（押せても何も起きない）", async () => {
    renderDetail({ sources: SOURCES, group: aGroup({ superseded_by_id: "g2" }) });
    await screen.findByText(/検証: 合格/);
    expect(screen.queryByRole("button", { name: "これは別々" })).toBeNull();
    expect(screen.queryByRole("button", { name: "構成を変える" })).toBeNull();
  });

  it("カメラの種類が変わった組には、使えない理由と次の一手を書く", async () => {
    renderDetail({ sources: SOURCES, group: aGroup({ profile_changed: true }) });
    expect(await screen.findByText(/この結果はもう使えません/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "同じ構成でやり直す" })).toBeInTheDocument();
  });
});

// つなぐ画面から移した錠（Phase 11）。**結合済みの組はつなぐ画面に出ない**ので、
// これらの操作を試せる場所はここしかない。
describe("つなぐ画面から移した操作", () => {
  const SOURCES = [
    { media_file_id: "s1", rel_path: "library/A.MP4", position: 0, missing: false },
    { media_file_id: "s2", rel_path: "library/B.MP4", position: 1, missing: false },
  ];

  /** 実際の `core/merge/verify.py` の `to_json` と同じ形（検査 4 つ）。 */
  function checks(verdicts: [string, string, string, string], passed: boolean) {
    const names = ["duration", "streams", "frames", "size"];
    return {
      passed,
      route: "concat",
      checks: names.map((name, index) => ({ name, verdict: verdicts[index], detail: {} })),
      route_dropped_streams: [],
    };
  }

  it("検査ごとに読める形で出す（内部の名前をそのまま出さない）", async () => {
    renderDetail({
      sources: SOURCES,
      group: aGroup({ verification: checks(["pass", "pass", "inconclusive", "inconclusive"], true) }),
    });
    expect(await screen.findByText(/検証: 合格/)).toBeInTheDocument();
    expect(screen.getByText("長さ: 合っています")).toBeInTheDocument();
    expect(screen.getByText("中身の構成: 合っています")).toBeInTheDocument();
    expect(screen.getByText("コマ数: 確かめられませんでした")).toBeInTheDocument();
    expect(screen.getByText("ファイルの大きさ: 確かめられませんでした")).toBeInTheDocument();
    expect(screen.queryByText(/duration|streams|frames|size/)).toBeNull();
  });

  it("採用は、落ちた検査の名前を理由にして確認を取ってから", async () => {
    const { calls } = renderDetail(
      {
        sources: SOURCES,
        group: aGroup({ verification: checks(["fail", "pass", "fail", "inconclusive"], false) }),
      },
      { "/merge-groups/g1?action=adopt": { id: "g1" } },
    );
    await userEvent.click(await screen.findByRole("button", { name: "中身を見て、これを使う" }));
    // 不合格だった検査の名前を、そのまま理由にする（判定不能は数えない）。
    expect(screen.getByRole("dialog")).toHaveTextContent("長さ / コマ数が合いません");
    expect(calls().some((call) => call.method === "PATCH")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(
        calls().some(
          (call) => call.path === "/merge-groups/g1?action=adopt" && call.method === "PATCH",
        ),
      ).toBe(true),
    );
  });

  it("「同じ構成でやり直す」は確認のあと、全 member で組み直す", async () => {
    const { calls, bodies } = renderDetail(
      {
        sources: SOURCES,
        group: aGroup({
          members: [
            { position: 0, media_file_id: "s1", rel_path: "library/A.MP4" },
            { position: 1, media_file_id: "s2", rel_path: "library/B.MP4" },
          ],
        }),
      },
      { "/merge-groups/g1?action=regroup": { id: "g1" } },
    );
    await userEvent.click(await screen.findByRole("button", { name: "同じ構成でやり直す" }));
    expect(calls().some((call) => call.method === "PATCH")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(
        calls().some(
          (call) => call.path === "/merge-groups/g1?action=regroup" && call.method === "PATCH",
        ),
      ).toBe(true),
    );
    expect(bodies.find((sent) => sent.path.includes("regroup"))?.body).toEqual({
      media_ids: ["s1", "s2"],
    });
  });

  it("結合済みの組を「これは別々」にすると、公開済み 1 件と出す", async () => {
    renderDetail({ sources: SOURCES, group: aGroup() });
    await userEvent.click(await screen.findByRole("button", { name: "これは別々" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("ファイル1 件");
  });

  it("操作のあとは、くわしくを引き直す（検証も採用の印も変わる）", async () => {
    const { calls } = renderDetail(
      { sources: SOURCES, group: aGroup() },
      { "/merge-groups/g1?action=discard": { id: "g1" } },
    );
    await userEvent.click(await screen.findByRole("button", { name: "これは別々" }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().filter((call) => call.path === "/media/m1").length).toBeGreaterThan(1),
    );
  });
});

// 画面は実際の応答（`passed` と `checks[]`）を読む。`core/merge/verify.py` の
// `to_json` はトップレベルに `verdict` も `reason` も書かない。**`passed` が偽の
// ときに「中身を見て、これを使う」を出す**のがここの要（§10 の `adopted_derived`。
// これが無いと、検証に落ちた出力を送る手段が画面から消える）。
describe("検証の結果", () => {
  /** 検証の結果は、つないだ動画のくわしくに出る（つなぐ画面には結合済みが出ない）。 */
  function renderWith(group: Record<string, unknown>) {
    return renderDetail({
      sources: [
        { media_file_id: "s1", rel_path: "library/A.MP4", position: 0, missing: false },
        { media_file_id: "s2", rel_path: "library/B.MP4", position: 1, missing: false },
      ],
      group,
    });
  }

  const MERGED = {
    id: "gv",
    status: "merged",
    detected_by: "auto",
    input_digest: "d",
    adopted_at: null,
    superseded_by_id: null,
    profile_changed: false,
    members: [],
    output: {
      media_file_id: "mo1",
      rel_path: "library/OUT.MP4",
      size_bytes: 2147483648,
      missing: false,
    },
  };

  /** `core/merge/verify.py` の `to_json` と同じ形（検査 4 つ）。 */
  function verification(
    passed: boolean,
    verdicts: [string, string, string, string],
    routeDropped: unknown[] = [],
  ) {
    const names = ["duration", "streams", "frames", "size"];
    return {
      passed,
      route: "concat",
      pipeline_version: 1,
      checks: names.map((name, index) => ({ name, verdict: verdicts[index], detail: {} })),
      dropped_streams: [],
      route_dropped_streams: routeDropped,
      seam_offsets: [600.0],
    };
  }

  it("不合格のときは、どの検査が合わなかったかを出す", async () => {
    renderWith({
      ...MERGED,
      verification: verification(false, ["pass", "fail", "inconclusive", "fail"]),
    });
    await waitFor(() => expect(screen.getByText(/検証: 不合格/)).toBeInTheDocument());
    expect(screen.getByText("長さ: 合っています")).toBeInTheDocument();
    expect(screen.getByText("中身の構成: 合いません")).toBeInTheDocument();
    expect(screen.getByText("コマ数: 確かめられませんでした")).toBeInTheDocument();
    expect(screen.getByText("ファイルの大きさ: 合いません")).toBeInTheDocument();
  });

  it("`passed` が偽なら、採用の操作を出す（`verdict` という欄は無い）", async () => {
    renderWith({
      ...MERGED,
      verification: verification(false, ["fail", "pass", "pass", "pass"]),
    });
    expect(
      await screen.findByRole("button", { name: "中身を見て、これを使う" }),
    ).toBeInTheDocument();
  });

  it("採用済みの組には、もう採用の操作を出さない", async () => {
    renderWith({
      ...MERGED,
      adopted_at: "2026-08-20T00:00:00Z",
      verification: verification(false, ["fail", "pass", "pass", "pass"]),
    });
    await waitFor(() => expect(screen.getByText(/検証: 不合格/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "中身を見て、これを使う" })).toBeNull();
    expect(screen.getByText(/中身を見て採用しました/)).toBeInTheDocument();
  });

  it("組み直された組には、採用の操作を出さない（API が 409 で断る）", async () => {
    renderWith({
      ...MERGED,
      superseded_by_id: "g99",
      verification: verification(false, ["fail", "pass", "pass", "pass"]),
    });
    await waitFor(() => expect(screen.getByText(/検証: 不合格/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "中身を見て、これを使う" })).toBeNull();
  });

  it("経路の都合で運べなかったものがあれば、件数を出す", async () => {
    renderWith({
      ...MERGED,
      verification: verification(false, ["pass", "fail", "pass", "pass"], [
        { codec_type: "data", codec_name: "none" },
      ]),
    });
    await waitFor(() =>
      expect(
        screen.getByText(/つなぎ方の都合で運べなかったものが 1 件あります/),
      ).toBeInTheDocument(),
    );
  });

  it("知らない検査が増えても、内部の名前は出さない", async () => {
    // `verify.py` に検査が足されたときの受け口。**名前をそのまま出さない**（§13）。
    renderWith({
      ...MERGED,
      verification: {
        passed: true,
        route: "concat",
        pipeline_version: 1,
        checks: [{ name: "container_overhead", verdict: "pass", detail: {} }],
        dropped_streams: [],
        route_dropped_streams: [],
        seam_offsets: [],
      },
    });
    await waitFor(() =>
      expect(screen.getByText("そのほかの検査: 合っています")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/container_overhead/)).toBeNull();
  });

  it("検証の記録が無い組には、検証の行を出さない", async () => {
    renderWith({ ...MERGED, verification: null });
    await waitFor(() => expect(screen.getByText("つないだ結果")).toBeInTheDocument());
    expect(screen.queryByText(/検証:/)).toBeNull();
  });
});
