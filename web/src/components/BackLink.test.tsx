// 戻る先（§13）。**作業の画面には入口が 2 つある**ので、来た道で決める。

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { BackLink } from "./BackLink";

function renderAt(state?: unknown) {
  render(
    <MemoryRouter initialEntries={[{ pathname: "/merge", state }]}>
      <Routes>
        <Route path="/" element={<p>ホーム画面</p>} />
        <Route path="/settings" element={<p>設定画面</p>} />
        <Route path="/merge" element={<BackLink />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("戻る先", () => {
  it("遷移元が無ければホームへ（URL を直接開いた場合）", async () => {
    renderAt(undefined);
    const link = await screen.findByRole("link", { name: /ホームへ/ });
    await userEvent.click(link);
    expect(await screen.findByText("ホーム画面")).toBeInTheDocument();
  });

  it("設定から来たときは設定へ戻る", async () => {
    renderAt({ from: "/settings" });
    const link = await screen.findByRole("link", { name: /設定へ/ });
    await userEvent.click(link);
    expect(await screen.findByText("設定画面")).toBeInTheDocument();
  });

  it("知らない行き先は受け取らない（名前も行き先もホーム）", async () => {
    // **`state` は画面から来る値なので、そのまま行き先にしない。** 知っている
    // 入口だけを行き先にする。**名前だけを見ても足りない** —— 名前は「ホームへ」
    // でも行き先が渡された文字列のまま、という壊れ方をする。
    renderAt({ from: "https://example.invalid/" });
    const link = await screen.findByRole("link", { name: /ホームへ/ });
    expect(link).toHaveAttribute("href", "/");
  });
});
