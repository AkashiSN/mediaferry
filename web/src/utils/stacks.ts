// 組（RAW+JPEG）の扱い。**一覧・詳細・送るの 3 画面がここだけを呼ぶ。**
//
// **3 画面が別々に組むと、画面によって枚数が違うことになる。** 数え方（枚数・
// 合計サイズ）はファイル単位、見え方（タイル・札）は組単位、という約束は
// 規約ではなくこの関数で守る。

import type { StackMember } from "../components/MediaTile";

/** 組にまとめられる行が満たすべき最小の形。 */
export type StackRow = { id: string; stack?: { members: StackMember[] } | null };

/** 1 タイル。**`rows` は「このタイルが表すファイル」**で、渡された集合に居るものだけ。 */
export type StackTile<T> = { primary: T; rows: T[] };

/** `rel_path` の拡張子（ドット無しの大文字）。 */
function extensionOf(relPath: string): string {
  const name = relPath.split("/").pop() ?? relPath;
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toUpperCase();
}

/**
 * 組の名乗り（`JPG+RAW`）。**1 枚では組にならない**ので `null`。
 *
 * **主の拡張子はファイル名から取り、相方は `RAW` と呼ぶ。** `stack.extensions` は
 * 利用者が編集できるので、主が HEIC の組がありうる —— 画面が実在しない
 * ファイル名を名乗らないための保険。相方が 2 つ以上あるときは枚数を添える
 * （**組の枚数を黙って隠さない**）。
 */
export function stackLabel(members: { rel_path: string }[]): string | null {
  if (members.length < 2) {
    return null;
  }
  const base = `${extensionOf(members[0].rel_path)}+RAW`;
  return members.length === 2 ? base : `${base} ×${members.length - 1}`;
}

/**
 * 行を組にまとめる。**渡された集合の中だけで組む。**
 *
 * 集合に来ていない相方はタイルに入れない —— 送る画面は「絞り込みが返した行」
 * しか送らないので、`stack.members` に居るだけの相方を数えると、送信済みの
 * ファイルを数え直すことになる（`docs/history/phase12-design.md` の 2）。
 *
 * **並びは入力順を保つ**（最初に現れた行の位置にタイルを置く）。API の並び
 * （`captured_at DESC, rel_path DESC`）を崩すと、日付のまとまりが割れる。
 */
export function groupIntoStacks<T extends StackRow>(rows: T[]): StackTile<T>[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const tiles: StackTile<T>[] = [];
  const placed = new Set<string>();
  for (const row of rows) {
    if (placed.has(row.id)) {
      continue;
    }
    const members = row.stack?.members;
    if (members === undefined || members === null) {
      placed.add(row.id);
      tiles.push({ primary: row, rows: [row] });
      continue;
    }
    // **主は `stack.members` の並び**（先頭が主）のうち、集合に実際に居る先頭。
    const present = members
      .map((member) => byId.get(member.id))
      .filter((one): one is T => one !== undefined);
    const mine = present.length === 0 ? [row] : present;
    for (const one of mine) {
      placed.add(one.id);
    }
    tiles.push({ primary: mine[0], rows: mine });
  }
  return tiles;
}
