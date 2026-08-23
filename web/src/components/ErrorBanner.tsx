// 失敗の表示（§13）。**例外の文字列をそのまま出さない。**

import { useState } from "react";

import { ApiError } from "../api/errors";

/**
 * **画面が自分で書いた、そのまま出してよい失敗の文言。**
 *
 * 素の `Error` を定型文へ潰すのは、内部の文言や相手由来の値が漏れるのを防ぐため。
 * だが画面が利用者に向けて書いた文（「YAML として読めません（2 行目）」など）まで
 * 潰すと、**直し方を知らせるために書いた文が消えて定型文だけが残る**。この型で
 * 包んだものだけを通す。
 */
export class UserFacingError extends Error {}

/** 閉じたかどうかを覚える鍵。**同じ内容なら同じ鍵**（例外の同一性では見ない）。 */
function contentKey(error: unknown): string {
  if (error instanceof ApiError) {
    return `api:${error.status}:${error.code}:${error.detail}`;
  }
  if (error instanceof UserFacingError) {
    return `user:${error.message}`;
  }
  return "other";
}

/**
 * 失敗を 1 本の帯で出す。
 *
 * **「閉じる」は必ず効く。** 呼び出し側は自分の state と `useQuery` の失敗を
 * `??` で束ねて渡すが、`onDismiss` で消せるのは前者だけ。取り直しの失敗を
 * 渡されているときに帯が消えないと、押しても何も起きないボタンになる。
 *
 * 覚えるのは**例外の同一性ではなく中身**。取り込み中は 1 秒おきに取り直すので
 * （`useReloadOnEvents`）、失敗が続いていると例外は毎回作り直され、同一性で
 * 覚えていると「閉じる」を押した 1 秒後にまた出てくる。覚えは**溜める**：
 * 束ねて渡された 2 つを順に閉じたとき、後ろにいた方が前に出て復活しないように。
 *
 * **もう一度押して、また失敗したら出す。** 失敗がいったん途切れたら（`??` の束
 * ぜんぶが空になったら）覚えを捨て、次に出た失敗は新しいものとして出す。
 */
export function ErrorBanner({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  const present = error !== null && error !== undefined;
  // **失敗が途切れたら覚えを捨てる。** 空 → 有りの変わり目で作り直す
  // （前の描画の状態と比べる、React の「描画中に state を直す」形）。
  const [spell, setSpell] = useState({ showing: present, dismissed: [] as string[] });
  if (spell.showing !== present) {
    setSpell({ showing: present, dismissed: present ? [] : spell.dismissed });
  }
  const key = contentKey(error);
  if (!present || spell.dismissed.includes(key)) {
    return null;
  }
  const message =
    error instanceof ApiError || error instanceof UserFacingError
      ? error.message
      : "予期しないエラーが起きました。画面を再読み込みしてください。";
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onDismiss ? (
        <button
          type="button"
          onClick={() => {
            setSpell((current) => ({ ...current, dismissed: [...current.dismissed, key] }));
            onDismiss();
          }}
        >
          閉じる
        </button>
      ) : null}
    </div>
  );
}
