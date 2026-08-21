// つなぐ（§13）。**なぜまとまったかが分かるようにする。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { MergeScreen } from "./Merge";

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
      size_bytes: 4294967296,
      duration_seconds: 600,
      captured_at: "2026-08-18T14:03:00+09:00",
    },
    {
      position: 1,
      media_file_id: "m2",
      rel_path: "library/DJI_0002.MP4",
      size_bytes: 4294967296,
      duration_seconds: 600,
      captured_at: "2026-08-18T14:13:00+09:00",
    },
  ],
};

/** `/send` へ渡った `location.state` を画面に出すだけの受け皿。 */
function SendProbe() {
  const location = useLocation();
  const state = location.state as { ids?: string[] } | null;
  return <p data-testid="send-ids">{(state?.ids ?? []).join(",")}</p>;
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("つなぐ", () => {
  it("なぜこの並びなのかを、構成とギャップで出す", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": { groups: [GROUP] },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [] },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/2 つに分かれています/)).toBeInTheDocument());
    expect(screen.getByText(/DJI_0001\.MP4/)).toBeInTheDocument();
    expect(screen.getByText(/4 GiB/)).toBeInTheDocument();
  });

  it("つなぎ目に空白が無ければ、同じ 1 本と判断した理由を出す（警告にしない）", async () => {
    // GROUP はつなぎ目の空白が 0 秒。**警告色にしない**ことも判断のうち。
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": { groups: [GROUP] },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [] },
    });
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
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": { groups: [gapped] },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [] },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/別の撮影かもしれない/)).toBeInTheDocument());
    expect(screen.queryByText(/連番が続いていて/)).not.toBeInTheDocument();
  });

  it("「これは別々」は確認を取ってから API を叩く", async () => {
    // 本文は既存の discard_merge_group をそのまま使う。
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": { groups: [GROUP] },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [] },
    });
    render(
      <MemoryRouter>
        <MergeScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "これは別々" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "これは別々" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("公開済みのファイル");
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
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
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": {
        groups: [{ ...GROUP, id: "g2", status: "failed" }],
      },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [] },
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
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": { groups: [] },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [] },
    });
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
      "/merge-groups?status=skipped": { groups: [] },
      "/merge-groups": {
        groups: [
          {
            ...GROUP,
            id: "g3",
            status: "merged",
            verification: { verdict: "fail", reason: "継ぎ目が不自然" },
          },
        ],
      },
      "/media": { media: [] },
      "/media/stale-derived": { stale: [] },
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
});
