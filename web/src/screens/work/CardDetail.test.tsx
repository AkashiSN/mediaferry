// カードの中身（§13）。判定結果・確度・信頼登録の同意を、複数ボリュームが
// 並ぶ前提で見る。

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SETTLE_MS } from "../../hooks/useReloadOnEvents";
import { emitJob } from "../../test/setup";
import { CardDetailScreen } from "./CardDetail";

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("カードの信頼登録", () => {
  const base = {
    volume_instance_id: "v1",
    fs_label: "SD_Card",
    size_bytes: 512_711_688_192,
    profile_slug: "dji-osmo",
    identity_confidence: "high",
    provisional: false,
    trusted: true,
    reason: null,
  };

  let calls: { path: string; method: string }[] = [];

  function stubDevices(
    volumes: unknown[],
    autoImport = "trusted",
    settingsStatus = 200,
    profiles: unknown[] = [],
  ) {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        calls.push({ path, method: init?.method ?? "GET" });
        if (path === "/settings" && settingsStatus !== 200) {
          return Promise.resolve(
            new Response(JSON.stringify({ error: { code: "internal", detail: "" } }), {
              status: settingsStatus,
            }),
          );
        }
        const body =
          path === "/settings"
            ? {
                warnings: [],
                settings: [
                  {
                    key: "AUTO_IMPORT",
                    value: autoImport,
                    source: "default",
                    locked: false,
                    tier: "runtime",
                    writable: true,
                  },
                ],
              }
            : path === "/devices"
              ? { volumes }
              : path === "/profiles"
                ? { profiles }
                : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
  }

  function renderCardDetail() {
    render(
      <MemoryRouter>
        <CardDetailScreen />
      </MemoryRouter>,
    );
  }

  it("「取り込む」を押すと、その操作だけを呼ぶ", async () => {
    stubDevices([{ ...base, trusted: true }]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を取り込む" }));
    await waitFor(() => {
      expect(calls.some((call) => call.path === "/volumes/v1/import" && call.method === "POST")).toBe(
        true,
      );
    });
    expect(calls.some((call) => call.path === "/volumes/v1/close")).toBe(false);
  });

  it("掴まれていないカードは、抜いていいと言う", async () => {
    stubDevices([{ ...base, busy: false }]);
    renderCardDetail();

    expect(await screen.findByText("いま抜いて大丈夫です。")).toBeInTheDocument();
  });

  it("作業中のカードは、抜かないでと言う", async () => {
    stubDevices([{ ...base, busy: true }]);
    renderCardDetail();

    expect(await screen.findByText(/抜かないでください/)).toBeInTheDocument();
  });

  // **断定文は、更新され続けなければならない**（§13）。この画面は「取り込む」を
  // 押した人がそのまま見ている場所なので、終わったことが届かないと「作業中です。
  // 終わるまで抜かないでください。」を永久に読み続ける。画面の役目は答えを出すこと
  // なので、答えが更新されないなら役目を果たしていない。
  it("作業が終わったら、押さなくても抜いていいと言う", async () => {
    vi.useFakeTimers();
    stubDevices([{ ...base, busy: true }]);
    renderCardDetail();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(screen.getByText(/抜かないでください/)).toBeInTheDocument();

    // サーバ側では作業が終わってカードを離した。合図は進捗の知らせで届く。
    stubDevices([{ ...base, busy: false }]);
    act(() => {
      emitJob({
        job_id: "j1",
        seq: 1,
        level: "info",
        message: "取り込み完了: 3 件 / スキップ 0 件 / 失敗 0 件",
        data: null,
        at: "2026-08-24T00:00:05Z",
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SETTLE_MS + 100);
    });

    expect(screen.getByText("いま抜いて大丈夫です。")).toBeInTheDocument();
  });

  it("何も起きないボタンを置かない", async () => {
    // 「取り外す」は押しても、掴んでいる作業が無ければ何も起きない（読み取り
    // 専用のマウントは作業の終わりに既に外れている）。値打ちは答えそのものな
    // ので、ボタンではなく常時の表示にする。
    stubDevices([{ ...base, busy: false }]);
    renderCardDetail();

    await screen.findByText("いま抜いて大丈夫です。");
    expect(screen.queryByRole("button", { name: /取り外す/ })).not.toBeInTheDocument();
  });

  it("2 枚を並べ、それぞれ独立に操作できる", async () => {
    // Osmo は内蔵ストレージと SD の 2 つが同時に見える。
    stubDevices([
      base,
      { ...base, volume_instance_id: "v2", fs_label: "Pocket4", profile_slug: "canon-eos" },
    ]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "Pocket4 をスキャン" }));

    await waitFor(() => {
      expect(calls.some((call) => call.path === "/volumes/v2/scan")).toBe(true);
    });
    expect(calls.some((call) => call.path === "/volumes/v1/scan")).toBe(false);
  });

  it("「対象だが中身が無い」と「対象外」を区別して出す", async () => {
    stubDevices([
      { ...base, provisional: true },
      {
        ...base,
        volume_instance_id: "v2",
        fs_label: "USB_STICK",
        size_bytes: 512_711_688_192,
        profile_slug: null,
        reason: "DCIM が無い",
      },
    ]);
    renderCardDetail();

    expect(await screen.findByText(/取り込む中身がまだありません/)).toBeInTheDocument();
    expect(screen.getByText(/DCIM が無い/)).toBeInTheDocument();
  });

  it("対象で中身もあるカードには「対象だが中身が無い」を出さない", async () => {
    // provisional の逆読み（`!volume.provisional` への取り違え）を検出する。
    // 上のテストは 2 枚とも見るが、1 枚目（provisional）の文言しか確かめて
    // いないので、条件を反転しても偶然もう 1 枚に文言が出て見逃す。
    stubDevices([{ ...base, provisional: false }]);
    renderCardDetail();

    await screen.findByText("SD_Card");
    expect(screen.queryByText(/取り込む中身がまだありません/)).toBeNull();
  });

  it("一致したボリュームでも、判定の理由を出す", async () => {
    // **理由は対象外だけのものではない**（§13）。「なぜこのプロファイルに
    // 決まったのか」が見えないと、プロファイルを直す手がかりが無い。
    stubDevices([{ ...base, reason: "DCIM に一致するファイルが 2 件" }]);
    renderCardDetail();

    expect(await screen.findByText(/DCIM に一致するファイルが 2 件/)).toBeInTheDocument();
  });

  it("理由が読めないときは「不明」と書く", async () => {
    stubDevices([{ ...base, reason: null }]);
    renderCardDetail();

    expect(await screen.findByText(/判定の理由: 不明/)).toBeInTheDocument();
  });

  it("判定結果には、一致したプロファイルか「対象外」を出す", async () => {
    stubDevices([{ ...base, profile_slug: null }]);
    renderCardDetail();

    expect(await screen.findByText(/判定: 対象外/)).toBeInTheDocument();
  });

  it("一致したカードでは「対象外の理由」と書かない", async () => {
    // ラベルだけ入れ替わっても理由の文言は同じままなので、値だけを見る
    // アサーションでは検出できない。ラベルそのものを、しかもどちらの向きに
    // 誤っても落ちるように確かめる。
    stubDevices([{ ...base, reason: "DCIM に一致するファイルが 2 件" }]);
    renderCardDetail();

    expect(await screen.findByText(/^判定の理由:/)).toBeInTheDocument();
    expect(screen.queryByText(/^対象外の理由:/)).toBeNull();
  });

  it("対象外のカードでは「判定の理由」と書かない", async () => {
    stubDevices([{ ...base, profile_slug: null, reason: "DCIM が無い" }]);
    renderCardDetail();

    expect(await screen.findByText(/^対象外の理由:/)).toBeInTheDocument();
    expect(screen.queryByText(/^判定の理由:/)).toBeNull();
  });

  it("確度は日本語で書く（内部の値をそのまま出さない）", async () => {
    // 実際に取りうる値は high / low の 2 つだけ（`_identity_confidence`）。
    // 値ごとに文言が違うことを確かめる。
    stubDevices([
      { ...base, identity_confidence: "high" },
      { ...base, volume_instance_id: "v2", fs_label: "Pocket4", identity_confidence: "low" },
    ]);
    renderCardDetail();

    // 閉じ括弧までを含めて確かめる。**末尾に何か付け足す変異**（文言の一部だけを
    // 変えて残す）だと、開き側だけの緩い正規表現では検出できない。
    expect(await screen.findByText(/（確度：確かめられています）/)).toBeInTheDocument();
    expect(screen.getByText(/（確度：まだ確かめられていません）/)).toBeInTheDocument();
    // 内部の値そのもの（"high" / "low"）は出さない。
    expect(screen.queryByText(/確度：high/)).toBeNull();
    expect(screen.queryByText(/確度：low/)).toBeNull();
  });

  it("カメラの種類は表示名を出し、見つからないときだけ slug にフォールバックする", async () => {
    stubDevices(
      [
        { ...base, profile_slug: "dji-osmo" },
        { ...base, volume_instance_id: "v2", fs_label: "Pocket4", profile_slug: "unknown-cam" },
      ],
      "trusted",
      200,
      [{ slug: "dji-osmo", name: "DJI Osmo Pocket" }],
    );
    renderCardDetail();

    expect(await screen.findByText(/判定: DJI Osmo Pocket/)).toBeInTheDocument();
    // 登録されている slug は、生のまま出さない。
    expect(screen.queryByText(/判定: dji-osmo/)).toBeNull();
    // 登録が無い slug だけ、フォールバックで出す。
    expect(screen.getByText(/判定: unknown-cam/)).toBeInTheDocument();
  });

  it("「対象だが中身が無い」は、カメラの種類の表示名を使う", async () => {
    stubDevices(
      [{ ...base, provisional: true }],
      "trusted",
      200,
      [{ slug: "dji-osmo", name: "DJI Osmo Pocket" }],
    );
    renderCardDetail();

    expect(
      await screen.findByText("DJI Osmo Pocket の対象ですが、取り込む中身がまだありません。"),
    ).toBeInTheDocument();
  });

  it("ラベルが無いカードには、UUID の代わりに既定名を出す", async () => {
    stubDevices([{ ...base, fs_label: "" }]);
    renderCardDetail();

    expect(await screen.findByText("名前の無いカード")).toBeInTheDocument();
    expect(screen.queryByText("v1")).toBeNull();
  });

  it("ラベルが無いカードが複数あると、連番で見分けられるようにする", async () => {
    stubDevices([
      { ...base, fs_label: "" },
      { ...base, volume_instance_id: "v2", fs_label: "" },
    ]);
    renderCardDetail();

    await userEvent.click(
      await screen.findByRole("button", { name: "名前の無いカード 2 をスキャン" }),
    );

    await waitFor(() => {
      expect(calls.some((call) => call.path === "/volumes/v2/scan")).toBe(true);
    });
    expect(calls.some((call) => call.path === "/volumes/v1/scan")).toBe(false);
  });

  // **どのカードなのかは、容量でも見分ける。** 同じカメラのカードが 2 枚
  // 挿さっていると、判定結果も確度も同じ行になる。
  it("カードの容量を、読める形で出す", async () => {
    stubDevices([
      { ...base, size_bytes: 512_711_688_192 },
      { ...base, volume_instance_id: "v2", fs_label: "Pocket4", size_bytes: 116_047_982_592 },
    ]);
    renderCardDetail();

    expect(await screen.findByText("477 GiB")).toBeInTheDocument();
    expect(screen.getByText("108 GiB")).toBeInTheDocument();
    expect(screen.queryByText(/512711688192/)).toBeNull();
  });

  it("カードが 1 枚も無いときは、そう書く", async () => {
    stubDevices([]);
    renderCardDetail();

    expect(await screen.findByText("接続中のカードはありません")).toBeInTheDocument();
  });

  it("「ホームへ」を押すと、ホームへ戻る", async () => {
    stubDevices([]);
    render(
      <MemoryRouter initialEntries={["/card"]}>
        <Routes>
          <Route path="/card" element={<CardDetailScreen />} />
          <Route path="/" element={<div>ホームのページ</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("link", { name: "ホームへ" }));

    expect(await screen.findByText("ホームのページ")).toBeInTheDocument();
  });

  it("操作が失敗したら、その旨を画面に出す", async () => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        calls.push({ path, method });
        if (path === "/volumes/v1/scan" && method === "POST") {
          return Promise.resolve(
            new Response(JSON.stringify({ error: { code: "internal", detail: "" } }), {
              status: 500,
            }),
          );
        }
        const body =
          path === "/settings"
            ? {
                warnings: [],
                settings: [
                  {
                    key: "AUTO_IMPORT",
                    value: "trusted",
                    source: "default",
                    locked: false,
                    tier: "runtime",
                    writable: true,
                  },
                ],
              }
            : path === "/devices"
              ? { volumes: [base] }
              : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card をスキャン" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("操作中は、ほかの操作もダイアログのボタンも押せなくする", async () => {
    let resolveScan: ((value: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveScan = resolve;
    });
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        calls.push({ path, method });
        if (path === "/volumes/v1/scan" && method === "POST") {
          return pending;
        }
        const body =
          path === "/settings"
            ? {
                warnings: [],
                settings: [
                  {
                    key: "AUTO_IMPORT",
                    value: "trusted",
                    source: "default",
                    locked: false,
                    tier: "runtime",
                    writable: true,
                  },
                ],
              }
            : path === "/devices"
              ? { volumes: [{ ...base, trusted: false }] }
              : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    render(
      <MemoryRouter>
        <CardDetailScreen />
      </MemoryRouter>,
    );

    const scanButton = await screen.findByRole("button", { name: "SD_Card をスキャン" });
    await userEvent.click(scanButton);

    // 進行中は、同じカードの他の操作（信頼する・取り込む）も押せない。
    await waitFor(() => expect(scanButton).toBeDisabled());
    expect(screen.getByRole("button", { name: "SD_Card を取り込む" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "SD_Card を信頼する" })).toBeDisabled();

    resolveScan?.(new Response(JSON.stringify({}), { status: 200 }));
    await waitFor(() => expect(scanButton).not.toBeDisabled());
  });

  it("確認ダイアログのボタンも、操作中は押せなくする。終わるとダイアログを閉じる", async () => {
    let resolveTrust: ((value: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveTrust = resolve;
    });
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        const path = input.replace(/^\/api/, "");
        const method = init?.method ?? "GET";
        calls.push({ path, method });
        if (path === "/volumes/v1/trust" && method === "POST") {
          return pending;
        }
        const body =
          path === "/settings"
            ? {
                warnings: [],
                settings: [
                  {
                    key: "AUTO_IMPORT",
                    value: "trusted",
                    source: "default",
                    locked: false,
                    tier: "runtime",
                    writable: true,
                  },
                ],
              }
            : path === "/devices"
              ? { volumes: [{ ...base, trusted: false }] }
              : {};
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    render(
      <MemoryRouter>
        <CardDetailScreen />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));
    await userEvent.click(await screen.findByRole("button", { name: "実行する" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "実行する" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "やめる" })).toBeDisabled();

    resolveTrust?.(new Response(JSON.stringify({}), { status: 200 }));
    await waitFor(() => {
      expect(calls.some((call) => call.path === "/volumes/v1/trust" && call.method === "POST")).toBe(
        true,
      );
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("承認は確認を経てから。ダイアログに信頼の限界を書く", async () => {
    stubDevices([{ ...base, trusted: false }]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    // 確認を取る理由そのもの（§12.1 のプライバシー）と、信頼の限界の両方を書く。
    // **同意の対象には、いま挿してあるカードの中身が含まれる。**
    expect(dialog).toHaveTextContent(/NAS へコピー/);
    expect(dialog).toHaveTextContent(/いま入っている中身/);
    expect(dialog).toHaveTextContent(/取り違え/);
    expect(calls.some((call) => call.path.includes("trust"))).toBe(false);
  });

  it("カードの文脈では「承認」を使わない（操作の名前は「信頼」1 つ）", async () => {
    // **同じ操作を 2 つの名前で呼ばない。** 本文が「承認」、ボタンとダイアログが
    // 「信頼」だと、押す前に読む文と押すボタンが別の話に見える。
    //
    // **「承認」は日時の確認（`/approve`）の語**として残す —— そちらは
    // 「承認する／却下する」で 1 つの意味に閉じている。
    stubDevices([{ ...base, trusted: false }]);
    renderCardDetail();

    expect(await screen.findByText(/まだ信頼していません/)).toBeInTheDocument();
    const main = document.querySelector("section[aria-label='カードの中身']");
    expect(main?.textContent ?? "").not.toMatch(/承認/);
  });

  it("信頼していないカードには、いま挿してある中身も対象だと書く", async () => {
    // **承認すると、いま挿してあるこのカードの中身が次の監視周期で取り込まれる。**
    // watcher は毎 tick、現在 live な presence を候補に組み直すため（§12.1）。
    // 「次に挿したときから」と書くと、同意の対象を取り違えさせる。
    stubDevices([{ ...base, trusted: false }]);
    renderCardDetail();

    expect(await screen.findByText(/いま入っている中身も含めて/)).toBeInTheDocument();
    expect(screen.queryByText(/次にこのカードを挿したときから/)).toBeNull();
  });

  it("確度が低いだけなら、始まらないとは断言しない", async () => {
    // **初回の観測は必ず low。** その観測で指紋を憶えるので、画面が一覧を
    // 取り直すと同じ挿入のまま high になり、次の tick で積まれる
    // （`jobs/watcher.py` と `jobs/volumes.py::_identity_confidence`）。
    // 「いまは始まりません」と書くと、数秒後に始まる経路を否定してしまう。
    stubDevices([{ ...base, trusted: true, identity_confidence: "low" }]);
    renderCardDetail();

    expect(await screen.findByText(/確かめられた場合/)).toBeInTheDocument();
    expect(screen.queryByText(/いまは自動取り込みは始まりません/)).toBeNull();
  });

  it("確度が低い状態を「始まる」と約束しない", async () => {
    // **`low` には 2 種類ある。** `fs_uuid` が無い媒体や、同じ UUID の別 presence が
    // 併存している間は、何度観測しても `high` にならない
    // （`jobs/volumes.py::_identity_confidence`）。API は理由を返さないので画面は
    // 区別できない。**だから条件形で書く**（「確かめられた場合は」）。
    stubDevices([{ ...base, trusted: true, identity_confidence: "low" }]);
    renderCardDetail();

    expect(await screen.findByText(/確かめられた場合/)).toBeInTheDocument();
    expect(screen.queryByText(/確かめられしだい/)).toBeNull();
  });

  it("確度が低い、まだ信頼していないカードの確認は、同意の対象を示したまま条件を添える", async () => {
    stubDevices([{ ...base, trusted: false, identity_confidence: "low" }]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    // **条件は文全体に掛ける。** 「以後このカードを挿すだけでコピーされます」を
    // 先に無条件で書いてから限定を付け足すと、`fs_uuid` の無い媒体では前半が
    // 成立せず、同じダイアログの中で矛盾する（§12.1 の同意として曖昧）。
    expect(dialog).toHaveTextContent(/確かめられた場合に限り/);
    expect(dialog).toHaveTextContent(/いま入っている中身も含めて/);
    expect(dialog).toHaveTextContent(/取り違え/);
    expect(dialog).not.toHaveTextContent(/信頼した数秒後に始まります/);
  });

  it("確度が高いカードの確認だけが、条件なしで約束する", async () => {
    stubDevices([{ ...base, trusted: false }]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/信頼した数秒後に始まります/);
    expect(dialog).not.toHaveTextContent(/確かめられた場合に限り/);
  });

  it("watcher が積まない状態では、承認しても始まらないと書く", async () => {
    // CANDIDATES は identity_confidence = 'high' かつ provisional = 0 を要求する
    // （`jobs/watcher.py`）。断言すると、同意の内容が実挙動とずれる。
    stubDevices([{ ...base, trusted: false, provisional: true }]);
    renderCardDetail();

    expect(await screen.findByText(/いまは自動取り込みは始まりません/)).toBeInTheDocument();
    expect(screen.queryByText(/数秒後から自動で取り込みます/)).toBeNull();
  });

  it("確度が低いカードでも、承認したら始まる見込みだと書く", async () => {
    stubDevices([{ ...base, trusted: false, identity_confidence: "low" }]);
    renderCardDetail();

    expect(await screen.findByText(/確かめられた場合/)).toBeInTheDocument();
    expect(screen.queryByText(/いまは自動取り込みは始まりません/)).toBeNull();
  });

  it("AUTO_IMPORT が off なら、承認しても始まらないと書く", async () => {
    stubDevices([{ ...base, trusted: false }], "off");
    renderCardDetail();

    expect(await screen.findByText(/いまは自動取り込みは始まりません/)).toBeInTheDocument();
  });

  it("始まらない状態の確認ダイアログは、いま始まらないことを書く", async () => {
    stubDevices([{ ...base, trusted: false, provisional: true }]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    // **同意の対象を偽らない。** 信頼は記録するが、いまはコピーが始まらない。
    expect(dialog).toHaveTextContent(/いまは自動取り込みは始まりません/);
    expect(dialog).toHaveTextContent(/取り違え/);
    expect(dialog).not.toHaveTextContent(/いま入っている中身も含めて/);
  });

  it("自動取り込みが切ってあれば、設定と同じ言葉で書いて導線を出す", async () => {
    stubDevices([base], "off");
    renderCardDetail();

    // **内部の設定キーを画面に出さない**（§13）。`Settings.tsx` の項目名と
    // 同じ言葉で書き、同じものが 3 通りの呼ばれ方をしないようにする。
    expect(
      await screen.findByText(/「信頼したカードを自動で取り込む」が切ってあります/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "設定を開く" })).toHaveAttribute("href", "/settings");
    expect(document.body.textContent).not.toContain("AUTO_IMPORT");
  });

  it("設定を読めていない間は、始まると断言せず信頼も押させない", async () => {
    // **`/settings` が未解決・失敗のときに `trusted` と仮定しない。** 実設定が
    // off でも「いまの中身を数秒後にコピー」と誤って同意を取ることになる。
    stubDevices([{ ...base, trusted: false }], "trusted", 500);
    renderCardDetail();

    expect(await screen.findByText(/設定をまだ読めていない/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SD_Card を信頼する" })).toBeDisabled();
  });

  it("設定の取得に失敗したら、その失敗も画面に出す", async () => {
    stubDevices([base], "trusted", 500);
    renderCardDetail();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("AUTO_IMPORT が有効なら、無効の案内は出さない", async () => {
    stubDevices([base]);
    renderCardDetail();

    expect(await screen.findByText(/挿すと自動で取り込みます/)).toBeInTheDocument();
    expect(screen.queryByText(/が切ってあります/)).toBeNull();
  });
});
