// 画面が使う読み取りの共通形（読み込み中・失敗・再取得）。

import { useCallback, useEffect, useState } from "react";

import { request } from "./client";

export type Query<T> = {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
};

/** GET を 1 本。**失敗しても画面は落とさない**（バナーに出して再取得できる）。 */
export function useQuery<T>(path: string, deps: unknown[] = []): Query<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    // **同期で setState しない**（連鎖レンダーになる）。読み込み中の印は
    // 取得の直前に非同期で立てる。
    const started = Promise.resolve().then(() => {
      if (!controller.signal.aborted) {
        setLoading(true);
      }
    });
    void started;
    request<T>(path, { signal: controller.signal })
      .then((body) => {
        setData(body);
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

  return { data, error, loading, reload };
}
