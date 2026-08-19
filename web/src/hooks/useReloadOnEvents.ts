// 進捗が届いたら一覧を取り直す（§13 の「画面を再読み込みせずに進む」）。
//
// **イベントの中身は読まない。** 「何かが進んだ」ことだけを合図にして、取り直しは
// API に任せる —— 何がどう変わったかをブラウザ側で組み立てると、同じ規則が 2 箇所に
// 散る。**まとめて 1 回にする**（1 件ずつ取り直すと、取り込み中に何十回も叩く）。

import { useEffect, useRef } from "react";

import type { JobEvent } from "./useEvents";

export const SETTLE_MS = 400;

export function useReloadOnEvents(events: JobEvent[], reload: () => void): void {
  const seen = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (events.length === seen.current) {
      return;
    }
    seen.current = events.length;
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
  }, [events, reload]);
}
