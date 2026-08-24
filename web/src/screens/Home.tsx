// ホーム（§13）。**画面は一覧を持たない** —— カード・作業・集計から 3 つの並びを
// 導き（`hooks/homeSections.ts`）、「いま動いていること」「やること」「いまの様子」
// の順に置く。その下に、送り先ごとの状況と最近の取り込みを添える。
//
// **カードは「状態」ではなく「仕事」として扱う。** 取り込む残りがあるカードは
// やることの札になるので、「カードが挿さっているのに、やることはありません」と
// いう食い違いは、条件の直しではなく形の上で起こり得ない。
//
// **数字より先に、次の一手を出す。** 宛先ごとの件数だけを並べても、読んだ人が
// 何をすればいいかは決まらない。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../api/client";
import { useDashboard } from "../api/dashboard";
import type { DestinationSummary } from "../api/dashboard";
import { useMutation, useQuery } from "../api/hooks";
import { CardStanding } from "../components/CardStanding";
import { ConfirmDialog, type Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";
import { JobCard } from "../components/JobCard";
import { fileName } from "../components/MediaTile";
import { formatBytes } from "../utils/formatBytes";
import type { Job } from "../components/JobProgress";
import { autoImportOutlook, autoImportState, profileDisplayName, volumeLabel } from "./work/CardDetail";
import type { Volume } from "./work/CardDetail";
import { homeSections } from "../hooks/homeSections";
import type { CardView, Standing, Todo } from "../hooks/homeSections";
import { useEvents } from "../hooks/useEvents";
import { isCancellable, useJobPulse } from "../hooks/useJobPulse";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";
import { formatDateTime } from "../utils/formatDateTime";

/** `/devices` の 1 要素。**判定関数と同じ型を土台にする**（`work/CardDetail.tsx`）。
 * ホームはそれに加えて、取り込む残り・数えた時刻・掴まれているかを見る。 */
type DeviceVolume = Volume & Pick<CardView, "pending_count" | "scanned_at" | "busy">;

type Devices = { volumes: DeviceVolume[] };
type Profile = { slug: string; name: string };
type Profiles = { profiles: Profile[] };

type Jobs = { jobs: Job[] };
type Setting = { key: string; value: string | null };
type Settings = { settings: Setting[] };

/** 件数から導くやること（カードの取り込み以外）。 */
type CountedTodo = Exclude<Todo, { kind: "import_card" }>;

export function HomeScreen() {
  // **集計は枠と共有する**（`api/dashboard.tsx`）。ここで別に引くと、取り込み中は
  // 同じ重い集計が 2 本ずつ飛ぶ。
  const dashboard = useDashboard();
  const devices = useQuery<Devices>("/devices");
  const jobs = useQuery<Jobs>("/jobs");
  const settings = useQuery<Settings>("/settings");
  const profiles = useQuery<Profiles>("/profiles");
  const { received, connected } = useEvents();
  useReloadOnEvents(received, devices.reload);
  useReloadOnEvents(received, jobs.reload);
  useReloadOnEvents(received, settings.reload);
  useReloadOnEvents(received, profiles.reload);

  const action = useMutation();
  const [confirming, setConfirming] = useState<{ confirmation: Confirmation; id: string } | null>(
    null,
  );

  // **未解決・失敗は `null` のまま持つ。** 既定値へ倒すと、同意の内容が実挙動と
  // ずれる（`watcher.py` は積まないのに「コピーされます」と書く）。
  const autoImport =
    (settings.data?.settings ?? []).find((setting) => setting.key === "AUTO_IMPORT")?.value ??
    null;

  const volumes = devices.data?.volumes ?? [];
  const profileList = profiles.data?.profiles ?? [];

  // **名前付けの実装を 2 つにしない**（§1）。ラベルの既定名と連番も、カメラの
  // 種類の表示名も `work/CardDetail.tsx` の引き当てをそのまま使う。`volumeLabel`
  // に一覧全体を渡すのは、ラベルが無いカードが複数あると同じ既定名になるため。
  const cards: CardView[] = volumes.map((volume) => ({
    volume_instance_id: volume.volume_instance_id,
    label: volumeLabel(volumes, volume),
    profile_name: profileDisplayName(volume.profile_slug, profileList),
    size_bytes: volume.size_bytes,
    profile_slug: volume.profile_slug,
    trusted: volume.trusted,
    provisional: volume.provisional,
    reason: volume.reason ?? "",
    pending_count: volume.pending_count,
    scanned_at: volume.scanned_at,
    busy: volume.busy,
  }));

  // **やることは画面が持つ一覧ではなく、状態から毎回導く。** 読み込み中は
  // 「読み込み中…」を出し、「やることはありません」とは書かない（直後に件数が
  // 現れて驚かせないため）。読み込み中かどうかは `loading` で見る（`data === null`
  // は失敗のときも真になり、失敗しても「読み込み中…」が消えなくなる）。
  // `const` に取ってから narrowing するのは ——`dashboard.data` を式のあちこちで
  // 直に見ると、`null` チェックの後でも型が絞られず `as` に頼ることになるため。
  const dashboardData = dashboard.data;
  const sections = homeSections({ cards, jobs: jobs.data?.jobs ?? [], counts: dashboardData });
  const nothing =
    sections.doing.length === 0 && sections.todo.length === 0 && sections.standing.length === 0;
  const loading = dashboard.loading || devices.loading || jobs.loading;
  // **読めていないものを「無い」とは言わない。** 3 本のうち 1 本でも値が無ければ
  // （失敗したか、まだ返っていない）、空表示は出さない —— カードが挿さっていても
  // 「やることはありません」と書いてしまう。失敗そのものはバナーが知らせる。
  const unread = dashboardData === null || devices.data === null || jobs.data === null;

  const averageRate = useJobPulse(sections.doing.length > 0, jobs.reload);

  async function trust(volumeId: string) {
    await action.run(async () => {
      await request(`/volumes/${volumeId}/trust`, { method: "POST" });
      devices.reload();
    });
    setConfirming(null);
  }

  /** 信頼の確認を出す。**見通しは `work/CardDetail.tsx` と同じ関数から作る** ——
   * 片方だけで判定すると、同意の内容と実挙動がずれる。 */
  function askTrust(card: CardView) {
    const volume = volumes.find(
      (candidate) => candidate.volume_instance_id === card.volume_instance_id,
    );
    if (volume === undefined) {
      return;
    }
    setConfirming({
      confirmation: {
        kind: "trust_volume",
        label: card.label,
        ...autoImportOutlook(volume, autoImport),
      },
      id: volume.volume_instance_id,
    });
  }

  /** 未信頼のカードに添える、自動取り込みの見通し。信頼のボタンの隣に置くので、
   * 承認すると何が起きるかが押す前に読める。信頼済みのカードには出さない。 */
  function autoImportNote(card: CardView): string | null {
    const volume = volumes.find(
      (candidate) => candidate.volume_instance_id === card.volume_instance_id,
    );
    if (volume === undefined || card.trusted) {
      return null;
    }
    return autoImportState(volume, autoImport);
  }

  /**
   * 「いま取り込む」（§13）。**数える → コピーする → 分かれた動画を探す**を、
   * この順に積む。
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
   */
  async function importNow(volumeId: string, profileSlug: string) {
    await action.run(async () => {
      await request(`/volumes/${volumeId}/scan`, { method: "POST" });
      await request(`/volumes/${volumeId}/import`, { method: "POST" });
      await request(`/merge-groups/detect?profile_slug=${encodeURIComponent(profileSlug)}`, {
        method: "POST",
      });
      devices.reload();
      jobs.reload();
    });
  }

  async function cancelJob(jobId: string) {
    await action.run(async () => {
      await request(`/jobs/${jobId}/cancel`, { method: "POST" });
      jobs.reload();
    });
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
          error={
            action.error ??
            dashboard.error ??
            devices.error ??
            jobs.error ??
            settings.error ??
            profiles.error
          }
          onDismiss={action.clear}
        />

        {connected === false && (
          <p role="status">進捗の接続が切れています。再接続を待っています…</p>
        )}

        {/* **走っている作業は全部出す。**「いま取り込む」は 数える → コピー →
            探す の 3 本を積むので、1 本だけ選ぶと残りが画面から消える。 */}
        {sections.doing.length > 0 && (
          <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="sechead">
              <h2>いま動いていること</h2>
              <span className="small">{sections.doing.length} 件</span>
            </div>
            {sections.doing.map(({ job, card }) => (
              <JobCard
                key={job.id}
                job={job}
                subject={card?.label ?? null}
                rate={averageRate(job)}
                onCancel={isCancellable(job) ? (id) => void cancelJob(id) : undefined}
                cancelBusy={action.busy}
                // **抜いていいかは、掴まれている間こそ要る**（§3）。文言は
                // `CardStanding` の 1 か所だけが持つ。カードに紐づかない作業
                // （送信など）には抜く相手がいないので出さない。
                footer={card === null ? undefined : <CardStanding card={card} />}
              />
            ))}
          </section>
        )}

        {sections.todo.length > 0 && (
          <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="sechead">
              <h2>やること</h2>
              <span className="small">{sections.todo.length} 件</span>
            </div>
            {sections.todo.map((todo) =>
              todo.kind === "import_card" ? (
                <ImportCardTask
                  key={todo.card.volume_instance_id}
                  card={todo.card}
                  note={autoImportNote(todo.card)}
                  busy={action.busy}
                  // 設定を読めていない間は信頼させない（同意の内容を作れない）。
                  canTrust={autoImport !== null}
                  onImport={importNow}
                  onAskTrust={askTrust}
                />
              ) : (
                <TaskCard
                  key={todo.kind}
                  task={todo}
                  destinations={dashboardData?.destinations ?? []}
                />
              ),
            )}
          </section>
        )}

        {/* 仕事も動きも無いカード。**抜いていいかは、押さずに読める**（§3）。 */}
        {sections.standing.length > 0 && (
          <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="sechead">
              <h2>いまの様子</h2>
            </div>
            {sections.standing.map((standing) => (
              <StandingCard key={standing.card.volume_instance_id} standing={standing} />
            ))}
          </section>
        )}

        {/* **空表示は 3 つの並びがすべて空のときだけ。** カードが挿さっていれば
            必ずどれかに出るので、カードと空表示は同時に出ない。 */}
        {nothing &&
          (loading ? (
            <p>読み込み中…</p>
          ) : unread ? (
            // 失敗はすぐ上のバナーで知らせるので、ここには何も書かない
            // （`読み込み中…` を出し続けると、失敗しても消えない）。
            null
          ) : (
            <EmptyState />
          ))}

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
            busy={action.busy}
            onCancel={() => setConfirming(null)}
            onConfirm={() => void trust(confirming.id)}
          />
        )}
      </div>
    </section>
  );
}

/** 取り込む残りがあるカード 1 枚（§1）。**承認と取り込みを 1 つの札に置く** ——
 * 別々の札に立てると、同じカードに対する仕事が 2 件あるように見える。 */
function ImportCardTask({
  card,
  note,
  busy,
  canTrust,
  onImport,
  onAskTrust,
}: {
  card: CardView;
  note: string | null;
  busy: boolean;
  canTrust: boolean;
  onImport: (volumeId: string, profileSlug: string) => void;
  onAskTrust: (card: CardView) => void;
}) {
  // **`slug` を先に取り出す。** 対象と判定できたカードだけがこの札になる
  // （`homeSections`）が、`const` の別名にしないと narrowing が効かない。
  const slug = card.profile_slug;
  return (
    <article className="card pad hl">
      <div className="rowtop">
        <div className="iconbox on">
          <Icon name="card" />
        </div>
        <div className="grow">
          <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>
            {card.label} から {card.pending_count} 件を取り込む
          </h3>
          {/* **容量は、どのカードなのかの手がかり**（§13）。同じカメラのカードが
              2 枚挿さっていると、種類だけでは区別が付かない。 */}
          <p className="small" style={{ marginTop: 4 }}>
            {formatBytes(card.size_bytes)} ・ {card.profile_name}
          </p>
          {note !== null && (
            <p className="small" style={{ marginTop: 4 }}>
              {note}
            </p>
          )}
          <CardStanding card={card} />
        </div>
        <div className="acts" style={{ flexDirection: "column", alignItems: "stretch" }}>
          {slug !== null && (
            <button
              type="button"
              className="btn primary"
              // **この画面の要求中に加えて、そのカードが掴まれているときも落とす。**
              // 走り出した札は「やること」から消えるが、別のタブから押された競合は
              // 消える前に届きうる。
              disabled={busy || card.busy}
              onClick={() => onImport(card.volume_instance_id, slug)}
            >
              いま取り込む
            </button>
          )}
          <div className="acts">
            {!card.trusted && (
              <button
                type="button"
                className="btn sm outline"
                disabled={busy || !canTrust}
                onClick={() => onAskTrust(card)}
              >
                このカードを信頼する
              </button>
            )}
            <Link to="/card" className="btn sm">
              中身を見る
            </Link>
          </div>
        </div>
      </div>
    </article>
  );
}

/** いまの様子 1 件（§1）。**理由は常に出す**（判定に外れたカードも）。 */
function StandingCard({ standing }: { standing: Standing }) {
  const card = standing.card;
  return (
    <article className="card pad">
      <div className="rowtop">
        <div className="iconbox on">
          <Icon name="card" />
        </div>
        <div className="grow">
          <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>
            {card.trusted
              ? `${card.label} のカードが挿さっています`
              : `${card.label} は初めて見るカードです`}
          </h3>
          {!card.trusted && card.profile_slug !== null && (
            <p className="muted" style={{ marginTop: 4 }}>
              {card.profile_name} のカードのようです。
            </p>
          )}
          <p className="small" style={{ marginTop: 4 }}>
            {formatBytes(card.size_bytes)}
          </p>
          <p role="note" className="small" style={{ marginTop: 4 }}>
            {standingLine(standing)}
          </p>
          <CardStanding card={card} />
        </div>
        <div className="acts">
          <Link to="/card" className="btn sm">
            中身を見る
          </Link>
        </div>
      </div>
    </article>
  );
}

/** そのカードがいまどう見えているか（§1）。**数える前の 0 件を「空」と書かない。** */
function standingLine({ card, kind }: Standing): string {
  switch (kind) {
    case "counting":
      return "中身を数えています。";
    case "not_target":
      return `対象外の理由: ${card.reason === "" ? "不明" : card.reason}`;
    case "no_contents":
      return `${card.profile_name} の対象ですが、取り込む中身がまだありません。`;
    case "done":
      return "取り込むものはありません。";
  }
}

const TASK_ICON = { merge: "merge", merge_review: "merge", send: "up", approve: "alert" } as const;

/** 件数から導くやること 1 件（§13）。4 種類しかないので直に分岐する。 */
function TaskCard({
  task,
  destinations,
}: {
  task: CountedTodo;
  destinations: DestinationSummary[];
}) {
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
  if (task.kind === "merge_review") {
    // **つないだが、思ったとおりか自動では確かめきれなかった組。** 送る候補にも
    // 構成ファイルにも出ないので、ここから入らないと画面のどこにも現れない
    // （`work/Merge.tsx` の「中身を見て、これを使う」がその決着）。
    return (
      <article className="card pad">
        <div className="row">
          <div className="iconbox">
            <Icon name={TASK_ICON.merge_review} />
          </div>
          <div className="grow">
            <h3 style={{ fontSize: "14.5px", fontWeight: 600 }}>
              つないだ動画を {task.count} 本、確かめてください
            </h3>
            <p className="small" style={{ marginTop: 3 }}>
              うまくつながったかを自動では確かめきれませんでした。中身を見て、使うかどうかを
              決めてください。決めるまでは送れません。
            </p>
          </div>
          <div className="acts">
            <Link to="/merge" className="btn outline">
              確かめる
            </Link>
          </div>
        </div>
      </article>
    );
  }
  if (task.kind === "send") {
    const names =
      destinations
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
