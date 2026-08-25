import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { MediaTile } from "./MediaTile";

const media = { id: "m1", rel_path: "library/dji-osmo/DCIM/A.MP4", kind: "video" };

function renderTile(props: Record<string, unknown>) {
  return render(
    <MemoryRouter>
      <MediaTile media={media} selected={false} {...props} />
    </MemoryRouter>,
  );
}

describe("MediaTile", () => {
  it("押すと開き、選ぶのは隅の丸", async () => {
    const onToggle = vi.fn();
    renderTile({ to: "/photos/m1", onToggle });

    // **タイル本体は開く道**（リンク）。押しても選択にはならない。
    expect(screen.getByRole("link", { name: "A.MP4" })).toHaveAttribute("href", "/photos/m1");
    await userEvent.click(screen.getByRole("link", { name: "A.MP4" }));
    expect(onToggle).not.toHaveBeenCalled();

    // **選ぶのは丸だけ。**
    await userEvent.click(screen.getByRole("button", { name: "選ぶ：A.MP4" }));
    expect(onToggle).toHaveBeenCalledWith("m1");
  });

  it("選ぶ丸は、選んでいるかどうかを名乗る", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn(), selected: true });
    expect(screen.getByRole("button", { name: "選ぶ：A.MP4" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("つないだ動画だと分かる", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn(), media: { ...media, role: "derived" } });
    expect(screen.getByText("つないだ")).toBeInTheDocument();
  });

  it("元ファイルには印を出さない", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn(), media: { ...media, role: "original" } });
    expect(screen.queryByText("つないだ")).toBeNull();
  });

  it("操作を渡さないときは絵のまま", () => {
    renderTile({});
    expect(screen.getByRole("img", { name: "A.MP4" })).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
