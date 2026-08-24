// カードを抜いていいか（§13）。**押して確かめるのではなく、常に出す。**
//
// 掴んでいる作業が無ければ、読み取り専用のマウントは作業の終わりに外れて
// いるので、その時点で既に安全である。画面は 2 秒ごとに取り直すので、
// 作業が終われば自分で切り替わる。

import type { CardView } from "../hooks/homeSections";

export function CardStanding({ card }: { card: CardView }) {
  return (
    <p role="status" className="small">
      {card.busy ? "作業中です。終わるまで抜かないでください。" : "いま抜いて大丈夫です。"}
    </p>
  );
}
