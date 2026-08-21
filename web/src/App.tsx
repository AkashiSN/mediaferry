// 画面の入口。認証が要るかを最初に確かめ、要るならログインを出す。

import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { request } from "./api/client";
import { useQuery } from "./api/hooks";
import { Layout } from "./components/Layout";
import type { Warning } from "./components/Layout";
import { tasksFrom } from "./hooks/useTasks";
import type { DashboardCounts } from "./hooks/useTasks";
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
 * 認証を済ませた後だけ描画する部分。**`taskCount` はここで 1 回だけ引く**
 * （画面ごとに数えさせない方針）。`App` 本体に置くと、ログイン確認が終わる前の
 * 描画でも `/dashboard` の `useEffect` が走ってしまうので、別のコンポーネントに
 * 切り出してマウント自体をログイン後まで遅らせる。
 */
function AuthedApp({ warnings }: { warnings: Warning[] }) {
  const dashboard = useQuery<DashboardCounts>("/dashboard");
  const taskCount = tasksFrom(dashboard.data).length;

  return (
    <BrowserRouter>
      <Layout warnings={warnings} taskCount={taskCount}>
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
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const settings = useQuery<Settings>("/settings", [session?.authenticated]);

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
  return <AuthedApp warnings={settings.data?.warnings ?? []} />;
}
