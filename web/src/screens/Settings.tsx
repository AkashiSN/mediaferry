// 設定（§13）。**入口と要約だけを置く。** 中身は送り先・カメラの種類・詳しい設定に
// 分かれていて、ここからそれぞれへ入る。
//
// 自動取り込みは「信頼したカードを挿すだけで NAS へコピーするか」の切り替えで、
// **送信の可否とは無関係**（送信はどちらの設定でも常に手動。§12.1）。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";

type Setting = { key: string; value: string | null; locked: boolean; writable: boolean };
type Settings = { settings: Setting[] };
type Destination = { id: string; name: string; enabled: boolean };
type Destinations = { destinations: Destination[] };
type Profile = { slug: string; name: string; archived: boolean };
type Profiles = { profiles: Profile[] };

/** ふだんは見ないが、困ったときに要る画面（§13「詳しい情報」）。 */
const DETAILS: readonly { to: string; title: string; note: string }[] = [
  { to: "/settings/jobs", title: "作業の履歴", note: "取り込みや送信がいつ終わったか" },
  {
    to: "/settings/merge-history",
    title: "つないだ動画の記録",
    note: "どの部品から作ったか、別々のままにした組み合わせ",
  },
  {
    // 置き換わった出力は、それを作った記録と同じ画面にある（どのつなぎ直しで
    // 置き換わったかが並んでいないと、消してよいか判断できない）。
    to: "/settings/merge-history#stale",
    title: "使っていないファイル",
    note: "つなぎ直しで置き換わった古い出力",
  },
  { to: "/card", title: "接続中のカード", note: "判定の理由と、信頼の記録" },
];

export function SettingsScreen() {
  const settings = useQuery<Settings>("/settings");
  const destinations = useQuery<Destinations>("/destinations");
  const profiles = useQuery<Profiles>("/profiles");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  // **読めていない値を既定へ倒さない。** off なのに「オン」と出すと、挿しただけで
  // コピーされると誤解させる（`work/CardDetail.tsx` と同じ扱い）。
  const autoImport =
    (settings.data?.settings ?? []).find((setting) => setting.key === "AUTO_IMPORT") ?? null;
  const on = autoImport?.value === "trusted";

  async function toggleAutoImport() {
    if (autoImport === null) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await request("/settings", {
        method: "PUT",
        body: { key: "AUTO_IMPORT", value: on ? "off" : "trusted" },
      });
      settings.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const living = (profiles.data?.profiles ?? []).filter((profile) => !profile.archived);

  return (
    <section aria-label="設定" className="wrap">
      <h1 className="page lg">設定</h1>

      <ErrorBanner
        error={error ?? settings.error ?? destinations.error ?? profiles.error}
        onDismiss={() => setError(null)}
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
            disabled={busy || autoImport === null || !autoImport.writable}
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
        {destinations.data === null ? (
          <p className="small">読み込み中…</p>
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

      <section className="card pad">
        <div className="sechead" style={{ marginBottom: 10 }}>
          <h2>詳しい情報</h2>
        </div>
        <p className="small" style={{ marginBottom: 12 }}>
          ふだんは見なくて大丈夫です。困ったときにここを開きます。
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {DETAILS.map((entry) => (
            <Link key={entry.title} to={entry.to} className="navitem" style={{ height: 52 }}>
              <Icon name="list" />
              <span className="grow">
                <b style={{ display: "block", fontSize: "13.5px", color: "var(--ink)" }}>
                  {entry.title}
                </b>
                <span className="small">{entry.note}</span>
              </span>
            </Link>
          ))}
          <Link to="/settings/general" className="navitem" style={{ height: 52 }}>
            <Icon name="gear" />
            <span className="grow">
              <b style={{ display: "block", fontSize: "13.5px", color: "var(--ink)" }}>詳しい設定</b>
              <span className="small">
                データの置き場所や送信の待ち時間。アプリ設定で固定されている項目もあります
              </span>
            </span>
          </Link>
        </div>
      </section>
    </section>
  );
}
