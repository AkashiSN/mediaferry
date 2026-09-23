// 撮影地のタイムゾーンの付け替え（写真の選択バーから開く）。
//
// **撮った瞬間は変えない。** カメラの時計を日本時間のまま旅行先で撮ると、瞬間は
// 正しいが日本の時刻で表示される。直すのは「どの地の時刻で見せるか」だけ。

import { useId, useRef, useState } from "react";

import { useQuery } from "../api/hooks";
import { ErrorBanner } from "./ErrorBanner";
import { useDialogFocus } from "./useDialogFocus";

export function ZoneDialog({
  count,
  onApply,
  onClose,
  busy,
}: {
  /** 付け替えるファイルの数（選んだタイルの数）。 */
  count: number;
  /** `null` は上書きを外す（カメラの時計のゾーンへ戻す）。 */
  onApply: (zone: string | null) => void;
  onClose: () => void;
  busy: boolean;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  useDialogFocus(dialog, onClose, busy);
  const zones = useQuery<{ timezones: string[] }>("/timezones");
  const [value, setValue] = useState("");
  const inputId = useId();
  const listId = useId();
  // **候補に無い名前では押せない。** API も同じ集合で検めるので、押せたのに
  // 400 で返る、を作らない。
  const known = zones.data?.timezones.includes(value) ?? false;

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog"
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label="撮影地のタイムゾーンを付け替える"
        aria-busy={busy}
      >
        <h2>撮影地のタイムゾーンを付け替える</h2>
        <p>
          選んだ {count} 件の表示する時刻を、撮影地のものに直します。撮った瞬間はそのままです。
          カメラの時計を日本時間のまま旅行先で撮ったときに使います。
        </p>
        <ErrorBanner error={zones.error} />
        <label htmlFor={inputId}>撮影地のタイムゾーン</label>
        <input
          id={inputId}
          className="field"
          list={listId}
          value={value}
          placeholder="Asia/Ho_Chi_Minh"
          autoComplete="off"
          disabled={busy}
          onChange={(event) => setValue(event.target.value)}
        />
        <datalist id={listId}>
          {(zones.data?.timezones ?? []).map((zone) => (
            <option key={zone} value={zone} />
          ))}
        </datalist>
        {/* **押した後に何が起きているかを言う。** ボタンが押せなくなるだけだと、
            読み上げでは進んでいるのか固まったのか分からない。 */}
        <p role="status" className="muted">
          {busy ? "付け替えています…" : ""}
        </p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button type="button" onClick={() => onApply(null)} disabled={busy}>
            上書きを外す
          </button>
          <button type="button" onClick={() => onApply(value)} disabled={busy || !known}>
            付け替える
          </button>
        </div>
      </div>
    </div>
  );
}
