// ログイン（認証が有効なときだけ出る。§12）。

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
    <section aria-label="ログイン">
      <h1>ログイン</h1>
      <ErrorBanner error={signIn.error} onDismiss={signIn.clear} />
      <form onSubmit={(event) => void submit(event)}>
        <label>
          パスワード
          <input name="password" type="password" required autoFocus />
        </label>
        <button type="submit" disabled={signIn.busy}>
          ログイン
        </button>
      </form>
    </section>
  );
}
