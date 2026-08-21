import "@testing-library/jest-dom/vitest";

// jsdom に `EventSource` は無い。**画面のテストで SSE を本物にしない**
// （線の上の挙動は実プロセスの E2E で見る）。ここでは「開いて閉じられる」に加え、
// テストから接続と配信を操作できる代役を置く（`openStream()` / `failStream()` /
// `emitJob()`）。何も呼ばなければ `onopen` も `onerror` も一度も発火しない、つまり
// 挙動はこれまでと変わらない —— 呼んで初めて画面から観測できる。
class StubEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private readonly listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();
  constructor(readonly url: string) {
    instances.push(this);
  }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }
  removeEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.get(type)?.delete(listener);
  }
  close(): void {}

  open(): void {
    this.onopen?.();
  }
  fail(): void {
    this.onerror?.();
  }
  emit(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

const instances: StubEventSource[] = [];

globalThis.EventSource = StubEventSource as unknown as typeof EventSource;

/** 直近に作られた（＝いま描画中の画面が開いた）代役。 */
function latestStream(): StubEventSource {
  const stream = instances.at(-1);
  if (!stream) {
    throw new Error("EventSource がまだ作られていません（先に画面を描画すること）");
  }
  return stream;
}

/** 接続が開いたことにする（`useEvents` の `connected` を真にする）。 */
export function openStream(): void {
  latestStream().open();
}

/** 接続が切れたことにする（`connected` を偽に戻す）。 */
export function failStream(): void {
  latestStream().fail();
}

/** `job` イベントを 1 件配る。 */
export function emitJob(event: Record<string, unknown>): void {
  latestStream().emit("job", event);
}
