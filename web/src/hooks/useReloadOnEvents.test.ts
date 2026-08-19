import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SETTLE_MS, useReloadOnEvents } from "./useReloadOnEvents";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("進捗が届いたら取り直す", () => {
  it("まとめて 1 回だけ取り直す（1 件ずつ叩かない）", () => {
    const reload = vi.fn();
    const { rerender } = renderHook(({ received }) => useReloadOnEvents(received, reload), {
      initialProps: { received: 0 },
    });

    rerender({ received: 1 });
    rerender({ received: 2 });
    rerender({ received: 3 });
    vi.advanceTimersByTime(SETTLE_MS + 10);

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("**保持の上限に達しても止まらない**（長さではなく総数で見る）", () => {
    const reload = vi.fn();
    const { rerender } = renderHook(({ received }) => useReloadOnEvents(received, reload), {
      initialProps: { received: 200 },
    });

    // 配列の長さは 200 のままでも、総数は増え続ける。
    rerender({ received: 201 });
    vi.advanceTimersByTime(SETTLE_MS + 10);
    rerender({ received: 202 });
    vi.advanceTimersByTime(SETTLE_MS + 10);

    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("何も届かなければ取り直さない", () => {
    const reload = vi.fn();
    const { rerender } = renderHook(({ received }) => useReloadOnEvents(received, reload), {
      initialProps: { received: 5 },
    });

    rerender({ received: 5 });
    vi.advanceTimersByTime(SETTLE_MS * 3);

    expect(reload).not.toHaveBeenCalled();
  });
});
