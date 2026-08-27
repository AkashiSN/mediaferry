# 実 USB での確認手順（Phase 1）

**2026-08-20〜21 に 1〜12 番を実機で通した。** 結果と、そこで見つかった不具合は
[`history/hardware-verification.md`](history/hardware-verification.md) にある。
**11 番は不合格**（実装の前提が崩れた）。5・8 番は修正後の踏み直しが残っている。

自動テストは実カードを扱えない。マウントは開発コンテナ（入れ子の非特権 LXC）
では AppArmor に阻まれるので、**TrueNAS ホストで実行する**。

既定シェルは zsh。以下の 2 点で bash と違う（`development.md` の「環境の癖と罠」）。

- 行内コメント（`cmd  # 説明`）が**無効**。`#` 以降が引数として渡る
- `tail -1` が `option used in invalid context` になる。`tail -n 1` を使う

API は loopback にしか出ていないので、ホストから `curl` で叩く。

```bash
BASE=http://127.0.0.1:8080/api
```

## チェック項目

### 1. カードが認識され、プロファイルが当たる

DJI の SD カードを挿す。

```bash
curl -s $BASE/devices
```

- 一覧に現れる
- `profile_slug` が `dji-osmo`
- `provisional` が `false`
- 初回は `identity_confidence` が `low`、`trusted` が `false`

### 2. 空の内蔵ストレージが暫定一致になる

Osmo の内蔵ストレージ（`DCIM` はあるが空）を挿す。Phase 0 の実測どおりの形。

- `profile_slug` が `dji-osmo`
- `provisional` が `true`
- `identity_confidence` が `low`

### 3. スキャン

```bash
VOL=<volume_instance_id>
curl -s -X POST $BASE/volumes/$VOL/scan
```

- `job_id` が返る
- `GET $BASE/jobs/<job_id>` が `succeeded` になる
- `GET $BASE/jobs/<job_id>/events?after_seq=0` にファイル名が出る
- この時点では `GET $BASE/media` は空

### 4. 取り込み

```bash
curl -s -X POST $BASE/volumes/$VOL/import
```

- `GET $BASE/media` にファイルが並ぶ
- `library/dji-osmo/` の下が**カードと同じ相対パス**になっている
  （`DCIM/DJI_001/DJI_....MP4` の階層がそのまま）
- `captured_at` がファイル名の壁時計にオフセットが付いた形
- `.LRF` が取り込まれていない

### 5. 取り込み中にカードを抜く

大きめのファイルの取り込み中に物理的に抜く。

- ジョブが `failed` で終わる
- `library/` に中途半端なファイルが残らない
- `staging/` にファイルが残らない（空のディレクトリは可）

### 6. 再挿入して再取り込み

- 取込済のファイルはスキップされる（`skipped` に計上）
- `library/` に重複が作られない

### 7. 取り込み中に再起動

大きめのファイルの取り込み中に。

```bash
docker restart <app コンテナ>
```

- 起動時に回収され、ログに reconciliation の結果が出る
- `GET $BASE/orphans` の `orphans` が空
- 途中まで書いたファイルが `library/` に現れない

### 8. キャンセル

```bash
curl -s -X POST $BASE/jobs/<job_id>/cancel
```

- ジョブが `cancelled` で終わる
- `staging/` にファイルが残らない
- `library/` に中途半端なファイルが残らない

### 9. 同名衝突

`library/dji-osmo/DCIM/DJI_001/` に、カード上と同名で**内容の違う**ファイルを
置いてから取り込む。

- 既存が**上書きされない**（中身が変わらない）
- 新しい方に別名が付く（`_<壁時計>` などの接尾辞）
- `GET $BASE/media` に両方が並ぶ

### 10. 取り外し

```bash
curl -s -X POST $BASE/volumes/$VOL/close
```

- 200 が返る
- カードを安全に抜ける（`dmesg` に I/O エラーが出ない）

### 11. mtime の解釈を実測する

**これは実装の前提の確認で、他の項目と性質が違う。**

`timestamps.py` と `publisher._collision_stamp` は「カードの時刻欄に UTC
オフセットが書かれていない（または 0）」ことを前提に、mtime の**UTC 表現**を
壁時計として使っている。DJI はファイル名に壁時計をそのまま埋めるので、両者が
一致するかで前提を確かめられる。

```bash
ls /path/to/DCIM/DJI_001
TZ=UTC stat -c '%y %n' /path/to/DCIM/DJI_001/DJI_20260817143000_0001_D.MP4
```

- ファイル名の `20260817143000` と、`TZ=UTC stat` が出す `2026-08-17 14:30:00`
  が**一致する** → 前提が成り立っている
- **一致しない** → その機種は exFAT の `OffsetFromUtc` を書いている。
  `timestamps.py` の `_wall_clock` を、mtime をプロファイルの `timezone` で
  描画する形へ変える必要がある

**mtime は「録画終端」であって開始ではない。** ファイル名の時刻と直接比べるのでは
なく、**開始 + `duration` と比べる**（DJI では終端の 2 秒ほど後になる。ファイルを
閉じるまでの時間）。

**カードをマウントし直さなくても測れる。** 公開時にソースの mtime をそのまま保つ
ので、取り込んだライブラリ側の実体で同じことが分かる。

> **2026-08-21 の結果: 不合格。** DJI Osmo Pocket 4 は `OffsetFromUtc` を書いていた
> （UTC 描画で 9 時間ずれ、`+09:00` 描画で終端の +2.00 秒）。直すべき 3 か所は
> [`history/hardware-verification.md`](history/hardware-verification.md) にある。

Linux の exfat ドライバは `OffsetFromUtc` の valid bit が立っていればその
オフセットで UTC へ変換し、立っていないときだけマウントの `time_offset`
（既定 0）を使う（`fs/exfat/misc.c` の `exfat_get_entry_time`）。

**結果はどちらであっても [`history/phase0-findings.md`](history/phase0-findings.md) に 1 件として残す。**

### 12. 埋め込みサムネイルの disposition を確かめる

結合が「最初の映像ストリームのみ」を保持する判定は、`disposition.attached_pic`
でサムネイルを見分けている（`core/merge/streams.py`）。

```bash
ffprobe -v error -print_format json -show_streams /path/to/DJI_....MP4
```

- `mjpeg` のストリームに `"attached_pic": 1` が立っている → 判定は正しい

**`ffprobe` はホストに無い。** アプリのコンテナ越しに叩く
（`docker exec <app のコンテナ> ffprobe …`）。

> **2026-08-21 の結果: 合格。** `mjpeg`（index 5）に `attached_pic=1` が立っていた。
- 立っていない → `keep_streams.video` が `primary` の間は影響しないが、`all` を
  使うプロファイルを足すときに `_is_thumbnail` の判定を増やす必要がある

### 13. Canon EOS 70D の実カードで `canon-eos` に確定するか

**`canon-eos` は仕様と知識から書いており、実データを一度も見ていない**（Phase 5
Task 4）。E2E で通しているのは、その `require` から組み立てた合成カードまで。

```bash
lsblk -o NAME,LABEL,FSTYPE,SIZE
ls /path/to/mount/DCIM
ls /path/to/mount/DCIM/100CANON | head
```

- ボリュームラベルが `EOS_DIGITAL` か（違えば `hints.volume_labels` を直す）
- `DCIM/100CANON/` の下に `IMG_0001.JPG` / `IMG_0001.CR2` / `MVI_0001.MOV` の形で
  並んでいるか（違えば `require.filename_pattern` と `scan.extensions` を直す）
- **カードリーダー経由で見える USB ID がリーダーのものであることを確かめる**
  （`hints.usb_ids` を空にした根拠。機種の ID が見えるなら足してよい）

> **2026-08-25 の結果: 合格、ただし根拠が違う。** `canon-eos` に `identity_confidence:
> high`・`provisional: false` で確定した。**ただし `fs_label` は空で `EOS_DIGITAL`
> ではなく、`hints.volume_labels` は一致に寄与していない**（確定は `require` だけで
> 起きている）。`DCIM/100CANON/` の 1 ディレクトリに `IMG_####.JPG` / `IMG_####.CR2`
> / `MVI_####.MOV` が 1488 件並び、`filename_pattern` の外れは 0 件だった。
>
> ホストで裏を取った。**ラベルが無いのは実カードの事実**（`blkid` は `TYPE="exfat"`
> だけで `LABEL=` を返さない）。**exFAT で焼いた SDXC にはラベルが付かない。**
> それでも `volume_labels` は残す —— `hint_score` は順位付けにしか効かないので、
> FAT32 のカードで当たれば得があり、当たらなくても損が無い。
> **2026-08-26 に追記: ラベルは付く。** 利用者がカメラで初期化したところ、
> `fs_label` が **`EOS_DIGITAL`** になった。**「exFAT にはラベルが付かない」のは
> PC で焼いたカードの話**で、`hints.volume_labels` の値は正しい。

> **`hints.usb_ids: []` も裏付けられた** —— `lsusb` に出るのは汎用の USB ストレージ
> （リーダー）で、Canon のベンダ ID は見えない。**リーダーの ID を足すと、その
> リーダーに挿したどのカードにも当たる**ので空のままにする。くわしくは
> [`history/hardware-verification.md`](history/hardware-verification.md)。

### 14. EOS 70D の 4GB 分割が連番から判別できるか

**`merge.enabled` を有効化してよいかの判断**。誤結合は公開済みの `media_file` を
取り残すので高くつく（だから既定は無効にしてある）。

- 4GB で分割された 1 本の録画が、どういう名前で並ぶか（`MVI_0001.MOV` の次が
  `MVI_0002.MOV` なのか、別の規則があるのか）
- **連続した別録画と区別が付くか。** 付かないなら `merge.enabled` は無効のまま、
  手動結合（Phase 4）に委ねる

> **2026-08-25 の結果: 題材が無く、決着していない。** カードの MOV は 2 本だけで、
> `MVI_8234` と `MVI_8240` の間に静止画が 5 枚挟まる＝別々の録画だった。**Canon は
> 静止画と動画で採番カウンタを共有する**（`IMG` の欠番 `8234` / `8240` がそのまま
> MOV の番号）ことは分かったが、それは「分割も別録画も隣り合う」を意味するので、
> **連番だけでは区別が付かないという懸念は解消していない**。`merge.enabled` は
> 無効のまま。4GB を超える録画の入ったカードで踏み直す。

> **2026-08-26 の結果: 決着。現行の規則では検出できない。** 4GB 超の録画を入れた
> カードで踏み直した。`MVI_0007`（4,260,142,424 B = 3.9675 GiB）と `MVI_0008`
> （218,782,864 B）が 1 本の分割で、`MVI_0006`（618 MB）が別録画。**現行の 2 条件が
> どちらも成立しない** —— (1) `min_part_size_gib: 4` ＝ 4,294,967,296 B に対して
> 実測が **33.2 MiB 足りない**、(2) MOV は EXIF が無く mtime へ落ちるが、
> **分割の両片は mtime が同一**（カメラが録画停止時刻を全片に書き直す）なので
> 差が −428 秒になる。
>
> **`creation_time`（15 番）を出所にすれば、閾値を触らずに正しく分かれる** ——
> 0006 → 0007 が **+55.063 秒**（外＝別録画）、0007 → 0008 が **+0.572 秒**
> （内＝同一録画）。`min_part_size_gib` は **3** へ下げる。くわしくは
> [`history/hardware-verification.md`](history/hardware-verification.md)。

### 15. Canon の MOV の `creation_time` が壁時計か UTC か

**第 4 の timestamp source を足すかの判断**。`canon-eos` の MOV は EXIF を持たない
ので、現状は `fallback: mtime` へ落ちる。

```bash
ffprobe -v error -show_entries format_tags=creation_time /path/to/MVI_0001.MOV
TZ=UTC stat -c '%y %n' /path/to/MVI_0001.MOV
```

- `creation_time` が撮影時の壁時計と一致する → `source: container` を足す余地がある
- UTC で書かれている → mtime へ落とす現状のままでよい（DJI と同じ罠）

> **2026-08-26 の結果: 壁時計に `Z` が付いていた。しかも録画の開始を指す。**
>
> ```
> MVI_0006  creation_time = 2026-08-26T12:35:08.000000Z   duration  69.937
> MVI_0007  creation_time = 2026-08-26T12:37:13.000000Z   duration 428.428
> MVI_0008  creation_time = 2026-08-26T12:44:22.000000Z   duration  23.023
> ```
>
> 撮影は 12 時台の JST なので、**数字は現地の壁時計で `Z` が嘘**。`12:35:08 +
> 69.937 = 12:36:17.9` が `MVI_0006` の mtime と一致するので、**`creation_time` は
> 録画の開始、mtime は終了**。**`source: container` を足す価値がある** —— 14 番の
> 判別にも要り、いまの mtime 由来では動画の時刻が 70 秒遅れる。
>
> **Immich では動画だけが 9 時間ずれる**（実物で確認）。Immich が `Z` を素直に
> UTC と読むため。写真は EXIF にオフセットが無く現地時刻として扱われるので正しい。
> 直すには `timezone_policy: force_offset` と `fix_datetime_after_upload: true` を
> **セットで**入れる（`datetime_plan` が `policy == "none"` を先に見て降りる）。

### 16. CR2 を Immich が受け取るか

`scan.extensions` に `CR2` を入れてあるので、取り込みまでは通る。**送信で弾かれると
`upload_record` が失敗のまま溜まる。**

- 1 枚だけ手で送って、Immich 側で資産として見えるか
- 見えないなら、`generic-dcim` と同じく `canon-eos` からも CR2 を外すか、
  RAW を送らない選択肢を設定に足す（Phase 6 のスタッキングと合わせて考える）

> **2026-08-26 の結果: 合格。** 実カードの CR2 は Immich に受け取られている。
> `GET /dashboard` の宛先要約が `complete: 104` / `failed: 0` / `stacked: 34` で、
> **弾かれた `upload_record` は 1 件も無い**。CR2 を外す必要は無い。

### 17. RAW+JPEG の組が実カードで成立するか

**Phase 6 のスタッキングの前提**（`docs/design.md` §9.11）。組と認めるのは 4 条件で、
うち 3 つが実データ依存。

- 同じ stem（`IMG_0001.JPG` と `IMG_0001.CR2`）
- `captured_at` が**秒まで一致**
- `captured_at_source` が同じ

**要は `exifread` が実機の CR2 から `DateTimeOriginal` を読めるか。** 読めなければ
JPG は `exif`・CR2 は `mtime` fallback になって出所が食い違い、**理由つきの見送りと
して画面に出る**（黙って誤動作はしない）。

```bash
uv run python -c "import exifread,sys; f=open(sys.argv[1],'rb'); print(exifread.process_file(f).get('EXIF DateTimeOriginal'))" /path/to/IMG_0001.CR2
```

- 読めて、同じ stem の JPG と秒まで一致する → 組が成立する
- 読めない → `stack` は見送りになる。CR2 を送らない選択肢を設定に足すかを、
  16 番（Immich が CR2 を受け取るか）と合わせて判断する

> **2026-08-26 の結果: 合格。** 実カードの CR2 から `DateTimeOriginal` が読めた。
> 取り込んだ 5 組は CR2 も JPG も `captured_at_source = exif` で、値は
> 12:33:05 / 12:33:46 / 12:33:59 / 12:34:09 / 12:34:23 と実際の撮影時刻に一致する
> （mtime より 1 秒早いのは撮影から書き込みまでの差）。**5 組すべてが組として成立。**
>
> **前回取り込んだ 68 件は `captured_at` が全部 `2026-08-08T13:00:00` で同一だったが、
> これはカメラの日時が未設定だったためで実装の問題ではない**（今回の 5 組が正しく
> 散ったことで切り分いた）。**それでも 34 組すべてが組として成立していた** ——
> Phase 10 で**組の身元から時刻を外した**判断が、実データで報われた形になる。

## Phase 13 の実機確認（18〜24 番）

**17 番までは全部閉じた**（2026-08-26）。ここからは Phase 13
（[`history/phase13-design.md`](history/phase13-design.md)）が入れた直しを実機で確かめる。

**先に決めること**: **DB を作り直す**（利用者の判断、2026-08-26）。
`library/` を残したまま DB だけ消すと、起動時の reconciliation が既存の
206 GB を孤立ファイルとして `/orphans` に並べる（アプリは動く）。
両方消すかは好みで決めてよい。

**取り込みの前に必ず**: `MEDIAFERRY_DEFAULT_TIMEZONE`（またはプロファイルの
`timezone`）を入れる。`canon-eos` が `force_offset` になったので、
**未設定だと Canon の取り込みが 1 件も通らない**（`importer.run` の前検査が
1 バイトも copy する前に落とす。§12.2）。

| # | 見ること | 落ちたときに疑うもの |
| --- | --- | --- |
| 18 | **`MVI_0007` と `MVI_0008` が 1 組として検出される**。つなぐ画面で候補に出るか | `min_part_size_gib: 3` / `_RESOLUTION_SECONDS` の `container: 1.0` |
| 19 | **結合が通り、出力の音声が `pcm_s16le` のまま残る**。`route` が `concat` か（`ts` に落ちていないか）。進捗が 0 のまま止まらないか | TS 経路の門（`pcm_*` で塞ぐ）／`MERGE_ARTIFACT_SUFFIXES` に `.mov` が入っているか |
| 20 | **Immich で動画が 12 時台（JST）に並ぶ**。9 時間ずれていないか | `timezone_policy: force_offset` と `fix_datetime_after_upload: true` の組。**片方だけでは効かない** |
| 21 | **写真 5 組の壁時計が変わらない**。`datetime_plan` は kind を見ないので、EXIF 由来の写真にも `+09:00` 付きで `dateTimeOriginal` が送られる | 写真がずれたら `force_offset` の適用範囲を kind で切る判断が要る |
| 22 | **写真タブで `MVI_0008` が `MVI_0007` より左上に来る**。RAW+JPEG の組は主（JPG）が先か。**画面から見ること**（API を直に叩くと題材が消える） | `ORDER BY` と索引（`0026`）の噛み合わせ |
| 23 | **`0026` の適用が通る**。適用後に `PRAGMA foreign_key_check` が空か | DB を作り直すなら一瞬で済む。時間と WAL の膨らみは、既存 DB を残した場合だけ見る |
| 24 | **`_requeue`（Immich への `needs_recheck` 差し戻し）と stack の再オープンが起動する**か。`recompute` を押して確かめる | 差分だけでは確認できなかった箇所 |

> **2026-08-27 の結果: 18〜23 は合格。24 は「起きない側」だけ確認できた。**
>
> 取り込み 13 件（動画 3・写真 5 組）。**動画 3 本の `captured_at_source` が `container` になり**、
> 値は実測の `creation_time` と一致した（`MVI_0006` = 12:35:08、`0007` = 12:37:13、`0008` = 12:44:22。
> いずれも `+09:00` 付き）。写真 10 枚は `exif` のまま。
>
> **18 番**: 検出ジョブが**自動で** `MVI_0007`+`MVI_0008` を 1 組にした（`detected_by: auto`）。
> 別録画の `MVI_0006`（618 MB / 69.9 秒）は入らない。`min_part_size_gib` を 4 → 3 に下げた判断が実カードで効いた。
>
> **19 番**: **`route = concat`**。検証 4 つとも合格 —— duration 451.451 秒（差 0.0）、
> **streams に `pcm_s16le` / `sowt` が残る**、frames 13,530（欠落 0）、size のずれ 0.0198%（許容 2%）。
> 落としたのは `tmcd`（959 bps）1 本だけで、`keep_streams.timecode: false` の宣言どおり。
> 継ぎ目は 428.428 秒で 1 本目の duration と完全一致。出力は
> `MVI_20260826123713_0007-0008_MERGED.MOV`、所要 30 秒。ffmpeg は 5.1.9-0+deb12u1。
>
> **20・21 番**: 送信 12 件がすべて `complete` / `origin = created_by_us`。
> **`awaiting_datetime_approval` は 0 件**なので、日時の書き戻しは承認待ちに落ちずに自動で適用された。
> Immich 側の表示も**動画・写真とも 12 時台の JST**（利用者が確認）。**9 時間ずれは解消。写真もずれていない。**
> codex のレビューが言った「写真にも `dateTimeOriginal` が送られるが、新規作成資産だけ自動補正するので害は無い」が実データで裏付いた。
>
> **22 番**: RAW+JPEG 5 組すべてで**主が JPG**、`stack.members` も `JPG → CR2` の順
> （`"JPG" > "CR2"` なので `rel_path DESC` が主を先に出す）。**CR2 と JPG は秒まで同じ値**なので、
> これが同じ撮影日時での tie-break の実証になる。動画 3 本は撮影日時が違うので降順は当たり前で、実証にはならない。
>
> **23 番**: `schema_version: 26`。移行が最後まで通らなければ lifespan で落ちて起動しないので、
> 起動できた時点で合格。`library/` ごと消したので `/orphans` も空だった。
>
> **24 番**: 再計算は**変更 0 件 / 据え置き 14 件 / 飛ばし 0 件 / 再確認へ戻し 0 件 / スタック再評価 0 件**。
> 値は 14 件すべて前後で不変で、送信レコードも 12 件が `complete` のまま。
> **「飛ばし 0 件」が Task 7 の設計の確認になる** —— 動画 3 本は `container` 由来だが、
> 再計算は `container_wall` を DB から読むので **ffprobe を呼び直さずに算出できている**（カードが無くてもよい）。
> **差し戻しが「起きる」側は未検証のまま。** 値を変えるには `container_semantics` 等をいじる必要があるが
> ビルトインは編集できず、複製したユーザ定義に既存のメディアは紐づかない。実データで自然に踏むまで持ち越す。
>
> **新しく見つかった件（実機でのみ分かる形）**: **つなぐボタンを押しても画面が変わらず、
> 利用者が「失敗した」と読んだ。** 詳細は [`development.md`](development.md) の持ち越し。

**既存の動画は `recompute` では直らない。** `container_wall` は NULL のままで、
再計算は ffprobe を呼ばない（呼ぶと再計算がカードの有無に依存する）。
器の時刻を使いたければ**取り込み直し**が要る。

## 関連

- [`backup.md`](backup.md)（バックアップとリストア）
- [`decisions.md`](decisions.md)（実測で覆った判断）と [`history/phase0-findings.md`](history/phase0-findings.md)（測った値そのもの）
- [`development.md`](development.md)（TrueNAS ホストと開発コンテナの癖）
