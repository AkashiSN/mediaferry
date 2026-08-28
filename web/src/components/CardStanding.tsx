// カードを抜いていいか（§13）。**押して確かめるのではなく、常に出す。**
//
// 掴んでいる作業が無ければ、読み取り専用のマウントは作業の終わりに外れて
// いるので、その時点で既に安全である。判定の出所は `/devices` の `busy`。
//
// **これは断定文なので、出す画面は `/devices` を取り直し続ける義務を負う。**
// 共通の経路は進捗の知らせで、ホーム（`screens/Home.tsx`）はそれに加えて、
// 走っている作業が空になった縁でも取り直す。押さなくても切り替わるのは、
// 呼び出し元がそれを繋いでいるからである。
//
// **`held` は「呼び出し元が既に知っている」を渡す口。** 進捗は `job.progress`
// の心拍で運ばれ、`job_event` は 1 件取り込み終えるまで出ない（`jobs/importer.py`）
// ので、大きい 1 本をコピーしている間は取り直しの合図が届かず、写しの `busy` は
// 古いままになる。走っている作業の下に出すときは、**その位置に在ること自体が
// 掴まれている証拠**なので、写しを待たずに立てる。

import type { CardView } from "../hooks/homeSections";

export function CardStanding({ card, held = false }: { card: CardView; held?: boolean }) {
  return (
    <p role="status" className="small">
      {held || card.busy ? "作業中です。終わるまで抜かないでください。" : "いま抜いて大丈夫です。"}
    </p>
  );
}
