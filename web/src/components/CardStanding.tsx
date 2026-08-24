// カードを抜いていいか（§13）。**押して確かめるのではなく、常に出す。**
//
// 掴んでいる作業が無ければ、読み取り専用のマウントは作業の終わりに外れて
// いるので、その時点で既に安全である。判定の出所は `/devices` の `busy` で、
// 掴んでいた作業が終われば画面がその写しを取り直すので、押さなくても切り替わる
// （`screens/Home.tsx` の知らせと縁の 2 経路）。

import type { CardView } from "../hooks/homeSections";

export function CardStanding({ card }: { card: CardView }) {
  return (
    <p role="status" className="small">
      {card.busy ? "作業中です。終わるまで抜かないでください。" : "いま抜いて大丈夫です。"}
    </p>
  );
}
