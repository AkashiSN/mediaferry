// 押したジョブを名指しで追う（§13）。**ホームが進捗と結果の唯一の置き場**で、
// ジョブを積む操作はここへ遷移してくる。
//
// **時計は「ホームに着いてから」クライアント側で計る。** `finished_at` との
// 引き算にすると、ブラウザとサーバの時計がずれていたとき、遅れていれば
// 「未来に終わったジョブ」が居座り、進んでいれば一度も出ない。
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

import type { Job } from "../components/JobProgress";
import { isLive } from "./useJobPulse";

/** 成功した結果を出しておく長さ。押してから目を上げ、読み返せる程度。 */
export const QUEUED_FADE_MS = 30_000;

export function useQueuedJobs(jobs: Job[]): {
  queued: Job[];
  note: string | null;
  dismiss: (id: string) => void;
} {
  const location = useLocation();
  const state = location.state as { jobIds?: string[]; note?: string | null } | null;
  const wanted = useMemo(() => new Set(state?.jobIds ?? []), [state]);
  const [dropped, setDropped] = useState<ReadonlySet<string>>(new Set());

  const queued = useMemo(
    () => jobs.filter((job) => wanted.has(job.id) && !dropped.has(job.id)),
    [jobs, wanted, dropped],
  );

  // **成功したものだけ時間で落とす。** 失敗は利用者が読むまで残す。
  const fading = queued
    .filter((job) => !isLive(job) && job.status === "succeeded")
    .map((job) => job.id)
    .join(",");

  useEffect(() => {
    if (!fading) {
      return;
    }
    const ids = fading.split(",");
    const timer = setTimeout(() => {
      setDropped((before) => new Set([...before, ...ids]));
    }, QUEUED_FADE_MS);
    return () => clearTimeout(timer);
  }, [fading]);

  const dismiss = useCallback((id: string) => {
    setDropped((before) => new Set([...before, id]));
  }, []);

  // **`note` はジョブと独立に返す。** 1 本も始まらなかった送信では `jobIds` が
  // 空になるが、そのときこそ知らせが要る。
  return { queued, note: state?.note ?? null, dismiss };
}
