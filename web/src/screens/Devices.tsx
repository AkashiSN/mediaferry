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

/** 自動取り込みの見通し。**`watcher.py` の `CANDIDATES` と同じ条件を見る。**
 *
 * 画面の文言と確認ダイアログの両方がここを見る。片方だけで判定すると、
 * 同意の内容と実挙動がずれる（それが起きた）。
 *
 * **`pending` を `blocked` と混ぜない。** 初回の観測は必ず
 * `identity_confidence = low` だが、その観測で指紋を憶えるので、一覧を取り直すと
 * 同じ挿入のまま `high` になり次の tick で積まれる（`_identity_confidence`）。
 * 「いまは始まりません」と書くと、**数秒後に始まる経路を否定する**ことになる。
 */
export type Outlook =
  | { state: "starts"; reason: null }
  | { state: "pending"; reason: string }
  | { state: "blocked"; reason: string };

export function autoImportOutlook(volume: Volume, autoImport: string | null): Outlook {
  if (autoImport === null) {
    // **読めていない値を `trusted` と仮定しない。** 実設定が off でも
    // 「いまの中身を数秒後にコピー」と誤って同意を取ることになる。
    return { state: "blocked", reason: "設定をまだ読めていない" };
  }
  if (autoImport !== "trusted") {
    return { state: "blocked", reason: "AUTO_IMPORT が off な" };
  }
  if (volume.provisional) {
    return { state: "blocked", reason: "対象の中身がまだ見つかっていない" };
  }
  if (volume.identity_confidence !== "high") {
    return { state: "pending", reason: "このカードだと確かめられしだい" };
  }
  return { state: "starts", reason: null };
}

/** そのカードで自動取り込みがどうなるか（§12.1）。 */
export function autoImportState(volume: Volume, autoImport: string | null): string {
  const outlook = autoImportOutlook(volume, autoImport);
  // **承認すると、いま挿してあるこのカードの中身が対象になる。** watcher は
  // 毎 tick、現在 live な presence から候補を組み直すので、条件が揃っていれば
  // 承認の数秒後に取り込みが始まる。「次に挿したときから」と書くと、同意の
  // 対象を取り違えさせる。**逆に、条件が揃っていないのに断言もしない。**
  if (!volume.trusted) {
    switch (outlook.state) {
      case "starts":
        return "未承認です。承認すると、いま入っている中身も含めて、数秒後から自動で取り込みます。";
      case "pending":
        return `未承認です。承認すると、${outlook.reason}、いま入っている中身も含めて自動で取り込みます。`;
      case "blocked":
        return `未承認です。承認しても、${outlook.reason}ので、いまは自動取り込みは始まりません。`;
    }
  }
  switch (outlook.state) {
    case "starts":
      return "信頼済み。挿すと自動で取り込みます。";
    case "pending":
      return `信頼済み。${outlook.reason}自動で取り込みます。`;
    case "blocked":
      return `信頼済みですが、${outlook.reason}ので、いまは自動取り込みは始まりません。`;
  }
}

export function DevicesScreen() {
  const devices = useQuery<Devices>("/devices");
  const settings = useQuery<Settings>("/settings");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ confirmation: Confirmation; id: string } | null>(
    null,
  );

  // **未解決・失敗は `null` のまま持つ。** 既定値へ倒すと、同意の内容が実挙動と
  // ずれる（`watcher.py` は積まないのに「コピーされます」と書く）。
  const autoImport =
    settings.data === null
      ? null
      : (settings.data.settings.find((setting) => setting.key === "AUTO_IMPORT")?.value ?? null);

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
      <ErrorBanner
        error={error ?? devices.error ?? settings.error}
        onDismiss={() => setError(null)}
      />
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
              {/* **理由は常に出す**（§13）。対象外なら「なぜ外れたか」、一致なら
                  「なぜそのプロファイルに決まったか」で、どちらもプロファイルを
                  直す手がかりになる。黙って消えると原因が分からない。 */}
              <p role="note">
                {volume.profile_slug === null ? "対象外の理由" : "判定の理由"}:{" "}
                {volume.reason ?? "不明"}
              </p>
              {/* **「対象だが中身が無い」は対象外ではない**（§6 / Phase 0 の発見 B）。 */}
              {volume.provisional && (
                <p role="note">
                  {volume.profile_slug} の対象ですが、取り込む中身がまだありません。
                </p>
              )}
              {volume.profile_slug !== null && <p>{autoImportState(volume, autoImport)}</p>}
              <div className="actions">
                {!volume.trusted && volume.profile_slug !== null && (
                  <button
                    type="button"
                    // 設定を読めていない間は押させない（同意の内容を作れない）。
                    disabled={busy !== null || autoImport === null}
                    onClick={() =>
                      setConfirming({
                        confirmation: {
                          kind: "trust_volume",
                          label,
                          // **同意の内容は、いまの条件から作る**（断言しない）。
                          ...autoImportOutlook(volume, autoImport),
                        },
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
