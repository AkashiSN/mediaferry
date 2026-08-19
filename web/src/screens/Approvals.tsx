// 承認待ち（§13）。**現在値と変更案を並べて**出す。

import { useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";

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

export function ApprovalsScreen() {
  const records = useQuery<Records>("/uploads?state=awaiting_datetime_approval");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [approving, setApproving] = useState<Record_ | null>(null);

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

  return (
    <section aria-label="承認待ち">
      <h1>承認待ち</h1>
      <ErrorBanner error={error ?? records.error} onDismiss={() => setError(null)} />
      {(records.data?.records ?? []).length === 0 && <p>承認待ちはありません。</p>}
      <table>
        <thead>
          <tr>
            <th>現在の値</th>
            <th>変更案</th>
            <th>観測した時刻</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(records.data?.records ?? []).map((record) => (
            <tr key={record.id}>
              {/* 読めなかった値は空欄にしない（「変更なし」に見えてしまう）。 */}
              <td>{record.remote_current ?? "（不明）"}</td>
              <td>{record.proposed ?? "—"}</td>
              <td>{record.remote_checked_at ?? "—"}</td>
              <td>
                {record.identical ? (
                  <span>変更なし</span>
                ) : (
                  <button type="button" disabled={busy} onClick={() => setApproving(record)}>
                    承認する
                  </button>
                )}
                <button type="button" disabled={busy} onClick={() => void act(record, "reject")}>
                  却下する
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
