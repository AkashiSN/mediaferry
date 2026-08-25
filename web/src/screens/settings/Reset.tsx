// リセット（§13）。**作り直したいときの入口。**
//
// **Immich にある写真は対象ではない** —— 消しに行かないし、消えない。ここで捨てるのは
// mediaferry が持っているものだけ（`docs/history/phase11-design.md` の 6）。
//
// **段は積み上げ。** 深い段は浅い段を含む。浅い順に並べるのは、上から読んで
// 「どこまで捨てるか」を決められるようにするため。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useMutation } from "../../api/hooks";
import { ConfirmDialog, type ResetScope } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";

/** 段 1 つ。**「何が消えるか」と「取り消せるか」を並べて出す。** */
type Stage = { scope: ResetScope; action: string; title: string; note: string; undo: string };

const STAGES: readonly Stage[] = [
  {
    scope: "jobs",
    action: "作業の記録を消す",
    title: "作業の記録",
    note: "作業の履歴と、別々にした組み合わせの記録",
    undo: "作り直せます（もう一度スキャンして探せば戻ります）",
  },
  {
    scope: "uploads",
    action: "送信の記録を消す",
    title: "送信の記録",
    note: "どれをどこへ送ったかの記録",
    // **ここだけ戻らない。** `first_check_result` は不変なので、一度
    // `pre_existing` に落ちると `created_by_us` には二度と戻らない。
    undo: "戻せません（Immich の写真は残りますが、日時の補正とスタックが以後効かなくなります）",
  },
  {
    scope: "library",
    action: "取り込んだファイルを消す",
    title: "取り込んだファイル",
    note: "NAS に取り込んだ写真と動画、つないだ動画",
    undo: "カードに元があれば、取り込み直せます",
  },
  {
    scope: "all",
    action: "すべて消す",
    title: "すべて",
    note: "上のすべてと、カードの記録（信頼の記録を含む）",
    undo: "戻せません（送り先とカメラの種類は残ります）",
  },
];

export function ResetScreen() {
  const running = useMutation();
  const [confirming, setConfirming] = useState<ResetScope | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function run(scope: ResetScope): Promise<void> {
    const ok = await running.run(() => request("/reset", { method: "POST", body: { scope } }));
    setConfirming(null);
    if (ok) {
      setDone(STAGES.find((stage) => stage.scope === scope)?.title ?? null);
    }
  }

  return (
    <section aria-label="リセット" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <div>
        <h1 className="page title-lg">やり直すために消します</h1>
        <p className="muted" style={{ marginTop: 7 }}>
          消えるのは mediaferry が持っているものだけです。
          <strong>Immich にある写真は消えません。</strong>
          下へ行くほど広く消えます（下の段は上の段を含みます）。
        </p>
      </div>

      <ErrorBanner error={running.error} onDismiss={running.clear} />
      {done !== null && <p role="status">{done}を消しました。</p>}

      {STAGES.map((stage) => (
        <section key={stage.scope} className="card pad">
          <div className="sechead" style={{ marginBottom: 8 }}>
            <h2>{stage.title}</h2>
          </div>
          <p className="small">{stage.note}</p>
          <p className="small" style={{ marginTop: 4 }}>
            {stage.undo}
          </p>
          <div className="acts" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn danger"
              disabled={running.busy}
              onClick={() => setConfirming(stage.scope)}
            >
              {stage.action}
            </button>
          </div>
        </section>
      ))}

      {confirming !== null && (
        <ConfirmDialog
          confirmation={{ kind: "reset", scope: confirming }}
          busy={running.busy}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void run(confirming)}
        />
      )}
    </section>
  );
}
