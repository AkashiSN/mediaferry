// 確認（§13）。**現在値と変更案を並べて**、1 件ずつのカードで出す。
//
// 判断（承認・却下の条件、承認だけ確認を要ること）は変えない。読めなかった値を
// 空欄にしない（空欄は「変更なし」に見える）。

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatDateTime } from "../../utils/formatDateTime";

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

export function ApproveScreen() {
  const records = useQuery<Records>("/uploads?state=awaiting_datetime_approval");
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

  return (
    <section aria-label="確認" className="wrap">
      <div className="row">
        <button type="button" className="btn sm" onClick={() => navigate("/")}>
          <Icon name="back" size={16} />
          ホームへ
        </button>
      </div>
      <div>
        <h1 className="page" style={{ fontSize: 24 }}>
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
              <div className="grow">
                {/* 内部の ID をそのまま出さない（§13）。名乗れる名前が無いので見出しは置かない。 */}
                <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
                  <div>
                    <div className="small">Immich にある日時</div>
                    <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2 }}>
                      {/* 読めなかった値は空欄にしない（「変更なし」に見えてしまう）。 */}
                      {record.remote_current ?? "（読めませんでした）"}
                    </div>
                  </div>
                  <div style={{ alignSelf: "center", color: "var(--ink-3)" }}>→</div>
                  <div>
                    <div className="small">直したい日時</div>
                    <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2, color: "var(--accent-deep)" }}>
                      {record.proposed ?? "—"}
                    </div>
                  </div>
                </div>
                <p className="small" style={{ marginTop: 8 }}>
                  観測した時刻: {record.remote_checked_at ? formatDateTime(record.remote_checked_at) : "—"}
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
            current: approving.remote_current,
            proposed: approving.proposed ?? "",
          }}
          busy={busy}
          onCancel={() => setApproving(null)}
          onConfirm={() => void act(approving, "approve")}
        />
      )}
    </section>
  );
}
