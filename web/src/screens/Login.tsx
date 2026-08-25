// ログイン（認証が有効なときだけ出る。§12）。
//
// **枠（`Layout`）の外に出る唯一の画面。** ナビは認証が済むまで出せないので、
// 中央にカードを 1 枚だけ置く（骨格は `styles.css` の `.signin`）。

import { useMutation } from "../api/hooks";
import { request } from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";

export function LoginScreen({ onSignedIn }: { onSignedIn: () => void }) {
  const signIn = useMutation();

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await signIn.run(async () => {
      await request("/auth/login", {
        method: "POST",
        body: { password: String(form.get("password") ?? "") },
      });
      onSignedIn();
    });
  }

  return (
    <section className="signin" aria-label="ログイン">
      <div className="card pad signin-card">
        <div className="signin-brand">mediaferry</div>
        <h1 className="page">ログイン</h1>
        <p className="muted">続けるにはパスワードを入れてください。</p>
        <ErrorBanner error={signIn.error} onDismiss={signIn.clear} />
        <form onSubmit={(event) => void submit(event)}>
          <label className="formrow">
            パスワード
            <input className="field" name="password" type="password" required autoFocus />
          </label>
          <button type="submit" className="btn primary" disabled={signIn.busy}>
            ログイン
          </button>
        </form>
      </div>
    </section>
  );
}
