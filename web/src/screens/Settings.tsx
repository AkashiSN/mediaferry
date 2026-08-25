// 設定（§13）。**入口と要約だけを置く。** 中身は送り先・カメラの種類・詳しい設定に
// 分かれていて、ここからそれぞれへ入る。
//
// 自動取り込みは「信頼したカードを挿すだけで NAS へコピーするか」の切り替えで、
// **送信の可否とは無関係**（送信はどちらの設定でも常に手動。§12.1）。

import { Link } from "react-router-dom";

import { request } from "../api/client";
import { useMutation, useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon, type IconName } from "../components/Icon";

type Setting = { key: string; value: string | null; locked: boolean; writable: boolean };
type Settings = { settings: Setting[] };
type Destination = { id: string; name: string; enabled: boolean };
type Destinations = { destinations: Destination[] };
type Profile = { slug: string; name: string; archived: boolean };
type Profiles = { profiles: Profile[] };

/** ふだんは見ないが、困ったときに要る画面（§13）。**性質ごとに分ける.**
 *
 * 1 つの節に混ぜると、「設定」というラベルで括られたものの中身が読めない ——
 * 実際にこの中で設定なのは「詳しい設定」1 つだけで、残りは**作業の画面**と
 * **記録**である。
 *
 * **「つなぐ」と「日時の確認」への常設の入口をここに置く。** ホームの「やること」
 * に出るのは仕事が残っているときだけなので、0 件の状態では入る道が無くなる
 * （ナビは 3 つで、`/merge` と `/approve` への導線は他に無い）。手でグループを
 * 作るのも、閾値を変えて探し直すのも、つなぐ画面にしかない。
 */
type Entry = { to: string; title: string; note: string; icon: IconName };

const WORK: readonly Entry[] = [
  {
    to: "/merge",
    title: "つなぐ",
    note: "分かれた動画を探して 1 本にする。手で組むこともできます",
    icon: "list",
  },
  {
    // ホームの「確認」の札は、待っているものがあるときだけ出る。**何を確認した
    // のか・いま何が待っているのかを、札が出ていないときにも見られるようにする。**
    to: "/approve",
    title: "日時の確認",
    note: "先に Immich にあった写真の日時を直していいか",
    icon: "list",
  },
  { to: "/card", title: "接続中のカード", note: "判定の理由と、信頼の記録", icon: "card" },
];

const RECORDS: readonly Entry[] = [
  { to: "/settings/jobs", title: "作業の履歴", note: "取り込みや送信がいつ終わったか", icon: "list" },
  {
    to: "/settings/merge-history",
    title: "つないだ後の後片付け",
    note: "別々のままにした組み合わせと、使っていない出力を片付ける",
    icon: "list",
  },
  {
    // 置き換わった出力は、それを作った記録と同じ画面にある（どのつなぎ直しで
    // 置き換わったかが並んでいないと、消してよいか判断できない）。
    to: "/settings/merge-history#stale",
    title: "使っていないファイル",
    note: "つなぎ直しで置き換わった古い出力",
    icon: "list",
  },
];

const ADVANCED: readonly Entry[] = [
  {
    to: "/settings/general",
    title: "詳しい設定",
    note: "データの置き場所や送信の待ち時間。アプリ設定で固定されている項目もあります",
    icon: "gear",
  },
];

/** 入口の並び 1 つ。**作業の画面には遷移元を渡す** —— 入口が 2 つあるので、
 * 戻り先は来た道で決める（`components/BackLink.tsx`）。 */
function Entries({ entries, carryFrom }: { entries: readonly Entry[]; carryFrom: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {entries.map((entry) => (
        <Link
          key={entry.title}
          to={entry.to}
          state={carryFrom ? { from: "/settings" } : undefined}
          className="navitem"
          style={{ height: 52 }}
        >
          <Icon name={entry.icon} />
          <span className="grow">
            <b style={{ display: "block", fontSize: "13.5px", color: "var(--ink)" }}>
              {entry.title}
            </b>
            <span className="small">{entry.note}</span>
          </span>
        </Link>
      ))}
    </div>
  );
}

export function SettingsScreen() {
  const settings = useQuery<Settings>("/settings");
  const destinations = useQuery<Destinations>("/destinations");
  const profiles = useQuery<Profiles>("/profiles");
  const toggling = useMutation();

  // **読めていない値を既定へ倒さない。** off なのに「オン」と出すと、挿しただけで
  // コピーされると誤解させる（`work/CardDetail.tsx` と同じ扱い）。
  const autoImport =
    (settings.data?.settings ?? []).find((setting) => setting.key === "AUTO_IMPORT") ?? null;
  const on = autoImport?.value === "trusted";

  async function toggleAutoImport() {
    if (autoImport === null) {
      return;
    }
    const saved = await toggling.run(() =>
      request("/settings", {
        method: "PUT",
        body: { key: "AUTO_IMPORT", value: on ? "off" : "trusted" },
      }),
    );
    if (saved) {
      settings.reload();
    }
  }

  const living = (profiles.data?.profiles ?? []).filter((profile) => !profile.archived);

  return (
    <section aria-label="設定" className="wrap">
      <h1 className="page title-lg">設定</h1>

      <ErrorBanner
        error={toggling.error ?? settings.error ?? destinations.error ?? profiles.error}
        onDismiss={toggling.clear}
      />

      <section className="card pad">
        <div className="row">
          <div className="grow">
            <h2 style={{ fontSize: "14.5px", fontWeight: 650 }}>
              信頼したカードを自動で取り込む
            </h2>
            <p className="small" style={{ marginTop: 4 }}>
              オフにすると、信頼したカードでも毎回こちらで「取り込む」を押します。
              <strong>送信はどちらの設定でも常に手動です。</strong>
            </p>
            {autoImport?.locked === true && (
              <p role="note" className="small" style={{ marginTop: 4 }}>
                <Icon name="lock" size={14} /> アプリ設定で固定されています。TrueNAS のアプリ
                設定で変えてください。
              </p>
            )}
          </div>
          <button
            type="button"
            className="switch"
            role="switch"
            aria-checked={on}
            aria-label="自動で取り込む"
            // 読めていない間と、固定されている間は押させない（送っても 409 で断られる）。
            disabled={toggling.busy || autoImport === null || !autoImport.writable}
            onClick={() => void toggleAutoImport()}
          >
            <i />
          </button>
        </div>
      </section>

      <section className="card pad">
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>送り先</h2>
          <Link to="/settings/destinations" className="btn sm quiet" style={{ marginLeft: "auto" }}>
            送り先を管理する
          </Link>
        </div>
        {destinations.loading ? (
          <p className="small">読み込み中…</p>
        ) : destinations.data === null ? (
          // 失敗はすぐ上のバナーで知らせるので、ここには何も書かない
          // （`読み込み中…` を出し続けると、失敗しても消えない）。
          null
        ) : destinations.data.destinations.length === 0 ? (
          <p className="small">送り先はまだありません。</p>
        ) : (
          <ul style={{ display: "flex", flexDirection: "column", gap: 12, listStyle: "none", padding: 0 }}>
            {destinations.data.destinations.map((destination) => (
              <li key={destination.id} className="row" style={{ gap: 10 }}>
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
                  <div style={{ fontSize: "13.5px", fontWeight: 600 }}>{destination.name}</div>
                  <div className="small">
                    {destination.enabled ? "使えます" : "休止中：送り先の候補に出ません"}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card pad">
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>カメラの種類</h2>
          <Link to="/settings/profiles" className="btn sm quiet" style={{ marginLeft: "auto" }}>
            カメラの種類を変える
          </Link>
        </div>
        <p className="small" style={{ marginBottom: 12 }}>
          挿したカードがどの機種かを見分けるための決まりです。ふだん触る必要はありません。
        </p>
        {living.length === 0 ? (
          <p className="small">使える決まりがありません。</p>
        ) : (
          <ul style={{ display: "flex", flexDirection: "column", gap: 10, listStyle: "none", padding: 0 }}>
            {living.map((profile) => (
              <li key={profile.slug} className="row">
                <span className="grow" style={{ fontSize: "13.5px" }}>
                  {profile.name}
                </span>
                <span className="small">{profile.slug}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card pad" aria-label="ふだんは使わない操作">
        <div className="sechead" style={{ marginBottom: 10 }}>
          <h2>ふだんは使わない操作</h2>
        </div>
        <p className="small" style={{ marginBottom: 12 }}>
          ホームの「やること」に出ていないときも、ここから入れます。
        </p>
        <Entries entries={WORK} carryFrom />
      </section>

      <section className="card pad" aria-label="記録">
        <div className="sechead" style={{ marginBottom: 10 }}>
          <h2>記録</h2>
        </div>
        <p className="small" style={{ marginBottom: 12 }}>
          ふだんは見なくて大丈夫です。困ったときにここを開きます。
        </p>
        <Entries entries={RECORDS} carryFrom={false} />
      </section>

      <section className="card pad" aria-label="詳しい設定">
        <div className="sechead" style={{ marginBottom: 10 }}>
          <h2>詳しい設定</h2>
        </div>
        <Entries entries={ADVANCED} carryFrom={false} />
      </section>
    </section>
  );
}
