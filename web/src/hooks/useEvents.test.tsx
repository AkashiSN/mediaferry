// 進捗の購読が **1 タブに 1 本**であることを見る。
//
// サーバは 1 接続につき DB 接続を 1 本使い、同時接続に上限を置いている
// （`api/routes_events.py` の `MAX_CONNECTIONS`）。画面ごとに `EventSource` を
// 開くと、1 タブで上限を何本も食い、開けるタブの数がその分だけ減る。

import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  emitJob,
  failStream,
  latestStreamUrl,
  liveStreamCount,
  openStream,
  streamCount,
} from "../test/setup";
import { useEvents } from "./useEvents";

afterEach(() => vi.restoreAllMocks());

function Watcher({ label }: { label: string }) {
  const { received, connected } = useEvents();
  return (
    <p>
      {label}:{received}:{String(connected)}
    </p>
  );
}

describe("進捗の購読", () => {
  it("購読する画面が 2 つでも、接続は 1 本しか開かない", () => {
    render(
      <>
        <Watcher label="a" />
        <Watcher label="b" />
      </>,
    );
    expect(streamCount()).toBe(1);
  });

  it("進捗の配信につなぐ", () => {
    render(<Watcher label="a" />);
    expect(latestStreamUrl()).toBe("/api/events");
  });

  it("1 本の接続で届いたイベントを、購読者全員に配る", () => {
    render(
      <>
        <Watcher label="a" />
        <Watcher label="b" />
      </>,
    );
    act(() =>
      emitJob({ job_id: "j1", seq: 1, level: "info", message: "進んだ", data: null, at: "" }),
    );
    expect(screen.getByText("a:1:null")).toBeInTheDocument();
    expect(screen.getByText("b:1:null")).toBeInTheDocument();
  });

  it("接続の状態も購読者全員に配る", () => {
    render(
      <>
        <Watcher label="a" />
        <Watcher label="b" />
      </>,
    );
    act(() => openStream());
    expect(screen.getByText("a:0:true")).toBeInTheDocument();
    act(() => failStream());
    expect(screen.getByText("b:0:false")).toBeInTheDocument();
  });

  it("後から購読を始めた画面にも、いまの状態を渡す", () => {
    const view = render(<Watcher label="a" />);
    act(() => openStream());
    view.rerender(
      <>
        <Watcher label="a" />
        <Watcher label="b" />
      </>,
    );
    expect(streamCount()).toBe(1);
    expect(screen.getByText("b:0:true")).toBeInTheDocument();
  });

  it("購読者が 1 人減っても閉じない。最後の 1 人が外れたときだけ閉じる", () => {
    const view = render(
      <>
        <Watcher label="a" />
        <Watcher label="b" />
      </>,
    );
    view.rerender(<Watcher label="a" />);
    expect(liveStreamCount()).toBe(1);
    view.unmount();
    expect(liveStreamCount()).toBe(0);
  });

  it("開き直したときは、前の接続の状態を持ち越さない", () => {
    // 持ち越すと、まだ繋がっていない接続を「繋がっている」と名乗ってしまう。
    const view = render(<Watcher label="a" />);
    act(() => openStream());
    expect(screen.getByText("a:0:true")).toBeInTheDocument();
    view.unmount();
    render(
      <>
        <Watcher label="a" />
        <Watcher label="b" />
      </>,
    );
    // 1 人目は新しい接続を開き、2 人目はその接続にぶら下がる。**どちらも、
    // まだ開いていない接続を「繋がっている」と名乗ってはいけない。**
    expect(screen.getByText("a:0:null")).toBeInTheDocument();
    expect(screen.getByText("b:0:null")).toBeInTheDocument();
  });

  it("全員が外れたあとで購読し直すと、新しく開き直す", () => {
    const view = render(<Watcher label="a" />);
    view.unmount();
    render(<Watcher label="a" />);
    expect(streamCount()).toBe(2);
    expect(liveStreamCount()).toBe(1);
  });
});
