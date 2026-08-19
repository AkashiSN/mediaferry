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
type Volumes = { volumes: { volume_instance_id: string; fs_label: string | null }[] };

export function SettingsScreen() {
  const settings = useQuery<Settings>("/settings");
  const profiles = useQuery<Profiles>("/profiles");
  const volumes = useQuery<Volumes>("/devices");
  const [tried, setTried] = useState<string | null>(null);
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
      <p>編集は次のフェーズで入ります。ここでは定義の確認と、判定の試行ができます。</p>
      {tried && <p role="status">{tried}</p>}
      <ul>
        {(profiles.data?.profiles ?? []).map((profile) => (
          <li key={profile.slug}>
            {profile.name}（{profile.slug}） 版 {profile.revision}
            {(volumes.data?.volumes ?? []).map((volume) => (
              <button
                key={volume.volume_instance_id}
                type="button"
                disabled={busy}
                onClick={() =>
                  void request<{ matched: boolean; reason: string | null }>(
                    `/profiles/${profile.slug}/test?volume_instance_id=${volume.volume_instance_id}`,
                    { method: "POST" },
                  )
                    .then((result) =>
                      setTried(
                        `${profile.slug} × ${volume.fs_label ?? volume.volume_instance_id}: ` +
                          (result.matched ? "一致" : `一致しない（${result.reason ?? "理由不明"}）`),
                      ),
                    )
                    .catch(setError)
                }
              >
                {volume.fs_label ?? volume.volume_instance_id} で試す
              </button>
            ))}
          </li>
        ))}
      </ul>
    </section>
  );
}
