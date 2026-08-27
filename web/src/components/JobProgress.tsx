// 進行中の作業の進捗（§13）。**ファイル名と件数で示す。**
//
// ここにあるのは、進捗の型と、それを 1 行の日本語に写す関数だけ。描くのは
// `JobCard.tsx`。

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
  // どのカードの作業か。カードに紐づかない作業（送信など）では null。
  volume_instance_id?: string | null;
  progress?: JobProgressValue | null;
  /** 最後に出した 1 文。**終わった作業の要約**で、一覧（`GET /jobs`）だけが持つ。
   * 進捗の知らせは画面を開いた後のぶんしか来ないので、これが無いと開く前に
   * 終わった作業は「完了」としか出せない（Phase 11 の N4）。 */
  last_message?: string | null;
};

// サーバが返す phase を §13 の言葉に写す（`merge` → **つなぐ**）。**内部の名前を
// そのまま出さない**ので、写せないものだけ生の値のまま出す。
const PHASES: Record<string, string> = {
  copy: "コピー中",
  merge: "つないでいます",
  upload: "送信中",
};

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
      parts.push(`残り約 ${formatRemaining((total - done) / bytesPerSecond)}`);
    }
  }
  return parts.join(" · ");
}

/** 残り時間をおおまかな日本語にする。**動画の長さ（`分:秒`）とは別物**
 * （`components/MediaTile.tsx` の `formatClipLength`）。 */
function formatRemaining(seconds: number): string {
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
