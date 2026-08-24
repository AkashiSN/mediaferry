// 走っている作業が無くなった**縁で 1 回だけ**取り直す（§13「作業が終われば、
// 押さなくても表示が切り替わる」）。
//
// **拍のたびには叩けない相手がいる。** `GET /devices` は `VolumeService.refresh()`
// を通ってブローカーへの `list_volumes` と `_probe` を伴うので、2 秒ごとに叩くと、
// カードを挿している限りマウントとアンマウントが数秒ごとに続く
// （`jobs/watcher.py` の冒頭が避けているのと同じ状態を、画面側から作り直す）。
//
// **進捗の知らせだけには頼らない。** 決着は成功でも失敗でも `job_event` に残る
// （`jobs/runner.py`）が、進捗の接続が切れている間は届かない。そのときでも拍が
// `/jobs` を取り直して「走っている作業が無い」に変わるので、この縁が効く。

import { useEffect, useRef } from "react";

/** `running` が真から偽へ変わったときだけ `reload` を呼ぶ。 */
export function useReloadWhenSettled(running: boolean, reload: () => void): void {
  // **最初の描画では取り直さない**（画面は既に読み込んでいる）。開いた時点の値を
  // 基準にする。
  const was = useRef(running);

  useEffect(() => {
    if (was.current && !running) {
      reload();
    }
    was.current = running;
  }, [running, reload]);
}
