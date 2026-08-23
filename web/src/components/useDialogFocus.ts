// ダイアログの焦点（§13）。
//
// `aria-modal="true"` を名乗る以上、**背後は「無い」ことになっている**。開いたら
// 中へ焦点を移し、Tab は中で回し、閉じたら開く前に触っていたところへ戻す。Esc でも
// 閉じられる（キーボードだけで抜けられない画面を作らない）。
//
// **確認のダイアログと、つなぐ画面の「構成を変える」が同じものを要る。** 写しを
// 2 つ持つと、片方だけ背後へ焦点が抜けるようになる。

import { useEffect, useRef } from "react";
import type { RefObject } from "react";

/** ダイアログの中で焦点を持てるもの。**押せないボタンは飛ばす**（`busy` の間、
 * 「やめる」と「実行する」はどちらも `disabled` になる）。 */
const FOCUSABLE =
  'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]),' +
  ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * `dialog` の中に焦点を閉じ込める。
 *
 * `busy` の間は Esc で閉じない（押した操作が走っている最中に確認だけ消えると、
 * 何が起きたのか分からなくなる）。
 */
export function useDialogFocus(
  dialog: RefObject<HTMLElement | null>,
  onEscape: () => void,
  busy = false,
): void {
  // **いちばん新しい `onEscape` と `busy` を、購読を張り直さずに読む。** 呼び出し側は
  // どちらも毎回の描画で作り直すので、依存に入れると開いている間ずっと登録し直しに
  // なり、そのたびに焦点が先頭のボタンへ跳ね返る。
  const latest = useRef({ onEscape, busy });
  useEffect(() => {
    latest.current = { onEscape, busy };
  });

  useEffect(() => {
    const node = dialog.current;
    if (node === null) {
      return;
    }
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = (): HTMLElement[] => [...node.querySelectorAll<HTMLElement>(FOCUSABLE)];
    focusable()[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (!latest.current.busy) {
          latest.current.onEscape();
        }
        return;
      }
      if (event.key !== "Tab" || node === null) {
        return;
      }
      const items = focusable();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      const outside = active === null || !node.contains(active);
      if (event.shiftKey && (outside || active === items[0])) {
        event.preventDefault();
        items[items.length - 1].focus();
      } else if (!event.shiftKey && (outside || active === items[items.length - 1])) {
        event.preventDefault();
        items[0].focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      restoreFocus(opener);
    };
  }, [dialog]);
}

/**
 * 開く前に触っていたところへ焦点を戻す。
 *
 * **戻る先が押せなくなっていることがある。** 実行すると走っている間だけ開いた
 * ボタンが `disabled` になるので、そのまま戻そうとすると焦点が `body` へ落ち、
 * キーボードだけの人は画面の頭からやり直しになる。戻せないときは、そのボタンが
 * 居た画面（`section[aria-label]`）へ入れる。
 */
function restoreFocus(opener: HTMLElement | null): void {
  opener?.focus();
  if (opener !== null && document.activeElement === opener) {
    return;
  }
  const screen = opener?.closest("section[aria-label]");
  if (screen instanceof HTMLElement) {
    // 焦点を受けるためだけの `tabindex`（Tab の順番には入れない）。
    screen.tabIndex = -1;
    screen.focus();
  }
}
