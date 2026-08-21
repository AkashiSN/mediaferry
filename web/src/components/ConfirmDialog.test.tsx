import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog, describe as describeConfirmation } from "./ConfirmDialog";
import type { Confirmation } from "./ConfirmDialog";
import { FORBIDDEN } from "../test/vocabulary";

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
    for (const confirmation of EVERY_KIND) {
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

  // **内部の名前を出さない**（§13）。E2E は全画面を巡るが、**ダイアログは開くまで
  // 描かれない**ので、確認の本文にはここでしか届かない。一覧は E2E と共有する。
  it("本文に内部の名前が出ていない", () => {
    for (const confirmation of EVERY_KIND) {
      const { unmount } = render(
        <ConfirmDialog confirmation={confirmation} onConfirm={() => {}} onCancel={() => {}} />,
      );
      const text = screen.getByRole("dialog").textContent ?? "";
      for (const word of FORBIDDEN) {
        expect(text, `${confirmation.kind} に「${word}」が出ている`).not.toContain(word);
      }
      unmount();
    }
  });
});

/**
 * `Confirmation` の全種類。**1 つでも欠けると、その本文は誰も読まない。**
 *
 * `Record<Confirmation["kind"], …>` にしてあるので、**union に種類を足すと
 * ここが型エラーになる**（一覧だと黙って抜ける）。
 */
const BY_KIND: Record<Confirmation["kind"], Confirmation> = {
  upload: { kind: "upload", count: 1, totalBytes: 1, destinationNames: ["a"] },
  archive_destination: { kind: "archive_destination", name: "a" },
  discard_merge_group: { kind: "discard_merge_group", groupLabel: "a", publishedCount: 1 },
  delete_merge_history: { kind: "delete_merge_history", groupLabel: "a" },
  delete_stale_derived: { kind: "delete_stale_derived", relPath: "derived/a.MP4" },
  remerge_group: { kind: "remerge_group", groupLabel: "a" },
  adopt_failed_merge: { kind: "adopt_failed_merge", groupLabel: "a", reason: "サイズ" },
  approve_datetime: { kind: "approve_datetime", current: "a", proposed: "b" },
  archive_profile: { kind: "archive_profile", slug: "a" },
  trust_volume: { kind: "trust_volume", label: "a", state: "starts", reason: null },
};

// `trust_volume` は state で本文が丸ごと入れ替わるので、残り 2 つも巡る。
const EVERY_KIND: Confirmation[] = [
  ...Object.values(BY_KIND),
  { kind: "trust_volume", label: "a", state: "pending", reason: "確かめられた場合" },
  { kind: "trust_volume", label: "a", state: "blocked", reason: "設定が off な" },
];


// **取り消せない操作の確認なので、キーボードだけで扱えること。** `aria-modal="true"`
// を名乗る以上、背後は無いことになっている —— そこへ焦点が抜けると、読み上げでは
// 何も無い場所を触ることになる。
describe("キーボードだけで扱える", () => {
  const UPLOAD: Confirmation = {
    kind: "upload",
    count: 1,
    totalBytes: 1024,
    destinationNames: ["home"],
  };

  it("Escape で閉じる", async () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog confirmation={UPLOAD} onConfirm={() => {}} onCancel={onCancel} />);

    await userEvent.keyboard("{Escape}");

    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("実行中は Escape でも閉じない（「やめる」も押せない状態と揃える）", async () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog confirmation={UPLOAD} onConfirm={() => {}} onCancel={onCancel} busy />);

    await userEvent.keyboard("{Escape}");

    expect(onCancel).not.toHaveBeenCalled();
  });

  it("開いたら焦点がダイアログの中に入る", () => {
    render(<ConfirmDialog confirmation={UPLOAD} onConfirm={() => {}} onCancel={() => {}} />);

    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
  });

  it("Tab を押し続けても、背後の要素へ抜けない", async () => {
    render(
      <>
        <button type="button">背後のボタン</button>
        <ConfirmDialog confirmation={UPLOAD} onConfirm={() => {}} onCancel={() => {}} />
      </>,
    );
    const dialog = screen.getByRole("dialog");

    for (let press = 0; press < 5; press += 1) {
      await userEvent.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
    await userEvent.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it("末尾から Tab で先頭へ、先頭から Shift+Tab で末尾へ回る", async () => {
    render(<ConfirmDialog confirmation={UPLOAD} onConfirm={() => {}} onCancel={() => {}} />);
    const cancel = screen.getByRole("button", { name: "やめる" });
    const confirm = screen.getByRole("button", { name: "実行する" });

    // 開いた直後は先頭（「やめる」）。
    expect(document.activeElement).toBe(cancel);
    await userEvent.tab();
    expect(document.activeElement).toBe(confirm);
    // **末尾からは先頭へ回る**（背後へ抜けない）。
    await userEvent.tab();
    expect(document.activeElement).toBe(cancel);
    // **先頭からは末尾へ回る。**
    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(confirm);
  });

  it("閉じたら、開く前に触っていたところへ焦点が戻る", () => {
    render(<button type="button">開いたボタン</button>);
    const opener = screen.getByRole("button", { name: "開いたボタン" });
    opener.focus();

    const dialog = render(
      <ConfirmDialog confirmation={UPLOAD} onConfirm={() => {}} onCancel={() => {}} />,
    );
    dialog.unmount();

    expect(document.activeElement).toBe(opener);
  });
});
