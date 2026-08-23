// 走っている作業の拍（§13）。**3 画面が同じものを使うので、ここで 1 度だけ確かめる。**

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { isLive, useJobPulse } from "./useJobPulse";
import type { Job } from "../components/JobProgress";

/** 開始から 10 秒で、全体 1000 バイト・この 1 本は 100 バイト進んだ作業。 */
function aJob(progress: Job["progress"]): Job {
  return {
    id: "j1",
    type: "upload",
    status: "running",
    created_at: "2026-08-21T00:00:00+00:00",
    started_at: "2026-08-21T00:00:00+00:00",
    progress,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("進行中かどうか", () => {
  it("キャンセル中も進行中に含める（まだ進捗が動く）", () => {
    expect(isLive(aJob(null))).toBe(true);
    expect(isLive({ ...aJob(null), status: "queued" })).toBe(true);
    expect(isLive({ ...aJob(null), status: "cancelling" })).toBe(true);
  });

  it("終わったものは含めない", () => {
    for (const status of ["succeeded", "failed", "cancelled"]) {
      expect(isLive({ ...aJob(null), status })).toBe(false);
    }
  });
});

describe("開始からの平均速度", () => {
  it("全体の進みを見る（1 本ぶんの進みではない）", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-21T00:00:10+00:00"));
    const { result } = renderHook(() => useJobPulse(true, () => {}));

    const rate = result.current(
      aJob({ phase: "copy", bytes_done: 100, bytes_done_all: 1000 }),
    );

    expect(rate).toBe(100);
  });

  it("1 本ぶんしか無ければ、それを見る", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-21T00:00:10+00:00"));
    const { result } = renderHook(() => useJobPulse(true, () => {}));

    expect(result.current(aJob({ phase: "copy", bytes_done: 100 }))).toBe(10);
  });

  it("始まった直後は出さない（1 秒未満で割らない）", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-21T00:00:00+00:00"));
    const { result } = renderHook(() => useJobPulse(true, () => {}));

    expect(result.current(aJob({ phase: "copy", bytes_done_all: 1000 }))).toBeNull();
  });

  it("まだ進んでいない作業では出さない", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-21T00:00:10+00:00"));
    const { result } = renderHook(() => useJobPulse(true, () => {}));

    expect(result.current(aJob({ phase: "copy", bytes_done_all: 0 }))).toBeNull();
    expect(result.current({ ...aJob({ phase: "copy", bytes_done_all: 10 }), started_at: null })).toBeNull();
  });
});

describe("取り直しの拍", () => {
  it("走っている間は 2 秒ごとに取り直す", () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    renderHook(() => useJobPulse(true, reload));

    expect(reload).toHaveBeenCalledTimes(1);
    act(() => void vi.advanceTimersByTime(4000));
    expect(reload).toHaveBeenCalledTimes(3);
  });

  it("走っていなければ、1 度も取り直さない", () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    renderHook(() => useJobPulse(false, reload));

    act(() => void vi.advanceTimersByTime(10_000));
    expect(reload).not.toHaveBeenCalled();
  });

  it("終わったら止める", () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    const { unmount } = renderHook(() => useJobPulse(true, reload));
    unmount();

    act(() => void vi.advanceTimersByTime(10_000));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
