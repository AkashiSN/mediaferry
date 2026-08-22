// 画面の枠（ナビゲーションと、公開の警告バナー）。
//
// ナビは「ホーム / 写真 / 設定」の 3 つだけ（§13）。広い画面では左の柱、狭い画面では
// 上の帯に見えるが、DOM は 1 つしか持たない。**同じ項目の nav を 2 つ置くと、
// スクリーンリーダーの読み上げが 2 度になる。**
//
// 名乗り（`.brand`）は狭い画面では出さない —— 3 項目と 1 行に並べると文字が欠ける
// （`styles.css` に実測値がある）。

import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { Icon, type IconName } from "./Icon";

export type Warning = { code: string; message: string };

type ScreenLink = { to: string; label: string; icon: IconName; match: (path: string) => boolean };

// 作業ページ（つなぐ・送る・確認など）はホームの下位にあり、ナビゲーションの項目を
// 増やさない（§13）。だから NavLink の `end` では現在地を判定できない
// —— `/merge` を開いていてもホームが現在地のままである必要がある。
const SCREENS: readonly ScreenLink[] = [
  {
    to: "/",
    label: "ホーム",
    icon: "home",
    match: (path) => !path.startsWith("/photos") && !path.startsWith("/settings"),
  },
  { to: "/photos", label: "写真", icon: "photo", match: (path) => path.startsWith("/photos") },
  { to: "/settings", label: "設定", icon: "gear", match: (path) => path.startsWith("/settings") },
];

export function Layout({
  warnings,
  taskCount,
  children,
}: {
  warnings: Warning[];
  taskCount: number;
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  return (
    <div className="layout">
      <nav className="nav" aria-label="画面">
        <div className="brand">
          <b>mediaferry</b>
        </div>
        {SCREENS.map((screen) => {
          const current = screen.match(pathname);
          const count = screen.to === "/" && taskCount > 0 ? taskCount : null;
          return (
            <Link
              key={screen.to}
              to={screen.to}
              className="navitem"
              aria-current={current ? "page" : undefined}
            >
              <Icon name={screen.icon} />
              <span className="navlabel grow">{screen.label}</span>
              {count !== null && <span className="pill">{count}</span>}
            </Link>
          );
        })}
      </nav>
      <main className="main">
        {warnings.map((warning) => (
          <div key={warning.code} className="warning-banner" role="status">
            {warning.message}
          </div>
        ))}
        {children}
      </main>
    </div>
  );
}
