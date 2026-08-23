// 進捗の購読（SSE）。
//
// **繋がった後に切れたら、再接続はブラウザに任せる。** `EventSource` は
// `Last-Event-ID` を自動で送るので、切れている間に起きたことも次の接続で届く
// （サーバ側は `job_event.id` で再開する）。
//
// **繋がらなかったときだけ、自分で開き直す。** 応答が非 2xx（枠が埋まっているときの
// 503、期限切れの 401、入れ替え中の 502）だと `EventSource` は接続を諦め、
// `readyState` が `CLOSED` のまま二度と繋ぎ直さない。1 タブ 1 本を共有している
// ため購読者は外れず、参照数での開き直しも起きない —— 放っておくと、そのタブの
// 進捗と一覧の取り直しがセッションの終わりまで止まる。間隔は失敗のたびに延ばす
// （埋まっている枠を叩き続けない）。
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
  /** サーバが流し始める位置を作り直した（見落とした変化がありうる）。 */
  onReset: () => void;
};

/** 開き直すまでの待ち。続けて失敗するほど延ばし、最後の値で頭打ちにする。 */
const REOPEN_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 15_000, 30_000];

const subscribers = new Set<Subscriber>();
let stream: EventSource | null = null;
// 共有している接続の状態。まだ一度も繋がっていなければ `null`。
let shared: boolean | null = null;
// 続けて失敗した回数（待ちの長さを決める）。繋がったら 0 に戻す。
let failures = 0;
let reopenTimer: ReturnType<typeof setTimeout> | null = null;

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
    failures = 0;
    shared = true;
    broadcast((subscriber) => subscriber.onConnected(true));
  };
  source.onerror = () => {
    // **もう共有していない接続の悲鳴は聞かない。** 開き直したあとに古い接続が
    // 鳴ると、繋がっている表示を偽で塗り替えてしまう。
    if (source !== stream) {
      return;
    }
    shared = false;
    broadcast((subscriber) => subscriber.onConnected(false));
    if (source.readyState === EventSource.CLOSED) {
      reopenLater();
    }
  };
  source.addEventListener("job", (message) => {
    const event = JSON.parse((message as MessageEvent<string>).data) as JobEvent;
    broadcast((subscriber) => subscriber.onEvent(event));
  });
  // **位置の作り直しを黙って捨てない**（`api/routes_events.py` の `cursor_reset`）。
  // DB を入れ替えた後の古いタブは、そのままだと「何も起きていない」ように見える。
  // 中身は無いが、**見落とした変化を取り直す合図**として数に足す。
  source.addEventListener("cursor_reset", () => {
    broadcast((subscriber) => subscriber.onReset());
  });
}

/** 諦められた接続を捨て、少し待ってから開き直す。 */
function reopenLater(): void {
  // **待ちを二重に積まない。** `onerror` は同じ接続で何度も鳴りうるので、
  // 上書きすると先の待ちが生き残ったまま両方が開き、1 タブ 2 本になる。
  if (reopenTimer !== null) {
    return;
  }
  stream?.close();
  stream = null;
  const delay = REOPEN_DELAYS_MS[Math.min(failures, REOPEN_DELAYS_MS.length - 1)];
  failures += 1;
  reopenTimer = setTimeout(() => {
    reopenTimer = null;
    // 待っている間に全員が外れていれば、開き直さない。
    if (subscribers.size > 0) {
      open();
    }
  }, delay);
}

/** 共有の接続に 1 人加わる。返り値を呼ぶと外れる。 */
function subscribe(subscriber: Subscriber): () => void {
  subscribers.add(subscriber);
  if (stream === null && reopenTimer === null) {
    open();
  } else if (shared !== null) {
    // **途中から見始めた画面にも、いまの状態を渡す。** 渡さないと、既に繋がって
    // いるのに `null` のままになり、接続の状態を出す画面が黙る。
    subscriber.onConnected(shared);
  }
  return () => {
    subscribers.delete(subscriber);
    if (subscribers.size > 0) {
      return;
    }
    if (reopenTimer !== null) {
      clearTimeout(reopenTimer);
      reopenTimer = null;
    }
    stream?.close();
    stream = null;
    shared = null;
    failures = 0;
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
        onReset: () => setReceived((previous) => previous + 1),
      }),
    [limit],
  );

  return { events, received, connected };
}

/**
 * 届いた数だけを見る。**イベントの中身は溜めない。**
 *
 * 取り直しの合図にしか使わない購読者（`api/dashboard.tsx`）が `useEvents` を
 * 呼ぶと、読まない 200 件の控えをもう 1 組持つことになる。
 */
export function useEventCount(): number {
  const [received, setReceived] = useState(0);
  useEffect(
    () =>
      subscribe({
        onEvent: () => setReceived((previous) => previous + 1),
        onConnected: () => undefined,
        onReset: () => setReceived((previous) => previous + 1),
      }),
    [],
  );
  return received;
}
