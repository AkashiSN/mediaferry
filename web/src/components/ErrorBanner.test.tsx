// 失敗の表示（§13）。**例外の文字列をそのまま出さない** —— ただし「画面が自分で
// 書いた文言」は例外で、そこを潰すと利用者が直せる失敗まで定型文になる。

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  // **同じ失敗が出直しても、閉じたものは閉じたまま。** `useReloadOnEvents` は
  // 取り込み中に 1 秒おきに取り直すので、失敗が続いていると例外が毎回作り直され、
  // 「閉じる」を押しても 1 秒で戻ってくる。
  it("同じ内容の失敗が作り直されても、閉じたままにする", async () => {
    const first = new ApiError(500, "internal", "", {});
    const { rerender } = render(<ErrorBanner error={first} onDismiss={() => undefined} />);
    await userEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(screen.queryByRole("alert")).toBeNull();

    rerender(<ErrorBanner error={new ApiError(500, "internal", "", {})} onDismiss={() => undefined} />);

    expect(screen.queryByRole("alert")).toBeNull();
  });

  // 呼び出し側は自分の state と `useQuery` の失敗を `??` で束ねて渡す。2 つ目を
  // 閉じたときに 1 つ目が戻ってくると、閉じたはずの帯が復活する。
  it("束ねて渡された 2 つを順に閉じたら、どちらも戻ってこない", async () => {
    const background = new ApiError(500, "internal", "", {});
    const action = new ApiError(409, "conflict", "", {});
    let latest: unknown = background;
    const { rerender } = render(
      <ErrorBanner error={latest} onDismiss={() => undefined} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "閉じる" }));

    // 押した操作が失敗して、別の失敗が前に出る。
    latest = action;
    rerender(<ErrorBanner error={latest} onDismiss={() => undefined} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "閉じる" }));

    // 呼び出し側が自分の失敗を消すと、束の後ろにいた失敗が前に出る。
    latest = background;
    rerender(<ErrorBanner error={latest} onDismiss={() => undefined} />);

    expect(screen.queryByRole("alert")).toBeNull();
  });

  // **もう一度押して、また失敗したら出す。** 出さないと、押しても何も起きない
  // ボタンになる。
  it("いったん失敗が消えたあとの、同じ失敗は出す", async () => {
    const { rerender } = render(
      <ErrorBanner error={new ApiError(409, "conflict", "", {})} onDismiss={() => undefined} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "閉じる" }));

    // 押し直すと、`useMutation.run` が走り出しに失敗を消す。
    rerender(<ErrorBanner error={null} onDismiss={() => undefined} />);
    rerender(<ErrorBanner error={new ApiError(409, "conflict", "", {})} onDismiss={() => undefined} />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
