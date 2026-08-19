// デバイス（§13）。判定結果と確度、信頼登録、スキャン、取り込み。
//
// **複数のボリュームを同時に扱う。** Osmo は内蔵ストレージと SD カードが同じ形で
// 見えるので、1 枚だけを前提にすると片方が操作できない。
//
// **信頼登録は「以後このカードを挿すだけで NAS へコピーする」という許可**なので、
// 確認を取り、そのうえで**信頼の限界**（同じ UUID の別のカードや復元したカードを
// 取り違えうること）を書く（§12.1）。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog, type Confirmation } from "../components/ConfirmDialog";
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
type Setting = { key: string; value: string | null };
type Settings = { settings: Setting[] };

/** そのカードで自動取り込みがどうなるか。**3 つの状態を区別して書く**（§12.1）。 */
export function autoImportState(volume: Volume): string {
  if (!volume.trusted) {
    // `_identity_confidence` は憶えた指紋が無ければ必ず low を返すので、
    // **効くのは 2 度目以降の挿入から**。「承認すれば今すぐ」と読ませない。
    return "未承認です。承認すると、次にこのカードを挿したときから自動で取り込みます。";
  }
  if (volume.identity_confidence !== "high") {
    return "信頼済みですが、確度が低いため自動取り込みはしません。";
  }
  return "信頼済み。挿すと自動で取り込みます。";
}

export function DevicesScreen() {
  const devices = useQuery<Devices>("/devices");
  const settings = useQuery<Settings>("/settings");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ confirmation: Confirmation; id: string } | null>(
    null,
  );

  const autoImport =
    (settings.data?.settings ?? []).find((setting) => setting.key === "AUTO_IMPORT")?.value ??
    "trusted";

  async function act(volumeId: string, action: "trust" | "scan" | "import" | "close") {
    setBusy(`${volumeId}:${action}`);
    setError(null);
    try {
      await request(`/volumes/${volumeId}/${action}`, { method: "POST" });
      devices.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setConfirming(null);
      setBusy(null);
    }
  }

  return (
    <section aria-label="デバイス">
      <h1>デバイス</h1>
      <ErrorBanner error={error ?? devices.error} onDismiss={() => setError(null)} />
      {autoImport === "off" && (
        <p role="note">
          自動取り込みは無効です（AUTO_IMPORT = off）。信頼済みのカードを挿しても
          取り込みは始まりません。<Link to="/settings">設定</Link>で変えられます。
        </p>
      )}
      {(devices.data?.volumes ?? []).length === 0 && <p>接続中のカードはありません。</p>}
      <ul>
        {(devices.data?.volumes ?? []).map((volume) => {
          const label = volume.fs_label ?? volume.volume_instance_id;
          return (
            <li key={volume.volume_instance_id}>
              <h2>{label}</h2>
              <p>
                判定: {volume.profile_slug ?? "対象外"}
                {volume.identity_confidence ? `（確度 ${volume.identity_confidence}）` : ""}
              </p>
              {/* **対象外も理由付きで出す**（§13）。黙って消えると原因が分からない。 */}
              {volume.profile_slug === null && (
                <p role="note">対象外の理由: {volume.reason ?? "不明"}</p>
              )}
              {/* **「対象だが中身が無い」は対象外ではない**（§6 / Phase 0 の発見 B）。 */}
              {volume.provisional && (
                <p role="note">
                  {volume.profile_slug} の対象ですが、取り込む中身がまだありません。
                </p>
              )}
              {volume.profile_slug !== null && autoImport !== "off" && (
                <p>{autoImportState(volume)}</p>
              )}
              <div className="actions">
                {!volume.trusted && volume.profile_slug !== null && (
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() =>
                      setConfirming({
                        confirmation: { kind: "trust_volume", label },
                        id: volume.volume_instance_id,
                      })
                    }
                  >
                    {label} を信頼する
                  </button>
                )}
                {volume.profile_slug !== null && (
                  <>
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void act(volume.volume_instance_id, "scan")}
                    >
                      {label} をスキャン
                    </button>
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void act(volume.volume_instance_id, "import")}
                    >
                      {label} を取り込む
                    </button>
                  </>
                )}
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void act(volume.volume_instance_id, "close")}
                >
                  {label} を取り外す
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      {confirming && (
        <ConfirmDialog
          confirmation={confirming.confirmation}
          busy={busy !== null}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void act(confirming.id, "trust")}
        />
      )}
    </section>
  );
}
