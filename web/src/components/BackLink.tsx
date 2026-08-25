// 作業の画面の「戻る」（§13）。
//
// **つなぐ・日時の確認・接続中のカードには入口が 2 つある** —— ホームの「やること」と、
// 設定 › ふだんは使わない操作。どちらか一方に固定すると、もう片方から来た人が
// 知らない画面へ飛ばされる。**来た道を `location.state` で受け取って決める。**
//
// **ブラウザの履歴と `document.referrer` は使わない。** URL を直接開いた場合と
// 画面内の遷移を区別できず、外から来たときには戻り先が無い。

import { Link, useLocation } from "react-router-dom";

import { Icon } from "./Icon";

/** 受け取ってよい戻り先。**`state` は画面から来る値なので、そのまま行き先にしない。** */
const KNOWN: Record<string, string> = {
  "/settings": "設定へ",
};

/** 遷移元を渡すときの `state`。**渡す側と受け取る側で同じ形を使う。** */
export type From = { from?: string };

export function BackLink() {
  const { state } = useLocation();
  const from = (state as From | null)?.from;
  // **知っている入口かどうかを 1 度だけ決め、名前と行き先の両方をそこから作る。**
  // 別々に導くと、名前は「ホームへ」なのに行き先は渡された文字列のまま、という
  // 食い違いが起きる（変異試験で見つかった）。
  const known = from !== undefined && Object.hasOwn(KNOWN, from);
  return (
    <div className="row">
      <Link to={known ? (from as string) : "/"} className="btn sm">
        <Icon name="back" size={16} />
        {known ? KNOWN[from as string] : "ホームへ"}
      </Link>
    </div>
  );
}
