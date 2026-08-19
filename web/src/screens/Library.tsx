// ライブラリ（§13）。一覧・フィルタ・複数選択 → 宛先を選んで送信。

import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";

type Media = {
  id: string;
  rel_path: string;
  kind: string;
  captured_at: string;
  size_bytes: number;
};

type MediaPage = { media: Media[]; total: number; page: number; page_size: number };
type Destinations = { destinations: { id: string; name: string; enabled: boolean }[] };

export function LibraryScreen() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [targets, setTargets] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);

  const query = params.toString();
  const media = useQuery<MediaPage>(`/media${query ? `?${query}` : ""}`, [query]);
  const destinations = useQuery<Destinations>("/destinations");

  const rows = useMemo(() => media.data?.media ?? [], [media.data]);
  const totalBytes = useMemo(
    () => rows.filter((row) => selected.has(row.id)).reduce((sum, row) => sum + row.size_bytes, 0),
    [rows, selected],
  );
  const chosen = (destinations.data?.destinations ?? []).filter((row) => targets.has(row.id));

  function toggle(set: Set<string>, id: string): Set<string> {
    const next = new Set(set);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    return next;
  }

  /**
   * 送信は 2 段階（§11）。
   *
   * `POST /uploads` は media × destination の組を作るだけで、**送信は始まらない**。
   * その後に宛先ごとの `POST /destinations/{id}/upload` が要る。**一部の宛先で
   * 失敗しても、成功した分は進める**（全部やり直しにしない）。
   */
  async function send() {
    setBusy(true);
    setError(null);
    try {
      await request("/uploads", {
        method: "POST",
        body: { media_ids: [...selected], destination_ids: [...targets] },
      });
      const failures: string[] = [];
      for (const destination of chosen) {
        try {
          await request(`/destinations/${destination.id}/upload`, { method: "POST" });
        } catch {
          failures.push(destination.name);
        }
      }
      setNote(
        failures.length === 0
          ? `${selected.size} 件を ${chosen.length} 宛先へ送信し始めました。`
          : `送信を開始できなかった宛先があります: ${failures.join(" / ")}。転送先の画面から再試行してください。`,
      );
      setSelected(new Set());
      setConfirming(false);
    } catch (caught) {
      setError(caught);
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="ライブラリ">
      <h1>ライブラリ</h1>
      <ErrorBanner error={error ?? media.error} onDismiss={() => setError(null)} />
      {note && <p role="status">{note}</p>}

      <form
        className="filters"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const next = new URLSearchParams();
          for (const [key, value] of form.entries()) {
            if (typeof value === "string" && value !== "") {
              next.set(key, value);
            }
          }
          setParams(next);
        }}
      >
        <label>
          種類
          <select name="kind" defaultValue={params.get("kind") ?? ""}>
            <option value="">すべて</option>
            <option value="video">動画</option>
            <option value="photo">写真</option>
          </select>
        </label>
        <label>
          名前
          <input name="q" defaultValue={params.get("q") ?? ""} />
        </label>
        <label>
          宛先
          <select name="destination_id" defaultValue={params.get("destination_id") ?? ""}>
            <option value="">指定なし</option>
            {(destinations.data?.destinations ?? []).map((destination) => (
              <option key={destination.id} value={destination.id}>
                {destination.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          状態
          <select name="status" defaultValue={params.get("status") ?? ""}>
            <option value="">すべて</option>
            <option value="unsent">未送信</option>
            <option value="sent">送信済み</option>
            <option value="failed">失敗</option>
            <option value="awaiting">承認待ち</option>
          </select>
        </label>
        <button type="submit">絞り込む</button>
      </form>

      <p>
        {rows.length} / {media.data?.total ?? 0} 件
      </p>

      <table>
        <thead>
          <tr>
            <th />
            <th>撮影日時</th>
            <th>ファイル</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <input
                  type="checkbox"
                  aria-label={`${row.rel_path} を選ぶ`}
                  checked={selected.has(row.id)}
                  onChange={() => setSelected((current) => toggle(current, row.id))}
                />
              </td>
              <td>{row.captured_at}</td>
              <td>
                <img src={`/api/media/${row.id}/thumbnail`} alt="" width={64} height={64} />
                {row.rel_path}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <fieldset>
        <legend>送り先</legend>
        {(destinations.data?.destinations ?? []).map((destination) => (
          <label key={destination.id}>
            <input
              type="checkbox"
              checked={targets.has(destination.id)}
              disabled={!destination.enabled}
              onChange={() => setTargets((current) => toggle(current, destination.id))}
            />
            {destination.name}
          </label>
        ))}
      </fieldset>

      <button
        type="button"
        disabled={selected.size === 0 || targets.size === 0}
        onClick={() => setConfirming(true)}
      >
        選んだ {selected.size} 件を送信する
      </button>

      {confirming && (
        <ConfirmDialog
          confirmation={{
            kind: "upload",
            count: selected.size,
            totalBytes,
            destinationNames: chosen.map((destination) => destination.name),
          }}
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void send()}
        />
      )}
    </section>
  );
}
