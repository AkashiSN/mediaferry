// ジョブ（§13）。一覧と、進捗・ログ・キャンセル。

import { useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";
import { statusLabel } from "../components/JobProgress";
import type { Job } from "../components/JobProgress";
import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";

type Jobs = { jobs: Job[] };

export function JobsScreen() {
  const jobs = useQuery<Jobs>("/jobs");
  const { events, received, connected } = useEvents();
  const [error, setError] = useState<unknown>(null);
  useReloadOnEvents(received, jobs.reload);

  // **画面を再読み込みせずに進む。** 届いたイベントのジョブだけ取り直す。
  const latestByJob = new Map(events.map((event) => [event.job_id, event]));

  async function cancel(jobId: string) {
    try {
      await request(`/jobs/${jobId}/cancel`, { method: "POST" });
      jobs.reload();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <section aria-label="ジョブ">
      <h1>ジョブ</h1>
      <ErrorBanner error={error ?? jobs.error} onDismiss={() => setError(null)} />
      {!connected && <p role="status">進捗の接続が切れています。再接続を待っています…</p>}
      <table>
        <thead>
          <tr>
            <th>種類</th>
            <th>状態</th>
            <th>進捗</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(jobs.data?.jobs ?? []).map((job) => (
            <tr key={job.id}>
              <td>{job.type}</td>
              <td>{statusLabel(job.status)}</td>
              <td>{latestByJob.get(job.id)?.message ?? ""}</td>
              <td>
                {["queued", "running"].includes(job.status) && (
                  <button type="button" onClick={() => void cancel(job.id)}>
                    キャンセル
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
