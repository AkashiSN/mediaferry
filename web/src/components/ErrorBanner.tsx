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

/**
 * 失敗を 1 本の帯で出す。
 *
 * **「閉じる」は必ず効く。** 呼び出し側は自分の state と `useQuery` の失敗を
 * `??` で束ねて渡すが、`onDismiss` で消せるのは前者だけ。取り直しの失敗を
 * 渡されているときに帯が消えないと、押しても何も起きないボタンになる。
 * 閉じたものはここで覚え、**別の失敗が来たらまた出す**（同じ失敗が再び起きた
 * ときも、例外は作り直されるので別物として出る）。
 */
export function ErrorBanner({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  const [dismissed, setDismissed] = useState<unknown>(null);
  if (error === null || error === undefined || error === dismissed) {
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
            setDismissed(error);
            onDismiss();
          }}
        >
          閉じる
        </button>
      ) : null}
    </div>
  );
}
