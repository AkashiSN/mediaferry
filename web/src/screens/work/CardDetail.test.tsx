// カードの中身（§13）。判定結果と確度、信頼登録、スキャン、取り込み。
//
// 旧 `screens/Devices.tsx` の「デバイスの信頼登録」describe をそのまま移す
// （Task 9）。アサーションは変えていない —— 見出しとボタンのラベルの体裁だけ
// プロトタイプの `cardScreen()` に合わせた。

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CardDetailScreen } from "./CardDetail";

beforeEach(() => {
  document.cookie = "XSRF-TOKEN=token; path=/";
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("カードの信頼登録", () => {
  const base = {
    volume_instance_id: "v1",
    fs_label: "SD_Card",
    profile_slug: "dji-osmo",
    identity_confidence: "high",
    provisional: false,
    trusted: true,
    reason: null,
  };

  let calls: { path: string; method: string }[] = [];

  function stubDevices(volumes: unknown[], autoImport = "trusted", settingsStatus = 200) {
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

  it("「取り込む」「取り外す」は、それぞれ別の操作を呼ぶ", async () => {
    // アクションの文字列を取り違えると（例えば「取り込む」が close を呼ぶ）、
    // ボタンの見た目は変わらないので気づきにくい。API のパスまで確かめる。
    stubDevices([{ ...base, trusted: true }]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を取り込む" }));
    await waitFor(() => {
      expect(calls.some((call) => call.path === "/volumes/v1/import" && call.method === "POST")).toBe(
        true,
      );
    });
    expect(calls.some((call) => call.path === "/volumes/v1/close")).toBe(false);

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を取り外す" }));
    await waitFor(() => {
      expect(calls.some((call) => call.path === "/volumes/v1/close" && call.method === "POST")).toBe(
        true,
      );
    });
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

  it("未承認のカードには、いま挿してある中身も対象だと書く", async () => {
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

  it("確度が低い未承認カードの確認は、同意の対象を示したまま条件を添える", async () => {
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
    expect(dialog).not.toHaveTextContent(/承認の数秒後に始まります/);
  });

  it("確度が高いカードの確認だけが、条件なしで約束する", async () => {
    stubDevices([{ ...base, trusted: false }]);
    renderCardDetail();

    await userEvent.click(await screen.findByRole("button", { name: "SD_Card を信頼する" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/承認の数秒後に始まります/);
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

  it("AUTO_IMPORT が off なら、その旨と設定への導線を出す", async () => {
    stubDevices([base], "off");
    renderCardDetail();

    expect(await screen.findByText(/自動取り込みは無効/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /設定/ })).toBeInTheDocument();
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
    expect(screen.queryByText(/自動取り込みは無効/)).toBeNull();
  });
});
