// 進捗が届いたら一覧を取り直す（§13 の「画面を再読み込みせずに進む」）。
//
// **イベントの中身は読まない。** 「何かが進んだ」ことだけを合図にして、取り直しは
// API に任せる —— 何がどう変わったかをブラウザ側で組み立てると、同じ規則が 2 箇所に
// 散る。**まとめて 1 回にする**（1 件ずつ取り直すと、取り込み中に何十回も叩く）。

import { useEffect, useRef } from "react";

export const SETTLE_MS = 400;

/**
 * `received`（受け取った総数）が増えたら、少し待ってから 1 回だけ取り直す。
 *
 * **配列の長さで判定しない。** 保持する件数には上限があるので、上限に達した後は
 * 中身が変わっても長さは同じ —— 長い取り込みの途中から取り直しが止まる。
 */
export function useReloadOnEvents(received: number, reload: () => void): void {
  // **最初の描画では取り直さない**（画面は既に読み込んでいる）。何件受け取った
  // 状態で開いたかを基準にする。
  const seen = useRef<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (seen.current === null) {
      seen.current = received;
      return;
    }
    if (received === seen.current) {
      return;
    }
    seen.current = received;
    if (timer.current !== null) {
      clearTimeout(timer.current);
    }
    timer.current = setTimeout(() => {
      timer.current = null;
      reload();
    }, SETTLE_MS);
    return () => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [received, reload]);
}
