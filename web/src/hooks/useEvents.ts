// 進捗の購読（SSE）。
//
// **再接続はブラウザに任せる。** `EventSource` は `Last-Event-ID` を自動で送るので、
// 切れている間に起きたことも次の接続で届く（サーバ側は `job_event.id` で再開する）。

import { useEffect, useRef, useState } from "react";

export type JobEvent = {
  job_id: string;
  seq: number;
  level: "debug" | "info" | "warning" | "error";
  message: string;
  data: Record<string, unknown> | null;
  at: string;
};

/**
 * 直近のイベントを保持する（画面はこれを見て進捗と一覧を更新する）。
 *
 * **受け取った総数も返す。** 配列は上限で切るので、上限に達した後は長さが
 * 変わらない —— 長さだけを見ていると、長い取り込みの途中から一覧の取り直しが
 * 止まる。
 */
export function useEvents(limit = 200): { events: JobEvent[]; received: number; connected: boolean } {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [received, setReceived] = useState(0);
  const [connected, setConnected] = useState(false);
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    const stream = new EventSource("/api/events");
    source.current = stream;
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    stream.addEventListener("job", (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as JobEvent;
      setEvents((previous) => [...previous, event].slice(-limit));
      setReceived((previous) => previous + 1);
    });
    return () => {
      stream.close();
      source.current = null;
    };
  }, [limit]);

  return { events, received, connected };
}
