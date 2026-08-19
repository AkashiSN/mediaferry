// デバイス（§13）。判定結果と確度、信頼登録、スキャン、取り込み。

import { useState } from "react";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";

type Volume = {
  volume_instance_id: string;
  fs_label: string | null;
  profile_slug: string | null;
  identity_confidence: string | null;
  provisional: boolean;
  trusted: boolean;
  reason: string | null;
};

type Devices = { volumes: Volume[] };

export function DevicesScreen() {
  const devices = useQuery<Devices>("/devices");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function act(volumeId: string, action: "trust" | "scan" | "import" | "close") {
    setBusy(`${volumeId}:${action}`);
    setError(null);
    try {
      await request(`/volumes/${volumeId}/${action}`, { method: "POST" });
      devices.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-label="デバイス">
      <h1>デバイス</h1>
      <ErrorBanner error={error ?? devices.error} onDismiss={() => setError(null)} />
      {(devices.data?.volumes ?? []).length === 0 && <p>接続中のカードはありません。</p>}
      <ul>
        {(devices.data?.volumes ?? []).map((volume) => (
          <li key={volume.volume_instance_id}>
            <h2>{volume.fs_label ?? volume.volume_instance_id}</h2>
            <p>
              判定: {volume.profile_slug ?? "対象外"}
              {volume.identity_confidence ? `（確度 ${volume.identity_confidence}）` : ""}
            </p>
            {/* **対象外も理由付きで出す**（§13）。黙って消えると原因が分からない。 */}
            {volume.profile_slug === null && (
              <p role="note">対象外の理由: {volume.reason ?? "不明"}</p>
            )}
            <div className="actions">
              {!volume.trusted && volume.profile_slug !== null && (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void act(volume.volume_instance_id, "trust")}
                >
                  このカードを信頼する
                </button>
              )}
              {volume.profile_slug !== null && (
                <>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void act(volume.volume_instance_id, "scan")}
                  >
                    スキャン
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void act(volume.volume_instance_id, "import")}
                  >
                    取り込む
                  </button>
                </>
              )}
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void act(volume.volume_instance_id, "close")}
              >
                取り外す
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
