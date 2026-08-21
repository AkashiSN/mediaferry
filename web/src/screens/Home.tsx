// ホーム（§13）。いま挿さっているカードの帯、やること、進行中の作業、
// 送り先ごとの状況、最近の取り込みを上から順に置く。
//
// **数字より先に、次の一手を出す。** 宛先ごとの件数だけを並べても、読んだ人が
// 何をすればいいかは決まらない。「やること」を上に置き、件数はその下に添える。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog, type Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";
import { JobCard } from "../components/JobCard";
import { fileName } from "../components/MediaTile";
import type { Job } from "../components/JobProgress";
import { autoImportOutlook, autoImportState, profileDisplayName, volumeLabel } from "./work/CardDetail";
import type { Volume } from "./work/CardDetail";
import { useEvents } from "../hooks/useEvents";
import { isLive, useJobPulse } from "../hooks/useJobPulse";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";
import { tasksFrom } from "../hooks/useTasks";
import type { Task } from "../hooks/useTasks";
import { formatDateTime } from "../utils/formatDateTime";

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

  /**
   * カードの帯の「いま取り込む」（§13）。**数える → コピーする → 分かれた動画を
   * 探す**を、この順に積む。
   *
   * **コピーは数えた結果を読む。** 取り込みのジョブは前のスキャンが残した
   * `source_entry` を publish するだけなので、数えずにコピーだけを積むと、
   * ジョブは成功のまま 1 件も取り込まない。
   *
   * **探すところまでやる。** ホームの「やること」は現行の結合候補の数から導く
   * ので、取り込んだあとに探しておかないと「つなぐ」が出ない（入口そのものは
   * 設定 › 詳しい情報に常設してある）。カメラの種類がつながない設定なら、
   * 探すジョブは「結合しない」と記録して何もしない。
   *
   * ジョブは積んだ順に 1 本ずつ走るので、探すのは取り込みが終わったあとになる。
   *
   * `profileSlug` は必ず値を持つ。**このボタンは対象と判定できたカードにしか
   * 出ない**（`CardBanner` の `actionable`）。
   */
  async function importNow(volumeId: string, profileSlug: string) {
    setBusy(`${volumeId}:import`);
    setError(null);
    try {
      await request(`/volumes/${volumeId}/scan`, { method: "POST" });
      await request(`/volumes/${volumeId}/import`, { method: "POST" });
      await request(`/merge-groups/detect?profile_slug=${encodeURIComponent(profileSlug)}`, {
        method: "POST",
      });
      devices.reload();
      jobs.reload();
    } catch (caught) {
      setError(caught);
    } finally {
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

  // **やることは画面が持つ一覧ではなく、状態から毎回導く。** 読み込み中は
  // 「読み込み中…」を出し、「やることはありません」とは書かない
  // （直後に件数が現れて驚かせないため）。読み込み中かどうかは `dashboard.loading`
  // で見る（`dashboardData === null` は失敗のときも真になり、失敗しても
  // 「読み込み中…」が消えなくなる）。`const` に取ってから narrowing するのは
  // ——`dashboard.data` を式のあちこちで直に見ると、`null` チェックの後でも
  // 型が絞られず `as` に頼ることになるため。
  const dashboardData = dashboard.data;
  const tasks: Task[] = dashboardData === null ? [] : tasksFrom(dashboardData);

  const running = (jobs.data?.jobs ?? []).find(isLive);
  const averageRate = useJobPulse(running !== undefined, jobs.reload);

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
            onImport={importNow}
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

        {dashboard.loading ? (
          <p>読み込み中…</p>
        ) : dashboardData === null ? (
          // 失敗はすぐ上のバナーで知らせるので、ここには何も書かない
          // （`読み込み中…` を出し続けると、失敗しても消えない）。
          null
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

        {dashboardData !== null && (dashboardData.orphans > 0 || dashboardData.missing > 0) && (
          // **報告はするが、消す操作は置かない**（自動削除はデータを失う経路になる。
          // `docs/decisions.md`）。
          <p role="status" className="card pad small">
            注意が要るもの：どこにも結び付いていないファイル {dashboardData.orphans} 件 ・
            見つからないファイル {dashboardData.missing} 件。どちらも自動では消しません。
          </p>
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
              {/* **高さをインラインで指定しない。** `styles.css` は狭い画面で
                  `.btn.sm` を 44px に戻すが、インラインの `height` はそれを
                  上回るので §13「押せる領域は 44px 以上」を割ってしまう。 */}
              <Link to="/photos" className="btn sm quiet" style={{ marginLeft: "auto" }}>
                すべて
              </Link>
            </div>
            {(dashboard.data?.recent_imports ?? []).length === 0 ? (
              <p className="small">まだありません。</p>
            ) : (
              <ul style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {/* **内部の表現をそのまま出さない**（§13）。相対パスはファイル名に、
                    `captured_at` の ISO 文字列は読める日時に写す。 */}
                {(dashboard.data?.recent_imports ?? []).map((media) => (
                  <li key={media.id} className="small">
                    {fileName(media.rel_path)}（{formatDateTime(media.captured_at)}）
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
  onImport,
  onAskTrust,
}: {
  volume: Volume;
  label: string;
  profileName: string;
  autoImport: string | null;
  busy: string | null;
  onAct: (volumeId: string, action: "trust" | "scan" | "import" | "close") => void;
  onImport: (volumeId: string, profileSlug: string) => void;
  onAskTrust: (label: string, outlook: ReturnType<typeof autoImportOutlook>) => void;
}) {
  // **`slug` を先に取り出す。** `actionable` から narrowing を効かせるには、
  // 別名が `const` である必要がある（そうしないと「いま取り込む」に渡す型が
  // `string | null` のままになる）。
  const slug = volume.profile_slug;
  const actionable = slug !== null;
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
              onClick={() => onImport(volume.volume_instance_id, slug)}
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
 * 送信に気づけるよう、0 より大きいときだけ別枠で足す。
 *
 * **`failed` も同じ扱いで出す。** 送れなかったものは「まだ送っていない」にも
 * 「送信済み」にも入らないので、ここに出さないと画面のどこにも現れない
 * （送り直すのは 設定 › 送り先）。休止中でも失敗の記録は消えないので出す。 */
function DestinationRow({ destination }: { destination: DestinationSummary }) {
  const parts = destination.enabled
    ? [`送信済み ${destination.complete} ・ 未送信 ${destination.unsent}`]
    : ["いまは送りません（休止中）"];
  if (destination.pending > 0) {
    parts.push(`送信中 ${destination.pending} 件`);
  }
  if (destination.failed > 0) {
    parts.push(`送れなかった ${destination.failed} 件`);
  }
  const line = parts.join(" ・ ");
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
