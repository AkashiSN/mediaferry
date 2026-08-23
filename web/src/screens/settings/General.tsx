// 詳しい設定（§12 / §13）。**env 由来は錠前付きの読み取り専用。**
//
// 優先順位は 環境変数 > DB（この画面） > 既定値。環境変数で決まっている項目は
// TrueNAS のアプリ設定が正本なので、ここでは変えられない。
//
// 出所（`env` / `db` / `default`）と反映のタイミング（`tier`）は内部の言葉なので、
// **日本語に写してから出す**（§13）。キーそのものは TrueNAS のアプリ設定に並ぶ名前
// と同じなので、そのまま出す。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";

type Setting = {
  key: string;
  value: string | null;
  source: string;
  locked: boolean;
  tier: string;
  writable: boolean;
};

type Settings = { settings: Setting[]; warnings: { code: string; message: string }[] };

/** その値がどこから来たか。 */
export function sourceLabel(source: string): string {
  switch (source) {
    case "env":
      return "アプリ設定で固定";
    case "db":
      return "この画面で設定";
    case "default":
      return "既定のまま";
    default:
      return "出所が不明";
  }
}

/** 変えた値がいつ効くか（`Tier`）。 */
export function tierLabel(tier: string): string {
  switch (tier) {
    case "runtime":
      return "すぐに効きます";
    case "restart":
      return "次にアプリを起動したときから効きます";
    case "bootstrap":
      return "アプリ設定でだけ変えられます";
    default:
      return "いつ効くかが不明";
  }
}

export function GeneralScreen() {
  const settings = useQuery<Settings>("/settings");
  const [error, setError] = useState<unknown>(null);
  // 打ちかけの値。**保存できたら捨て、サーバが持っている値に戻す**（正規化された
  // 値や、拒否されて変わらなかった値と画面が食い違わないように）。保存に失敗した
  // ときは残す（打った文字を勝手に消さない）。
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // いま保存している項目。**画面ぜんぶを止めない。** 保存は欄から離れたときに
  // 走るので、全体を無効にすると Tab で移った先の欄がその瞬間に無効化され、
  // 焦点が外れて打った文字が落ちる。
  const [saving, setSaving] = useState<string | null>(null);

  function forget(key: string) {
    setDrafts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  /** 保存した値のままなら捨てる。**打ち足した分は残す**（打った文字を消さない）。 */
  function forgetIfUnchanged(key: string, saved: string) {
    setDrafts((current) => {
      if (current[key] !== saved) {
        return current;
      }
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function save(setting: Setting, value: string) {
    // **変わっていない値は送らない。** 送ると DB に行ができ、その項目の出所が
    // 「既定のまま」から「この画面で設定」に変わる（欄を通り過ぎただけで出所が
    // 動いて見える）。
    if (value === (setting.value ?? "")) {
      forget(setting.key);
      return;
    }
    setSaving(setting.key);
    setError(null);
    try {
      await request("/settings", { method: "PUT", body: { key: setting.key, value } });
      forgetIfUnchanged(setting.key, value);
      settings.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setSaving(null);
    }
  }

  return (
    <section aria-label="詳しい設定" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <h1 className="page title-lg">詳しい設定</h1>

      <ErrorBanner error={error ?? settings.error} onDismiss={() => setError(null)} />

      <p className="muted">
        ふだんは変えなくて大丈夫です。TrueNAS のアプリ設定で決めてある項目は、ここでは
        変えられません（アプリ設定を直してください）。
      </p>

      <section className="card pad">
        <ul style={{ display: "flex", flexDirection: "column", gap: 16, listStyle: "none", padding: 0 }}>
          {(settings.data?.settings ?? []).map((setting) => (
            <li key={setting.key} className="row">
              <div className="grow">
                {/* キー名は区切りが無いので、欄が狭いときは途中で折り返す。 */}
                <div className="ident" style={{ fontSize: "13.5px", fontWeight: 600 }}>
                  {setting.key}
                  {/* env 由来は TrueNAS のアプリ設定で固定されている（§12）。 */}
                  {setting.locked && (
                    <span
                      role="img"
                      aria-label="固定されています"
                      title="TrueNAS のアプリ設定で固定されています"
                      style={{ display: "inline-flex", verticalAlign: "middle", marginLeft: 6 }}
                    >
                      <Icon name="lock" size={14} />
                    </span>
                  )}
                </div>
                <div className="small">
                  {sourceLabel(setting.source)} ・ {tierLabel(setting.tier)}
                  {saving === setting.key && " ・ 保存しています…"}
                </div>
              </div>
              <input
                className="field"
                style={{ flex: "1 1 200px" }}
                aria-label={`${setting.key} の値`}
                value={drafts[setting.key] ?? setting.value ?? ""}
                placeholder="（未設定）"
                // **変えられない項目は押しても入らない。** 値は出すが、書けない。
                // **保存が飛んでいる間も、その欄だけ止める**（画面ぜんぶは止めない）。
                // 止めないと、飛んでいる間に打ち直した文字が、応答が返った瞬間に
                // 打ちかけの値ごと捨てられて消える。
                disabled={!setting.writable || saving === setting.key}
                onChange={(event) => {
                  const value = event.currentTarget.value;
                  setDrafts((current) => ({ ...current, [setting.key]: value }));
                }}
                onBlur={(event) => void save(setting, event.currentTarget.value)}
              />
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}
