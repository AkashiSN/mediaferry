// 1 件のくわしく（§13）。写真タブでタイルを押すと開く。**ここで「それが何かを知り、
// いらなければ消す」が完結する** —— API は `GET /media/{id}` の 1 本で描くのに
// 必要なものをすべて返す（複数の API を継ぎ足すと片方だけ古い状態が出るため）。

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { request } from "../api/client";
import { useMutation, useQuery } from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { Confirmation } from "../components/ConfirmDialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { Icon } from "../components/Icon";
import { fileName } from "../components/MediaTile";
import { formatBytes } from "../utils/formatBytes";
import { formatDateTime } from "../utils/formatDateTime";
// **つなぐ画面と同じ部品・同じ判断を使う。** 書き写すと、採用できる条件が
// 2 か所に分かれ、片方だけが古くなる（`SENDABLE_CLAUSE` と揃っていないと、
// 押しても送れるようにならないボタンが出る）。
import {
  adoptable,
  failureReason,
  RegroupDialog,
  VerificationResult,
  type Group,
} from "./work/Merge";

type SourceItem = {
  media_file_id: string;
  rel_path: string;
  position: number;
  missing: boolean;
};

type DestinationItem = {
  destination_id: string;
  name: string;
  state: string | null;
  presence: string;
};

type MediaDetail = {
  id: string;
  role: "original" | "derived";
  rel_path: string;
  size_bytes: number;
  kind: string;
  captured_at: string;
  captured_at_source: string;
  duration_seconds: number | null;
  probe_state: string;
  missing_at: string | null;
  sources: SourceItem[];
  destinations: DestinationItem[];
  deletable: boolean;
  delete_blocked_reason: string | null;
  delete_frees_sources: boolean;
  /** この出力を持っているグループ（元のファイルなら `null`）。 */
  group: Group | null;
};

/**
 * 宛先ごとの状況（§13 の 7 語）。**サーバは語彙を返し、日本語にするのはここだけ**
 * ——`_presence`（`api/routes_media.py`）が返す 7 語をここで初めて日本語にする。
 */
const PRESENCE: Record<string, string> = {
  not_sent: "まだ送っていません",
  sending: "送っている最中です",
  present: "Immich に入っています",
  trashed: "Immich のゴミ箱にあります",
  gone: "Immich にはもうありません",
  unknown: "Immich にあるか確かめていません",
  failed: "送れませんでした",
};

/** 動画の長さを `分:秒` に丸める（`MediaTile` の `formatClipLength` と同じ書式）。 */
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

/** 確認に出すグループの名乗り。**内部の ID を出さない**（§13）ので、元のファイル名を使う。 */
function groupLabel(data: MediaDetail): string {
  return data.sources[0]?.rel_path ?? fileName(data.rel_path);
}

export function PhotoDetailScreen() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const detail = useQuery<MediaDetail>(id === undefined ? "" : `/media/${id}`, [id]);
  const deletion = useMutation();
  const [confirming, setConfirming] = useState(false);
  // グループへの操作（採用・やり直し・構成の変更・別々にする）。**消すのとは
  // 別の状態で持つ** —— 片方の確認が開いている間に、もう片方が実行されないように。
  const edit = useMutation();
  const [groupConfirm, setGroupConfirm] = useState<{
    value: Confirmation;
    run: () => Promise<unknown>;
  } | null>(null);
  const [regrouping, setRegrouping] = useState(false);

  const data = detail.data;
  // **置き換えられた組にも、何だったのかは出す。** 検証の結果は「なぜこの出力が
  // 残っているのか」の手がかりで、行き止まりにしないための材料でもある。
  // **ただし操作は出さない** —— 構成そのものが古く、押しても何も起きない
  // （つなぐ画面の判断と同じ）。
  const group = data?.group ?? null;
  const editable = group !== null && group.superseded_by_id === null;

  /** グループへの操作。**成功したら詳細を引き直す**（検証も採用済みの印も変わる）。 */
  async function act(path: string, body?: unknown): Promise<void> {
    if (await edit.run(() => request(path, { method: "PATCH", body }))) {
      detail.reload();
    }
    setGroupConfirm(null);
  }

  async function runDelete() {
    if (id === undefined) {
      return;
    }
    if (await deletion.run(() => request(`/media/${id}`, { method: "DELETE" }))) {
      // **消したものの画面に留まらない**（§13）。写真タブへ戻す。
      navigate("/photos");
      return;
    }
    setConfirming(false);
  }

  const confirmation: Confirmation | null =
    data === null
      ? null
      : {
          kind: "delete_merged_video",
          name: fileName(data.rel_path),
          sourceCount: data.sources.length,
          freesSources: data.delete_frees_sources,
        };

  return (
    <section aria-label="くわしく" className="wrap">
      <div className="row">
        <Link to="/photos" className="btn sm">
          <Icon name="back" size={16} />
          写真へ
        </Link>
      </div>

      {/* **画面が持つ失敗は 1 本**（帯も 1 本）。消すのとグループへの操作を分けて
          出すと、どちらの話なのか読む側には分からない。 */}
      <ErrorBanner
        error={detail.error ?? deletion.error ?? edit.error}
        onDismiss={() => {
          deletion.clear();
          edit.clear();
        }}
      />

      {data && (
        <>
          <img
            src={`/api/media/${data.id}/thumbnail`}
            alt=""
            style={{
              width: "100%",
              maxHeight: 360,
              objectFit: "contain",
              borderRadius: 12,
            }}
          />

          <div>
            {/* **`.ident` を外さない。** ファイル名は `_` でも `/` でも折り返さない
                1 語として扱われるので、狭い画面では箱の外へ流れ出る。 */}
            <h1 className="page title-lg ident">{fileName(data.rel_path)}</h1>
            {data.role === "derived" && (
              <p className="muted">つないだ動画（{data.sources.length} 本から）</p>
            )}
            <p className="small">
              {formatDateTime(data.captured_at)}
              {data.kind === "video" && data.duration_seconds != null
                ? ` ・ ${formatDuration(data.duration_seconds)}`
                : ""}
              {` ・ ${formatBytes(data.size_bytes)}`}
            </p>
          </div>

          <section className="card pad">
            {/* **`.sechead` で包む。** 素の `<h2>` はブラウザ既定の大きさで描かれ、
                画面の見出し（`h1.page.title-lg`）とほとんど変わらなくなる。 */}
            <div className="sechead" style={{ marginBottom: 12 }}>
              <h2>宛先ごとの状況</h2>
            </div>
            {data.destinations.length === 0 ? (
              <p className="small">宛先がありません。</p>
            ) : (
              <ul
                style={{
                  listStyle: "none",
                  display: "flex",
                  flexDirection: "column",
                  gap: 12,
                }}
              >
                {data.destinations.map((dest) => (
                  // **状況は名前の直下に置く。** 左右に離して置くと、狭い画面では
                  // 状況だけが次の行へ落ち、どの宛先の状況なのか読めなくなる。
                  <li key={dest.destination_id} className="row">
                    <div className="grow">
                      <div style={{ fontSize: "13.5px", fontWeight: 600 }}>{dest.name}</div>
                      <div className="small">{PRESENCE[dest.presence] ?? dest.presence}</div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {data.role === "derived" && (
            <section className="card pad">
              <div className="sechead" style={{ marginBottom: 12 }}>
                <h2>元になったファイル</h2>
              </div>
              {data.sources.length === 0 ? (
                <p className="small">見つかりません。</p>
              ) : (
                <ul
                  style={{
                    listStyle: "none",
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                  }}
                >
                  {[...data.sources]
                    .sort((left, right) => left.position - right.position)
                    .map((source) => (
                      <li key={source.media_file_id}>
                        <Link to={`/photos/${source.media_file_id}`} className="ident">
                          {fileName(source.rel_path)}
                        </Link>
                        {source.missing && <span className="small"> （見当たりません）</span>}
                      </li>
                    ))}
                </ul>
              )}
            </section>
          )}

          {group !== null && (
            <section className="card pad">
              <div className="sechead" style={{ marginBottom: 12 }}>
                <h2>つないだ結果</h2>
              </div>
              {group.verification !== null && (
                <VerificationResult verification={group.verification} />
              )}
              {group.adopted_at !== null && (
                <p className="small" style={{ marginTop: 6 }}>
                  中身を見て採用しました。
                </p>
              )}
              {/* **行き止まりにしない**（§13）。使えない理由と、次にやることを書く。 */}
              {group.profile_changed && group.status === "merged" && (
                <p className="small" style={{ marginTop: 6, color: "var(--warn)" }}>
                  つないだあとにカメラの種類が変わったので、この結果はもう使えません。「同じ構成でやり直す」でつなぎ直してください。
                </p>
              )}
              {!editable && (
                <p className="small" style={{ marginTop: 6 }}>
                  つなぎ直しで置き換わった結果なので、ここからの操作はありません。
                </p>
              )}
              <div className="acts" style={{ marginTop: 14 }}>
                {editable && adoptable(group) && (
                  // §10 の `adopted_derived`。**検証に落ちた出力は、人が中身を見て
                  // 採用しない限り送る候補に出ない**（`SENDABLE_CLAUSE` は `passed` か
                  // `adopted_at` を見る）。**ここがその唯一の入口。**
                  <button
                    type="button"
                    className="btn sm"
                    disabled={edit.busy}
                    onClick={() =>
                      setGroupConfirm({
                        value: {
                          kind: "adopt_failed_merge",
                          groupLabel: groupLabel(data),
                          reason: failureReason(
                            group.verification as NonNullable<typeof group.verification>,
                          ),
                        },
                        run: () => act(`/merge-groups/${group.id}?action=adopt`),
                      })
                    }
                  >
                    中身を見て、これを使う
                  </button>
                )}
                {editable && (
                  <>
                    <button
                      type="button"
                      className="btn sm"
                      disabled={edit.busy}
                      onClick={() =>
                        setGroupConfirm({
                          value: {
                            kind: "remerge_group",
                            groupLabel: groupLabel(data),
                          },
                          run: () =>
                            act(`/merge-groups/${group.id}?action=regroup`, {
                              media_ids: group.members.map((member) => member.media_file_id),
                            }),
                        })
                      }
                    >
                      同じ構成でやり直す
                    </button>
                    <button
                      type="button"
                      className="btn sm"
                      disabled={edit.busy}
                      onClick={() => setRegrouping(true)}
                    >
                      構成を変える
                    </button>
                    <button
                      type="button"
                      className="btn sm"
                      disabled={edit.busy}
                      onClick={() =>
                        setGroupConfirm({
                          value: {
                            kind: "discard_merge_group",
                            groupLabel: groupLabel(data),
                            publishedCount: group.status === "merged" ? 1 : 0,
                          },
                          run: () => act(`/merge-groups/${group.id}?action=discard`),
                        })
                      }
                    >
                      これは別々
                    </button>
                  </>
                )}
              </div>
            </section>
          )}

          <div className="acts">
            <button
              type="button"
              className="btn primary"
              onClick={() =>
                navigate("/send", {
                  state: { ids: [data.id], destinationIds: [] },
                })
              }
            >
              送る
            </button>
            <button
              type="button"
              className="btn"
              disabled={!data.deletable}
              onClick={() => setConfirming(true)}
            >
              消す
            </button>
          </div>
          {!data.deletable && data.delete_blocked_reason !== null && (
            <p className="small">{data.delete_blocked_reason}</p>
          )}
        </>
      )}

      {confirming && confirmation !== null && (
        <ConfirmDialog
          confirmation={confirmation}
          busy={deletion.busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void runDelete()}
        />
      )}

      {regrouping && group !== null && (
        <RegroupDialog
          group={group}
          onCancel={() => setRegrouping(false)}
          onSubmit={(mediaIds) => {
            setRegrouping(false);
            void act(`/merge-groups/${group.id}?action=regroup`, {
              media_ids: mediaIds,
            });
          }}
        />
      )}

      {groupConfirm !== null && (
        <ConfirmDialog
          confirmation={groupConfirm.value}
          busy={edit.busy}
          onCancel={() => setGroupConfirm(null)}
          onConfirm={() => void groupConfirm.run()}
        />
      )}
    </section>
  );
}
