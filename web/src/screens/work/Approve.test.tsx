import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { APPROVE_PAGE, ApproveScreen } from "./Approve";

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("確認", () => {
  // 裁定 20「打ち切ったことを黙らない」。一覧には上限があり、ホームの件数は
  // 全件を数える。黙って切ると、ホームの「N 件」といくら見比べても合わない。
  it("上限で切れているときは、ほかにもあると書く", async () => {
    const records = Array.from({ length: APPROVE_PAGE + 1 }, (_, index) => ({
      id: `r${index}`,
      destination_id: "d1",
      media_file_id: `m${index}`,
      origin: "pre_existing",
      remote_current: null,
      proposed: "2026-08-14 20:02",
      remote_checked_at: null,
      identical: false,
    }));
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": { records },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText(`先頭 ${APPROVE_PAGE} 件だけを出しています（ほかにもあります）。`),
    ).toBeInTheDocument();
  });

  // **ちょうど上限のときは切れていない。** 読めた数だけで判断すると、
  // 出し切っているのに「ほかにもあります」と書く。
  it("ちょうど上限の件数なら、切れているとは書かない", async () => {
    const records = Array.from({ length: APPROVE_PAGE }, (_, index) => ({
      id: `r${index}`,
      destination_id: "d1",
      media_file_id: `m${index}`,
      rel_path: `library/2026/08/DJI_${index}.MP4`,
      origin: "pre_existing",
      remote_current: null,
      proposed: "2026-08-14 20:02",
      remote_checked_at: null,
      identical: false,
    }));
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": { records },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(APPROVE_PAGE));
    expect(screen.queryByText(/ほかにもあります/)).toBeNull();
  });

  it("上限に達していなければ、余計なことは書かない", async () => {
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: null,
            proposed: "2026-08-14 20:02",
            remote_checked_at: null,
            identical: false,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("（読めませんでした）")).toBeInTheDocument());
    expect(screen.queryByText(/ほかにもあります/)).toBeNull();
  });

  it("読めなかった値を空欄にしない", async () => {
    // **空欄は「変更なし」に見える。**
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: null,
            proposed: "2026-08-14 20:02",
            remote_checked_at: null,
            identical: false,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("（読めませんでした）")).toBeInTheDocument());
  });

  it("却下はリモートに触らないと画面に書く", async () => {
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": { records: [] },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/Immich には何も起きません/)).toBeInTheDocument());
    // 画面の名前は「確認」（内部の「承認待ち」を出さない。§13）。
    expect(screen.getByRole("region", { name: "確認" })).toBeInTheDocument();
    // **空の表を出さない**（§13）。無いことも書く。
    expect(screen.getByText("確認するものはありません")).toBeInTheDocument();
  });

  it("承認は確認を取ってから API を叩く", async () => {
    // **不可逆な操作（リモートの書き換え）は確認を必須にする**（§13）。
    const { calls } = stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: "2026-08-14 10:00",
            proposed: "2026-08-14 20:02",
            remote_checked_at: "2026-08-14 09:00",
            identical: false,
          },
        ],
      },
      // **叩く先も登録する**（`stubApi` は知らないパスを 404 で返す）。
      "/uploads/r1/approve": { status: "ok" },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "承認する" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("2026年8月14日 20:02");
    expect(calls().some((c) => c.method === "POST" && c.path.includes("/approve"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/uploads/r1/approve" && c.method === "POST")).toBe(true),
    );
    // **叩いた後を見る。** 応答が失敗のままだと「叩いた」までしか試さないので、
    // 一覧を引き直す枝が死んでいても気付けない。
    await waitFor(() =>
      expect(calls().filter((c) => c.path.startsWith("/uploads?state=")).length).toBeGreaterThan(1),
    );
  });

  it("却下は確認なしで reject を叩く（approve と取り違えない）", async () => {
    // §13「不可逆な操作は確認を取る」の対になる判断: 却下はリモートに触らないので
    // 確認は要らないが、その分だけ**承認と取り違えて叩くと確認なしで書き換わって
    // しまう**。path も method も具体的に見る。
    const { calls } = stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: "2026-08-14 10:00",
            proposed: "2026-08-14 20:02",
            remote_checked_at: "2026-08-14 09:00",
            identical: false,
          },
        ],
      },
      "/uploads/r1/reject": { status: "ok" },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "却下する" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/uploads/r1/reject" && c.method === "POST")).toBe(true),
    );
    expect(calls().some((c) => c.path.endsWith("/approve"))).toBe(false);
    await waitFor(() =>
      expect(calls().filter((c) => c.path.startsWith("/uploads?state=")).length).toBeGreaterThan(1),
    );
  });

  it("直したい日時が読めなければ空欄にしない", async () => {
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: "2026-08-14 10:00",
            proposed: null,
            remote_checked_at: null,
            identical: false,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("—")).toBeInTheDocument());
  });

  // カードの表示（「—」）と揃える。空文字のままだと確認ダイアログが
  // 「変更後 」で途切れる。
  it("直したい日時が読めなくても、確認ダイアログが途切れない", async () => {
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: "2026-08-14 10:00",
            proposed: null,
            remote_checked_at: null,
            identical: false,
          },
        ],
      },
      // **叩く先も登録する**（`stubApi` は知らないパスを 404 で返す）。
      "/uploads/r1/approve": { status: "ok" },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "承認する" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("変更後 —");
  });

  it("内部の ID を見出しに出さない", async () => {
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1-uuid-should-not-appear",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: "2026-08-14 10:00",
            proposed: "2026-08-14 20:02",
            remote_checked_at: "2026-08-14 09:00",
            identical: false,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await screen.findByRole("button", { name: "承認する" });
    expect(screen.queryByText("m1-uuid-should-not-appear")).not.toBeInTheDocument();
  });

  it("観測した時刻を人が読める形にする", async () => {
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: "2026-08-14 10:00",
            proposed: "2026-08-14 20:02",
            remote_checked_at: "2026-08-14 09:00",
            identical: false,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    // システム時刻（clock が作る UTC）はそのまま出すと現地時刻に見えるので、印を添える。
    await waitFor(() => expect(screen.getByText(/2026年8月14日 09:00（UTC）/)).toBeInTheDocument());
    expect(screen.queryByText("2026-08-14 09:00")).not.toBeInTheDocument();
  });

  it("変更が無い行では承認を促さない", async () => {
    stubApi({
      "/destinations": { destinations: [{ id: "d1", name: "家の Immich" }] },
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
            rel_path: "library/2026/08/DJI_0001.MP4",
            origin: "pre_existing",
            remote_current: "2026-08-14 20:02",
            proposed: "2026-08-14 20:02",
            remote_checked_at: "2026-08-14 09:00",
            identical: true,
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("変更なし")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "承認する" })).toBeNull();
  });
});

// **どの写真を、どの Immich で書き換えるのかを画面に出す。** これはリモート資産の
// 書き換えなので取り消せず、送り先が 2 つあると名前が無ければどちらのライブラリを
// 触るのか判別できない（§13「宛先を取り違えたまま送ると取り消せない」）。
describe("何を、どこで書き換えるのか", () => {
  const RECORD = {
    id: "r1",
    destination_id: "d2",
    media_file_id: "m1",
    rel_path: "library/2026/08/DJI_0001.MP4",
    origin: "pre_existing",
    remote_current: "2026-08-14T10:00:00+09:00",
    proposed: "2026-08-14T20:02:00+09:00",
    remote_checked_at: "2026-08-14T09:00:00Z",
    identical: false,
  };

  function stubApprove(overrides: Record<string, unknown> = {}) {
    return stubApi({
      "/uploads?state=awaiting_datetime_approval": { records: [RECORD] },
      "/destinations": {
        destinations: [
          { id: "d1", name: "家の Immich", enabled: true },
          { id: "d2", name: "外の Immich", enabled: true },
        ],
      },
      ...overrides,
    });
  }

  function renderApprove() {
    return render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
  }

  // **一覧が持っている値で描く。** 1 件ずつ引き直すと、上限いっぱい（200 件）の
  // 確認で 200 本の要求が飛び、承認のたびに全件を引き直すことになる。
  it("ファイル名は一覧の行から取る（1 件ずつ引き直さない）", async () => {
    const { calls } = stubApprove();
    renderApprove();
    expect(await screen.findByRole("heading", { name: "DJI_0001.MP4" })).toBeInTheDocument();
    expect(calls().some((call) => call.path.startsWith("/media/"))).toBe(false);
  });

  it("どの写真かをサムネイルで出す", async () => {
    stubApprove();
    renderApprove();
    // タイルの読み上げ名はファイル名（`MediaTile`）。押せないタイルは絵として
    // 出す（`role="img"`）ので、押せる木からは探せない。
    const tile = await screen.findByRole("img", { name: "DJI_0001.MP4" });
    expect(tile.querySelector("img")).toHaveAttribute("src", "/api/media/m1/thumbnail");
  });

  it("見出しはファイル名（UUID を見出しにしない）", async () => {
    stubApprove();
    renderApprove();
    expect(
      await screen.findByRole("heading", { name: "DJI_0001.MP4" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("m1")).toBeNull();
  });

  it("どの送り先を書き換えるのかを名前で出す", async () => {
    stubApprove();
    renderApprove();
    expect(await screen.findByText("送り先: 外の Immich")).toBeInTheDocument();
    expect(screen.queryByText(/家の Immich/)).toBeNull();
    expect(screen.queryByText(/d2/)).toBeNull();
  });

  it("送り先の名前が引けないときは、内部の ID を出さずに分からないと書く", async () => {
    stubApprove({ "/destinations": { destinations: [] } });
    renderApprove();
    expect(await screen.findByText("送り先: 分かりません")).toBeInTheDocument();
    expect(screen.queryByText(/d2/)).toBeNull();
  });

  it("ファイルが読めないときも、UUID を見出しにしない", async () => {
    // 一覧が位置を持たない行（元のファイルが消えている等）。
    stubApprove({
      "/uploads?state=awaiting_datetime_approval": { records: [{ ...RECORD, rel_path: null }] },
    });
    renderApprove();
    await screen.findByRole("button", { name: "承認する" });
    expect(screen.getByRole("heading", { name: "ファイル名が読めません" })).toBeInTheDocument();
    expect(screen.queryByText("m1")).toBeNull();
  });

  it("現在値と変更案も人が読める形にする", async () => {
    stubApprove();
    renderApprove();
    // 撮影日時系はそのまま切り出す（`formatDateTime`）。UTC の印は付けない。
    expect(await screen.findByText("2026年8月14日 10:00")).toBeInTheDocument();
    expect(screen.getByText("2026年8月14日 20:02")).toBeInTheDocument();
    expect(screen.queryByText("2026-08-14T10:00:00+09:00")).toBeNull();
    expect(screen.queryByText(/2026年8月14日 10:00（UTC）/)).toBeNull();
  });

  it("確認にも読める形の日時を出す", async () => {
    stubApprove();
    renderApprove();
    await userEvent.click(await screen.findByRole("button", { name: "承認する" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("2026年8月14日 10:00");
    expect(dialog).toHaveTextContent("2026年8月14日 20:02");
  });
});

// **`busy` を `false` に倒しても落ちないなら、二重に承認・却下できる。**
// これはリモートの書き換えなので、二重送信も失敗の握り潰しも見過ごせない。
describe("飛んでいる間と、失敗したとき", () => {
  const RECORD = {
    id: "r1",
    destination_id: "d1",
    media_file_id: "m1",
    rel_path: "library/2026/08/DJI_0001.MP4",
    origin: "pre_existing",
    remote_current: "2026-08-14T10:00:00+09:00",
    proposed: "2026-08-14T20:02:00+09:00",
    remote_checked_at: "2026-08-14T09:00:00Z",
    identical: false,
  };

  /** `path` への応答だけを握って止める `fetch`。 */
  function heldFetch(path: string) {
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
        const body = target.startsWith("/uploads") ? { records: [RECORD] } : {};
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
    return release;
  }

  function renderApprove() {
    return render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
  }

  it("却下が飛んでいる間は、承認も却下も押せない", async () => {
    const release = heldFetch("/uploads/r1/reject");
    renderApprove();
    const reject = await screen.findByRole("button", { name: "却下する" });
    const approve = screen.getByRole("button", { name: "承認する" });
    expect(reject).toBeEnabled();
    expect(approve).toBeEnabled();
    await userEvent.click(reject);
    await waitFor(() => expect(reject).toBeDisabled());
    expect(approve).toBeDisabled();
    release();
    await waitFor(() => expect(reject).toBeEnabled());
  });

  it("承認が飛んでいる間は、確認の「実行する」も押せない", async () => {
    const release = heldFetch("/uploads/r1/approve");
    renderApprove();
    await userEvent.click(await screen.findByRole("button", { name: "承認する" }));
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
        if (target === "/uploads/r1/reject") {
          return Promise.resolve(
            new Response(
              JSON.stringify({ error: { code: "internal", detail: "", meta: {} } }),
              { status: 500 },
            ),
          );
        }
        const body = target.startsWith("/uploads") ? { records: [RECORD] } : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderApprove();
    await userEvent.click(await screen.findByRole("button", { name: "却下する" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
