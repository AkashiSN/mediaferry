// 写真 1 枚のタイル（§13）。プロトタイプの `tile()` から色・間隔を取る。
//
// **状態の印は形と文字の両方で伝える。** 色だけにすると、色覚特性や白黒印刷で
// 区別が付かない：未送信は白縁の丸、送信済みはチェック、確認が要るは「!」、
// 送れなかったものは「×」。

import { Icon } from "./Icon";

export type MediaStatus = "unsent" | "awaiting" | "sent" | "failed" | null;

export type Media = {
  id: string;
  rel_path: string;
  kind: string;
  captured_at: string;
  size_bytes: number;
  duration_seconds?: number | null;
  status?: MediaStatus;
};

/** `rel_path` の末尾（ファイル名）。ボタンの `aria-label` に使う。 */
function fileName(relPath: string): string {
  const parts = relPath.split("/");
  return parts[parts.length - 1];
}

/** 秒を `分:秒` に丸める（プロトタイプの `dur` と同じ形）。 */
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function MediaTile({
  media,
  selected,
  onToggle,
}: {
  media: Media;
  selected: boolean;
  onToggle?: (id: string) => void;
}) {
  const name = fileName(media.rel_path);
  const hasDuration = media.kind === "video" && media.duration_seconds != null;
  return (
    <button
      type="button"
      className={`tile${selected ? " sel" : ""}`}
      title={name}
      aria-label={name}
      aria-pressed={selected}
      disabled={!onToggle}
      onClick={() => onToggle?.(media.id)}
    >
      <img
        src={`/api/media/${media.id}/thumbnail`}
        alt=""
        loading="lazy"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "cover",
          borderRadius: "inherit",
        }}
      />
      {selected ? (
        <span className="check">
          <Icon name="check" size={12} />
        </span>
      ) : (
        <StatusMark status={media.status ?? null} />
      )}
      {hasDuration && <span className="dur">{formatDuration(media.duration_seconds as number)}</span>}
    </button>
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
