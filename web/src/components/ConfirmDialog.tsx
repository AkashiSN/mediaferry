// 不可逆な操作の確認（§13）。
//
// **操作の種類ごとに、確認に出すものが違う。** アップロードは件数・合計サイズ・
// 宛先名だが、宛先の退役や結合グループの破棄にそれらは無い。型で取り違えを防ぐ
// ため、種類ごとの直和にする（計画レビューの指摘）。

import type { ReactNode } from "react";

export type Confirmation =
  | { kind: "upload"; count: number; totalBytes: number; destinationNames: string[] }
  | { kind: "archive_destination"; name: string }
  | { kind: "discard_merge_group"; groupLabel: string; publishedCount: number }
  | { kind: "delete_merge_history"; groupLabel: string }
  | { kind: "remerge_group"; groupLabel: string }
  | { kind: "adopt_failed_merge"; groupLabel: string; reason: string }
  | { kind: "approve_datetime"; current: string | null; proposed: string }
  | { kind: "archive_profile"; slug: string }
  | { kind: "trust_volume"; label: string; state: "starts" | "pending" | "blocked"; reason: string | null };

export function describe(confirmation: Confirmation): { title: string; body: ReactNode } {
  switch (confirmation.kind) {
    case "upload":
      return {
        title: "この内容で送信しますか",
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
        title: "この転送先を退役させますか",
        body: (
          <p>
            {confirmation.name} を退役させます。送信済みの記録は残りますが、以後この宛先へは
            送れなくなります。
          </p>
        ),
      };
    case "discard_merge_group":
      return {
        title: "この結合グループを破棄しますか",
        body: (
          <p>
            {confirmation.groupLabel} を破棄します。公開済みのファイル
            {confirmation.publishedCount} 件は消えませんが、選択肢には出なくなります。
          </p>
        ),
      };
    case "delete_merge_history":
      return {
        title: "この破棄の記録を消しますか",
        body: (
          <p>
            {confirmation.groupLabel} の記録を消します。**もう一度「候補を検出する」を
            押すと、この組み合わせがまた出ることがあります**（記録が「作り直さない」の
            根拠になっているため）。ファイルは何も消えません。
          </p>
        ),
      };
    case "remerge_group":
      return {
        title: "同じ構成でやり直しますか",
        body: (
          <p>
            {confirmation.groupLabel} と同じ構成で新しい候補を作ります。**いまの結合物は
            消えません**（古いグループに残ります）。結合の実装が変わったときに、作り直す
            ための操作です。
          </p>
        ),
      };
    case "adopt_failed_merge":
      return {
        title: "検証に通っていない結合物を採用しますか",
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
        title: "このプロファイルを候補から外しますか",
        body: (
          <p>
            {confirmation.slug} を候補から外します。取り込み済みのファイルと過去の
            リビジョンは残りますが、
            <strong>以後このプロファイルは新しいカードの判定に使われなくなります</strong>。
          </p>
        ),
      };
  }
}

export function formatBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // 端数が無ければ小数点を出さない（「3.0 GiB」より「3 GiB」の方が読みやすい）。
  const shown =
    value >= 10 || unit === 0 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, "");
  return `${shown} ${units[unit]}`;
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
  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
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
