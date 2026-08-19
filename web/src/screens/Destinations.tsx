// 転送先（§12.3 / §13）。**空の DB から 1 件目をここで作れる。**

import { useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";

type Destination = {
  id: string;
  name: string;
  enabled: boolean;
  base_url: string;
  public_url: string | null;
  // 同じ向き先を指す宛先の指紋（§12.3）。同値のものが他にあれば画面で知らせる。
  remote_user_id: string | null;
};

type Destinations = { destinations: Destination[] };

/** 同じ向き先を指す宛先が他にあるか（§12.3。UNIQUE は置かず警告だけ）。 */
export function sharesLibrary(all: Destination[], one: Destination): boolean {
  if (one.remote_user_id === null) {
    return false;
  }
  return all.some(
    (other) => other.id !== one.id && other.remote_user_id === one.remote_user_id,
  );
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

  return (
    <section aria-label="転送先">
      <h1>転送先</h1>
      <ErrorBanner error={error ?? destinations.error} onDismiss={() => setError(null)} />

      <h2>追加する</h2>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          名前
          <input name="name" required />
        </label>
        <label>
          接続先 URL
          <input name="base_url" required placeholder="http://immich:2283" />
        </label>
        <label>
          表示用 URL（任意）
          <input name="public_url" />
        </label>
        <label>
          API キー
          {/* **既存の値は出さない**（§12.3。読み出しの API を作らない）。 */}
          <input name="api_key" type="password" required />
        </label>
        <button type="submit" disabled={busy}>
          接続を検証して追加する
        </button>
      </form>

      <h2>一覧</h2>
      <ul>
        {(destinations.data?.destinations ?? []).map((destination) => (
          <li key={destination.id}>
            <strong>{destination.name}</strong>（{destination.base_url}）
            {!destination.enabled && <span>（無効）</span>}
            {sharesLibrary(destinations.data?.destinations ?? [], destination) && (
              <p role="note">同じライブラリを指している宛先があります。</p>
            )}
            <div className="actions">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void request(`/destinations/${destination.id}/verify`, { method: "POST" })
                    .then(() => destinations.reload())
                    .catch(setError)
                }
              >
                接続を確かめる
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void request(`/destinations/${destination.id}/recheck`, { method: "POST" })
                    .then(() => destinations.reload())
                    .catch(setError)
                }
              >
                状態を再確認する
              </button>
              <button type="button" disabled={busy} onClick={() => setArchiving(destination)}>
                退役させる
              </button>
            </div>
          </li>
        ))}
      </ul>

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
