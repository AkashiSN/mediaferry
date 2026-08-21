// ホーム（§13）。**やることが無いときは、無いと書く。**

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
});
