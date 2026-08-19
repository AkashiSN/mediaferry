// 設定とプロファイル（§12 / §13）。**env 由来は錠前付きの読み取り専用。**
//
// プロファイルの編集は **YAML のテキストエリア 1 枚**にする。`filename_pattern` も
// `timestamp.pattern` も正規表現なので、フォームに落とすと表現力が落ちるうえ、
// 規則が画面とサーバの 2 か所に散る。JSON ではなく YAML なのは、正規表現の
// バックスラッシュを二重に書かずに済むため（ビルトインも YAML で書いてある）。

import { dump, load, YAMLException } from "js-yaml";
import { useState } from "react";
import { Link } from "react-router-dom";

import { request } from "../api/client";
import { useQuery } from "../api/hooks";
import { ConfirmDialog, type Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";

type Setting = {
  key: string;
  value: string | null;
  source: string;
  locked: boolean;
  tier: string;
  writable: boolean;
};

type Profile = {
  slug: string;
  name: string;
  revision: number;
  revision_id: string;
  builtin: boolean;
  archived: boolean;
};

type ProfileDetail = Profile & { definition: Record<string, unknown> };

type Settings = { settings: Setting[]; warnings: { code: string; message: string }[] };
type Profiles = { profiles: Profile[] };
type Volumes = { volumes: { volume_instance_id: string; fs_label: string | null }[] };

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

export function SettingsScreen() {
  const settings = useQuery<Settings>("/settings");
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

  async function save(key: string, value: string) {
    setBusy(true);
    setError(null);
    try {
      await request("/settings", { method: "PUT", body: { key, value } });
      settings.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

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
      setError(
        new Error(`YAML として読めません（${line} 行目）。字下げと引用符を確かめてください。`),
      );
      setNotice(`YAML として読めません（${line} 行目）。字下げと引用符を確かめてください。`);
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

  async function recompute(slug: string) {
    setBusy(true);
    setError(null);
    try {
      const started = await request<{ job_id: string }>(`/profiles/${slug}/recompute`, {
        method: "POST",
      });
      setJobId(started.job_id);
      setRecomputeHint(null);
      setNotice(`「${slug}」の撮影日時を再計算しています。ファイルは動きません。`);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="設定">
      <h1>設定</h1>
      <ErrorBanner error={error ?? settings.error} onDismiss={() => setError(null)} />
      <table>
        <thead>
          <tr>
            <th>キー</th>
            <th>値</th>
            <th>出所</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(settings.data?.settings ?? []).map((setting) => (
            <tr key={setting.key}>
              <td>{setting.key}</td>
              <td>
                {setting.writable ? (
                  <input
                    defaultValue={setting.value ?? ""}
                    aria-label={`${setting.key} の値`}
                    onBlur={(event) => void save(setting.key, event.currentTarget.value)}
                    disabled={busy}
                  />
                ) : (
                  <span>{setting.value ?? "（未設定）"}</span>
                )}
              </td>
              <td>
                {setting.source}
                {/* env 由来は TrueNAS のアプリ設定で固定されている（§12）。 */}
                {setting.locked && <span title="環境変数で固定されています">🔒</span>}
              </td>
              <td>{setting.tier}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>デバイスプロファイル</h2>
      <p>
        保存すると<strong>新しいリビジョン</strong>ができます。過去に取り込んだデータの
        解釈は、そのとき使ったリビジョンのまま変わりません。
      </p>
      {notice && <p role="status">{notice}</p>}
      {recomputeHint && <p role="status">{recomputeHint}</p>}
      {jobId && (
        <p>
          <Link to="/jobs">ジョブの進捗を見る</Link>
        </p>
      )}
      {tried && <p role="status">{tried}</p>}
      <button type="button" disabled={busy} onClick={openTemplate}>
        プロファイルを新規作成
      </button>

      {duplicating && (
        <div aria-label="複製">
          <p>
            複製元: {duplicating.source}。slug は
            <strong>作成後に変更できません</strong>（ライブラリのパスに使います）。
          </p>
          <label>
            新しい slug
            <input
              value={duplicating.slug}
              onChange={(event) =>
                setDuplicating({ ...duplicating, slug: event.currentTarget.value })
              }
            />
          </label>
          <label>
            表示名
            <input
              value={duplicating.name}
              onChange={(event) =>
                setDuplicating({ ...duplicating, name: event.currentTarget.value })
              }
            />
          </label>
          <button type="button" disabled={busy} onClick={() => void duplicate()}>
            複製する
          </button>
          <button type="button" disabled={busy} onClick={() => setDuplicating(null)}>
            やめる
          </button>
        </div>
      )}

      {editing && (
        <div aria-label="プロファイルの編集">
          <label htmlFor="profile-definition">プロファイル定義（YAML）</label>
          <textarea
            id="profile-definition"
            rows={24}
            value={editing.text}
            onChange={(event) => setEditing({ ...editing, text: event.currentTarget.value })}
          />
          <button type="button" disabled={busy} onClick={() => void saveProfile()}>
            保存する
          </button>
          <button type="button" disabled={busy} onClick={() => setEditing(null)}>
            やめる
          </button>
        </div>
      )}

      <ul>
        {(profiles.data?.profiles ?? []).map((profile) => (
          <li key={profile.slug}>
            {profile.name}（{profile.slug}） 版 {profile.revision}
            {/* ビルトインは次のアプリ更新で上書きされるので編集させない（§6）。 */}
            {profile.builtin && <span title="ビルトインは編集できません">🔒</span>}
            {profile.archived && <span>（候補から外してあります）</span>}
            {profile.builtin ? (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setDuplicating({
                    source: profile.slug,
                    slug: `${profile.slug}-copy`,
                    name: `${profile.name} の複製`,
                  })
                }
              >
                {profile.slug} を複製して編集
              </button>
            ) : (
              <button type="button" disabled={busy} onClick={() => void openEditor(profile.slug)}>
                {profile.slug} を編集
              </button>
            )}
            {!profile.builtin && !profile.archived && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setConfirming({
                    confirmation: { kind: "archive_profile", slug: profile.slug },
                    slug: profile.slug,
                  })
                }
              >
                {profile.slug} を候補から外す
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => void recompute(profile.slug)}
            >
              {profile.slug} の撮影日時を再計算する
            </button>
            {(volumes.data?.volumes ?? []).map((volume) => (
              <button
                key={volume.volume_instance_id}
                type="button"
                disabled={busy}
                onClick={() =>
                  void request<{ matched: boolean; reason: string | null }>(
                    `/profiles/${profile.slug}/test?volume_instance_id=${volume.volume_instance_id}`,
                    { method: "POST" },
                  )
                    .then((result) =>
                      setTried(
                        `${profile.slug} × ${volume.fs_label ?? volume.volume_instance_id}: ` +
                          (result.matched ? "一致" : `一致しない（${result.reason ?? "理由不明"}）`),
                      ),
                    )
                    .catch(setError)
                }
              >
                {profile.slug} を {volume.fs_label ?? volume.volume_instance_id} で試す
              </button>
            ))}
          </li>
        ))}
      </ul>

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
