import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../../test/api";
import { ApproveScreen } from "./Approve";

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});
afterEach(() => vi.restoreAllMocks());

describe("確認", () => {
  it("読めなかった値を空欄にしない", async () => {
    // **空欄は「変更なし」に見える。**
    stubApi({
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
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
    stubApi({ "/uploads?state=awaiting_datetime_approval": { records: [] } });
    render(
      <MemoryRouter>
        <ApproveScreen />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/Immich には何も起きません/)).toBeInTheDocument());
  });

  it("承認は確認を取ってから API を叩く", async () => {
    // **不可逆な操作（リモートの書き換え）は確認を必須にする**（§13）。
    const { calls } = stubApi({
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
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
    await userEvent.click(await screen.findByRole("button", { name: "承認する" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("2026-08-14 20:02");
    expect(calls().some((c) => c.method === "POST" && c.path.includes("/approve"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/uploads/r1/approve" && c.method === "POST")).toBe(true),
    );
  });

  it("却下は確認なしで reject を叩く（approve と取り違えない）", async () => {
    // §13「不可逆な操作は確認を取る」の対になる判断: 却下はリモートに触らないので
    // 確認は要らないが、その分だけ**承認と取り違えて叩くと確認なしで書き換わって
    // しまう**。path も method も具体的に見る。
    const { calls } = stubApi({
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
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
    await userEvent.click(await screen.findByRole("button", { name: "却下する" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/uploads/r1/reject" && c.method === "POST")).toBe(true),
    );
    expect(calls().some((c) => c.path.endsWith("/approve"))).toBe(false);
  });

  it("直したい日時が読めなければ空欄にしない", async () => {
    stubApi({
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
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

  it("内部の ID を見出しに出さない", async () => {
    stubApi({
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1-uuid-should-not-appear",
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
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
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
    await waitFor(() => expect(screen.getByText(/2026年8月14日 09:00/)).toBeInTheDocument());
    expect(screen.queryByText("2026-08-14 09:00")).not.toBeInTheDocument();
  });

  it("変更が無い行では承認を促さない", async () => {
    stubApi({
      "/uploads?state=awaiting_datetime_approval": {
        records: [
          {
            id: "r1",
            destination_id: "d1",
            media_file_id: "m1",
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
