// つなぐ（§13）。**なぜまとまったかが分かるようにする。**

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { MergeScreen } from "./Merge";

// **2 パートのサイズをわざと変える。** 両方 4 GiB だと、パートごとの表示が 2 要素とも
// 同じ文字列になり `getByText` が「複数一致」で落ちる（かつては導入文の固定の
// 「4 GiB」を誤って拾っていた—— 導入文から数字を抜いた今回の直しで、この GROUP の
// サイズがテストの実データになっている）。
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
      verification: { verdict: "pass", reason: null },
    };
    stubApi({ ...ROUTES, "/merge-groups": { groups: [withOutput] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/library\/OUT\.MP4/)).toBeInTheDocument());
    expect(screen.getByText(/2 GiB/)).toBeInTheDocument();
    expect(screen.getByText(/検証: pass/)).toBeInTheDocument();
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
        groups: [{ ...GROUP, id: "g3", status: "merged", verification: { verdict: "fail", reason: "継ぎ目が不自然" } }],
      },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    const adopt = await screen.findByRole("button", { name: "中身を見て、これを使う" });
    await userEvent.click(adopt);
    expect(screen.getByRole("dialog")).toHaveTextContent("継ぎ目が不自然");
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
    const target = { ...GROUP, id: "g21" };
    const { calls, bodies } = stubApiWithBodies({ ...ROUTES, "/merge-groups": { groups: [target] } });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "構成を変える" }));
    const dialog = await screen.findByRole("dialog", { name: "構成を変える" });
    const checkboxes = within(dialog).getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(2);
    await userEvent.click(checkboxes[1]); // position 1（m2）のチェックを外す
    await userEvent.click(within(dialog).getByRole("button", { name: "この構成にする" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/g21?action=regroup" && c.method === "PATCH")).toBe(true),
    );
    const sent = bodies.find((b) => b.path === "/merge-groups/g21?action=regroup");
    expect(sent?.body).toEqual({ media_ids: ["m1"] });
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
});
