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

/** `POST /reset` が返す、表ごとに消した件数。 */
type Removed = Record<string, number>;

/**
 * 訳を持つ表だけを、画面の語彙で結果に出す（§13「内部の語を画面へ流さない」）。
 *
 * **訳の無いキーは黙って落とす。** `db/reset.py` が消す表を増やしても、
 * 訳を足すまでは内部名が画面へ出ない。
 */
const REMOVED_LABELS: readonly (readonly [string, string])[] = [
  ["job", "作業の履歴"],
  ["job_event", "出来事"],
  ["upload_record", "送信の記録"],
  ["artifact_staging", "公開の記録"],
  ["media_file", "写真・動画"],
  ["merge_group", "つないだ組み合わせ"],
  ["merge_member", "つないだ元ファイル"],
  ["source_entry", "カードで見つけたファイル"],
  ["volume_instance", "カードの記録"],
  ["volume_presence", "カードを挿した履歴"],
  ["source_device", "カードの識別情報"],
];

/** 実際に消えたものだけを、訳と件数で文にする。**0 件は書かない**（段のカードに何が消えるかは既に書いてある）。 */
function describeRemoved(removed: Removed): string {
  const parts = REMOVED_LABELS.filter(([key]) => (removed[key] ?? 0) > 0).map(
    ([key, label]) => `${label} ${removed[key]} 件`,
  );
  if (parts.length === 0) {
    return "消すものはありませんでした。";
  }
  return `${parts.join("・")}を消しました。`;
}

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
  const [removed, setRemoved] = useState<Removed | null>(null);

  async function run(scope: ResetScope): Promise<void> {
    // **新しい操作を始めた時点で古い結果の帯を消す。** 残したままだと、次の
    // 操作が失敗したときに前回の成功の帯とエラーの帯が同時に出て、支援技術では
    // どちらが今回の応答か見分けが付かない。
    setRemoved(null);
    // **応答の `removed` は `useMutation.run` の返り値では拾えない**（`run` は
    // 成否だけを返す）ので、閉包の中で受け取る。
    let captured: Removed | null = null;
    const ok = await running.run(async () => {
      const body = (await request("/reset", { method: "POST", body: { scope } })) as {
        status: string;
        removed: Removed;
      };
      captured = body.removed;
    });
    setConfirming(null);
    if (ok) {
      setRemoved(captured);
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

      {/* **失敗の帯と同じ位置に置く。** 結果の知らせは種類を問わず同じ場所に出す。 */}
      {removed !== null && (
        <div className="result-banner" role="status">
          <span>{describeRemoved(removed)}</span>
          <button type="button" aria-label="閉じる" onClick={() => setRemoved(null)}>
            <Icon name="close" size={18} />
          </button>
        </div>
      )}

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
