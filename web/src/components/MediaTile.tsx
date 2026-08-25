// 写真 1 枚のタイル（§13）。プロトタイプの `tile()` から色・間隔を取る。
//
// **状態の印は形と文字の両方で伝える。** 色だけにすると、色覚特性や白黒印刷で
// 区別が付かない：未送信は白縁の丸、送信済みはチェック、確認が要るは「!」、
// 送れなかったものは「×」。

import { Link } from "react-router-dom";

import { Icon } from "./Icon";

export type MediaStatus = "unsent" | "awaiting" | "sent" | "failed" | null;

/** 組（RAW+JPEG）に含まれる 1 行。`GET /media?collapse=stack` の主の行が持つ。 */
export type StackMember = { id: string; rel_path: string; size_bytes: number };

export type Media = {
  id: string;
  rel_path: string;
  kind: string;
  captured_at: string;
  size_bytes: number;
  duration_seconds?: number | null;
  status?: MediaStatus;
  role?: "original" | "derived";
  /** 組（RAW+JPEG）。**主の行にだけ付く**（`GET /media?collapse=stack`）。曖昧な組
   * （大小文字違いが混ざる等）は Immich でも組まれないので、どの行にも付かない。 */
  stack?: { members: StackMember[] } | null;
};

/** タイルが実際に読む部分だけ。**一覧の 1 行から直に描ける**ようにするため、
 * 撮影時刻や大きさは要求しない（`確認` は `GET /uploads` の行だけで描く）。 */
export type TileMedia = Pick<Media, "id" | "rel_path"> &
  Partial<Pick<Media, "kind" | "duration_seconds" | "status" | "role" | "stack">>;

/** `rel_path` の末尾（ファイル名）。**内部の相対パスを画面に出さない**（§13）ので、
 * タイルの `aria-label` も、ホームの「さっき取り込んだもの」もここを通す。 */
export function fileName(relPath: string): string {
  const parts = relPath.split("/");
  return parts[parts.length - 1];
}

/** 動画の長さを `分:秒` に丸める（タイルの右下に出す値）。**進捗の「残り約 N 分」
 * とは別物**（`components/JobProgress.tsx` の `formatRemaining`）。 */
function formatClipLength(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function MediaTile({
  media,
  selected,
  onToggle,
  to,
}: {
  media: TileMedia;
  selected: boolean;
  onToggle?: (id: string) => void;
  /** 押したときに開く先。**選ぶのとは別の的**（隅の丸が選ぶ）。 */
  to?: string;
}) {
  const name = fileName(media.rel_path);
  const hasDuration = media.kind === "video" && media.duration_seconds != null;
  const inside = (
    <>
      <img src={`/api/media/${media.id}/thumbnail`} alt="" loading="lazy" className="tileimg" />
      {/* **「つないだ」と `RAW` は独立に立つ。** 出す条件が `role` と `stack` で
          別々なので、両方立つ行がありうる。同じ座標へ重ねると片方が読めなくなる
          ため、1 つの入れ物へ入れて横に並べる（`styles.css` の `.madeofs`）。
          **どちらかを捨てない** —— 「つないだ動画」と「組の主」は別の事実で、
          片方を隠すとタイルがその行を名乗り損ねる。 */}
      {(media.role === "derived" || media.stack) && (
        <span className="madeofs">
          {media.role === "derived" && <span className="madeof">つないだ</span>}
          {media.stack && <span className="madeof raw">RAW</span>}
        </span>
      )}
      <StatusMark status={media.status ?? null} />
      {hasDuration && (
        <span className="dur">{formatClipLength(media.duration_seconds as number)}</span>
      )}
    </>
  );

  // **押せないタイルをボタンにしない。** `disabled` なボタンは読み上げの木から
  // 外れるので、確認の下見（`work/Send.tsx`）に並ぶ写真のファイル名が、目で
  // 見ている人にしか届かなくなる。押せないなら、それは絵である。
  if (!to && !onToggle) {
    return (
      <span className={`tile${selected ? " sel" : ""}`} title={name} role="img" aria-label={name}>
        {inside}
      </span>
    );
  }
  // **的を入れ子にしない。** `<a>` の中の `<button>` は不正な HTML なので、
  // 全面を覆うリンクと、その上に載る丸を**兄弟**に並べる。
  return (
    <div className={`tile${selected ? " sel" : ""}`} title={name}>
      {inside}
      {to && <Link className="tilehit" to={to} aria-label={name} />}
      {onToggle && (
        <button
          type="button"
          className={`pick${selected ? " on" : ""}`}
          aria-label={`選ぶ：${name}`}
          aria-pressed={selected}
          onClick={() => onToggle(media.id)}
        >
          {selected && <Icon name="check" size={12} />}
        </button>
      )}
    </div>
  );
}

/** 宛先ごとの状態の印。分からないとき（絞り込みが宛先を伴わないとき）は出さない。 */
function StatusMark({ status }: { status: MediaStatus }) {
  if (status === "sent") {
    return (
      <span className="mark sent">
        <Icon name="check" size={11} />
      </span>
    );
  }
  if (status === "awaiting") {
    return <span className="mark awaiting">!</span>;
  }
  if (status === "failed") {
    return <span className="mark failed">×</span>;
  }
  if (status === "unsent") {
    return <span className="mark unsent" />;
  }
  return null;
}
