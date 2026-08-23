// ホームの「やること」（§13）。**画面が一覧を持たない**：3 つの数から毎回導く。
// 別々の場所で増減するものを画面側に持つと、片方だけ消し忘れる。

export type TaskKind = "merge" | "merge_review" | "send" | "approve";
export type Task = { kind: TaskKind; count: number };
export type DashboardCounts = {
  merge_candidates: number;
  merge_review_total: number;
  unsent_total: number;
  awaiting_total: number;
};

// **つなぐ → 確かめる → 送る → 確認**。手を動かす順に並べる（つないでから送る）。
const ORDER = [
  { kind: "merge", of: (c: DashboardCounts) => c.merge_candidates },
  { kind: "merge_review", of: (c: DashboardCounts) => c.merge_review_total },
  { kind: "send", of: (c: DashboardCounts) => c.unsent_total },
  { kind: "approve", of: (c: DashboardCounts) => c.awaiting_total },
] as const;

export function tasksFrom(counts: DashboardCounts | null): Task[] {
  if (counts === null) {
    return [];
  }
  return ORDER.map((row) => ({ kind: row.kind, count: row.of(counts) })).filter(
    (task) => task.count > 0,
  );
}
