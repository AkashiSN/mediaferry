// ホーム（§13）。**やることが無いときは、無いと書く。**

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { openStream, failStream } from "../test/setup";
import { stubApi } from "../test/api";
import { HomeScreen } from "./Home";

const EMPTY_DASHBOARD = {
  media_total: 0,
  destinations: [],
  running_jobs: 0,
  recent_imports: [],
  orphans: 0,
  missing: 0,
  warnings: [],
  merge_candidates: 0,
  unsent_total: 0,
  awaiting_total: 0,
};

function renderHome() {
  return render(
    <MemoryRouter>
      <HomeScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ホーム", () => {
  it("やることを、在るものだけ出す", async () => {
    stubApi({
      "/dashboard": { ...EMPTY_DASHBOARD, merge_candidates: 3, unsent_total: 48 },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/分かれている動画を 3 本つなぐ/)).toBeInTheDocument());
    expect(screen.getByText(/48 件をまだ送っていません/)).toBeInTheDocument();
    expect(screen.queryByText(/確認があります/)).not.toBeInTheDocument();
  });

  // §13「内部の名前をそのまま出さない」「日時は人が読める形で出す」。相対パスも
  // 生の ISO 文字列も内部の表現で、どちらも画面に出すものではない。
  it("さっき取り込んだものは、ファイル名と読める日時で出す", async () => {
    stubApi({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        recent_imports: [
          { id: "m1", rel_path: "2026/08/21/DJI_0043.MP4", captured_at: "2026-08-21T14:05:33+09:00" },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() =>
      expect(screen.getByText("DJI_0043.MP4（2026年8月21日 14:05）")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/2026\/08\/21\/DJI_0043\.MP4/)).toBeNull();
    expect(screen.queryByText(/14:05:33/)).toBeNull();
    // 「すべて」は写真の画面へ行く。
    expect(screen.getByRole("link", { name: "すべて" })).toHaveAttribute("href", "/photos");
  });

  it("やることが 1 つも無ければ、無いと書く", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await waitFor(() =>
      expect(screen.getByText("いま、やることはありません")).toBeInTheDocument(),
    );
  });

  it("読み込み中は「やることはありません」を出さない", () => {
    // **0 件と読み込み中を混ぜない。** 直後に 3 件現れると驚かせる。
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    expect(screen.queryByText("いま、やることはありません")).not.toBeInTheDocument();
  });

  it("挿さっているカードを、信頼していなければそう書く", async () => {
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "OSMO",
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM がある",
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText("初めて見るカードです")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "このカードを信頼する" })).toBeInTheDocument();
  });

  it("カメラの種類は、生の slug ではなく表示名を出す（§13）", async () => {
    // `work/CardDetail.tsx` の `profileDisplayName` と同じ引き当てを使う。
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "OSMO",
            profile_slug: "dji-osmo",
            identity_confidence: "high",
            provisional: true,
            trusted: false,
            reason: "DCIM がある",
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
      "/profiles": { profiles: [{ slug: "dji-osmo", name: "DJI Osmo Pocket" }] },
    });
    renderHome();

    expect(await screen.findByText("DJI Osmo Pocket のカードのようです。")).toBeInTheDocument();
    expect(
      await screen.findByText("DJI Osmo Pocket の対象ですが、取り込む中身がまだありません。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/dji-osmo/)).toBeNull();
  });

  it("カメラの種類は、登録が無い slug だけフォールバックで出す", async () => {
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          {
            volume_instance_id: "v1",
            fs_label: "OSMO",
            profile_slug: "unknown-cam",
            identity_confidence: "high",
            provisional: false,
            trusted: false,
            reason: "DCIM がある",
          },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
      "/profiles": { profiles: [{ slug: "dji-osmo", name: "DJI Osmo Pocket" }] },
    });
    renderHome();

    expect(await screen.findByText("unknown-cam のカードのようです。")).toBeInTheDocument();
  });

  // **押した先の配線を、押して確かめる。** `CardBanner` のボタンは
  // `work/CardDetail.tsx` と同じ 4 つの操作（`trust` / `scan` / `import` /
  // `close`）を叩くが、**この画面だけに入った書き間違い**（`import` を `scan` と
  // 書き違える等）は、そちらの試験では捕まらない。
  const actionableVolume = {
    volume_instance_id: "v1",
    fs_label: "OSMO",
    profile_slug: "dji-osmo",
    identity_confidence: "high",
    provisional: false,
    trusted: true,
    reason: "DCIM がある",
  };

  it("「いま取り込む」を押すと、数えてから取り込み、分かれた動画まで探す", async () => {
    const api = stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    await userEvent.click(await screen.findByRole("button", { name: "いま取り込む" }));

    // **数えるのが先。** 取り込みのジョブは前のスキャンが残した記録を読むので、
    // 数えずに取り込むとジョブは成功のまま 1 件も取り込まない。
    await waitFor(() =>
      expect(
        api.calls().some((call) => call.path === "/volumes/v1/import" && call.method === "POST"),
      ).toBe(true),
    );
    const posts = api.calls().filter((call) => call.method === "POST");
    expect(posts.map((call) => call.path)).toEqual([
      "/volumes/v1/scan",
      "/volumes/v1/import",
      "/merge-groups/detect?profile_slug=dji-osmo",
    ]);
  });

  it("対象外のカードには「いま取り込む」を出さない（探す先も無い）", async () => {
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [{ ...actionableVolume, profile_slug: null }] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    await waitFor(() => expect(screen.getByText(/対象外の理由/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "いま取り込む" })).toBeNull();
  });

  it("「取り外す」を押すと、そのカードを取り外す", async () => {
    const api = stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    await userEvent.click(await screen.findByRole("button", { name: "取り外す" }));

    await waitFor(() =>
      expect(
        api.calls().some((call) => call.path === "/volumes/v1/close" && call.method === "POST"),
      ).toBe(true),
    );
  });

  it("ラベルが無いカードが複数あると、見出しを連番で見分けられるようにする", async () => {
    // `CardBanner` は `work/CardDetail.tsx` の `volumeLabel` を使う。一覧全体を
    // 渡さないと、複数枚が同時にラベル無しのとき見分けが付かない。
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": {
        volumes: [
          { ...actionableVolume, fs_label: "" },
          { ...actionableVolume, volume_instance_id: "v2", fs_label: "" },
        ],
      },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    expect(
      await screen.findByText("名前の無いカード 1 のカードが挿さっています"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("名前の無いカード 2 のカードが挿さっています"),
    ).toBeInTheDocument();
  });

  it("「中身を見る」を押すと、カードの中身のページへ行く（裁定 30）", async () => {
    // ホームのカードの帯からカードの中身へ行ける（§13）。**ここは帯の配線だけを
    // 見る** —— ルート表そのものは `App.test.tsx` が受け持つ。
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<HomeScreen />} />
          <Route path="/card" element={<div>カードの中身のページ</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("link", { name: "中身を見る" }));

    expect(await screen.findByText("カードの中身のページ")).toBeInTheDocument();
  });

  it("進行中の作業があれば、ファイル名と件数で出す", async () => {
    stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [] },
      "/jobs": {
        jobs: [
          {
            id: "j1",
            type: "import",
            status: "running",
            created_at: "2026-08-18T05:00:00Z",
            started_at: "2026-08-18T05:00:00Z",
            progress: {
              phase: "copy",
              rel_path: "DCIM/100MEDIA/DJI_0043.MP4",
              file_index: 12,
              file_count: 87,
              bytes_done: 1024,
              bytes_total: 4096,
            },
          },
        ],
      },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/12\/87 件/)).toBeInTheDocument());
    expect(screen.getByText(/DJI_0043\.MP4/)).toBeInTheDocument();
  });

  // 裁定 8: 積んだまま送信が始まっていない `pending` は「まだ送っていない」から
  // 消えたので、止まった送信に気づけるよう送り先の行に別枠で出す。
  it("宛先に積んだまま止まっているものを「送信中」で出す", async () => {
    stubApi({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        destinations: [
          {
            destination_id: "d1",
            name: "home",
            enabled: true,
            complete: 5,
            failed: 0,
            awaiting_approval: 0,
            pending: 3,
            unsent: 0,
            stacked: 0,
            stack_skipped: 0,
          },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/送信中 3 件/)).toBeInTheDocument());
  });

  // **送れなかったものは、ここに出さないとどの画面にも出ない。**「まだ送って
  // いない」にも「送信済み」にも入らない（`docs/design.md` §10）。
  it("送れなかったものがあれば、送り先の行に件数を出す", async () => {
    stubApi({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        destinations: [
          {
            destination_id: "d1",
            name: "home",
            enabled: true,
            complete: 5,
            failed: 2,
            awaiting_approval: 0,
            pending: 0,
            unsent: 0,
            stacked: 0,
            stack_skipped: 0,
          },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/送れなかった 2 件/)).toBeInTheDocument());
  });

  it("送れなかったものが無ければ、その枠は出さない", async () => {
    stubApi({
      "/dashboard": {
        ...EMPTY_DASHBOARD,
        destinations: [
          {
            destination_id: "d1",
            name: "home",
            enabled: true,
            complete: 5,
            failed: 0,
            awaiting_approval: 0,
            pending: 0,
            unsent: 0,
            stacked: 0,
            stack_skipped: 0,
          },
        ],
      },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/送信済み 5/)).toBeInTheDocument());
    expect(screen.queryByText(/送れなかった/)).toBeNull();
  });

  // `docs/decisions.md` の「孤立ファイルは報告するだけ」の **報告** にあたる。
  // 消す操作は置かないが、黙ってもいけない。
  it("行き場の無いファイルと、見つからないファイルを報告する", async () => {
    stubApi({
      "/dashboard": { ...EMPTY_DASHBOARD, orphans: 3, missing: 1 },
      "/devices": { volumes: [] },
      "/jobs": { jobs: [] },
    });
    renderHome();
    const note = await screen.findByRole("status");
    expect(note).toHaveTextContent("どこにも結び付いていないファイル 3 件");
    expect(note).toHaveTextContent("見つからないファイル 1 件");
    // **削除の操作は足さない**（自動削除はデータを失う経路になる）。
    expect(note).toHaveTextContent("自動では消しません");
  });

  it("どちらも 0 件なら、報告そのものを出さない", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await waitFor(() => expect(screen.getByText("いま、やることはありません")).toBeInTheDocument());
    expect(screen.queryByText(/どこにも結び付いていないファイル/)).toBeNull();
  });

  it("開いた直後は、接続が切れているとは出さない（まだ繋がったことが無いだけなので）", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await waitFor(() => expect(screen.getByText("いま、やることはありません")).toBeInTheDocument());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("接続が切れたら、そう出す", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    failStream();
    expect(await screen.findByRole("status")).toHaveTextContent("進捗の接続が切れています");
  });

  it("つながったら、表示を消す", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    failStream();
    await screen.findByRole("status");
    openStream();
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });
});
