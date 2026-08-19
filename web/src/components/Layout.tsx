// 画面の枠（ナビゲーションと、公開の警告バナー）。

import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

export type Warning = { code: string; message: string };

const SCREENS = [
  { to: "/", label: "ダッシュボード" },
  { to: "/devices", label: "デバイス" },
  { to: "/library", label: "ライブラリ" },
  { to: "/merges", label: "結合" },
  { to: "/destinations", label: "転送先" },
  { to: "/approvals", label: "承認待ち" },
  { to: "/jobs", label: "ジョブ" },
  { to: "/settings", label: "設定" },
];

export function Layout({ warnings, children }: { warnings: Warning[]; children: ReactNode }) {
  return (
    <div className="layout">
      <nav aria-label="画面">
        <ul>
          {SCREENS.map((screen) => (
            <li key={screen.to}>
              <NavLink to={screen.to} end={screen.to === "/"}>
                {screen.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <main>
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
