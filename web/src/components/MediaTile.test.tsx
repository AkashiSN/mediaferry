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

  // **`to` と `onToggle` は独立に渡せる。** 両方渡すテスト（上）だけでは
  // 「開く道が無ければ絵」と「選ぶ道が無ければ絵」の境界が別々に検査されない
  // ——`if (!to && !onToggle)` を `if (!to || !onToggle)` に崩しても、両方渡す
  // テストだけでは片方が欠けたときの分岐まで踏めず検出できない。
  it("to だけ渡すと開く道はあるが、選ぶ丸は出ない", () => {
    renderTile({ to: "/photos/m1" });
    expect(screen.getByRole("link", { name: "A.MP4" })).toHaveAttribute("href", "/photos/m1");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("onToggle だけ渡すと選ぶ丸はあるが、開く道は出ない", () => {
    renderTile({ onToggle: vi.fn() });
    expect(screen.getByRole("button", { name: "選ぶ：A.MP4" })).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("選んでいないタイルにはチェックマークを出さない", () => {
    const { container } = renderTile({ to: "/photos/m1", onToggle: vi.fn(), selected: false });
    expect(container.querySelector(".pick svg")).toBeNull();
  });

  it("RAW も一緒にあると分かる", () => {
    renderTile({
      to: "/photos/m1",
      onToggle: vi.fn(),
      media: {
        ...media,
        stack: {
          members: [
            { id: "m1", rel_path: "library/dji-osmo/DCIM/A.JPG", size_bytes: 100 },
            { id: "m2", rel_path: "library/dji-osmo/DCIM/A.CR2", size_bytes: 200 },
          ],
        },
      },
    });
    expect(screen.getByText("RAW")).toBeInTheDocument();
  });

  it("組でなければ RAW とは書かない", () => {
    renderTile({ to: "/photos/m1", onToggle: vi.fn() });
    expect(screen.queryByText("RAW")).toBeNull();
  });

  // **「つないだ」と `RAW` は独立に立つ。** どちらを出すかは `role` と `stack` が
  // 別々に決めるので、両方立った行がありうる。同じ座標へ重ねると片方が読めなく
  // なるため、1 つの入れ物へ横に並べる（`styles.css` の `.madeofs`）。
  it("つないだ動画が組でもあれば、札を 2 つとも並べる", () => {
    const { container } = renderTile({
      to: "/photos/m1",
      onToggle: vi.fn(),
      media: {
        ...media,
        role: "derived",
        stack: {
          members: [
            { id: "m1", rel_path: "library/dji-osmo/DCIM/A.JPG", size_bytes: 100 },
            { id: "m2", rel_path: "library/dji-osmo/DCIM/A.CR2", size_bytes: 200 },
          ],
        },
      },
    });
    // **同じ入れ物の兄弟として並ぶ**ことを見る。別々に絶対配置すると重なる。
    const badges = [...container.querySelectorAll(".madeofs > .madeof")];
    expect(badges.map((badge) => badge.textContent)).toEqual(["つないだ", "RAW"]);
  });
});
