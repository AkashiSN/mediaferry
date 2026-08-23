// 送信中（§13）。**この画面を閉じても送信は続く。** 閉じる手段を必ず置く。

import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ErrorBanner } from "../../components/ErrorBanner";
import { JobCard } from "../../components/JobCard";
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
  // この画面を直接開いた場合（閉じたあとにブラウザの戻る、再読み込み、1 件も
  // 始まらなかった送信）は、進行中の送信ジョブをすべて出す。
  //
  // **終わったものは出さない。** `GET /jobs` は状態を問わず直近 50 件を返すので、
  // type だけで絞ると過去の送信が「送っています」の下に並ぶ。
  const jobs = useMemo(() => {
    const all = jobsQuery.data?.jobs ?? [];
    if (jobIds && jobIds.length > 0) {
      return all.filter((job) => jobIds.includes(job.id));
    }
    return all.filter((job) => job.type === "upload" && isLive(job));
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
        // **進行中の作業の見せ方は `JobCard` が持つ**（題・状態・進捗の行・帯）。
        // ここで描き直すと、進捗の帯だけこの画面から抜け落ちる。
        jobs.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            rate={averageRate(job)}
            cancelLabel="送るのをやめる"
            onCancel={
              ["queued", "running"].includes(job.status) ? (id) => void cancel(id) : undefined
            }
          />
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
