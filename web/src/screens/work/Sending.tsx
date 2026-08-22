// 送信中（§13）。**この画面を閉じても送信は続く。** 閉じる手段を必ず置く。

import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ErrorBanner } from "../../components/ErrorBanner";
import { progressLine, statusLabel } from "../../components/JobProgress";
import type { Job } from "../../components/JobProgress";
import { isLive, useJobPulse } from "../../hooks/useJobPulse";

type Jobs = { jobs: Job[] };

export function SendingScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const passed = location.state as { jobIds?: string[]; note?: string | null } | null;
  const jobIds = passed?.jobIds;
  const note = passed?.note ?? null;

  const jobsQuery = useQuery<Jobs>("/jobs");
  const [error, setError] = useState<unknown>(null);

  // **`Send.tsx` から渡された `jobIds` があれば、それだけを追う。** 渡されずに
  // この画面を直接開いた場合（閉じたあとにブラウザの戻る、など）は、進行中の
  // 送信ジョブをすべて出す。
  const jobs = useMemo(() => {
    const all = jobsQuery.data?.jobs ?? [];
    if (jobIds && jobIds.length > 0) {
      return all.filter((job) => jobIds.includes(job.id));
    }
    return all.filter((job) => job.type === "upload");
  }, [jobsQuery.data, jobIds]);

  const running = jobs.some(isLive);
  const averageRate = useJobPulse(running, jobsQuery.reload);

  async function cancel(jobId: string) {
    try {
      await request(`/jobs/${jobId}/cancel`, { method: "POST" });
      jobsQuery.reload();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <section aria-label="送信中" className="wrap">
      <div className="row">
        <h1 className="page">送っています</h1>
        <button
          type="button"
          className="btn sm"
          style={{ marginLeft: "auto" }}
          onClick={() => navigate("/")}
        >
          閉じる
        </button>
      </div>

      <ErrorBanner error={error ?? jobsQuery.error} onDismiss={() => setError(null)} />
      {/* **断られた組と、開始に失敗した宛先を隠さない**（`Send.tsx` の `summarise`）。 */}
      {note && <p role="status">{note}</p>}

      {jobs.length === 0 ? (
        <div className="card pad empty">
          <p className="muted">いま送っているものはありません。</p>
        </div>
      ) : (
        jobs.map((job) => (
          <section key={job.id} className="card pad">
            <div className="row">
              <b>{statusLabel(job.status)}</b>
              {["queued", "running"].includes(job.status) && (
                <button
                  type="button"
                  className="btn danger sm"
                  style={{ marginLeft: "auto" }}
                  onClick={() => void cancel(job.id)}
                >
                  送るのをやめる
                </button>
              )}
            </div>
            {job.progress && (
              <p className="small job-progress-line">{progressLine(job.progress, averageRate(job))}</p>
            )}
          </section>
        ))
      )}

      {/* **結果があるのは作業の履歴。** ホームの「やること」は残っている仕事しか
          出さないので、送り終えると空になる（§13）。

          **行き先はボタンで置く。** 文の途中のリンクは行の高さ（17px）しか無く、
          §13「押せる領域は 44px 以上」を満たせない。 */}
      <p className="muted" style={{ textAlign: "center" }}>
        この画面を閉じても送信は続きます。
        <br />
        終わったら 設定 › 詳しい情報 › 作業の履歴 で結果を見られます。
      </p>
      <div className="acts" style={{ justifyContent: "center" }}>
        <Link to="/settings/jobs" className="btn sm">
          作業の履歴を見る
        </Link>
      </div>
    </section>
  );
}
