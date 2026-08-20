// ダッシュボード（§13）。接続中デバイス・実行中ジョブ・宛先ごとの同期状況・
// 最近の取り込み・注意が要るもの。

import { Link } from "react-router-dom";

import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";
import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";

type DestinationSummary = {
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

type Dashboard = {
  media_total: number;
  destinations: DestinationSummary[];
  running_jobs: number;
  recent_imports: { id: string; rel_path: string; captured_at: string }[];
  orphans: number;
  missing: number;
  warnings: { code: string; message: string }[];
};

type Devices = { volumes: { volume_instance_id: string; profile_slug: string | null }[] };

export function DashboardScreen() {
  const dashboard = useQuery<Dashboard>("/dashboard");
  const devices = useQuery<Devices>("/devices");
  const { received } = useEvents();
  useReloadOnEvents(received, dashboard.reload);

  if (dashboard.error) {
    return <ErrorBanner error={dashboard.error} onDismiss={dashboard.reload} />;
  }
  if (dashboard.data === null) {
    return <p>読み込み中…</p>;
  }
  const summary = dashboard.data;
  return (
    <section aria-label="ダッシュボード">
      <h1>ダッシュボード</h1>
      <dl className="tiles">
        <div>
          <dt>接続中のデバイス</dt>
          <dd>{devices.data?.volumes.length ?? 0}</dd>
        </div>
        <div>
          <dt>実行中のジョブ</dt>
          <dd>
            <Link to="/jobs">{summary.running_jobs}</Link>
          </dd>
        </div>
        <div>
          <dt>ライブラリ</dt>
          <dd>
            <Link to="/library">{summary.media_total} 件</Link>
          </dd>
        </div>
      </dl>

      <h2>宛先ごとの状況</h2>
      {summary.destinations.length === 0 ? (
        <p>
          転送先がまだありません。<Link to="/destinations">転送先を追加</Link>すると、ここに
          同期の状況が出ます。
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>宛先</th>
              <th>送信済み</th>
              <th>未送信</th>
              <th>失敗</th>
              <th>承認待ち</th>
              <th>スタック</th>
            </tr>
          </thead>
          <tbody>
            {summary.destinations.map((destination) => (
              <tr key={destination.destination_id}>
                <td>{destination.name}</td>
                <td>{destination.complete}</td>
                <td>
                  <Link to={`/library?destination_id=${destination.destination_id}&status=unsent`}>
                    {destination.unsent}
                  </Link>
                </td>
                <td>{destination.failed}</td>
                <td>
                  <Link to="/approvals">{destination.awaiting_approval}</Link>
                </td>
                <td>
                  {destination.stacked} 組
                  {destination.stack_skipped > 0 && (
                    <> / 見送り {destination.stack_skipped} 件</>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(summary.orphans > 0 || summary.missing > 0) && (
        <p role="status">
          注意が要るもの: 孤立ファイル {summary.orphans} 件 / 見つからないファイル{" "}
          {summary.missing} 件
        </p>
      )}

      <h2>最近の取り込み</h2>
      <ul>
        {summary.recent_imports.map((media) => (
          <li key={media.id}>
            {media.rel_path}（{media.captured_at}）
          </li>
        ))}
      </ul>
    </section>
  );
}
