// 作業の履歴（§13「詳しい情報」）。いま走っている作業はホームに出るので、ここは
// 一覧と、終わった作業がいつ・どうなったかを見る場所。

import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useMutation, useQuery } from "../../api/hooks";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { JobCard } from "../../components/JobCard";
import type { Job } from "../../components/JobProgress";
import { useEvents } from "../../hooks/useEvents";
import { isCancellable, isLive, useJobPulse } from "../../hooks/useJobPulse";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatSystemDateTime } from "../../utils/formatDateTime";

type Jobs = { jobs: Job[] };

export function JobHistoryScreen() {
  const jobs = useQuery<Jobs>("/jobs");
  const { events, received, connected } = useEvents();
  const cancelling = useMutation();
  useReloadOnEvents(received, jobs.reload);

  const running = (jobs.data?.jobs ?? []).some(isLive);
  const averageRate = useJobPulse(running, jobs.reload);

  // **ジョブごとの最新イベントの索引。** 進捗が無い（終わった）ジョブに、届いた最後の
  // イベントの文言を添えるのに使う。一覧そのものの取り直しは、上の `useReloadOnEvents`
  // が「届いた」ことだけを合図にまとめて 1 回行う（イベントの中身はそちらでは読まない）。
  const latestByJob = new Map(events.map((event) => [event.job_id, event]));

  async function cancel(jobId: string) {
    if (await cancelling.run(() => request(`/jobs/${jobId}/cancel`, { method: "POST" }))) {
      jobs.reload();
    }
  }

  const rows = jobs.data?.jobs ?? [];

  /**
   * カードに添える補足の行を組み立てる。
   *
   * **終わった日時を出すのは、設定の入口が「取り込みや送信がいつ終わったか」を
   * 約束しているから**（`Settings.tsx`）。システム時刻は UTC で保存されるので、
   * 現地時刻に見えないよう印を添える（`formatSystemDateTime`）。
   */
  function notesFor(job: Job): string[] {
    const notes: string[] = [];
    if (job.finished_at) {
      notes.push(`終わった日時: ${formatSystemDateTime(job.finished_at)}`);
    }
    // 進捗が無い（終わった）ジョブには、届いた最後のイベントの文言を添える。
    // **開いている間に届いた知らせを優先し、無ければサーバが添えた 1 文を使う。**
    // 進捗の知らせは開いた後のぶんしか来ないので、これが無いと**画面を開く前に
    // 終わった作業は「完了」としか出せない**（Phase 11 の N4）。
    const latest = job.progress ? undefined : latestByJob.get(job.id);
    const summary = latest?.message ?? (job.progress ? undefined : job.last_message);
    if (summary) {
      notes.push(summary);
    }
    return notes;
  }

  return (
    <section aria-label="作業の履歴" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <h1 className="page title-lg">作業の履歴</h1>

      <ErrorBanner error={cancelling.error ?? jobs.error} onDismiss={cancelling.clear} />

      {connected === false && (
        <p role="status">進捗の接続が切れています。再接続を待っています…</p>
      )}

      {rows.length === 0 ? (
        <div className="card pad empty">
          <p className="muted">作業の記録はまだありません。</p>
        </div>
      ) : (
        rows.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            rate={averageRate(job)}
            onCancel={isCancellable(job) ? (id) => void cancel(id) : undefined}
            cancelBusy={cancelling.busy}
            notes={notesFor(job)}
          />
        ))
      )}
    </section>
  );
}
