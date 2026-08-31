// 確認（§13）。**現在値と変更案を並べて**、1 件ずつのカードで出す。
//
// 判断（承認・却下の条件、承認だけ確認を要ること）は変えない。読めなかった値を
// 空欄にしない（空欄は「変更なし」に見える）。
//
// **ファイル名とサムネイルは `GET /uploads` の行から描く。** 1 件ずつ
// `GET /media/{id}` を引くと、上限いっぱいの 200 件で 200 本の要求になり、
// 承認のたびに全件を引き直すことになる。

import { useState } from "react";

import { request } from "../../api/client";
import { useDashboardReload, useDefaultTimezone } from "../../api/dashboard";
import { useMutation, useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { BackLink } from "../../components/BackLink";
import { ErrorBanner } from "../../components/ErrorBanner";
import { fileName, MediaTile } from "../../components/MediaTile";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatCapturedDateTime, formatSystemDateTime } from "../../utils/formatDateTime";

type Record_ = {
  id: string;
  destination_id: string;
  media_file_id: string;
  // 一覧が持つファイルの位置（§13 のためにファイル名を出す。内部の ID は出さない）。
  rel_path: string | null;
  origin: string;
  remote_current: string | null;
  captured_at_tz: string | null;
  proposed: string | null;
  remote_checked_at: string | null;
  identical: boolean;
};

type Records = { records: Record_[] };

/** 一度に出す件数（API の `GET /uploads` の既定と同じ）。**打ち切ったことは黙らない。** */
export const APPROVE_PAGE = 200;
type Destination = { id: string; name: string };
type Destinations = { destinations: Destination[] };

export function ApproveScreen() {
  // **1 件多く読む。** ちょうど上限の件数だったときに「ほかにもあります」と
  // 書かないため（読めた数だけでは、切れたのか出し切ったのかが分からない）。
  const records = useQuery<Records>(
    `/uploads?state=awaiting_datetime_approval&limit=${APPROVE_PAGE + 1}`,
  );
  // **どの Immich を書き換えるのかを名前で出すため**に引く（§13。送り先が
  // 2 つあると、id だけでは画面から判別できない）。
  const destinations = useQuery<Destinations>("/destinations");
  const decision = useMutation();
  // 時刻に添える印（§13）。両側とも提案のオフセットで並ぶので、印は 1 つでよい。
  const defaultZone = useDefaultTimezone();
  const { received } = useEvents();
  useReloadOnEvents(received, records.reload);
  const [approving, setApproving] = useState<Record_ | null>(null);
  const refreshTasks = useDashboardReload();

  async function act(record: Record_, action: "approve" | "reject") {
    if (await decision.run(() => request(`/uploads/${record.id}/${action}`, { method: "POST" }))) {
      records.reload();
      // 却下は進捗のイベントを出さない。**枠の「やること」も一緒に直す。**
      refreshTasks();
    }
    setApproving(null);
  }

  const found = records.data?.records ?? [];
  const truncated = found.length > APPROVE_PAGE;
  const rows = truncated ? found.slice(0, APPROVE_PAGE) : found;
  /** 書き換えの可否を決める行が 1 つでもあるか。**見出しの言い方がこれで決まる。** */
  const needsDecision = rows.some((record) => !record.identical);

  /** 送り先の表示名。引けないときも**内部の ID は出さない**（§13）。 */
  function destinationName(id: string): string {
    return (
      (destinations.data?.destinations ?? []).find((row) => row.id === id)?.name ?? "分かりません"
    );
  }

  return (
    <section aria-label="確認" className="wrap">
      <BackLink />
      <div>
        <h1 className="page title-lg">
          写真の日時を直していいか確かめます
        </h1>
        <p className="muted" style={{ marginTop: 7 }}>
          下の写真は、先に誰かが Immich へ上げていたものです。mediaferry は自分が上げた写真の
          日時しか直しません。
          {/* **変えるものが無いなら、決断を迫らない。** 変更のある行が 1 つも無い
              一覧に「決めてください」と書くと、決めることが無いのに決断を求めた
              ことになる。 */}
          {needsDecision ? (
            <>
              書き換えていいかどうかを決めてください。
              <b>却下しても、Immich には何も起きません。</b>
            </>
          ) : (
            <>
              日時は既に合っているので、決めることはありません。
              <b>片付けても、Immich には何も起きません。</b>
            </>
          )}
        </p>
      </div>

      <ErrorBanner error={decision.error ?? records.error} onDismiss={decision.clear} />

      {/* 裁定 20: ホームの件数は全件を数えるので、ここが上限で切れていることを
          言わないと、いくら片付けても数が合わないように見える。 */}
      {truncated && (
        <p role="note" className="small">
          先頭 {APPROVE_PAGE} 件だけを出しています（ほかにもあります）。
        </p>
      )}

      {rows.length === 0 ? (
        <div className="card pad empty">
          <h2 style={{ fontSize: 17, fontWeight: 650 }}>確認するものはありません</h2>
        </div>
      ) : (
        rows.map((record) => (
          <article key={record.id} className="card pad">
            <div className="rowtop" style={{ flexWrap: "wrap" }}>
              {/* **どの写真かを見せる。** リモートの書き換えは取り消せないので、
                  対象を見ないまま承認させない（§13）。 */}
              {record.rel_path && (
                // `.tile` は `aspect-ratio: 1` で幅を親から取るので、幅を持つ枠に置く
                // （`work/Send.tsx` の下見と同じ考え方）。
                <div style={{ width: 72, flex: "0 0 auto" }}>
                  <MediaTile
                    media={{ id: record.media_file_id, rel_path: record.rel_path }}
                    selected={false}
                  />
                </div>
              )}
              <div className="grow">
                {/* 内部の ID をそのまま出さない（§13）。読めないときはそう書く。 */}
                <h2 style={{ fontSize: 15.5, fontWeight: 650 }}>
                  {record.rel_path ? fileName(record.rel_path) : "ファイル名が読めません"}
                </h2>
                <p className="small" style={{ marginTop: 4, marginBottom: 8 }}>
                  送り先: {destinationName(record.destination_id)}
                </p>
                <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
                  <div>
                    <div className="small">Immich にある日時</div>
                    <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2 }}>
                      {/* 読めなかった値は空欄にしない（「変更なし」に見えてしまう）。 */}
                      {record.remote_current === null
                        ? "（読めませんでした）"
                        : formatCapturedDateTime(
                            record.remote_current,
                            record.captured_at_tz,
                            defaultZone,
                          )}
                    </div>
                  </div>
                  <div style={{ alignSelf: "center", color: "var(--ink-3)" }}>→</div>
                  <div>
                    <div className="small">直したい日時</div>
                    <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2, color: "var(--accent-deep)" }}>
                      {record.proposed === null
                        ? "—"
                        : formatCapturedDateTime(record.proposed, record.captured_at_tz, defaultZone)}
                    </div>
                  </div>
                </div>
                <p className="small" style={{ marginTop: 8 }}>
                  観測した時刻: {record.remote_checked_at
                    ? formatSystemDateTime(record.remote_checked_at, defaultZone)
                    : "—"}
                </p>
              </div>
              <div className="acts">
                {record.identical ? (
                  // **ボタンと同じ重さで出す。** ここは「承認する」があった場所
                  // なので、小さな地の文にすると目が滑る。
                  <span className="state">変更なし</span>
                ) : (
                  <button type="button" className="btn outline" disabled={decision.busy} onClick={() => setApproving(record)}>
                    承認する
                  </button>
                )}
                {/* **叩く先は同じ**（却下）。**呼び方だけを変える** —— 却下は
                    「変更を拒む」意味なので、変えるものが無い行の語彙ではない。 */}
                <button type="button" className="btn sm" disabled={decision.busy} onClick={() => void act(record, "reject")}>
                  {record.identical ? "片付ける" : "却下する"}
                </button>
              </div>
            </div>
          </article>
        ))
      )}

      {approving && (
        <ConfirmDialog
          confirmation={{
            kind: "approve_datetime",
            // 確認にも読める形で出す（画面の表示と同じ言い方にする）。
            current:
              approving.remote_current === null
                ? null
                : formatCapturedDateTime(
                    approving.remote_current,
                    approving.captured_at_tz,
                    defaultZone,
                  ),
            // カードの表示（「—」）と揃える。空文字だと「変更後 」で文が途切れる。
            proposed:
              approving.proposed === null
                ? "—"
                : formatCapturedDateTime(approving.proposed, approving.captured_at_tz, defaultZone),
          }}
          busy={decision.busy}
          onCancel={() => setApproving(null)}
          onConfirm={() => void act(approving, "approve")}
        />
      )}
    </section>
  );
}
