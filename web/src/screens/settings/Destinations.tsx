// 送り先（§12.3 / §13）。**空の DB から 1 件目をここで作れる。**
//
// **既存の API キーは画面に出さない。** 読み出しの API を作らないので、欄は常に空
// から始まり、入れ直したときだけ送る（§12.3）。

import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useDashboardReload } from "../../api/dashboard";
import { useMutation, useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { fileName } from "../../components/MediaTile";
import { formatSystemDateTime } from "../../utils/formatDateTime";

type Destination = {
  id: string;
  name: string;
  enabled: boolean;
  base_url: string;
  public_url: string | null;
  // 同じ向き先を指す宛先の指紋（§12.3）。同値のものが他にあれば画面で知らせる。
  remote_user_id: string | null;
  verified_at: string | null;
};

type Destinations = { destinations: Destination[] };

/** スタックの見送り（§9.11）。**理由は常に出す**（対象外のときだけ出すと、
 * 出ていないことが仕様に見える）。 */
type SkippedStack = {
  id: string;
  media_file_id: string;
  /** ファイルの位置（API が添える）。**内部の ID は画面に出さない**（§13）。 */
  rel_path: string | null;
  stack_reason: string | null;
};

type SkippedStacks = { records: SkippedStack[] };

/** 一度に出す件数。**打ち切ったことを黙らない**（下の「ほかにもある」）。 */
const SKIP_PAGE = 50;

/** 一度に送り直す件数。API の `GET /uploads` の既定と同じ上限に合わせる。 */
const RETRY_PAGE = 200;

/** 失敗した記録 1 件（送り直す対象）。 */
type FailedRecord = { id: string };
type FailedRecords = { records: FailedRecord[] };

/** 送り直した結果の 1 文。**戻せなかった分を隠さない。** */
export function resendNote(retried: number, skipped: number): string {
  const parts = [
    retried > 0 ? `${retried} 件を送り直しています。` : "送信を始め直しました。",
  ];
  if (skipped > 0) {
    parts.push(`${skipped} 件は送り直せませんでした。`);
  }
  return parts.join("");
}

/**
 * 送れなかったものと、送り直す操作（§8 / §9.10）。
 *
 * **`retry` と `upload` は順番が決まっている。** `retry` が `failed` を `pending`
 * へ戻し、そのあとの `POST /destinations/{id}/upload` が積まれた `pending` を
 * 拾って送る。逆に呼ぶと、`failed` のままの記録は拾われない。
 *
 * **失敗が 0 件でも押せる。** 送信の開始そのものに失敗した宛先の記録は `pending`
 * のままなので、`upload` を積み直すだけで動き出す（`work/Send.tsx` の案内文が
 * 指しているのはこの操作）。
 */
function Resend({
  destination,
  busy,
  onResend,
}: {
  destination: Destination;
  busy: boolean;
  onResend: (
    destination: Destination,
    recordIds: string[],
  ) => Promise<{ retried: number; skipped: number } | null>;
}) {
  const failed = useQuery<FailedRecords>(
    `/uploads?destination_id=${destination.id}&state=failed&limit=${RETRY_PAGE}`,
  );
  const records = failed.data?.records ?? [];
  const [note, setNote] = useState<string | null>(null);

  async function resend() {
    setNote(null);
    const outcome = await onResend(
      destination,
      records.map((record) => record.id),
    );
    if (outcome !== null) {
      setNote(resendNote(outcome.retried, outcome.skipped));
    }
    failed.reload();
  }

  return (
    <section style={{ marginTop: 12 }}>
      <div className="sechead">
        <h3 style={{ fontSize: "13.5px", fontWeight: 600 }}>送れなかったもの</h3>
      </div>
      {failed.error !== null && failed.error !== undefined && <ErrorBanner error={failed.error} />}
      <p className="small">
        {failed.loading
          ? "読み込み中…"
          : records.length === 0
            ? "送れなかったものはありません。"
            : `送れなかったもの ${records.length} 件`}
      </p>
      {!failed.loading && records.length === 0 && (
        // **「送れなかったものはありません」で行き止まりに読ませない。** `送る`
        // 画面の「開始できなかった宛先」の案内はここへ誘導するが、そちらは
        // 失敗ゼロでも `pending` のまま止まった送信がある場合の話なので、
        // ボタンがまだ効くことを添える。
        <p className="small">
          失敗が無くても、止まっている送信をここから始め直せます。
        </p>
      )}
      {records.length === RETRY_PAGE && (
        <p role="note" className="small">
          一度に送り直せるのは {RETRY_PAGE} 件までです（ほかにもあります）。
        </p>
      )}
      {note !== null && (
        <p role="status" className="small">
          {note}
        </p>
      )}
      <div className="acts" style={{ marginTop: 10 }}>
        <button
          type="button"
          className="btn sm"
          aria-label={`送り直す：${destination.name}`}
          // 休止中の宛先は送信の対象にならないので、押しても何も起きない。
          disabled={busy || !destination.enabled}
          onClick={() => void resend()}
        >
          送り直す
        </button>
      </div>
    </section>
  );
}

function StackSkips({ destinationId }: { destinationId: string }) {
  const skipped = useQuery<SkippedStacks>(
    `/uploads?destination_id=${destinationId}&stack_state=skipped&limit=${SKIP_PAGE}`,
  );
  const records = skipped.data?.records ?? [];
  return (
    <section style={{ marginTop: 12 }}>
      <div className="sechead">
        <h3 style={{ fontSize: "13.5px", fontWeight: 600 }}>スタックの見送り</h3>
      </div>
      {skipped.error !== null && skipped.error !== undefined && (
        <ErrorBanner error={skipped.error} />
      )}
      {skipped.loading ? (
        <p className="small">読み込み中…</p>
      ) : records.length === 0 ? (
        <p className="small">見送りはありません。</p>
      ) : (
        <>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {records.map((record) => (
              <li key={record.id} className="small">
                {record.rel_path ? fileName(record.rel_path) : "ファイル名が読めません"}:{" "}
                {record.stack_reason ?? "理由不明"}
              </li>
            ))}
          </ul>
          {records.length === SKIP_PAGE && (
            <p role="note" className="small">
              先頭 {SKIP_PAGE} 件だけを出しています（ほかにもあります）。
            </p>
          )}
        </>
      )}
    </section>
  );
}

/**
 * 接続の設定（§12.3）。**`base_url` を必ず送るので、押すたびに新しいリビジョンが
 * 増える。** `name` と `enabled` だけの本文はリビジョンを作らない短絡路に入るため、
 * `0007` の回復手順（保存し直すと今の観測がリビジョンに入る）がそこでは動かない。
 *
 * **API キーは常に空から始める。** 読み出しの API を作らないので画面に既存の値は
 * 無く、空のまま保存したときは `api_key` を送らない（＝いまの鍵を変えない）。
 */
function ConnectionForm({
  destination,
  busy,
  onSave,
}: {
  destination: Destination;
  busy: boolean;
  onSave: (destination: Destination, event: React.FormEvent<HTMLFormElement>) => Promise<void>;
}) {
  return (
    <section style={{ marginTop: 12 }}>
      <div className="sechead">
        <h3 style={{ fontSize: "13.5px", fontWeight: 600 }}>接続の設定</h3>
      </div>
      <form
        aria-label={`接続の設定：${destination.name}`}
        onSubmit={(event) => void onSave(destination, event)}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 8 }}>
          <label className="formrow">
            接続先 URL
            <input
              className="field"
              name="base_url"
              aria-label={`接続先 URL：${destination.name}`}
              defaultValue={destination.base_url}
              required
            />
          </label>
          <label className="formrow">
            表示用 URL（任意）
            <input
              className="field"
              name="public_url"
              aria-label={`表示用 URL：${destination.name}`}
              defaultValue={destination.public_url ?? ""}
            />
          </label>
          <label className="formrow">
            新しい API キー（変えないときは空のまま）
            {/* **既存の値は出さない**（§12.3。読み出しの API を作らない）。 */}
            <input
              className="field"
              name="api_key"
              type="password"
              autoComplete="new-password"
              aria-label={`新しい API キー：${destination.name}`}
            />
          </label>
          <label className="formrow">
            向き先が同じライブラリかどうか
            {/* 接続先のホストが変わって `remote_user_id` が同じときだけ、
                サーバがこの答えを求める（§12.3 の `target_epoch`）。 */}
            <select
              className="field"
              name="same_library"
              aria-label={`向き先が同じライブラリかどうか：${destination.name}`}
              defaultValue=""
            >
              <option value="">決めていない</option>
              <option value="yes">同じライブラリ（送信済みの記録を引き継ぐ）</option>
              <option value="no">別のライブラリ（送信済みの記録を引き継がない）</option>
            </select>
          </label>
        </div>
        <div className="acts" style={{ marginTop: 12 }}>
          <button
            type="submit"
            className="btn sm"
            aria-label={`接続の設定を保存する：${destination.name}`}
            disabled={busy}
          >
            保存する
          </button>
        </div>
      </form>
    </section>
  );
}

/** 同じ向き先を指す宛先が他にあるか（§12.3。UNIQUE は置かず警告だけ）。 */
export function sharesLibrary(all: Destination[], one: Destination): boolean {
  if (one.remote_user_id === null) {
    return false;
  }
  return all.some((other) => other.id !== one.id && other.remote_user_id === one.remote_user_id);
}

export function DestinationsScreen() {
  const destinations = useQuery<Destinations>("/destinations");
  const refreshTasks = useDashboardReload();
  const edit = useMutation();
  const [archiving, setArchiving] = useState<Destination | null>(null);

  /**
   * 送り先を変えたら、**枠の「やること」も一緒に直す。**
   *
   * 送り先の増減・入り切りは「まだ送っていない」の数を動かすが、進捗の
   * イベントは出さない。枠は画面を移っても再マウントしないので、呼ばないと
   * ナビのバッジが古いまま残る。
   */
  function reloadAll() {
    destinations.reload();
    refreshTasks();
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // **待つ前に掴んでおく。** `await` の後の `currentTarget` は null になる
    // （React の合成イベントは処理が終わると要素への参照を落とす）。
    const element = event.currentTarget;
    const form = new FormData(element);
    await edit.run(async () => {
      await request("/destinations", {
        method: "POST",
        body: {
          name: String(form.get("name") ?? ""),
          base_url: String(form.get("base_url") ?? ""),
          public_url: form.get("public_url") ? String(form.get("public_url")) : null,
          api_key: String(form.get("api_key") ?? ""),
        },
      });
      element.reset();
      reloadAll();
    });
  }

  async function archive(destination: Destination) {
    await edit.run(async () => {
      await request(`/destinations/${destination.id}/archive`, { method: "POST" });
      reloadAll();
    });
    setArchiving(null);
  }

  /**
   * 接続の設定を保存する。**接続に関わる欄を必ず送る**ので、値を変えずに押しても
   * 新しいリビジョンが増え、そこに今の観測（`remote_user_id`）が入る。
   */
  async function saveConnection(
    destination: Destination,
    event: React.FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();
    // **待つ前に掴んでおく。** `await` の後の `currentTarget` は null になる。
    const element = event.currentTarget;
    const form = new FormData(element);
    const apiKey = String(form.get("api_key") ?? "");
    const sameLibrary = String(form.get("same_library") ?? "");
    const body: Record<string, unknown> = {
      base_url: String(form.get("base_url") ?? ""),
      public_url: form.get("public_url") ? String(form.get("public_url")) : null,
    };
    if (apiKey !== "") {
      // 空欄は「変えない」。送ると鍵を空にしてしまう。
      body.api_key = apiKey;
    }
    if (sameLibrary !== "") {
      body.same_library = sameLibrary === "yes";
    }
    await edit.run(async () => {
      await request(`/destinations/${destination.id}`, { method: "PATCH", body });
      // **鍵を画面に残さない。**
      element.reset();
      reloadAll();
    });
  }

  /**
   * 送り先 1 件への操作。失敗はバナーに出す（黙って何も起きないのを避ける）。
   *
   * **飛んでいる間は押させない。** `busy` を立てないと、ボタンの `disabled` は
   * 常に偽で、二重に送れてしまう（効かないガードは無いガードより悪い）。
   */
  async function act(path: string, options: { method: string; body?: unknown }) {
    await edit.run(async () => {
      await request(path, options);
      reloadAll();
    });
  }

  /**
   * 送れなかったものを送り直す。**一部が戻せなくても、戻せた分は送る**
   * （`work/Send.tsx` の「一部の宛先が失敗しても進める」と同じ考え方）。
   *
   * **1 件も戻せなくても、送信そのものは始め直す。** この操作は `pending` の
   * まま止まった記録を動かす唯一の手段で（`work/Send.tsx` の「開始できなかった
   * 宛先」の案内が指す先）、戻せた記録が無いことはそれをやめる理由にならない。
   * 戻せなかった理由は隠さずバナーへ出す。
   */
  async function resend(
    destination: Destination,
    recordIds: string[],
  ): Promise<{ retried: number; skipped: number } | null> {
    const outcome: { retried: number; skipped: number } = {
      retried: recordIds.length,
      skipped: 0,
    };
    const done = await edit.run(async () => {
      const results = await Promise.allSettled(
        recordIds.map((id) => request(`/uploads/${id}/retry`, { method: "POST" })),
      );
      const skipped = results.filter((result) => result.status === "rejected");
      if (skipped.length > 0) {
        // **戻せなかった理由を隠さない。** `run` は始めに 1 度だけ失敗を消すので、
        // ここで出した文言はこの後の成功で消えない。
        edit.fail(skipped[0].reason);
      }
      outcome.retried = recordIds.length - skipped.length;
      outcome.skipped = skipped.length;
      await request(`/destinations/${destination.id}/upload`, { method: "POST" });
      reloadAll();
    });
    return done ? outcome : null;
  }

  const all = destinations.data?.destinations ?? [];

  return (
    <section aria-label="送り先" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
      </div>
      <h1 className="page title-lg">送り先</h1>

      <ErrorBanner error={edit.error ?? destinations.error} onDismiss={edit.clear} />

      <section className="card pad">
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>送り先を追加する</h2>
        </div>
        {/* **名前を付ける。** 既にある送り先の「接続の設定」にも同じ見出しの欄が
            並ぶので、どちらの form かを読み上げでも区別できるようにする。 */}
        <form aria-label="送り先を追加する" onSubmit={(event) => void submit(event)}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <label className="formrow">
              名前
              <input className="field" name="name" required />
            </label>
            <label className="formrow">
              接続先 URL
              <input className="field" name="base_url" required placeholder="http://immich:2283" />
            </label>
            <label className="formrow">
              表示用 URL（任意）
              <input className="field" name="public_url" />
            </label>
            <label className="formrow">
              API キー
              {/* **既存の値は出さない**（§12.3。読み出しの API を作らない）。 */}
              <input className="field" name="api_key" type="password" required />
            </label>
          </div>
          <div className="acts" style={{ marginTop: 14 }}>
            <button type="submit" className="btn primary" disabled={edit.busy}>
              接続を検証して追加する
            </button>
          </div>
        </form>
      </section>

      {all.length === 0 && (
        <p className="muted">送り先はまだありません。上の欄で 1 件目を作れます。</p>
      )}

      {all.map((destination) => (
        <section key={destination.id} className="card pad">
          <div className="rowtop">
            <div className="grow">
              <h2 style={{ fontSize: 16, fontWeight: 650 }}>{destination.name}</h2>
              <p className="small" style={{ marginTop: 4 }}>
                {destination.enabled ? "使えます" : "休止中：送り先の候補に出ません"} ・{" "}
                {destination.base_url}
              </p>
              <p className="small" style={{ marginTop: 4 }}>
                最後に確かめた:{" "}
                {destination.verified_at === null
                  ? "まだ確かめていません"
                  : formatSystemDateTime(destination.verified_at)}
              </p>
              {sharesLibrary(all, destination) && (
                <p role="note" className="small" style={{ marginTop: 4 }}>
                  同じライブラリを指している送り先があります。
                </p>
              )}
            </div>
          </div>
          <ConnectionForm destination={destination} busy={edit.busy} onSave={saveConnection} />
          <Resend destination={destination} busy={edit.busy} onResend={resend} />
          <StackSkips destinationId={destination.id} />
          <div className="acts" style={{ marginTop: 14 }}>
            <button
              type="button"
              className="btn sm"
              aria-label={`名前を変える：${destination.name}`}
              disabled={edit.busy}
              onClick={() => {
                // **空の名前は送らない。** 名前は送り先を指す唯一の手掛かりで、
                // 空にすると一覧も確認の本文も名前の無い行になる（API も 400 で
                // 断るが、押せなくするのが一段目）。
                const typed = window.prompt("新しい名前", destination.name)?.trim();
                if (typed && typed !== destination.name) {
                  void act(`/destinations/${destination.id}`, {
                    method: "PATCH",
                    body: { name: typed },
                  });
                }
              }}
            >
              名前を変える
            </button>
            <button
              type="button"
              className="btn sm"
              aria-label={`${destination.enabled ? "休止する" : "使う"}：${destination.name}`}
              disabled={edit.busy}
              onClick={() =>
                void act(`/destinations/${destination.id}`, {
                  method: "PATCH",
                  body: { enabled: !destination.enabled },
                })
              }
            >
              {destination.enabled ? "休止する" : "使う"}
            </button>
            <button
              type="button"
              className="btn sm"
              aria-label={`つながるか確かめる：${destination.name}`}
              disabled={edit.busy}
              onClick={() =>
                void act(`/destinations/${destination.id}/verify`, { method: "POST" })
              }
            >
              つながるか確かめる
            </button>
            <button
              type="button"
              className="btn sm"
              aria-label={`状態を再確認する：${destination.name}`}
              disabled={edit.busy}
              onClick={() =>
                void act(`/destinations/${destination.id}/recheck`, { method: "POST" })
              }
            >
              状態を再確認する
            </button>
            <button
              type="button"
              className="btn sm quiet"
              aria-label={`退役させる：${destination.name}`}
              disabled={edit.busy}
              onClick={() => setArchiving(destination)}
            >
              退役させる
            </button>
          </div>
        </section>
      ))}

      {archiving && (
        <ConfirmDialog
          confirmation={{ kind: "archive_destination", name: archiving.name }}
          busy={edit.busy}
          onCancel={() => setArchiving(null)}
          onConfirm={() => void archive(archiving)}
        />
      )}
    </section>
  );
}
