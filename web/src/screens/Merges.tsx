// 結合（§13）。**なぜグループ化されたかが分かる**ように、構成・ギャップ・サイズを出す。

import { useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { formatBytes } from "../components/ConfirmDialog";

type Member = { media_file_id: string; rel_path: string; size_bytes: number; gap_seconds: number | null };
type Group = {
  id: string;
  status: string;
  detected_by: string;
  input_digest: string;
  verification: { verdict: string; reason: string | null } | null;
  superseded_by_id: string | null;
  members: Member[];
};

type Groups = { groups: Group[] };

export function MergesScreen() {
  const groups = useQuery<Groups>("/merge-groups");
  const [error, setError] = useState<unknown>(null);
  const [confirmation, setConfirmation] = useState<{ value: Confirmation; run: () => Promise<void> } | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await request(path, { method: path.includes("?action=") ? "PATCH" : "POST", body });
      groups.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      setConfirmation(null);
    }
  }

  return (
    <section aria-label="結合">
      <h1>結合</h1>
      <ErrorBanner error={error ?? groups.error} onDismiss={() => setError(null)} />
      <button type="button" disabled={busy} onClick={() => void act("/merge-groups/detect")}>
        候補を検出する
      </button>
      <ul>
        {(groups.data?.groups ?? []).map((group) => (
          <li key={group.id}>
            <h2>
              {group.members.length} パート（{group.status}
              {group.detected_by === "manual" ? " / 手動" : ""}）
            </h2>
            {/* **なぜこの並びなのかを見せる**（§13）。 */}
            <table>
              <thead>
                <tr>
                  <th>ファイル</th>
                  <th>大きさ</th>
                  <th>前との間隔</th>
                </tr>
              </thead>
              <tbody>
                {group.members.map((member) => (
                  <tr key={member.media_file_id}>
                    <td>{member.rel_path}</td>
                    <td>{formatBytes(member.size_bytes)}</td>
                    <td>{member.gap_seconds === null ? "—" : `${member.gap_seconds} 秒`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {group.verification && (
              <p>
                検証: {group.verification.verdict}
                {group.verification.reason ? `（${group.verification.reason}）` : ""}
              </p>
            )}
            <div className="actions">
              {group.status === "detected" && (
                <button type="button" disabled={busy} onClick={() => void act(`/merge-groups/${group.id}/merge`)}>
                  結合する
                </button>
              )}
              {group.status === "failed" && (
                <button type="button" disabled={busy} onClick={() => void act(`/merge-groups/${group.id}/merge`)}>
                  再試行する
                </button>
              )}
              {group.verification?.verdict === "fail" && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    setConfirmation({
                      value: {
                        kind: "adopt_failed_merge",
                        groupLabel: group.members[0]?.rel_path ?? group.id,
                        reason: group.verification?.reason ?? "検証に不合格",
                      },
                      run: () => act(`/merge-groups/${group.id}?action=adopt`),
                    })
                  }
                >
                  不合格でも採用する
                </button>
              )}
              {group.superseded_by_id === null && group.status !== "skipped" && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    setConfirmation({
                      value: {
                        kind: "discard_merge_group",
                        groupLabel: group.members[0]?.rel_path ?? group.id,
                        publishedCount: group.status === "merged" ? 1 : 0,
                      },
                      run: () => act(`/merge-groups/${group.id}?action=discard`),
                    })
                  }
                >
                  破棄する
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {confirmation && (
        <ConfirmDialog
          confirmation={confirmation.value}
          busy={busy}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => void confirmation.run()}
        />
      )}
    </section>
  );
}
