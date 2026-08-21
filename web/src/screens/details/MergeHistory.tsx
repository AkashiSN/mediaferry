// つないだ動画の記録（§13「詳しい情報」）。破棄した組み合わせと、使っていない出力は
// どちらも操作できない記録なので、つなぐ画面（`work/Merge.tsx`）には出さずここへ置く。
//
// **使っていない出力の一覧は消さない**（`id="stale"`）。実機では、置き換えられて
// `/merge-groups` から見えなくなった出力が 66 GiB 残っていた経路がここだけだった。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ConfirmDialog, formatBytes } from "../../components/ConfirmDialog";
import type { Confirmation } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";

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

export function MergeHistoryScreen() {
  const discarded = useQuery<DiscardedGroups>("/merge-groups?status=skipped");
  const stale = useQuery<Stale>("/media/stale-derived");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<{
    value: Confirmation;
    run: () => Promise<void>;
  } | null>(null);
  const { received } = useEvents();
  useReloadOnEvents(received, discarded.reload);
  useReloadOnEvents(received, stale.reload);

  async function act(path: string) {
    setBusy(true);
    setError(null);
    try {
      await request(path, { method: "DELETE" });
      discarded.reload();
      stale.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      setConfirmation(null);
    }
  }

  const discardedGroups = discarded.data?.groups ?? [];
  const staleItems = stale.data?.stale ?? [];

  return (
    <section aria-label="つないだ動画の記録" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <h1 className="page lg">つないだ動画の記録</h1>

      <ErrorBanner
        error={error ?? discarded.error ?? stale.error}
        onDismiss={() => setError(null)}
      />

      <section className="card pad">
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>破棄した組み合わせ</h2>
        </div>
        {discardedGroups.length === 0 ? (
          <p className="small">破棄した組み合わせはありません。</p>
        ) : (
          <ul style={{ display: "flex", flexDirection: "column", gap: 14, listStyle: "none", padding: 0 }}>
            {discardedGroups.map((group) => (
              <li key={group.id} className="row" style={{ alignItems: "flex-start" }}>
                <ul className="grow" style={{ listStyle: "none", padding: 0 }}>
                  {group.members.map((member) => (
                    <li key={member.media_file_id} className="small">
                      {member.rel_path}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="btn sm quiet"
                  disabled={busy}
                  onClick={() =>
                    setConfirmation({
                      value: {
                        kind: "delete_merge_history",
                        groupLabel: group.members[0]?.rel_path ?? group.id,
                      },
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

      {staleItems.length > 0 && (
        <section id="stale" className="card pad">
          <div className="sechead" style={{ marginBottom: 12 }}>
            <h2>使っていない出力</h2>
          </div>
          <p className="small" style={{ marginBottom: 12 }}>
            やり直しや破棄で置き換わった結合結果です。元になったファイルはここには出ません。
          </p>
          <ul style={{ display: "flex", flexDirection: "column", gap: 14, listStyle: "none", padding: 0 }}>
            {staleItems.map((item) => (
              <li key={item.id} className="row" style={{ alignItems: "flex-start" }}>
                <div className="grow">
                  <code className="small">{item.rel_path}</code>
                  <div className="small">
                    {formatBytes(item.size_bytes)} ・
                    {item.reason === "superseded" ? "組み直しで置き換わった" : "破棄した組"}
                  </div>
                </div>
                <button
                  type="button"
                  className="btn sm quiet"
                  disabled={busy}
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
        </section>
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
