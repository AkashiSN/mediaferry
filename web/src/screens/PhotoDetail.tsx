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
  /** その送信レコードの id。まだ送っていない宛先（`state === null`）では `null`。 */
  upload_id: string | null;
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
  /** この 1 件が属する組（RAW+JPEG。組でなければ `null`）。**主が先頭。** */
  stack: { members: { id: string; rel_path: string; size_bytes: number }[] } | null;
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
  // 宛先ごとの操作（送り直す・サーバを確かめる）。**消す・グループへの操作とは
  // 別の状態で持つ** —— 確認を挟まない即時操作なので、他の busy と混ぜると
  // 無関係な操作までボタンが押せなくなる。
  // **busy は全宛先で 1 つを共有する。** どれか 1 つを操作している間は、
  // 他の宛先のボタンも押せなくする（連打で同じ操作が二重に飛ぶのを防ぐのが
  // 目的で、宛先ごとに分けるほどの重さの操作ではない）。
  const acting = useMutation();

  const data = detail.data;
  // **置き換えられた組にも、何だったのかは出す。** 検証の結果は「なぜこの出力が
  // 残っているのか」の手がかりで、行き止まりにしないための材料でもある。
  // **ただし操作は出さない** —— 構成そのものが古く、押しても何も起きない
  // （つなぐ画面の判断と同じ）。
  const group = data?.group ?? null;
  const editable = group !== null && group.superseded_by_id === null;

  // 送るものとして選んでいる member。**組でなければ `null`**（この 1 件を送る）。
  // **既定は全部オン** —— 一覧の丸が組ごとに選ぶのと揃える。外したい人だけが
  // 操作する。送るまでの一時的な選択なので、URL には持たない。
  const members = data?.stack?.members ?? null;
  // **id の並びで見る。** `members` は毎回新しい配列なので、そのまま比べると
  // 描画のたびに選択が既定へ戻る。
  const memberKey = members === null ? "" : members.map((member) => member.id).join(",");
  const [includedKey, setIncludedKey] = useState("");
  const [included, setIncluded] = useState<Set<string> | null>(null);
  // **描画の途中で比べてその場で更新する。** `useEffect` で書くと、このリポジトリの
  // react-hooks/set-state-in-effect（「効果の中で同期的に setState する」形を
  // 避けるルール）に引っかかる（`Photos.tsx` の並び替えと同じ形）。
  if (includedKey !== memberKey) {
    setIncludedKey(memberKey);
    setIncluded(memberKey === "" ? null : new Set(memberKey.split(",")));
  }

  /** 「送る」へ渡す id。**組ならチェックの付いたぶんだけ**、組でなければこの 1 件。 */
  const sendingIds = data === null ? [] : included === null ? [data.id] : [...included];
  // **組のときだけ出す節でしか使わない。** `members` が `null` の値は実際には
  // 描画されない（呼び出し側が `members !== null` で包む）ので、フォールバックは
  // 空配列だけで足りる。
  const sendingBytes = (members ?? [])
    .filter((member) => included?.has(member.id))
    .reduce((sum, member) => sum + member.size_bytes, 0);

  /** グループへの操作。**成功したら詳細を引き直す**（検証も採用済みの印も変わる）。 */
  async function act(path: string, body?: unknown): Promise<void> {
    if (await edit.run(() => request(path, { method: "PATCH", body }))) {
      detail.reload();
    }
    setGroupConfirm(null);
  }

  /** リモートから消えたと確認できた送信を、pending へ戻して送り直す。 */
  async function requeue(uploadId: string): Promise<void> {
    if (await acting.run(() => request(`/uploads/${uploadId}/requeue`, { method: "POST" }))) {
      detail.reload();
    }
  }

  /** その宛先の状態をサーバへ問い合わせ直す。Immich 側で消した資産にはこれで気づく。 */
  async function recheck(destinationId: string): Promise<void> {
    if (
      await acting.run(() => request(`/destinations/${destinationId}/recheck`, { method: "POST" }))
    ) {
      detail.reload();
    }
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
        error={detail.error ?? deletion.error ?? edit.error ?? acting.error}
        onDismiss={() => {
          deletion.clear();
          edit.clear();
          acting.clear();
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

          {members !== null && (
            <section className="card pad">
              <div className="sechead" style={{ marginBottom: 12 }}>
                <h2>この 1 枚を作っているファイル</h2>
              </div>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
                {members.map((member) => (
                  // **選ぶ的と開く的を分ける**（タイルの「丸で選ぶ・絵で開く」と
                  // 同じ形）。従は一覧に出ないので、名前からその 1 件へ行けないと
                  // 「宛先ごとの状況」も「消す」も届かなくなる（§13）。
                  <li key={member.id} className="row">
                    <input
                      type="checkbox"
                      checked={included?.has(member.id) ?? false}
                      aria-label={`送る：${fileName(member.rel_path)}`}
                      onChange={() =>
                        setIncluded((current) => {
                          const next = new Set(current ?? []);
                          if (next.has(member.id)) {
                            next.delete(member.id);
                          } else {
                            next.add(member.id);
                          }
                          return next;
                        })
                      }
                    />
                    <Link to={`/photos/${member.id}`} className="ident grow">
                      {fileName(member.rel_path)}
                    </Link>
                    <span className="small">{formatBytes(member.size_bytes)}</span>
                  </li>
                ))}
              </ul>
              <p className="small" style={{ marginTop: 10 }}>
                {sendingIds.length} 枚 ・ {formatBytes(sendingBytes)} を送ります
              </p>
            </section>
          )}

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
                {data.destinations.map((dest) => {
                  // **ローカル変数に写す。** `dest.upload_id` のまま条件分岐すると、
                  // クロージャの中では絞り込みが効かず `string | null` のまま残る。
                  const uploadId = dest.upload_id;
                  return (
                    // **状況は名前の直下に置く。** 左右に離して置くと、狭い画面では
                    // 状況だけが次の行へ落ち、どの宛先の状況なのか読めなくなる。
                    <li key={dest.destination_id} className="row">
                      <div className="grow">
                        <div style={{ fontSize: "13.5px", fontWeight: 600 }}>{dest.name}</div>
                        <div className="small">{PRESENCE[dest.presence] ?? dest.presence}</div>
                      </div>
                      {/* **`presence === "gone"` が送り直せる条件そのもの。**
                          API 側（`remote_asset_id IS NULL` かつ `remote_checked_at IS
                          NOT NULL` な `complete`）と同じ判断を、画面は `presence` の
                          語彙だけで見る。組み直さない。 */}
                      {dest.presence === "gone" && uploadId !== null && (
                        // **`aria-label` に宛先名を足す。** `gone` な宛先が 2 つ以上
                        // 並ぶと、同じ「送り直す」が名前だけでは見分けられない
                        // （member の「送る：${name}」と同じ規約）。
                        <button
                          type="button"
                          className="btn sm"
                          aria-label={`送り直す：${dest.name}`}
                          disabled={acting.busy}
                          onClick={() => void requeue(uploadId)}
                        >
                          送り直す
                        </button>
                      )}
                      {/* Immich 側で消しても、この画面へは再確認するまで反映されない。
                          気づく手段が設定の奥にしか無いと辿り着けないので、ここにも置く。 */}
                      {dest.state !== null && (
                        <button
                          type="button"
                          className="btn sm quiet"
                          aria-label={`サーバを確かめる：${dest.name}`}
                          disabled={acting.busy}
                          onClick={() => void recheck(dest.destination_id)}
                        >
                          サーバを確かめる
                        </button>
                      )}
                    </li>
                  );
                })}
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
              disabled={sendingIds.length === 0}
              onClick={() =>
                navigate("/send", {
                  state: { ids: sendingIds, destinationIds: [] },
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
