# Phase 17 の設計 —— 撮影地のタイムゾーンをファイルごとに上書きする

**この文書が実装の正本。** 現在の仕様は [`../design.md`](../design.md)、決着した判断は
[`../decisions.md`](../decisions.md)。

## この回が何か

DJI Osmo Pocket の時計を JST のままにしてベトナム（UTC+7）で撮った。取り込むと、
ファイル名の壁時計（JST）に `Asia/Tokyo` のオフセットが付く。

```
DJI_20260922201631_0055_D.MP4 → captured_at = 2026-09-22T20:16:31+09:00
```

**瞬間はこれで合っている。** 合っていないのは「どの地の壁時計で見せるか」で、Immich は
現地で 18:16 に撮った動画を 20:16 と表示する。

プロファイルの `timezone` と `MEDIAFERRY_DEFAULT_TIMEZONE` が表すのは**カメラの時計の
ゾーン**で、これは JST で正しい。撮影地は旅行ごとに違い、カード 1 枚の中でも混ざる。
**取り込んだ後に、選んだファイルだけ撮影地のゾーンへ付け替えたい。**

## 決めたこと

- **瞬間は変えない。付け替えるのは表示のゾーンだけ**（利用者の裁定、2026-09-23。
  「時計の数字をそのままオフセットだけ替える」案は採らない —— 時計を JST のまま
  持って行ったのだから、数字は JST の数字である）
  `2026-09-22T20:16:31+09:00` → `2026-09-22T18:16:31+07:00`
- **上書きはファイルごとに DB へ持つ**（`media_file.captured_at_zone_override`）。
  `captured_at` を書き換えるだけにすると、プロファイルを保存して
  `recompute_timestamps` を走らせたときに、黙って JST へ戻る
- **上書きを持つのは `original` だけ。** `derived` は今と同じく先頭の active member
  から継ぐ（`Merger._captured_of` / `Recomputer._recomputed_derived`）
- **API は同期で 1 トランザクション。** ファイルを読まず DB の値の変換だけなので、
  ジョブにしない
- **送信済みは `needs_recheck` へ戻す。** `recompute_timestamps` と同じ扱いで、
  次の送信で Immich の日時が直る

## 1. データ

移行 `0002` で列を 1 本足す。

```sql
ALTER TABLE media_file ADD COLUMN captured_at_zone_override TEXT;
```

- NULL は「上書きなし」。値は IANA のゾーン名（`zoneinfo.available_timezones()` に
  含まれるもの）
- `derived` の行では常に NULL（書くのは API だけで、API は `original` にしか書かない）
- **CHECK は付けない。** ゾーン名の正しさは SQL で判定できない。境界（API）で検める

## 2. 撮影日時の算出

上書きの適用は `core/timestamps.py` の純粋関数 1 つに置く。

```python
def with_zone_override(value: CapturedAt, zone_name: str | None) -> CapturedAt:
    """瞬間を保ったまま、表示のゾーンを付け替える. None なら何もしない."""
```

- `at` は `value.at.astimezone(ZoneInfo(zone_name))`、`tz` は `zone_name`
- `source` と `note` は保つ（出所は変わらない。DST の曖昧さの記録も、カメラの時計の
  ゾーンで壁時計を解いたときのものなので残す）
- **`timezone_policy: none` のプロファイルには適用できない。** そこでは `at` が
  「UTC の札を貼った壁時計」で瞬間ではないので、付け替えても意味を成さない。
  API が 400 で断る（§3）

使う場所は 3 つ。

| どこ | 何をする |
| --- | --- |
| `POST /media/timezone` | 保存済みの `captured_at` に適用して書く（§3） |
| `Recomputer._recomputed_original` | `resolve_captured_at` の結果に適用する。**再計算で上書きが消えない** |
| `derived` | 何もしない。継ぐ元の member が既に付け替えられている |

**API は再計算しない。** 保存済みの `captured_at` をそのまま変換する。瞬間を変えない
操作なので、ファイル名や EXIF から解き直す必要が無い。上書きを外すとき（`null`）は、
**カメラの時計のゾーン**（`captured_at_revision_id` の版の `timestamp.timezone`、
無ければ `DEFAULT_TIMEZONE`）へ付け替える。これも瞬間を保つ変換である。

## 3. API

```
POST /media/timezone
{ "ids": ["<media_file.id>", ...], "timezone": "Asia/Ho_Chi_Minh" | null }
→ 200 { "changed": n, "unchanged": n, "requeued": n }
```

1. `timezone` を検める。`available_timezones()` に無ければ 400
2. `ids` を解決する。**`derived` の id はその active member（全員）へ置き換える**。
   写真の一覧は結合した動画もタイルとして出すので、利用者はそれを選びうる
3. どれか 1 件でもプロファイルが `timezone_policy: none` なら、**全体を 400 で断る**
   （一部だけ適用すると、どれが直ったか画面から読めない）。見つからない id も 400
4. `BEGIN IMMEDIATE` の中で、対象の `original` ごとに
   - `captured_at_zone_override` を書く
   - `captured_at` / `captured_at_tz` を §2 の変換で書き直す
   - 値が動いたら、`Recomputer` と同じ差し戻し（`complete` → `needs_recheck`）と
     スタック見送りの戻しを行う
5. 同じトランザクションで、**先頭の active member が対象に入っている `derived`** の
   `captured_at` / `captured_at_tz` を member から継ぎ直し、動いたら同じく差し戻す

差し戻しとスタック見送りの戻しは、`Recomputer._requeue` / `_reopen_stack` から
関数として取り出して共有する（写しを作らない）。

## 4. 画面

- **写真の選択バー**に「撮影地のタイムゾーン」ボタンを足す。押すとダイアログが開き、
  ゾーンを選ぶ（入力で絞り込める一覧。候補は `GET /timezones` が
  `available_timezones()` を並べて返す）。「上書きを外す」も選べる
- 適用後は一覧を読み直し、選択を解く
- **印は既存の仕組みで出る。** `formatCapturedDateTime` は `captured_at_tz` の名前で
  印を作るので、付け替えた行は「（ベトナム時間）」相当になる
- **くわしく（`/photos/:id`）** に、上書き中であることと元のゾーンが分かる一文を出す
  （`GET /media/{id}` が `captured_at_zone_override` を返す）

## 5. 範囲の外

- **自分が作ったと証明できない Immich 資産には、ゾーンの付け替えが届かない。**
  承認の要否は `same_instant` で決まり（Phase 16）、瞬間が同じなら「既に合っている」
  として承認を待たずに `complete` にする。付け替えは瞬間を変えないので、ここで止まる。
  自分で上げた資産（`created_by_us`）は `set_date_time_original` を無条件で打つので
  届く。**今回の題材は 1 件も送っていないので当たらない。** `known-issues.md` に書く
- 取り込み時にゾーンを選ぶこと、プロファイル単位の撮影地設定は作らない

## 6. 試験の方針

先に失敗するテストを書き、変異試験で判断を 1 つずつ壊して確かめる。

| 層 | 確かめること |
| --- | --- |
| `with_zone_override` | 瞬間が変わらない／`tz` が変わる／`source`・`note` を保つ／None で素通し |
| `Recomputer` | 上書きのある行を再計算しても付け替えが残る／上書きの無い行は今まで通り |
| `POST /media/timezone` | 変換して書く／`derived` の id が member へ広がる／`derived` が継ぎ直される／`complete` が `needs_recheck` へ戻る／動かなかった行は戻さない／`null` でカメラの時計のゾーンへ戻る／`timezone_policy: none` と不明なゾーンと不明な id で 400、DB は無傷 |
| 移行 | `0002` が当たり、既存行の列が NULL |
| 画面 | 選択バーのボタン → ダイアログ → 要求の本文／適用後に読み直す／くわしくの一文 |

### 実機で確かめること

- 9/19〜9/22 の DJI の動画を選んで `Asia/Ho_Chi_Minh` にすると、`captured_at` が
  `+07:00` の壁時計になる
- それを Immich へ送ると、Immich の表示が現地の時刻になる

## 触らないもの

- `resolve_captured_at` の本体（カメラの時計の解釈は変わらない）
- 結合のグループ検出（瞬間しか見ないので結論が変わらない）
- ライブラリのパス（`captured_at` を含まない）
