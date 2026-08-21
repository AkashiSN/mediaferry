// つなぐ（§13）。**なぜグループ化されたかが分かる**ように、構成・ギャップ・パートサイズを出す。
//
// 操作できない記録（破棄した組み合わせ・使っていない出力）はここに出さない。混ざると
// 「いま何が起きるのか」が読めなくなるため、設定 › 詳しい情報（`details/MergeHistory.tsx`）
// に置く。

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ConfirmDialog, formatBytes } from "../../components/ConfirmDialog";
import type { Confirmation } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatDateTime } from "../../utils/formatDateTime";

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
  output: { media_file_id: string; rel_path: string; size_bytes: number; missing: boolean } | null;
  members: Member[];
};

type Groups = { groups: Group[] };

/** `position` 順に並べる。**API の順を信用しない**（表示も計算もこの順で行う）。 */
function ordered(members: Member[]): Member[] {
  return [...members].sort((a, b) => a.position - b.position);
}

/**
 * パート間でいちばん大きい空白（秒）。**これが「なぜ同じ 1 本と判断したか」の根拠**。
 *
 * 隣り合うどちらかの長さが読めない（`duration_seconds === null`）と、そのつなぎ目の
 * 終端が計算できない。**0 として扱わない** —— 0 は「隙間なく続いている」という
 * 積極的な主張であり、読めなかっただけの部分を「別の撮影ではない」と断定してしまう。
 * その場合は `null`（分からない）を返す。
 */
function gapSeconds(members: Member[]): number | null {
  const sorted = ordered(members);
  let max = 0;
  for (let i = 1; i < sorted.length; i += 1) {
    const previous = sorted[i - 1];
    if (previous.duration_seconds === null) {
      return null;
    }
    const previousEnd = Date.parse(previous.captured_at) + previous.duration_seconds * 1000;
    const gapMs = Date.parse(sorted[i].captured_at) - previousEnd;
    max = Math.max(max, gapMs / 1000);
  }
  return Math.round(max * 10) / 10;
}

function totalBytes(members: Member[]): number {
  return members.reduce((sum, member) => sum + member.size_bytes, 0);
}

function totalMinutes(members: Member[]): number {
  const seconds = members.reduce((sum, member) => sum + (member.duration_seconds ?? 0), 0);
  return Math.round(seconds / 60);
}

export function MergeScreen() {
  const groups = useQuery<Groups>("/merge-groups");
  // 手で組むときの選択肢（**検出が拾えなかった並びを人が組む**）。
  const media = useQuery<{ media: { id: string; rel_path: string }[] }>("/media?page_size=200");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [regrouping, setRegrouping] = useState<Group | null>(null);
  const [error, setError] = useState<unknown>(null);
  const { received } = useEvents();
  useReloadOnEvents(received, groups.reload);
  const [confirmation, setConfirmation] = useState<{ value: Confirmation; run: () => Promise<void> } | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function act(path: string, body?: unknown, method?: "POST" | "PATCH" | "DELETE") {
    setBusy(true);
    setError(null);
    try {
      await request(path, {
        method: method ?? (path.includes("?action=") ? "PATCH" : "POST"),
        body,
      });
      groups.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      setConfirmation(null);
    }
  }

  function renderGroup(group: Group) {
    const members = ordered(group.members);
    const gap = gapSeconds(members);
    const warn = gap !== null && gap > 0;
    return (
      <article key={group.id} className={`card pad${warn ? " wn" : ""}`}>
        <div className="rowtop" style={{ flexWrap: "wrap" }}>
          <div className="parts">
            {members.map((member, index) => (
              <div key={member.media_file_id} className={`t${index % 8}`}>
                <Icon name="play" />
              </div>
            ))}
          </div>
          <div className="grow">
            <h2 style={{ fontSize: "15.5px", fontWeight: 650 }}>
              {members.length} つに分かれています
              {group.detected_by === "manual" ? "（手動）" : ""}
              {group.status === "failed" ? "・結合に失敗しました" : ""}
            </h2>
            <p className="muted" style={{ marginTop: 4 }}>
              合計 {formatBytes(totalBytes(members))} ・ つなぐと 1 本（約 {totalMinutes(members)} 分）
            </p>
            <p className="small" style={{ marginTop: 3, color: warn ? "var(--warn)" : undefined }}>
              {gap === null
                ? "長さが読めないパートがあるので、つなぎ目の空白は分かりません。"
                : warn
                  ? `つなぎ目に ${gap} 秒の空白があります。別の撮影かもしれないので、確かめてから決めてください。`
                  : "連番が続いていて、つなぎ目の空白は 0.0 秒です。だから同じ 1 本と判断しました。"}
            </p>
            <ul style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10, listStyle: "none" }}>
              {members.map((member) => (
                <li key={member.media_file_id} className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ fontSize: 13 }}>{member.rel_path}</span>
                  <span className="small">
                    {formatBytes(member.size_bytes)} ・ {formatDateTime(member.captured_at)} ・
                    {member.duration_seconds === null ? "—" : ` ${Math.round(member.duration_seconds)} 秒`}
                  </span>
                </li>
              ))}
            </ul>
            {group.output && (
              <p className="small" style={{ marginTop: 8 }}>
                できたファイル: <code>{group.output.rel_path}</code>（
                {formatBytes(group.output.size_bytes)}
                {group.output.missing ? "・見つかりません" : ""}）
              </p>
            )}
            {group.verification && (
              <p className="small">
                検証: {group.verification.verdict}
                {group.verification.reason ? `（${group.verification.reason}）` : ""}
              </p>
            )}
          </div>
          <div className="acts">
            {group.status === "detected" && (
              <button type="button" className="btn outline" disabled={busy} onClick={() => void act(`/merge-groups/${group.id}/merge`)}>
                つなぐ
              </button>
            )}
            {group.status === "failed" && (
              <button type="button" className="btn warnish" disabled={busy} onClick={() => void act(`/merge-groups/${group.id}/merge`)}>
                再試行する
              </button>
            )}
            {group.status === "failed" && (
              // Ruling 12: 結合に失敗した組は §10 の既定の一覧から外れ、送る手段が無い。
              // ここから member を対象にした送るへ進めるようにする。
              <button
                type="button"
                className="btn sm"
                disabled={busy}
                onClick={() =>
                  navigate("/send", { state: { ids: members.map((member) => member.media_file_id) } })
                }
              >
                個別に送る
              </button>
            )}
            {group.verification?.verdict === "fail" && (
              <button
                type="button"
                className="btn sm"
                disabled={busy}
                onClick={() =>
                  setConfirmation({
                    value: {
                      kind: "adopt_failed_merge",
                      groupLabel: members[0]?.rel_path ?? group.id,
                      reason: group.verification?.reason ?? "検証に不合格",
                    },
                    run: () => act(`/merge-groups/${group.id}?action=adopt`),
                  })
                }
              >
                中身を見て、これを使う
              </button>
            )}
            {group.status === "merged" && group.superseded_by_id === null && (
              <button
                type="button"
                className="btn sm"
                disabled={busy}
                onClick={() =>
                  setConfirmation({
                    value: {
                      kind: "remerge_group",
                      groupLabel: members[0]?.rel_path ?? group.id,
                    },
                    run: () =>
                      act(`/merge-groups/${group.id}?action=regroup`, {
                        media_ids: members.map((member) => member.media_file_id),
                      }),
                  })
                }
              >
                同じ構成でやり直す
              </button>
            )}
            {group.superseded_by_id === null && (
              <button type="button" className="btn sm" disabled={busy} onClick={() => setRegrouping(group)}>
                構成を変える
              </button>
            )}
            {group.superseded_by_id === null && (
              <button
                type="button"
                className="btn sm"
                disabled={busy}
                onClick={() =>
                  setConfirmation({
                    value: {
                      kind: "discard_merge_group",
                      groupLabel: members[0]?.rel_path ?? group.id,
                      publishedCount: group.status === "merged" ? 1 : 0,
                    },
                    run: () => act(`/merge-groups/${group.id}?action=discard`),
                  })
                }
              >
                これは別々
              </button>
            )}
          </div>
        </div>
      </article>
    );
  }

  const rows = groups.data?.groups ?? [];

  return (
    <section aria-label="つなぐ" className="wrap">
      <div className="row">
        <button type="button" className="btn sm" onClick={() => navigate("/")}>
          <Icon name="back" size={16} />
          ホームへ
        </button>
      </div>
      <div>
        <h1 className="page lg">
          分かれている動画を 1 本につなぎます
        </h1>
        <p className="muted" style={{ marginTop: 7 }}>
          カメラは長い動画を、ある大きさごとに区切って保存します。ここでつないでおくと、
          Immich には 1 本の動画として並びます。つないだあとも、元の分かれたファイルは
          NAS に残ります。
        </p>
      </div>

      <ErrorBanner error={error ?? groups.error} onDismiss={() => setError(null)} />

      <div>
        <button type="button" className="btn sm" disabled={busy} onClick={() => void act("/merge-groups/detect")}>
          分かれた動画を探す
        </button>
      </div>

      <details>
        <summary>手でグループを作る</summary>
        <p className="small" style={{ marginTop: 8 }}>
          検出が拾えなかった並びを、2 件以上選んで組みます。
        </p>
        <ul style={{ display: "flex", flexDirection: "column", gap: 6, listStyle: "none" }}>
          {(media.data?.media ?? []).map((row) => (
            <li key={row.id}>
              <label className="row">
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
          className="btn sm"
          disabled={busy || picked.size < 2}
          onClick={() => void act("/merge-groups", { media_ids: [...picked] }).then(() => setPicked(new Set()))}
        >
          選んだ {picked.size} 件でグループを作る
        </button>
      </details>

      {rows.length === 0 ? (
        <div className="card pad empty">
          <h2 style={{ fontSize: 17, fontWeight: 650 }}>つなぐものはありません</h2>
          <p className="muted" style={{ marginTop: 6 }}>
            分かれた動画が見つかると、ここに出ます。
          </p>
        </div>
      ) : (
        rows.map(renderGroup)
      )}

      {regrouping && (
        <div className="dialog-backdrop" role="presentation">
          <div className="dialog" role="dialog" aria-modal="true" aria-label="構成を変える">
            <h2>構成を変える</h2>
            <p>
              残すパートを選びます。新しいグループを作り、いまのグループはそちらへ
              向け直します（公開済みのファイルは消えません）。
            </p>
            <ul>
              {ordered(regrouping.members).map((member) => (
                <li key={member.media_file_id}>
                  <label>
                    <input type="checkbox" defaultChecked value={member.media_file_id} name="member" />
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
                    ...document.querySelectorAll<HTMLInputElement>('input[name="member"]:checked'),
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
