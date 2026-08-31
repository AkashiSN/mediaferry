// 進行中の作業 1 件（§13）。ファイル名と件数で進捗を示す。

import type { ReactNode } from "react";

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

// **1 つの種別が複数の仕事を兼ねているとき、押した操作で呼び分ける。** `upload` は
// 送る・再確認する・日時の承認の 3 つを `params.mode` で分けているので、種別だけで
// 決めると 3 つとも「送信」になり、押したことが履歴に伝わらない。
const JOB_MODE_LABELS: Record<string, Record<string, string>> = {
  upload: {
    recheck: "再確認",
    approve: "日時の承認",
  },
};

/** 作業の札。**知らない値で札を空にしない** —— `mode` を写せなければ種別の札へ、
 * 種別も写せなければ生の値へ落とす。履歴は過去の行を出す画面なので、こちらが
 * 知らない形の行が混ざる。 */
function jobLabel(job: Job): string {
  const byMode = typeof job.mode === "string" ? JOB_MODE_LABELS[job.type]?.[job.mode] : undefined;
  return byMode ?? JOB_TYPE_LABELS[job.type] ?? job.type;
}

export function JobCard({
  job,
  subject,
  rate,
  onCancel,
  cancelLabel = "中止する",
  cancelBusy = false,
  notes,
  footer,
}: {
  job: Job;
  /** その作業が扱っている当のもの（カードのラベルなど）。**題に添える。**
   * 何に対する作業かが出ていないと、同じ種類の作業が並んだとき見分けが付かない。 */
  subject?: string | null;
  rate: number | null;
  onCancel?: (jobId: string) => void;
  /** 止めるボタンの文言。呼び出し元によって呼び方が違う（ホームは送信ジョブに
   * 「送るのをやめる」を渡す）。 */
  cancelLabel?: string;
  /** 止める要求が飛んでいる間は押せなくする。**2 度目は 409 で弾かれるだけ**なので、
   * 押した人にはバナーしか残らない。 */
  cancelBusy?: boolean;
  /**
   * この作業に添える補足の行（終わった日時、届いた最後の文言など）。**空の行は
   * 描かない。**
   */
  notes?: string[];
  /**
   * この作業の箱の中に添える要素。**文字列ではなく要素で受ける** —— 状態として
   * 読み上げさせたい行（`role="status"`）は、文言だけを渡すと意味づけが落ちる。
   *
   * `notes` と同じく**箱の内側に描く**。呼び出し元がカードの外に兄弟として
   * 並べると、カードの内側の余白ではなく外枠に揃い、縁からはみ出して見える。
   */
  footer?: ReactNode;
}) {
  const title = jobLabel(job);
  return (
    <section className="card pad">
      <div className="row">
        <div className="grow">
          <div className="row" style={{ gap: 9 }}>
            <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>{title}</h3>
            {subject && <span className="small">{subject}</span>}
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
          <button
            type="button"
            className="btn sm"
            disabled={cancelBusy}
            onClick={() => onCancel(job.id)}
          >
            {cancelLabel}
          </button>
        )}
      </div>
      {job.progress && (
        <div className="bar" style={{ marginTop: 12 }}>
          <i style={{ width: `${barPercent(job.progress)}%` }} />
        </div>
      )}
      {/* **補足はカードの中で描く。** 添える文言は呼び出し元ごとに違う（作業の履歴は
          終わった日時と最後の文言を添え、ホームは何も添えない）が、箱を
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
      {footer}
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
