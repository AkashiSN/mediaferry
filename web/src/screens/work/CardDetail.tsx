// カードの中身（§13）。判定結果と確度、信頼登録、スキャン、取り込み。
//
// **複数のボリュームを同時に扱う。** Osmo は内蔵ストレージと SD カードが同じ形で
// 見えるので、1 枚だけを前提にすると片方が操作できない。
//
// **信頼登録は「以後このカードを挿すだけで NAS へコピーする」という許可**なので、
// 確認を取り、そのうえで**信頼の限界**（同じ UUID の別のカードや復元したカードを
// 取り違えうること）を書く（§12.1）。

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useMutation, useQuery } from "../../api/hooks";
import { CardStanding } from "../../components/CardStanding";
import { ConfirmDialog, type Confirmation } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import type { CardView } from "../../hooks/homeSections";
import { useEventCount } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatBytes } from "../../utils/formatBytes";

/** カード 1 枚（`GET /devices` の 1 要素）。**判定関数がここにあるので、型もここに
 * 1 つだけ置く**（ホームの札も同じものを描く）。 */
export type Volume = {
  volume_instance_id: string;
  // API は空文字を返す（`None` にはならない）。ラベルの有無は `""` で見る。
  fs_label: string;
  // ボリュームの総容量。**同じカメラのカードが 2 枚挿さっていると、判定結果も
  // 確度も同じ行になる**ので、目の前のどれなのかは容量でも見分ける。
  size_bytes: number;
  profile_slug: string | null;
  identity_confidence: string | null;
  provisional: boolean;
  trusted: boolean;
  reason: string | null;
  // ホームの札（`CardStanding`）と同じ判断をするための欄。ホームの
  // `homeSections.ts` もこの 3 つを見る。
  pending_count: number;
  scanned_at: string | null;
  busy: boolean;
};

type Devices = { volumes: Volume[] };
type Setting = { key: string; value: string | null };
type Settings = { settings: Setting[] };
type Profile = { slug: string; name: string };
type Profiles = { profiles: Profile[] };

/** カードの表示名。**内部の UUID を画面に出さない**（§13）。
 *
 * API の `fs_label` は空文字であって `null` ではないので、`??` は素通りする
 * （`??` を使うと常にラベルが勝ち、フォールバックが一度も効かない）。ラベルが
 * 無いカードには人間向けの既定名を使う。複数枚が同時にラベル無しだと同じ名前に
 * なるので、そのときだけ連番で見分けられるようにする（UUID は使わない）。
 */
export function volumeLabel(
  volumes: readonly { volume_instance_id: string; fs_label: string }[],
  volume: { volume_instance_id: string; fs_label: string },
): string {
  if (volume.fs_label !== "") {
    return volume.fs_label;
  }
  const unnamed = volumes.filter((candidate) => candidate.fs_label === "");
  if (unnamed.length <= 1) {
    return "名前の無いカード";
  }
  const position = unnamed.findIndex(
    (candidate) => candidate.volume_instance_id === volume.volume_instance_id,
  );
  return `名前の無いカード ${position + 1}`;
}

/** カメラの種類の表示名。**slug をそのまま画面に出さない**（§13）。
 *
 * `/profiles` から引いた表示名を出し、**見つからない（未登録・まだ読めていない）
 * ときだけ** slug にフォールバックする。 */
export function profileDisplayName(
  slug: string | null,
  profiles: readonly Profile[],
): string {
  if (slug === null) {
    return "対象外";
  }
  return profiles.find((profile) => profile.slug === slug)?.name ?? slug;
}

/** 同定の確度を利用者向けの日本語にする（§8）。
 *
 * **実際に取りうる値は `high` と `low` の 2 つだけ**（`_identity_confidence`）。
 * それ以外（読めていない等）では何も出さない。 */
export function confidenceLabel(identityConfidence: string | null): string | null {
  switch (identityConfidence) {
    case "high":
      return "確かめられています";
    case "low":
      return "まだ確かめられていません";
    default:
      return null;
  }
}

/** 自動取り込みの見通し。**`watcher.py` の `CANDIDATES` と同じ条件を見る。**
 *
 * 画面の文言と確認ダイアログの両方がここを見る。片方だけで判定すると、
 * 同意の内容と実挙動がずれる（それが起きた）。
 *
 * **`pending` を `blocked` と混ぜない。** 初回の観測は必ず
 * `identity_confidence = low` だが、その観測で指紋を憶えるので、一覧を取り直すと
 * 同じ挿入のまま `high` になり次の tick で積まれる（`_identity_confidence`）。
 * 「いまは始まりません」と書くと、**数秒後に始まる経路を否定する**ことになる。
 *
 * **ただし `pending` は約束ではない。** `fs_uuid` が無い媒体や、同じ UUID の別
 * presence が併存している間は、何度観測しても `high` にならない。API は `low` の
 * 理由を返さないので画面は区別できない。**だから条件形で書く**（「確かめられた
 * 場合は」）。同意の対象は変わらないので、確認ダイアログはそのまま出す。
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
    // **内部の設定キーを理由に出さない**（§13）。この文字列は画面にも確認
    // ダイアログにもそのまま出る。`Settings.tsx` の項目名に合わせる。
    return { state: "blocked", reason: "「信頼したカードを自動で取り込む」が切ってある" };
  }
  if (volume.provisional) {
    return { state: "blocked", reason: "対象の中身がまだ見つかっていない" };
  }
  if (volume.identity_confidence !== "high") {
    return { state: "pending", reason: "このカードだと確かめられた場合" };
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
        return `未承認です。承認すると、${outlook.reason}に、いま入っている中身も含めて自動で取り込みます。`;
      case "blocked":
        return `未承認です。承認しても、${outlook.reason}ので、いまは自動取り込みは始まりません。`;
    }
  }
  switch (outlook.state) {
    case "starts":
      return "信頼済み。挿すと自動で取り込みます。";
    case "pending":
      return `信頼済み。${outlook.reason}に自動で取り込みます。`;
    case "blocked":
      return `信頼済みですが、${outlook.reason}ので、いまは自動取り込みは始まりません。`;
  }
}

export function CardDetailScreen() {
  const devices = useQuery<Devices>("/devices");
  const settings = useQuery<Settings>("/settings");
  const profiles = useQuery<Profiles>("/profiles");
  // **「抜いていいか」は断定文なので、更新され続けなければならない**（§13）。
  // 出所は `/devices` の `busy` で、押した取り込みが終わったことは進捗の知らせ
  // でしか届かない。**数だけを見る購読を使う** —— この画面はイベントの中身を
  // 読まないので、`useEvents` を呼ぶと読まない 200 件の控えを持つことになる。
  useReloadOnEvents(useEventCount(), devices.reload);
  const card = useMutation();
  const [confirming, setConfirming] = useState<{ confirmation: Confirmation; id: string } | null>(
    null,
  );
  const navigate = useNavigate();

  // **未解決・失敗は `null` のまま持つ。** 既定値へ倒すと、同意の内容が実挙動と
  // ずれる（`watcher.py` は積まないのに「コピーされます」と書く）。
  const autoImport =
    settings.data === null
      ? null
      : (settings.data.settings.find((setting) => setting.key === "AUTO_IMPORT")?.value ?? null);

  async function act(volumeId: string, action: "trust" | "scan" | "import") {
    await card.run(async () => {
      await request(`/volumes/${volumeId}/${action}`, { method: "POST" });
      devices.reload();
    });
    setConfirming(null);
  }

  const volumes = devices.data?.volumes ?? [];
  const profileList = profiles.data?.profiles ?? [];

  return (
    <section aria-label="カードの中身" className="wrap">
      <div className="row">
        <button type="button" className="btn sm" onClick={() => navigate("/")}>
          <Icon name="back" size={16} />
          ホームへ
        </button>
      </div>
      <h1 className="page title-lg">カードの中身</h1>

      <ErrorBanner
        error={card.error ?? devices.error ?? settings.error ?? profiles.error}
        onDismiss={card.clear}
      />

      {/* **内部の設定キーを画面に出さない**（§13）。`Settings.tsx` の項目名と
          同じ言葉で書く —— 同じものが画面ごとに違う名前で出ると、どれとどれが
          同じ設定なのか読む側には分からない。

          **行き先はボタンで置く。** 文の途中のリンクは行の高さしか無く、
          §13「押せる領域は 44px 以上」を満たせない。 */}
      {autoImport === "off" && (
        <>
          <p role="note">
            「信頼したカードを自動で取り込む」が切ってあります。信頼済みのカードを
            挿しても取り込みは始まりません。
          </p>
          <div className="acts">
            <Link to="/settings" className="btn sm">
              設定を開く
            </Link>
          </div>
        </>
      )}

      {volumes.length === 0 ? (
        <div className="card pad empty">
          <h2 style={{ fontSize: 17, fontWeight: 650 }}>接続中のカードはありません</h2>
        </div>
      ) : (
        volumes.map((volume) => {
          const label = volumeLabel(volumes, volume);
          const profileName = profileDisplayName(volume.profile_slug, profileList);
          const confidence = confidenceLabel(volume.identity_confidence);
          const actionable = volume.profile_slug !== null;
          // `CardStanding` はホームと同じ形（`CardView`）を受け取る。ここは
          // `Volume` しか持っていないので、既に引いてある表示名で作り直す。
          const cardView: CardView = {
            volume_instance_id: volume.volume_instance_id,
            label,
            profile_name: profileName,
            size_bytes: volume.size_bytes,
            profile_slug: volume.profile_slug,
            trusted: volume.trusted,
            provisional: volume.provisional,
            reason: volume.reason ?? "",
            pending_count: volume.pending_count,
            scanned_at: volume.scanned_at,
            busy: volume.busy,
          };
          return (
            <section key={volume.volume_instance_id} className="card pad">
              <div className="rowtop">
                <div className="iconbox on">
                  <Icon name="card" />
                </div>
                <div className="grow">
                  <h2 style={{ fontSize: 16, fontWeight: 650 }}>{label}</h2>
                  <p className="small" style={{ marginTop: 4 }}>
                    {formatBytes(volume.size_bytes)}
                  </p>
                  <p className="small" style={{ marginTop: 4 }}>
                    判定: {profileName}
                    {confidence !== null ? `（確度：${confidence}）` : ""}
                  </p>
                  {/* **理由は常に出す**（§13）。対象外なら「なぜ外れたか」、一致なら
                      「なぜそのプロファイルに決まったか」で、どちらもプロファイルを
                      直す手がかりになる。黙って消えると原因が分からない。 */}
                  <p role="note" className="small" style={{ marginTop: 4 }}>
                    {volume.profile_slug === null ? "対象外の理由" : "判定の理由"}:{" "}
                    {volume.reason ?? "不明"}
                  </p>
                  {/* **「対象だが中身が無い」は対象外ではない**（§6 / Phase 0 の発見 B）。 */}
                  {volume.provisional && (
                    <p role="note" className="small" style={{ marginTop: 4 }}>
                      {profileName} の対象ですが、取り込む中身がまだありません。
                    </p>
                  )}
                  {/* **抜いていいかは、押さずに読める**（§3）。文言は `CardStanding`
                      の 1 か所だけが持つ。 */}
                  <CardStanding card={cardView} />
                </div>
              </div>
              {actionable && (
                <p
                  className="muted"
                  style={{
                    marginTop: 14,
                    paddingTop: 14,
                    borderTop: "1px solid var(--line-2)",
                  }}
                >
                  {autoImportState(volume, autoImport)}
                </p>
              )}
              <div className="acts" style={{ marginTop: 14 }}>
                {!volume.trusted && actionable && (
                  <button
                    type="button"
                    className="btn outline"
                    // 設定を読めていない間は押させない（同意の内容を作れない）。
                    disabled={card.busy || autoImport === null}
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
                {actionable && (
                  <>
                    <button
                      type="button"
                      className="btn sm"
                      disabled={card.busy}
                      onClick={() => void act(volume.volume_instance_id, "scan")}
                    >
                      {label} をスキャン
                    </button>
                    <button
                      type="button"
                      className="btn primary"
                      disabled={card.busy}
                      onClick={() => void act(volume.volume_instance_id, "import")}
                    >
                      {label} を取り込む
                    </button>
                  </>
                )}
              </div>
            </section>
          );
        })
      )}

      {confirming && (
        <ConfirmDialog
          confirmation={confirming.confirmation}
          busy={card.busy}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void act(confirming.id, "trust")}
        />
      )}
    </section>
  );
}
