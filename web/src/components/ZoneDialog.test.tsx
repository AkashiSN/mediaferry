// 撮影地のタイムゾーンの付け替え。

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../test/api";
import { ZoneDialog } from "./ZoneDialog";

describe("撮影地のタイムゾーンのダイアログ", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("候補にあるゾーンを選ぶと、その名前で適用する", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Ho_Chi_Minh", "Asia/Tokyo"] } });
    const onApply = vi.fn();
    render(<ZoneDialog count={2} onApply={onApply} onClose={() => {}} busy={false} />);

    await userEvent.type(await screen.findByLabelText("撮影地のタイムゾーン"), "Asia/Ho_Chi_Minh");
    await userEvent.click(screen.getByRole("button", { name: "付け替える" }));

    expect(onApply).toHaveBeenCalledWith("Asia/Ho_Chi_Minh");
  });

  it("候補に無い名前では適用できない", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Tokyo"] } });
    render(<ZoneDialog count={1} onApply={vi.fn()} onClose={() => {}} busy={false} />);

    await userEvent.type(await screen.findByLabelText("撮影地のタイムゾーン"), "Mars/X");

    expect(screen.getByRole("button", { name: "付け替える" })).toBeDisabled();
  });

  it("上書きを外せる", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Tokyo"] } });
    const onApply = vi.fn();
    render(<ZoneDialog count={1} onApply={onApply} onClose={() => {}} busy={false} />);

    await userEvent.click(await screen.findByRole("button", { name: "上書きを外す" }));

    expect(onApply).toHaveBeenCalledWith(null);
  });

  it("何件を付け替えるのかを書く", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Tokyo"] } });
    render(<ZoneDialog count={3} onApply={vi.fn()} onClose={() => {}} busy={false} />);

    expect(await screen.findByText(/3 件/)).toBeInTheDocument();
  });

  it("送っている最中は押せない", async () => {
    stubApi({ "/timezones": { timezones: ["Asia/Tokyo"] } });
    render(<ZoneDialog count={1} onApply={vi.fn()} onClose={() => {}} busy />);

    await userEvent.type(await screen.findByLabelText("撮影地のタイムゾーン"), "Asia/Tokyo");

    expect(screen.getByRole("button", { name: "付け替える" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "上書きを外す" })).toBeDisabled();
  });
});
