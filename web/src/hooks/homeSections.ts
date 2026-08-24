// ホームの導出(§13)。**画面は一覧を持たない** —— カード・作業・集計から
// 毎回 3 つの並びを導く。
//
// **カードは「状態」ではなく「仕事」として扱う。** 取り込む残りがあるカードを
// やることに並べることで、「カードが挿さっているのに、やることはありません」
// という食い違いが、条件の直しではなく形の上で起こり得なくなる。

import { isLive } from "./useJobPulse";
import type { Job } from "../components/JobProgress";

export type CardView = {
  volume_instance_id: string;
  label: string;
  profile_name: string;
  size_bytes: number;
  profile_slug: string | null;
  trusted: boolean;
  provisional: boolean;
  reason: string;
  pending_count: number;
  scanned_at: string | null;
  busy: boolean;
};

export type DashboardCounts = {
  merge_candidates: number;
  merge_review_total: number;
  unsent_total: number;
  awaiting_total: number;
};

export type Doing = { job: Job; card: CardView | null };
export type Todo =
  | { kind: "import_card"; card: CardView }
  | { kind: "merge" | "merge_review" | "send" | "approve"; count: number };
export type StandingKind = "counting" | "not_target" | "no_contents" | "done";
export type Standing = { card: CardView; kind: StandingKind };
export type HomeSections = { doing: Doing[]; todo: Todo[]; standing: Standing[] };

// **つなぐ → 確かめる → 送る → 確認。** 手を動かす順(つないでから送る)。
// カードの取り込みはこれより前に来る。
export const COUNTED = [
  { kind: "merge", of: (c: DashboardCounts) => c.merge_candidates },
  { kind: "merge_review", of: (c: DashboardCounts) => c.merge_review_total },
  { kind: "send", of: (c: DashboardCounts) => c.unsent_total },
  { kind: "approve", of: (c: DashboardCounts) => c.awaiting_total },
] as const;

/** いま実際に動いているか(待機中は含まない)。並べる順を決めるのに使う。 */
function isRunning(job: Job): boolean {
  return job.status === "running" || job.status === "cancelling";
}

export function homeSections(input: {
  cards: CardView[];
  jobs: Job[];
  counts: DashboardCounts | null;
}): HomeSections {
  const byId = new Map(input.cards.map((card) => [card.volume_instance_id, card]));
  const live = input.jobs.filter(isLive);

  // **走っているものが先、待っているものは古い順。** 一覧の並びは API 側の
  // 都合で変わりうるので、順序をここで決める。
  const doing: Doing[] = [...live]
    .sort((a, b) =>
      isRunning(a) === isRunning(b)
        ? a.created_at.localeCompare(b.created_at)
        : isRunning(a)
          ? -1
          : 1,
    )
    .map((job) => ({
      job,
      card: job.volume_instance_id ? (byId.get(job.volume_instance_id) ?? null) : null,
    }));

  const held = new Set(
    live.map((job) => job.volume_instance_id).filter((id): id is string => Boolean(id)),
  );

  const todo: Todo[] = [];
  const standing: Standing[] = [];
  for (const card of input.cards) {
    // 動いているカードは、いま動いていることの側で見えている。
    if (held.has(card.volume_instance_id)) {
      continue;
    }
    if (card.profile_slug === null) {
      standing.push({ card, kind: "not_target" });
    } else if (card.scanned_at === null) {
      // **「取り込むものはありません」とは言わない。** 数える前の 0 件は
      // 「空」ではない。
      standing.push({ card, kind: "counting" });
    } else if (card.pending_count > 0) {
      todo.push({ kind: "import_card", card });
    } else if (card.provisional) {
      standing.push({ card, kind: "no_contents" });
    } else {
      standing.push({ card, kind: "done" });
    }
  }

  if (input.counts !== null) {
    const counts = input.counts;
    for (const row of COUNTED) {
      const count = row.of(counts);
      if (count > 0) {
        todo.push({ kind: row.kind, count });
      }
    }
  }

  return { doing, todo, standing };
}
