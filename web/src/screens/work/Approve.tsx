// 確認（§13）。**現在値と変更案を並べて**、1 件ずつのカードで出す。
//
// 判断（承認・却下の条件、承認だけ確認を要ること）は変えない。読めなかった値を
// 空欄にしない（空欄は「変更なし」に見える）。

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { MediaTile, type Media } from "../../components/MediaTile";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatDateTime, formatSystemDateTime } from "../../utils/formatDateTime";

type Record_ = {
  id: string;
  destination_id: string;
  media_file_id: string;
  origin: string;
  remote_current: string | null;
  proposed: string | null;
  remote_checked_at: string | null;
  identical: boolean;
};

type Records = { records: Record_[] };
type Destination = { id: string; name: string };
type Destinations = { destinations: Destination[] };

/** `rel_path` の末尾（ファイル名）。**内部の ID を見出しにしない**（§13）。 */
function fileName(relPath: string): string {
  const parts = relPath.split("/");
  return parts[parts.length - 1];
}

export function ApproveScreen() {
  const records = useQuery<Records>("/uploads?state=awaiting_datetime_approval");
  // **どの Immich を書き換えるのかを名前で出すため**に引く（§13。送り先が
  // 2 つあると、id だけでは画面から判別できない）。
  const destinations = useQuery<Destinations>("/destinations");
  const [files, setFiles] = useState<Record<string, Media>>({});
  const [error, setError] = useState<unknown>(null);
  const { received } = useEvents();
  useReloadOnEvents(received, records.reload);
  const [busy, setBusy] = useState(false);
  const [approving, setApproving] = useState<Record_ | null>(null);
  const navigate = useNavigate();

  async function act(record: Record_, action: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      await request(`/uploads/${record.id}/${action}`, { method: "POST" });
      records.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      setApproving(null);
    }
  }

  const rows = records.data?.records ?? [];
  // **依存は id の並びで見る。** 配列は毎回新しいので、そのまま依存に置くと
  // 描画のたびに読み直しに行く。
  const wantedKey = [...new Set(rows.map((record) => record.media_file_id))].join(",");

  // どの写真かをサムネイルで見せるために、対象のファイルを引く。
  // **1 件が読めなくても残りは出す**（`work/Send.tsx` の対象の解決と同じ考え方）。
  useEffect(() => {
    let cancelled = false;
    const wanted = wantedKey === "" ? [] : wantedKey.split(",");
    // **同期で setState しない**（連鎖レンダーになる）。対象が無いときも
    // `allSettled` の解決を通して空へ戻す。
    void Promise.allSettled(wanted.map((id) => request<Media>(`/media/${id}`))).then((results) => {
      if (cancelled) {
        return;
      }
      const found: Record<string, Media> = {};
      for (const result of results) {
        if (result.status === "fulfilled") {
          found[result.value.id] = result.value;
        }
      }
      setFiles(found);
    });
    return () => {
      cancelled = true;
    };
  }, [wantedKey]);

  /** 送り先の表示名。引けないときも**内部の ID は出さない**（§13）。 */
  function destinationName(id: string): string {
    return (
      (destinations.data?.destinations ?? []).find((row) => row.id === id)?.name ?? "分かりません"
    );
  }

  return (
    <section aria-label="確認" className="wrap">
      <div className="row">
        <button type="button" className="btn sm" onClick={() => navigate("/")}>
          <Icon name="back" size={16} />
          ホームへ
        </button>
      </div>
      <div>
        <h1 className="page lg">
          写真の日時を直していいか確かめます
        </h1>
        <p className="muted" style={{ marginTop: 7 }}>
          下の写真は、先に誰かが Immich へ上げていたものです。mediaferry は自分が上げた写真の
          日時しか直しません。書き換えていいかどうかを決めてください。
          <b>却下しても、Immich には何も起きません。</b>
        </p>
      </div>

      <ErrorBanner error={error ?? records.error} onDismiss={() => setError(null)} />

      {rows.length === 0 ? (
        <div className="card pad empty">
          <h2 style={{ fontSize: 17, fontWeight: 650 }}>確認するものはありません</h2>
        </div>
      ) : (
        rows.map((record) => (
          <article key={record.id} className="card pad">
            <div className="rowtop" style={{ flexWrap: "wrap" }}>
              {/* **どの写真かを見せる。** リモートの書き換えは取り消せないので、
                  対象を見ないまま承認させない（§13）。 */}
              {files[record.media_file_id] && (
                // `.tile` は `aspect-ratio: 1` で幅を親から取るので、幅を持つ枠に置く
                // （`work/Send.tsx` の下見と同じ考え方）。
                <div style={{ width: 72, flex: "0 0 auto" }}>
                  <MediaTile media={files[record.media_file_id]} selected={false} />
                </div>
              )}
              <div className="grow">
                {/* 内部の ID をそのまま出さない（§13）。読めないときはそう書く。 */}
                <h2 style={{ fontSize: 15.5, fontWeight: 650 }}>
                  {files[record.media_file_id]
                    ? fileName(files[record.media_file_id].rel_path)
                    : "ファイル名が読めません"}
                </h2>
                <p className="small" style={{ marginTop: 4, marginBottom: 8 }}>
                  送り先: {destinationName(record.destination_id)}
                </p>
                <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
                  <div>
                    <div className="small">Immich にある日時</div>
                    <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2 }}>
                      {/* 読めなかった値は空欄にしない（「変更なし」に見えてしまう）。 */}
                      {record.remote_current === null
                        ? "（読めませんでした）"
                        : formatDateTime(record.remote_current)}
                    </div>
                  </div>
                  <div style={{ alignSelf: "center", color: "var(--ink-3)" }}>→</div>
                  <div>
                    <div className="small">直したい日時</div>
                    <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2, color: "var(--accent-deep)" }}>
                      {record.proposed === null ? "—" : formatDateTime(record.proposed)}
                    </div>
                  </div>
                </div>
                <p className="small" style={{ marginTop: 8 }}>
                  観測した時刻: {record.remote_checked_at ? formatSystemDateTime(record.remote_checked_at) : "—"}
                </p>
              </div>
              <div className="acts">
                {record.identical ? (
                  <span className="small">変更なし</span>
                ) : (
                  <button type="button" className="btn outline" disabled={busy} onClick={() => setApproving(record)}>
                    承認する
                  </button>
                )}
                <button type="button" className="btn sm" disabled={busy} onClick={() => void act(record, "reject")}>
                  却下する
                </button>
              </div>
            </div>
          </article>
        ))
      )}

      {approving && (
        <ConfirmDialog
          confirmation={{
            kind: "approve_datetime",
            // 確認にも読める形で出す（画面の表示と同じ言い方にする）。
            current:
              approving.remote_current === null ? null : formatDateTime(approving.remote_current),
            proposed: approving.proposed === null ? "" : formatDateTime(approving.proposed),
          }}
          busy={busy}
          onCancel={() => setApproving(null)}
          onConfirm={() => void act(approving, "approve")}
        />
      )}
    </section>
  );
}
