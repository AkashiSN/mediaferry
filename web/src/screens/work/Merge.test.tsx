// つなぐ（§13）。**なぜまとまったかが分かるようにする。**

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { failureReason, MergeScreen } from "./Merge";

// **2 パートのサイズをわざと変える。** 両方同じ大きさだと、パートごとの表示が
// 2 要素とも同じ文字列になり `getByText` が「複数一致」で落ちる。この GROUP の
// サイズが、パートサイズの表示を確かめる実データになっている。
const GROUP = {
  id: "g1",
  status: "detected",
  detected_by: "auto",
  input_digest: "d",
  verification: null,
  superseded_by_id: null,
  output: null,
  members: [
    {
      position: 0,
      media_file_id: "m1",
      rel_path: "library/DJI_0001.MP4",
      size_bytes: 4294967296, // 4 GiB
      duration_seconds: 600,
      captured_at: "2026-08-18T14:03:00+09:00",
    },
    {
      position: 1,
      media_file_id: "m2",
      rel_path: "library/DJI_0002.MP4",
      size_bytes: 1610612736, // 1.5 GiB（合計 5.5 GiB）
      duration_seconds: 600,
      captured_at: "2026-08-18T14:13:00+09:00",
    },
  ],
};

/**
 * 実際の `core/merge/verify.py` の `to_json` が返す形（`passed` / `route` /
 * `checks[]`）。**トップレベルに `verdict` も `reason` も無い。** 画面が読むのは
 * `passed` と `checks[]` で、`db/selection.py` の `SENDABLE_CLAUSE` が見ているのも
 * `passed` である。
 */
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
    checks: names.map((name, index) => ({
      name,
      verdict: verdicts[index],
      detail: {},
    })),
    dropped_streams: [],
    route_dropped_streams: routeDropped,
    seam_offsets: [600.0],
  };
}

const ROUTES = {
  "/merge-groups?status=skipped": { groups: [] },
  "/media": { media: [] },
  "/media/stale-derived": { stale: [] },
};

/** `/send` へ渡った `location.state` を画面に出すだけの受け皿。 */
function SendProbe() {
  const location = useLocation();
  const state = location.state as { ids?: string[] } | null;
  return <p data-testid="send-ids">{(state?.ids ?? []).join(",")}</p>;
}

/** リクエストの本文（JSON）を記録する `stubApi` の代役。 */
function stubApiWithBodies(routes: Record<string, unknown>) {
  const bodies: { path: string; body: unknown }[] = [];
  const api = stubApi(routes, (path, init) => {
    if (init?.body) {
      bodies.push({ path, body: JSON.parse(init.body as string) as unknown });
    }
  });
  return { ...api, bodies };
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("つなぐ", () => {
  it("画面の名前は「つなぐ」（内部の名前を出さない）", async () => {
    stubApi({ ...ROUTES, "/merge-groups": { groups: [] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("region", { name: "つなぐ" })).toBeInTheDocument();
  });

  it("なぜこの並びなのかを、構成とギャップで出す", async () => {
    stubApi({ ...ROUTES, "/merge-groups": { groups: [GROUP] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/2 つに分かれています/)).toBeInTheDocument());
    expect(screen.getByText(/DJI_0001\.MP4/)).toBeInTheDocument();
    // **パートごとのサイズを実データで見る。** 合計も一緒に検証する
    // （`totalBytes` が 0 を返す変異も、ここで一緒に落とせる）。
    expect(screen.getByText(/4 GiB/)).toBeInTheDocument();
    expect(screen.getByText(/1\.5 GiB/)).toBeInTheDocument();
    expect(screen.getByText(/合計 5\.5 GiB/)).toBeInTheDocument();
    // `totalMinutes` が 0 を返す変異はここで落ちる（600 + 600 秒 = 20 分）。
    expect(screen.getByText(/約 20 分/)).toBeInTheDocument();
  });

  it("つなぎ目に空白が無ければ、同じ 1 本と判断した理由を出す（警告にしない）", async () => {
    // GROUP はつなぎ目の空白が 0 秒。**警告色にしない**ことも判断のうち。
    stubApi({ ...ROUTES, "/merge-groups": { groups: [GROUP] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/連番が続いていて/)).toBeInTheDocument());
    expect(screen.queryByText(/別の撮影かもしれない/)).not.toBeInTheDocument();
  });

  it("つなぎ目に空白があれば、確かめてから決めるよう警告する", async () => {
    const gapped = {
      ...GROUP,
      id: "g4",
      members: [
        GROUP.members[0],
        { ...GROUP.members[1], captured_at: "2026-08-18T14:20:00+09:00" }, // 7 分後（600 秒の再生が終わってなお空白）
      ],
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [gapped] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/別の撮影かもしれない/)).toBeInTheDocument());
    expect(screen.queryByText(/連番が続いていて/)).not.toBeInTheDocument();
  });

  it("空白はパート間でいちばん大きいものを出す（3 パート、Math.max）", async () => {
    // pair(0,1) の空白は 300 秒、pair(1,2) の空白は 30 秒。**最後の値ではなく最大値**を
    // 出すこと、かつ値そのもの（300）が正しいことを見る（`gap * 3` のような変異も
    // ここで落ちる）。
    const threeParts = {
      ...GROUP,
      id: "g6",
      members: [
        {
          position: 0,
          media_file_id: "p0",
          rel_path: "library/P0.MP4",
          size_bytes: 1073741824,
          duration_seconds: 300,
          captured_at: "2026-08-18T14:00:00+09:00",
        },
        {
          position: 1,
          media_file_id: "p1",
          rel_path: "library/P1.MP4",
          size_bytes: 1073741824,
          duration_seconds: 300,
          captured_at: "2026-08-18T14:10:00+09:00", // 前パートの終端 14:05:00 から 300 秒後
        },
        {
          position: 2,
          media_file_id: "p2",
          rel_path: "library/P2.MP4",
          size_bytes: 1073741824,
          duration_seconds: 300,
          captured_at: "2026-08-18T14:15:30+09:00", // 前パートの終端 14:15:00 から 30 秒後
        },
      ],
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [threeParts] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByText(/つなぎ目に 300 秒の空白があります/)).toBeInTheDocument(),
    );
  });

  it("member の並びは position 順にする（API の配列順を信用しない）", async () => {
    const swapped = {
      ...GROUP,
      id: "g5",
      members: [
        // 配列の並びは position と逆順（position:1 を先頭に置く）。
        // `ordered()` が並べ替えなければ、レンダー順は SECOND → FIRST になる。
        { ...GROUP.members[0], position: 1, media_file_id: "mA", rel_path: "library/SECOND.MP4" },
        { ...GROUP.members[1], position: 0, media_file_id: "mB", rel_path: "library/FIRST.MP4" },
      ],
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [swapped] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/FIRST\.MP4/)).toBeInTheDocument());
    const names = screen.getAllByText(/(FIRST|SECOND)\.MP4/).map((el) => el.textContent);
    expect(names).toEqual(["library/FIRST.MP4", "library/SECOND.MP4"]);
  });

  it("長さが読めないパートがあると、空白は分からないと言う（0 として扱わない）", async () => {
    // **右側（各パートの長さ表示）は既に「—」で正直に出している。** 左側（空白の
    // 計算）だけ読めない長さを 0 として扱うと、連続した 2 パートに「別の撮影かも
    // しれない」と誤って断定しうる。
    const unknownDuration = {
      ...GROUP,
      id: "g7",
      members: [{ ...GROUP.members[0], duration_seconds: null }, GROUP.members[1]],
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [unknownDuration] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByText(/つなぎ目の空白は分かりません/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/連番が続いていて/)).not.toBeInTheDocument();
    expect(screen.queryByText(/別の撮影かもしれない/)).not.toBeInTheDocument();
  });

  // 既定のテンプレートは `source: "mtime"` で、`captured_at` はファイルの最終
  // 書き込み時刻＝**クリップの終端**になる。前パートの終端に長さを足した時刻より
  // 次のパートの時刻が前になるので、空白は負になる。**0 に丸めると「隙間なく
  // 続いている」という積極的な主張になり、取り消せない結合をその根拠で確認させる。**
  it("パートが重なって見えるときは、空白を 0 と言わない", async () => {
    const overlapping = {
      ...GROUP,
      id: "g20",
      members: [
        GROUP.members[0],
        // 前パートの終端は 14:13:00。次のパートの時刻がそれより前＝重なっている。
        { ...GROUP.members[1], captured_at: "2026-08-18T14:05:00+09:00" },
      ],
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [overlapping] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByText(/つなぎ目の空白は分かりません/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/連番が続いていて/)).toBeNull();
    expect(screen.queryByText(/0\.0 秒/)).toBeNull();
  });

  it("時刻が読めないパートがあると、空白は分からないと言う", async () => {
    const unparsable = {
      ...GROUP,
      id: "g21",
      members: [GROUP.members[0], { ...GROUP.members[1], captured_at: "" }],
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [unparsable] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByText(/つなぎ目の空白は分かりません/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/連番が続いていて/)).toBeNull();
  });

  it("パートが 1 つしかない組では、つなぎ目の話をしない", async () => {
    // 構成を変えるでパートを 1 つまで外すと起きる。つなぎ目が無いので、
    // 「空白は 0.0 秒です。だから同じ 1 本と判断しました」は言いようがない。
    const single = { ...GROUP, id: "g22", members: [GROUP.members[0]] };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [single] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/1 つに分かれています/)).toBeInTheDocument());
    expect(screen.queryByText(/連番が続いていて/)).toBeNull();
    expect(screen.queryByText(/別の撮影かもしれない/)).toBeNull();
  });

  it("手動で作った組・結合に失敗した組は、見出しにそれと分かる印を出す", async () => {
    const manual = { ...GROUP, id: "g10", detected_by: "manual" };
    const failed = {
      ...GROUP,
      id: "g11",
      status: "failed",
      members: [
        { ...GROUP.members[0], media_file_id: "fm1" },
        { ...GROUP.members[1], media_file_id: "fm2" },
      ],
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [manual, failed] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/（手動）/)).toBeInTheDocument());
    expect(screen.getByText(/結合に失敗しました/)).toBeInTheDocument();
  });

  it("できたファイルと検証結果を表示する", async () => {
    const withOutput = {
      ...GROUP,
      id: "g8",
      status: "merged",
      output: { media_file_id: "mo1", rel_path: "library/OUT.MP4", size_bytes: 2147483648, missing: false },
      verification: verification(true, ["pass", "pass", "inconclusive", "inconclusive"]),
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [withOutput] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/library\/OUT\.MP4/)).toBeInTheDocument());
    expect(screen.getByText(/2 GiB/)).toBeInTheDocument();
    expect(screen.getByText(/検証: 合格/)).toBeInTheDocument();
    // **検査ごとに読める形で出す**（内部の名前をそのまま出さない。§13）。
    expect(screen.getByText("長さ: 合っています")).toBeInTheDocument();
    expect(screen.getByText("中身の構成: 合っています")).toBeInTheDocument();
    expect(screen.getByText("コマ数: 確かめられませんでした")).toBeInTheDocument();
    expect(screen.getByText("ファイルの大きさ: 確かめられませんでした")).toBeInTheDocument();
    expect(screen.queryByText(/duration|streams|frames|size/)).toBeNull();
    // 合格した組に採用の操作は要らない（採用は不合格のものを救う手段）。
    expect(screen.queryByRole("button", { name: "中身を見て、これを使う" })).toBeNull();
    // 現行のグループの出力は消せない（消せるのは details/MergeHistory.tsx の
    // 使っていない出力だけ）。
    expect(screen.queryByRole("button", { name: /このファイルを消す/ })).toBeNull();
  });

  it("「つなぐ」は検出済みの候補を結合する", async () => {
    const { calls } = stubApi({ ...ROUTES, "/merge-groups": { groups: [GROUP] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "つなぐ" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/g1/merge" && c.method === "POST")).toBe(true),
    );
  });

  it("失敗した組の「再試行する」は結合をやり直す", async () => {
    const failed = { ...GROUP, id: "g2", status: "failed" };
    const { calls } = stubApi({ ...ROUTES, "/merge-groups": { groups: [failed] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "再試行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/g2/merge" && c.method === "POST")).toBe(true),
    );
  });

  it("「これは別々」は確認を取ってから API を叩く（未公開なら 0 件と出す）", async () => {
    // 本文は既存の discard_merge_group をそのまま使う。GROUP は status: "detected"
    // （まだ結合していない）ので、公開済みの件数は 0 件になるはず
    // （`publishedCount` の変異はここで落ちる）。
    const { calls } = stubApi({ ...ROUTES, "/merge-groups": { groups: [GROUP] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "これは別々" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "これは別々" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("公開済みのファイル");
    expect(dialog).toHaveTextContent("ファイル0 件");
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
    // **破棄と採用を取り違えない。** 採用は「検証に落ちた出力を使う」で、
    // 破棄は「別々の動画として扱う」—— 意味が逆になる。
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(
        calls().some((c) => c.path === "/merge-groups/g1?action=discard" && c.method === "PATCH"),
      ).toBe(true),
    );
    expect(calls().some((c) => c.path.includes("action=adopt"))).toBe(false);
  });

  it("結合済みの組を「これは別々」にすると、公開済み 1 件と出す", async () => {
    const merged = { ...GROUP, id: "g9", status: "merged" };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [merged] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "これは別々" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("ファイル1 件");
  });

  it("破棄した組み合わせと、使っていない出力は、ここには出さない", async () => {
    // **操作できないものを混ぜると「いま何が起きるのか」が読めなくなる**（設定 › 詳しい情報へ）。
    stubApi({
      "/merge-groups?status=skipped": {
        groups: [
          {
            id: "g9",
            status: "skipped",
            members: [],
            detected_by: "auto",
            input_digest: "d",
            verification: null,
            superseded_by_id: null,
            output: null,
          },
        ],
      },
      "/merge-groups": { groups: [] },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [{ id: "s1", rel_path: "derived/old.MP4", size_bytes: 1, captured_at: "", reason: "superseded" }] },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/つなぐものはありません/)).toBeInTheDocument());
    expect(screen.queryByText(/破棄した組み合わせ/)).not.toBeInTheDocument();
    expect(screen.queryByText(/derived\/old\.MP4/)).not.toBeInTheDocument();
  });

  it("失敗した組の member は、個別に送るへ渡せる（裁定 12）", async () => {
    // §10 の既定の一覧は、結合に失敗したグループの member を外す。ここが唯一の
    // 個別送信の入口なので、押すと対象の member を持って /send へ進めることを見る。
    stubApi({
      ...ROUTES,
      "/merge-groups": { groups: [{ ...GROUP, id: "g2", status: "failed" }] },
    });
    render(
      <MemoryRouter initialEntries={["/merge"]}>
        <Routes>
          <Route path="/merge" element={<MergeScreen />} />
          <Route path="/send" element={<SendProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "個別に送る" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "個別に送る" }));
    await waitFor(() => expect(screen.getByTestId("send-ids")).toHaveTextContent("m1,m2"));
  });

  it("「分かれた動画を探す」は検出を叩く", async () => {
    // 言い換えの表（brief）：「候補を検出する」→「分かれた動画を探す」。
    const { calls } = stubApi({ ...ROUTES, "/merge-groups": { groups: [] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/つなぐものはありません/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "分かれた動画を探す" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/detect" && c.method === "POST")).toBe(true),
    );
  });

  it("「中身を見て、これを使う」は確認を取ってから採用する", async () => {
    // 言い換えの表（brief）：「不合格でも採用する」→「中身を見て、これを使う」。
    const { calls } = stubApi({
      ...ROUTES,
      "/merge-groups": {
        groups: [
          {
            ...GROUP,
            id: "g3",
            status: "merged",
            adopted_at: null,
            // 結合が終わった組には必ず出力がある（`_group` の `output`）。
            output: {
              media_file_id: "mo3",
              rel_path: "library/OUT3.MP4",
              size_bytes: 2147483648,
              missing: false,
            },
            verification: verification(false, ["fail", "pass", "fail", "inconclusive"]),
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    const adopt = await screen.findByRole("button", { name: "中身を見て、これを使う" });
    await userEvent.click(adopt);
    // 不合格だった検査の名前を、そのまま理由にする。
    expect(screen.getByRole("dialog")).toHaveTextContent("長さ / コマ数が合いません");
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/g3?action=adopt" && c.method === "PATCH")).toBe(true),
    );
  });

  it("「同じ構成でやり直す」は確認のあと、全 member で regroup する", async () => {
    const merged = { ...GROUP, id: "g20", status: "merged" };
    const { calls, bodies } = stubApiWithBodies({ ...ROUTES, "/merge-groups": { groups: [merged] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "同じ構成でやり直す" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/g20?action=regroup" && c.method === "PATCH")).toBe(true),
    );
    const sent = bodies.find((b) => b.path === "/merge-groups/g20?action=regroup");
    expect(sent?.body).toEqual({ media_ids: ["m1", "m2"] });
  });

  it("「構成を変える」は、チェックを外した member を除いて regroup する", async () => {
    const target = {
      ...GROUP,
      id: "g21",
      members: [
        ...GROUP.members,
        { ...GROUP.members[1], position: 2, media_file_id: "m3", rel_path: "library/DJI_0003.MP4" },
      ],
    };
    const { calls, bodies } = stubApiWithBodies({ ...ROUTES, "/merge-groups": { groups: [target] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "構成を変える" }));
    const dialog = await screen.findByRole("dialog", { name: "構成を変える" });
    // 読み上げの名前（`aria-label`）と、見えている題は別の式。
    expect(within(dialog).getByRole("heading", { name: "構成を変える" })).toBeInTheDocument();
    const checkboxes = within(dialog).getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(3);
    await userEvent.click(checkboxes[2]); // position 2（m3）のチェックを外す
    await userEvent.click(within(dialog).getByRole("button", { name: "この構成にする" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/g21?action=regroup" && c.method === "PATCH")).toBe(true),
    );
    const sent = bodies.find((b) => b.path === "/merge-groups/g21?action=regroup");
    expect(sent?.body).toEqual({ media_ids: ["m1", "m2"] });
  });

  // つなぐ組は 2 件以上（`POST /merge-groups` と同じ条件。API も 400 で断る）。
  // 1 件まで外せてしまうと、つなぎ目の無い組が一覧に残る。
  it("1 件まで外したら、その構成にはできない", async () => {
    const target = { ...GROUP, id: "g24" };
    const { calls } = stubApiWithBodies({ ...ROUTES, "/merge-groups": { groups: [target] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "構成を変える" }));
    const dialog = await screen.findByRole("dialog", { name: "構成を変える" });
    await userEvent.click(within(dialog).getAllByRole("checkbox")[1]);

    expect(within(dialog).getByRole("button", { name: "この構成にする" })).toBeDisabled();
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
  });

  // `aria-modal="true"` を名乗る以上、背後は「無い」ことになっている（§13）。
  it("「構成を変える」は、開いたら中へ焦点が入り、Esc で閉じられる", async () => {
    const target = { ...GROUP, id: "g25" };
    stubApiWithBodies({ ...ROUTES, "/merge-groups": { groups: [target] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    const opener = await screen.findByRole("button", { name: "構成を変える" });
    await userEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "構成を変える" });
    expect(dialog.contains(document.activeElement)).toBe(true);

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "構成を変える" })).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  // **二重送信を止めているのは「押した瞬間に閉じる」こと。** ここに
  // `disabled={busy}` を置いても一度も真にならない（`busy` が立つ時点では
  // ダイアログが既に消えている）ので、押せなさではなく消えることを見る。
  it("「この構成にする」を押した時点で、ダイアログが消える", async () => {
    const target = { ...GROUP, id: "g22" };
    stubApiWithBodies({ ...ROUTES, "/merge-groups": { groups: [target] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "構成を変える" }));
    const dialog = await screen.findByRole("dialog", { name: "構成を変える" });

    await userEvent.click(within(dialog).getByRole("button", { name: "この構成にする" }));

    expect(screen.queryByRole("dialog", { name: "構成を変える" })).toBeNull();
  });

  it("「やめる」でもダイアログが消える", async () => {
    const target = { ...GROUP, id: "g23" };
    const { calls } = stubApiWithBodies({ ...ROUTES, "/merge-groups": { groups: [target] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "構成を変える" }));
    const dialog = await screen.findByRole("dialog", { name: "構成を変える" });

    await userEvent.click(within(dialog).getByRole("button", { name: "やめる" }));

    expect(screen.queryByRole("dialog", { name: "構成を変える" })).toBeNull();
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
  });

  it("2 件未満では手動グループを作れない（ガード）", async () => {
    const { calls } = stubApi({
      ...ROUTES,
      "/merge-groups": { groups: [] },
      "/media": {
        media: [
          { id: "x1", rel_path: "library/X1.MP4" },
          { id: "x2", rel_path: "library/X2.MP4" },
        ],
      },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByText("手でグループを作る"));
    const checkboxes = await screen.findAllByRole("checkbox");
    await userEvent.click(checkboxes[0]);
    expect(screen.getByRole("button", { name: /選んだ 1 件でグループを作る/ })).toBeDisabled();
    await userEvent.click(checkboxes[1]);
    const makeButton = screen.getByRole("button", { name: /選んだ 2 件でグループを作る/ });
    expect(makeButton).toBeEnabled();
    await userEvent.click(makeButton);
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups" && c.method === "POST")).toBe(true),
    );
  });

  // 失敗した選択を消すと、**やり直すのに選び直しからになる**（失敗は上の帯で
  // 知らせている）。
  it("グループを作れなかったときは、選んだものを残す", async () => {
    const media = [
      { id: "x1", rel_path: "library/X1.MP4" },
      { id: "x2", rel_path: "library/X2.MP4" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        if (path === "/merge-groups" && init?.method === "POST") {
          return Promise.resolve(
            new Response(JSON.stringify({ error: { code: "conflict", detail: "別の組にいる", meta: {} } }), {
              status: 409,
            }),
          );
        }
        if (path.startsWith("/media")) {
          return Promise.resolve(new Response(JSON.stringify({ media }), { status: 200 }));
        }
        if (path.startsWith("/merge-groups")) {
          return Promise.resolve(new Response(JSON.stringify({ groups: [] }), { status: 200 }));
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByText("手でグループを作る"));
    const checkboxes = await screen.findAllByRole("checkbox");
    await userEvent.click(checkboxes[0]);
    await userEvent.click(checkboxes[1]);
    await userEvent.click(screen.getByRole("button", { name: /選んだ 2 件でグループを作る/ }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /選んだ 2 件でグループを作る/ })).toBeInTheDocument();
  });
});

// 画面は実際の応答（`passed` と `checks[]`）を読む。`core/merge/verify.py` の
// `to_json` はトップレベルに `verdict` も `reason` も書かない。**`passed` が偽の
// ときに「中身を見て、これを使う」を出す**のがここの要（§10 の `adopted_derived`。
// これが無いと、検証に落ちた出力を送る手段が画面から消える）。
describe("検証の結果", () => {
  function renderWith(group: unknown) {
    stubApi({ ...ROUTES, "/merge-groups": { groups: [group] } });
    return render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
  }

  const MERGED = {
    ...GROUP,
    id: "gv",
    status: "merged",
    adopted_at: null,
    output: {
      media_file_id: "mo1",
      rel_path: "library/OUT.MP4",
      size_bytes: 2147483648,
      missing: false,
    },
  };

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
    await waitFor(() => expect(screen.getByText(/library\/OUT\.MP4/)).toBeInTheDocument());
    expect(screen.queryByText(/検証:/)).toBeNull();
  });
});

describe("不合格の理由の 1 文", () => {
  it("合わなかった検査を並べる", () => {
    expect(
      failureReason({
        passed: false,
        checks: [
          { name: "duration", verdict: "fail" },
          { name: "streams", verdict: "pass" },
          { name: "frames", verdict: "inconclusive" },
          { name: "size", verdict: "fail" },
        ],
        route_dropped_streams: [],
      }),
    ).toBe("長さ / ファイルの大きさが合いません");
  });

  it("判定不能は理由に数えない（合否に使わないため）", () => {
    expect(
      failureReason({
        passed: false,
        checks: [{ name: "frames", verdict: "inconclusive" }],
        route_dropped_streams: [],
      }),
    ).toBe("検証に通っていません");
  });
});

// **`busy` を `false` に倒しても落ちないテストしか無いガードは、無いガードと同じ**
// （押し続けると同じ操作が何度も飛ぶ）。失敗を握り潰していないことも同じように見る。
describe("飛んでいる間と、失敗したとき", () => {
  /** `path` への応答だけを握って止める `fetch`。 */
  function heldFetch(path: string, routes: Record<string, unknown>) {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const target = input.replace(/^\/api/, "");
        if (target === path) {
          await held;
          return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
        }
        const keys = Object.keys(routes);
        const key = keys.find((k) => k === target) ?? keys.find((k) => target.startsWith(k));
        return new Response(JSON.stringify(key === undefined ? {} : routes[key]), { status: 200 });
      }),
    );
    return release;
  }

  const MERGED_FAILING = {
    ...GROUP,
    id: "g30",
    status: "merged",
    adopted_at: null,
    output: {
      media_file_id: "mo30",
      rel_path: "library/OUT30.MP4",
      size_bytes: 2147483648,
      missing: false,
    },
    verification: verification(false, ["fail", "pass", "pass", "pass"]),
  };

  it("1 つの操作が飛んでいる間は、どの操作も押せない", async () => {
    const release = heldFetch("/merge-groups/detect", {
      ...ROUTES,
      "/merge-groups": {
        groups: [GROUP, { ...GROUP, id: "g31", status: "failed" }, MERGED_FAILING],
      },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    const names = [
      "つなぐ",
      "再試行する",
      "個別に送る",
      "中身を見て、これを使う",
      "同じ構成でやり直す",
      "分かれた動画を探す",
    ];
    for (const name of names) {
      expect(await screen.findByRole("button", { name })).toBeEnabled();
    }
    expect(screen.getAllByRole("button", { name: "構成を変える" })[0]).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "これは別々" })[0]).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "分かれた動画を探す" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "分かれた動画を探す" })).toBeDisabled(),
    );
    for (const name of names) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    for (const button of screen.getAllByRole("button", { name: "構成を変える" })) {
      expect(button).toBeDisabled();
    }
    for (const button of screen.getAllByRole("button", { name: "これは別々" })) {
      expect(button).toBeDisabled();
    }
    release();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "分かれた動画を探す" })).toBeEnabled(),
    );
  });

  it("確認のあと飛んでいる間は、確認の「実行する」も押せない", async () => {
    const release = heldFetch("/merge-groups/g1?action=discard", {
      ...ROUTES,
      "/merge-groups": { groups: [GROUP] },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "これは別々" }));
    const run = screen.getByRole("button", { name: "実行する" });
    await userEvent.click(run);
    await waitFor(() => expect(run).toBeDisabled());
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("失敗を握り潰さない（バナーに出す）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const target = input.replace(/^\/api/, "");
        if (target === "/merge-groups/detect") {
          return Promise.resolve(
            new Response(
              JSON.stringify({ error: { code: "internal", detail: "", meta: {} } }),
              { status: 500 },
            ),
          );
        }
        return Promise.resolve(new Response(JSON.stringify({ groups: [] }), { status: 200 }));
      }),
    );
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "分かれた動画を探す" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
