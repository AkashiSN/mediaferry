// 画面の入口。認証が要るかを最初に確かめ、要るならログインを出す。

import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { request } from "./api/client";
import { DashboardProvider, useDashboard } from "./api/dashboard";
import { useQuery } from "./api/hooks";
import { Layout } from "./components/Layout";
import type { Warning } from "./components/Layout";
import { useEvents } from "./hooks/useEvents";
import { useReloadOnEvents } from "./hooks/useReloadOnEvents";
import { COUNTED } from "./hooks/homeSections";
import { CardDetailScreen } from "./screens/work/CardDetail";
import { LoginScreen } from "./screens/Login";
import { HomeScreen } from "./screens/Home";
import { MergeScreen } from "./screens/work/Merge";
import { ApproveScreen } from "./screens/work/Approve";
import { SendScreen } from "./screens/work/Send";
import { SendingScreen } from "./screens/work/Sending";
import { PhotosScreen } from "./screens/Photos";
import { SettingsScreen } from "./screens/Settings";
import { DestinationsScreen } from "./screens/settings/Destinations";
import { ProfilesScreen } from "./screens/settings/Profiles";
import { GeneralScreen } from "./screens/settings/General";
import { JobHistoryScreen } from "./screens/details/JobHistory";
import { MergeHistoryScreen } from "./screens/details/MergeHistory";

type Session = { required: boolean; authenticated: boolean };
type Settings = { warnings: Warning[] };

/**
 * 認証を済ませた後だけ描画する部分。**`taskCount` も `/settings` の警告も
 * ここで 1 回だけ引く**（画面ごとに数えさせない方針）。`App` 本体に置くと、
 * ログイン確認が終わる前の描画でも `useEffect` が走ってしまうので、別の
 * コンポーネントに切り出してマウント自体をログイン後まで遅らせる。
 *
 * **枠も進捗で取り直す。** ここは `BrowserRouter` の外側なので画面を移っても
 * 再マウントせず、取り直さないとナビのバッジも警告バナーもセッション中ずっと
 * 開いた時のままになる（§13「画面を再読み込みせずに進む」）。
 */
function AuthedApp() {
  return (
    <DashboardProvider>
      <Framed />
    </DashboardProvider>
  );
}

/** 枠と画面。**ダッシュボードの集計は枠とホームで同じ 1 本を見る**（`api/dashboard.tsx`）。 */
function Framed() {
  const settings = useQuery<Settings>("/settings");
  const dashboard = useDashboard();
  // **バッジは集計だけから数える。** ホームの導出（`homeSections`）は `/devices`
  // と `/jobs` も要るので、枠でも同じ導出をすると画面ごとに 3 本ずつ飛ぶ。
  const counts = dashboard.data;
  const taskCount = counts === null ? 0 : COUNTED.filter((row) => row.of(counts) > 0).length;
  const { received } = useEvents();
  useReloadOnEvents(received, settings.reload);

  return (
    <BrowserRouter>
      <Layout warnings={settings.data?.warnings ?? []} taskCount={taskCount}>
        <Routes>
          <Route path="/" element={<HomeScreen />} />
          <Route path="/card" element={<CardDetailScreen />} />
          <Route path="/merge" element={<MergeScreen />} />
          <Route path="/approve" element={<ApproveScreen />} />
          <Route path="/send" element={<SendScreen />} />
          <Route path="/sending" element={<SendingScreen />} />
          <Route path="/photos" element={<PhotosScreen />} />
          <Route path="/settings" element={<SettingsScreen />} />
          <Route path="/settings/destinations" element={<DestinationsScreen />} />
          <Route path="/settings/profiles" element={<ProfilesScreen />} />
          <Route path="/settings/general" element={<GeneralScreen />} />
          <Route path="/settings/jobs" element={<JobHistoryScreen />} />
          <Route path="/settings/merge-history" element={<MergeHistoryScreen />} />
          {/* **知らないパスで本文を空にしない**（§13）。ルート表に無いパスは
              `Layout` だけが描かれ、何が起きたのかも次に何をすべきかも出ない。 */}
          <Route path="*" element={<NotFoundScreen />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

/** ルート表に無いパス（§13）。**内部のパスをそのまま出さない**ので、開こうとした
 * URL は載せずに、戻る道だけを置く。 */
function NotFoundScreen() {
  return (
    <section aria-label="その画面はありません" className="wrap">
      <h1 className="page title-lg">その画面はありません</h1>
      <p className="muted">
        住所が変わったか、書き間違いかもしれません。ホームから開き直してください。
      </p>
      <div className="acts">
        <Link to="/" className="btn primary">
          ホームへ戻る
        </Link>
      </div>
    </section>
  );
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);

  const load = useCallback(() => {
    request<Session>("/auth/session")
      .then(setSession)
      .catch(() => setSession({ required: true, authenticated: false }));
  }, []);

  useEffect(load, [load]);

  if (session === null) {
    return <p>読み込み中…</p>;
  }
  if (session.required && !session.authenticated) {
    return <LoginScreen onSignedIn={load} />;
  }
  return <AuthedApp />;
}
