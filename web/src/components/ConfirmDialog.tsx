// 不可逆な操作の確認（§13）。
//
// **操作の種類ごとに、確認に出すものが違う。** アップロードは件数・合計サイズ・
// 宛先名だが、送り先の退役やつなぐ組み合わせの破棄にそれらは無い。型で取り違えを防ぐ
// ため、種類ごとの直和にする（計画レビューの指摘）。

import { useRef } from "react";
import type { ReactNode } from "react";

import { formatBytes } from "../utils/formatBytes";
import { formatDate } from "../utils/formatDateTime";
import { useDialogFocus } from "./useDialogFocus";

export type Confirmation =
  | {
      kind: "upload";
      count: number;
      totalBytes: number;
      destinationNames: string[];
      /** つないだ動画の数。**元のファイルとは別物**なので、内訳を出す。 */
      derivedCount: number;
      /** 撮影日の幅。読める値が 1 つも無ければ `null`。 */
      capturedRange: { from: string; to: string } | null;
      /** 対象が 1 度に読める上限で切れているか（裁定 20）。 */
      truncated: boolean;
    }
  | { kind: "archive_destination"; name: string }
  | { kind: "discard_merge_group"; groupLabel: string; publishedCount: number }
  | { kind: "delete_merged_video"; name: string; sourceCount: number; freesSources: boolean }
  | { kind: "delete_merge_history"; groupLabel: string }
  | { kind: "delete_stale_derived"; relPath: string }
  | { kind: "remerge_group"; groupLabel: string }
  | { kind: "adopt_failed_merge"; groupLabel: string; reason: string }
  | { kind: "approve_datetime"; current: string | null; proposed: string }
  | { kind: "archive_profile"; slug: string }
  | { kind: "trust_volume"; label: string; state: "starts" | "pending" | "blocked"; reason: string | null }
  | { kind: "reset"; scope: ResetScope };

/** リセットの段（`db/reset.py` の `SCOPES` と同じ並び）。 */
export type ResetScope = "jobs" | "uploads" | "library" | "all";

const RESET_TITLE: Record<ResetScope, string> = {
  jobs: "作業の記録を消しますか",
  uploads: "送信の記録を消しますか",
  library: "取り込んだファイルを消しますか",
  all: "すべて消しますか",
};

/**
 * リセットの確認（§13）。
 *
 * **「Immich からは何も消えません」と「戻せません」を、必ず両方書く。** 片方だけだと
 * 読み違える —— 前者だけなら代償が無いように読め、後者だけなら Immich の写真が
 * 消えると読める。
 */
const RESET_BODY: Record<ResetScope, ReactNode> = {
  jobs: (
    <p>
      作業の履歴と、別々にした組み合わせの記録を消します。ファイルも送信の記録も
      消えません。<strong>もう一度探せば戻ります。</strong>
    </p>
  ),
  uploads: (
    <p>
      どれをどこへ送ったかの記録を消します。<strong>Immich からは何も消えません。</strong>
      ただし<strong>戻せません</strong> —— 次に送ったときに「自分が上げた」と証明できなく
      なるので、撮影日時の自動補正は 1 件ずつの承認に変わり、RAW と JPEG を 1 枚に
      束ねるのも行われなくなります。
    </p>
  ),
  library: (
    <p>
      NAS に取り込んだ写真と動画、つないだ動画を消します。
      <strong>Immich からは何も消えません。</strong>
      <strong>カードに元があれば、取り込み直せます。</strong>
      送信の記録も一緒に消えるので、送り直したときの扱いは上の段と同じになります。
    </p>
  ),
  all: (
    <p>
      上のすべてに加えて、カードの記録（どのカードを信頼したか）を消します。
      <strong>Immich からは何も消えません。</strong>
      <strong>送り先とカメラの種類は残ります</strong>ので、接続のやり直しは要りません。
      <strong>戻せません。</strong>
    </p>
  ),
};

/** 撮影日の幅を 1 行にする。**同じ日なら範囲にしない**（「A 〜 A」は読みにくい）。 */
function describeRange(range: { from: string; to: string }): string {
  const from = formatDate(range.from);
  const to = formatDate(range.to);
  return from === to ? from : `${from} 〜 ${to}`;
}

export function describe(confirmation: Confirmation): { title: string; body: ReactNode } {
  switch (confirmation.kind) {
    case "upload":
      return {
        title: "この内容で送りますか",
        body: (
          <>
            <ul>
              <li>{confirmation.count} 件</li>
              {/* **0 件のときは書かない。** 「つないだ動画が 0 件」は、無い話を
                  1 行使って言うだけで、読む側の負担にしかならない。 */}
              {confirmation.derivedCount > 0 && (
                <li>うち、つないだ動画が {confirmation.derivedCount} 件</li>
              )}
              <li>合計 {formatBytes(confirmation.totalBytes)}</li>
              {confirmation.capturedRange !== null && (
                <li>撮影日: {describeRange(confirmation.capturedRange)}</li>
              )}
              <li>宛先: {confirmation.destinationNames.join(" / ")}</li>
            </ul>
            {/* **押した後に「全部送った」と誤解させない**（裁定 20）。送る画面の
                帯にも同じことが出ているが、**確認は押す直前の最後の 1 枚**なので、
                ここでも言う。 */}
            {confirmation.truncated && (
              <p>
                <strong>これで全部ではありません。</strong>
                1 度に送れる分を超えているので、送り終えたらもう一度送ってください。
              </p>
            )}
          </>
        ),
      };
    case "archive_destination":
      return {
        title: "この送り先を退役させますか",
        body: (
          <p>
            {confirmation.name} を退役させます。送信済みの記録は残りますが、以後この宛先へは
            送れなくなります。
          </p>
        ),
      };
    case "discard_merge_group":
      return {
        title: "このつなぐ組み合わせを破棄しますか",
        body: (
          <p>
            {confirmation.groupLabel} を破棄します。公開済みのファイル
            {confirmation.publishedCount} 件は消えませんが、選択肢には出なくなります。
          </p>
        ),
      };
    case "delete_merged_video":
      return {
        title: "このつないだ動画を消しますか",
        body: (
          <p>
            {confirmation.name} を消します。
            {confirmation.freesSources ? (
              <>
                {" "}
                <strong>
                  元になった {confirmation.sourceCount} 件は残り、「まだ送っていない」に戻ります
                </strong>
                。
              </>
            ) : (
              <>
                {" "}
                <strong>元になった {confirmation.sourceCount} 件は残ります</strong>。
              </>
            )}
            この動画は NAS から消え、<strong>元に戻せません</strong>。
          </p>
        ),
      };
    case "delete_merge_history":
      return {
        title: "この記録を消しますか",
        body: (
          <p>
            {confirmation.groupLabel} の記録を消します。
            <strong>
              もう一度「分かれた動画を探す」を押すと、この組み合わせがまた出ることがあります
            </strong>
            （記録が「作り直さない」の根拠になっているため）。ファイルは何も消えません。
          </p>
        ),
      };
    case "delete_stale_derived":
      return {
        title: "このつないだファイルを消しますか",
        body: (
          <p>
            {confirmation.relPath} を消します。<strong>元になったファイルは残ります</strong>。
            もう現行でないグループをつないだ結果なので、選択肢には出ていません。
          </p>
        ),
      };
    case "remerge_group":
      return {
        title: "同じ構成でやり直しますか",
        body: (
          <p>
            {confirmation.groupLabel} と同じ構成で新しい候補を作ります。
            <strong>いまつないだファイルは消えません</strong>
            （古いグループに残ります）。つなぎ方が変わったときに、作り直すための操作です。
          </p>
        ),
      };
    case "adopt_failed_merge":
      return {
        title: "検証に通っていないファイルを採用しますか",
        body: (
          <p>
            {confirmation.groupLabel} は「{confirmation.reason}」で不合格です。採用すると
            送信の選択肢に出ます。
          </p>
        ),
      };
    case "approve_datetime":
      return {
        title: "リモートの日時を書き換えますか",
        body: (
          <p>
            現在 {confirmation.current ?? "（不明）"} → 変更後 {confirmation.proposed}
          </p>
        ),
      };
    case "trust_volume":
      return {
        title: "このカードを信頼しますか",
        body: (
          <>
            {confirmation.state === "starts" && (
              <p>
                {confirmation.label} を信頼すると、
                <strong>
                  いま入っている中身も含めて、以後このカードを挿すだけで NAS へコピーされます
                </strong>
                （画面の操作は要りません）。取り込みは信頼した数秒後に始まります。
              </p>
            )}
            {/* **条件は文全体に掛ける。** 約束を先に無条件で置いてから限定を
                付け足すと、確かめられない媒体（`fs_uuid` が無い等）では前半が
                成立せず、同じダイアログの中で矛盾する。 */}
            {confirmation.state === "pending" && (
              <p>
                {confirmation.label} を信頼すると、
                <strong>
                  {confirmation.reason}に限り、いま入っている中身も含めて、以後このカードを
                  挿すだけで NAS へコピーされます
                </strong>
                （画面の操作は要りません）。確かめられない媒体では、信頼を記録するだけで
                取り込みは始まりません。
              </p>
            )}
            {confirmation.state === "blocked" && (
              // **始まらないのに「コピーされます」と書かない。** 同意の内容が
              // 実挙動とずれる（`watcher.py` の CANDIDATES を満たしていない）。
              <p>
                {confirmation.label} の信頼を記録します。ただし{confirmation.reason}ので、
                <strong>いまは自動取り込みは始まりません</strong>。条件が整うと、挿すだけで
                NAS へコピーされるようになります。
              </p>
            )}
            {/* **信頼の限界を明示する**（§12.1）。指紋は同一性の証明ではない。 */}
            <p>
              見分けはカードの中身の指紋で行うので、
              <strong>同じ UUID の別のカードや、復元したカードを取り違えることがあります。</strong>
            </p>
          </>
        ),
      };
    case "reset":
      return {
        title: RESET_TITLE[confirmation.scope],
        body: RESET_BODY[confirmation.scope],
      };
    case "archive_profile":
      return {
        title: "このカメラの種類を候補から外しますか",
        body: (
          <p>
            {confirmation.slug} を候補から外します。取り込み済みのファイルと過去の
            リビジョンは残りますが、
            <strong>以後このカメラの種類は新しいカードの判定に使われなくなります</strong>。
          </p>
        ),
      };
  }
}

export function ConfirmDialog({
  confirmation,
  onConfirm,
  onCancel,
  busy = false,
}: {
  confirmation: Confirmation;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const { title, body } = describe(confirmation);
  const dialog = useRef<HTMLDivElement>(null);

  // **キーボードだけで閉じられ、背後へ焦点が抜けない**（§13）。
  useDialogFocus(dialog, onCancel, busy);

  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog" ref={dialog} role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        {body}
        <div className="dialog-actions">
          <button type="button" onClick={onCancel} disabled={busy}>
            やめる
          </button>
          <button type="button" onClick={onConfirm} disabled={busy}>
            実行する
          </button>
        </div>
      </div>
    </div>
  );
}
