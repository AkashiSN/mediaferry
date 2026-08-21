// 日時を人が読める形にする（§13「画面に出す言葉」）。
//
// **文字列のまま切り出す。** `Date` を経由して組み立て直すと、見ている人のブラウザの
// タイムゾーンで再解釈されてしまう —— 撮影地の壁時計（`captured_at` が持つ解決済みの
// オフセット）や観測時刻が、見ている人の場所の時刻にすり替わる。

/** `YYYY-MM-DD` の日付部分。読めなければ `null`。 */
function dateOf(iso: string): string | null {
  const day = iso.slice(0, 10);
  return day.length === 10 ? day : null;
}

/** 日付だけを「○年○月○日」にする。 */
export function formatDate(iso: string): string {
  const day = dateOf(iso);
  if (day === null) {
    return "日付が不明";
  }
  const [year, month, date] = day.split("-");
  return `${year}年${Number(month)}月${Number(date)}日`;
}

/** 日付と時刻（分まで）を「○年○月○日 HH:MM」にする。 */
export function formatDateTime(iso: string): string {
  const day = dateOf(iso);
  const time = iso.slice(11, 16);
  if (day === null || time.length !== 5) {
    return "日時が不明";
  }
  return `${formatDate(iso)} ${time}`;
}

/**
 * システム時刻（`mediaferry.clock.now_iso` が作る UTC の ISO 文字列）を人が読める形にする。
 *
 * `captured_at` と違い、システム時刻は常に UTC で保存される。上の `formatDateTime` と
 * 同じく文字列を切り出すだけなので、そのまま出すと見ている人の現地時刻に見えてしまう。
 * **UTC だと分かる印を添える**（変換はしない —— ブラウザのタイムゾーンに依存させない
 * という上の設計は崩さない）。
 */
export function formatSystemDateTime(iso: string): string {
  const formatted = formatDateTime(iso);
  return formatted === "日時が不明" ? formatted : `${formatted}（UTC）`;
}
