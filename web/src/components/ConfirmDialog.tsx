// 不可逆な操作の確認（§13）。
//
// **操作の種類ごとに、確認に出すものが違う。** アップロードは件数・合計サイズ・
// 宛先名だが、送り先の退役やつなぐ組み合わせの破棄にそれらは無い。型で取り違えを防ぐ
// ため、種類ごとの直和にする（計画レビューの指摘）。

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { formatBytes } from "../utils/formatBytes";

export type Confirmation =
  | { kind: "upload"; count: number; totalBytes: number; destinationNames: string[] }
  | { kind: "archive_destination"; name: string }
  | { kind: "discard_merge_group"; groupLabel: string; publishedCount: number }
  | { kind: "delete_merge_history"; groupLabel: string }
  | { kind: "delete_stale_derived"; relPath: string }
  | { kind: "remerge_group"; groupLabel: string }
  | { kind: "adopt_failed_merge"; groupLabel: string; reason: string }
  | { kind: "approve_datetime"; current: string | null; proposed: string }
  | { kind: "archive_profile"; slug: string }
  | { kind: "trust_volume"; label: string; state: "starts" | "pending" | "blocked"; reason: string | null };

export function describe(confirmation: Confirmation): { title: string; body: ReactNode } {
  switch (confirmation.kind) {
    case "upload":
      return {
        title: "この内容で送りますか",
        body: (
          <ul>
            <li>{confirmation.count} 件</li>
            <li>合計 {formatBytes(confirmation.totalBytes)}</li>
            <li>宛先: {confirmation.destinationNames.join(" / ")}</li>
          </ul>
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
                （画面の操作は要りません）。取り込みは承認の数秒後に始まります。
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

/** ダイアログの中で焦点を持てるもの。**押せないボタンは飛ばす**（`busy` の間、
 * 「やめる」と「実行する」はどちらも `disabled` になる）。 */
const FOCUSABLE =
  'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]),' +
  ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

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

  // **いちばん新しい `onCancel` と `busy` を、購読を張り直さずに読む。** 呼び出し側は
  // どちらも毎回の描画で作り直すので、依存に入れると開いている間ずっと登録し直しに
  // なり、そのたびに焦点が先頭のボタンへ跳ね返る。
  const latest = useRef({ onCancel, busy });
  useEffect(() => {
    latest.current = { onCancel, busy };
  });

  // **キーボードだけで閉じられ、背後へ焦点が抜けない**（§13。`aria-modal="true"` を
  // 名乗る以上、背後は「無い」ことになっている）。開いたら中へ焦点を移し、閉じたら
  // 開く前に触っていたところへ戻す。
  useEffect(() => {
    const node = dialog.current;
    if (node === null) {
      return;
    }
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = (): HTMLElement[] => [...node.querySelectorAll<HTMLElement>(FOCUSABLE)];
    focusable()[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        // **実行中は閉じない。** 「やめる」が押せない状態と揃える（押した操作が
        // 走っている最中に確認だけ消えると、何が起きたのか分からなくなる）。
        if (!latest.current.busy) {
          latest.current.onCancel();
        }
        return;
      }
      if (event.key !== "Tab" || node === null) {
        return;
      }
      const items = focusable();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      const outside = active === null || !node.contains(active);
      if (event.shiftKey && (outside || active === items[0])) {
        event.preventDefault();
        items[items.length - 1].focus();
      } else if (!event.shiftKey && (outside || active === items[items.length - 1])) {
        event.preventDefault();
        items[0].focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      opener?.focus();
    };
  }, []);

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
