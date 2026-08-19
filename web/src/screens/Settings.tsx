// 設定とプロファイル（§12 / §13）。**env 由来は錠前付きの読み取り専用。**

import { useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";

type Setting = {
  key: string;
  value: string | null;
  source: string;
  locked: boolean;
  tier: string;
  writable: boolean;
};

type Settings = { settings: Setting[]; warnings: { code: string; message: string }[] };
type Profiles = { profiles: { slug: string; name: string; revision: number }[] };

export function SettingsScreen() {
  const settings = useQuery<Settings>("/settings");
  const profiles = useQuery<Profiles>("/profiles");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function save(key: string, value: string) {
    setBusy(true);
    setError(null);
    try {
      await request("/settings", { method: "PUT", body: { key, value } });
      settings.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="設定">
      <h1>設定</h1>
      <ErrorBanner error={error ?? settings.error} onDismiss={() => setError(null)} />
      <table>
        <thead>
          <tr>
            <th>キー</th>
            <th>値</th>
            <th>出所</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(settings.data?.settings ?? []).map((setting) => (
            <tr key={setting.key}>
              <td>{setting.key}</td>
              <td>
                {setting.writable ? (
                  <input
                    defaultValue={setting.value ?? ""}
                    aria-label={`${setting.key} の値`}
                    onBlur={(event) => void save(setting.key, event.currentTarget.value)}
                    disabled={busy}
                  />
                ) : (
                  <span>{setting.value ?? "（未設定）"}</span>
                )}
              </td>
              <td>
                {setting.source}
                {/* env 由来は TrueNAS のアプリ設定で固定されている（§12）。 */}
                {setting.locked && <span title="環境変数で固定されています">🔒</span>}
              </td>
              <td>{setting.tier}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>デバイスプロファイル</h2>
      <p>編集は次のフェーズで入ります。ここでは判定に使う定義を確認できます。</p>
      <ul>
        {(profiles.data?.profiles ?? []).map((profile) => (
          <li key={profile.slug}>
            {profile.name}（{profile.slug}） 版 {profile.revision}
          </li>
        ))}
      </ul>
    </section>
  );
}
