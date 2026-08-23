// 走っている作業の拍（§13「進捗は必ずファイル名と件数で示す」）。
//
// **ホーム・作業の履歴・送信中の 3 画面が、同じ拍を必要とする。** 写しを 3 つ
// 持つと、間隔や速度の出し方が片方だけずれる。

import { useEffect, useState } from "react";

import type { Job } from "../components/JobProgress";

/** 進捗を持ちうる状態（`api/routes_system.py` の `_LIVE_STATUSES` と揃える）。 */
export const LIVE_STATUSES = ["queued", "running", "cancelling"];

/** その作業がまだ動いているか（待機中も含む）。 */
export function isLive(job: Job): boolean {
  return LIVE_STATUSES.includes(job.status);
}

/** いま実際に走っているか（待機中は含まない）。 */
function isRunning(job: Job): boolean {
  return job.status === "running" || job.status === "cancelling";
}

/**
 * いま画面に出す作業を 1 つ選ぶ。
 *
 * **走っているものを優先する。** 一覧は新しい順に来るので、先頭から `isLive` で
 * 拾うと最後に積んだ待機中を掴む。「いま取り込む」は スキャン → コピー →
 * 候補の検出 の 3 本を積むため、コピーの間ずっと「候補の検出・待機中」を出す
 * ことになり、進捗も出ず、中止も別の作業に当たる。
 *
 * 走っているものが無ければ、**次に走る＝いちばん古い待機中**を返す。並び順に
 * 頼らず `created_at` で選ぶ（一覧の順は API 側の都合で変わりうる）。
 */
export function pickLiveJob(jobs: Job[]): Job | undefined {
  const running = jobs.find(isRunning);
  if (running !== undefined) {
    return running;
  }
  return jobs
    .filter((job) => job.status === "queued")
    .reduce<Job | undefined>(
      (oldest, job) => (oldest === undefined || job.created_at < oldest.created_at ? job : oldest),
      undefined,
    );
}

/**
 * 走っている間だけ 2 秒ごとに `reload` を呼び、**開始からの平均速度**を返す関数を
 * 渡す。
 *
 * **進捗はイベントではないので、走っている間だけ引きに行く。** SSE は「ファイルを
 * 1 つ取り込んだ」のような節目しか流さない（16 GiB のコピーの途中では何も来ない）。
 *
 * **速度は開始からの平均。** 2 点の差分は state か ref が要り、どちらも描画中には
 * 触れない。長い処理では平均の方が値も落ち着く。
 *
 * 「いま」も取り直しと同じ拍で進める。**描画中に `Date.now()` は呼べない**
 * （呼ぶと、たまたま起きた再描画で値が変わる）。
 */
export function useJobPulse(running: boolean, reload: () => void): (job: Job) => number | null {
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!running) {
      return;
    }
    const tick = () => {
      setNow(Date.now());
      reload();
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => clearInterval(timer);
  }, [running, reload]);

  return (job: Job): number | null => {
    const done = job.progress?.bytes_done_all ?? job.progress?.bytes_done;
    if (!job.started_at || done === undefined || done <= 0) {
      return null;
    }
    const seconds = (now - Date.parse(job.started_at)) / 1000;
    return seconds > 1 ? done / seconds : null;
  };
}
