// 送る（§13）。**取り消せないので、宛先 → 対象 → 確認の 3 段を 1 画面に縦に並べる。**
// 別ページに分けると戻る操作が増えるだけで、選び直しがしにくくなる。
//
// **この画面だけは、進捗で取り直さない**（他の一覧は `useReloadOnEvents` を持つ）。
// 対象は利用者が選んでいる最中のもので、裏で増減すると、確かめた内容と送るものが
// 食い違う。新しく取り込まれたぶんは、送り終えて画面へ戻ったときに入る。

import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useDashboardReload } from "../../api/dashboard";
import { useMutation, useQuery } from "../../api/hooks";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { MediaTile, type Media } from "../../components/MediaTile";
import { formatBytes } from "../../utils/formatBytes";
import { groupIntoStacks } from "../../utils/stacks";

type Destination = { id: string; name: string; enabled: boolean };
type Destinations = { destinations: Destination[] };
type MediaPage = { media: Media[]; total: number; page: number; page_size: number };
type Pair = {
  media_file_id: string;
  destination_id: string;
  result: string;
  upload_record_id: string | null;
  reason: string | null;
};
type PairResult = { pairs: Pair[] };

/** 何を送るか（§13「送る」の 2 段目）。`pick` だけは選ぶと写真の画面へ移る。 */
type Preset = "selection" | "unsent" | "day0" | "pick";

/** 1 度に読む件数（`core/listing.MAX_PAGE_SIZE` と同じ）。 */
const PAGE_SIZE = 200;

/**
 * 宛先ごとの未送信を 1 つの並びにまとめる。**同じ写真を二重に数えない。**
 *
 * 並びは API と同じ `captured_at DESC, rel_path DESC`（SQL も文字列として比べるので、
 * オフセット付きの値をそのまま比べれば同じ順になる）。
 */
export function mergeMedia(pages: { media: Media[] }[]): Media[] {
  const byId = new Map<string, Media>();
  for (const page of pages) {
    for (const media of page.media) {
      byId.set(media.id, media);
    }
  }
  return [...byId.values()].sort((left, right) => {
    if (left.captured_at !== right.captured_at) {
      return left.captured_at < right.captured_at ? 1 : -1;
    }
    return left.rel_path < right.rel_path ? 1 : -1;
  });
}

/**
 * いちばん新しい 1 件（**実際の瞬間で比べる**）。
 *
 * `captured_at` は現地の時差付きで保存されるので、時差の違うカメラが混ざると
 * 文字列の大小は時刻の大小と一致しない。読めない値は候補にしない。
 */
function newest(media: Media[]): Media | undefined {
  return media.reduce<Media | undefined>((best, item) => {
    const at = Date.parse(item.captured_at);
    if (Number.isNaN(at)) {
      return best;
    }
    return best === undefined || at > Date.parse(best.captured_at) ? item : best;
  }, undefined);
}

/**
 * 送信の結果を 1 文にする（**断られた写真と、開始に失敗した宛先を隠さない**）。
 *
 * **数えるのはファイルと宛先で、pair ではない。** `POST /uploads` が返す pair は
 * media × destination の直積なので、その数をそのまま報告すると、組（RAW+JPEG）を
 * 1 つ送っただけで「2」と出るし、同じ 1 枚を 2 宛先へ送っても「2」と出る。画面は
 * どこも「件」で数え、「組」は RAW+JPEG のスタックを指す語なので、pair の数に
 * 「組」と付けると利用者の見ているものと合わない。**媒体の id で重複を落とす。**
 *
 * `started` は**実際に送信が始まった宛先の数**。pair が受け付けられただけの数を
 * 渡すと、同じ 1 文で「2 宛先で始めた」と「1 宛先は始められなかった」を並べる
 * ことになる（取り消せない操作の報告としては嘘になる）。
 */
export function summarise(
  pairs: { media_file_id: string; result: string; reason: string | null }[],
  failures: string[],
  started: number,
): string {
  const rejected = pairs.filter((pair) => pair.result === "rejected");
  const accepted = new Set(
    pairs.filter((pair) => pair.result !== "rejected").map((pair) => pair.media_file_id),
  );
  const refused = new Set(rejected.map((pair) => pair.media_file_id));
  const parts = [`${accepted.size} 件を、${started} 宛先へ送り始めました。`];
  if (refused.size > 0) {
    const reasons = [...new Set(rejected.map((pair) => pair.reason ?? "理由不明"))];
    parts.push(`送れない写真が ${refused.size} 件ありました（${reasons.join(" / ")}）。`);
  }
  if (failures.length > 0) {
    parts.push(
      `開始できなかった宛先: ${failures.join(" / ")}。設定 › 送り先の「送り直す」で始め直せます。`,
    );
  }
  return parts.join("");
}

/**
 * 対象の撮影日の幅（確認に出す）。読める値が 1 つも無ければ `null`。
 *
 * **日付の文字列で比べる。** `captured_at` は現地の時差付きなので、実際の瞬間の
 * 前後と文字の前後は一致しない。ここで欲しいのは**画面に出す日付のラベル**で、
 * 写真タブの日付の見出しも同じ切り出し方をしている —— 揃えておかないと、
 * 「8月17日」と束ねて見せたものが確認では別の日として出る。
 */
export function capturedRange(media: Media[]): { from: string; to: string } | null {
  const readable = media.filter((item) => item.captured_at.slice(0, 10).length === 10);
  if (readable.length === 0) {
    return null;
  }
  let from = readable[0];
  let to = readable[0];
  for (const item of readable) {
    if (item.captured_at.slice(0, 10) < from.captured_at.slice(0, 10)) {
      from = item;
    }
    if (item.captured_at.slice(0, 10) > to.captured_at.slice(0, 10)) {
      to = item;
    }
  }
  return { from: from.captured_at, to: to.captured_at };
}

export function SendScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const passed = location.state as { ids?: string[]; destinationIds?: string[] } | null;
  const ids = passed?.ids;

  const [preset, setPreset] = useState<Preset>(ids && ids.length > 0 ? "selection" : "unsent");
  // **選んだ宛先。** 写真の画面から持ち帰った宛先があれば、それを選んだ状態で
  // 始める（§13 の「宛先を先に決める」が往復で巻き戻らないように）。候補が
  // 1 つしかないときは黙ってそれを使う（`Photos.tsx` の宛先選びと同じ考え方）。
  const [targets, setTargets] = useState<Set<string>>(new Set(passed?.destinationIds ?? []));
  // **確認は「いまの対象」に結び付けて持つ。** 宛先や対象の種類が変わった時点で
  // 中身が変わるので、開いたままにすると確かめたのと違うものが送られる。
  const [confirmingFor, setConfirmingFor] = useState<string | null>(null);
  // 送信そのものと、対象の解決の失敗。**画面が持つ失敗は 1 本**（帯も 1 本）。
  const sending = useMutation();
  const refreshTasks = useDashboardReload();
  // 対象の解決で一部だけ外れたときの断り書き（隠さない。§13）。
  const [note, setNote] = useState<string | null>(null);

  const [targetMedia, setTargetMedia] = useState<Media[]>([]);
  // サーバ側の総数。`targetMedia` は 1 度に読む上限（200 件）で切れることがあるので、
  // 「すべて」と名乗る対象がそれより多いときに気付けるよう別に持つ。
  // **宛先が 2 つ以上のときは `null`。** 宛先ごとの総数を足すと、両方に未送信の
  // 写真を二重に数えてしまう（残りの件数は数で言えない）。
  const [targetTotal, setTargetTotal] = useState<number | null>(0);
  // 1 度に読む上限で切れたかどうか。件数が言えないときでも、切れたことは言う。
  const [targetTruncated, setTargetTruncated] = useState(false);
  const [targetLoading, setTargetLoading] = useState(false);

  const destinationsQuery = useQuery<Destinations>("/destinations");
  const destinationRows = destinationsQuery.data?.destinations ?? [];
  const enabledDestinations = destinationRows.filter((row) => row.enabled);

  function effectiveTargets(base: Set<string>): Set<string> {
    if (base.size > 0 || enabledDestinations.length !== 1) {
      return base;
    }
    return new Set([enabledDestinations[0].id]);
  }

  const chosen = destinationRows.filter(
    (row) => effectiveTargets(targets).has(row.id) && row.enabled,
  );
  // **効果の依存は id の並びで見る。** `chosen` は毎回新しい配列なので、その
  // まま依存に置くと描画のたびに読み直しに行く。
  const chosenKey = chosen.map((destination) => destination.id).join(",");

  function toggleTarget(id: string) {
    setTargets((current) => {
      const next = new Set(effectiveTargets(current));
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  // 対象の解決（§10）。preset ごとに叩く API が違う。
  useEffect(() => {
    let cancelled = false;
    const targetIds = chosenKey === "" ? [] : chosenKey.split(",");

    /** 選んだ宛先ぶんの「まだ送っていない」を、宛先ごとに 1 巡ずつ読む。 */
    function unsentPages(day: { from: string; to: string } | null): Promise<MediaPage[]> {
      return Promise.all(
        targetIds.map((destinationId) => {
          const query = new URLSearchParams({
            destination_id: destinationId,
            status: "unsent",
            page_size: String(PAGE_SIZE),
            // **組を教えてもらうが、畳ませない。** 畳むと未送信の RAW が一覧から
            // 消え、送られないまま Immich でスタックが組まれない
            // （`docs/history/phase12-design.md` の 2）。
            stack: "members",
          });
          if (day !== null) {
            query.set("captured_from", day.from);
            query.set("captured_to", day.to);
          }
          return request<MediaPage>(`/media?${query.toString()}`);
        }),
      );
    }

    function clear() {
      if (!cancelled) {
        setTargetMedia([]);
        setTargetTotal(0);
        setTargetTruncated(false);
      }
    }

    async function resolve() {
      if (preset === "pick") {
        // 自分で選ぶ：写真の画面へ渡す。写真の画面は宛先を 1 つしか絞れないので、
        // 絞り込みに渡すのは先頭の 1 つ。
        navigate(
          `/photos?status=unsent${targetIds[0] ? `&destination_id=${targetIds[0]}` : ""}`,
        );
        return;
      }
      if (preset === "selection" && (!ids || ids.length === 0)) {
        clear();
        return;
      }
      if (preset !== "selection" && targetIds.length === 0) {
        clear();
        return;
      }

      setTargetLoading(true);
      sending.clear();
      setNote(null);
      try {
        if (preset === "selection") {
          const settled = await Promise.allSettled(
            (ids ?? []).map((id) => request<Media>(`/media/${id}`)),
          );
          // **1 件が読めなくても、残りは送れる**（宛先の一部が開始に失敗しても
          // 進める、というこの画面の判断と同じ考え方）。外れた分は隠さず 1 文で言う。
          const found = settled
            .filter((result): result is PromiseFulfilledResult<Media> => result.status === "fulfilled")
            .map((result) => result.value);
          const missing = settled.length - found.length;
          if (!cancelled) {
            setTargetMedia(found);
            setTargetTotal(found.length);
            setTargetTruncated(false);
            if (missing > 0) {
              setNote(`${missing} 件は見つからないので外しました。`);
            }
          }
          return;
        }

        let pages = await unsentPages(null);
        if (preset === "day0") {
          // **いちばん新しい撮影日のぶんだけ。** 絞らずに 1 巡取った中から、
          // **実際の瞬間がいちばん新しいもの**を選び、その日の 0 時〜24 時で
          // 絞り直す。
          //
          // **並び（`mergeMedia`）の先頭では選ばない。** 並びは API と同じ文字列
          // 比較で、`captured_at` は現地の時差付きなので、時差の違うカメラが
          // 混ざると文字の順と時刻の順がずれる。
          const top = newest(mergeMedia(pages));
          if (top === undefined) {
            clear();
            return;
          }
          const day = top.captured_at.slice(0, 10);
          // **時差は末尾から読む。** 秒より細かい桁があると（`…:00.123456+09:00`）
          // 決め打ちの位置では切れず、絞り込みの端が別の時刻になる。
          const offset = /(Z|[+-]\d{2}:\d{2})$/.exec(top.captured_at)?.[0] ?? "";
          pages = await unsentPages({
            from: `${day}T00:00:00${offset}`,
            to: `${day}T23:59:59${offset}`,
          });
        }
        if (!cancelled) {
          setTargetMedia(mergeMedia(pages));
          // **応答の `total` を読む。** 200 件の上限で切れていても黙らない
          // （裁定 20）。宛先が 2 つ以上あると総数を足せないので、そのときは
          // 「切れている」ことだけを持つ。
          setTargetTotal(targetIds.length === 1 ? pages[0].total : null);
          setTargetTruncated(pages.some((page) => page.total > page.media.length));
        }
      } catch (caught) {
        if (!cancelled) {
          sending.fail(caught);
          setTargetMedia([]);
          setTargetTotal(0);
          setTargetTruncated(false);
        }
      } finally {
        if (!cancelled) {
          setTargetLoading(false);
        }
      }
    }

    void resolve();
    return () => {
      cancelled = true;
    };
    // ids は同じ選択の間は同じ配列なので、深く比べる必要はない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, chosenKey]);

  const totalBytes = useMemo(
    () => targetMedia.reduce((sum, media) => sum + media.size_bytes, 0),
    [targetMedia],
  );

  // **タイルは組単位、数はファイル単位。** 返ってきた行の集合の中だけで組む
  // ので、相方が対象でなければ単独のタイルになる。
  const tiles = useMemo(() => groupIntoStacks(targetMedia), [targetMedia]);
  const shown = tiles.slice(0, 16);
  const shownFiles = shown.reduce((count, tile) => count + tile.rows.length, 0);

  // 開いた時点の対象（種類 × 宛先）。**変わったら確認はひとりでに閉じる。**
  const targetKey = `${preset}:${chosenKey}`;
  const confirming = confirmingFor === targetKey;

  /**
   * 送信は 2 段階（§10）。
   *
   * `POST /uploads` は media × destination の組を作るだけで、**送信は始まらない**。
   * その後に宛先ごとの `POST /destinations/{id}/upload` が要る。**一部の宛先で
   * 失敗しても、成功した分は進める**（全部やり直しにしない）。
   */
  async function send() {
    const done = await sending.run(async () => {
      const created = (await request("/uploads", {
        method: "POST",
        body: { media_ids: targetMedia.map((media) => media.id), destination_ids: chosen.map((d) => d.id) },
      })) as PairResult;
      // **pair ごとの結果を読む。** 送れない pair（結合中のグループの構成ファイル
      // など）は backend が理由付きで断る。**受け付けられた pair がある宛先だけ**
      // 送信を始める。
      const accepted = new Set(
        created.pairs.filter((pair) => pair.result !== "rejected").map((pair) => pair.destination_id),
      );
      const failures: string[] = [];
      const jobIds: string[] = [];
      for (const destination of chosen.filter((one) => accepted.has(one.id))) {
        try {
          const started = (await request(`/destinations/${destination.id}/upload`, {
            method: "POST",
          })) as { job_id: string };
          jobIds.push(started.job_id);
        } catch {
          failures.push(destination.name);
        }
      }
      const note = summarise(created.pairs, failures, jobIds.length);
      // **1 本も始まらなかったときのために、ここで直す。** 進捗のイベントが
      // 出ないので、枠の「やること」もホームの件数も送る前のまま残る。
      refreshTasks();
      // 進捗の置き場はホーム 1 本。押した瞬間に画面が変わらないと「失敗した」と
      // 読まれるので、積んだジョブの id と結果の文を持って遷移する。
      navigate("/", { state: { jobIds, note } });
    });
    if (!done) {
      // 失敗はバナーに出るので、確認は閉じて選び直せるようにする。
      setConfirmingFor(null);
    }
  }

  const presets: { key: Preset; title: string; sub: string }[] = [];
  if (ids && ids.length > 0) {
    presets.push({ key: "selection", title: "選んだもの", sub: `${ids.length} 件` });
  }
  presets.push({
    key: "unsent",
    title: "まだ送っていないもの、すべて",
    sub: presetSub("unsent"),
  });
  presets.push({
    key: "day0",
    title: "いちばん新しい撮影日のぶんだけ",
    sub: presetSub("day0"),
  });
  presets.push({ key: "pick", title: "写真を自分で選ぶ", sub: "一覧から選びます" });

  function presetSub(key: "unsent" | "day0"): string {
    if (chosen.length === 0) {
      return "宛先を選んでください";
    }
    if (preset !== key) {
      return key === "day0"
        ? "いちばん新しい日にちだけ送ります"
        : "選んだ送り先へまだ送っていないもの";
    }
    if (targetLoading) {
      return "読み込み中…";
    }
    return `${targetMedia.length} 件 ・ ${formatBytes(totalBytes)}`;
  }

  return (
    <section aria-label="送る" className="wrap">
      {/* **行に包む。** `.wrap` は縦並びなので、直下に置いたボタンは
          `align-items: stretch` で幅いっぱいに伸びる（§13 の他の画面と同じ形）。 */}
      <div className="row">
        <button type="button" className="btn sm quiet" onClick={() => navigate("/")}>
          <Icon name="back" size={16} />
          やめる
        </button>
      </div>
      <h1 className="page">Immich へ送る</h1>

      <ErrorBanner error={sending.error ?? destinationsQuery.error} onDismiss={sending.clear} />
      {note && <p role="status">{note}</p>}

      <section>
        <div className="sechead">
          <h2>どこへ送りますか</h2>
        </div>
        <p className="small" style={{ margin: "4px 0 12px" }}>
          送ったあとで取り消すことはできません。
        </p>
        <div className="chips">
          {destinationRows.map((destination) => {
            const on = effectiveTargets(targets).has(destination.id) && destination.enabled;
            return (
              <button
                key={destination.id}
                type="button"
                className="chip stacked"
                aria-pressed={on}
                disabled={!destination.enabled}
                onClick={() => toggleTarget(destination.id)}
              >
                <span style={{ display: "block", fontWeight: 650 }}>{destination.name}</span>
                <span className="small">
                  {destination.enabled ? "つながっています" : "いまは休止中なので選べません"}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <div className="sechead" style={{ marginBottom: 12 }}>
          <h2>何を送りますか</h2>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 10,
          }}
        >
          {presets.map((option) => (
            // **すぐ下の「送るもの」と釣り合う重さにする。** 選ぶだけの段が送る
            // 中身より大きい面積を占めると、目が先にこちらへ行く（`.pad` は使わない）。
            <label
              key={option.key}
              className="card"
              style={{
                display: "flex",
                gap: 10,
                padding: "11px 13px",
                alignItems: "flex-start",
                cursor: "pointer",
                borderColor: preset === option.key ? "var(--accent)" : undefined,
                background: preset === option.key ? "var(--accent-soft)" : undefined,
              }}
            >
              <input
                type="radio"
                name="preset"
                value={option.key}
                checked={preset === option.key}
                onChange={() => setPreset(option.key)}
                style={{ marginTop: 2 }}
              />
              <span>
                <b style={{ display: "block", fontSize: "13.5px" }}>{option.title}</b>
                <span className="small">{option.sub}</span>
              </span>
            </label>
          ))}
        </div>
      </section>

      <section>
        <div className="sechead" style={{ marginBottom: 11 }}>
          <h2 style={{ fontSize: "14.5px" }}>送るもの</h2>
          <span className="small">
            {targetMedia.length} 件のうち、はじめの {shownFiles} 件
          </span>
        </div>
        <div
          style={{
            display: "grid",
            // **74px 未満にしない。** 320〜430px 幅の実測で、これより狭いと
            // `JPG+RAW` の札が削れる。
            gridTemplateColumns: "repeat(auto-fill, minmax(74px, 1fr))",
            gap: 7,
          }}
        >
          {shown.map((tile) => (
            // **タイルの `stack.members` は「このタイルが表すファイル」。**
            // 一覧では組の全員だが、ここでは対象になっているぶんだけ ——
            // 相方が送信済みなら、札を出さずに 1 枚として並べる。
            <MediaTile
              key={tile.primary.id}
              media={{
                ...tile.primary,
                stack: {
                  members: tile.rows.map((row) => ({
                    id: row.id,
                    rel_path: row.rel_path,
                    size_bytes: row.size_bytes,
                  })),
                },
              }}
              selected={false}
            />
          ))}
        </div>
      </section>

      <div className="card pad rowtop">
        <span style={{ color: "var(--ink-2)", flex: "0 0 auto" }}>
          <Icon name="info" />
        </span>
        {/* **`.grow` を外さない。** `.rowtop` は行に詰めるかを flex-basis で決めるので、
            basis が max-content のままだと縮む前にアイコンと別の行へ落ちる。 */}
        <p className="muted grow">
          Immich にすでにある写真は、自動でとばします（同じ写真が二重に並びません）。Immich の
          ゴミ箱に入っているものも「ある」と数えるので、勝手に戻すことはありません。
        </p>
      </div>

      <div className="card pad row sendbar">
        <div className="grow">
          <div style={{ fontSize: 15, fontWeight: 650 }}>
            {targetMedia.length} 件 ・ {formatBytes(totalBytes)}
          </div>
          <div className="small">送り先：{chosen.map((d) => d.name).join(" / ") || "（選んでください）"}</div>
          {/* **「すべて」が黙って上限で切れない**（裁定 20）。1 度に読むのは
              200 件までなので、それより多ければ正直に言う。宛先が 2 つ以上の
              ときは、残りを数で言うと同じ写真を重複して数えることになる。 */}
          {targetTruncated && (
            <div className="small">
              {targetTotal === null
                ? "1 度に送れる分を超えています。これを送ったあと、もう一度送ってください。"
                : `残り ${targetTotal - targetMedia.length} 件は次にもう一度送ってください。`}
            </div>
          )}
        </div>
        {/* **読み直している間は押させない。** 手元の一覧は前の宛先ぶんのままで、
            確認に出しても実際に送るものと一致しない。 */}
        <button
          type="button"
          className="btn primary big"
          disabled={targetMedia.length === 0 || chosen.length === 0 || targetLoading}
          onClick={() => setConfirmingFor(targetKey)}
        >
          内容を確かめる
        </button>
      </div>

      {confirming && (
        <ConfirmDialog
          confirmation={{
            kind: "upload",
            count: targetMedia.length,
            totalBytes,
            destinationNames: chosen.map((d) => d.name),
            // **つないだ動画は元のファイルとは別物**なので、内訳を出す。
            derivedCount: targetMedia.filter((media) => media.role === "derived").length,
            capturedRange: capturedRange(targetMedia),
            // 帯と同じことを、押す直前の 1 枚にも出す（裁定 20）。
            truncated: targetTruncated,
          }}
          busy={sending.busy}
          onCancel={() => setConfirmingFor(null)}
          onConfirm={() => void send()}
        />
      )}
    </section>
  );
}
