// 押したジョブを名指しで追う（§13）。

import { act, renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { QUEUED_FADE_MS, useQueuedJobs } from "./useQueuedJobs";
import type { Job } from "../components/JobProgress";

function wrapper(jobIds: string[]) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <MemoryRouter initialEntries={[{ pathname: "/", state: { jobIds } }]}>
        {children}
      </MemoryRouter>
    );
  };
}

const succeeded: Job = {
  id: "a",
  type: "merge",
  status: "succeeded",
  created_at: "2026-08-21T00:00:00+00:00",
  last_message: "できた",
};
const failed: Job = {
  id: "b",
  type: "merge",
  status: "failed",
  created_at: "2026-08-21T00:00:00+00:00",
};

describe("useQueuedJobs", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("押したジョブだけを出す", () => {
    const { result } = renderHook(() => useQueuedJobs([succeeded, failed]), {
      wrapper: wrapper(["a"]),
    });
    expect(result.current.queued.map((job) => job.id)).toEqual(["a"]);
  });

  it("成功はホームに着いてから 30 秒で消える", () => {
    const { result } = renderHook(() => useQueuedJobs([succeeded]), {
      wrapper: wrapper(["a"]),
    });
    expect(result.current.queued).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS));
    expect(result.current.queued).toHaveLength(0);
  });

  // **実際の長さ（30 秒）を、定数を経由せず直に書く。** ここを `QUEUED_FADE_MS`
  // 越しに書くと、定数そのものを短くする変異（例: 0 にする）が、変異後の値でも
  // 変異後の値だけ進めて「消えた」と確認することになり、素通りしてしまう。
  it("30 秒経つ前は消えない", () => {
    const { result } = renderHook(() => useQueuedJobs([succeeded]), {
      wrapper: wrapper(["a"]),
    });
    act(() => void vi.advanceTimersByTime(29_999));
    expect(result.current.queued).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(1));
    expect(result.current.queued).toHaveLength(0);
  });

  it("失敗は時間で消えない。見逃した人には「何も起きなかった」と区別が付かない", () => {
    const { result } = renderHook(() => useQueuedJobs([failed]), {
      wrapper: wrapper(["b"]),
    });
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS * 3));
    expect(result.current.queued).toHaveLength(1);
  });

  it("note を渡す。断られた写真と、開始に失敗した宛先を隠さない（§13）", () => {
    function withNote({ children }: { children: React.ReactNode }) {
      return (
        <MemoryRouter
          initialEntries={[{ pathname: "/", state: { jobIds: ["a"], note: "2 件は断られました" } }]}
        >
          {children}
        </MemoryRouter>
      );
    }
    const { result } = renderHook(() => useQueuedJobs([succeeded]), { wrapper: withNote });
    expect(result.current.note).toBe("2 件は断られました");
  });

  it("note は、ジョブが消えても残る。1 本も始まらなかった送信でも知らせが要る", () => {
    function withNote({ children }: { children: React.ReactNode }) {
      return (
        <MemoryRouter
          initialEntries={[{ pathname: "/", state: { jobIds: [], note: "1 件も始まりませんでした" } }]}
        >
          {children}
        </MemoryRouter>
      );
    }
    const { result } = renderHook(() => useQueuedJobs([]), { wrapper: withNote });
    expect(result.current.queued).toHaveLength(0);
    expect(result.current.note).toBe("1 件も始まりませんでした");
  });

  it("× で消せる", () => {
    const { result } = renderHook(() => useQueuedJobs([failed]), {
      wrapper: wrapper(["b"]),
    });
    act(() => result.current.dismiss("b"));
    expect(result.current.queued).toHaveLength(0);
  });

  it("走っている間は消えない。30 秒より長い作業でも結果まで見える", () => {
    const running: Job = { ...succeeded, id: "a", status: "running" };
    const { result, rerender } = renderHook(
      ({ jobs }: { jobs: Job[] }) => useQueuedJobs(jobs),
      { wrapper: wrapper(["a"]), initialProps: { jobs: [running] } },
    );
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS * 2));
    expect(result.current.queued).toHaveLength(1);
    rerender({ jobs: [succeeded] });
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS));
    expect(result.current.queued).toHaveLength(0);
  });
});
