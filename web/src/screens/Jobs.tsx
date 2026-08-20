// ジョブ（§13）。一覧と、進捗・ログ・キャンセル。

import { useEffect, useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";
import { progressLine, statusLabel } from "../components/JobProgress";
import type { Job } from "../components/JobProgress";
import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";

type Jobs = { jobs: Job[] };

export function JobsScreen() {
  const jobs = useQuery<Jobs>("/jobs");
  const { events, received, connected } = useEvents();
  const [error, setError] = useState<unknown>(null);
  useReloadOnEvents(received, jobs.reload);

  // **進捗はイベントではないので、走っている間だけ引きに行く。** SSE は
  // 「ファイルを 1 つ取り込んだ」のような節目しか流さない（16 GiB のコピーの
  // 途中では何も来ない）。
  const running = (jobs.data?.jobs ?? []).some((job) =>
    ["queued", "running", "cancelling"].includes(job.status),
  );
  // 「いま」も取り直しと同じ拍で進める。**描画中に `Date.now()` は呼べない**
  // （呼ぶと、たまたま起きた再描画で値が変わる）。
  const [now, setNow] = useState(0);
  const reload = jobs.reload;
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

  // **速度は 2 点の差分から出す。** サーバに持たせると、心拍の間隔に依存した
  // 値を永続化することになる。

  // **画面を再読み込みせずに進む。** 届いたイベントのジョブだけ取り直す。
  const latestByJob = new Map(events.map((event) => [event.job_id, event]));

  async function cancel(jobId: string) {
    try {
      await request(`/jobs/${jobId}/cancel`, { method: "POST" });
      jobs.reload();
    } catch (caught) {
      setError(caught);
    }
  }

  // **速度は開始からの平均。** 2 点の差分は state か ref が要り、どちらも
  // 描画中には触れない（連鎖レンダーと、描画中の ref 参照はどちらも禁じ手）。
  // 長い処理では平均の方が値も落ち着く。
  function averageRate(job: Job): number | null {
    const done = job.progress?.bytes_done_all ?? job.progress?.bytes_done;
    if (!job.started_at || done === undefined || done <= 0) {
      return null;
    }
    const seconds = (now - Date.parse(job.started_at)) / 1000;
    return seconds > 1 ? done / seconds : null;
  }

  return (
    <section aria-label="ジョブ">
      <h1>ジョブ</h1>
      <ErrorBanner error={error ?? jobs.error} onDismiss={() => setError(null)} />
      {!connected && <p role="status">進捗の接続が切れています。再接続を待っています…</p>}
      <table>
        <thead>
          <tr>
            <th>種類</th>
            <th>状態</th>
            <th>進捗</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(jobs.data?.jobs ?? []).map((job) => (
            <tr key={job.id}>
              <td>{job.type}</td>
              <td>{statusLabel(job.status)}</td>
              <td>
                {job.progress ? (
                  <span className="job-progress-line">
                    {progressLine(job.progress, averageRate(job))}
                  </span>
                ) : (
                  (latestByJob.get(job.id)?.message ?? "")
                )}
              </td>
              <td>
                {["queued", "running"].includes(job.status) && (
                  <button type="button" onClick={() => void cancel(job.id)}>
                    キャンセル
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
