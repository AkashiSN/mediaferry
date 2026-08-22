// アイコン（§13）。**外部の書体もスクリプトも読まない**ので、すべてインライン SVG。
// パスは docs/history/phase7-prototype.html の `ic` オブジェクトからそのまま取る。

const PATHS = {
  home: (
    <>
      <path d="M4 11l8-6 8 6" />
      <path d="M6 10v9h12v-9" />
    </>
  ),
  photo: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="8.5" cy="10" r="1.6" />
      <path d="M4 17l5-5 4 4 2.5-2.5L20 17" />
    </>
  ),
  gear: (
    <>
      <path d="M4 8h9M19 8h1M4 16h1M11 16h9" />
      <circle cx="16" cy="8" r="2.6" />
      <circle cx="8" cy="16" r="2.6" />
    </>
  ),
  card: (
    <>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M10 3v3.5M13 3v3.5" />
    </>
  ),
  merge: (
    <>
      <rect x="2.5" y="7" width="8" height="10" rx="1.5" />
      <rect x="13.5" y="7" width="8" height="10" rx="1.5" />
      <path d="M10.5 12h3" />
    </>
  ),
  up: (
    <>
      <path d="M12 19V5" />
      <path d="M6 11l6-6 6 6" />
    </>
  ),
  alert: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5" />
      <circle cx="12" cy="16.4" r=".95" fill="currentColor" stroke="none" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-4.5" />
      <circle cx="12" cy="8.2" r=".95" fill="currentColor" stroke="none" />
    </>
  ),
  check: <path d="M5 13l4 4 10-10" />,
  lock: (
    <>
      <rect x="4" y="10" width="16" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  back: <path d="M14 6l-6 6 6 6" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  play: <path d="M10 8l6 4-6 4z" />,
  image: (
    <>
      <path d="M3 17l5-5 4 4 3-3 6 6" />
      <circle cx="8" cy="8" r="1.8" />
    </>
  ),
  minus: <path d="M8 12h8" />,
  list: <path d="M4 7h16M4 12h16M4 17h9" />,
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
