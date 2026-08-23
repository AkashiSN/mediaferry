// 画面が使う読み取りの共通形（読み込み中・失敗・再取得）。

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { request } from "./client";

export type Query<T> = {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** いま持っている `data` が、**別の問い合わせのもの**（取得が終わっていない）。
   * 出せる値はあるが、いまの絞り込みやページのものではない。 */
  stale: boolean;
  reload: () => void;
};

/**
 * GET を 1 本。**失敗しても画面は落とさない**（バナーに出して再取得できる）。
 *
 * `loading` は「まだ出せる値が無い」という意味で、**取り直しの間は立てない**。
 * 取り込み中は進捗のたびに取り直す（`useReloadOnEvents`）ので、そのたびに
 * 立てると、ホームの「やること」が 1 秒おきに「読み込み中…」へ化ける。
 */
export function useQuery<T>(path: string, deps: unknown[] = []): Query<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  // どの問い合わせの結果を持っているか。**新しい条件の結果が返るまで、前の結果に
  // 新しい条件の意味を被せない**（前のページの行に新しい番号を付けない、など）。
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  // 効果の中から「いま出せる値があるか」を見るための写し（`data` を依存に
  // 入れると、取得のたびに取得し直しになる）。
  const held = useRef<T | null>(null);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    // **同期で setState しない**（連鎖レンダーになる）。読み込み中の印は
    // 取得の直前に非同期で立てる。
    const started = Promise.resolve().then(() => {
      if (!controller.signal.aborted) {
        setLoading(held.current === null);
      }
    });
    void started;
    request<T>(path, { signal: controller.signal })
      .then((body) => {
        held.current = body;
        setData(body);
        setLoadedPath(path);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps]);

  // **同じ中身なら同じ物を返す。** これをそのまま context の値にする画面が
  // あるので（`api/dashboard.tsx`）、毎回作り直すと購読側が描き直しになる。
  const stale = data !== null && loadedPath !== path;
  return useMemo(
    () => ({ data, error, loading, stale, reload }),
    [data, error, loading, stale, reload],
  );
}

export type Mutation = {
  /** 走っている間だけ真（ボタンを押せなくするための印）。 */
  busy: boolean;
  error: unknown;
  /**
   * 1 つ走らせて、**成功したかを返す**。
   *
   * 失敗は `error` に入れてここで呑む（**画面は落とさない**。バナーに出して
   * やり直せる）。返り値を見れば、成功したときだけ進む後片付けを書ける。
   */
  run: (action: () => Promise<unknown>) => Promise<boolean>;
  /** 要求以外の失敗を出す（画面が自分で書いた文言など）。 */
  fail: (error: unknown) => void;
  /** バナーの「閉じる」。 */
  clear: () => void;
};

/**
 * 画面が使う書き込みの共通形（実行中・失敗）。
 *
 * `useQuery` と対にする。**同じ 5 行**（busy を立てる / 失敗を消す / 走らせる /
 * 失敗を捕まえる / busy を戻す）を画面ごとに書き写すと、どれか 1 つ
 * （たいてい `finally` の busy 戻し）が抜けた写しが混ざる。
 */
export function useMutation(): Mutation {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = useCallback(async (action: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      setError(caught);
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const fail = useCallback((caught: unknown) => setError(caught), []);
  const clear = useCallback(() => setError(null), []);

  return { busy, error, run, fail, clear };
}
