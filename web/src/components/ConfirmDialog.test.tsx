import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog, describe as describeConfirmation, formatBytes } from "./ConfirmDialog";
import type { Confirmation } from "./ConfirmDialog";

describe("不可逆な操作の確認", () => {
  it("送信は件数・合計サイズ・宛先名を出す（§13）", () => {
    render(
      <ConfirmDialog
        confirmation={{
          kind: "upload",
          count: 12,
          totalBytes: 3 * 1024 * 1024 * 1024,
          destinationNames: ["home", "backup"],
        }}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText("12 件")).toBeInTheDocument();
    expect(screen.getByText(/合計 3 GiB/)).toBeInTheDocument();
    expect(screen.getByText(/home \/ backup/)).toBeInTheDocument();
  });

  it("宛先の退役では、件数やサイズを求めない（意味が無い）", () => {
    const { body } = describeConfirmation({ kind: "archive_destination", name: "home" });
    expect(body).toBeDefined();
  });

  it("破棄は、公開済みのファイルが消えないことを伝える", () => {
    render(
      <ConfirmDialog
        confirmation={{ kind: "discard_merge_group", groupLabel: "DJI_001", publishedCount: 1 }}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/消えません/)).toBeInTheDocument();
  });

  it("日時の承認は、現在値と変更後を並べる", () => {
    render(
      <ConfirmDialog
        confirmation={{ kind: "approve_datetime", current: null, proposed: "2026-08-17T14:30" }}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    // 読めなかった現在値は「不明」と出す（空欄にして変更なしに見せない）。
    expect(screen.getByText(/（不明）/)).toBeInTheDocument();
    expect(screen.getByText(/2026-08-17T14:30/)).toBeInTheDocument();
  });

  it("実行するまでは何も起きない", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        confirmation={{ kind: "archive_destination", name: "home" }}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "実行する" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("実行中は二重に押せない", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        confirmation={{ kind: "archive_destination", name: "home" }}
        onConfirm={onConfirm}
        onCancel={() => {}}
        busy
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "実行する" }));

    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("種類ごとに、必ず題と本文がある", () => {
    const all: Confirmation[] = [
      { kind: "upload", count: 1, totalBytes: 1, destinationNames: ["a"] },
      { kind: "archive_destination", name: "a" },
      { kind: "discard_merge_group", groupLabel: "a", publishedCount: 0 },
      { kind: "adopt_failed_merge", groupLabel: "a", reason: "サイズ" },
      { kind: "approve_datetime", current: "a", proposed: "b" },
    ];
    for (const confirmation of all) {
      const { title, body } = describeConfirmation(confirmation);
      expect(title.length).toBeGreaterThan(0);
      expect(body).toBeTruthy();
    }
  });
});

describe("大きさの表示", () => {
  it("人が読める単位にする", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(30 * 1024 ** 3)).toBe("30 GiB");
  });
});
