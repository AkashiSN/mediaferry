// 走っている作業の拍（§13「進捗は必ずファイル名と件数で示す」）。
//
// **ホーム・作業の履歴・送信中の 3 画面が、同じ拍を必要とする。** 写しを 3 つ
// 持つと、間隔や速度の出し方が片方だけずれる。

import { useEffect, useState } from "react";

import type { Job } from "../components/JobProgress";

/** 進捗を持ちうる状態（`api/routes_system.py` の `_LIVE_STATUSES` と揃える）。 */
export const LIVE_STATUSES = ["queued", "running", "cancelling"];

/** その作業がまだ動いているか。 */
export function isLive(job: Job): boolean {
  return LIVE_STATUSES.includes(job.status);
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
