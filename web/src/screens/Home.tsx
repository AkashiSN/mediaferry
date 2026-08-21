// ホーム（§13）。いま挿さっているカードの帯、やること、進行中の作業、
// 送り先ごとの状況、最近の取り込みを上から順に置く。
//
// 旧ダッシュボードは宛先ごとの 6 列の数字を出すだけで、家族が読んで次の一手を
// 決められる形ではなかった。ここでは「次に何をすればいいか」を先に出す。

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog, type Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";
import { JobCard } from "../components/JobCard";
import type { Job } from "../components/JobProgress";
import { autoImportOutlook, autoImportState, profileDisplayName, volumeLabel } from "./work/CardDetail";
import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";
import { tasksFrom } from "../hooks/useTasks";
import type { Task } from "../hooks/useTasks";

type Volume = {
  volume_instance_id: string;
  // API は空文字を返す（`None` にはならない）。ラベルの有無は `""` で見る。
  fs_label: string;
  profile_slug: string | null;
  identity_confidence: string | null;
  provisional: boolean;
  trusted: boolean;
  reason: string | null;
};

type Devices = { volumes: Volume[] };
type Profile = { slug: string; name: string };
type Profiles = { profiles: Profile[] };

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
  merge_candidates: number;
  unsent_total: number;
  awaiting_total: number;
};

type Jobs = { jobs: Job[] };
type Setting = { key: string; value: string | null };
type Settings = { settings: Setting[] };

// 進捗を持ちうる状態（`routes_system._LIVE_STATUSES` と揃える）。
const LIVE_STATUSES = ["queued", "running", "cancelling"];

export function HomeScreen() {
  const dashboard = useQuery<Dashboard>("/dashboard");
  const devices = useQuery<Devices>("/devices");
  const jobs = useQuery<Jobs>("/jobs");
  const settings = useQuery<Settings>("/settings");
  const profiles = useQuery<Profiles>("/profiles");
  const { received, connected } = useEvents();
  useReloadOnEvents(received, dashboard.reload);
  useReloadOnEvents(received, devices.reload);
  useReloadOnEvents(received, jobs.reload);
  useReloadOnEvents(received, settings.reload);
  useReloadOnEvents(received, profiles.reload);

  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ confirmation: Confirmation; id: string } | null>(
    null,
  );

  // **未解決・失敗は `null` のまま持つ。** 既定値へ倒すと、同意の内容が実挙動と
  // ずれる（`watcher.py` は積まないのに「コピーされます」と書く）。
  const autoImport =
    (settings.data?.settings ?? []).find((setting) => setting.key === "AUTO_IMPORT")?.value ??
    null;

  async function act(volumeId: string, action: "trust" | "scan" | "import" | "close") {
    setBusy(`${volumeId}:${action}`);
    setError(null);
    try {
      await request(`/volumes/${volumeId}/${action}`, { method: "POST" });
      devices.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setConfirming(null);
      setBusy(null);
    }
  }

  async function cancelJob(jobId: string) {
    try {
      await request(`/jobs/${jobId}/cancel`, { method: "POST" });
      jobs.reload();
    } catch (caught) {
      setError(caught);
    }
  }

  // **やることは画面が持つ一覧ではなく、状態から毎回導く。** `dashboardData` が
  // 無い間は「読み込み中…」を出し、「やることはありません」とは書かない
  // （直後に件数が現れて驚かせないため）。`const` に取ってから narrowing する
  // ——`dashboard.data` を式のあちこちで直に見ると、`null` チェックの後でも
  // 型が絞られず `as` に頼ることになる。
  const dashboardData = dashboard.data;
  const tasks: Task[] = dashboardData === null ? [] : tasksFrom(dashboardData);

  const running = (jobs.data?.jobs ?? []).find((job) => LIVE_STATUSES.includes(job.status));

  // 「いま」も取り直しと同じ拍で進める。**描画中に `Date.now()` は呼べない**
  // （呼ぶと、たまたま起きた再描画で値が変わる）。`Jobs.tsx` の作り方をそのまま
  // 持ってくる。
  const [now, setNow] = useState(0);
  const reloadJobs = jobs.reload;
  const isRunning = running !== undefined;
  useEffect(() => {
    if (!isRunning) {
      return;
    }
    const tick = () => {
      setNow(Date.now());
      reloadJobs();
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => clearInterval(timer);
  }, [isRunning, reloadJobs]);

  // **速度は開始からの平均。** 2 点の差分は state か ref が要り、どちらも
  // 描画中には触れない。長い処理では平均の方が値も落ち着く。
  function averageRate(job: Job): number | null {
    const done = job.progress?.bytes_done_all ?? job.progress?.bytes_done;
    if (!job.started_at || done === undefined || done <= 0) {
      return null;
    }
    const seconds = (now - Date.parse(job.started_at)) / 1000;
    return seconds > 1 ? done / seconds : null;
  }

  return (
    <section aria-label="ホーム">
      <div className="wrap">
        <div className="row">
          <h1 className="page">ホーム</h1>
          <span
            className="chip"
            style={{ marginLeft: "auto", height: 32, fontSize: "12.5px", pointerEvents: "none" }}
          >
            <Icon name="lock" size={16} /> 自動で取り込む：
            <b>{autoImport === null ? "…" : autoImport === "trusted" ? "オン" : "オフ"}</b>
          </span>
        </div>

        <ErrorBanner
          error={error ?? dashboard.error ?? devices.error ?? jobs.error ?? settings.error ?? profiles.error}
          onDismiss={() => setError(null)}
        />

        {connected === false && (
          <p role="status">進捗の接続が切れています。再接続を待っています…</p>
        )}

        {(devices.data?.volumes ?? []).map((volume) => (
          <CardBanner
            key={volume.volume_instance_id}
            volume={volume}
            // **一覧全体を渡す。** ラベルが無いカードが複数あると同じ既定名に
            // なるので、連番で見分けるには他のカードも見える必要がある。
            label={volumeLabel(devices.data?.volumes ?? [], volume)}
            // **カメラの種類は `work/CardDetail.tsx` と同じ引き当てを使う**
            // （§13。生の slug を出さない。写像は 1 か所にだけ持つ）。
            profileName={profileDisplayName(volume.profile_slug, profiles.data?.profiles ?? [])}
            autoImport={autoImport}
            busy={busy}
            onAct={act}
            onAskTrust={(label, outlook) =>
              setConfirming({
                confirmation: { kind: "trust_volume", label, ...outlook },
                id: volume.volume_instance_id,
              })
            }
          />
        ))}

        {running && (
          <JobCard job={running} rate={averageRate(running)} onCancel={(id) => void cancelJob(id)} />
        )}

        {dashboardData === null ? (
          <p>読み込み中…</p>
        ) : tasks.length === 0 ? (
          <EmptyState />
        ) : (
          <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="sechead">
              <h2>やること</h2>
              <span className="small">{tasks.length} 件</span>
            </div>
            {tasks.map((task) => (
              <TaskCard key={task.kind} task={task} dashboard={dashboardData} />
            ))}
          </section>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 18,
          }}
        >
          <section className="card pad">
            <div className="sechead" style={{ marginBottom: 12 }}>
              <h2 style={{ fontSize: 14 }}>送り先</h2>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {(dashboard.data?.destinations ?? []).length === 0 && (
                <p className="small">送り先がまだありません。</p>
              )}
              {(dashboard.data?.destinations ?? []).map((destination) => (
                <DestinationRow key={destination.destination_id} destination={destination} />
              ))}
            </div>
          </section>
          <section className="card pad">
            <div className="sechead" style={{ marginBottom: 12 }}>
              <h2 style={{ fontSize: 14 }}>さっき取り込んだもの</h2>
              <Link
                to="/photos"
                className="btn sm quiet"
                style={{ marginLeft: "auto", height: 26, padding: "0 8px" }}
              >
                すべて
              </Link>
            </div>
            {(dashboard.data?.recent_imports ?? []).length === 0 ? (
              <p className="small">まだありません。</p>
            ) : (
              <ul style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {(dashboard.data?.recent_imports ?? []).map((media) => (
                  <li key={media.id} className="small">
                    {media.rel_path}（{media.captured_at}）
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {confirming && (
          <ConfirmDialog
            confirmation={confirming.confirmation}
            busy={busy !== null}
            onCancel={() => setConfirming(null)}
            onConfirm={() => void act(confirming.id, "trust")}
          />
        )}
      </div>
    </section>
  );
}

/** いま挿さっているカードの帯（§13）。**理由は常に出す**（判定に外れたカードも）。 */
function CardBanner({
  volume,
  label,
  profileName,
  autoImport,
  busy,
  onAct,
  onAskTrust,
}: {
  volume: Volume;
  label: string;
  profileName: string;
  autoImport: string | null;
  busy: string | null;
  onAct: (volumeId: string, action: "trust" | "scan" | "import" | "close") => void;
  onAskTrust: (label: string, outlook: ReturnType<typeof autoImportOutlook>) => void;
}) {
  const actionable = volume.profile_slug !== null;
  return (
    <section className="card pad hl">
      <div className="rowtop">
        <div className="iconbox on">
          <Icon name="card" />
        </div>
        <div className="grow">
          <h2 style={{ fontSize: 16, fontWeight: 650 }}>
            {volume.trusted ? `${label} のカードが挿さっています` : "初めて見るカードです"}
          </h2>
          {!volume.trusted && actionable && (
            <p className="muted" style={{ marginTop: 4 }}>
              {profileName} のカードのようです。
            </p>
          )}
          {actionable ? (
            <p className="small" style={{ marginTop: 4 }}>
              {autoImportState(volume, autoImport)}
            </p>
          ) : (
            <p role="note" className="small" style={{ marginTop: 4 }}>
              対象外の理由: {volume.reason ?? "不明"}
            </p>
          )}
          {volume.provisional && (
            <p role="note" className="small" style={{ marginTop: 4 }}>
              {profileName} の対象ですが、取り込む中身がまだありません。
            </p>
          )}
        </div>
        <div className="acts" style={{ flexDirection: "column", alignItems: "stretch" }}>
          {actionable && (
            <button
              type="button"
              className="btn primary"
              disabled={busy !== null}
              onClick={() => onAct(volume.volume_instance_id, "import")}
            >
              いま取り込む
            </button>
          )}
          <div className="acts">
            {!volume.trusted && actionable && (
              <button
                type="button"
                className="btn sm outline"
                // 設定を読めていない間は押させない（同意の内容を作れない）。
                disabled={busy !== null || autoImport === null}
                onClick={() => onAskTrust(label, autoImportOutlook(volume, autoImport))}
              >
                このカードを信頼する
              </button>
            )}
            <Link to="/card" className="btn sm">
              中身を見る
            </Link>
            <button
              type="button"
              className="btn sm"
              disabled={busy !== null}
              onClick={() => onAct(volume.volume_instance_id, "close")}
            >
              取り外す
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

const TASK_ICON = { merge: "merge", send: "up", approve: "alert" } as const;

/** やること 1 件（§13）。3 種類しかないので直に分岐する。 */
function TaskCard({ task, dashboard }: { task: Task; dashboard: Dashboard }) {
  if (task.kind === "merge") {
    return (
      <article className="card pad">
        <div className="row">
          <div className="iconbox">
            <Icon name={TASK_ICON.merge} />
          </div>
          <div className="grow">
            <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>
              分かれている動画を {task.count} 本つなぐ
            </h3>
            <p className="small" style={{ marginTop: 3 }}>
              カメラが 4 GiB ごとに分けて保存した動画です。つなぐと 1 本の動画になります。
            </p>
          </div>
          <div className="acts">
            <Link to="/merge" className="btn outline">
              つなぐ
            </Link>
          </div>
        </div>
      </article>
    );
  }
  if (task.kind === "send") {
    const names =
      dashboard.destinations
        .filter((destination) => destination.enabled)
        .map((destination) => destination.name)
        .join(" / ") || "（送り先がありません）";
    return (
      <article className="card pad">
        <div className="row">
          <div className="iconbox">
            <Icon name={TASK_ICON.send} />
          </div>
          <div className="grow">
            <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>{task.count} 件をまだ送っていません</h3>
            <p className="small" style={{ marginTop: 3 }}>
              送り先「{names}」。送るかどうかは毎回こちらで決めます。
            </p>
          </div>
          <div className="acts">
            <Link to="/photos?status=unsent" className="btn sm quiet">
              どれか見る
            </Link>
            <Link to="/send" className="btn primary">
              送る
            </Link>
          </div>
        </div>
      </article>
    );
  }
  return (
    <article className="card pad wn">
      <div className="row">
        <div className="iconbox wn">
          <Icon name={TASK_ICON.approve} />
        </div>
        <div className="grow">
          <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>
            写真の日時を直していいか、{task.count} 件の確認があります
          </h3>
          <p className="small" style={{ marginTop: 3 }}>
            先に誰かが Immich へ上げていた写真です。勝手に書き換えないので、こちらで決めてください。
          </p>
        </div>
        <div className="acts">
          <Link to="/approve" className="btn warnish">
            確認する
          </Link>
        </div>
      </div>
    </article>
  );
}

function EmptyState() {
  return (
    <section className="card pad empty">
      <div
        className="iconbox on"
        style={{ margin: "0 auto 14px", width: 52, height: 52, flex: "none", borderRadius: "50%" }}
      >
        <Icon name="check" size={26} />
      </div>
      <h2 style={{ fontSize: 18, fontWeight: 650 }}>いま、やることはありません</h2>
      <p className="muted" style={{ marginTop: 6 }}>
        カードを挿すと、ここに次にやることが出ます。
      </p>
    </section>
  );
}

/** 送り先 1 件の行。**`pending` は「積んだまま送信が始まっていない」件数**で、
 * 「まだ送っていない」（宛先の有効な記録が無いもの）には含まれない。止まった
 * 送信に気づけるよう、0 より大きいときだけ別枠で足す。 */
function DestinationRow({ destination }: { destination: DestinationSummary }) {
  const base = destination.enabled
    ? `送信済み ${destination.complete} ・ 未送信 ${destination.unsent}`
    : "いまは送りません（休止中）";
  const line = destination.pending > 0 ? `${base} ・ 送信中 ${destination.pending} 件` : base;
  return (
    <div className="row" style={{ gap: 10 }}>
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          flex: "0 0 8px",
          background: destination.enabled ? "var(--ok)" : "var(--ink-3)",
        }}
      />
      <div className="grow">
        <div
          style={{
            fontSize: "13.5px",
            fontWeight: 600,
            color: destination.enabled ? undefined : "var(--ink-2)",
          }}
        >
          {destination.name}
        </div>
        <div className="small">{line}</div>
      </div>
    </div>
  );
}
