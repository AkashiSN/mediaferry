import "@testing-library/jest-dom/vitest";

// jsdom に `EventSource` は無い。**画面のテストで SSE を本物にしない**
// （線の上の挙動は実プロセスの E2E で見る）。ここでは「開いて閉じられる」
// だけの最小の代役を置く。
class StubEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {}
  addEventListener(): void {}
  removeEventListener(): void {}
  close(): void {}
}

globalThis.EventSource = StubEventSource as unknown as typeof EventSource;
