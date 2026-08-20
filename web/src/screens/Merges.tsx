// 結合（§13）。**なぜグループ化されたかが分かる**ように、構成・ギャップ・サイズを出す。

import { useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";
import { formatBytes } from "../components/ConfirmDialog";

type Member = {
  position: number;
  media_file_id: string;
  rel_path: string;
  size_bytes: number;
  duration_seconds: number | null;
  captured_at: string;
};
type Group = {
  id: string;
  status: string;
  detected_by: string;
  input_digest: string;
  verification: { verdict?: string; reason?: string | null } | null;
  superseded_by_id: string | null;
  members: Member[];
};

type Groups = { groups: Group[] };

export function MergesScreen() {
  const groups = useQuery<Groups>("/merge-groups");
  // 破棄したものは既定の一覧に入らない（API の既定が生きているものだけ）。
  const discarded = useQuery<Groups>("/merge-groups?status=skipped");
  const media = useQuery<{ media: { id: string; rel_path: string }[] }>("/media?page_size=200");
  // 手で組むときの選択（**検出が拾えなかった並びを人が組む**）。
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [regrouping, setRegrouping] = useState<Group | null>(null);
  const [error, setError] = useState<unknown>(null);
  const { received } = useEvents();
  useReloadOnEvents(received, groups.reload);
  useReloadOnEvents(received, discarded.reload);
  const [confirmation, setConfirmation] = useState<{ value: Confirmation; run: () => Promise<void> } | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(path: string, body?: unknown, method?: "POST" | "PATCH" | "DELETE") {
    setBusy(true);
    setError(null);
    try {
      await request(path, {
        method: method ?? (path.includes("?action=") ? "PATCH" : "POST"),
        body,
      });
      groups.reload();
      discarded.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      setConfirmation(null);
    }
  }

  function renderGroup(group: Group) {
    return (
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
                    <th>撮影日時</th>
                    <th>長さ</th>
                  </tr>
                </thead>
                <tbody>
                  {group.members.map((member) => (
                    <tr key={member.media_file_id}>
                      <td>{member.rel_path}</td>
                      <td>{formatBytes(member.size_bytes)}</td>
                      <td>{member.captured_at}</td>
                      <td>
                        {member.duration_seconds === null ? "—" : `${Math.round(member.duration_seconds)} 秒`}
                      </td>
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
                {group.status === "merged" && group.superseded_by_id === null && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      setConfirmation({
                        value: {
                          kind: "remerge_group",
                          groupLabel: group.members[0]?.rel_path ?? group.id,
                        },
                        run: () =>
                          act(`/merge-groups/${group.id}?action=regroup`, {
                            media_ids: group.members.map((member) => member.media_file_id),
                          }),
                      })
                    }
                  >
                    同じ構成でやり直す
                  </button>
                )}
                {group.superseded_by_id === null && group.status !== "skipped" && (
                  <button type="button" disabled={busy} onClick={() => setRegrouping(group)}>
                    構成を変える
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
    );
  }

  return (
    <section aria-label="結合">
      <h1>結合</h1>
      <ErrorBanner error={error ?? groups.error} onDismiss={() => setError(null)} />
      <button type="button" disabled={busy} onClick={() => void act("/merge-groups/detect")}>
        候補を検出する
      </button>

      <details>
        <summary>手でグループを作る</summary>
        <p>検出が拾えなかった並びを、2 件以上選んで組みます。</p>
        <ul className="picker">
          {(media.data?.media ?? []).map((row) => (
            <li key={row.id}>
              <label>
                <input
                  type="checkbox"
                  checked={picked.has(row.id)}
                  onChange={() =>
                    setPicked((current) => {
                      const next = new Set(current);
                      if (next.has(row.id)) {
                        next.delete(row.id);
                      } else {
                        next.add(row.id);
                      }
                      return next;
                    })
                  }
                />
                {row.rel_path}
              </label>
            </li>
          ))}
        </ul>
        <button
          type="button"
          disabled={busy || picked.size < 2}
          onClick={() =>
            void act("/merge-groups", { media_ids: [...picked] }).then(() => setPicked(new Set()))
          }
        >
          選んだ {picked.size} 件でグループを作る
        </button>
      </details>
      <ul>
        {(groups.data?.groups ?? []).map(renderGroup)}
      </ul>

      {/* **履歴は畳む。** 破棄したものは操作できず、同じファイル名が繰り返し並ぶので、
          既定の一覧に混ぜると「いま何が起きるのか」が読めなくなる（API の既定も
          生きているグループだけ）。**なぜこの組み合わせを作らないか**の根拠として
          見たい場面はあるので、消さずに開けるようにする。 */}
      {(discarded.data?.groups ?? []).length > 0 && (
        <details>
          <summary>破棄した組み合わせ（{discarded.data?.groups.length ?? 0} 件）</summary>
          <p>
            構成ファイルは手放しているので、これらは記録です。**消すと、もう一度
            「候補を検出する」を押したときに同じ組み合わせがまた出ることがあります**
            （この記録が「作り直さない」の根拠になっています）。
          </p>
          <ul>
            {(discarded.data?.groups ?? []).map((group) => (
              <li key={group.id}>
                <h2>{group.members.length} パート（破棄済み）</h2>
                <ul>
                  {group.members.map((member) => (
                    <li key={member.media_file_id}>{member.rel_path}</li>
                  ))}
                </ul>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    setConfirmation({
                      value: {
                        kind: "delete_merge_history",
                        groupLabel: group.members[0]?.rel_path ?? group.id,
                      },
                      run: () => act(`/merge-groups/${group.id}`, undefined, "DELETE"),
                    })
                  }
                >
                  消す
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {regrouping && (
        <div className="dialog-backdrop" role="presentation">
          <div className="dialog" role="dialog" aria-modal="true" aria-label="構成を変える">
            <h2>構成を変える</h2>
            <p>
              残すパートを選びます。**新しいグループを作り、いまのグループはそちらへ
              向け直します**（公開済みのファイルは消えません）。
            </p>
            <ul>
              {regrouping.members.map((member) => (
                <li key={member.media_file_id}>
                  <label>
                    <input
                      type="checkbox"
                      defaultChecked
                      value={member.media_file_id}
                      name="member"
                    />
                    {member.rel_path}
                  </label>
                </li>
              ))}
            </ul>
            <div className="dialog-actions">
              <button type="button" onClick={() => setRegrouping(null)} disabled={busy}>
                やめる
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  const checked = [
                    ...document.querySelectorAll<HTMLInputElement>(
                      'input[name="member"]:checked',
                    ),
                  ].map((input) => input.value);
                  const target = regrouping;
                  setRegrouping(null);
                  void act(`/merge-groups/${target.id}?action=regroup`, { media_ids: checked });
                }}
              >
                この構成にする
              </button>
            </div>
          </div>
        </div>
      )}
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
