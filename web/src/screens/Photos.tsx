// 写真（§13）。日付でまとめたグリッドにして並べる。
//
// **写真を選ぶ画面なので、写真が見える大きさで並べる。** 表のセルに収まる大きさの
// サムネイルでは、どれを選ぶかが決められない。

import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useQuery } from "../api/hooks";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";
import { MediaTile, type Media } from "../components/MediaTile";
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
 * 400 を避けるため素通りさせる（呼び出し側が別に「送り先を選んでください」を出す）。
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
 * （`captured_at DESC, rel_path DESC`）。撮影日時が読めない行も、専用のまとまりに残す
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
  // **Shift の範囲の起点。** `selected` と違って、これは「並び」に属する状態
  // なので、並びが変われば無効になる（下で、並びと一緒に捨てる）。
  const [anchor, setAnchor] = useState<string | null>(null);

  const destinations = useQuery<Destinations>("/destinations");
  const destinationRows = destinations.data?.destinations ?? [];

  const filter = filterFromParams(params);
  // **空文字は「選んでいない」として扱う。** プルダウンの `<option value="">
  // 送り先を選ぶ</option>` を選び直すと `destination_id=""` が URL に入るが、
  // `null` でないので `needsDestination`（`=== null` 判定）を素通りし、
  // `buildMediaQuery` 側だけが falsy 判定で `status` ごと絞り込みを落とす ——
  // 「送信済み」のチップは押されたままなのに、宛先を伴わない一覧が出てしまう。
  const chosenDestinationId = params.get("destination_id") || null;
  // 宛先が 1 つしか無ければ、黙ってそれを使う。2 つ以上あるときは選ばせる。
  const effectiveDestinationId =
    destinationRows.length === 1 ? destinationRows[0].id : chosenDestinationId;
  // **ここもメモ化する。** 下の `rows` の `useMemo` がこの値に依存しており、
  // 生の式のままだと React Compiler が `rows` 側の手動メモ化を安全に保てず、
  // 最適化を諦める（lint の react-hooks/preserve-manual-memoization が指す）。
  const needsDestination = useMemo(
    () => DESTINATION_SCOPED.has(filter) && effectiveDestinationId === null,
    [filter, effectiveDestinationId],
  );

  const page = pageFromParams(params);
  const mediaQuery = buildMediaQuery(
    filter,
    needsDestination ? null : effectiveDestinationId,
    params,
  );
  const media = useQuery<MediaPage>(`/media?${mediaQuery}`, [mediaQuery]);
  // **アンカーを最後に見た `mediaQuery`。** 変わっていれば下でアンカーを捨てる。
  const [anchorMediaQuery, setAnchorMediaQuery] = useState(mediaQuery);
  // **並びが変わったらアンカーを捨てる。** `mediaQuery` は絞り込み・探している
  // 言葉・ページ・宛先をすべて含むので、これが変わることは並びが変わること。
  // 捨てないと、変わったあとのアンカーが利用者の見ていたものと違う範囲を指す。
  //
  // **`useEffect` ではなく、描画の途中で比べてその場で捨てる。** `useEffect`
  // で書くと、このリポジトリの react-hooks/set-state-in-effect（「効果の中で
  // 同期的に setState する」形を避けるルール）に引っかかる。加えて、描画のたびに
  // 比べる形なら、値が変わった回に余分な描画を挟まずに済む。
  if (anchorMediaQuery !== mediaQuery) {
    setAnchorMediaQuery(mediaQuery);
    setAnchor(null);
  }
  // 取り込みや送信が進んだら取り直す（**画面を再読み込みせずに進む**。§13）。
  const { received } = useEvents();
  useReloadOnEvents(received, media.reload);

  const rows: Media[] = useMemo(() => {
    if (needsDestination) {
      return [];
    }
    return media.data?.media ?? [];
  }, [media.data, needsDestination]);
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

  /** この行が表すファイル（組なら members、組でなければその行 1 つ）。 */
  function membersOf(item: Media): { id: string; size_bytes: number }[] {
    return item.stack?.members ?? [{ id: item.id, size_bytes: item.size_bytes }];
  }

  /** 1 タイルぶんを選ぶ／外す（**組は全員まとめて**）。 */
  function toggleOne(item: Media) {
    const members = membersOf(item);
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

  /**
   * アンカーから今回のタイルまでを**選ぶ**（外さない）。**アンカーが `rows` に
   * 無ければ何もせず `false` を返す**（呼び出し側が 1 枚だけの選択にフォール
   * バックする）。SSE の再取得（`useReloadOnEvents`）は `mediaQuery` を変えない
   * ので、絞り込みを変えていなくても、取り込みで 200 件の外へ押し出されたり、
   * つなぎ直しで消えたりして、アンカーの行が並びから居なくなることがある。
   *
   * **選ぶ側に倒す。** アンカーの選択状態に合わせて外す作りもあるが、
   * 「シフトで選び、要らないものを個別に外す」の方が手数が少なく、押した
   * 結果が予想しやすい。
   *
   * **並びは `rows`**（API の `captured_at DESC, rel_path DESC` そのまま）。日付の
   * まとまりはまたぐ —— 利用者が見ている並びは 1 本の流れで、まとまりは
   * 見出しにすぎない。
   */
  function selectRange(fromId: string, toId: string): boolean {
    const from = rows.findIndex((row) => row.id === fromId);
    const to = rows.findIndex((row) => row.id === toId);
    if (from === -1 || to === -1) {
      return false;
    }
    const span = rows.slice(Math.min(from, to), Math.max(from, to) + 1);
    setSelected((current) => {
      const next = new Map(current);
      for (const item of span) {
        for (const member of membersOf(item)) {
          next.set(member.id, member.size_bytes);
        }
      }
      return next;
    });
    return true;
  }

  /**
   * **畳んだタイルは「1 枚（RAW+JPEG）」を表す**（`GET /media?collapse=stack`）。
   * 選ぶ丸も送るのもその単位に揃えないと、主（JPG）しか積まれず、相方（CR2）が
   * 送られないまま Immich でスタックが組まれない
   * （`docs/history/phase10-design.md` §4「選んで送る画面の契約はそのまま」）。
   *
   * **範囲が組めなかったときは、1 枚だけの選択にフォールバックする。** アンカー
   * が無い場合（`anchor === null`）も、あった場合（`selectRange` が `false` を
   * 返す＝並びから消えていた）も、同じ 1 行にまとめる —— 別々に書くと、片方だけ
   * 直して片方を忘れる形で 2 つが食い違いうる。
   */
  function toggle(item: Media, modifiers: { shift: boolean }) {
    const ranged = modifiers.shift && anchor !== null && selectRange(anchor, item.id);
    if (!ranged) {
      toggleOne(item);
    }
    setAnchor(item.id);
  }

  /** その日がどれだけ選ばれているか。**見えている行だけを数える。** */
  function dayState(items: Media[]): "all" | "some" | "none" {
    const ids = items.flatMap((item) => membersOf(item).map((member) => member.id));
    const on = ids.filter((id) => selected.has(id)).length;
    if (on === 0) {
      return "none";
    }
    return on === ids.length ? "all" : "some";
  }

  /**
   * その日をまとめて選ぶ／外す。**全部選ばれているときだけ外し、それ以外は選ぶ。**
   *
   * **触るのは、いま画面に並んでいる行だけ。** 絞り込みで隠れているぶんや次の
   * ページのぶんは触らない —— 見えていないものを選ぶ丸は、押した結果が
   * 確かめられない。
   */
  function toggleDay(items: Media[]) {
    const clearing = dayState(items) === "all";
    setSelected((current) => {
      const next = new Map(current);
      for (const item of items) {
        for (const member of membersOf(item)) {
          if (clearing) {
            next.delete(member.id);
          } else {
            next.set(member.id, member.size_bytes);
          }
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
        {/* **宛先が 2 つ以上あるときだけ、常設で選ばせる。** 1 つしか無ければ
            黙ってそれを使うので選ばせる意味が無い（`effectiveDestinationId`）。
            どの絞り込みからでも切り替えられるよう、右端に置いて絞り込みとは
            独立させる。 */}
        {destinationRows.length > 1 && (
          <select
            className="field"
            style={{ width: "auto", marginLeft: "auto" }}
            aria-label="送り先"
            value={effectiveDestinationId ?? ""}
            onChange={(event) => selectDestination(event.target.value)}
          >
            <option value="">送り先を選ぶ</option>
            {destinationRows.map((destination) => (
              <option key={destination.id} value={destination.id} disabled={!destination.enabled}>
                {destination.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {needsDestination ? (
        <div className="card pad empty">
          <p className="muted">
            {destinationRows.length === 0
              ? "送り先がまだ無いので、宛先ごとの絞り込みは使えません。"
              : "上の送り先を選んでください。"}
          </p>
        </div>
      ) : groups.length === 0 ? (
        <div className="card pad empty">
          <p className="muted">この絞り込みに当てはまる写真はありません。</p>
        </div>
      ) : (
        groups.map((group) => {
          // **1 グループにつき 1 回だけ求める。** className・aria-checked・
          // アイコンの分岐がそれぞれ呼ぶと、同じ結果を描画ごとに何度も計算する。
          const state = dayState(group.items);
          return (
            <section
              key={group.label}
              style={{ display: "flex", flexDirection: "column", gap: 10 }}
            >
              <div className="sechead dayhead">
                {/* **丸と日付を 1 つのボタンにする。** 21px の丸だけが押せる形だと
                    狙いにくく、隣の日付が押せそうに見えて押せない。見出しを
                    `<h2>` のまま残したいので、ボタンを見出しの中に入れる
                    （見出しの中身は句要素なので `<button>` は置ける）。

                    **`role="checkbox"` の 3 状態にする。** 「一部選ばれている」は
                    押下状態（`aria-pressed`）では表せず、読み上げで全選択と
                    区別が付かない。`mixed` はチェックボックスにしかない。 */}
                <h2 style={{ fontSize: 14 }}>
                  <button
                    type="button"
                    role="checkbox"
                    className={`daypick${state === "none" ? "" : " on"}`}
                    aria-checked={state === "all" ? "true" : state === "some" ? "mixed" : "false"}
                    aria-label={`${group.label} をまとめて選ぶ`}
                    onClick={() => toggleDay(group.items)}
                  >
                    <span className="daydot" aria-hidden="true">
                      {state === "all" && <Icon name="check" size={12} />}
                      {state === "some" && <span className="dash" />}
                    </span>
                    {group.label}
                  </button>
                </h2>
                <span className="small">{group.items.length} 件</span>
              </div>
              <div className="grid">
                {group.items.map((item) => (
                  <MediaTile
                    key={item.id}
                    media={item}
                    to={`/photos/${item.id}`}
                    selected={selected.has(item.id)}
                    onToggle={(_id, modifiers) => toggle(item, modifiers)}
                  />
                ))}
              </div>
            </section>
          );
        })
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
