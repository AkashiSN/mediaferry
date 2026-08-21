// 送り先（§12.3 / §13）。**空の DB から 1 件目をここで作れる。**
//
// **既存の API キーは画面に出さない。** 読み出しの API を作らないので、欄は常に空
// から始まり、入れ直したときだけ送る（§12.3）。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { formatDateTime } from "../../utils/formatDateTime";

type Destination = {
  id: string;
  name: string;
  enabled: boolean;
  base_url: string;
  public_url: string | null;
  // 同じ向き先を指す宛先の指紋（§12.3）。同値のものが他にあれば画面で知らせる。
  remote_user_id: string | null;
  verified_at: string | null;
};

type Destinations = { destinations: Destination[] };

/** スタックの見送り（§9.11）。**理由は常に出す**（対象外のときだけ出すと、
 * 出ていないことが仕様に見える）。 */
type SkippedStack = {
  id: string;
  media_file_id: string;
  stack_reason: string | null;
};

type SkippedStacks = { records: SkippedStack[] };

/** 一度に出す件数。**打ち切ったことを黙らない**（下の「ほかにもある」）。 */
const SKIP_PAGE = 50;

function StackSkips({ destinationId }: { destinationId: string }) {
  const skipped = useQuery<SkippedStacks>(
    `/uploads?destination_id=${destinationId}&stack_state=skipped&limit=${SKIP_PAGE}`,
  );
  const records = skipped.data?.records ?? [];
  return (
    <section style={{ marginTop: 12 }}>
      <div className="sechead">
        <h3 style={{ fontSize: "13.5px", fontWeight: 600 }}>スタックの見送り</h3>
      </div>
      {skipped.error !== null && skipped.error !== undefined && (
        <ErrorBanner error={skipped.error} />
      )}
      {skipped.data === undefined ? (
        <p className="small">読み込み中…</p>
      ) : records.length === 0 ? (
        <p className="small">見送りはありません。</p>
      ) : (
        <>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {records.map((record) => (
              <li key={record.id} className="small">
                {record.media_file_id}: {record.stack_reason ?? "理由不明"}
              </li>
            ))}
          </ul>
          {records.length === SKIP_PAGE && (
            <p role="note" className="small">
              先頭 {SKIP_PAGE} 件だけを出しています（ほかにもあります）。
            </p>
          )}
        </>
      )}
    </section>
  );
}

/** 同じ向き先を指す宛先が他にあるか（§12.3。UNIQUE は置かず警告だけ）。 */
export function sharesLibrary(all: Destination[], one: Destination): boolean {
  if (one.remote_user_id === null) {
    return false;
  }
  return all.some((other) => other.id !== one.id && other.remote_user_id === one.remote_user_id);
}

export function DestinationsScreen() {
  const destinations = useQuery<Destinations>("/destinations");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [archiving, setArchiving] = useState<Destination | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // **待つ前に掴んでおく。** `await` の後の `currentTarget` は null になる
    // （React の合成イベントは処理が終わると要素への参照を落とす）。
    const element = event.currentTarget;
    const form = new FormData(element);
    setBusy(true);
    setError(null);
    try {
      await request("/destinations", {
        method: "POST",
        body: {
          name: String(form.get("name") ?? ""),
          base_url: String(form.get("base_url") ?? ""),
          public_url: form.get("public_url") ? String(form.get("public_url")) : null,
          api_key: String(form.get("api_key") ?? ""),
        },
      });
      element.reset();
      destinations.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function archive(destination: Destination) {
    setBusy(true);
    try {
      await request(`/destinations/${destination.id}/archive`, { method: "POST" });
      destinations.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      setArchiving(null);
    }
  }

  /** 送り先 1 件への操作。失敗はバナーに出す（黙って何も起きないのを避ける）。 */
  async function act(path: string, options: { method: string; body?: unknown }) {
    setError(null);
    try {
      await request(path, options);
      destinations.reload();
    } catch (caught) {
      setError(caught);
    }
  }

  const all = destinations.data?.destinations ?? [];

  return (
    <section aria-label="送り先" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <h1 className="page lg">送り先</h1>

      <ErrorBanner error={error ?? destinations.error} onDismiss={() => setError(null)} />

      <section className="card pad">
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>送り先を追加する</h2>
        </div>
        <form onSubmit={(event) => void submit(event)}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <label className="formrow">
              名前
              <input className="field" name="name" required />
            </label>
            <label className="formrow">
              接続先 URL
              <input className="field" name="base_url" required placeholder="http://immich:2283" />
            </label>
            <label className="formrow">
              表示用 URL（任意）
              <input className="field" name="public_url" />
            </label>
            <label className="formrow">
              API キー
              {/* **既存の値は出さない**（§12.3。読み出しの API を作らない）。 */}
              <input className="field" name="api_key" type="password" required />
            </label>
          </div>
          <div className="acts" style={{ marginTop: 14 }}>
            <button type="submit" className="btn primary" disabled={busy}>
              接続を検証して追加する
            </button>
          </div>
        </form>
      </section>

      {all.length === 0 && (
        <p className="muted">送り先はまだありません。上の欄で 1 件目を作れます。</p>
      )}

      {all.map((destination) => (
        <section key={destination.id} className="card pad">
          <div className="rowtop">
            <div className="grow">
              <h2 style={{ fontSize: 16, fontWeight: 650 }}>{destination.name}</h2>
              <p className="small" style={{ marginTop: 4 }}>
                {destination.enabled ? "使えます" : "休止中：送り先の候補に出ません"} ・{" "}
                {destination.base_url}
              </p>
              <p className="small" style={{ marginTop: 4 }}>
                最後に確かめた:{" "}
                {destination.verified_at === null
                  ? "まだ確かめていません"
                  : formatDateTime(destination.verified_at)}
              </p>
              {sharesLibrary(all, destination) && (
                <p role="note" className="small" style={{ marginTop: 4 }}>
                  同じライブラリを指している送り先があります。
                </p>
              )}
            </div>
          </div>
          <StackSkips destinationId={destination.id} />
          <div className="acts" style={{ marginTop: 14 }}>
            <button
              type="button"
              className="btn sm"
              aria-label={`名前を変える：${destination.name}`}
              disabled={busy}
              onClick={() => {
                const name = window.prompt("新しい名前", destination.name);
                if (name !== null && name !== destination.name) {
                  void act(`/destinations/${destination.id}`, { method: "PATCH", body: { name } });
                }
              }}
            >
              名前を変える
            </button>
            <button
              type="button"
              className="btn sm"
              aria-label={`${destination.enabled ? "休止する" : "使う"}：${destination.name}`}
              disabled={busy}
              onClick={() =>
                void act(`/destinations/${destination.id}`, {
                  method: "PATCH",
                  body: { enabled: !destination.enabled },
                })
              }
            >
              {destination.enabled ? "休止する" : "使う"}
            </button>
            <button
              type="button"
              className="btn sm"
              aria-label={`つながるか確かめる：${destination.name}`}
              disabled={busy}
              onClick={() =>
                void act(`/destinations/${destination.id}/verify`, { method: "POST" })
              }
            >
              つながるか確かめる
            </button>
            <button
              type="button"
              className="btn sm"
              aria-label={`状態を再確認する：${destination.name}`}
              disabled={busy}
              onClick={() =>
                void act(`/destinations/${destination.id}/recheck`, { method: "POST" })
              }
            >
              状態を再確認する
            </button>
            <button
              type="button"
              className="btn sm quiet"
              aria-label={`退役させる：${destination.name}`}
              disabled={busy}
              onClick={() => setArchiving(destination)}
            >
              退役させる
            </button>
          </div>
        </section>
      ))}

      {archiving && (
        <ConfirmDialog
          confirmation={{ kind: "archive_destination", name: archiving.name }}
          busy={busy}
          onCancel={() => setArchiving(null)}
          onConfirm={() => void archive(archiving)}
        />
      )}
    </section>
  );
}
