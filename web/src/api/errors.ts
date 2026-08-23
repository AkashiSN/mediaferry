// API の失敗を、**利用者に見せる日本語**へ変える（§13）。
//
// 画面は `detail` をそのまま出さない。内部の文言や相手由来の値が利用者へ流れる
// うえ、「次に何をすべきか」が書かれていない。code ごとに文面を持ち、知らない
// code のときだけ detail を添える。

export type ApiErrorBody = {
  error: { code: string; detail: string; meta: Record<string, unknown> };
};

const MESSAGES: Record<string, string> = {
  not_authenticated: "ログインが要ります。",
  bad_request: "この操作は受け付けられません。入力を確かめてください。",
  csrf_failed: "画面が古くなっています。再読み込みしてから操作してください。",
  cross_site_request: "この操作は受け付けられません。画面を開き直してください。",
  untrusted_host: "この名前ではアクセスできません。設定の TRUSTED_HOSTS を確認してください。",
  too_many_attempts: "試行が多すぎます。しばらく待ってからやり直してください。",
  too_many_streams: "進捗の接続が多すぎます。開いているタブを閉じてください。",
  not_found: "見つかりませんでした。画面を再読み込みしてください。",
  conflict: "いまの状態ではこの操作はできません。",
  job_already_finished: "その作業はもう終わっています。",
  not_retryable: "失敗した状態ではないので、再試行できません。",
  not_requeueable: "この記録は送り直せません。",
  not_awaiting_approval: "確認を待っている記録ではありません。画面を再読み込みしてください。",
  already_invalidated: "この記録は無効になっています。",
  approval_already_queued: "この承認はもう実行待ちです。",
  setting_locked: "この設定は環境変数で固定されています。TrueNAS のアプリ設定で変更してください。",
  same_library_undecided: "同じライブラリを指しているかどうかを選んでください。",
  destination_unreachable: "送り先に接続できません。URL と API キーを確認してください。",
  secret_key_missing: "MEDIAFERRY_SECRET_KEY が未設定です。送り先を使うには設定が要ります。",
  invalid_endpoint: "URL の形式が正しくありません。",
  unknown_field: "知らない項目が含まれています。",
  missing_field: "必要な項目が足りません。",
  unknown_action: "知らない操作です。",
  validation_failed: "入力の形式が正しくありません。",
  thumbnail_failed: "サムネイルを作れませんでした。元のファイルが消えている可能性があります。",
  internal: "内部エラーが起きました。設定 › 詳しい情報 › 作業の履歴を確認してください。",
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: string,
    readonly meta: Record<string, unknown> = {},
  ) {
    // **`message` は画面に出す日本語。** `Error` が自分の own property として
    // 持つので、getter で上書きしようとしても効かない（構築時に決める）。
    super(displayMessage(code, detail));
  }
}

// **どこが悪いかを落とさない code。** 定型文だけでは直せない失敗がある
// （カメラの種類の定義は 1 枚の YAML なので、「形式が正しくありません」では
// どの項目を直せばよいか分からない）。`detail` は API がこちらで書いた日本語
// だけを入れる契約なので、添えても相手由来の値は出ない（§13）。
//
// `bad_request` は 400 の受け皿で、断る理由がそのつど違う（「slug は作成後に
// 変更できない」「知らない status」…）。文面だけでは直しようがないので添える。
const WITH_DETAIL = new Set(["validation_failed", "bad_request"]);

/** 画面に出す日本語。**知らない code のときだけ** detail を添える。 */
export function displayMessage(code: string, detail: string): string {
  const known = MESSAGES[code];
  if (known !== undefined) {
    return detail && WITH_DETAIL.has(code) ? `${known}（${detail}）` : known;
  }
  return detail ? `予期しないエラー（詳細: ${detail}）` : "予期しないエラー";
}

export function toApiError(status: number, body: unknown): ApiError {
  const envelope = body as Partial<ApiErrorBody>;
  const error = envelope?.error;
  if (error && typeof error.code === "string") {
    return new ApiError(status, error.code, error.detail ?? "", error.meta ?? {});
  }
  return new ApiError(status, "internal", "");
}
