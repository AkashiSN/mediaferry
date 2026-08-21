// バイト数を人が読める形にする（§13「画面に出す言葉」）。
//
// **確認ダイアログだけのものではない。** 送る・つなぐ・写真・進捗のどれもが同じ
// 書式で合計を出すので、共有の置き場に 1 つだけ持つ。

export function formatBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // 端数が無ければ小数点を出さない（「3.0 GiB」より「3 GiB」の方が読みやすい）。
  const shown =
    value >= 10 || unit === 0 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, "");
  return `${shown} ${units[unit]}`;
}
