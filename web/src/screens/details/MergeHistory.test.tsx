// つないだ後の後片付け（§13）。別々のままにした組み合わせと、使っていない出力は操作
// できない記録なので、ここでは一覧と削除の確認だけを見る。

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { emitJob } from "../../test/setup";
import { DashboardProvider } from "../../api/dashboard";
import { stubApi } from "../../test/api";
import { MergeHistoryScreen } from "./MergeHistory";

const EMPTY_ROUTES = {
  "/merge-groups?status=skipped": { groups: [] },
  "/media/stale-derived": { stale: [] },
};

function discardedGroup(id: string, relPath: string) {
  return {
    id,
    status: "skipped",
    detected_by: "auto",
    input_digest: "d",
    verification: null,
    superseded_by_id: null,
    output: null,
    members: [
      {
        position: 0,
        media_file_id: `m-${id}`,
        rel_path: relPath,
        size_bytes: 1,
        duration_seconds: null,
        captured_at: "",
      },
    ],
  };
}

function staleItem(id: string, relPath: string, reason = "superseded") {
  return { id, rel_path: relPath, size_bytes: 1024, captured_at: "", reason };
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("つないだ後の後片付け", () => {
  // 消すのはジョブにならないので、取り直さないと枠の数（写真の合計・未送信）が
  // 古いまま残る。
  it("消したあと、枠の集計を取り直す", async () => {
    const { calls } = stubApi({
      "/dashboard": {
        media_total: 1,
        destinations: [],
        running_jobs: 0,
        recent_imports: [],
        orphans: 0,
        missing: 0,
        warnings: [],
        merge_candidates: 0,
        merge_review_total: 0,
        unsent_total: 0,
        awaiting_total: 0,
      },
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s9", "derived/gone.MP4")] },
      "/media/s9": { id: "s9" },
    });
    render(
      <MemoryRouter>
        <DashboardProvider>
          <MergeHistoryScreen />
        </DashboardProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(calls().filter((c) => c.path === "/dashboard")).toHaveLength(1));
    await userEvent.click(await screen.findByRole("button", { name: /このファイルを消す/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() =>
      expect(calls().filter((c) => c.path === "/dashboard").length).toBeGreaterThan(1),
    );
  });

  it("使っていない出力を、ファイルとして消せる", async () => {
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s1", "derived/old.MP4")] },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/derived\/old\.MP4/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /このファイルを消す/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("元になったファイルは残ります");
    expect(calls().some((c) => c.method === "DELETE")).toBe(false);
  });

  it("使っていない出力が無くても節は出し、そう書く（裁定 42 の着地）", async () => {
    stubApi(EMPTY_ROUTES);
    const { container } = render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("使っていない出力")).toBeInTheDocument());
    expect(screen.getByText("使っていない出力はありません。")).toBeInTheDocument();
    // 0 件でも #stale の錨は消えない。
    const stale = container.querySelector("#stale");
    expect(stale).not.toBeNull();
    // 0 件のときは合計サイズを添えない（「0 件 ・ 合計 0 B」のように出さない）。
    expect(within(stale as HTMLElement).getByText("0 件")).toBeInTheDocument();
  });

  it("別々のままにした組み合わせを一覧で見せ、確認を経てから記録を消す", async () => {
    const { calls } = stubApi({
      ...EMPTY_ROUTES,
      "/merge-groups?status=skipped": { groups: [discardedGroup("old1", "library/a.MP4")] },
      // **叩く先も登録する**（`stubApi` は知らないパスを 404 で返す）。
      "/merge-groups/old1": { id: "old1" },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("library/a.MP4")).toBeInTheDocument());
    // **記録の一覧に操作ボタンを混ぜない。** ここはつなぐ・別々にする対象を
    // 選ぶ画面ではなく、過去の記録を見る場所（§13「詳しい情報」）。
    expect(screen.queryByRole("button", { name: /つなぐ|これは別々/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "消す：library/a.MP4" }));
    // 消す対象が分かるように、その組の 1 つ目のファイル名を確認に出す。
    expect(screen.getByRole("dialog")).toHaveTextContent("library/a.MP4");
    // **画面にあるボタンの名前で言う**（§13 の言い換え。「候補を検出する」はもう無い）。
    expect(screen.getByRole("dialog")).toHaveTextContent("もう一度「分かれた動画を探す」を");
    expect(screen.getByRole("dialog")).toHaveTextContent("組み合わせがまた出ることがあります");
    expect(calls().some((c) => c.method === "DELETE")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/old1" && c.method === "DELETE")).toBe(true),
    );
    // **消した後は一覧を引き直す。** 叩いたところまでしか見ないと、画面が
    // 消えたはずの組を出したままでも気付けない。
    await waitFor(() =>
      expect(
        calls().filter((c) => c.path === "/merge-groups?status=skipped").length,
      ).toBeGreaterThan(1),
    );
  });

  it("別々のままにした組み合わせが無ければ、そう書く", async () => {
    stubApi(EMPTY_ROUTES);
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("別々のままにした組み合わせはありません。")).toBeInTheDocument();
  });

  it("複数あると、それぞれ別のファイル名で「消す」を選べる", async () => {
    const { calls } = stubApi({
      ...EMPTY_ROUTES,
      "/merge-groups?status=skipped": {
        groups: [discardedGroup("g1", "library/a.MP4"), discardedGroup("g2", "library/b.MP4")],
      },
      "/merge-groups/g2": { id: "g2" },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("library/b.MP4")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "消す：library/b.MP4" }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/merge-groups/g2" && c.method === "DELETE")).toBe(true),
    );
    expect(calls().some((c) => c.path === "/merge-groups/g1" && c.method === "DELETE")).toBe(false);
  });

  it("使っていない出力が複数あると、それぞれ別のファイル名で「このファイルを消す」を選べる", async () => {
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s1", "derived/a.MP4"), staleItem("s2", "derived/b.MP4")] },
      "/media/s2": { id: "s2" },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("derived/b.MP4")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "このファイルを消す：derived/b.MP4" }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() => expect(calls().some((c) => c.path === "/media/s2" && c.method === "DELETE")).toBe(true));
    expect(calls().some((c) => c.path === "/media/s1" && c.method === "DELETE")).toBe(false);
  });

  it("見出しに件数（と、使っていない出力は合計サイズ）を出す", async () => {
    stubApi({
      "/merge-groups?status=skipped": {
        groups: [discardedGroup("g1", "library/a.MP4"), discardedGroup("g2", "library/b.MP4")],
      },
      "/media/stale-derived": { stale: [staleItem("s1", "derived/a.MP4"), staleItem("s2", "derived/b.MP4")] },
      "/media/s2": { id: "s2" },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("library/b.MP4")).toBeInTheDocument());
    expect(screen.getByText("2 件")).toBeInTheDocument();
    // stale の各アイテムは 1024 B なので、合計は 2 KiB。
    expect(screen.getByText(/2 件 ・ 合計 2 KiB/)).toBeInTheDocument();
  });

  it("組み直しではなく別々にしたことで残った出力は「別々にした組」と出す", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s2", "derived/discarded.MP4", "discarded")] },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/1 KiB ・別々にした組$/)).toBeInTheDocument();
    expect(screen.queryByText(/組み直しで置き換わった/)).not.toBeInTheDocument();
  });

  it("確認したあと、使っていない出力をそのファイルの DELETE で消す", async () => {
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s3", "derived/old3.MP4")] },
      "/media/s3": { id: "s3" },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /このファイルを消す/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/media/s3" && c.method === "DELETE")).toBe(true),
    );
  });

  it("消してよいか聞くとき、確認ダイアログに内部 ID ではなく相対パスを渡す", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("internal-uuid-123", "derived/named.MP4")] },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /このファイルを消す/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("derived/named.MP4");
    expect(dialog).not.toHaveTextContent("internal-uuid-123");
  });

  it("消している間は、どちらの「消す」ボタンも押せない（二重送信を防ぐ）", async () => {
    let open: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        if (method === "DELETE") {
          await gate;
          return new Response(JSON.stringify({}), { status: 200 });
        }
        const body =
          path === "/merge-groups?status=skipped"
            ? { groups: [discardedGroup("old1", "library/a.MP4")] }
            : path === "/media/stale-derived"
              ? { stale: [staleItem("s1", "derived/old.MP4")] }
              : {};
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "消す：library/a.MP4" }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "消す：library/a.MP4" })).toBeDisabled());
    expect(screen.getByRole("button", { name: /このファイルを消す/ })).toBeDisabled();
    // 確認ダイアログ自身の「実行する」も、返事が戻るまでは押せない（連打で 2 回叩かない）。
    expect(screen.getByRole("button", { name: "実行する" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "やめる" })).toBeDisabled();

    open();
    await waitFor(() => expect(screen.getByRole("button", { name: "消す：library/a.MP4" })).toBeEnabled());
  });

  it("消したら、一覧を取り直す", async () => {
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s1", "derived/old.MP4")] },
      "/media/s1": { status: "ok" },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /このファイルを消す/ }));
    const before = calls().filter((c) => c.path === "/media/stale-derived" && c.method === "GET").length;
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(
        calls().filter((c) => c.path === "/media/stale-derived" && c.method === "GET").length,
      ).toBeGreaterThan(before),
    );
  });

  // 設定 › 詳しい情報の「使っていないファイル」は `#stale` で来る。router は
  // `#` を見ないので、自分で送らないと画面の上のほうを出したまま止まる。
  it("#stale で開いたら、その節まで送る", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s1", "derived/old.MP4")] },
    });
    const scrolled: string[] = [];
    vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(function (this: Element) {
      scrolled.push(this.id);
    });
    render(
      <MemoryRouter initialEntries={["/settings/merge-history#stale"]}>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(scrolled).toContain("stale"));
  });

  it("進捗のイベントが届いたら、一覧を取り直す", async () => {
    const { calls } = stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [] },
    });
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("使っていない出力はありません。")).toBeInTheDocument());
    const before = calls().filter((c) => c.path === "/media/stale-derived" && c.method === "GET").length;
    emitJob({ job_id: "j1", seq: 1, level: "info", message: "更新", data: null, at: "" });
    await waitFor(
      () =>
        expect(
          calls().filter((c) => c.path === "/media/stale-derived" && c.method === "GET").length,
        ).toBeGreaterThan(before),
      { timeout: 2000 },
    );
  });

  it("消すのに失敗したら、その旨を画面に出す", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        if (path === "/media/s1" && method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ error: { code: "internal", detail: "" } }), { status: 500 }),
          );
        }
        const body =
          path === "/merge-groups?status=skipped"
            ? { groups: [] }
            : path === "/media/stale-derived"
              ? { stale: [staleItem("s1", "derived/old.MP4")] }
              : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /このファイルを消す/ }));
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("見出しと領域の名は「つないだ後の後片付け」（内部の名前を出さない）", async () => {
    stubApi(EMPTY_ROUTES);
    const { container } = render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await screen.findByText("別々のままにした組み合わせはありません。");
    expect(screen.getByRole("heading", { level: 1, name: "つないだ後の後片付け" })).toBeInTheDocument();
    expect(container.querySelector('section[aria-label="つないだ後の後片付け"]')).not.toBeNull();
    // §13「破棄 → これは別々」。節の見出しも内部語のままにしない。
    expect(screen.getByRole("heading", { level: 2, name: "別々のままにした組み合わせ" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "使っていない出力" })).toBeInTheDocument();
  });

  it("名前が中身と合っていて、つないだ動画の在り処を案内する", async () => {
    stubApi(EMPTY_ROUTES);
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await screen.findByText("別々のままにした組み合わせはありません。");
    // **探しに来た人を迷子にしない。** 成功した結合は写真タブにある。
    expect(screen.getByRole("link", { name: /つないだ動画/ })).toHaveAttribute(
      "href",
      "/photos?role=derived",
    );
  });

  it("設定へ戻る導線を出す", async () => {
    stubApi(EMPTY_ROUTES);
    render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("link", { name: /設定へ/ })).toHaveAttribute("href", "/settings");
  });

  it("使っていない出力の節に、飛び先の錨（#stale）を置く（裁定 42）", async () => {
    stubApi({
      "/merge-groups?status=skipped": { groups: [] },
      "/media/stale-derived": { stale: [staleItem("s1", "derived/old.MP4")] },
    });
    const { container } = render(
      <MemoryRouter>
        <MergeHistoryScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/derived\/old\.MP4/)).toBeInTheDocument());
    expect(container.querySelector("#stale")).not.toBeNull();
  });
});
