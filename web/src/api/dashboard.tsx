// ダッシュボードの集計を、**このタブで 1 度だけ引く**。
//
// 枠（ナビのやることバッジ）とホームがそれぞれ引くと、同じ応答を 2 本取りに行く。
// この集計は media 1 件ごとに宛先の記録を当たるので軽くない（`api/routes_system.py`）
// うえ、取り込み中は進捗のたびに取り直す —— 2 本になると、その負荷も 2 倍になる。
//
// **取り直しもここが持つ。** 購読する側がそれぞれ `useReloadOnEvents` を張ると、
// 1 つの合図で何度も取り直すことになる。

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";
import type { DashboardCounts } from "../hooks/useTasks";
import { useQuery } from "./hooks";
import type { Query } from "./hooks";

export type DestinationSummary = {
  destination_id: string;
  name: string;
  enabled: boolean;
  complete: number;
  failed: number;
  awaiting_approval: number;
  pending: number;
  unsent: number;
  stacked: number;
  stack_skipped: number;
};

export type Dashboard = DashboardCounts & {
  media_total: number;
  destinations: DestinationSummary[];
  running_jobs: number;
  recent_imports: { id: string; rel_path: string; captured_at: string }[];
  orphans: number;
  missing: number;
  warnings: { code: string; message: string }[];
};

const DashboardContext = createContext<Query<Dashboard> | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const dashboard = useQuery<Dashboard>("/dashboard");
  const { received } = useEvents();
  useReloadOnEvents(received, dashboard.reload);
  return <DashboardContext.Provider value={dashboard}>{children}</DashboardContext.Provider>;
}

/** **`DashboardProvider` の内側でだけ使える。** 外側で使うと 2 本目を引くことになる。 */
export function useDashboard(): Query<Dashboard> {
  const value = useContext(DashboardContext);
  if (value === null) {
    throw new Error("useDashboard は DashboardProvider の内側で使うこと");
  }
  return value;
}

/**
 * 集計を取り直す。**ジョブにならない操作の後に呼ぶ。**
 *
 * 却下・破棄・送り先の入り切りは進捗のイベントを出さないので、これを呼ばないと
 * ナビの「やること」の数が古いまま残る（画面を移っても枠は再マウントしない）。
 *
 * **枠の外では何もしない。** 画面だけを描くとき（テスト）に、枠の有無で画面の
 * 書き方を変えずに済ませる。
 */
export function useDashboardReload(): () => void {
  const value = useContext(DashboardContext);
  return value?.reload ?? noop;
}

function noop(): void {
  /* 枠の外では取り直す相手がいない */
}
