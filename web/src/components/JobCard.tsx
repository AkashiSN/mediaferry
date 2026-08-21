// 進行中の作業 1 件（§13）。ファイル名と件数で進捗を示す。

import { progressLine, statusLabel } from "./JobProgress";
import type { Job, JobProgressValue } from "./JobProgress";

const JOB_TYPE_LABELS: Record<string, string> = {
  import: "取り込み",
  scan: "スキャン",
  merge: "つなぐ",
  detect_groups: "候補の検出",
  upload: "送信",
  recompute_timestamps: "日時の再計算",
};

export function JobCard({
  job,
  rate,
  onCancel,
}: {
  job: Job;
  rate: number | null;
  onCancel?: (jobId: string) => void;
}) {
  const title = JOB_TYPE_LABELS[job.type] ?? job.type;
  return (
    <section className="card pad">
      <div className="row">
        <div className="grow">
          <div className="row" style={{ gap: 9 }}>
            <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>{title}</h3>
            <span className="small">{statusLabel(job.status)}</span>
          </div>
          {job.progress && (
            <p className="small" style={{ marginTop: 3 }}>
              {progressLine(job.progress, rate)}
            </p>
          )}
        </div>
        {/* **押した先の扱いを持たない場所に、押せるボタンを置かない。** `onCancel`
            を渡さない呼び出し元では中止ボタンごと出さない。 */}
        {onCancel && (
          <button type="button" className="btn sm" onClick={() => onCancel(job.id)}>
            中止する
          </button>
        )}
      </div>
      {job.progress && (
        <div className="bar" style={{ marginTop: 12 }}>
          <i style={{ width: `${barPercent(job.progress)}%` }} />
        </div>
      )}
    </section>
  );
}

function barPercent(progress: JobProgressValue): number {
  const total = progress.bytes_total_all ?? progress.bytes_total ?? 0;
  const done = progress.bytes_done_all ?? progress.bytes_done ?? 0;
  if (total <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, Math.floor((done / total) * 100)));
}
