// 画面の入口。認証が要るかを最初に確かめ、要るならログインを出す。

import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { request } from "./api/client";
import { useQuery } from "./api/hooks";
import { Layout } from "./components/Layout";
import { ApprovalsScreen } from "./screens/Approvals";
import { DashboardScreen } from "./screens/Dashboard";
import { DestinationsScreen } from "./screens/Destinations";
import { DevicesScreen } from "./screens/Devices";
import { JobsScreen } from "./screens/Jobs";
import { LibraryScreen } from "./screens/Library";
import { LoginScreen } from "./screens/Login";
import { MergesScreen } from "./screens/Merges";
import { SettingsScreen } from "./screens/Settings";

type Session = { required: boolean; authenticated: boolean };
type Settings = { warnings: { code: string; message: string }[] };

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
  return (
    <BrowserRouter>
      {/* やることの件数は後続のタスクで配線する（画面ごとに数えさせない方針）。 */}
      <Layout warnings={settings.data?.warnings ?? []} taskCount={0}>
        <Routes>
          <Route path="/" element={<DashboardScreen />} />
          <Route path="/devices" element={<DevicesScreen />} />
          <Route path="/library" element={<LibraryScreen />} />
          <Route path="/merges" element={<MergesScreen />} />
          <Route path="/destinations" element={<DestinationsScreen />} />
          <Route path="/approvals" element={<ApprovalsScreen />} />
          <Route path="/jobs" element={<JobsScreen />} />
          <Route path="/settings" element={<SettingsScreen />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
