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
  notes,
}: {
  job: Job;
  rate: number | null;
  onCancel?: (jobId: string) => void;
  /**
   * この作業に添える補足の行（終わった日時、届いた最後の文言など）。**空の行は
   * 描かない。**
   */
  notes?: string[];
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
      {/* **補足はカードの中で描く。** 添える文言は呼び出し元ごとに違う（作業の履歴は
          終わった日時と最後の文言を添え、ホームと送信中は何も添えない）が、箱を
          描いているのはこのカードなので、行の置き場所もここが持つ。呼び出し元が
          カードの外に兄弟として並べると、カードの内側の余白ではなく外枠に揃い、
          縁からはみ出して見える。 */}
      {(notes ?? [])
        .filter((note) => note !== "")
        .map((note, index) => (
          <p key={index} className="small card-note">
            {note}
          </p>
        ))}
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
