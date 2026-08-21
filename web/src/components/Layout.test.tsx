// ナビは 1 つ。**同じ項目の nav を 2 つ置かない**（読み上げが 2 度になる）。

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Layout } from "./Layout";

function renderAt(path: string, taskCount = 0) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Layout warnings={[]} taskCount={taskCount}>
        <p>中身</p>
      </Layout>
    </MemoryRouter>,
  );
}

describe("画面の枠", () => {
  it("ナビゲーションは 1 つで、項目は 3 つだけ", () => {
    renderAt("/");
    const navs = screen.getAllByRole("navigation");
    expect(navs).toHaveLength(1);
    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(screen.getByRole("link", { name: /ホーム/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /写真/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /設定/ })).toBeInTheDocument();
  });

  it("作業ページを開いている間も、ホームが現在地のまま", () => {
    renderAt("/merge");
    expect(screen.getByRole("link", { name: /ホーム/ })).toHaveAttribute("aria-current", "page");
  });

  it("やることの件数をホームに添える。0 件のときは出さない", () => {
    const { unmount } = renderAt("/", 3);
    expect(screen.getByRole("link", { name: /ホーム/ })).toHaveTextContent("3");
    unmount();
    renderAt("/", 0);
    expect(screen.getByRole("link", { name: /ホーム/ })).not.toHaveTextContent("0");
  });

  it("公開の警告はバナーで出す", () => {
    render(
      <MemoryRouter>
        <Layout warnings={[{ code: "w1", message: "危ない組み合わせ" }]} taskCount={0}>
          <p>中身</p>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("危ない組み合わせ");
  });
});
