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

/** 直近のイベントを保持する（画面はこれを見て進捗と一覧を更新する）。 */
export function useEvents(limit = 200): { events: JobEvent[]; connected: boolean } {
  const [events, setEvents] = useState<JobEvent[]>([]);
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
    });
    return () => {
      stream.close();
      source.current = null;
    };
  }, [limit]);

  return { events, connected };
}
