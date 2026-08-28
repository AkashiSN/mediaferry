// 日時を人が読める形にする（§13「画面に出す言葉」）。
//
// **壁時計は文字列のまま切り出す。** `Date` を経由して組み立て直すと、見ている人の
// ブラウザのタイムゾーンで再解釈されてしまう —— 撮影地の壁時計（`captured_at` が持つ
// 解決済みのオフセット）が、見ている人の場所の時刻にすり替わる。
//
// **直すのはシステム時刻だけ。** そちらは常に UTC で保存されるので、設定した
// タイムゾーン（`DEFAULT_TIMEZONE`）へ直して出す。撮影日時は撮った土地の時刻の
// ままにする（利用者の裁定、2026-08-28。直すと「現地で何時だったか」が読めなくなる）。
//
// **どちらにも印を添える。** 直した数字も直さない数字も、印が無ければどの時計の
// ものか画面から決められない。

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

/** 日付と時刻（分まで）を「○年○月○日 HH:MM」にする。**印は付けない。** */
export function formatDateTime(iso: string): string {
  const day = dateOf(iso);
  const time = iso.slice(11, 16);
  if (day === null || time.length !== 5) {
    return "日時が不明";
  }
  return `${formatDate(iso)} ${time}`;
}

const UNKNOWN = "日時が不明";

/**
 * ゾーンの短い名前（`Asia/Tokyo` → `JST`、`Europe/Berlin` → `GMT+2`）。
 *
 * **その瞬間で引く。** 夏時間のあるゾーンは時期で名前が変わる。知らないゾーン名は
 * `Intl` が投げるので、そのときは `null`（呼び出し側が UTC のまま出す）。
 */
function zoneLabel(zone: string, at: Date): string | null {
  try {
    const parts = new Intl.DateTimeFormat("ja-JP", {
      timeZone: zone,
      timeZoneName: "short",
    }).formatToParts(at);
    return parts.find((part) => part.type === "timeZoneName")?.value ?? null;
  } catch {
    return null;
  }
}

/** 値が持つオフセットから印を作る（`+09:00` → `GMT+9`、`Z` → `UTC`）。無ければ `null`。 */
function offsetLabel(iso: string): string | null {
  if (/[Zz]$/.test(iso)) {
    return "UTC";
  }
  const found = /([+-])(\d{2}):(\d{2})$/.exec(iso);
  if (found === null) {
    return null;
  }
  const [, sign, hours, minutes] = found;
  if (Number(hours) === 0 && Number(minutes) === 0) {
    return "UTC";
  }
  const tail = Number(minutes) === 0 ? "" : `:${minutes}`;
  return `GMT${sign}${Number(hours)}${tail}`;
}

/** ゾーンの中の壁時計を「○年○月○日 HH:MM」にする。ゾーンが読めなければ `null`。 */
function wallClockIn(zone: string, at: Date): string | null {
  try {
    const parts = new Intl.DateTimeFormat("ja-JP", {
      timeZone: zone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(at);
    const value = (type: string) => parts.find((part) => part.type === type)?.value ?? null;
    const [year, month, day, hour, minute] = [
      value("year"),
      value("month"),
      value("day"),
      value("hour"),
      value("minute"),
    ];
    if ([year, month, day, hour, minute].some((part) => part === null)) {
      return null;
    }
    return `${year}年${month}月${day}日 ${hour}:${minute}`;
  } catch {
    return null;
  }
}

/**
 * システム時刻（`mediaferry.clock.now_iso` が作る UTC の ISO 文字列）を人が読める形にする。
 *
 * **`zone` へ直して、そのゾーンの印を添える。** `zone` が無い・読めないときは
 * 直さずに「（UTC）」で出す —— 勝手に見ている人の時計へ倒すと、どの時計の数字なのかが
 * 画面から決められなくなる。
 */
export function formatSystemDateTime(iso: string, zone: string | null = null): string {
  const raw = formatDateTime(iso);
  const at = new Date(iso);
  if (zone !== null && !Number.isNaN(at.getTime())) {
    const wall = wallClockIn(zone, at);
    const label = zoneLabel(zone, at);
    if (wall !== null && label !== null) {
      return `${wall}（${label}）`;
    }
  }
  return raw === UNKNOWN ? raw : `${raw}（UTC）`;
}

/**
 * 撮影日時（`captured_at`）を人が読める形にする。**壁時計は直さない。**
 *
 * 印は「その値がどの時計のものか」を言う。順に、
 *
 * 1. `tz`（`media_file.captured_at_tz`。解決できたゾーン名）
 * 2. `zone`（`DEFAULT_TIMEZONE`）—— **ゾーンが決まらなかった値はこれとみなす。**
 *    `timezone_policy: none` の値は `+00:00` で保存されるので（カメラの壁時計に
 *    UTC の札が貼られた形）、オフセットをそのまま信じると本当に UTC で撮ったものと
 *    区別が付かない。**空かどうかで見分けられるのは `tz` だけ**（利用者の裁定、2026-08-28）
 * 3. どちらも無ければ、値が持つオフセット
 */
export function formatCapturedDateTime(
  iso: string,
  tz: string | null,
  zone: string | null,
): string {
  const formatted = formatDateTime(iso);
  if (formatted === UNKNOWN) {
    return formatted;
  }
  const at = new Date(iso);
  const name = tz ?? zone;
  const label =
    name !== null && !Number.isNaN(at.getTime()) ? zoneLabel(name, at) : offsetLabel(iso);
  return label === null ? formatted : `${formatted}（${label}）`;
}
