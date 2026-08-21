// 作業の履歴（§13「詳しい情報」）。いま走っている作業はホームに出るので、ここは
// 一覧と、終わった作業がいつ・どうなったかを見る場所。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { JobCard } from "../../components/JobCard";
import type { Job } from "../../components/JobProgress";
import { useEvents } from "../../hooks/useEvents";
import { isLive, useJobPulse } from "../../hooks/useJobPulse";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatSystemDateTime } from "../../utils/formatDateTime";

type Jobs = { jobs: Job[] };

// 中止できる状態。待機中はすぐに取り消され、実行中はキャンセル中を経て取り消される
// （`db/jobs.py` の `request_cancel`）。
const CANCELLABLE_STATUSES = ["queued", "running"];

export function JobHistoryScreen() {
  const jobs = useQuery<Jobs>("/jobs");
  const { events, received, connected } = useEvents();
  const [error, setError] = useState<unknown>(null);
  useReloadOnEvents(received, jobs.reload);

  const running = (jobs.data?.jobs ?? []).some(isLive);
  const averageRate = useJobPulse(running, jobs.reload);

  // **ジョブごとの最新イベントの索引。** 進捗が無い（終わった）ジョブに、届いた最後の
  // イベントの文言を添えるのに使う。一覧そのものの取り直しは、上の `useReloadOnEvents`
  // が「届いた」ことだけを合図にまとめて 1 回行う（イベントの中身はそちらでは読まない）。
  const latestByJob = new Map(events.map((event) => [event.job_id, event]));

  async function cancel(jobId: string) {
    setError(null);
    try {
      await request(`/jobs/${jobId}/cancel`, { method: "POST" });
      jobs.reload();
    } catch (caught) {
      setError(caught);
    }
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

      {connected === false && (
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
            {/* Settings.tsx の入口が「いつ終わったか」を約束しているので、終わった
                ジョブには終了日時を添える。システム時刻は UTC なので印を付ける。 */}
            {job.finished_at && (
              <p className="small" style={{ marginTop: -8, marginBottom: 8, paddingLeft: 4 }}>
                終わった日時: {formatSystemDateTime(job.finished_at)}
              </p>
            )}
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
