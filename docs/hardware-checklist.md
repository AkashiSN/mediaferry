# 実 USB での確認手順（Phase 1）

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

### 14. EOS 70D の 4GB 分割が連番から判別できるか

**`merge.enabled` を有効化してよいかの判断**。誤結合は公開済みの `media_file` を
取り残すので高くつく（だから既定は無効にしてある）。

- 4GB で分割された 1 本の録画が、どういう名前で並ぶか（`MVI_0001.MOV` の次が
  `MVI_0002.MOV` なのか、別の規則があるのか）
- **連続した別録画と区別が付くか。** 付かないなら `merge.enabled` は無効のまま、
  手動結合（Phase 4）に委ねる

### 15. Canon の MOV の `creation_time` が壁時計か UTC か

**第 4 の timestamp source を足すかの判断**。`canon-eos` の MOV は EXIF を持たない
ので、現状は `fallback: mtime` へ落ちる。

```bash
ffprobe -v error -show_entries format_tags=creation_time /path/to/MVI_0001.MOV
TZ=UTC stat -c '%y %n' /path/to/MVI_0001.MOV
```

- `creation_time` が撮影時の壁時計と一致する → `source: container` を足す余地がある
- UTC で書かれている → mtime へ落とす現状のままでよい（DJI と同じ罠）

### 16. CR2 を Immich が受け取るか

`scan.extensions` に `CR2` を入れてあるので、取り込みまでは通る。**送信で弾かれると
`upload_record` が失敗のまま溜まる。**

- 1 枚だけ手で送って、Immich 側で資産として見えるか
- 見えないなら、`generic-dcim` と同じく `canon-eos` からも CR2 を外すか、
  RAW を送らない選択肢を設定に足す（Phase 6 のスタッキングと合わせて考える）

## 関連

- [`backup.md`](backup.md)（バックアップとリストア）
- [`decisions.md`](decisions.md)（実測で覆った判断）と [`history/phase0-findings.md`](history/phase0-findings.md)（測った値そのもの）
- [`development.md`](development.md)（TrueNAS ホストと開発コンテナの癖）
