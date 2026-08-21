// 送信中（§13）。**この画面を閉じても送信は続く。** 閉じる手段を必ず置く。

import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ErrorBanner } from "../../components/ErrorBanner";
import { progressLine, statusLabel } from "../../components/JobProgress";
import type { Job } from "../../components/JobProgress";

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

  // **進捗はイベントではないので、走っている間だけ引きに行く。** SSE は
  // 「ファイルを 1 つ取り込んだ」のような節目しか流さない（16 GiB のコピーの
  // 途中では何も来ない）。`Jobs.tsx` と同じ作り。
  const running = jobs.some((job) => ["queued", "running", "cancelling"].includes(job.status));
  // 「いま」も取り直しと同じ拍で進める。**描画中に `Date.now()` は呼べない**
  // （呼ぶと、たまたま起きた再描画で値が変わる）。
  const [now, setNow] = useState(0);
  const reload = jobsQuery.reload;
  useEffect(() => {
    if (!running) {
      return;
    }
    const tick = () => {
      setNow(Date.now());
      reload();
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => clearInterval(timer);
  }, [running, reload]);

  // **速度は開始からの平均。** 2 点の差分は state か ref が要り、どちらも
  // 描画中には触れない。`Jobs.tsx` の `averageRate` と同じ考え方。
  function averageRate(job: Job): number | null {
    const done = job.progress?.bytes_done_all ?? job.progress?.bytes_done;
    if (!job.started_at || done === undefined || done <= 0) {
      return null;
    }
    const seconds = (now - Date.parse(job.started_at)) / 1000;
    return seconds > 1 ? done / seconds : null;
  }

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

      <p className="muted" style={{ textAlign: "center" }}>
        この画面を閉じても送信は続きます。
        <br />
        終わったら「やること」から結果を見られます。
      </p>
    </section>
  );
}
