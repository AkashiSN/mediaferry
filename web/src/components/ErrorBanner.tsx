// 失敗の表示（§13）。**例外の文字列をそのまま出さない。**

import { ApiError } from "../api/errors";

export function ErrorBanner({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  if (error === null || error === undefined) {
    return null;
  }
  const message =
    error instanceof ApiError
      ? error.message
      : "予期しないエラーが起きました。画面を再読み込みしてください。";
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onDismiss ? (
        <button type="button" onClick={onDismiss}>
          閉じる
        </button>
      ) : null}
    </div>
  );
}
