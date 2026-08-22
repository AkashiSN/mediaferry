// 進行中の作業の進捗（§13）。**ファイル名と件数で示す。**
//
// ここにあるのは、進捗の型と、それを 1 行の日本語に写す関数だけ。描くのは
// `JobCard.tsx` と `work/Sending.tsx`。

import { formatBytes } from "../utils/formatBytes";

/** 走っている間だけ入る（終わると API が落とす）。 */
export type JobProgressValue = {
  phase: string;
  rel_path?: string;
  route?: string;
  parts?: number;
  file_index?: number;
  file_count?: number;
  bytes_done?: number;
  bytes_total?: number;
  bytes_done_all?: number;
  bytes_total_all?: number;
};

export type Job = {
  id: string;
  type: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  // **走っている間は無い**（終わった時点で API が入れる）。
  finished_at?: string | null;
  progress?: JobProgressValue | null;
};

// サーバが返す phase を §13 の言葉に写す（`merge` → **つなぐ**）。**内部の名前を
// そのまま出さない**ので、写せないものだけ生の値のまま出す。
const PHASES: Record<string, string> = { copy: "コピー中", merge: "つないでいます" };

/** **速度と残りは画面で出す。** サーバに持たせると心拍の間隔に依存した値を残すことになる。 */
export function progressLine(progress: JobProgressValue, bytesPerSecond: number | null): string {
  const parts: string[] = [PHASES[progress.phase] ?? progress.phase];
  if (progress.file_index && progress.file_count) {
    parts.push(`${progress.file_index}/${progress.file_count} 件`);
  }
  if (progress.parts) {
    parts.push(`${progress.parts} パート${progress.route ? `・経路 ${progress.route}` : ""}`);
  }
  const name = progress.rel_path?.split("/").pop();
  if (name) {
    parts.push(name);
  }
  const done = progress.bytes_done ?? 0;
  const total = progress.bytes_total ?? 0;
  if (total > 0) {
    parts.push(`${formatBytes(done)} / ${formatBytes(total)}（${Math.floor((done / total) * 100)}%）`);
  }
  if (bytesPerSecond && bytesPerSecond > 0) {
    parts.push(`${formatBytes(bytesPerSecond)}/秒`);
    if (total > done) {
      parts.push(`残り約 ${formatDuration((total - done) / bytesPerSecond)}`);
    }
  }
  return parts.join(" · ");
}

function formatDuration(seconds: number): string {
  if (seconds < 90) {
    return `${Math.max(1, Math.round(seconds))} 秒`;
  }
  if (seconds < 5400) {
    return `${Math.round(seconds / 60)} 分`;
  }
  return `${(seconds / 3600).toFixed(1)} 時間`;
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
