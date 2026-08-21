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

  // **Markdown は JSX の中で効かない。** `**強調**` と書くとアスタリスクが
  // そのまま画面に出る。取り消せない操作の確認文で起きるといちばん読みにくい。
  it("本文に Markdown の記号が残っていない", () => {
    for (const confirmation of EVERY_KIND) {
      const { unmount } = render(
        <ConfirmDialog confirmation={confirmation} onConfirm={() => {}} onCancel={() => {}} />,
      );
      expect(screen.getByRole("dialog").textContent ?? "").not.toContain("**");
      unmount();
    }
  });

  // **確認の本文は、画面にあるボタンの名前で言う**（§13 の言い換え）。
  it("本文が、もう無いボタンの名前を指していない", () => {
    for (const confirmation of EVERY_KIND) {
      const { unmount } = render(
        <ConfirmDialog confirmation={confirmation} onConfirm={() => {}} onCancel={() => {}} />,
      );
      expect(screen.getByRole("dialog").textContent ?? "").not.toContain("候補を検出する");
      unmount();
    }
  });
});

/** `Confirmation` の全種類。**1 つでも欠けると、その本文は誰も読まない。** */
const EVERY_KIND: Confirmation[] = [
  { kind: "upload", count: 1, totalBytes: 1, destinationNames: ["a"] },
  { kind: "archive_destination", name: "a" },
  { kind: "discard_merge_group", groupLabel: "a", publishedCount: 1 },
  { kind: "delete_merge_history", groupLabel: "a" },
  { kind: "delete_stale_derived", relPath: "derived/a.MP4" },
  { kind: "remerge_group", groupLabel: "a" },
  { kind: "adopt_failed_merge", groupLabel: "a", reason: "サイズ" },
  { kind: "approve_datetime", current: "a", proposed: "b" },
  { kind: "archive_profile", slug: "a" },
  { kind: "trust_volume", label: "a", state: "starts", reason: null },
  { kind: "trust_volume", label: "a", state: "pending", reason: "確かめられた場合" },
  { kind: "trust_volume", label: "a", state: "blocked", reason: "設定が off な" },
];

describe("大きさの表示", () => {
  it("人が読める単位にする", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(30 * 1024 ** 3)).toBe("30 GiB");
  });
});
