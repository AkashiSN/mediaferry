// 失敗の表示（§13）。**例外の文字列をそのまま出さない** —— ただし「画面が自分で
// 書いた文言」は例外で、そこを潰すと利用者が直せる失敗まで定型文になる。

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ErrorBanner, UserFacingError } from "./ErrorBanner";
import { ApiError } from "../api/errors";

describe("失敗の表示", () => {
  it("何も無ければ何も出さない", () => {
    const { container } = render(<ErrorBanner error={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("API の失敗は、code から引いた日本語を出す", () => {
    render(<ErrorBanner error={new ApiError(404, "not_found", "no such row", {})} />);
    expect(screen.getByRole("alert")).toHaveTextContent("見つかりませんでした");
  });

  it("画面が自分で書いた文言は、そのまま出す", () => {
    render(<ErrorBanner error={new UserFacingError("YAML として読めません（2 行目）。")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("YAML として読めません（2 行目）。");
  });

  it("それ以外の例外は、中身を出さずに定型文にする", () => {
    render(<ErrorBanner error={new TypeError("undefined is not a function")} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("予期しないエラーが起きました");
    expect(alert).not.toHaveTextContent("undefined is not a function");
  });
});
