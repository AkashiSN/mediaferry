// ジョブの進捗（§13）。**ファイル名と件数で示す。**

import type { JobEvent } from "../hooks/useEvents";

export type Job = {
  id: string;
  type: string;
  status: string;
  created_at: string;
};

export function JobProgress({ job, events }: { job: Job; events: JobEvent[] }) {
  const mine = events.filter((event) => event.job_id === job.id);
  const latest = mine.at(-1);
  return (
    <div className="job-progress">
      <span className="job-status">{statusLabel(job.status)}</span>
      {latest ? <span className="job-message">{latest.message}</span> : null}
    </div>
  );
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "待機中",
    running: "実行中",
    cancelling: "キャンセル中",
    cancelled: "キャンセル済み",
    succeeded: "完了",
    failed: "失敗",
  };
  return labels[status] ?? status;
}
