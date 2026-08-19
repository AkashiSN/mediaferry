// ライブラリ（§13）。一覧・フィルタ・複数選択 → 宛先を選んで送信。

import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";

type Media = {
  id: string;
  rel_path: string;
  kind: string;
  captured_at: string;
  size_bytes: number;
};

type MediaPage = { media: Media[]; total: number; page: number; page_size: number };
type Pair = {
  media_file_id: string;
  destination_id: string;
  result: string;
  upload_record_id: string | null;
  reason: string | null;
};
type PairResult = { pairs: Pair[] };
type Destinations = { destinations: { id: string; name: string; enabled: boolean }[] };

/** 送信の結果を 1 文にする（**断られた組と、開始に失敗した宛先を隠さない**）。 */
export function summarise(
  total: number,
  rejected: { reason: string | null }[],
  failures: string[],
  started: number,
): string {
  const parts = [`${total - rejected.length} 組を作り、${started} 宛先で送信を始めました。`];
  if (rejected.length > 0) {
    const reasons = [...new Set(rejected.map((pair) => pair.reason ?? "理由不明"))];
    parts.push(`送れない組が ${rejected.length} 件ありました（${reasons.join(" / ")}）。`);
  }
  if (failures.length > 0) {
    parts.push(`開始できなかった宛先: ${failures.join(" / ")}。転送先の画面から再試行できます。`);
  }
  return parts.join("");
}

export function LibraryScreen() {
  const [params, setParams] = useSearchParams();
  // **選んだものは、隠れても覚えておく。** 大きさも一緒に持つ —— 表示中の行から
  // 計算すると、絞り込みで隠した分が合計から抜けて、確認の数字が実際と食い違う。
  const [selected, setSelected] = useState<Map<string, number>>(new Map());
  const [targets, setTargets] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);

  const query = params.toString();
  const media = useQuery<MediaPage>(`/media${query ? `?${query}` : ""}`, [query]);
  const destinations = useQuery<Destinations>("/destinations");
  // 取り込みや送信が進んだら取り直す（**画面を再読み込みせずに進む**。§13）。
  const { received } = useEvents();
  useReloadOnEvents(received, media.reload);

  const rows = useMemo(() => media.data?.media ?? [], [media.data]);
  const totalBytes = useMemo(
    () => [...selected.values()].reduce((sum, size) => sum + size, 0),
    [selected],
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

  function toggleMedia(current: Map<string, number>, row: Media): Map<string, number> {
    const next = new Map(current);
    if (next.has(row.id)) {
      next.delete(row.id);
    } else {
      next.set(row.id, row.size_bytes);
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
      const created = (await request("/uploads", {
        method: "POST",
        body: { media_ids: [...selected.keys()], destination_ids: [...targets] },
      })) as PairResult;
      // **組ごとの結果を読む。** 送れない組（結合中のグループの構成ファイルなど）は
      // backend が理由付きで断る。**受け付けられた組がある宛先だけ**送信を始める。
      const accepted = new Set(
        created.pairs.filter((pair) => pair.result !== "rejected").map((pair) => pair.destination_id),
      );
      const rejected = created.pairs.filter((pair) => pair.result === "rejected");
      const failures: string[] = [];
      for (const destination of chosen.filter((one) => accepted.has(one.id))) {
        try {
          await request(`/destinations/${destination.id}/upload`, { method: "POST" });
        } catch {
          failures.push(destination.name);
        }
      }
      setNote(summarise(created.pairs.length, rejected, failures, accepted.size));
      setSelected(new Map());
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
                  onChange={() => setSelected((current) => toggleMedia(current, row))}
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
