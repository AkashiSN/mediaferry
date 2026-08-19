// ログイン（認証が有効なときだけ出る。§12）。

import { useState } from "react";

import { request } from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";

export function LoginScreen({ onSignedIn }: { onSignedIn: () => void }) {
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await request("/auth/login", { method: "POST", body: { password: String(form.get("password") ?? "") } });
      onSignedIn();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="ログイン">
      <h1>ログイン</h1>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <form onSubmit={(event) => void submit(event)}>
        <label>
          パスワード
          <input name="password" type="password" required autoFocus />
        </label>
        <button type="submit" disabled={busy}>
          ログイン
        </button>
      </form>
    </section>
  );
}
