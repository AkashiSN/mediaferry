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

  // `Home.tsx` の `CardBanner` は `Devices.tsx` の `act` とほぼ同じ配線を
  // 複製している。`Devices.tsx` 側のクリック試験は `screens.test.tsx` に
  // あるが、Home 側の配線だけに入った書き間違い（例えば `import` を `scan`
  // と書き違える）はそちらのテストでは検出できない。
  const actionableVolume = {
    volume_instance_id: "v1",
    fs_label: "OSMO",
    profile_slug: "dji-osmo",
    identity_confidence: "high",
    provisional: false,
    trusted: true,
    reason: "DCIM がある",
  };

  it("「いま取り込む」を押すと、そのカードの取り込みを始める", async () => {
    const api = stubApi({
      "/dashboard": EMPTY_DASHBOARD,
      "/devices": { volumes: [actionableVolume] },
      "/jobs": { jobs: [] },
      "/settings": { settings: [{ key: "AUTO_IMPORT", value: "trusted" }], warnings: [] },
    });
    renderHome();

    await userEvent.click(await screen.findByRole("button", { name: "いま取り込む" }));

    await waitFor(() =>
      expect(
        api.calls().some((call) => call.path === "/volumes/v1/import" && call.method === "POST"),
      ).toBe(true),
    );
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

  it("「中身を見る」を押すと、カードの中身のページへ行く（Ruling 30）", async () => {
    // ホームのカードの帯からカードの中身へ行ける（§13）。`/card` のルートは
    // まだ無いので（Task 12 が生やす）、遷移先だけを直に確かめる。
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

  // Ruling 8: 積んだまま送信が始まっていない `pending` は「まだ送っていない」から
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

  it("進捗の接続が切れていると、そう出す", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    expect(await screen.findByRole("status")).toHaveTextContent("進捗の接続が切れています");
  });

  it("つながっている間は、接続が切れているという表示を出さない", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await screen.findByRole("status");
    openStream();
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("つながった後に切れたら、また表示を出す", async () => {
    stubApi({ "/dashboard": EMPTY_DASHBOARD, "/devices": { volumes: [] }, "/jobs": { jobs: [] } });
    renderHome();
    await screen.findByRole("status");
    openStream();
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    failStream();
    expect(await screen.findByRole("status")).toHaveTextContent("進捗の接続が切れています");
  });
});
