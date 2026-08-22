// 進捗の購読（SSE）。
//
// **再接続はブラウザに任せる。** `EventSource` は `Last-Event-ID` を自動で送るので、
// 切れている間に起きたことも次の接続で届く（サーバ側は `job_event.id` で再開する）。
//
// **1 タブに 1 本だけ開く。** サーバは接続 1 本につき DB 接続を 1 本使うため同時接続に
// 上限がある（`api/routes_events.py` の `MAX_CONNECTIONS`）。枠と画面がそれぞれ開くと
// 1 タブで 2 本食い、開けるタブの数が半分になる。購読者を数え、**最初の 1 人が開き、
// 最後の 1 人が閉じる**。

import { useEffect, useState } from "react";

export type JobEvent = {
  job_id: string;
  seq: number;
  level: "debug" | "info" | "warning" | "error";
  message: string;
  data: Record<string, unknown> | null;
  at: string;
};

/** 共有している接続を見ている 1 つの画面。 */
type Subscriber = {
  onEvent: (event: JobEvent) => void;
  onConnected: (value: boolean) => void;
};

const subscribers = new Set<Subscriber>();
let stream: EventSource | null = null;
// 共有している接続の状態。まだ一度も繋がっていなければ `null`。
let shared: boolean | null = null;

/** いまの購読者全員へ配る。**配っている間の増減に耐える**ように写しを回す。 */
function broadcast(deliver: (subscriber: Subscriber) => void): void {
  for (const subscriber of [...subscribers]) {
    deliver(subscriber);
  }
}

function open(): void {
  const source = new EventSource("/api/events");
  stream = source;
  source.onopen = () => {
    shared = true;
    broadcast((subscriber) => subscriber.onConnected(true));
  };
  source.onerror = () => {
    shared = false;
    broadcast((subscriber) => subscriber.onConnected(false));
  };
  source.addEventListener("job", (message) => {
    const event = JSON.parse((message as MessageEvent<string>).data) as JobEvent;
    broadcast((subscriber) => subscriber.onEvent(event));
  });
}

/** 共有の接続に 1 人加わる。返り値を呼ぶと外れる。 */
function subscribe(subscriber: Subscriber): () => void {
  subscribers.add(subscriber);
  if (stream === null) {
    open();
  } else if (shared !== null) {
    // **途中から見始めた画面にも、いまの状態を渡す。** 渡さないと、既に繋がって
    // いるのに `null` のままになり、接続の状態を出す画面が黙る。
    subscriber.onConnected(shared);
  }
  return () => {
    subscribers.delete(subscriber);
    if (subscribers.size === 0 && stream !== null) {
      stream.close();
      stream = null;
      shared = null;
    }
  };
}

/**
 * 直近のイベントを保持する（画面はこれを見て進捗と一覧を更新する）。
 *
 * **受け取った総数も返す。** 配列は上限で切るので、上限に達した後は長さが
 * 変わらない —— 長さだけを見ていると、長い取り込みの途中から一覧の取り直しが
 * 止まる。
 *
 * **`connected` は 3 状態。** まだ一度も繋がっていない（`null`）と、明示的に
 * 切れた（`false`）を区別する。マウント直後は必ず `null` を経由するので、
 * `!connected` で判定すると開くたびに「切れている」バナーが一瞬光ってしまう。
 * 画面側は `connected === false`（＝一度は繋がって、その後切れた）だけを見る。
 */
export function useEvents(limit = 200): {
  events: JobEvent[];
  received: number;
  connected: boolean | null;
} {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [received, setReceived] = useState(0);
  const [connected, setConnected] = useState<boolean | null>(null);

  useEffect(
    () =>
      subscribe({
        onEvent: (event) => {
          setEvents((previous) => [...previous, event].slice(-limit));
          setReceived((previous) => previous + 1);
        },
        onConnected: setConnected,
      }),
    [limit],
  );

  return { events, received, connected };
}
