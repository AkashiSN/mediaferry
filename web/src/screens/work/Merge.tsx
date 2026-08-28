// つなぐ（§13）。**なぜグループ化されたかが分かる**ように、構成・ギャップ・パートサイズを出す。
//
// 操作できない記録（破棄した組み合わせ・使っていない出力）はここに出さない。混ざると
// 「いま何が起きるのか」が読めなくなるため、設定 › 詳しい情報（`details/MergeHistory.tsx`）
// に置く。

import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useDashboardReload, useDefaultTimezone } from "../../api/dashboard";
import { useMutation, useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import type { Confirmation } from "../../components/ConfirmDialog";
import { BackLink } from "../../components/BackLink";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { useDialogFocus } from "../../components/useDialogFocus";
import { useEvents } from "../../hooks/useEvents";
import { useReloadOnEvents } from "../../hooks/useReloadOnEvents";
import { formatBytes } from "../../utils/formatBytes";
import { formatCapturedDateTime } from "../../utils/formatDateTime";

export type Member = {
  position: number;
  media_file_id: string;
  rel_path: string;
  size_bytes: number;
  duration_seconds: number | null;
  captured_at: string;
  captured_at_tz: string | null;
};
/**
 * 検証の 1 項目（`core/merge/verify.py` の `Check`）。`verdict` は
 * `pass` / `fail` / `inconclusive` のいずれか。
 */
type VerificationCheck = { name: string; verdict: string };
/**
 * 結合結果の検証（§9.8）。**合否は `passed` に入る。**
 * トップレベルの `verdict` や `reason` は存在しない —— `db/selection.py` の
 * `SENDABLE_CLAUSE` が送れるかどうかを判断するのも `passed` である。
 */
export type Verification = {
  passed: boolean;
  checks: VerificationCheck[];
  // 経路のコンテナが運べずに外したストリーム（TS 経路の `tmcd` など）。
  route_dropped_streams: unknown[];
};
export type Group = {
  id: string;
  status: string;
  detected_by: string;
  input_digest: string;
  verification: Verification | null;
  adopted_at: string | null;
  superseded_by_id: string | null;
  /** 作ったときからカメラの種類が変わったか（`db/selection.py` の
   * `group_is_current` は変わっていたら必ず断る）。 */
  profile_changed: boolean;
  output: { media_file_id: string; rel_path: string; size_bytes: number; missing: boolean } | null;
  members: Member[];
};

/** 検査の名前を画面の言葉にする（内部の名前をそのまま出さない。§13）。 */
function checkLabel(name: string): string {
  switch (name) {
    case "duration":
      return "長さ";
    case "streams":
      return "中身の構成";
    case "frames":
      return "コマ数";
    case "size":
      return "ファイルの大きさ";
    default:
      return "そのほかの検査";
  }
}

/** 検査の結果を画面の言葉にする。判定不能は合否に使わないので、そう書く。 */
function verdictLabel(verdict: string): string {
  if (verdict === "pass") {
    return "合っています";
  }
  if (verdict === "fail") {
    return "合いません";
  }
  return "確かめられませんでした";
}

/**
 * 不合格の理由を 1 文にする（採用の確認に渡す）。
 *
 * **判定不能は理由に数えない。** 合否に使っていないものを理由にすると、
 * 「合わなかった」と読める文が実際には合っていた検査を指してしまう。
 */
export function failureReason(verification: Verification): string {
  const failed = (verification.checks ?? [])
    .filter((check) => check.verdict === "fail")
    .map((check) => checkLabel(check.name));
  return failed.length === 0 ? "検証に通っていません" : `${failed.join(" / ")}が合いません`;
}

/** 検証の結果。**採用の判断ができるように、検査ごとに出す**（§13「検証結果」）。 */
export function VerificationResult({ verification }: { verification: Verification }) {
  const dropped = verification.route_dropped_streams ?? [];
  return (
    <div className="small" style={{ marginTop: 6 }}>
      <p>検証: {verification.passed ? "合格" : "不合格"}</p>
      <ul style={{ listStyle: "none", padding: 0, marginTop: 3 }}>
        {(verification.checks ?? []).map((check) => (
          <li key={check.name}>
            {checkLabel(check.name)}: {verdictLabel(check.verdict)}
          </li>
        ))}
      </ul>
      {dropped.length > 0 && (
        <p style={{ marginTop: 3 }}>
          つなぎ方の都合で運べなかったものが {dropped.length} 件あります。
        </p>
      )}
    </div>
  );
}

type Groups = { groups: Group[] };

/** `position` 順に並べる。**API の順を信用しない**（表示も計算もこの順で行う）。 */
export function ordered(members: Member[]): Member[] {
  return [...members].sort((a, b) => a.position - b.position);
}

/** 入っていれば外し、入っていなければ入れた**新しい**集合。**同じ物を書き換えない**
 * （React は同一性で描き直しを決める）。 */
function toggled(current: Set<string>, id: string): Set<string> {
  const next = new Set(current);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return next;
}

/** 一度に出す組の数（API の `GET /merge-groups` の既定と同じ）。**打ち切ったことは黙らない。** */
export const MERGE_PAGE = 200;

/** 端数の丸めで生じる程度の重なりは、隙間なしとして扱う（秒）。 */
const OVERLAP_TOLERANCE_SECONDS = 1;

/**
 * パート間でいちばん大きい空白（秒）。**これが「なぜ同じ 1 本と判断したか」の根拠**。
 *
 * 測れないつなぎ目は次のとき。
 *
 * - 隣り合うどちらかの長さが読めない（`duration_seconds === null`）
 * - 時刻が読めない（`captured_at` を解釈できない）
 * - パートが重なって見える —— `captured_at` が撮影の開始ではないときに起きる
 *   （カメラの種類の既定は `source: "mtime"` で、これはクリップの**終端**）
 *
 * **測れたつなぎ目は、別のつなぎ目が測れなかったからといって捨てない。** 捨てると、
 * 大きい空白（＝別の撮影かもしれない）を測れていたのに、取り消せない結合の直前で
 * 警告そのものが消える。
 *
 * **測れなかったものを 0 にはしない。** 0 は「隙間なく続いている」という積極的な
 * 主張で、取り消せない結合をその根拠で確認させることになる。だから 0 と言えるのは
 * **すべてのつなぎ目が測れて、どれも 0 だったとき**だけ。それ以外で測れない
 * つなぎ目が混ざっていれば `null`（分からない）を返す。
 */
function gapSeconds(members: Member[]): number | null {
  const sorted = ordered(members);
  let max: number | null = null;
  let unmeasurable = false;
  for (let i = 1; i < sorted.length; i += 1) {
    const previous = sorted[i - 1];
    if (previous.duration_seconds === null) {
      unmeasurable = true;
      continue;
    }
    const previousEnd = Date.parse(previous.captured_at) + previous.duration_seconds * 1000;
    const gap = (Date.parse(sorted[i].captured_at) - previousEnd) / 1000;
    if (Number.isNaN(gap) || gap < -OVERLAP_TOLERANCE_SECONDS) {
      unmeasurable = true;
      continue;
    }
    max = Math.max(max ?? 0, gap);
  }
  if (max !== null && max > 0) {
    return Math.round(max * 10) / 10;
  }
  // ここに来る max は 0 か null。**0 と言い切れるのは、全部測れたときだけ。**
  return unmeasurable || max === null ? null : 0;
}

function totalBytes(members: Member[]): number {
  return members.reduce((sum, member) => sum + member.size_bytes, 0);
}

function totalMinutes(members: Member[]): number {
  const seconds = members.reduce((sum, member) => sum + (member.duration_seconds ?? 0), 0);
  return Math.round(seconds / 60);
}

/**
 * 採用できる組か（`db/merges.py` の `adopt` が受け付ける条件と揃える）。
 * 組み直された組と、既に採用した組には出さない —— 押しても 409 か無反応になる。
 */
export function adoptable(group: Group): boolean {
  return (
    // **カメラの種類が変わった組には出さない。** 採用しても `group_is_current`
    // が断るので、押しても送れるようにはならない。
    !group.profile_changed &&
    group.status === "merged" &&
    group.superseded_by_id === null &&
    group.adopted_at === null &&
    group.output !== null &&
    group.verification !== null &&
    !group.verification.passed
  );
}

export function MergeScreen() {
  // **1 件多く読む。** ちょうど上限の件数だったときに「ほかにもあります」と
  // 書かないため（読めた数だけでは、切れたのか出し切ったのかが分からない）。
  // **まだつないでいないものだけ。** 結合済みへの操作は、その 1 本を見ている
  // 画面（`/photos/:id`）にある。**絞るのはサーバ側** —— 手元で外すと、
  // 上限の 200 件を結合済みが埋めて「ほかにもあります」が出ない。
  const groups = useQuery<Groups>(`/merge-groups?pending=true&limit=${MERGE_PAGE + 1}`);
  // 手で組むときの選択肢（**検出が拾えなかった並びを人が組む**）。
  const media = useQuery<{ media: { id: string; rel_path: string }[] }>("/media?page_size=200");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [regrouping, setRegrouping] = useState<Group | null>(null);
  const edit = useMutation();
  const { received } = useEvents();
  // 時刻に添える印（§13）。読めていなければ `null`（システム時刻は UTC のまま出す）。
  const defaultZone = useDefaultTimezone();
  useReloadOnEvents(received, groups.reload);
  const [confirmation, setConfirmation] = useState<{
    value: Confirmation;
    run: () => Promise<unknown>;
  } | null>(null);
  const refreshTasks = useDashboardReload();
  const navigate = useNavigate();

  /** 成功したかを返す。**失敗したのに後片付けを進めない**ため。 */
  async function act(
    path: string,
    body?: unknown,
    method?: "POST" | "PATCH" | "DELETE",
    /** **ジョブを積む操作だけ true。** 進捗の置き場はホームなので、押した
     *  瞬間に画面が変わらず「失敗した」と読まれないよう、積んだジョブの id を
     *  持ってそこへ送る。検出は候補がこの画面に出るので連れ出さない。 */
    handOff?: boolean,
  ): Promise<boolean> {
    let queued: string | null = null;
    const done = await edit.run(async () => {
      const started = (await request(path, {
        method: method ?? (path.includes("?action=") ? "PATCH" : "POST"),
        body,
      })) as { job_id?: string } | null;
      queued = started?.job_id ?? null;
    });
    if (done) {
      groups.reload();
      // 破棄・採用・組み直しは進捗のイベントを出さない。**枠の「やること」も
      // 一緒に直す。**
      refreshTasks();
    }
    setConfirmation(null);
    if (done && handOff && queued) {
      navigate("/", { state: { jobIds: [queued], note: null } });
    }
    return done;
  }

  function renderGroup(group: Group) {
    const members = ordered(group.members);
    const gap = gapSeconds(members);
    const warn = gap !== null && gap > 0;
    return (
      <article key={group.id} className={`card pad${warn ? " wn" : ""}`}>
        <div className="rowtop" style={{ flexWrap: "wrap" }}>
          <div className="parts">
            {members.map((member, index) => (
              <div key={member.media_file_id} className={`t${index % 8}`}>
                <Icon name="play" />
              </div>
            ))}
          </div>
          <div className="grow">
            <h2 style={{ fontSize: "15.5px", fontWeight: 650 }}>
              {members.length} つに分かれています
              {group.detected_by === "manual" ? "（手動）" : ""}
              {group.status === "failed" ? "・結合に失敗しました" : ""}
            </h2>
            <p className="muted" style={{ marginTop: 4 }}>
              合計 {formatBytes(totalBytes(members))} ・ つなぐと 1 本（約 {totalMinutes(members)} 分）
            </p>
            <p className="small" style={{ marginTop: 3, color: warn ? "var(--warn)" : undefined }}>
              {gap === null
                ? "パートの時刻と長さからは、つなぎ目の空白は分かりません。"
                : warn
                  ? `つなぎ目に ${gap} 秒の空白があります。別の撮影かもしれないので、確かめてから決めてください。`
                  : "連番が続いていて、つなぎ目の空白は 0.0 秒です。だから同じ 1 本と判断しました。"}
            </p>
            <ul style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10, listStyle: "none" }}>
              {members.map((member) => (
                <li key={member.media_file_id} className="row" style={{ justifyContent: "space-between" }}>
                  <span className="ident" style={{ fontSize: 13 }}>
                    {member.rel_path}
                  </span>
                  <span className="small">
                    {formatBytes(member.size_bytes)} ・ {formatCapturedDateTime(member.captured_at, member.captured_at_tz, defaultZone)} ・
                    {member.duration_seconds === null ? "—" : ` ${Math.round(member.duration_seconds)} 秒`}
                  </span>
                </li>
              ))}
            </ul>
            {group.verification && <VerificationResult verification={group.verification} />}
          </div>
          <div className="acts">
            {group.status === "detected" && (
              <button
                type="button"
                className="btn outline"
                disabled={edit.busy}
                onClick={() => void act(`/merge-groups/${group.id}/merge`, undefined, undefined, true)}
              >
                つなぐ
              </button>
            )}
            {group.status === "failed" && (
              <button
                type="button"
                className="btn warnish"
                disabled={edit.busy}
                onClick={() => void act(`/merge-groups/${group.id}/merge`, undefined, undefined, true)}
              >
                再試行する
              </button>
            )}
            {group.status === "failed" && (
              // 裁定 12: 結合に失敗した組は §10 の既定の一覧から外れ、送る手段が無い。
              // ここから member を対象にした送るへ進めるようにする。
              <button
                type="button"
                className="btn sm"
                disabled={edit.busy}
                onClick={() =>
                  navigate("/send", { state: { ids: members.map((member) => member.media_file_id) } })
                }
              >
                個別に送る
              </button>
            )}
            {group.superseded_by_id === null && (
              <button type="button" className="btn sm" disabled={edit.busy} onClick={() => setRegrouping(group)}>
                構成を変える
              </button>
            )}
            {group.superseded_by_id === null && (
              <button
                type="button"
                className="btn sm"
                disabled={edit.busy}
                onClick={() =>
                  setConfirmation({
                    value: {
                      kind: "discard_merge_group",
                      groupLabel: members[0]?.rel_path ?? group.id,
                      publishedCount: group.status === "merged" ? 1 : 0,
                    },
                    run: () => act(`/merge-groups/${group.id}?action=discard`),
                  })
                }
              >
                これは別々
              </button>
            )}
          </div>
        </div>
      </article>
    );
  }

  const found = groups.data?.groups ?? [];
  const truncated = found.length > MERGE_PAGE;
  const rows = truncated ? found.slice(0, MERGE_PAGE) : found;

  return (
    <section aria-label="つなぐ" className="wrap">
      <BackLink />
      <div>
        <h1 className="page title-lg">
          分かれている動画を 1 本につなぎます
        </h1>
        <p className="muted" style={{ marginTop: 7 }}>
          カメラは長い動画を、ある大きさごとに区切って保存します。ここでつないでおくと、
          Immich には 1 本の動画として並びます。つないだあとも、元の分かれたファイルは
          NAS に残ります。
        </p>
        {/* **行き止まりにしない**（§13）。この画面はこれからつなぐものだけを出すので、
            つないだ結果がどこにあるかを書く。**構成を変える・別々にするといった
            操作も、つないだ後はその 1 本のくわしくで行う。** */}
        <p className="muted" style={{ marginTop: 7 }}>
          つないだ動画はここには出ません。{" "}
          <Link to="/photos?role=derived" className="btn sm quiet">
            写真 › つないだ動画
          </Link>{" "}
          で見て、構成を変えたり別々に戻したりできます。
        </p>
      </div>

      <ErrorBanner error={edit.error ?? groups.error} onDismiss={edit.clear} />

      {/* 裁定 20: ホームの「やること」は全件を数えるので、ここが上限で切れている
          ことを言わないと、いくら片付けても数が合わないように見える。 */}
      {truncated && (
        <p role="note" className="small">
          先頭 {MERGE_PAGE} 件だけを出しています（ほかにもあります）。
        </p>
      )}

      <div>
        <button type="button" className="btn sm" disabled={edit.busy} onClick={() => void act("/merge-groups/detect")}>
          分かれた動画を探す
        </button>
      </div>

      <details>
        <summary>手でグループを作る</summary>
        <p className="small" style={{ marginTop: 8 }}>
          検出が拾えなかった並びを、2 件以上選んで組みます。
        </p>
        <ul style={{ display: "flex", flexDirection: "column", gap: 6, listStyle: "none" }}>
          {(media.data?.media ?? []).map((row) => (
            <li key={row.id}>
              <label className="row">
                <input
                  type="checkbox"
                  checked={picked.has(row.id)}
                  onChange={() =>
                    setPicked((current) => toggled(current, row.id))
                  }
                />
                {row.rel_path}
              </label>
            </li>
          ))}
        </ul>
        <button
          type="button"
          className="btn sm"
          disabled={edit.busy || picked.size < 2}
          // **失敗したら選択を残す。** 消すと、やり直すのに選び直しからになる
          // （失敗そのものは上の帯で知らせている）。
          onClick={() =>
            void act("/merge-groups", { media_ids: [...picked] }).then((ok) => {
              if (ok) {
                setPicked(new Set());
              }
            })
          }
        >
          選んだ {picked.size} 件でグループを作る
        </button>
      </details>

      {rows.length === 0 ? (
        <div className="card pad empty">
          <h2 style={{ fontSize: 17, fontWeight: 650 }}>つなぐものはありません</h2>
          <p className="muted" style={{ marginTop: 6 }}>
            分かれた動画が見つかると、ここに出ます。
          </p>
        </div>
      ) : (
        rows.map(renderGroup)
      )}

      {regrouping && (
        <RegroupDialog
          group={regrouping}
          onCancel={() => setRegrouping(null)}
          onSubmit={(mediaIds) => {
            const target = regrouping;
            setRegrouping(null);
            void act(`/merge-groups/${target.id}?action=regroup`, { media_ids: mediaIds });
          }}
        />
      )}

      {confirmation && (
        <ConfirmDialog
          confirmation={confirmation.value}
          busy={edit.busy}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => void confirmation.run()}
        />
      )}
    </section>
  );
}

/**
 * 構成を変える（§13）。**残すパートを選び直し、新しいグループを作る。**
 *
 * **2 件以上でなければ確定できない**（`POST /merge-groups` と同じ条件。API も
 * 400 で断る）。1 件だけの組にはつなぎ目が無く、つなぎようがない。
 *
 * **二重送信を止めているのは「押した瞬間に閉じる」こと。** 押せなさではない
 * （このダイアログを開くボタン自体が `busy` の間は押せず、開いている間に `busy`
 * を真にできる経路も無い）。
 */
export function RegroupDialog({
  group,
  onCancel,
  onSubmit,
}: {
  group: Group;
  onCancel: () => void;
  onSubmit: (mediaIds: string[]) => void;
}) {
  const members = ordered(group.members);
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(members.map((member) => member.media_file_id)),
  );
  const dialog = useRef<HTMLDivElement>(null);
  useDialogFocus(dialog, onCancel);

  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog" ref={dialog} role="dialog" aria-modal="true" aria-label="構成を変える">
        <h2>構成を変える</h2>
        <p>
          残すパートを選びます。新しいグループを作り、いまのグループはそちらへ
          向け直します（公開済みのファイルは消えません）。
        </p>
        <ul>
          {members.map((member) => (
            <li key={member.media_file_id}>
              <label>
                <input
                  type="checkbox"
                  checked={picked.has(member.media_file_id)}
                  onChange={() =>
                    setPicked((current) => toggled(current, member.media_file_id))
                  }
                />
                {member.rel_path}
              </label>
            </li>
          ))}
        </ul>
        {picked.size < 2 && <p className="small">つなぐには 2 件以上えらんでください。</p>}
        <div className="dialog-actions">
          <button type="button" onClick={onCancel}>
            やめる
          </button>
          <button
            type="button"
            disabled={picked.size < 2}
            onClick={() =>
              onSubmit(
                members
                  .map((member) => member.media_file_id)
                  .filter((id) => picked.has(id)),
              )
            }
          >
            この構成にする
          </button>
        </div>
      </div>
    </div>
  );
}
