# Phase 13 の設計 —— 時刻の出所を器から取り、Canon の分割と Immich の日時を直す

**2026-08-26。** Canon EOS 70D の実カードでチェックリスト 13〜17 番を踏み切った結果
（[`hardware-verification.md`](hardware-verification.md) の「Canon の 14〜17 番が
決着した」）から、直すべきものが 4 つ出た。**4 つは 1 つの原因を共有している** ——
**動画の撮影時刻を、いま器（QuickTime の `creation_time`）から読んでいない**こと。

| # | 症状 | 実測 |
| --- | --- | --- |
| 1 | Immich で**動画だけが 9 時間ずれる** | Canon は `creation_time` に現地の壁時計を書き、`Z` を付ける。Immich が素直に UTC と読む |
| 2 | 動画の `captured_at` が**録画の終了時刻**になる | `creation_time` は開始、mtime は終了。実測で 70 秒差 |
| 3 | **4GB 分割が検出できない** | サイズの下限が 33.2 MiB 足りず、mtime は分割の両片で同一 |
| 4 | 一覧で**同時刻の行の並びが乱数** | tie-break が 32 桁の乱数 hex（`id DESC`） |

1〜3 は `creation_time` を読めば同時に解ける。4 は独立だが、**3 と同じ画面で目に見えた**
（`MVI_0007` が `MVI_0008` より左上に来た）ので同じ Phase で閉じる。

## 決めたこと

| 論点 | 決定 | 理由 |
| --- | --- | --- |
| 範囲 | **4 つ全部**を Phase 13 に入れる | 1〜3 は同じ値に依存する。4 は独立だが触るファイルが重ならない |
| 連鎖の書き方 | `timestamp.source` を**配列**にし、`fallback` を廃止 | いちばん正直。**リリース前なので、触っていないプロファイルのリビジョンが進む代償を受け入れられる** |
| 旧リビジョンの互換 | **読まない** | 利用者の判断。過去の Immich のデータは気にせず、リリース版からデータを溜め直す |
| 移行 | **通常どおり足す**（スカッシュは別タスク） | スカッシュはそれ自体が `migration_checksums.txt` と 13 個のテストに触る作業 |
| テーブル再構築 | **移行 runner に「外部キーを外して走らせる」経路を足す** | runner の穴はスカッシュしても消えない。いま塞げば試験が付く |
| `merge` | **有効にする。出力は `.MOV`。PCM では TS 経路を選ばない** | PCM は MP4 に版依存で入らず、TS では黙って消える |
| Immich | `timezone_policy: force_offset` + `fix_datetime_after_upload: true` | `dji-osmo` が同じ形で既に解いている |
| 並び | `captured_at DESC, rel_path DESC`。索引 3 本を張り替える | `rel_path` は `UNIQUE` なので単独で tie-break になる |

## 1. 時刻の出所 `container`

### データの流れ

```
staging のファイル
   │
   ├─ MediaProbe.describe()          ← -show_format を既に叩いている
   │      └→ ProbeResult.container_wall = format.tags.creation_time（生の文字列）
   │
   └─ resolve_captured(staging_abs, probe)
          └→ core/timestamps.py が source の連鎖を順に試す
```

**`ffprobe` の呼び出しは増えない。** `adapters/ffprobe.py` の `describe()` は
`-show_format -show_streams` を既に叩いており、`format.tags.creation_time` は
**いまのペイロードに入っている**。`ProbeResult` に欄を足して表に出すだけでよい。

**`publisher.py` の `_read_metadata` を「probe → captured」の順に入れ替える。**
現在は captured を先に決めてから probe しており（`publisher.py:244-249`）、
`container` の値が間に合わない。どちらも `_with_lease_pulse` の中なので、
リースの扱いは変わらない。

### プロファイルの形

```yaml
timestamp:
  source: [exif, container, mtime]   # 配列。fallback は書かない
  container_semantics: wall_clock    # 既定。Canon は Z が嘘なので壁時計
```

- **連鎖の最後は `mtime` でなければならない**とパーサで弾く。`mtime` は必ず値を
  返す終端なので、これで `resolve` が全域関数のままになる
- `container_semantics` は `mtime_semantics` と同じ扱い（既定 `wall_clock`、
  `instant` も選べる）。**媒体の性質は形から推定できない**（11 番の教訓）ので、
  宣言に委ねる
- **旧形（`source: str` + `fallback: str`）は読まない。** ビルトイン 3 つを
  すべて新しい形に書き換える

### 保存

`media_file` に **`container_wall`（TEXT, nullable）** を足し、`ffprobe` が返した
文字列を**そのまま**入れる（`2026-08-26T12:35:08.000000Z`）。解釈しない。

**理由は再計算。** `container_semantics` を後から変えたときに、**再 probe せずに
読み直せる**。`mtime_ns` を生の整数で持っているのと同じ考え方で、「意味の解釈は
`timestamps.py` の 1 か所に置く」という既存の規則にも合う。

`captured_at_source` の `CHECK` に `container` を足す（移行は §4）。

### 副作用として消えるもの

`importer._captured_for` の「EXIF を読むときだけ遅延解決、それ以外は即値」という
**二股が要らなくなる**。probe は公開時に必ず走るので、**取り込み経路は常に遅延解決**
に一本化できる。`ArtifactRequest` の「`captured` と `resolve_captured` はどちらか
一方」という不変条件はそのままで、結合の出力（`merger.py`）は即値のまま。

## 2. 分割検出と結合

### `canon-eos` の `merge`

```yaml
merge:
  enabled: true
  tolerance_seconds: 5                                   # 据え置き
  min_part_size_gib: 3                                   # 実測 3.9675 GiB を通す
  sequence_pattern: '^MVI_(?P<seq>\d{4})$'               # stem に当てる（output.py:47）
  output_name: "MVI_{ts}_{first_seq}-{last_seq}_MERGED.MOV"
  keep_streams: { video: primary, audio: all, timecode: false, data: false }
```

`grouping.py` の `_RESOLUTION_SECONDS` に **`container: 1.0`** を足す
（`creation_time` は秒までしか持たない）。無いと、丸めで生じる符号のぶれが
「重なり」と誤読される —— DJI の 5 パートで踏んだのと同じ形。

**実測どおりに分かれることの根拠**（`creation_time` を出所にした場合）:

| 継ぎ目 | 前の終端 | 次の開始 | 差 | 判定 |
| --- | --- | --- | --- | --- |
| 0006 → 0007 | 12:36:17.937 | 12:37:13 | +55.063 s | 外 → **別録画** |
| 0007 → 0008 | 12:44:21.428 | 12:44:22 | +0.572 s | 内 → **同一録画** |

`{ts}` は先頭パートの `captured_at` の壁時計なので、`container` を入れた後は
**録画の開始時刻**が名前に載る（`MVI_20260826123713_0007-0008_MERGED.MOV`）。

### 出力が `.MOV` である理由

Canon の音声は **`pcm_s16le`**（実測、1536 kbps）。手元の ffmpeg（git master）で
測った結果:

| 器 | `-c copy` | 読み直すと |
| --- | --- | --- |
| `.MOV` | OK | `pcm_s16le / audio` **そのまま** |
| `.MP4` | OK（**この版では**） | `pcm_s16le / audio` |
| `.ts` | **OK（終了コード 0）** | **`bin_data / data`** —— 音声が消える |

`.MP4` が通るのは `ipcm` を持つ新しい版だから。**配るイメージは bookworm の
ffmpeg 5.1 系**なので、版に依存しない `.MOV` を選ぶ。

**`merger.py:141` の拡張子の集合に `.mov` を足す。** ここを直さないと、`work/` を
舐める進捗が 4 GB の結合中ずっと 0 のままになる（Phase 8 で「送信の進捗が出ない」を
直したのと同じ穴を、こちらから作り込むことになる）。

### TS 経路を PCM で塞ぐ

`core/merge/streams.py` に純粋関数を足す。

```python
def ts_route_blockers(streams, keep) -> tuple[str, ...]:
    """TS 経路が**無損失で運べない**、保持対象のストリームを返す.

    mpegts は PCM を private data として詰め、**警告だけ出して成功する**
    （実測: 読み直すと bin_data の data ストリームになり、音声が消える）。
    ffmpeg が失敗しない以上、こちらで運べないと判断するしかない。
    """
```

判定は codec 名が `pcm_` で始まるかどうか。`UNSUPPORTED_BY_TS`（種別で `data` を
落とす）とは**別の軸**なので、既存の集合には混ぜない。

merger は concat を試し、失敗したときに `ts_route_blockers` が空でなければ
**TS を試さずに不合格**にし、理由を残す。**4 GB を再 mux してから駄目だと分かる
経路を残さない。**

## 3. Immich への書き戻し

```yaml
timestamp:
  timezone_policy: force_offset
  timezone: null                      # MEDIAFERRY_DEFAULT_TIMEZONE を使う
immich:
  fix_datetime_after_upload: true
```

**コードの変更は無い。** `core/uploads/decisions.py` の `datetime_plan` は既に
`force_offset` × `created_by_us` で書き戻す形になっている。`fix_datetime_after_upload`
を単独で立てても効かないのは、`policy == "none"` を先に見て降りるため。

**代償を 2 つ記録しておく。**

1. **`force_offset` は「撮影地のゾーンを設定値で決め打つ」意味**（§6）。旅先で撮ると
   自宅のオフセットが付く。`dji-osmo` が既に受け入れている取り引きと同じもの
2. **`captured_at_tz` が `null` から値ありに変わる**ので、`publisher._collision_stamp`
   が作る衝突接尾辞の桁も変わる（`publisher.py:271`）。同名衝突のときだけ現れ、
   既存の公開名は確定済みなので遡っては変わらない

## 4. 移行 —— runner に穴があった

**`media_file` の作り直しは、いまの runner ではできない。**

`migrate.py:91` は `BEGIN IMMEDIATE ... COMMIT` の 1 本の `executescript` で走らせ、
ファイル側に `BEGIN` / `COMMIT` を書くことを禁じている。**`PRAGMA foreign_keys` は
トランザクション内では黙って無視される**ので、移行ファイルの中からは外せない。
`media_file` を参照する外部キーは 4 本ある（`merge_group.output_media_file_id` /
`merge_member.media_file_id` / `source_entry.media_file_id` /
`upload_record.media_file_id`）。

**理屈で決めずに測った**（SQLite 3.46.1）:

| やり方 | 結果 |
| --- | --- |
| 素直に `DROP TABLE` して入れ替える | **失敗**（`FOREIGN KEY constraint failed`） |
| `PRAGMA legacy_alter_table` を張る | **失敗** —— トランザクション内では効かず、`RENAME` が子テーブルの参照先を書き換える |
| `PRAGMA defer_foreign_keys = ON` | **失敗** —— DROP の暗黙 DELETE で立った違反が COMMIT まで残る |
| **`PRAGMA foreign_keys = OFF` を「トランザクションの外」で立てる** | **成功**（`foreign_key_check` も空） |

### runner に足すもの

移行ファイルの**先頭行**を `-- mediaferry:foreign-keys-off` にして宣言する
（runner はこの 1 行だけを見る。本文の途中に書いても効かない）。runner は

1. `PRAGMA foreign_keys = OFF`（**トランザクションの外**）
2. いままでどおり `BEGIN IMMEDIATE ... COMMIT` の `executescript`
3. `PRAGMA foreign_keys = ON`
4. **`PRAGMA foreign_key_check` を走らせ、空でなければ `MigrationError`**

4 番が、外部キーを外すことを許容できるようにする手当てで、**SQLite 公式の
12 手順が求めているもの**そのもの。**穴はスカッシュしても消えない** —— リリース後に
一度でも `CHECK` を変えたくなれば同じ壁に当たるので、いま塞ぐ。

### 移行の構成

- **`0026`** —— `media_file` の作り直し。`container_wall` を足し、`captured_at_source`
  の `CHECK` を `('filename','exif','mtime','container')` へ広げる。索引 2 本
  （`media_file_sha1` / `media_file_captured_at`）と trigger 2 本（`0011` の
  `media_file_captured_revision_*`）を作り直す。**FK オフの宣言付き**
- **`0027`** —— 索引 3 本の張り替え

## 5. 一覧の並び

**張り替えるのは 3 本。** `0013` は `(profile_id, role, rel_path)` なので対象外。

| 移行 | いま | 変更後 |
| --- | --- | --- |
| `0014` | `(profile_id, captured_at DESC, id DESC)` | `(profile_id, captured_at DESC, rel_path DESC)` |
| `0022` | `(role, captured_at DESC, id DESC)` | `(role, captured_at DESC, rel_path DESC)` |
| `0023` | `(captured_at DESC, id DESC) WHERE role='derived'` | `(captured_at DESC, rel_path DESC) WHERE role='derived'` |

並びを直すのは 4 か所。

- `routes_media.py:202` の `ORDER BY m.captured_at DESC, m.id DESC`
- `db/selection.py` の `_ORIGINALS` / `_DERIVED` / `_MEMBERS_OF_UNMERGED`
  （**`limit` で切るので、順序が決まらないと境界が揺れる**。`GET /uploads/selectable`
  は既定 500 件で切る）
- `core/listing.py` の契約コメント

**`media_file.rel_path` は `TEXT NOT NULL UNIQUE`**（`0003:9`）なので、単独で
tie-break として成立する。`id` を足す必要は無い。副産物として RAW+JPEG の並びも
決定的になる（`"JPG" > "CR2"` なので主が必ず先。**いまは id の偶然でそう見えている**）。

## 6. 試験の方針

- **実装より先に失敗するテストを書く。** 特に `container` の連鎖は、`wall_clock` と
  `instant` の両方を通す筋書きを先に作る（**既定値と一致するだけで通るテスト**を
  作らない。Phase 5 で 1 度踏んだ）
- **変異試験は `PYTHONDONTWRITEBYTECODE=1`。** 今回は境界が多い —— 連鎖の順、
  連鎖の終端が `mtime` であることの検査、`min_part_size_gib` の 3、
  `tolerance_seconds` の内外、`pcm_` の判定、並びの向き、FK オフの宣言の有無
- **E2E を受け入れコマンドに入れる**（Phase 8 で 8 タスクぶん赤のまま気づかなかった）
- **索引の張り替えは前後で同じ計測を取る。** 対象は `GET /media`（`collapse=stack`
  と `stack=members` を含む）、`role=derived`、`GET /uploads/selectable` の 3 経路。
  **Phase 9 は「測る対象が足りていなかった」ために退行を見落とした**
- **全移行を流した後に `PRAGMA foreign_key_check` が空であることを確かめるテスト**を
  1 本置く（`0026` の安全網）

### 実機で確かめること

1. `MVI_0007` + `MVI_0008` が 1 組として検出され、結合が通るか
2. 結合出力の音声が **`pcm_s16le` のまま**残るか（TS へ落ちていないか）
3. Immich の動画が **12 時台**に並ぶか
4. 一覧で `MVI_0008` が `MVI_0007` より左上に来るか
5. 写真 5 組の壁時計が変わらないこと（`+00:00` → `+09:00` で数字は不変）

## 触らないもの

- **DJI の `mtime_semantics: instant`。** 11 番で実測した判断で、今回の変更とは
  独立している
- **`stack` の規則。** 組の身元から時刻を外した Phase 10 の判断は、カメラの時計が
  止まっていた 68 件で**実データに報われた**（時刻が全部同じでも 34 組すべてが成立）
- **`generic-dcim` の `merge`。** 実データが無い

## 関連

- [`hardware-verification.md`](hardware-verification.md) —— 実測の記録（Canon の
  14〜17 番、`creation_time` の値、PCM の測定）
- [`../hardware-checklist.md`](../hardware-checklist.md) —— 13〜17 番の結果
- [`phase10-design.md`](phase10-design.md) —— 組の身元から時刻を外した理由
- [`../decisions.md`](../decisions.md) —— 実測で覆った判断
