// 作業の履歴（§13「詳しい情報」）。いま走っている作業はホームに出るので、ここは
// 一覧と、終わった作業に何が起きたかを見る場所。中止は走っている間だけできる。

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { JobCard } from "../../components/JobCard";
import type { Job } from "../../components/JobProgress";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";

type Jobs = { jobs: Job[] };

// キャンセルできる状態（`routes_system` の生存状態と揃える）。
const CANCELLABLE_STATUSES = ["queued", "running"];
// 進捗を取り直しにいく必要がある状態。
const LIVE_STATUSES = ["queued", "running", "cancelling"];

export function JobHistoryScreen() {
  const jobs = useQuery<Jobs>("/jobs");
  const { events, received, connected } = useEvents();
  const [error, setError] = useState<unknown>(null);
  useReloadOnEvents(received, jobs.reload);

  // **進捗はイベントではないので、走っている間だけ引きに行く。** SSE は
  // 「ファイルを 1 つ取り込んだ」のような節目しか流さない（16 GiB のコピーの
  // 途中では何も来ない）。
  const running = (jobs.data?.jobs ?? []).some((job) => LIVE_STATUSES.includes(job.status));
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
  // 描画中には触れない。長い処理では平均の方が値も落ち着く。
  function averageRate(job: Job): number | null {
    const done = job.progress?.bytes_done_all ?? job.progress?.bytes_done;
    if (!job.started_at || done === undefined || done <= 0) {
      return null;
    }
    const seconds = (now - Date.parse(job.started_at)) / 1000;
    return seconds > 1 ? done / seconds : null;
  }

  const rows = jobs.data?.jobs ?? [];

  return (
    <section aria-label="作業の履歴" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <h1 className="page lg">作業の履歴</h1>

      <ErrorBanner error={error ?? jobs.error} onDismiss={() => setError(null)} />

      {!connected && (
        <p role="status">進捗の接続が切れています。再接続を待っています…</p>
      )}

      {rows.length === 0 ? (
        <div className="card pad empty">
          <p className="muted">作業の記録はまだありません。</p>
        </div>
      ) : (
        rows.map((job) => (
          <div key={job.id}>
            <JobCard
              job={job}
              rate={averageRate(job)}
              onCancel={CANCELLABLE_STATUSES.includes(job.status) ? (id) => void cancel(id) : undefined}
            />
            {/* **進捗が無い（終わった）ジョブは、届いた最後のイベントの文言で補う。** */}
            {!job.progress && latestByJob.get(job.id) && (
              <p className="small" style={{ marginTop: -8, marginBottom: 8, paddingLeft: 4 }}>
                {latestByJob.get(job.id)?.message}
              </p>
            )}
          </div>
        ))
      )}
    </section>
  );
}
