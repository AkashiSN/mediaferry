// カメラの種類（§6 / §13）。挿したカードがどの機種かを見分けるための決まり。
//
// 編集は **YAML のテキストエリア 1 枚**にする。`filename_pattern` も
// `timestamp.pattern` も正規表現なので、フォームに落とすと表現力が落ちるうえ、
// 規則が画面とサーバの 2 か所に散る。JSON ではなく YAML なのは、正規表現の
// バックスラッシュを二重に書かずに済むため（ビルトインも YAML で書いてある）。
//
// **slug は画面に出す。** 作成後は変えられない識別子で、複製のときに利用者自身が
// 決めるもの（ライブラリのパスに使う）。表示名を主に出し、slug は補助的に添える。
//
// **カメラ 1 台につき板 1 枚**（`settings/Destinations.tsx` の送り先と同じ形）。
// 操作は 2 行に分ける: 上は**この決まりや取り込み済みの日時を変える**もの、下は
// **判定を見るだけで何も変えない**「試す」。

import { dump, load, YAMLException } from "js-yaml";
import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ConfirmDialog, type Confirmation } from "../../components/ConfirmDialog";
import { ErrorBanner, UserFacingError } from "../../components/ErrorBanner";
import { Icon } from "../../components/Icon";
import { volumeLabel } from "../work/CardDetail";

type Profile = {
  slug: string;
  name: string;
  revision: number;
  revision_id: string;
  builtin: boolean;
  archived: boolean;
};

type ProfileDetail = Profile & { definition: Record<string, unknown> };

type Profiles = { profiles: Profile[] };
type Volume = { volume_instance_id: string; fs_label: string };
type Volumes = { volumes: Volume[] };

/** 新規作成の雛形。**空欄から始めさせない**（必須項目が多い）。 */
const TEMPLATE = {
  slug: "my-camera",
  name: "私のカメラ",
  hints: { usb_ids: [], volume_labels: [] },
  require: { roots: ["DCIM"], filename_pattern: "^.*\\.(JPG|MP4)$", min_matching_files: 1 },
  scan: { roots: ["DCIM"], extensions: ["JPG", "MP4"] },
  timestamp: {
    source: "mtime",
    pattern: null,
    format: null,
    fallback: "mtime",
    timezone_policy: "none",
    timezone: null,
    // mtime が何を表すか。exFAT の OffsetFromUtc を書く機種だけ instant。
    mtime_semantics: "wall_clock",
  },
  merge: {
    enabled: false,
    tolerance_seconds: 5,
    min_part_size_gib: 4,
    sequence_pattern: "",
    output_name: "",
    keep_streams: { video: "primary", audio: "all", timecode: false, data: false },
  },
  // RAW+JPEG を Immich のスタックとして束ねる規則（§6）。先頭の拡張子が primary。
  stack: { enabled: false },
  immich: { tags: [], tag_pre_existing: true, fix_datetime_after_upload: false },
};

/** 編集中の状態。`slug` が `null` なら新規作成（`POST /profiles`）。 */
type Editing = { slug: string | null; text: string; timestamp: string };

/** 複製の入口。slug は作成後 immutable なので、**作る前に決めさせる**（§6）。 */
type Duplicating = { source: string; slug: string; name: string };

export function ProfilesScreen() {
  const profiles = useQuery<Profiles>("/profiles");
  const volumes = useQuery<Volumes>("/devices");
  const [tried, setTried] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [recomputeHint, setRecomputeHint] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [duplicating, setDuplicating] = useState<Duplicating | null>(null);
  const [confirming, setConfirming] = useState<{ confirmation: Confirmation; slug: string } | null>(
    null,
  );
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  /** 編集を開く。**一覧は定義を持たない**ので 1 件だけ読み直す。 */
  async function openEditor(slug: string) {
    setBusy(true);
    setError(null);
    try {
      const detail = await request<ProfileDetail>(`/profiles/${slug}`);
      setEditing({
        slug,
        text: dump(detail.definition, { lineWidth: 100 }),
        // 保存後に「解釈が変わったか」を見るための、開いた時点の写し。
        timestamp: JSON.stringify(detail.definition.timestamp ?? null),
      });
      setNotice(null);
      setRecomputeHint(null);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  function openTemplate() {
    setEditing({ slug: null, text: dump(TEMPLATE, { lineWidth: 100 }), timestamp: "" });
    setNotice(null);
    setRecomputeHint(null);
  }

  async function saveProfile() {
    if (editing === null) {
      return;
    }
    let definition: unknown;
    try {
      // **YAML として読めない段階でサーバへ送らない。** 送ると 400 が返るだけで、
      // どの行が悪いかは分からない（構文の位置を知っているのはパーサだけ）。
      definition = load(editing.text);
    } catch (caught) {
      const line = caught instanceof YAMLException ? (caught.mark?.line ?? 0) + 1 : 0;
      // **1 か所にだけ出す。** 同じ文を `notice`（`role="status"`）にも出すと、
      // 失敗が 2 度読み上げられる。**失敗はバナー側**（`role="alert"`）に置く。
      // 素の `Error` だと `ErrorBanner` が定型文へ潰して行番号が消えるので、
      // 「画面が書いた文言」として包む。
      setError(
        new UserFacingError(
          `YAML として読めません（${line} 行目）。字下げと引用符を確かめてください。`,
        ),
      );
      setNotice(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await request<ProfileDetail>(
        editing.slug === null ? "/profiles" : `/profiles/${editing.slug}`,
        { method: editing.slug === null ? "POST" : "PUT", body: { definition } },
      );
      const changed =
        editing.slug !== null &&
        JSON.stringify((definition as { timestamp?: unknown }).timestamp ?? null) !==
          editing.timestamp;
      setNotice(`「${saved.slug}」を保存しました（版 ${saved.revision}）。`);
      // **自動では走らせない**（§6）。既存データの再計算は明示の操作。
      setRecomputeHint(
        changed
          ? "撮影日時の解釈が変わりました。取り込み済みのファイルは自動では直りません。" +
              "「撮影日時を再計算する」を実行してください。"
          : null,
      );
      setEditing(null);
      profiles.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function duplicate() {
    if (duplicating === null) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const made = await request<ProfileDetail>(`/profiles/${duplicating.source}/duplicate`, {
        method: "POST",
        body: { slug: duplicating.slug, name: duplicating.name },
      });
      setDuplicating(null);
      profiles.reload();
      await openEditor(made.slug);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function archive(slug: string) {
    setBusy(true);
    setError(null);
    try {
      await request(`/profiles/${slug}/archive`, { method: "POST" });
      setNotice(`「${slug}」を候補から外しました。`);
      profiles.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setConfirming(null);
      setBusy(false);
    }
  }

  async function recompute(profile: Profile) {
    setBusy(true);
    setError(null);
    try {
      const started = await request<{ job_id: string }>(`/profiles/${profile.slug}/recompute`, {
        method: "POST",
      });
      setJobId(started.job_id);
      setRecomputeHint(null);
      setNotice(`「${profile.name}」の撮影日時を計算し直しています。ファイルは動きません。`);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  /** 判定を試す。**結果は一致・不一致どちらも理由付きで出す**（§13）。 */
  async function tryOn(profile: Profile, volume: Volume, label: string) {
    setError(null);
    try {
      const result = await request<{ matched: boolean; reason: string | null }>(
        `/profiles/${profile.slug}/test?volume_instance_id=${volume.volume_instance_id}`,
        { method: "POST" },
      );
      setTried(
        `「${profile.name}」と ${label}: ` +
          (result.matched ? "一致します" : `一致しません（${result.reason ?? "理由不明"}）`),
      );
    } catch (caught) {
      setError(caught);
    }
  }

  const cards = volumes.data?.volumes ?? [];

  return (
    <section aria-label="カメラの種類" className="wrap">
      <div className="row">
        <Link to="/settings" className="btn sm">
          <Icon name="back" size={16} />
          設定へ
        </Link>
        {/* **作る操作は一覧の外に置く。** 一覧はカメラ 1 台につき 1 枚の板なので、
            末尾に混ぜると最後のカメラの操作に見える。 */}
        <button
          type="button"
          className="btn sm outline"
          style={{ marginLeft: "auto" }}
          disabled={busy}
          onClick={openTemplate}
        >
          新しく作る
        </button>
      </div>
      <h1 className="page title-lg">カメラの種類</h1>

      <ErrorBanner error={error ?? profiles.error} onDismiss={() => setError(null)} />

      <p className="muted">
        挿したカードがどの機種かを見分けるための決まりです。ふだん触る必要はありません。
        保存すると<strong>新しい版</strong>ができます。取り込み済みのファイルの解釈は、
        そのとき使った版のまま変わりません。
      </p>

      {notice && <p role="status" className="muted">{notice}</p>}
      {recomputeHint && (
        <p role="status" className="card pad wn">
          {recomputeHint}
        </p>
      )}
      {jobId && (
        <p>
          <Link to="/settings/jobs">作業の進み具合を見る</Link>
        </p>
      )}
      {tried && <p role="status" className="muted">{tried}</p>}

      {duplicating && (
        <section className="card pad" aria-label="複製">
          <div className="sechead">
            <h2>複製して変える</h2>
          </div>
          <p className="muted" style={{ marginTop: 6 }}>
            複製元: {duplicating.source}。slug は
            <strong>作成後に変更できません</strong>（ライブラリのパスに使います）。
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 12 }}>
            <label className="formrow">
              新しい slug
              <input
                className="field"
                value={duplicating.slug}
                onChange={(event) =>
                  setDuplicating({ ...duplicating, slug: event.currentTarget.value })
                }
              />
            </label>
            <label className="formrow">
              表示名
              <input
                className="field"
                value={duplicating.name}
                onChange={(event) =>
                  setDuplicating({ ...duplicating, name: event.currentTarget.value })
                }
              />
            </label>
          </div>
          <div className="acts" style={{ marginTop: 14 }}>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => void duplicate()}
            >
              複製する
            </button>
            <button
              type="button"
              className="btn sm"
              disabled={busy}
              onClick={() => setDuplicating(null)}
            >
              やめる
            </button>
          </div>
        </section>
      )}

      {editing && (
        <section className="card pad" aria-label="定義の編集">
          <label className="formrow" htmlFor="profile-definition">
            カメラの種類の定義（YAML）
          </label>
          <textarea
            id="profile-definition"
            className="field"
            rows={24}
            value={editing.text}
            onChange={(event) => setEditing({ ...editing, text: event.currentTarget.value })}
          />
          <div className="acts" style={{ marginTop: 14 }}>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => void saveProfile()}
            >
              保存する
            </button>
            <button type="button" className="btn sm" disabled={busy} onClick={() => setEditing(null)}>
              やめる
            </button>
          </div>
        </section>
      )}

      {/* **カメラ 1 台を 1 枚の板にする。** 区切りの無い 1 枚に見出しと操作を
          積むと、ボタンが上下どちらのカメラのものか読めない。1 件ずつ板に分ける
          のは `settings/Destinations.tsx` の送り先と同じ形。 */}
      {(profiles.data?.profiles ?? []).map((profile) => (
        <section key={profile.slug} className="card pad">
          <div className="rowtop">
            <div className="grow">
              <h2 style={{ fontSize: 16, fontWeight: 650 }}>{profile.name}</h2>
              <p className="small ident" style={{ marginTop: 4 }}>
                {profile.slug} ・ 版 {profile.revision}
                {/* ビルトインは次のアプリ更新で上書きされるので編集させない（§6）。 */}
                {profile.builtin && " ・ 最初から入っているので編集できません"}
                {profile.archived && " ・ 候補から外してあります"}
              </p>
            </div>
          </div>
          {/* このカメラの決まりを変える操作。**枠のある素のボタン**で出す。 */}
          <div className="acts" style={{ marginTop: 14 }}>
            {profile.builtin ? (
              <button
                type="button"
                className="btn sm"
                aria-label={`複製して変える：${profile.name}`}
                disabled={busy}
                onClick={() =>
                  setDuplicating({
                    source: profile.slug,
                    slug: `${profile.slug}-copy`,
                    name: `${profile.name} の複製`,
                  })
                }
              >
                複製して変える
              </button>
            ) : (
              <button
                type="button"
                className="btn sm"
                aria-label={`編集：${profile.name}`}
                disabled={busy}
                onClick={() => void openEditor(profile.slug)}
              >
                編集
              </button>
            )}
            <button
              type="button"
              className="btn sm"
              aria-label={`撮影日時を再計算する：${profile.name}`}
              disabled={busy}
              onClick={() => void recompute(profile)}
            >
              撮影日時を再計算する
            </button>
            {!profile.builtin && !profile.archived && (
              <button
                type="button"
                className="btn sm quiet"
                aria-label={`候補から外す：${profile.name}`}
                disabled={busy}
                onClick={() =>
                  setConfirming({
                    confirmation: { kind: "archive_profile", slug: profile.slug },
                    slug: profile.slug,
                  })
                }
              >
                候補から外す
              </button>
            )}
          </div>
          {/* **試すだけの操作は、変える操作と混ぜない。** 上の 2 つはこのカメラの
              決まりや取り込み済みの日時を変えるが、こちらは判定を見るだけで何も
              変わらない。行を分けて、そう書き添える。 */}
          {cards.length > 0 && (
            <>
              <p className="small" style={{ marginTop: 14 }}>
                いま挿さっているカードで、この決まりが当たるか試せます（何も変わりません）。
              </p>
              <div className="acts" style={{ marginTop: 8 }}>
                {cards.map((volume) => {
                  const label = volumeLabel(cards, volume);
                  return (
                    <button
                      key={volume.volume_instance_id}
                      type="button"
                      className="btn sm quiet"
                      aria-label={`${profile.name} を ${label} で試す`}
                      disabled={busy}
                      onClick={() => void tryOn(profile, volume, label)}
                    >
                      {label} で試す
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </section>
      ))}

      {confirming && (
        <ConfirmDialog
          confirmation={confirming.confirmation}
          busy={busy}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void archive(confirming.slug)}
        />
      )}
    </section>
  );
}
