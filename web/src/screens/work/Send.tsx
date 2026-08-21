// 送る（§13）。**取り消せないので、宛先 → 対象 → 確認の 3 段を 1 画面に縦に並べる。**
// 別ページに分けると戻る操作が増えるだけで、選び直しがしにくくなる。

import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ConfirmDialog, formatBytes } from "../../components/ConfirmDialog";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { MediaTile, type Media } from "../../components/MediaTile";

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

/** 最低限の形を持つ `Media` かどうか。**プレビューの描画で落ちないための最後の砦。** */
function isMedia(value: unknown): value is Media {
  const candidate = value as Partial<Media> | null;
  return (
    candidate !== null &&
    typeof candidate === "object" &&
    typeof candidate.id === "string" &&
    typeof candidate.rel_path === "string"
  );
}

/** 送信の結果を 1 文にする（**断られた組と、開始に失敗した宛先を隠さない**）。 */
export function summarise(
  total: number,
  rejected: { reason: string | null }[],
  failures: string[],
  started: number,
): string {
  const parts = [`${total - rejected.length} 組を作り、${started} 宛先で送信を始めました。`];
  if (rejected.length > 0) {
    const reasons = [...new Set(rejected.map((pair) => pair.reason ?? "理由不明"))];
    parts.push(`送れない組が ${rejected.length} 件ありました（${reasons.join(" / ")}）。`);
  }
  if (failures.length > 0) {
    parts.push(`開始できなかった宛先: ${failures.join(" / ")}。転送先の画面から再試行できます。`);
  }
  return parts.join("");
}

export function SendScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const ids = (location.state as { ids?: string[] } | null)?.ids;

  const [preset, setPreset] = useState<Preset>(ids && ids.length > 0 ? "selection" : "unsent");
  // **選んだ宛先。** 候補が 1 つしかないときは、黙ってそれを使う
  // （`Photos.tsx` の宛先選びと同じ考え方）。空のままなら derive 側で補う。
  const [targets, setTargets] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const [targetMedia, setTargetMedia] = useState<Media[]>([]);
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
  const primaryDestinationId = chosen[0]?.id ?? null;

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

    async function resolve() {
      if (preset === "pick") {
        // 自分で選ぶ：写真の画面へ渡す。宛先が決まっていれば絞り込みも渡す。
        navigate(
          `/photos?status=unsent${primaryDestinationId ? `&destination_id=${primaryDestinationId}` : ""}`,
        );
        return;
      }
      if (preset === "selection" && (!ids || ids.length === 0)) {
        setTargetMedia([]);
        return;
      }
      if (preset !== "selection" && primaryDestinationId === null) {
        setTargetMedia([]);
        return;
      }

      setTargetLoading(true);
      setError(null);
      try {
        if (preset === "selection") {
          const items = await Promise.all((ids ?? []).map((id) => request<Media>(`/media/${id}`)));
          // **形が崩れているものは対象から外す。** 選んでから消えた場合や、テストの
          // 代役 API が別の形を返した場合に、プレビューの描画で落ちないようにする。
          if (!cancelled) {
            setTargetMedia(items.filter(isMedia));
          }
          return;
        }

        const query = new URLSearchParams({
          destination_id: primaryDestinationId as string,
          status: "unsent",
          page_size: "200",
        });
        if (preset === "day0") {
          // **いちばん新しい撮影日のぶんだけ。** 並びは `captured_at DESC` なので、
          // 絞らずに 1 巡取った先頭が最新の撮影日。その日の 0 時〜24 時で絞り直す。
          const latest = await request<MediaPage>(`/media?${query.toString()}`);
          const top = latest.media[0];
          if (top === undefined) {
            if (!cancelled) {
              setTargetMedia([]);
            }
            return;
          }
          const day = top.captured_at.slice(0, 10);
          const offset = top.captured_at.slice(19);
          query.set("captured_from", `${day}T00:00:00${offset}`);
          query.set("captured_to", `${day}T23:59:59${offset}`);
        }
        const page = await request<MediaPage>(`/media?${query.toString()}`);
        if (!cancelled) {
          setTargetMedia(page.media);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught);
          setTargetMedia([]);
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
  }, [preset, primaryDestinationId]);

  const totalBytes = useMemo(
    () => targetMedia.reduce((sum, media) => sum + media.size_bytes, 0),
    [targetMedia],
  );

  /**
   * 送信は 2 段階（§10）。
   *
   * `POST /uploads` は media × destination の組を作るだけで、**送信は始まらない**。
   * その後に宛先ごとの `POST /destinations/{id}/upload` が要る。**一部の宛先で
   * 失敗しても、成功した分は進める**（全部やり直しにしない）。
   */
  async function send() {
    setBusy(true);
    setError(null);
    try {
      const created = (await request("/uploads", {
        method: "POST",
        body: { media_ids: targetMedia.map((media) => media.id), destination_ids: chosen.map((d) => d.id) },
      })) as PairResult;
      // **組ごとの結果を読む。** 送れない組（結合中のグループの構成ファイルなど）は
      // backend が理由付きで断る。**受け付けられた組がある宛先だけ**送信を始める。
      const accepted = new Set(
        created.pairs.filter((pair) => pair.result !== "rejected").map((pair) => pair.destination_id),
      );
      const rejected = created.pairs.filter((pair) => pair.result === "rejected");
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
      const note = summarise(created.pairs.length, rejected, failures, accepted.size);
      navigate("/sending", { state: { jobIds, note } });
    } catch (caught) {
      setError(caught);
      setConfirming(false);
      setBusy(false);
    }
  }

  const presets: { key: Preset; title: string; sub: string }[] = [];
  if (ids && ids.length > 0) {
    presets.push({ key: "selection", title: "写真の画面で選んだもの", sub: `${ids.length} 件` });
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
    if (primaryDestinationId === null) {
      return "宛先を選んでください";
    }
    if (preset !== key) {
      return key === "day0" ? "いちばん新しい日にちだけ送ります" : "この宛先へまだ送っていないもの";
    }
    if (targetLoading) {
      return "読み込み中…";
    }
    return `${targetMedia.length} 件 ・ ${formatBytes(totalBytes)}`;
  }

  return (
    <section aria-label="送る" className="wrap">
      <button type="button" className="btn sm quiet" onClick={() => navigate("/")}>
        <Icon name="back" size={16} />
        やめる
      </button>
      <h1 className="page">Immich へ送る</h1>

      <ErrorBanner error={error ?? destinationsQuery.error} onDismiss={() => setError(null)} />

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
                className="chip"
                style={{ height: 56, padding: "0 18px" }}
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
            gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
            gap: 10,
          }}
        >
          {presets.map((option) => (
            <label
              key={option.key}
              className="card pad"
              style={{
                display: "flex",
                gap: 11,
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
                <b style={{ display: "block", fontSize: 14 }}>{option.title}</b>
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
            {targetMedia.length} 件のうち、はじめの {Math.min(16, targetMedia.length)} 件
          </span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(58px, 1fr))",
            gap: 7,
          }}
        >
          {targetMedia.slice(0, 16).map((media) => (
            <MediaTile key={media.id} media={media} selected={false} />
          ))}
        </div>
      </section>

      <div className="card pad rowtop">
        <span style={{ color: "var(--ink-2)", flex: "0 0 auto" }}>
          <Icon name="info" />
        </span>
        <p className="muted">
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
        </div>
        <button
          type="button"
          className="btn primary big"
          disabled={targetMedia.length === 0 || chosen.length === 0}
          onClick={() => setConfirming(true)}
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
          }}
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void send()}
        />
      )}
    </section>
  );
}
