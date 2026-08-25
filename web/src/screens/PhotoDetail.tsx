// 1 件のくわしく（§13）。写真タブでタイルを押すと開く。**ここで「それが何かを知り、
// いらなければ消す」が完結する** —— API は `GET /media/{id}` の 1 本で描くのに
// 必要なものをすべて返す（複数の API を継ぎ足すと片方だけ古い状態が出るため）。

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { request } from "../api/client";
import { useMutation, useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";
import { fileName } from "../components/MediaTile";
import { formatBytes } from "../utils/formatBytes";
import { formatDateTime } from "../utils/formatDateTime";

type SourceItem = {
  media_file_id: string;
  rel_path: string;
  position: number;
  missing: boolean;
};

type DestinationItem = {
  destination_id: string;
  name: string;
  state: string | null;
  presence: string;
};

type MediaDetail = {
  id: string;
  role: "original" | "derived";
  rel_path: string;
  size_bytes: number;
  kind: string;
  captured_at: string;
  captured_at_source: string;
  duration_seconds: number | null;
  probe_state: string;
  missing_at: string | null;
  sources: SourceItem[];
  destinations: DestinationItem[];
  deletable: boolean;
  delete_blocked_reason: string | null;
  delete_frees_sources: boolean;
};

/**
 * 宛先ごとの状況（§13 の 7 語）。**サーバは語彙を返し、日本語にするのはここだけ**
 * ——`_presence`（`api/routes_media.py`）が返す 7 語をここで初めて日本語にする。
 */
const PRESENCE: Record<string, string> = {
  not_sent: "まだ送っていません",
  sending: "送っている最中です",
  present: "Immich に入っています",
  trashed: "Immich のゴミ箱にあります",
  gone: "Immich にはもうありません",
  unknown: "Immich にあるか確かめていません",
  failed: "送れませんでした",
};

/** 動画の長さを `分:秒` に丸める（`MediaTile` の `formatClipLength` と同じ書式）。 */
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function PhotoDetailScreen() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const detail = useQuery<MediaDetail>(id === undefined ? "" : `/media/${id}`, [id]);
  const deletion = useMutation();
  const [confirming, setConfirming] = useState(false);

  const data = detail.data;

  async function runDelete() {
    if (id === undefined) {
      return;
    }
    if (await deletion.run(() => request(`/media/${id}`, { method: "DELETE" }))) {
      // **消したものの画面に留まらない**（§13）。写真タブへ戻す。
      navigate("/photos");
      return;
    }
    setConfirming(false);
  }

  const confirmation: Confirmation | null =
    data === null
      ? null
      : {
          kind: "delete_merged_video",
          name: fileName(data.rel_path),
          sourceCount: data.sources.length,
          freesSources: data.delete_frees_sources,
        };

  return (
    <section aria-label="くわしく" className="wrap">
      <div className="row">
        <Link to="/photos" className="btn sm">
          <Icon name="back" size={16} />
          写真へ
        </Link>
      </div>

      <ErrorBanner error={detail.error ?? deletion.error} onDismiss={deletion.clear} />

      {data && (
        <>
          <img
            src={`/api/media/${data.id}/thumbnail`}
            alt=""
            style={{ width: "100%", maxHeight: 360, objectFit: "contain", borderRadius: 12 }}
          />

          <div>
            <h1 className="page title-lg">{fileName(data.rel_path)}</h1>
            {data.role === "derived" && (
              <p className="muted">つないだ動画（{data.sources.length} 本から）</p>
            )}
            <p className="small">
              {formatDateTime(data.captured_at)}
              {data.kind === "video" && data.duration_seconds != null
                ? ` ・ ${formatDuration(data.duration_seconds)}`
                : ""}
              {` ・ ${formatBytes(data.size_bytes)}`}
            </p>
          </div>

          <section className="card pad">
            <h2>宛先ごとの状況</h2>
            {data.destinations.length === 0 ? (
              <p className="small">宛先がありません。</p>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                {data.destinations.map((dest) => (
                  <li key={dest.destination_id} className="rowtop">
                    <span className="grow">{dest.name}</span>
                    <span className="small">{PRESENCE[dest.presence] ?? dest.presence}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {data.role === "derived" && (
            <section className="card pad">
              <h2>元になったファイル</h2>
              {data.sources.length === 0 ? (
                <p className="small">見つかりません。</p>
              ) : (
                <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                  {[...data.sources]
                    .sort((left, right) => left.position - right.position)
                    .map((source) => (
                      <li key={source.media_file_id}>
                        <Link to={`/photos/${source.media_file_id}`}>{fileName(source.rel_path)}</Link>
                        {source.missing && <span className="small"> （見当たりません）</span>}
                      </li>
                    ))}
                </ul>
              )}
            </section>
          )}

          <div className="acts">
            <button
              type="button"
              className="btn primary"
              onClick={() => navigate("/send", { state: { ids: [data.id], destinationIds: [] } })}
            >
              送る
            </button>
            <button
              type="button"
              className="btn"
              disabled={!data.deletable}
              onClick={() => setConfirming(true)}
            >
              消す
            </button>
          </div>
          {!data.deletable && data.delete_blocked_reason !== null && (
            <p className="small">{data.delete_blocked_reason}</p>
          )}
        </>
      )}

      {confirming && confirmation !== null && (
        <ConfirmDialog
          confirmation={confirmation}
          busy={deletion.busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void runDelete()}
        />
      )}
    </section>
  );
}
