// つないだ動画の記録（§13「詳しい情報」）。別々のままにした組み合わせと、使っていない
// 出力は、どちらも操作できない記録なので、つなぐ画面（`work/Merge.tsx`）には出さずここへ
// 置く。
//
// **使っていない出力の一覧は消さない**（`id="stale"`）。置き換えられて `/merge-groups`
// から見えなくなった出力は、ここからしか辿れない。

import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { request } from "../../api/client";
import { useDashboardReload } from "../../api/dashboard";
import { useMutation, useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import type { Confirmation } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatBytes } from "../../utils/formatBytes";

type Member = { media_file_id: string; rel_path: string };
type DiscardedGroup = { id: string; members: Member[] };
type DiscardedGroups = { groups: DiscardedGroup[] };

type StaleItem = {
  id: string;
  rel_path: string;
  size_bytes: number;
  captured_at: string;
  reason: string;
};
type Stale = { stale: StaleItem[] };

/** 一覧・確認・`aria-label` のどこでも同じ名乗りにする（§13「内部の名前をそのまま出さない」）。 */
function groupLabel(group: DiscardedGroup): string {
  return group.members[0]?.rel_path ?? group.id;
}

export function MergeHistoryScreen() {
  const discarded = useQuery<DiscardedGroups>("/merge-groups?status=skipped");
  const stale = useQuery<Stale>("/media/stale-derived");
  const { hash } = useLocation();
  const deletion = useMutation();
  const refreshTasks = useDashboardReload();
  const [confirmation, setConfirmation] = useState<{
    value: Confirmation;
    run: () => Promise<void>;
  } | null>(null);
  const { received } = useEvents();
  useReloadOnEvents(received, discarded.reload);
  useReloadOnEvents(received, stale.reload);

  async function act(path: string) {
    if (await deletion.run(() => request(path, { method: "DELETE" }))) {
      discarded.reload();
      stale.reload();
      // 消すのは進捗のイベントを出さない。**枠の「やること」も一緒に直す。**
      refreshTasks();
    }
    setConfirmation(null);
  }

  const discardedGroups = discarded.data?.groups ?? [];
  // **`#stale` で来たら、そこまで送る。** router は住所の中の `#` を見ないので、
  // 設定 › 詳しい情報の「使っていないファイル」から来ても、上のほうを出したまま
  // 止まる（節そのものは画面のいちばん下にある）。読み込み終わりを待って送る
  // —— 空のうちに送っても、行が増えた分だけずれる。
  const staleReady = stale.loading;
  useEffect(() => {
    if (hash !== "#stale" || staleReady) {
      return;
    }
    document.getElementById("stale")?.scrollIntoView({ block: "start" });
  }, [hash, staleReady]);

  const staleItems = stale.data?.stale ?? [];
  const staleBytes = staleItems.reduce((sum, item) => sum + item.size_bytes, 0);

  return (
    <section aria-label="つないだ動画の記録" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <h1 className="page title-lg">つないだ動画の記録</h1>

      <ErrorBanner
        error={deletion.error ?? discarded.error ?? stale.error}
        onDismiss={deletion.clear}
      />

      <section className="card pad">
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>別々のままにした組み合わせ</h2>
          <span className="small">{discardedGroups.length} 件</span>
        </div>
        {discardedGroups.length === 0 ? (
          <p className="small">別々のままにした組み合わせはありません。</p>
        ) : (
          <ul style={{ display: "flex", flexDirection: "column", gap: 14, listStyle: "none", padding: 0 }}>
            {discardedGroups.map((group) => (
              <li key={group.id} className="rowtop">
                <ul className="grow" style={{ listStyle: "none", padding: 0 }}>
                  {group.members.map((member) => (
                    <li key={member.media_file_id} className="small ident">
                      {member.rel_path}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="btn sm quiet"
                  aria-label={`消す：${groupLabel(group)}`}
                  disabled={deletion.busy}
                  onClick={() =>
                    setConfirmation({
                      value: { kind: "delete_merge_history", groupLabel: groupLabel(group) },
                      run: () => act(`/merge-groups/${group.id}`),
                    })
                  }
                >
                  消す
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section id="stale" className="card pad">
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>使っていない出力</h2>
          <span className="small">
            {staleItems.length} 件{staleItems.length > 0 ? ` ・ 合計 ${formatBytes(staleBytes)}` : ""}
          </span>
        </div>
        <p className="small" style={{ marginBottom: 12 }}>
          組み直したり、これは別々にしたりして置き換わった結合結果です。元になったファイルは
          ここには出ません。
        </p>
        {staleItems.length === 0 ? (
          <p className="small">使っていない出力はありません。</p>
        ) : (
          <ul style={{ display: "flex", flexDirection: "column", gap: 14, listStyle: "none", padding: 0 }}>
            {staleItems.map((item) => (
              <li key={item.id} className="rowtop">
                <div className="grow">
                  <code className="small ident">{item.rel_path}</code>
                  <div className="small">
                    {formatBytes(item.size_bytes)} ・
                    {item.reason === "superseded" ? "組み直しで置き換わった" : "別々にした組"}
                  </div>
                </div>
                <button
                  type="button"
                  className="btn sm quiet"
                  aria-label={`このファイルを消す：${item.rel_path}`}
                  disabled={deletion.busy}
                  onClick={() =>
                    setConfirmation({
                      value: { kind: "delete_stale_derived", relPath: item.rel_path },
                      run: () => act(`/media/${item.id}`),
                    })
                  }
                >
                  このファイルを消す
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {confirmation && (
        <ConfirmDialog
          confirmation={confirmation.value}
          busy={deletion.busy}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => void confirmation.run()}
        />
      )}
    </section>
  );
}
