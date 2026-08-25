// 写真（§13）。日付でまとめたグリッドにして、1 枚ごとに宛先ごとの状態を印で出す。
//
// **写真を選ぶ画面なので、写真が見える大きさで並べる。** 表のセルに収まる大きさの
// サムネイルでは、どれを選ぶかが決められない。状態の印には凡例を添える（色と形だけで
// 意味を伝えない）。

import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";
import { MediaTile, type Media, type MediaStatus } from "../components/MediaTile";
import { useEvents } from "../hooks/useEvents";
import { useReloadOnEvents } from "../hooks/useReloadOnEvents";
import { formatBytes } from "../utils/formatBytes";
import { formatDate } from "../utils/formatDateTime";

type MediaPage = { media: Media[]; total: number; page: number; page_size: number };
type Destination = { id: string; name: string; enabled: boolean };
type Destinations = { destinations: Destination[] };

type FilterKey = "all" | "unsent" | "awaiting" | "video" | "derived" | "sent" | "failed";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "すべて" },
  { key: "unsent", label: "まだ送っていない" },
  { key: "awaiting", label: "確認が要る" },
  { key: "video", label: "動画" },
  // **宛先ごとの絞り込みではない**ので `DESTINATION_SCOPED` には入れない。
  { key: "derived", label: "つないだ動画" },
  { key: "sent", label: "送信済み" },
  { key: "failed", label: "送れなかった" },
];

// **「まだ送っていない」「確認が要る」「送信済み」「送れなかった」は宛先ごと**なので、
// `destination_id` を伴わなければ API が 400 を返す。
const DESTINATION_SCOPED: ReadonlySet<FilterKey> = new Set([
  "unsent",
  "awaiting",
  "sent",
  "failed",
]);

// 1 度に読む件数。**渡さないと API の既定（50 件）で切れる。** 上限は
// `core/listing.MAX_PAGE_SIZE` と同じ 200 で、`work/Send.tsx` や `work/Merge.tsx`
// もこの数で読む。
const PAGE_SIZE = 200;

/** URL の検索パラメータから、いまの絞り込みを読む。 */
function filterFromParams(params: URLSearchParams): FilterKey {
  if (params.get("kind") === "video") {
    return "video";
  }
  if (params.get("role") === "derived") {
    return "derived";
  }
  const status = params.get("status");
  if (status === "unsent" || status === "awaiting" || status === "sent" || status === "failed") {
    return status;
  }
  return "all";
}

/** URL の検索パラメータから、いま見ているページ（1 始まり）を読む。 */
function pageFromParams(params: URLSearchParams): number {
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  return Number.isFinite(page) && page > 1 ? page : 1;
}

/**
 * `/media` へ渡すクエリを組み立てる。宛先が決まっていない宛先ごとの絞り込みは、
 * 400 を避けるため素通りさせる（呼び出し側が別に「宛先を選んでください」を出す）。
 *
 * **探している言葉とページも渡す。** どちらも落とすと、1 度に読む 200 件の外に
 * あるものへ画面から辿り着けなくなる（API は `q` も `page` も受け付ける）。
 */
function buildMediaQuery(
  filter: FilterKey,
  destinationId: string | null,
  params: URLSearchParams,
): string {
  const query = new URLSearchParams();
  if (filter === "video") {
    query.set("kind", "video");
  } else if (filter === "derived") {
    query.set("role", "derived");
  } else if (DESTINATION_SCOPED.has(filter) && destinationId) {
    query.set("status", filter);
    query.set("destination_id", destinationId);
  }
  const wanted = params.get("q");
  if (wanted) {
    query.set("q", wanted);
  }
  const page = pageFromParams(params);
  if (page > 1) {
    query.set("page", String(page));
  }
  // **写真タブは組を畳む。** 畳まないと同じ 1 枚が 2 タイルに割れて並ぶ。
  query.set("collapse", "stack");
  query.set("page_size", String(PAGE_SIZE));
  return query.toString();
}

/** `captured_at` の日付部分でまとめる。**並びは API の順を保つ**
 * （`captured_at DESC, id DESC`）。撮影日時が読めない行も、専用のまとまりに残す
 * —— 落とすと画面の件数と API の `total` が食い違う。 */
export function groupByDate(media: Media[]): { label: string; items: Media[] }[] {
  const groups: { label: string; items: Media[] }[] = [];
  const index = new Map<string, number>();
  for (const item of media) {
    const label = dateLabel(item.captured_at);
    let position = index.get(label);
    if (position === undefined) {
      position = groups.length;
      index.set(label, position);
      groups.push({ label, items: [] });
    }
    groups[position].items.push(item);
  }
  return groups;
}

/** 撮影日でまとめる見出し。日付部分の書式は `formatDate` を使う。 */
function dateLabel(capturedAt: string): string {
  if (capturedAt.slice(0, 10).length !== 10) {
    return "撮影日時が不明";
  }
  return formatDate(capturedAt);
}

export function PhotosScreen() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  // **選んだものは、隠れても覚えておく。** 大きさも一緒に持つ —— 表示中の行から
  // 計算すると、絞り込みで隠した分が合計から抜けて、確認の数字が実際と食い違う。
  const [selected, setSelected] = useState<Map<string, number>>(new Map());

  const destinations = useQuery<Destinations>("/destinations");
  const destinationRows = destinations.data?.destinations ?? [];

  const filter = filterFromParams(params);
  const chosenDestinationId = params.get("destination_id");
  // 宛先が 1 つしか無ければ、黙ってそれを使う。2 つ以上あるときは選ばせる。
  const effectiveDestinationId =
    destinationRows.length === 1 ? destinationRows[0].id : chosenDestinationId;
  const needsDestination = DESTINATION_SCOPED.has(filter) && effectiveDestinationId === null;

  const page = pageFromParams(params);
  const mediaQuery = buildMediaQuery(
    filter,
    needsDestination ? null : effectiveDestinationId,
    params,
  );
  const media = useQuery<MediaPage>(`/media?${mediaQuery}`, [mediaQuery]);
  // 取り込みや送信が進んだら取り直す（**画面を再読み込みせずに進む**。§13）。
  const { received } = useEvents();
  useReloadOnEvents(received, media.reload);

  // 宛先ごとの絞り込みが効いている間は、返ってきた行はすべてその状態。
  // それ以外（すべて／動画）は宛先ごとの状態が定まらないので、印を出さない。
  //
  // **新しい絞り込みの結果が返るまでは、印を出さない。** 前の絞り込みの行が
  // 並んだままなので、被せると送信済みの 200 枚に赤い × が付く。
  const impliedStatus: MediaStatus =
    !media.stale && DESTINATION_SCOPED.has(filter) ? (filter as MediaStatus) : null;
  const rows: Media[] = useMemo(() => {
    if (needsDestination) {
      return [];
    }
    return (media.data?.media ?? []).map((row) => ({ ...row, status: impliedStatus }));
  }, [media.data, impliedStatus, needsDestination]);
  const groups = useMemo(() => groupByDate(rows), [rows]);
  // **サーバ側の総数。** 1 度に読むのは `PAGE_SIZE` 件までなので、これより
  // 読めた行が少なければ切れている。宛先を選ぶ前は、いま出している 0 件と
  // 揃わない数（宛先を伴わない問い合わせの総数）を出さない。
  const total = needsDestination ? 0 : (media.data?.total ?? 0);

  const totalBytes = useMemo(
    () => [...selected.values()].reduce((sum, size) => sum + size, 0),
    [selected],
  );

  // いま出しているのが何件目から何件目か（ページ送りの案内）。
  // **番号は URL ではなく、いま並んでいる行が属するページから数える**（応答の
  // `page`）。URL で数えると、次のページを読んでいる間だけ「201–400 / 250 件」の
  // ように、前のページの行に新しい番号が付く。
  const shownPage = media.data?.page ?? 1;
  const firstIndex = rows.length === 0 ? 0 : (shownPage - 1) * PAGE_SIZE + 1;
  const lastIndex = (shownPage - 1) * PAGE_SIZE + rows.length;

  // 探している言葉は URL が持つ。**欄は URL の値で作り直す**（`key`）ので、
  // 戻る操作や、他の画面から `q` 付きで来たときにも中身が合う。
  const wanted = params.get("q") ?? "";

  function selectFilter(next: FilterKey) {
    const nextParams = new URLSearchParams(params);
    nextParams.delete("kind");
    nextParams.delete("status");
    nextParams.delete("role");
    // **絞り込みを変えたら 1 ページ目へ戻す。** 3 ページ目のまま移ると、
    // 当てはまるものが 1 ページ分しか無いときに「ありません」と出る。
    nextParams.delete("page");
    if (next === "video") {
      nextParams.set("kind", "video");
    } else if (next === "derived") {
      nextParams.set("role", "derived");
    } else if (next !== "all") {
      nextParams.set("status", next);
    }
    setParams(nextParams);
  }

  function goToPage(next: number) {
    const nextParams = new URLSearchParams(params);
    if (next <= 1) {
      nextParams.delete("page");
    } else {
      nextParams.set("page", String(next));
    }
    setParams(nextParams);
  }

  function search(wanted: string) {
    const nextParams = new URLSearchParams(params);
    if (wanted === "") {
      nextParams.delete("q");
    } else {
      nextParams.set("q", wanted);
    }
    nextParams.delete("page");
    setParams(nextParams);
  }

  function selectDestination(id: string) {
    const nextParams = new URLSearchParams(params);
    nextParams.set("destination_id", id);
    // **宛先を変えたら 1 ページ目へ戻す**（絞り込みや検索と同じ）。3 ページ目の
    // まま移ると、当てはまるものが 1 ページ分しか無いときに空の一覧だけが出る。
    nextParams.delete("page");
    setParams(nextParams);
  }

  /**
   * **畳んだタイルは「1 枚（RAW+JPEG）」を表す**（`GET /media?collapse=stack`）。
   * 選ぶ丸も送るのもその単位に揃えないと、主（JPG）しか積まれず、相方（CR2）が
   * 送られないまま Immich でスタックが組まれない
   * （`docs/history/phase10-design.md` §4「選んで送る画面の契約はそのまま」）。
   */
  function toggle(item: Media) {
    const members = item.stack?.members ?? [{ id: item.id, size_bytes: item.size_bytes }];
    setSelected((current) => {
      const next = new Map(current);
      const allSelected = members.every((member) => next.has(member.id));
      for (const member of members) {
        if (allSelected) {
          next.delete(member.id);
        } else {
          next.set(member.id, member.size_bytes);
        }
      }
      return next;
    });
  }

  return (
    <section aria-label="写真" className="wrap">
      <div className="row">
        <h1 className="page">写真</h1>
        <span className="small">
          {FILTERS.find((f) => f.key === filter)?.label}：{rows.length} / {total} 件
        </span>
      </div>

      <ErrorBanner error={media.error ?? destinations.error} />

      {/* **ファイル名で探せるようにする。** 1 度に読むのは 200 件までなので、
          これが無いと古いものへは画面から辿り着けない。 */}
      <form
        className="row"
        onSubmit={(event) => {
          event.preventDefault();
          const typed = new FormData(event.currentTarget).get("q");
          search(String(typed ?? "").trim());
        }}
      >
        <label className="small" htmlFor="photo-search">
          ファイル名でさがす
        </label>
        <input
          key={wanted}
          id="photo-search"
          name="q"
          className="field grow"
          type="search"
          defaultValue={wanted}
          placeholder="DSC_0431"
        />
        <button type="submit" className="btn sm">
          さがす
        </button>
        {params.get("q") && (
          <button type="button" className="btn sm quiet" onClick={() => search("")}>
            さがすのをやめる
          </button>
        )}
      </form>

      <div className="chips">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className="chip"
            aria-pressed={filter === f.key}
            disabled={DESTINATION_SCOPED.has(f.key) && destinationRows.length === 0}
            onClick={() => selectFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {DESTINATION_SCOPED.has(filter) && destinationRows.length >= 2 && (
        <div className="chips" aria-label="宛先">
          {destinationRows.map((destination) => (
            <button
              key={destination.id}
              type="button"
              className="chip"
              aria-pressed={destination.id === effectiveDestinationId}
              disabled={!destination.enabled}
              onClick={() => selectDestination(destination.id)}
            >
              {destination.name}
            </button>
          ))}
        </div>
      )}

      {/* **状態の印には凡例を添える。** 色と形だけで意味を伝えない（§13）。 */}
      <div className="legend">
        <span>
          <i className="lg" style={{ border: "2px solid var(--ink-3)" }} />
          まだ送っていない
        </span>
        <span>
          <i className="lg" style={{ background: "var(--ok)" }}>
            <Icon name="check" size={8} />
          </i>
          送信済み
        </span>
        <span>
          <i className="lg" style={{ background: "var(--warn-dot)" }}>
            !
          </i>
          確認が要る
        </span>
        <span>
          <i className="lg" style={{ background: "var(--danger)" }}>
            ×
          </i>
          送れなかった
        </span>
      </div>

      {needsDestination ? (
        <div className="card pad empty">
          <p className="muted">
            {destinationRows.length === 0
              ? "送り先がまだ無いので、宛先ごとの絞り込みは使えません。"
              : "宛先を選んでください。"}
          </p>
        </div>
      ) : groups.length === 0 ? (
        <div className="card pad empty">
          <p className="muted">この絞り込みに当てはまる写真はありません。</p>
        </div>
      ) : (
        groups.map((group) => (
          <section key={group.label} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="sechead">
              <h2 style={{ fontSize: 14 }}>{group.label}</h2>
              <span className="small">{group.items.length} 件</span>
            </div>
            <div className="grid">
              {group.items.map((item) => (
                <MediaTile
                  key={item.id}
                  media={item}
                  to={`/photos/${item.id}`}
                  selected={selected.has(item.id)}
                  onToggle={() => toggle(item)}
                />
              ))}
            </div>
          </section>
        ))
      )}

      {/* **1 ページに収まらないときだけ出す。** 収まっているのに前後のボタンが
          あると、押せない操作が常に並ぶ。**ただし 2 ページ目以降では必ず出す** ——
          住所に `page` を持ったまま件数の少ない絞り込みへ来ると、空の一覧から
          戻る道が無くなる。 */}
      {!needsDestination && (total > PAGE_SIZE || page > 1) && (
        <div className="row" style={{ justifyContent: "center", gap: 12 }}>
          <button
            type="button"
            className="btn sm"
            disabled={page <= 1}
            onClick={() => goToPage(page - 1)}
          >
            前の {PAGE_SIZE} 件
          </button>
          <span className="small">
            {firstIndex}–{lastIndex} / {total} 件
          </span>
          <button
            type="button"
            className="btn sm"
            disabled={lastIndex >= total}
            onClick={() => goToPage(page + 1)}
          >
            次の {PAGE_SIZE} 件
          </button>
        </div>
      )}

      {selected.size > 0 && (
        <div className="actionbar">
          <div>
            <div style={{ fontSize: 14, fontWeight: 650 }}>{selected.size} 件を選択中</div>
            <div style={{ fontSize: "11.5px", opacity: 0.65 }}>合計 {formatBytes(totalBytes)}</div>
          </div>
          <button
            type="button"
            className="btn primary"
            style={{ marginLeft: "auto" }}
            // **絞り込んでいた宛先も持って帰る**（§13 の「宛先を先に決める」が、
            // 写真を選びに来た往復で巻き戻らないように）。写真の画面は宛先を
            // 1 つしか絞れないので、持ち帰るのもその 1 つ。
            onClick={() =>
              navigate("/send", {
                state: {
                  ids: [...selected.keys()],
                  destinationIds: effectiveDestinationId === null ? [] : [effectiveDestinationId],
                },
              })
            }
          >
            送る
          </button>
          <button
            type="button"
            className="btn quiet"
            onClick={() => setSelected(new Map())}
          >
            やめる
          </button>
        </div>
      )}
    </section>
  );
}
