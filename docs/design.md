# mediaferry 設計仕様書

**この文書は現在の仕様だけを書く。** そう決めた理由は [`decisions.md`](decisions.md)、
どう作ったかは [`history/`](history/README.md) にある。

## 1. 背景と目的

現在、DJI Osmo Pocket 4 で撮影した動画は `dot_local/bin/executable_dji_workflow.py`
（PEP 723 / `uv run --script`）で取り込んでいる。このスクリプトは以下を行う。

1. SD カードから外付け SSD へ rsync
2. ~16GiB で自動分割された MP4 のグループを検出し ffmpeg で結合
3. `upload/` にハードリンクを張って immich-go の入力を作る
4. immich-go のコマンドを画面に表示（実行はユーザに委ねる）
5. アップロード後、Immich API で撮影日時のタイムゾーンずれを補正

このワークフローには 3 つの問題がある。

- **macOS 前提**である。`diskutil` に依存し、外付け SSD をマウントした Mac でしか動かない。
- **CLI に習熟していないと実行できない**。オプションが 15 個あり、`--since` の指定を誤ると
  対象が空になる。immich-go のコマンドは手でコピペする必要がある。
- **DJI 固有の知識がロジックに埋め込まれている**。ファイル名規約・分割サイズ閾値・
  タイムゾーン補正がコードに直書きされており、他機種へ広げられない。

本仕様は、これを **TrueNAS のカスタムアプリとして動く Web アプリケーション**へ作り直す。
エンドユーザは NAS の USB ポートにカードリーダーを挿すだけでよく、以降の確認と操作は
すべて Web 画面で完結する。対象機種も DJI に限定せず、一眼レフ（Canon EOS 70D 等）で
撮影した写真も同じ仕組みで Immich へ同期できるようにする。

### 名称

`mediaferry`。カメラ → NAS → Immich という「メディアを運ぶ渡し船」を表す。
特定のベンダにも転送先にも寄らない名前とし、将来 Immich 以外の転送先を追加しても
名前が実態とずれないようにした。

## 2. スコープ

### 含むもの

- USB マスストレージ（SD カードリーダー、および UMS として振る舞うカメラ本体）の
  自動検出・read-only マウント・取り込み
- デバイスプロファイルによる機種差の吸収（ビルトイン + GUI での編集・新規作成）
- 分割動画のグループ検出・結合・結合結果の検証
- Immich REST API への直接アップロードと重複排除
- 撮影日時のタイムゾーン補正
- 取り込み状況・同期状況の可視化と、ジョブの実行・再開・再試行
- 環境変数と Web 画面の両方からの設定

### 含まないもの

- MTP 接続（DJI Osmo Pocket 4 が UMS として見えることを実測で確認したため不要）
- PTP 接続（Canon EOS 70D は PTP 固定だが、SD カードリーダー経由で取り込む方針としたため
  初回スコープ外。将来 `SourceProvider` として差し込める構造にはしておく）
- Immich 以外の転送先
- 複数ユーザ・権限管理（§14 のとおり単一パスワードのみ）
- ソースデバイスへの書き込み（取り込み後の SD 消去など）。read-only を貫く
- 転送先ごとに取り込みルールを変えること。取り込みはデバイスプロファイル、
  送り先は転送先プロファイルと役割を分ける

## 3. 用語

| 用語 | 意味 |
| --- | --- |
| ソースデバイス | USB に接続された機器（カードリーダー、カメラ本体） |
| ボリューム | ソースデバイスが提供するマウント可能なファイルシステム。1 デバイスが複数持つ |
| ボリュームインスタンス | 特定のファイルシステムの「世代」。再フォーマットで別インスタンスになる |
| プロファイル | 機種ごとの取り込みルール一式 |
| プロファイルリビジョン | プロファイル定義の不変スナップショット。編集のたびに増える |
| ライブラリ | NAS 上に蓄積されるオリジナルの集合。不変・追記のみ |
| 派生物 | 結合済み MP4 など、ライブラリから生成されたファイル |
| ソースエントリ | ボリューム上で観測された 1 ファイル。保存済みファイルとは別概念 |
| アーティファクト | ライブラリまたは派生物として公開される 1 ファイル。公開手順は共通（§9.3） |
| メディアファイル | 公開済みアーティファクトの DB 上の表現 |
| 結合グループ | 連続録画として結合すべき分割ファイルの集合 |
| 論理宛先 | ユーザが名前を付けて管理する送り先。同一性は `upload_destination.id` |
| 宛先リビジョン | ある時点の接続設定一式の不変スナップショット |
| リモートターゲット | 実際に繋がった Immich のアカウント。`remote_user_id` で観測する検出値 |

## 4. 実現可能性の検証結果

TrueNAS 25.10.6（kernel 6.12.99-production+truenas / Docker 28.3.1）の実機で確認した。

| 検証項目 | 結果 | 設計への影響 |
| --- | --- | --- |
| DJI Osmo Pocket 4 の USB プロトコル | **USB Mass Storage**（`class=08 subclass=06`、`usb-storage` と `uas` の両インタフェース） | MTP 実装が不要になった |
| TrueNAS の USB 自動マウント | **無し**。公式にも機能要望が Not Accepted | アプリ自身がマウントする必要がある |
| privileged コンテナ内でのマウント | **可能**。`-t exfat -o ro,noatime` の型明示が必須（型を省くと busybox mount が `/proc/filesystems` を総当たりして EINVAL になる） | マウントはコンテナ内で完結する |
| ホットプラグの伝播 | **可能**。`-v /dev:/dev` でホストの devtmpfs を直接参照すれば `add` / `remove` が届く | 起動後に挿したデバイスを扱える |
| udev イベントの受信 | **可能**。コンテナ内で kernel uevent（netlink）を受信できる | ポーリングでなくイベント駆動にできる |
| ファイルシステムモジュール | exfat / vfat はホストに存在するがロードされていない。mount(2) が `request_module` を出すとホスト側 `/sbin/modprobe` が走る | `/lib/modules:ro` をマウントする |
| RNDIS の副作用 | Osmo 接続時にネットワークインタフェース（`enx*`）が生えるが DOWN のまま。デフォルトルートは影響を受けない | 対処不要 |

### DJI Osmo Pocket 4 は 2 つのボリュームを出す

Pocket 4 は **107GB の内蔵ストレージ**を搭載しており（Pocket 3 には無かった）、
microSD スロットと合わせて 2 つの独立したストレージを持つ。USB 接続時にはこれを
別々のガジェット実装で同時に見せる。

```
sdj  108.1G  LIO-ORG / RAMDISK-MCP     (パーティションテーブルあり)
└─sdj1 108.1G exfat  Pocket4   UUID=4356-50A7      ← 内蔵ストレージ
sdk  477.5G  Linux / File-Stor Gadget  exfat  SD_Card  UUID=26B1-2FD6  ← microSD（superfloppy）
```

`RAMDISK-MCP` は Linux カーネルの SCSI target framework（LIO）の rd_mcp バックストアの
既定 product string で、DJI がそのまま使っているだけである。108 GiB の実 exfat が
載っている以上、実体は内蔵フラッシュである。

**両方に取り込むべき映像が入りうる**ため、「どちらか一方を選ぶ」のではなく
**両方をスキャン対象にする**。ディスク直（superfloppy）とパーティションの両方を
候補に含める必要がある。

同一デバイスの 2 ボリュームは同じ `library/dji-osmo/` に合流する。DJI のファイル名は
撮影時刻（`DJI_YYYYMMDDHHMMSS_...`）で一意なので実際の衝突はまず起きないが、
起きた場合は §9.3 の衝突処理が働く。

### USB ポートパスは同定に使えない

抜き挿しで `usb2/2-5` → `usb2/2-4` と移動することを実測した。さらに `/dev/sdX` は
再利用されるため、スキャン時点のデバイスノードをマウント時に信用してはならない（§9.2）。

### DJI は `.LRF` プロキシ動画を書く

`DJI_20260808125215_0001_D.LRF` が MP4 と対で存在する。拡張子ホワイトリストで除外する。

## 5. アーキテクチャ

**2 コンテナ**構成とする。特権を必要とするのはマウントだけなので、そこだけを
小さく切り出して分離する。

```
┌──────────────────────────────┐         ┌───────────────────────────┐
│ mediaferry-mountd            │  unix   │ mediaferry (app)          │
│ privileged                   │ socket  │ 非特権 / cap_drop: ALL    │
│                              │◄───────►│                           │
│  DeviceMonitor (uevent)      │ SCM_    │  Scanner / Importer       │
│  MountBroker                 │ RIGHTS  │  GroupDetector / Merger   │
│   → 自身の mount namespace   │ で dirfd│  ImmichClient / JobRunner │
│     内に ro マウント          │  を渡す │  FastAPI + React SPA      │
│                              │         │                           │
│  /dev, /lib/modules          │         │  /data （データセット）    │
│  /data へのアクセスは無し     │         │                           │
└──────────────────────────────┘         └───────────────────────────┘
```

### ファイル記述子の受け渡し

`mountd` はマウントしたボリュームをコンテナ間で共有しない。代わりに、
**マウントルートのディレクトリ記述子（`O_RDONLY|O_DIRECTORY`）を
`SCM_RIGHTS` でアプリへ渡す**。アプリはその dirfd を起点に、
`os.scandir(dirfd)` と `dir_fd=` 付きの `os.open()` という `*at` 系システムコールだけで
走査・読み取りを行う。

この方式を選んだ理由は 3 つある。

1. **マウント伝播（`rshared`）が不要**になる。TrueNAS のカスタムアプリで
   共有マウントが機能するかという未検証の依存が消える。
2. **`mountd` に `/data` へのアクセスが一切不要**になる。特権側がアプリの指示で
   ライブラリや DB に書く経路（confused deputy）が原理的に存在しない。
3. **アプリが読めるのは `mountd` が開いたものだけ**に限定される。アプリが
   マウント先のパスを組み立てて任意の場所を触ることができない。

性能上の不利はない。渡されるのは通常のファイル記述子で、読み出しはネイティブ速度になる。

パス解決には常に単一のパス構成要素のみを使い、`..`・絶対パス・シンボリックリンクを
辿らない（`O_NOFOLLOW`）。これにより `openat2(RESOLVE_BENEATH)` と同等の閉じ込めを
構成的に実現する。

### アプリ内部の層

```
├─ web/     React SPA。ビルド成果物を FastAPI が静的配信
├─ api/     FastAPI: REST + SSE
├─ core/    ドメインロジック。OS もネットワークも知らない
└─ worker   単一 asyncio ワーカー。SQLite のジョブテーブルで駆動
```

`core/` を純粋に保ち、副作用を境界のアダプタに閉じ込める。USB 実機なしで CI を
回せるようにするため（デバイス層を fake に差し替える）と、将来の移管時に
外部依存の差し替え点を明示するため。

### コンポーネント

| コンポーネント | 所属 | 責務 |
| --- | --- | --- |
| `DeviceMonitor` | mountd | netlink uevent の購読と候補ボリュームの列挙 |
| `MountBroker` | mountd | 検証付き read-only マウントと dirfd の受け渡し |
| `BrokerClient` | app | mountd との unix socket 通信と fd 受領 |
| `ProfileRegistry` | app | プロファイルとリビジョンの解決・CRUD |
| `Scanner` | app | dirfd 起点の走査、既知ソースエントリとの照合 |
| `ArtifactPublisher` | app | staging → 検証 → no-clobber 公開 → DB 反映（§9.3） |
| `Importer` | app | ソースからの読み出しと SHA-1 計算。公開は `ArtifactPublisher` に委譲 |
| `Reconciler` | app | 起動時に DB とファイルシステムの齟齬を回収 |
| `GroupDetector` | app | 分割グループの検出 |
| `Merger` | app | ffmpeg concat と結合結果の検証。公開は `ArtifactPublisher` に委譲 |
| `ThumbnailService` | app | サムネイル生成とキャッシュ |
| `SelectionService` | app | アップロードの選択肢と宛先ごとの状態の算出（§10） |
| `ImmichClient` | app | 重複判定・アップロード・タグ付与・撮影日時補正 |
| `JobRunner` | app | ジョブの実行・再開・キャンセル・進捗発行 |
| `SettingsService` | app | env > DB > 既定値の解決とロック情報 |

`GroupDetector` と `Merger` の判定・結合ロジックは現行 `dji_workflow.py` の
`detect_groups` / `merge_group` / `merge_via_ts` を移植する。TS フォールバックを含む
結合処理は実運用で検証済みのため、挙動を変えずに移す。

## 6. デバイスプロファイル

機種差を**コードの分岐ではなく設定の差分**として表現する。これが「DJI 以外にも対応する」
という要件に対する中心的な仕掛けである。

ビルトインとして `dji-osmo` / `canon-eos` / `generic-dcim` を同梱する。ビルトインは
直接編集できず、GUI で編集しようとすると複製が作られる。これによりアプリ更新時に
ビルトインを安全に差し替えられる。

```yaml
slug: dji-osmo                    # 作成後は変更不可
name: DJI Osmo Pocket
hints:                            # 候補の順位付けにのみ使う。単独では確定しない
  usb_ids: ["2ca3:*"]
  volume_labels: ["SD_Card", "Pocket4"]
require:                          # すべて満たすことが確定の必要条件
  roots: ["DCIM", "PANORAMA"]     # いずれか 1 つ以上が存在すること
  filename_pattern: '^DJI_\d{14}_\d{4}_D\.(MP4|JPG)$'
  min_matching_files: 1
scan:
  roots: ["DCIM", "PANORAMA"]
  extensions: [MP4, JPG]          # .LRF はここに無いので除外される
timestamp:
  # 先頭から順に試し、値が出た時点で止まる。**末尾は mtime でなければならない**
  # （mtime だけが必ず値を返すので、算出が全域関数であり続ける）
  source: [filename, mtime]       # filename | exif | container | mtime
  pattern: '^DJI_(?P<ts>\d{14})_'
  format: "%Y%m%d%H%M%S"
  timezone_policy: force_offset   # none | force_offset
  timezone: null                  # force_offset なら設定必須（§12.2）
  mtime_semantics: instant        # wall_clock（既定）| instant
  container_semantics: wall_clock # wall_clock（既定）| instant
merge:
  enabled: true
  tolerance_seconds: 5
  min_part_size_gib: 15
  sequence_pattern: '_(?P<seq>\d{4})_D$'
  output_name: "DJI_{ts}_{first_seq}-{last_seq}_MERGED.MP4"
stack:
  enabled: false                  # RAW を書かない機種なので組が無い（下記）
immich:
  tags: ["DJI Osmo Pocket 4"]
  tag_pre_existing: true          # 既存アセットにもタグは付ける（追加のみ）
  fix_datetime_after_upload: true # 自分が作成したアセットに限る（§9.6）
```

### マッチ規則（明示）

`hints` と `require` を分けたのは、「USB ID だけで確定してしまう」経路が
指摘されたためである。

- `hints` は**候補プロファイルの順位付けにのみ**使う。単独で確定させない。
  `usb_ids` と `volume_labels` は OR、各リスト内の要素も OR。
- `require` は**確定の必要条件**で、すべて AND。`roots` はいずれか 1 つ以上が
  存在すればよい（OR）。`filename_pattern` に一致する実ファイルが
  `min_matching_files` 件以上あることを、**マウント先の中身を見て確認する**。
- **中身が空でも正当なボリュームがある。** 実測で、Osmo の内蔵ストレージは
  `DCIM/` を持つが中身が無かった（まだ内蔵に録画していない）。`require.roots` を
  満たし、かつ `hints` にも一致するボリュームは、`min_matching_files` を満たさなくても
  **暫定マッチ**として扱う。ただし `identity_confidence` は `low` とし、自動取り込みの
  対象にはしない。一度でも `filename_pattern` に一致するファイルを観測したら `high` に
  上げる。画面では「対象外」ではなく「対象だが中身が無い」と表示して区別できるようにする。
- どのプロファイルの `require` も満たさないボリュームは `generic-dcim` に落ちる。
  `generic-dcim` の `require` すら満たさなければ「対象外」として表示のみ。

`require.roots` と `merge.output_name` に現れるパスは、`..`・絶対パス・シンボリック
リンクを禁止し、解決後にマウントルートまたは `DATA_ROOT` の内側であることを検証する。

### プロファイルのリビジョン

GUI での編集は既存定義を書き換えず、**新しいリビジョンを作る**。取り込み・結合・
アップロードの各レコードは使用したリビジョン ID を保持する。これにより、後から
プロファイルを変えても過去データの解釈が変わらない。

- `slug` は作成後 immutable。ライブラリのパスに使われるため。
- 使用済みプロファイルは削除できず archive のみ。
- タイムスタンプ解釈やタイムゾーンを変えた場合、既存データへの再計算は自動では
  行わず、`recompute_timestamps` ジョブとして明示的に実行する。

### タイムゾーン方針

`timezone_policy` は現行スクリプトの `--fix-timezone` を一般化したもの。

- `none`: 撮影日時にアプリは介入しない。EXIF や埋め込みメタデータを Immich に委ねる。
  機種が分からない `generic-dcim` はこちら。
- `force_offset`: 連鎖から得た**壁時計**に `timezone` のオフセットを付与して
  `dateTimeOriginal` を書き戻す。DJI も Canon もこちら —— DJI は MP4 の `creation_time` を
  UTC で書きつつオフセットも GPS も書かず、Canon は**現地の壁時計に `Z` を付けて書く**。
  どちらも Immich が撮影地の TZ を判定できず、`localDateTime` を UTC の壁時計のまま
  採用してしまう（Canon は 9 時間ずれるのを実測。
  [`history/hardware-verification.md`](history/hardware-verification.md)）。
  `none` のときは描画に使う TZ が無いので、UTC 表現の壁時計をそのまま採る。

`mtime_semantics` は **mtime が何を表すか**を宣言する。**媒体の性質であって、値の形からは
見分けられない。**

- `wall_clock`（既定）: 現地の壁時計を UTC と見なした疑似 epoch。FAT32 と、exFAT でも
  `OffsetFromUtc` の valid bit が立っていない媒体はこちら（Linux はマウントの
  `time_offset`、既定 0 で合成する）。UTC 表現の桁を壁時計として読む
- `instant`: 真の瞬間。exFAT の `OffsetFromUtc` を書く媒体（DJI で実測。
  [`history/hardware-verification.md`](history/hardware-verification.md)）。`timezone` を
  付けた値をそのまま使い、**オフセットを付け直さない**（naive の壁時計へ落とすと DST の
  戻りで 1 時間ずれる）

`container_semantics` は **器（QuickTime の `format.tags.creation_time`）が何を表すか**を
同じ形で宣言する。**器が申告した文字列は解釈せずそのまま `media_file.container_wall` に持ち、
意味はプロファイルが決める** —— そうしておけば、意味を読み違えていても再計算で直せる。

- `wall_clock`（既定）: 現地の壁時計。**`Z` が付いていても信じない。**
  Canon はこちら（実測）—— 桁だけを壁時計として採る
- `instant`: 真の瞬間。タイムゾーン付きの値をそのまま使う

**器が指すのは録画の開始時刻**で、mtime（録画の終了時刻）とは別物である。分割された
片は開始が別々で、mtime は 2 つとも同じになる。QuickTime の epoch
（`1904-01-01T00:00:00`）は「書かれていない」と同義なので、値が無いものとして次へ落とす。

下の「曖昧・存在しない壁時計」の扱いは、ファイル名と EXIF、それに `wall_clock` の mtime と器
—— **壁時計から始めた値**だけに当たる。

**この宣言は 3 か所に効き、3 つは連動する**（片方だけ別の意味で読むと、`library/` と
`derived/` で衝突接尾辞の壁時計や mtime の epoch がずれる）: `captured_at` の算出
（`timestamps._wall_clock`）、公開名の衝突接尾辞（`publisher._collision_stamp`）、
結合出力の mtime（`merger._recording_end_ns`）。

`force_offset` かつ `timezone` が未解決（プロファイルにも `MEDIAFERRY_DEFAULT_TIMEZONE`
にも値が無い）の場合は、**取り込みを開始せず設定画面へ誘導する**（§12.2）。判定は
`importer.run` の前検査で、**1 バイトも copy する前に**ジョブごと失敗させる。
`canon-eos` も `force_offset` なので、**タイムゾーンが入っていないと Canon の取り込みは
1 件も通らない**。

DST の境界で壁時計が曖昧（1 時間が 2 回ある）または存在しない場合は、
それぞれ「先に来る方を採用」「1 時間後ろへずらす」と決め、`captured_at_note` に記録する。

### スタッキング（`stack`）

RAW+JPEG を同時記録する機種では、1 回のシャッターで 2 つのファイルができる
（70D なら `IMG_1234.CR2` と `IMG_1234.JPG`）。Immich では別々の写真として並ぶので、
**アップロード後にスタックで束ねる**（§9.11）。組を決めるのはローカルであり、
プロファイルの規則として書く。

```yaml
stack:
  enabled: true
  extensions: [JPG, CR2]     # 先頭ほど primary。ここに無い拡張子は組に入れない
```

`tolerance_seconds` はパーサが**許すが読まない**。既存の `profile_revision` は
この鍵を持つ JSON のまま保存されているので、拒むと過去のリビジョンが開けなくなる。

**組の同一性は 3 条件すべてを満たすものとする。撮影時刻は見ない。**

| 条件 | どこで見るか | 何を閉じるか |
| --- | --- | --- |
| 同じカード | `source_entry.volume_instance_id`（**両者の published な観測が 1 つでも同じボリュームで重なること**） | 連番が一周した別カードとの誤結合 |
| 同じディレクトリ・同じ stem | `source_entry.rel_path`（**カード上の原名**） | 公開時の改名で組が崩れる／別物と組む |
| **同じ時点でカードに在ったこと** | `source_entry.copresent_key`（**鍵ごとに一致すること**） | 片方だけ撮り直したときに、古い published な行と組む経路 |

上の 3 条件は**同じ観測で**成り立たなければならない。`(volume_instance_id, stem_prefix)`
を鍵として持ち、鍵の集合が交わることを見る —— 「別のカードで stem が一致、別のカードで
ボリュームが一致」で通り抜ける経路を閉じるためである。同席の証拠も**鍵ごとに**
突き合わせる（集合へ潰すと、別々の観測でそれぞれが一致するだけの組が通る）。

**同席の印**（`copresent_key`）は `<スキャンの job_id>:<stem prefix>` で、**同じ鍵の下に
`stack.extensions` の異なる拡張子が 2 つ以上同時に見えたスキャン**だけが書く。印は
`media_file_id` が `NULL` に戻されたとき（＝中身が変わって取り込み直しになるとき）に
消える。**`quick_fingerprint` の一致／不一致を引き金にしない** —— あれは確率的な
キャッシュ鍵であって完全性検査ではないので、撮り直したファイルを黙って取りこぼす。

`copresent_key` が `NULL` なのは「同席していない」ではなく「**まだ確かめられていない**」
である。判定では確かめられない相手として扱い、組まない（見送りの理由を画面に出す）。

**同じ拡張子の相方が 2 つ以上あるときは組まない。** `UNIQUE (volume_instance_id, rel_path)`
があるので同じ鍵の下に同じ原名は 1 つだが、拡張子は `{ext.upper()}` で正規化して
突き合わせるため、case-sensitive なファイルシステムでは `IMG_0001.JPG` と `IMG_0001.jpg`
が同じ拡張子として並びうる。**どちらかを機械に選ばせない。** 送信そのものは止めず、
「同じ拡張子の相方が複数ある。自動では決められない」を理由として画面に出す。

**撮影時刻を条件にしない理由。** 時刻は同じ 1 枚であることを弱めこそすれ強めない。
カメラの時計が切れたまま撮り、後から一括で日時を入れ直すと、JPG だけ書き換わって
CR2 が元のまま残りうる（RAW に書き込める道具の方が少ない）。逆に時刻が揃っていても、
それは同席の証拠より弱い。時刻の食い違いは**誤って組を拒む理由にしかならない**ので
外した（`docs/decisions.md`）。

**ライブラリ側の `media_file.rel_path` の stem で組んではならない。** 公開は衝突時に
名前へ `_{stamp}` を足す（§9.3）ので、次の経路で別々の写真が 1 つのスタックに入る。

1. カード A が JPEG のみで `IMG_1234.JPG` を公開済み
2. 連番が一周した別のカード B から `IMG_1234.CR2` が入る。CR2 は衝突しないので改名されない
3. ライブラリ上の stem が一致してしまう

**カード上の観測は 1 つに絞らない。** 1 つの `media_file` は複数の `source_entry` を
持ちうるし、`observed_at` は再スキャンのたびに更新される（`scan.py` の `_touch`）ので、
「最初の観測」を順序で選ぶと**同じ組が実行のたびに変わりうる**。**観測ごとに
`(volume_instance_id, stem_prefix)` の鍵を作り、その集合が交わること**を条件にする
（順序に依らない）。

**ボリュームと stem を別々に見てはならない。** 集合を「ボリュームの集合」と
「1 つの stem」へ平坦化すると、*別の観測*でボリュームが一致し、*さらに別の観測*で
stem が一致するだけの組が通る。鍵は必ず組にして持つ。

同じ `media_file` が複数の観測で候補に入りうるので、**組の member は
`media_file_id` で一意化する**。しないと、同じ資産を 2 回送り、同じレコードを
2 回記録することになる。

カード上の観測が残っていない `media_file`（再フォーマットや、同じパスに別の中身が
来て `media_file_id` が外れた場合）は、理由つきの見送りにする。

**残る穴を 1 つ明記して受け入れる。** `volume_instance` の同定は
`(fs_uuid, fs_type, size_bytes)` による推測なので（§8）、**複製・復元した媒体は同じ
`volume_instance` に畳まれる**。同じ `rel_path` は 1 行に畳まれるため、複製元と複製先で
同じ名前に別々の撮影が入っていると、片方の中身が他方を上書きした形で観測される。
中身が変われば `media_file_id` が外れて同席の印も消えるので、**古い片割れと組むことは
無い** —— ただし「片方だけ差し替わり、もう片方は元のまま」という復元では、両者が同じ
スキャンで同席したことになり、組が成立する。これは観測としては正しい（その時点で
2 つは同じカードに同時に在った）ので受け入れる。**不変の観測 cohort を別に永続化する
案は採らない** —— 追加する表と provenance の維持コストが、閉じるリスクに見合わない。

`recompute_timestamps` は、`captured_at` を動かしたレコードの見送り（`skipped`）を
未評価へ戻す（`stacked` は戻さない）。**時刻を組の条件から外したので、この戻しで
結果が変わることはもう無い** —— いま見送りの理由になりうるもの（規則が無効・拡張子が
対象外・相方が居ない／未完了・無効化済み・同席の証拠が無い・曖昧・`origin`・資産 ID・
別プロファイル）は、どれも `captured_at` を見ない。無駄な再評価が 1 巡するだけなので
そのままにしてある。

**規則の読み方が変わったときは、定義 JSON の比較では戻せない。** `_publish_revision` が
見ているのは**パース後の `StackRule`** どうしなので、`stack.tolerance_seconds` のように
「JSON には残るが読まなくなった」鍵は、旧版と新版が同じ値にパースされて差が出ない。
**この場合、既に付いた見送りは自動では戻らない。** 読み方を変えたときは、見送りを未評価へ戻す手当てを別に用意する。

## 7. ストレージレイアウト

TrueNAS のデータセットをホストパスでコンテナ内 `/data` にマウントする。
データセットのパスはデプロイ時の設定であり、リポジトリには記載しない。

```
/data/
├── library/<profile-slug>/<デバイス上の相対パス>   # オリジナル。不変・追記のみ
├── derived/<profile-slug>/<同じ相対パス>           # 結合済み MP4 等の派生物
├── staging/<job-id>/                                # 公開前の一時ファイル（import / merge 共通）
├── work/<job-id>/                                   # ffmpeg の中間生成物
└── var/
    ├── mediaferry.sqlite3
    ├── thumbs/                                      # サムネイルキャッシュ。再生成可
    └── logs/
```

デバイス上の相対パス（`DCIM/DJI_001/DJI_....MP4`）を保つ。**この鏡写しの構造は
意図的な設計価値**で、ユーザが NAS を直接開いて中身を辿れることを保証する。
プロファイル slug で分けるのは、複数機種のファイル名が衝突しうるため
（`IMG_0001.JPG` は多くの機種で使われる）。

`staging/` は `library/` と `derived/` と**同じファイルシステム上**に置く。
公開が `link(2)` による原子的操作である必要があるため。

`staging/` と `work/` はジョブ ID ごとに分ける。起動時の掃除は、リースが失効した
ジョブのディレクトリだけを対象とし、かつ `artifact_staging` に生存レコードが無いことを
確認してから行う（§9.4）。

DB には絶対パスを保存せず、`DATA_ROOT` からの相対パスのみを正規形とする。
データセットのマウント位置を変えても整合性が保たれるようにするため。

### 現行構成からの変更点

現行の `upload/` と `failed_merges/` は**廃止する**。

`upload/` は immich-go にフォルダを食わせるためだけに存在した使い捨てのハードリンク
ツリーである。Immich API を直接叩くならアップロード対象は DB のクエリで表現でき、
ディレクトリを作る必要がない。これに伴い、`upload/` の削除確認プロンプト、
ハードリンクの再構築、予約ディレクトリによる走査除外がすべて不要になる。

ただし、これらのディレクトリが**暗黙に担っていた 3 つの役割**を明示的に置き換える
必要がある。この置き換えが本設計の成立条件である。

| 暗黙に担っていた役割 | 置き換え先 |
| --- | --- |
| アップロード対象の表現（`upload/` に置かれたものが対象） | §10 の選択肢の提示規則 |
| 中間生成物の隔離（`failed_merges/` に隔離） | `merge_group.status` と `staging/` `work/` |
| 再開判断（ディレクトリの有無で判断） | §9.4 の公開プロトコルと §9.5 の reconciliation |

## 8. データモデル

SQLite（WAL モード、`busy_timeout` 設定、書き込みは単一ワーカーに限定）。
すべての列挙値に CHECK 制約、すべての外部キーに ON DELETE を明示する。
マイグレーションはバージョン管理し、バックアップとリストアの手順を定める（§18-4）。

### プロファイル

| テーブル | 主なカラム |
| --- | --- |
| `device_profile` | `id`, `slug` UNIQUE immutable, `name`, `builtin`, `archived_at`, `current_revision_id` |
| `profile_revision` | `id`, `profile_id`, `revision`, `definition_json`, `schema_version`, `created_at`。**不変** |

### ソース側

| テーブル | 主なカラム |
| --- | --- |
| `source_device` | `id`, `usb_vendor_id`, `usb_product_id`, `usb_product`, `serial`, `first_seen_at`, `last_seen_at` |
| `volume_instance` | `id`(内部 UUID), `fs_uuid`, `fs_type`, `fs_label`, `size_bytes`, `identity_confidence`, `content_manifest_digest`, `last_source_device_id`, `profile_id`, `profile_revision_id`, `trusted_at`, `scanned_at`, `first_seen_at`, `last_seen_at` |
| `volume_presence` | `id`, `volume_instance_id`, `generation`, `device_node`, `major`, `minor`, `sysfs_path`, `attached_at`, `detached_at` |
| `source_entry` | `id`, `volume_instance_id`, `rel_path`, `size_bytes`, `mtime_ns`, `quick_fingerprint`, `fingerprint_version`, `media_file_id`, `state`, `observed_at` |

`volume_instance` を `source_device` と分けたのは、**カードはリーダーの間を移動する**
ため。デバイスとは独立に記憶する。

**USB の `serial` を一意な識別子として扱ってはならない。** 実測で、
DJI Osmo Pocket 4 の `serial` は Linux ガジェットの既定値 `123456789ABCDEF` だった。
機体を識別する文字列は `product`（`OsmoPocket4-<機体固有>`）側にある。同じ機種が
2 台あれば `serial` は衝突するので、デバイス同定は
`(usb_vendor_id, usb_product_id, usb_product, serial)` の組で行う。

`volume_presence` を分けたのは、同じ identity のカードが同時に 2 枚挿さる可能性が
あるため。ジョブは `volume_presence.id` と `generation` を参照し、
`device_node` を信用しない。

#### ボリューム同定の確度

`(fs_uuid, fs_type, size_bytes)` は**識別子ではなく推測**である。read-only で扱う以上、
ボリュームに永続的なマーカーを書けないので、これは原理的な限界である。
`dd` で複製したカード、UUID を保持したまま復元・再フォーマットしたカード、
UUID が空で容量が同じカードは、同一と誤認しうる。

そのため `identity_confidence` を持つ。

| 確度 | 条件 | 自動取り込み |
| --- | --- | --- |
| `high` | `fs_uuid` があり、`content_manifest_digest` が前回と連続的（既知ファイルが残存） | `trusted_at` があれば自動 |
| `low` | `fs_uuid` が空、同一 identity の同時接続を検出、または既知ファイルがほぼ消えている | **常に再承認を要求** |

`content_manifest_digest` は「`scan.roots` 直下のディレクトリ名と、既知ファイルの
残存率」から作る軽量な要約で、フォーマット直後や別カードへの差し替えを検出する。
完全な保証ではないため、§12.1 にリスクを明記してユーザに提示する。

#### quick_fingerprint

スキャン時の同一性判定に `(rel_path, size, mtime)` だけを使うと、
「SD を再フォーマットして連番が再利用され、たまたま同じサイズ・同じ mtime の
別ファイルが同じパスに来る」ケースを取りこぼす。かといって 16GiB のファイルに
毎回フル SHA-1 を掛けるのは実用に耐えない。

```
quick_fingerprint = sha1( b"mfq" ‖ u8(version) ‖ u64le(size) ‖ w[0] ‖ w[1] ‖ … ‖ w[15] )
```

`w[i]` は 16 個の 64KiB ウィンドウ（合計 1MiB）。オフセットは `size` から決定的に
算出した等間隔位置とし、範囲が重なる場合は重複を除去する。`size < 1MiB` なら
ファイル全体を読む。ドメイン分離子と固定幅のサイズ符号化を含めることで、
連結の曖昧さを排除する。`fingerprint_version` を `source_entry` に保存し、
将来アルゴリズムを変えても既存レコードを解釈できるようにする。

**これは同一性の確率的キャッシュキーであって、完全性検査ではない。**
サンプリング対象外の領域だけが変化・破損したファイルは検出できない。
ビットロットの検出が必要なら、保存済みの `media_file.sha1` とソースのフルハッシュを
突き合わせる `deep_verify` ジョブ（任意実行）を使う。

フル SHA-1 はコピー時のストリームで 1 回だけ計算し、`media_file.sha1` に保存する
（Immich の重複判定に必要）。ローカル同一性のためだけに SHA-256 を追加計算することは
しない（判断の根拠は [`decisions.md`](decisions.md)）。

`quick_fingerprint` が一致しても `mtime` が記録より古いなど不整合がある場合は
「曖昧」と判定し、フルハッシュで確認する。

### 公開（アーティファクト）

| テーブル | 主なカラム |
| --- | --- |
| `artifact_staging` | `id`, `kind`(import/merge), `job_id`, `lease_token`, `state`, `staging_rel_path`, `final_rel_path`, `expected_size`, `content_sha1`, `metadata_json`, `source_entry_id`, `merge_group_id`, `created_at`, `updated_at` |
| `media_file` | `id`, `role`(original/derived), `profile_id`, `profile_revision_id`, `rel_path` UNIQUE, `size_bytes`, `mtime_ns`, `sha1`, `kind`(photo/video), `captured_at`, `captured_at_source`(filename/exif/**container**/mtime), `captured_at_tz`, `captured_at_note`, **`container_wall`**（ffprobe が返した `creation_time` を**解釈せず生のまま**持つ。意味は `container_semantics` が決めるので、読み違えても再計算で直せる）, `duration_seconds`, `probe_state`, `missing_at`, `captured_at_revision_id`, `created_at` |

`artifact_staging` は **import と merge の両方**が使う。派生物の公開に
取り込みと同じ crash protocol を適用するためで、これがないと結合物だけが
回収不能になる。

`artifact_staging.state` ∈ `writing` / `staged` / `published`。

**`staged` に遷移する時点で `final_rel_path`・`content_sha1`・`expected_size`・
`metadata_json` をすべて永続化する。** これにより reconciliation はパスの推測をせず、
永続情報とハッシュだけで公開を再開できる。

`media_file.probe_state` ∈ `ok` / `failed` / `not_applicable`。ffprobe が正当に失敗した
場合と、そもそも実行していない場合を区別する。公開前にメタデータを確定させるため
（§9.3）、公開済みレコードに `not_run` は存在しない。

### 結合

| テーブル | 主なカラム |
| --- | --- |
| `merge_group` | `id`, `profile_id`, `profile_revision_id`, `status`, `input_digest`, `output_media_file_id`, `detected_by`(auto/manual), `superseded_by_id`, `tool_version`, `verification_json`, `adopted_at`, `error` |
| `merge_member` | `merge_group_id`, `media_file_id`, `position` |

`input_digest` は「構成ファイルの ordered な id と sha1、結合設定、
プロファイルリビジョン」から作る決定的なダイジェスト。

制約:

- `UNIQUE(merge_group_id, position)`, `UNIQUE(merge_group_id, media_file_id)`
- `UNIQUE(input_digest) WHERE superseded_by_id IS NULL`
- 1 つの `media_file` が同時に属せるアクティブグループは 1 つまで（部分ユニーク索引）
- グループの構成を手動編集すると旧グループは `superseded_by_id` で新グループを指す

### 転送先とアップロード

| テーブル | 主なカラム |
| --- | --- |
| `upload_destination` | `id`, `name` UNIQUE, `kind`('immich'), `enabled`, `archived_at`, `current_revision_id`, `created_at` |
| `destination_revision` | `id`, `destination_id`, `revision`, `target_epoch`, `base_url`, `public_url`, `credential_id`, `remote_user_id`, `server_instance_id`, `verified_at`, `created_at`。**不変** |
| `destination_credential` | `id`, `destination_id`, `revision`, `secret_encrypted`, `key_fingerprint`, `created_at`, `purged_at` |
| `upload_record` | `id`, `destination_id`, `target_epoch`, `media_file_id`, `state`, `selection_rule`, `origin`, `first_check_result`, `remote_asset_id`, `remote_is_trashed`, `remote_checked_at`, `checksum`, `attempts`, `last_error`, `eligibility_reason`, `merge_group_id`, `claim_job_id`, `claim_token`, `claim_expires_at`, `destination_revision_id`, `stack_state`, `remote_stack_id`, `stack_reason`, `invalidated_at`, `invalidated_reason`, `updated_at` |

**転送先はユーザが管理するプロファイルである。** デバイスプロファイルと同じく
Web 画面で作成・編集する（§12.3）。用語を 3 層に分ける。

| 層 | 意味 | 可変性 |
| --- | --- | --- |
| **論理宛先**（`upload_destination`） | ユーザが付けた名前で識別する送り先。`id` が安定した同一性 | `name` と `enabled` は可変 |
| **リビジョン**（`destination_revision`） | ある時点の接続設定一式のスナップショット。`base_url`・資格情報・検証で得た remote identity | **不変**。編集のたびに新しい行が増える |
| **リモートターゲット** | 実際に繋がった Immich のアカウント。`remote_user_id` で観測する | 検出値 |

`remote_user_id` と `server_instance_id` は**検出値であって同一性ではない**。
`remote_user_id` に UNIQUE 制約は置かない。同じ Immich アカウントを指す論理宛先を
2 つ作ること（内部 URL 用と VPN 用、別の運用名など）は正当な使い方であり、
**警告は出すが拒否も統合もしない**。

実測で、Immich v3.1.0 はサーバインスタンス ID を公開していないことが
分かった。`/api/server/about` が返すのは version・build・sourceCommit で、
いずれもリリースに紐づく値なので同じ版の全サーバで一致し、識別子にならない。
そのため当面 `server_instance_id` は null で、検出値は実質 `remote_user_id`
だけになる。将来の Immich がインスタンス ID を公開したら、コードは優先して
そちらを使う。

#### `target_epoch` — 履歴を引き継いでよいかの境界

編集には 2 種類ある。**リビジョンが増えるのは常に、`target_epoch` が進むのは
向き先が変わったときだけ**である。

| 編集 | 再検証の結果 | `target_epoch` | アップロード履歴 |
| --- | --- | --- | --- |
| API キーのローテート、内部 URL の変更 | `remote_user_id` が同じ | 据え置き | **引き継ぐ** |
| 別アカウント・別サーバへの向き替え | `remote_user_id` が違う | **進める** | 引き継がない |
| `base_url` のホストが変わり `remote_user_id` は同じ | 判別できない（DB 複製・復元の可能性） | **ユーザに確認して決める** | 選択による |

3 つ目は自動判定できない。同じユーザ UUID を持つ別ライブラリ（DB を複製・復元
したサーバ）かもしれないし、単に経路を変えただけかもしれない。**黙ってどちらにも
倒さず、ユーザに「同じライブラリか」を尋ねる。**

`upload_record` の同一性は `(destination_id, target_epoch, media_file_id)`。
epoch を進めれば、**旧 epoch の記録を監査履歴として残したまま**、同じメディアを
新しい向き先へ送れる。**一意性は表制約ではなく、有効な行だけを見る部分 UNIQUE
索引で守る**（下記）。

#### 編集と削除の規則

- 編集は**接続の検証に成功してから原子的に反映**する。検証に失敗した設定は保存しない
- 編集の時点で claim 済みのジョブは**旧リビジョンのスナップショットで完走**させる。
  途中で URL や鍵が入れ替わることはない
- 未 claim のキュー項目は、`target_epoch` が据え置きなら新リビジョンで続行、
  epoch が進んだなら**破棄**して理由を記録する
- 論理宛先は物理削除しない。`archived_at` を立てて新規選択の対象から外す。
  履歴と監査情報は残る

**API キーの指紋は転送先の同一性に含めない。** 含めると、漏洩対策でキーを
ローテートしただけで別の転送先と見なされ、全件が再アップロード対象になる。

`base_url` は §12.4 のとおり CDN やリバースプロキシを経由しない直接到達できる
アドレスにする。`public_url` は画面のリンク生成にだけ使う。

**同一性の一意性は、部分 UNIQUE 索引 `upload_record_live_identity` で守る。**

```sql
CREATE UNIQUE INDEX upload_record_live_identity
    ON upload_record (media_file_id, target_epoch, destination_id)
    WHERE invalidated_at IS NULL;
```

守る不変条件は「**有効な**記録は (宛先, epoch, メディア) 1 組につき高々 1 つ」であって
「行が 1 つ」ではない。**表制約の `UNIQUE (destination_id, target_epoch, media_file_id)`
では守れない** —— それだと無効化された行の隣に新しい行を作れず、消滅した記録を
無効化して「まだ送っていない」へ戻す（§9.10）と、同じ組に無効化された行と新しい行が
並ぶので、送信が `IntegrityError` になる。述語を `WHERE invalidated_at IS NULL` に
することで、無効化された行は監査履歴としてその隣に残れる。

**列順は `(media_file_id, target_epoch, destination_id)` でなければならない。** 一意性は
列の集合で決まるので順序は守るものを変えないが、**述語が同じ部分索引どうしは、先頭の
列が重なると計画を奪い合う**（統計は取っていないので、選ばれた計画がそのまま実機に出る）。

- `destination_id` を先頭に置くと `upload_record_claimable`（`(destination_id, state)`）
  から奪う。`claim_next` が「pending の行だけを辿る」から「その宛先・epoch の有効な
  全行を辿って `state` で捨てる」へ落ちる（実測 20 回で 0.000 秒 → 0.095 秒）。claim は
  ファイル 1 本ごとに走るので、同期 1 回が O(N^2) になる
- `(media_file_id, destination_id, target_epoch)` にすると、今度は
  `upload_record_live_pair`（`(media_file_id, destination_id)`）から奪う
- `target_epoch` を 2 番目に挟めば、`destination_id` 単独にも
  `(media_file_id, destination_id)` にも当たらない。**両方を動かさない並びはこれだけ**

**行を使い回す案は採らない。** `first_check_result` は不変（trigger）なので `origin` の
判定をやり直せず、「無効化された記録は再利用しない」とその理由（**なぜ最初に送信を
許可したかが失われる**）にも反する。**行を消す案も採らない** —— 相手が誤って `accept` を
返しただけで、その宛先の送信記録が消える。

`upload_record.origin` ∈ `created_by_us` / `pre_existing` / `unknown`。

`selection_rule` ∈ `default` / `failed_group_member` / `adopted_derived`。
**選択を許可した根拠であり、作成時に決まって以後は変わらない。** claim 時に
どの条件で再評価するかをこれで決める（§10）。

**再試行はこの欄を書き換えない。** 再試行は「`failed` を `pending` へ戻す」操作で
あって、選択の根拠ではない。上書きすると「なぜ最初に送信を許可したか」が失われ、
claim が安全条件しか見なくなって、今なら許可されない古い派生物を送ってしまう。
試行回数は `attempts` が表す。

`invalidated_at` / `invalidated_reason` は状態機械とは独立の直交フラグで、
グループの supersede などでレコードが無効になったことを表す。
`state` の列挙には混ぜない。

#### claim — SQLite でどう排他するか

**SQLite に行ロックは無い。** `SELECT ... FOR UPDATE` は存在せず、`UNIQUE` 制約は
行の重複作成しか防げない。同じ行を読んだ 2 つのジョブがどちらも HTTP へ進むのを
止められない。宛先が複数になったことで、この競合点はメディア数 × 宛先数に増えている。

代わりに **`BEGIN IMMEDIATE` の中の条件付き UPDATE（CAS）**で所有権を取る。

```sql
BEGIN IMMEDIATE;
UPDATE upload_record
   SET state = 'checking',
       claim_job_id = :job_id,
       claim_token = :token,
       claim_expires_at = :expires,
       destination_revision_id = :revision_id
 WHERE id = :id
   AND invalidated_at IS NULL
   AND state IN ('pending', 'needs_recheck')
   AND (claim_expires_at IS NULL OR claim_expires_at < :now)
RETURNING *;
COMMIT;
```

更新できた 1 ジョブだけが実行者になる。0 行なら他のジョブが所有しているか、状態が
変わっている。`failed` からの再開は、先に「`failed` → `pending`」の CAS を明示的な
再試行操作として行う（`selection_rule` は変えない）。

**外部への副作用の直前と、その結果を commit する時点で、`claim_token` とジョブの
リースが一致することを再確認する。** キャンセルされた古いジョブが新しいジョブの
状態を上書きすることを防ぐ。

#### claim の保持と解放

- 実行中はジョブのリースと同時に `claim_expires_at` を heartbeat で延長する
- **終端（`complete` / `failed` / `awaiting_datetime_approval`）、`needs_recheck`、
  再試行による `pending` への差し戻しでは、同じ条件付き UPDATE の中で
  `claim_job_id` / `claim_token` / `claim_expires_at` を NULL に戻す。**
  未来の期限が残ったままだと、明示操作しても期限まで claim できなくなる
- 解放は自分の `claim_token` を持つジョブだけができる。古いトークンでは消せない
- `CHECK`: claim の 3 欄は**すべて NULL かすべて非 NULL**

#### テーブル間の不変条件は DB で守る

単純な外部キーだけだと、**別の論理宛先に属する行を参照できてしまう**。
宛先の取り違えは最も危険な誤りなので、アプリの検証だけに頼らない。

候補キーを用意して複合外部キー（または trigger）で縛る。

| 制約 | 目的 |
| --- | --- |
| `destination_revision` に `UNIQUE(destination_id, revision)` | 版番号の重複を防ぐ |
| `destination_credential` に `UNIQUE(destination_id, id)` | 複合 FK の参照先 |
| `destination_revision` に `UNIQUE(destination_id, target_epoch, id)` | 複合 FK の参照先 |
| `upload_destination.current_revision_id` → 同じ `destination_id` の revision | 他宛先の版を現行にできない |
| `destination_revision.credential_id` → 同じ `destination_id` の credential | **他宛先の鍵を使えない** |
| `upload_record(destination_id, target_epoch, destination_revision_id)` → 同じ宛先・同じ epoch の revision | 送り先の取り違えを DB で防ぐ |

`destination_revision` は不変なので、**UPDATE と DELETE を trigger で禁止**する。
リポジトリ層の作法に頼らず、テストでも固定する。

### ジョブ

| テーブル | 主なカラム |
| --- | --- |
| `job` | `id`, `type`, `status`, `params_json`, `progress_json`, `lease_token`, `lease_expires_at`, `created_at`, `started_at`, `finished_at`, `error` |
| `job_event` | `id`, `job_id`, `seq`, `level`, `message`, `data_json`, `at`。`UNIQUE(job_id, seq)` |

`job.status` ∈ `queued` / `running` / `cancelling` / `cancelled` / `interrupted` /
`succeeded` / `failed`。

## 9. コンポーネント仕様

### 9.1 マウントブローカー

`mountd` は unix ドメインソケット（`/run/mediaferry/broker.sock`）で待ち受ける。

| 要求 | 応答 |
| --- | --- |
| `list_volumes` | 候補ボリュームの一覧。`{volume_key, device_node, major, minor, sysfs_path, fs_type, fs_uuid, fs_label, size_bytes, usb:{vid,pid,serial}, generation}` |
| `open_volume {volume_key, expect:{major, minor, fs_uuid, fs_type, generation}}` | `{handle}` + **マウントルートの dirfd**（`SCM_RIGHTS`） |
| `close_volume {handle}` | ok |
| `subscribe` | uevent のストリーム（add / remove / change と generation） |

強制する制約（アプリ側からは変更できない）:

- 対象は `TRAN=usb` のブロックデバイスのみ
- `fs_type` は allowlist（初版は `exfat`, `vfat`）のみ。`blkid` が返した任意の型を
  そのままカーネルに渡さない
- マウントオプションは常に `ro,nosuid,nodev,noexec` 固定。アプリは指定できない
- マウント先は `mountd` 自身の mount namespace 内の内部パス。アプリからは見えない
- `expect` の内容を**マウント直前と直後の両方で検証**する。不一致なら失敗させる
- 渡す fd は `O_RDONLY|O_DIRECTORY|O_NOFOLLOW`

ソケットとピアの保護:

- ソケットは `mountd` 所有の専用ディレクトリに置き、アプリ側には **read-only** で
  マウントする。アプリがソケットを unlink・置換できないようにするため
- ソケットのモードとグループを固定し、アプリの UID/GID を固定する
- `SO_PEERCRED` で接続元の UID を検証する
- 要求サイズの上限とタイムアウトを設ける
- `handle` は発行した接続に束縛し、別の接続からは操作できない

### 9.2 デバイス検出とボリューム判定

1. `DeviceMonitor` が netlink で `block` サブシステムの uevent を購読する。
   起動時には `/sys/class/block` を走査して既存デバイスを拾う。
2. `TRAN=usb` のディスクと、そのパーティションを候補に加える。ディスク直に
   ファイルシステムがある superfloppy 構成（Osmo の SD カード）と、パーティションに
   ある構成（Osmo の内蔵ストレージ）の両方を扱う。
3. イベント世代（`generation`）ごとに `sysfs_path` + `major:minor` +
   ファイルシステム識別子のスナップショットを取り、`volume_presence` に記録する。
4. **ボリュームごとに**プロファイルを解決する。デバイス単位ではない。
   1. `volume_instance` に記憶されたプロファイルがあれば**候補として**採用する。
      ただし §6 の `require` を必ず再検証する。記憶を無条件に信用しない。
   2. `hints` に一致するプロファイルを優先順に並べる。
   3. 各候補について、dirfd 起点で中身が `require` を満たすかを検証する。
      **最初に満たしたものを採用する。中身の検証を通らないプロファイルは採用しない。**
   4. どれも満たさなければ `generic-dcim`。それも満たさなければ「対象外」。
5. `identity_confidence` を判定する（§8）。`low` なら信頼を継承しない。
6. 対象外と判定したボリュームは直ちに `close_volume` する。

**`subscribe`（uevent のストリーム）は実装していない。**プロトコルには
残してあるが、アプリ側は `list_volumes` の `generation` をポーリングして変化を検出する。
理由は、**高いのは列挙ではなく判定**だから。`enumerate_volumes` は `_is_usb` で絞ってから
`blkid` を掛けるので、USB ブロックデバイスが無い間はサブプロセスが 1 つも起きない。
一方 4 の判定は候補ごとに**実際にマウントする**ので重く、これは uevent 駆動にしても同じ
罠になる（`change` イベントは中身を触らなくても飛ぶ）。分けるべきはトリガの種類ではなく
「安い変化検出」と「高い判定」で、それは観測トークン（`broker_epoch` と `generation`）が
動いたときだけ判定を走らせることで達成できる。**次の担当がここを uevent 化しようとする前に、
この段落を読むこと。**

**TOCTOU 対策**: スキャンからインポートまでの間にデバイスが抜かれ、`/dev/sdX` が
別のカードに再利用される可能性がある（実測で抜き挿し後にポートが移動することを確認済み）。

- キューに積むジョブは `device_node` ではなく `volume_presence.id` と `generation` を持つ
- `open_volume` は常に `expect` 付きで要求し、ブローカー側で再検証する
- `remove` イベントを受けたら、その `generation` に紐づく未実行ジョブを無効化する。
  既に受け取っている dirfd は参照カウントで drain する
- 一度受け取った dirfd はデバイスが抜かれても安全（読み出しは失敗するがパスの
  すり替わりは起きない）

### 9.3 アーティファクトの公開プロトコル（共通）

取り込みと結合の**両方**がこの手順を使う。ファイルの公開と DB のコミットの間に
落ちても、齟齬を検出して回収できる状態にする。

1. `artifact_staging` を `state = writing` で作成し、`staging_rel_path` と
   `job_id` / `lease_token` を記録して commit する。
2. `staging/<job-id>/<uuid>` へ書き込む。**書き込みストリームで SHA-1 を計算する。**
3. ファイルを `fsync` する。
4. サイズとハッシュを検証する。
5. **メタデータをここで確定させる**（`kind`、`captured_at` とその出所、
   動画なら ffprobe の `duration_seconds` と `probe_state`）。
   公開後ではなく公開前に済ませることで、「実体はあるがメタデータが欠けたまま
   永久にスキップされる」状態を作らない。
6. `final_rel_path` を決定する（衝突解決は決定的。下記）。
7. `state = staged` にし、`final_rel_path`・`content_sha1`・`expected_size`・
   `metadata_json` を**すべて永続化して commit する**。
8. **no-clobber で公開する。** `os.link(staging, final)` を使う。既存があれば
   `EEXIST` で失敗するため、`os.replace` のように既存を黙って上書きしない。
   - `EEXIST` の場合: 既存ファイルのハッシュを取る。
     - `content_sha1` と一致 → 既に公開済みとみなし、手順 10 へ
     - 不一致 → 次の決定的な別名を予約し、`final_rel_path` を更新して commit し、
       手順 8 を再試行
9. **公開先の親ディレクトリを `fsync` する。** staging の親が別ディレクトリなら、
   `unlink` 後にそちらも `fsync` する。これを怠ると電源断で公開が失われる。
10. `os.unlink(staging)`。
11. 短いトランザクションで `media_file` を挿入し、`artifact_staging.state = published`、
    呼び出し元のレコード（`source_entry.media_file_id` または
    `merge_group.output_media_file_id`）を更新して commit する。

**手順 2〜7 の途中で落ちたら、その場で書きかけを捨てる。** `writing` の行と
`staging/<job-id>/<uuid>` を消してから例外を上げ直す。起動時の reconciliation
（§9.6）も同じものを捨てるが、それは次の起動まで走らない ——
待つと、動かし続ける限り分割 1 本ぶん（DJI なら 16 GiB）が残り、しかも
`GET /orphans` には出ない（行を持つ実体は孤立ではない）。**捨ててよいのは
自分のジョブが `writing` で持っている行だけ**で、`staged` 以降には触らない。
後始末の失敗で本当の失敗を覆い隠さない。

`link` + `unlink` を採用したのは、`renameat2(RENAME_NOREPLACE)` が Python 標準
ライブラリから直接呼べない一方、`link` は同じ no-clobber 性を持ち、同一ファイル
システム内で原子的だからである。

#### 衝突の扱い（明示）

同じ相対パスに**内容の異なる**ファイルが既にある場合
（SD をフォーマットして連番が再利用されたケース）:

- **既存のファイルは絶対に移動しない。** ライブラリは不変であり、既存を動かすと
  `media_file.rel_path` と、それを参照する `merge_member` / `upload_record` が壊れる。
- **新しく公開する側の保存名を変える。** 順に試す決定的な系列:
  1. `<stem>_<YYYYMMDDHHMMSS><ext>` — タイムスタンプはソース側 mtime を
     プロファイルの `timezone` で解釈した壁時計
  2. `<stem>_<YYYYMMDDHHMMSS>_<sha1 先頭 8 桁><ext>`
  3. `<stem>_<YYYYMMDDHHMMSS>_<sha1 先頭 8 桁>_<n><ext>`（`n` = 2, 3, …）
- 決定的な順序で試すため、途中で落ちて再実行しても同じ名前に落ち着く。
- 同じ内容（サイズ + SHA-1 一致）が既にあれば再公開しない。

用語を分ける。`media_file.rel_path` は**保存先の相対パス**、
`source_entry.rel_path` は**カード上の原名**であり、衝突時には両者が食い違う。

### 9.4 取り込み

1. 新規ファイルの合計サイズと `/data` の空き容量を比較し、不足なら開始しない。
2. `open_volume` で dirfd を取得する。
3. 1 ファイルずつ順に処理する（USB が律速なので並列化しない）。
   dirfd 起点で `O_NOFOLLOW` で開き、§9.3 の公開プロトコルに渡す。
4. すべて完了したら `close_volume` する（`finally` で必ず実行する）。

現行スクリプトの `.rsync-partial` は、中断ファイルが転送先に残ると
`--ignore-existing` がそれを「既存」とみなして永久にスキップするという問題への
回避策だった。staging + 原子的 no-clobber 公開ではこの問題自体が起きない。

### 9.5 スキャン

dirfd 起点で `scan.roots` 配下から `scan.extensions` に一致するファイルを列挙する。
ドット始まりのディレクトリと `._*`（AppleDouble）は除外する。

各ファイルについて `quick_fingerprint` を計算し、`source_entry` を引く。

| 照合結果 | 判定 |
| --- | --- |
| `(rel_path, size, mtime, quick_fingerprint, fingerprint_version)` すべて一致し `state=published` | 取込済 |
| `rel_path` は一致するが他が違う | 新規（衝突処理の対象になりうる） |
| 該当なし | 新規 |
| 一致するが `mtime` が記録より古いなど不整合 | 曖昧。フルハッシュで確認 |
| `fingerprint_version` が古い | 再計算して更新 |

この段階でフル SHA-1 は計算しない（16GiB を読む必要があるため）。

**最後まで見たスキャンは、カードから消えたファイルの行も外す。** 残すと
`pending_count` が実体より多いままになり、画面が「N 件取り込む」と言うのに
取り込みは開けない ENOENT で失敗する。しかも失敗した行は取り込み対象の条件
（`state IN ('seen','failed')`）に居座るので、**以後の取り込みが毎回失敗する**。

| 対象 | 扱い |
| --- | --- |
| 今回の列挙に現れず、**無いと言い切れる**（`ENOENT` / `ENOTDIR`）、`state IN ('seen','failed')` | 行を削除する |
| 今回の列挙に現れないが、**開けば在る** | 残す。`scan.extensions` を狭めただけかもしれない（**「無い」には観測を要求する**） |
| 今回の列挙に現れず、**在るかどうか確かめられなかった**（`EACCES` / `EIO` / `EMFILE` など） | 残す。件数を警告としてジョブに残す。**「確かめられなかった」は「無い」の証明ではない** —— 列挙も開けないディレクトリを黙って飛ばすので、区別しないと一時的な障害で部分木ぶんが消える |
| `state = 'published'` | 残す。「このカードから取り込んだ」という記録で、§9.11 の「同じカード」判定と同定確度の標本が引く |
| `artifact_staging` がまだ指している | 残す。`ON DELETE RESTRICT` なので消しに行くとスキャンごと落ちる。中身は §9.6 が完遂する |

**途中で降りたスキャンは外さない**（`mark_scanned` と同じ理由）。見ていないだけの
ファイルを「消えた」と読むため。**キャンセルは列挙の中だけでなく掃除の直前でも
見る** —— 一致するファイルが 0 件のカードでは列挙が 1 度も回らず、降りたことに
気づけない。

### 9.6 起動時の reconciliation

`Reconciler` が起動時に以下を回収する。**`library/` と `derived/` の両方**を対象とする。

| 齟齬 | 回収 |
| --- | --- |
| `artifact_staging.state = writing` | staging を削除しレコードを破棄。呼び出し元は再実行。**通常はここまで残らない** —— `staged` の手前で落ちた行は publish 側がその場で捨てる（§9.3）。ここに来るのは、捨てる前にプロセスごと落ちた場合 |
| `artifact_staging.state = staged` | §9.3 の手順 8 から再開。永続化済みの `final_rel_path` と `content_sha1` だけを使い、パスを推測しない |
| `artifact_staging.state = published` だが `media_file` が無い | 手順 11 を再実行 |
| `library/` `derived/` に実体があるが `media_file` も `artifact_staging` も無い（orphan） | ハッシュを取って画面に出す。**削除しない** |
| `media_file` があるが実体が無い | `missing_at` を立てて画面に出す。選択肢から外す |
| リースが失効したジョブの `staging/` `work/` | 生存する `artifact_staging` が指していないことを確認してから削除 |

一時ファイルを無条件に消さないのは、別ジョブが使用中の可能性があるため。
必ずジョブの所有権とリース状態、および `artifact_staging` の参照を確認する。

**回収の結果は起動時にログへ 1 行残す**（0 でない項目だけ、無ければ「齟齬なし」）。
破棄も再開もディスク上の実体を動かすので、記録が無いと消えた容量の説明が付かない。
孤立か自動で回収できない staging があるときは、続けて警告を出す。API から見えるのは
孤立の件数だけ（`GET /orphans`）。

### 9.7 結合グループの検出

`merge.enabled` なプロファイルの動画に対して実行する。同一録画と判定する条件は
現行スクリプトと同じ。

- 直前ファイルの終端（開始時刻 + duration）と次ファイルの開始時刻の差が
  `tolerance_seconds` 以内
- かつ直前ファイルのサイズが `min_part_size_gib` 以上

第 2 条件は、DJI が ~16GiB で自動分割することを利用して「分割」と「連続した別録画」を
区別するためのもの。閾値を下回るサイズのファイルの直後は別グループとして扱う。

**差の下限は 0 ではなく `captured_at` の分解能**（`filename` と `exif` は秒、それ以外は 0）。
分解能を**超える**重なりだけを別グループとする。`captured_at` が秒までしか無いのに
`duration` は小数なので、終端の推定は構造的に 1 秒ぶれる。0 で切ると、**同じ録画の継ぎ目が
丸めの符号で割れる**。境界はちょうど 1 分解能ぶんの重なりで、そこは別グループ ——
秒に丸めた 2 つの開始時刻の差は真の差から `(-1, +1)` の開区間ぶんしかずれないので、
ちょうど 1 秒の重なりは丸めでは作れない。**粗い方の分解能**を使う（誤差は粗い方が支配する）。

`duration` は公開時（§9.3 手順 5）に確定済みの `media_file.duration_seconds` を使う。
`probe_state = failed` のファイルはグループ境界として扱う。

検出結果は `merge_group` に `detected_by = auto` として保存する。`input_digest` が
同じアクティブグループが既にあれば作らない。ユーザが画面でグループを分割・結合した
場合は `detected_by = manual` になり、以後の自動検出で上書きされない。

### 9.8 結合と結合結果の検証

1. ffmpeg concat demuxer（`-f concat -safe 0 -c copy -fflags +genpts`）を試す。
   **data ストリームは `-map` に含めない** —— concat demuxer が運べず、
   `Cannot map stream #0:N - unsupported type` で即座に落ちる（実機の DJI は
   `tmcd` を持つので毎回これで TS へ落ちていた）。TS 経路も同じものを落とすので、
   最初から選ばなければ失うものは無い。落としたものは `route_dropped` に記録する。
2. 失敗したら TS 経由のフォールバック（各ファイルを `mpegts` に変換して
   `concat:` プロトコルで結合、`-bsf:a aac_adtstoasc`）。コーデックに応じて
   `h264_mp4toannexb` / `hevc_mp4toannexb` を選ぶ。
3. 出力は `work/<job-id>/` に書く。最終パスへ直接書かない。
4. 検証（下表）を行い、結果を `verification_json` に入れる。
5. **合格・不合格にかかわらず** §9.3 の公開プロトコルで `derived/` へ公開する。
   不合格の場合は `adopted_at = NULL` のままにし、既定の選択肢から外す。
   `work/` に残すとリース失効時の掃除で消えてしまうため、durable な場所へ出す。
6. 出力の mtime を録画終了時刻（最後のパートの開始時刻 + duration）に揃える。
   `captured_at` はオフセット付きなので、**その瞬間をそのまま** epoch にする
   （取り込みの mtime も真の瞬間なので、ここで UTC と読み替えると `library/` と
   `derived/` でずれる）。
7. 公開の直前にキャンセルトークンとジョブリースを再確認する。

#### 保持するストリームを明示的に決める

**ffmpeg の暗黙の選択に任せない。** 任せると「何が保持されたか」が出力を見るまで
分からず、誤ったストリームを選んだ出力を、その出力自身を基準に合格させてしまう。

プロファイルで保持対象を宣言し、`-map` で明示する。

```yaml
merge:
  keep_streams:
    video: primary      # 最初の映像ストリームのみ（埋め込みサムネイルを除く）
    audio: all
    timecode: true      # tmcd
    data: false         # djmd / dbgi などのデータトラック
```

concat demuxer でも TS フォールバックでも**同じ `-map` を使う**。経路によって
保持されるストリームが変わらないようにする。

#### 検証

| 検査 | 合格条件 |
| --- | --- |
| duration | 結合後 ≈ Σ パート（許容誤差 ±1 秒 × パート数） |
| ストリーム構成 | ffprobe がエラーを返さず、`keep_streams` で宣言した種別が揃い、**本数と codec が全パートで一致**している |
| 映像フレーム数 | Σ パート − 結合後 ≤ 2 × (パート数 − 1) + 2。継ぎ目で数フレーム落ちるのは正常 |
| サイズ | 下記の条件を満たすときのみ評価する |

**サイズを「Σ パートのファイルサイズ」と比較してはならない。** `-c copy` は
宣言したストリームだけを引き継ぐため、正常な結合でもファイルサイズは大きく減る。

期待サイズは **保持対象と宣言したストリームについて、各パートの
`bit_rate × duration` を合算**して求める。ただしこの検査は次を満たすときだけ適用する。

- 保持対象の全ストリームで `bit_rate` が取得できる（`N/A` が無い）
- 各パートの `bit_rate` のばらつきが小さく、平均値として信用できる

満たさない場合は **`inconclusive`（判定不能）** として記録し、合否には使わない。
`bit_rate` は codec やコンテナによって `N/A` になり、可変ビットレートでは丸めた
平均値でしかない。一律 ±2% を必須にすると、Canon や汎用 MP4 の正常な出力を
また一律で不合格にしてしまう。

| 経路 | 許容誤差 |
| --- | --- |
| concat demuxer | ±2%（DJI の実測で差 0.002%） |
| TS フォールバック | ±5%。mux のオーバーヘッドが通常経路と異なる |

`verification_json` には、**期待値の算出根拠**（どのストリームを対象にしたか、
各パートの `bit_rate` と `duration`）と、`inconclusive` の場合はその理由を残す。

実測では、DJI Osmo Pocket 4 の MP4 は 6 ストリームを持っていた。

| # | 種別 | タグ | ビットレート | 結合後 |
| --- | --- | --- | --- | --- |
| 0 | video hevc | `hvc1` | 79.9 Mbps | 保持 |
| 1 | audio aac | `mp4a` | 317 kbps | 保持 |
| 2 | data | `djmd`（DJI メタデータ） | 11.3 kbps | 脱落 |
| 3 | data | `dbgi`（DJI ジャイロ/デバッグ） | **10.3 Mbps** | 脱落 |
| 4 | data | `tmcd`（タイムコード） | — | 保持 |
| 5 | video mjpeg | サムネイル | — | 脱落 |

16GiB × 2 の結合で **11.4% 小さくなった**（34,373,429,887 B → 30,452,085,873 B）。
`dbgi` の 10.3 Mbps × 3036 秒 ≒ 3.91 GB がその大半である。許容誤差 1% の
サイズ比較では、**正常な結合がすべて不合格になる**。

保持されたストリームのビットレートから期待値を出せば厳しく検査できる。
上の例では (79,924,667 + 317,374) bit/s × 3036 秒 ÷ 8 = 30,451,573,000 B に対し
実測 30,452,085,873 B で差 0.002% だった。

判定に使う実値:

- 映像フレーム数は ffprobe の `nb_frames` だけを見る。`-count_frames` は
  30 GiB を全デコードするので使わない。取れないパートが 1 つでもあれば
  `inconclusive` とする
- 「`bit_rate` のばらつきが小さい」は `(max - min) / mean ≤ 0.1`。分散ではなく
  範囲で見るのは、パートが 2 本のときにも意味を持たせるため。**対応する
  ストリームごとに評価する**（合計で見ると、支配的な映像が音声の変動を隠す）
- 期待サイズは **`bit_rate` が取れた保持ストリームだけ**で組み立てる。取れなかった
  のが映像・音声なら `inconclusive`、data（`tmcd` など）なら推定から外して続ける。
  外したストリームは `verification_json` に残す。`tmcd` を理由に全体を
  `inconclusive` にすると、既定の DJI プロファイルでサイズ検査が常に無効になる
- 検証器の版は `verification_json.pipeline_version` に記録する。**`input_digest`
  には入れない**（入力の同一性の判定であって、検証器の同一性ではない。混ぜると
  閾値を変えるたびに既存の派生物が選択肢から消える）
- TS 経路は `mpegts` が運べないストリーム（QuickTime の data track）を外して
  結合する。外したものは `verification_json.route_dropped_streams` に残る。
  ストリーム検査は不合格になるが、出力は公開されるのでユーザが目視して採用できる
- 期待サイズの比較は**コンテナのオーバーヘッドを含まない**。16 GiB 級の実ファイル
  では 0.002%（実測）だが、数百 KB の合成クリップでは 7〜8% に達する。
  許容誤差 2% は実機の大きさを前提とした値である

**データトラックの脱落は許容する。** Immich は `dbgi` を使わないので視聴上の
損失はなく、転送量が 11% 減る利点がある。Gyroflow などで後から手ブレ補正を
かけたい場合は `library/` のオリジナルを使う（オリジナルは常に保持される）。
脱落したストリームは `verification_json` に記録し、画面にも表示してユーザが
把握できるようにする。

パートの継ぎ目にあたる時刻のサムネイルを生成し、ユーザが「継ぎ目が破綻していないか」を
目視できるようにする。ユーザは破棄・再試行・「そのまま採用」を選べる。
採用した場合のみ `adopted_at` が入り、アップロード対象になる。

出力ファイル名が既存の別グループと衝突する場合は §9.3 の衝突規則が働き、
`input_digest` 由来ではなく SHA-1 由来の決定的な別名が付く。

### 9.9 ジョブ

| ジョブ種別 | 内容 |
| --- | --- |
| `scan` | ボリュームのスキャン |
| `import` | スキャン結果のファイルをライブラリへ公開 |
| `detect_groups` | 結合グループの検出 |
| `merge` | 1 グループの結合 |
| `upload` | 指定メディアのアップロード |
| `recompute_timestamps` | プロファイル変更後の撮影日時再計算 |
| `deep_verify` | 保存済み SHA-1 とソースのフルハッシュの突き合わせ（任意） |

**リース**: 実行中のジョブは `lease_token` と `lease_expires_at` を持ち、定期的に更新する。
ファイルを公開する直前にリースの有効性を確認する。起動時の掃除はリースが失効した
ジョブのみを対象にする。

**進捗**: `progress_json` に**取り込みも送信も同じ形**で書く —— どの段階か（`phase`。
`copy` / `upload` など。画面の言葉へ写すのは `JobProgress.tsx` の `PHASES` で、
**内部の名前をそのまま出さない**。§13）、いま扱っている相対パス（`rel_path`）、
件数（`file_index` / `file_count`）、1 件のバイト（`bytes_done` / `bytes_total`）、
ジョブ全体のバイト（`bytes_done_all` / `bytes_total_all`）。形を揃えるのは、画面側の
進捗の描き方を 1 つにするため（速度と残り時間も同じ計算で出る）。**合計は開始時の
スナップショット**なので走っている間に増減しうる。実測が合計を追い越したら**合計の
ほうを実測に合わせて伸ばす** —— `12 / 10 件` のような嘘を画面に出さない。**送信の進捗は
心拍の UPDATE に相乗りさせる**（書き込みを増やさない。`docs/decisions.md`）。

**失敗の決着**: ハンドラが例外を送出したら、ワーカーは `failed` にし、**続けて
`job_event` を 1 行書く**。合図を出さずに終わると進捗の配信（SSE）に何も流れず、画面は
終わったことを知る手段を持たない。**成功の経路の完了行はハンドラ自身が出す**ので、
**どちらの終わり方でも合図が 1 本は出る** —— これが §13 の「作業が終われば、押さなくても
表示が切り替わる」の土台になる。

- **書く順は「決着 → 合図」。** 合図が先だと、それを受けて `/jobs` を取り直した画面が
  まだ `running` の一覧を読む。**この順序の規則は失敗の経路のもの**で、成功の経路の完了行は
  `finish_claimed` より先に出る（一覧の見え方が 1 拍ぶん遅れるだけ）。**`busy` はどちらでも
  既に偽** —— ハンドラは自分の `finally` でボリュームを離してから戻る
- **決着はハンドラの接続を閉じてから、ワーカーの接続で付ける。** 落ちたハンドラは書き込み
  トランザクションを開いたままのことがあり、その中で書くと取りこぼしを拾う `ROLLBACK` が
  決着ごと巻き戻す
- **決着と合図は「どのジョブが落ちてもワーカーは生かす」`try` の内側に置く。** `finish` は
  `LeaseLost` を、`emit` は `BEGIN IMMEDIATE` の待ちきれを送出しうる。ここから上がると
  ワーカーのループを抜け、**HTTP は生きたままジョブだけが二度と走らなくなる**
- **理由は `job_event` に載せる。** 画面は `job.error` を出さないので、ここに書かなければ
  どこにも現れない（例外の文字列に秘密を混ぜない）

**再開**: 起動時に `status = running` のジョブを `interrupted` に倒し、
`artifact_staging` や `upload_record` のレコード単位の進捗から再開する。

**期限切れの回収**: 起動時だけでは足りない。決着（`_settle`）はハンドラが落ちた
後にも通る唯一の経路で、そこで `finish` が書けなかった行は `running` のまま残る。
残ると**リセットが `job_in_flight` で断られ続ける** —— 案内は「終わってから」だが、
終わる主体はもう居ない。そこでワーカーは、**`claim_next` が空を返したときにだけ**
`reap_expired_leases` を回す。掴めなかった瞬間はそのワーカーが 1 つも所有していない
ことが確定するので、走っている自分のジョブを誤って倒す経路が無い。**リース長ごとに
間引く**（poll は 0.5 秒ごとで、毎周回だと空振りの UPDATE が書き込みロックを取り
続ける）。

**キャンセル**: 協調的キャンセル。

1. `job.status = cancelling` にする
2. コピー・アップロードは chunk 境界をキャンセルポイントとする
3. 外部プロセスには SIGTERM を送り、猶予後に SIGKILL、`wait` で刈り取る。
   プロセスグループ単位で送り、子プロセスを取り残さない
4. 中間生成物を掃除し、dirfd を `finally` で解放する
5. `job.status = cancelled` にする

「キャンセル済みと表示した後に公開される」ことを防ぐため、公開処理の直前に
必ずキャンセルトークンを再確認する。

### 9.10 Immich へのアップロード

`upload_record.state` の状態機械。各段階は冪等で、どこで落ちても再開できる。

```
pending → checking → uploading → asset_known → tagging → fixing_datetime → complete
   ↑              ↘ (既存) ────────↗                  ↘
   │                                        awaiting_datetime_approval
   └── needs_recheck ←── 中断・キャンセル（サーバ側の成否が不明）

   いずれの段階からも → failed（リトライ上限到達時）
```

| 状態 | 意味 | 終端か |
| --- | --- | --- |
| `pending` | 選択済みで未着手 | — |
| `checking` 〜 `fixing_datetime` | 進行中 | — |
| `awaiting_datetime_approval` | 日時補正にユーザの承認が要る（§9.10 の `origin`。**現在値が提案と違う瞬間のときだけ**） | **ジョブにとっては終端**。レコードは承認待ち |
| `complete` | 完了 | 終端 |
| `failed` | リトライ上限に達した | 終端（明示再試行で `pending` へ戻せる） |
| `needs_recheck` | 中断・キャンセルでサーバ側の成否が不明。次回 `checking` から照合し直す | — |

**ジョブは、担当する全レコードが `complete` / `failed` / `awaiting_datetime_approval`
のいずれかに達した時点で終了する。** 承認待ちのレコードがあってもジョブは
進行中のままにしない。承認は別の操作として扱う。

**送信は宛先ごとに 1 本のジョブで、レコードを 1 件ずつ直列に処理する。** 並列化は
しない。1 つの Immich に同時に投げる本数を増やしても律速はネットワークと
サーバ側の取り込みで、こちらが増やせるのは失敗の同時多発だけになる。直列なら、
キャンセルの観測点も「次の 1 件に入る前」だけで済む。

**preflight（§10）は「その宛先のリビジョンで最初の 1 件を送る前に 1 回」。**
成功は 15 分間だけ憶える。失敗はリビジョンが変わるまで憶え続ける —— 向き先が
違うまま何度も試すと、間違った Immich に少しずつ資産が積み上がる。

1. **checking**: 対象の `sha1` を集めて `POST /api/assets/bulk-upload-check` に送る。
   既存と判定されたら `remote_asset_id` を得て `asset_known` へ進む。

   応答は `action: accept`、または `action: reject, reason: "duplicate",
   assetId: <id>, isTrashed: <bool>`。**`isTrashed` を無視してはならない。**
   Immich はゴミ箱を持ち（既定 30 日）、ユーザが削除した資産も重複として
   再アップロードを弾く。`isTrashed: true` の場合は
   `upload_record.remote_is_trashed` に記録し、「Immich 側でゴミ箱に入っている」
   と画面に出す。ゴミ箱の保持期間を過ぎると資産は消えるので、
   **アップロード済みとして扱いつつ、ユーザが気づける状態にしておく**。
   利用者が意図的に消したものを黙って上げ直すことはしない。
2. **uploading**: `POST /api/assets` へ multipart で送る。ファイルは
   **ストリーミングで送り**、メモリに載せない。`x-immich-checksum` ヘッダを付ける。
   `deviceAssetId` に `mediaferry:<media_file_id>` を設定する。

   **チェックサムの encoding は base64 に統一する。** 実測では
   `bulk-upload-check` が hex と base64 の両方を受理したが、
   `x-immich-checksum` ヘッダは base64 で成功した。片方に揃えないと
   取り違えが起きるので、両方とも base64 で送る。
3. **asset_known**: `remote_asset_id` を記録して commit する。ここで初めて
   「サーバ側に存在する」ことが確定する。
4. **tagging**: `immich.tags` を付与する。タグが無ければ作成する。タグ付けは
   追加操作のみで、既存タグを消さない。**`origin` が `created_by_us` でない場合
   （`pre_existing` / `unknown`）は `tag_pre_existing` の設定に従う。**
   自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めたタグを
   付けてしまわないようにする。
5. **fixing_datetime**: 下記の条件を満たす場合のみ実行する。
6. **complete**。

### 重複アセットの扱い

チェックサムの一致には 2 つの異なる原因があり、**区別せずに後処理を適用しては
ならない**。

**実測で、Immich v3.1.0 の `GET /api/assets/{id}` は `deviceAssetId` を
返さないことが分かった。** クライアント由来で残るのは `originalFileName` だけで、
これは同じ元ファイルなら別経路のアップロードでも一致するので判別に使えない。

代わりに次の 2 つを使う。どちらも実在を確認済み。

1. **`POST /api/assets` の応答に含まれる `status`**（`created` / `duplicate`）。
   `created` が返れば、その資産は自分が作ったものだと確定する。
2. **初回 `checking` の結果**（`upload_record.first_check_result`）。
   `bulk-upload-check` は `action: accept` / `action: reject, reason: duplicate,
   assetId: <id>` を返す。**初回が `reject` なら「以前から存在した」ことを証明できる。**
   逆に初回が `accept` だったことは自作の証明にはならない（チェックとアップロードの
   間に別のクライアントが割り込みうる）。

| origin | 判定方法 | タグ | 撮影日時の補正 |
| --- | --- | --- | --- |
| `created_by_us` | **`POST /api/assets` が `status: created` を返し、その事実をローカルに commit できた場合だけ** | 付与する | **自動で行う** |
| `pre_existing` | **初回 `checking` が即 `reject`** だった | `tag_pre_existing` が真なら付与（追加のみ） | **自動では行わない**。現在値を読み、**提案と違う瞬間なら** `awaiting_datetime_approval` へ進み、差分を画面に出して明示承認を求める |
| `unknown` | 上のいずれでもない | **`pre_existing` と同じ**（`tag_pre_existing` に従う） | **自動では行わない**。`pre_existing` と同じ扱い |

**観測した現在値が提案と同じ瞬間なら、承認を待たずに `complete` にする。** 承認が
守っているのは「**他人が上げた写真を勝手に書き換えない**」ことなので、**書き換え
ないなら守るものが無い**。**外部への副作用を起こさない**ので、§9.10 の「明示操作
でしか起こさない」にも触れない。現在値は、承認の画面に出す差分のために**どのみち
その場で 1 度読んでいる**（`jobs/uploader.py` の `_observed_datetime`）ので、
新しい問い合わせは増えない。

**読めなかった現在値を「同じ」にしない。** 相手が答えられなければ「分からない」で
あって「変更なし」ではないので、承認待ちへ倒す。ここを取り違えると、承認を飛ばして
黙って終わらせることになる。

**数えるときに `identical` を除くだけの案は採らない。** 承認待ちの行が画面のどこ
からも見えなくなり、「**画面から呼べない API は、機能が無いのと同じ**」と同じ形の
穴になる（§13）。

**同じ瞬間かどうかの判定は 1 か所に置く**（`core/uploads/decisions.py` の
`same_instant`）。状態機械の判断と、画面に出す `identical`（§11 の `GET /uploads`）が
同じ関数を呼ぶ。2 本に分けると、片方だけ直したときに画面と状態機械が食い違う。

**差分は瞬間で比べ、同じオフセットに直して並べる。** Immich は日時を **UTC へ
正規化して返す**ので、`+09:00` で書いた値は `+00:00` の表記で戻る。文字列の一致で
見ると**同じ瞬間が常に「違う」**になり、「変更なし」が真になる場面が無くなる
（実機で、リセット後に送り直した資産が全部この確認に並んだ。2026-08-28）。画面は
文字列から壁時計を切り出すだけなので、片方が UTC のままだと同じ瞬間が 9 時間
ずれた 2 つの時刻として並ぶ。**観測値は提案と同じオフセットへ直してから返す**
（瞬間は変えない）。**オフセットの無い値・読めない値は、比べも直しもしない** ——
どの地の時刻か決まらないものに現地時刻を補うと、承認を黙って飛ばす方へ倒れる。

`unknown` になるのは次のような場合。

- 初回 `checking` が `accept` だったのに、自分の `POST` が `duplicate` を返した
- `POST` の応答を受け取る前に落ちた、または応答を commit する前に落ちた
- `uploading` の途中でキャンセルした

**「初回が `accept` だったから自分のものだ」とは判定しない。** チェックとアップロードの
間に別のクライアントが同じファイルを上げた可能性を否定できないためである。その資産に
ユーザが手動で時刻を直していたら、自動補正はそれを壊す。証明できないものは
`unknown` に倒し、承認を求める。

`first_check_result` は**不変**とする。`pre_existing` の安全判定には使えるが、
自作であることの証明には使えない。

`pre_existing` のアセットは、別経路で既にアップロードされ、ユーザが手動で時刻を
修正済みかもしれない。そこへ `force_offset` を無条件に適用すると、
**ユーザの既存資産を意図せず書き換える**。

`deviceAssetId` は実測で資産応答から読み戻せないことが分かっており、
判別には使えない。Immich 側の重複管理に使われる可能性があるのでアップロード時には
引き続き送るが、**読み戻して判定に使うことはしない**。

### 「サーバは成功、ローカル未記録」の窓

`uploading` のまま再起動・キャンセルした場合は `needs_recheck` に落とし、
再開時に必ず `checking` からやり直す。チェックサム照合で既存が見つかれば
`asset_known` へ進むため、**二重アップロードにはならない**。

ただし**その資産が自分のものである証明は無い**。`origin` は `unknown` のままとし、
撮影日時の補正は自動で行わず `awaiting_datetime_approval` へ進む。タグの付与は
追加操作なので実行してよい。

### ゴミ箱と消滅の追跡

`remote_is_trashed` は `checking` 時点のスナップショットにすぎない。`complete` に
なったレコードは通常再照合されないので、ゴミ箱の保持期限を過ぎて資産が消えても
「送信済み」のまま残る。

**自動で再アップロードはしない**（利用者が意図的に消したものを黙って戻さない）。
代わりに、宛先ごとの「**状態を再確認**」操作を用意する。現行 epoch の `complete` の
レコードに `bulk-upload-check` を掛け直し、結果ごとに次を行う。

- **資産が在る**（`reject` が返る）: `remote_asset_id` / `remote_is_trashed` /
  `remote_checked_at` を書き直す。ゴミ箱から復元されていれば `remote_is_trashed` は
  false に戻る
- **資産が消えている**（`accept` が返る）: その記録を `invalidated_at` と
  `invalidated_reason = 'remote_missing'` で**無効化する**。無効化された記録は
  「この宛先の有効な記録」ではなくなるので、そのメディアは通常の「まだ送っていない」へ
  戻り、ふつうの送る動線から送れる
- **ゴミ箱（`remote_is_trashed`）は無効化しない。** ゴミ箱に在ることは「無い」ことの
  証明ではない。判定の根拠は `bulk-upload-check` が `accept` を返したことだけ
- **スタックの照合**（§9.11）: 資産の照合が済んでから、`stacked` と記録している組が
  相手にまだ在るか、メンバー集合が一致するかを確かめ、解けている／崩れている組を
  未評価へ戻す

**観測と無効化は同じトランザクションで書く。** 分けると「消えたと記録したが未送信に
戻っていない」中途半端な状態が残る。**照合したときの行にしか書かない**（`stamp_many` の
CAS。§8）ので、照合の最中に動いた行は無効化もされない。

**送り直し専用の状態遷移は持たない。** 無効化して通常経路へ戻すだけで、**送信そのものは
利用者の明示操作のまま**である（`decisions.md`）。副産物として、次に送るのは新しい記録に
なるので `origin` の判定も最初からになる —— 資産が本当に消えていれば初回 `checking` が
`accept`、`POST /api/assets` が `created` を返して `created_by_us` に決まり、日時の補正が
自動で通る。

**`presence` の `gone`（「Immich にはもうありません」）は、原理的に出ない。** 消滅と
判定された記録はその場で無効化され、宛先ごとの状況（§13）は無効な記録を数えないので、
そのメディアは「まだ送っていません」として出る。**語彙としては残す** —— 起きないはずの
ことが起きたときに、生の enum ではなく本当のことを言うための保険であり、**出たら
どこかが壊れている。**

### 承認待ちの解消

`awaiting_datetime_approval` には**承認と却下の両方**を用意する。

| 操作 | 結果 |
| --- | --- |
| 承認 | `fixing_datetime` へ進み、`dateTimeOriginal` を書き戻してから `complete` |
| 却下 | **リモートを一切変更せずに `complete`** |

却下が無いと、既に正しい日時が入っている資産について「補正不要」と判断しても
承認待ちを消せず、一覧に残り続ける。

**変更が無い行では、決断を迫らない。** §9.10 の通り、現在値が提案と同じ瞬間の
レコードは承認待ちにならないので、この一覧に並ぶのは**古い DB に残っている行**か、
**承認を待っている間に相手が日時を変えた行**だけである。それでも画面の分岐は消さ
ない。並んだときは「変更なし」を**ボタンと同じ重さで**出し、片付ける操作の名前を
「**片付ける**」にする（「却下」は変更を拒む意味で、変えるものが無い場面の語彙では
ない）。**叩く先は却下と同じ**で、リモートには触らない。変更のある行が 1 つも無い
一覧では、見出しの「書き換えていいかどうかを決めてください」も出さない。

プロファイルの `timezone_policy` が `none` で補正案そのものが無い場合は、
承認を経ずに `complete` にする。

失敗は指数バックオフで最大 `upload_max_attempts` 回まで再試行する。上限に達したら
記録して次のファイルへ進む（1 件の失敗で全体を止めない）。

**並列度の設定は置かない。** 送信は宛先ごとに 1 本のジョブで 1 件ずつ直列に進める
（上記）。ワーカーを多重化しても増やせるのは失敗の同時多発であり、律速は
ネットワークと相手側の取り込みである。設定だけを残すと「効かない設定」が画面に
並ぶので、`UPLOAD_CONCURRENCY` は持たない。

Immich の API は破壊的変更が入りうるため、エンドポイントとフィールド名は
対象バージョンの OpenAPI 定義から確定してあり、対応バージョンは README に明記する。

### 9.11 RAW / JPEG のスタッキング

RAW+JPEG の組（§6 の `stack`）を、アップロードの後に Immich のスタックとして束ねる。
**アップロードの状態機械には状態を足さない。** スタックは「両方が送り終わって初めて
成立する」操作で、`upload_record` は (メディア × 宛先) の粒度しか持たないため、
**同じジョブの第 2 パス**として回す。`mode` が `send` / `recheck` / `approve` の
いずれでも走らせる（承認で `complete` になった行も対象になる）。

抽出は**その宛先の現行 `target_epoch`** で、`state = 'complete'` かつ
`stack_state IS NULL` の行を batch で区切って回す。**このジョブで新しく完了した行
だけが自然に残る**ので、ライブラリ全体を舐めない。1 件ごとにキャンセルを観測する。

**`target_epoch` を条件から外してはならない。** 向き先を変えた宛先では、旧 epoch の
`complete` が監査履歴として**無効化されずに残る**（§8。`_invalidate_old_epoch_locked`
は `state <> 'complete'` だけを無効化する）。epoch で絞らないと、**別ライブラリへ送った
資産 ID を現行の資格情報で送る**ことになり、UUID がたまたま存在すれば他人の資産を
束ねる。再確認（`records_for_recheck`）が同じ理由で epoch を条件にしているのと同じ
形にそろえる。

組が成立しない場合も**見送りとして決着させる**（未評価のまま残さない。残すと毎回
全件を舐めることになる）。見送りの理由は画面に出す。

| 事象 | 記録 |
| --- | --- |
| `stack.enabled` が偽／自分の拡張子が `extensions` に無い | `skipped`（対象外） |
| 相方が居ない／まだ `complete` でない／無効化済み | `skipped`（後から相方が完了したとき、**相方の側から `stacked` へ更新される**） |
| **同席の証拠が無い**（`copresent_key` が `NULL`、または鍵ごとに一致しない） | `skipped`。相方候補ですらないので「相方が見つからない」として決着する |
| **同じ拡張子の相方が 2 つ以上ある** | `skipped`。「同じ拡張子の相方が複数ある。自動では決められない」。**送信そのものは止めない** |
| どちらかの `origin` が `created_by_us` でない | `skipped`。**自分が上げたと証明できない資産は束ねない** |

`origin` の条件は §9.10 のタグ付けより厳しくしている。`POST /stacks` は
「渡した資産が既存スタックの primary なら、その既存スタックを吸収する」ため、
タグ（追加のみ）と違って**利用者が手で作った組を作り直しうる**。

**身元の規則は 1 つだが、実装は 2 つある。** 第 2 パスは `identity_partners`
（Python）で判定し、一覧の `collapse=stack`（§12）は**同じ規則を SQL で書き直した**
`_secondary_exists_sql` / `_ambiguous_exists_sql` / `_members_of` で判定する。一覧は
`identity_partners` を呼ばない —— 一覧は「畳むかどうか」を 1 本のクエリで決める必要が
あり、行ごとに Python の判定を呼ぶと組がページの境目をまたぐため（§6 の「難所」）。

**したがって、二重実装がずれたら不具合である。** 実際に、SQL 側が曖昧さを同席グループ
単位で数え、`identity_partners` が主の全観測にまたがって数えていたずれが見つかった
（`docs/history/phase10-record.md`）。**両者の結論が一致することを突き合わせるテストを
置く。**

身元（カード上の事実）と資格（`origin`・`state`・資産 ID・プロファイル）の分け方は
共通で、資格を見るのは第 2 パスだけである。

**組の規則は、そのメディアのプロファイルの現行リビジョンから読む。** 取り込みに
使った版ではない —— スタックは取り込みの記録ではなく「いま適用する操作」で、
`immich.tags` と `fix_datetime_after_upload` が現行リビジョンを見ているのと同じ層に
ある（`ProfileRegistry.by_id` は `current_revision_id` を join する）。**組の全員に
同じ `profile_id` を要求する**ので、規則は組ごとに 1 つに決まり、「どの member から
評価しても同じ組になる」が版を跨いでも成立する。それ以前に取り込んだメディアも、
プロファイルが `stack` を持つ版へ進んだ時点で対象になる。

**「現行版を使う」には再評価の経路が要る。** 一度見送りとして決着した行は未評価へ
戻らないので、規則を有効にしても、拡張子や許容差を変えても、その行は二度と
評価されない。**プロファイルの新しいリビジョンを作るトランザクションで、その
プロファイルのメディアに紐づく見送りを未評価へ戻す**（`stacked` は戻さない）。
`sync_builtins` がビルトインの版を進める経路も同じ扱いにする。再計算が
`captured_at` を動かしたときに戻すのと、同じ形である。

**戻すのは `stack` 節が変わったときだけにする。** 名前やタグだけの編集で全件を
再評価すると、見送りの理由が「相手側に別のスタックがある」「相手が受け付けない」の
行まで、もう一度リモートへ問い合わせ直すことになる。

**外部へ触る手前の作法は §9.10 と同じにする。** ただし `complete` のレコードは claim を
持たないので、`prepare_side_effect` は流用できない。スタック用の guard を置き、
**1 つのトランザクションで次をすべて照合する**。

- ジョブのリース
- 組の全員の `(target_epoch, state, remote_asset_id, invalidated_at, stack_state)`
- **宛先の現行リビジョンの `target_epoch` が、開始時に固定した epoch と同じであること**
- **そのプロファイルの `current_revision_id` が、組を決めたときの版と同じであること**

後ろの 2 つが要る理由は、**進行中のレコードを無効化する既存の停止境界から、
スタックだけが外れている**ためである。epoch を進める編集は `state <> 'complete'` の
行しか無効化しないので（§8）、`complete` を扱う第 2 パスは「開始後に別ライブラリへ
向き替えられた」ことに気づけない。preflight も固定した旧リビジョンを検査するので、
旧向き先が生きていれば成功してしまう。プロファイルの編集も同期の API で行えるので、
組を決めた後・送る前に規則が変わりうる。

`_guard` と同じく **prepare → preflight（`with_lease_pulse` で囲む）→ prepare** の
2 段にし、**`POST` と `PUT` のそれぞれの直前で取り直す**。

**相手待ちはすべて `with_lease_pulse` で囲む。** preflight だけでは足りない ——
`UPLOAD_TIMEOUT_SECONDS` は既定 86400 秒なので、資産の読み取りや `POST` が
60 秒のリースを跨ぐのは正常な動作である。囲まないと、送信が成功した直後の記録で
リースを失い、正常なジョブが失敗になる。

**ローカルで決着する見送りも、リースの下で書く。** 相手に触らない見送り（規則が
無効、観測が無い、相方が居ない）が大量にあると、書いている間にリースが切れうる。
記録は `assert_lease` + 厳密な CAS を同じトランザクションで行い、行をまたいだ
経過時間でも heartbeat を打つ（§9.6 の再計算と同じ 2 つの仕掛け）。

結果の記録も**同じトランザクションで、guard と同じものを確認し、全員に厳密な CAS を
当てる。1 件でも条件に合わなければ 1 行も書かない**（一部だけ `stacked` になると、
残りは別の組として再評価される）。CAS には**送った相手（`remote_asset_id`）**も含める。

**相手に触らない見送りも、記録の条件は同じにする。** 規則が無効・観測が無い・相方が
居ない、といった見送りは guard を通らないが、条件を緩めると次の順序で**旧規則の判断が
新しい版の世界へ残る**。

1. 旧版 R1 の規則で「見送り」と判断する
2. 別の接続が R2 を発行し、既存の見送りを未評価へ戻して commit（この行はまだ未評価
   なので対象外）
3. R1 の判断を書く → **R2 では二度と評価されない**

書けなかった場合（CAS 不一致）は成功として数えない。外部への副作用が済んだ後で
CAS に落ちたときは、**DB を書かずに次の送信の「既存スタックの回収」へ渡す。**

**組んだ後で相手側だけが変わることがある。** 再確認（§9.10）は資産の有無だけでなく、
`stacked` と記録している組そのものも照合する。`GET /api/stacks` を**絞り込み無しで**
1 回だけ引き、`remote_stack_id` → その組の `remote_asset_id` の集合と突き合わせる。
これを持たないと、Immich 側でスタックだけ解除されても気づかず、**もう存在しない
`remote_stack_id` が残って設定 › 送り先の「N 組」が嘘になる**。

| 再確認で見た相手の姿 | すること |
| --- | --- |
| 同じ `stack_id` が無い | **解けている。** その組の行を全部未評価へ戻す |
| 在るが、メンバー集合が我々の組と一致しない | **崩れている。** 同じく未評価へ戻す |
| 一致 | **触らない**（`updated_at` も動かさない） |

- **`stacked` の行が 0 組なら `GET /api/stacks` を呼ばない**（空振りの要求を出さない）
- **資産の照合（`stamp_many`）の後に走らせる。** 消滅した資産は `_reopen_stack_of` が
  既に組を開いており、開いた行は `stack_state IS NULL` になるのでこの段の対象から
  自然に外れる。逆順だと同じ組を 2 度開く
- **戻す UPDATE は `assert_lease` と同じ 1 つのトランザクション**で、`target_epoch` と
  `remote_stack_id` と `stack_state = 'stacked'` を条件にした CAS で当てる
- **対象を `state` で絞らない。** `stacked` は「その `remote_asset_id` を送った結果」で、
  レコードの `state` とは独立である（`decisions.md`）。`complete` に絞ると、
  片方が `needs_recheck` へ差し戻された組はこちらの集合が相手の集合の真部分集合に
  なるので**毎回「崩れている」と読み**、続く第 2 パスがそれを `skipped` に落とす ——
  Immich では組んだままなのに、画面が「見送り」と言う

戻した組は同じジョブの第 2 パスが拾い、**相手の姿を読み直してから**決める（下の表）。
全員が `stack: null`（解除された）なら `POST /stacks` で**組み直し**、誰かが別のスタックに
入っていれば集合が一致しないので `skipped` になる。**ループしない** —— 組み直した組は
次の再確認で一致するので触らず、`skipped` に落ちた行は `stacked` ではないので、この段の
対象に入らない。

**利用者が Immich で手で解除した組も、これで作り直される。** 承知のうえでそちらへ倒した
（利用者の判断）—— MF が組むべきと判断した組は常に Immich へ反映させ、表示と実体が
食い違ったまま残る方を避ける。**この緊張は `decisions.md` に中身ごと残してある。**
組みたくない組がある場合の逃げ道は、カメラの種類の `stack` 節を切ること
（プロファイル単位）で、**1 組だけ外す手段は無い。**

相手の現在の姿は送る直前に読み直す（`AssetResponseDto.stack`）。**キーが無いのも `null` も「スタックに入っていない」として扱い、形が違うものは protocol error にする** —— 黙って「入っていない」と読むと、スタック済みの資産を作り直す。

| 相手の状態 | すること |
| --- | --- |
| 全員が `stack: null` | `POST /stacks {assetIds}` |
| 既存スタックがあり、**メンバー集合が我々の組と一致** | 作らない。`stacked` と `remote_stack_id` を記録する（**中断からの回収経路**） |
| 既存スタックがあり、集合が一致しない | **触らない。** `skipped` に理由を残す |

`AssetResponseDto.stack` は `{id, primaryAssetId, assetCount}` しか返さないので、集合の
一致は `GET /stacks?primaryAssetId=` でメンバーを引いて確かめる。既存スタックの
primary が我々の組の外にある場合は引く手がかりが無いので、その時点で「一致しない」
として触らない。

**吸収は実測で確かめた**（2026-08-20、実 Immich v3.1.0）。A と B のスタックがある
ところへ `POST /stacks {B, C}` を送ると、**応答は {A, B, C} の 3 枚**になる
（旧スタックの相方 A まで畳み込まれる）。仕様の文言どおりだった。

**そのとき、こちらのクライアントは応答を確定させない。** 要求した集合と全単射で
なければ protocol error にするので（§9.11 の fail-closed）、吸収された結果を
`remote_stack_id` として保存することはない。**吸収は起こりうるが、記録は残さない。**

**この「触らない」は保証ではなく、窓を最小にした最善である。** Immich には条件付きの
作成が無く、`POST /stacks` は無条件に吸収する。読み直してから `POST` するまでの間に
利用者や別のクライアントが同じ資産でスタックを作れば、それを吸収してしまう。
`POST` の直前まで読み直しを遅らせても窓は閉じない —— **これは実装で消せる競合ではない。**
残余の競合として受け入れ、次の 2 つで影響を抑える。

- 読み直しは `POST` の直前に置く（窓を最小にする）
- 送るのは**両方が `created_by_us`** の組だけ（利用者が別途 primary にしている資産は、
  こちらが送る集合に入りにくい）

Immich が条件付きの作成か、既存スタックを吸収しない作成を提供したら、そちらへ移す。

primary は `POST` の応答を見て、`extensions` の先頭側でなければ `PUT /stacks/{id}` で
直す。**既に望みどおりなら `PUT` を打たない**（相手を無駄に変えない）。どれが primary に
なるかは相手の仕様に書かれていないので、確認そのものは常に行う。

**中断からの回収に新しい状態は要らない。** 送信中や直後に落ちれば行は未評価のまま
残り、次の送信で「集合が一致」の経路が回収する。**回収の経路でも primary を検査する。**
`POST` の直後・`PUT` の前に落ちると、相手が選んだ primary（RAW かもしれない）のまま
集合だけが一致するので、検査しないと**望みの primary へ二度と直らない**。

失敗の扱いは 2 つに分ける。**4xx と応答の形の違反は理由つきの見送り**にする
（再試行しても直らない）。**接続不能・5xx・認証失敗・redirect は未評価のまま残し、
その時点で第 2 パスを打ち切る。** これらは組ではなく**宛先の障害**なので、次の組へ
進むと、失効した鍵や停止したサーバへ未評価の全件ぶんの要求を投げ続けることになる
（`UPLOAD_TIMEOUT_SECONDS` は既定 86400 秒なので、事実上終わらなくなる）。残りは
未評価のまま次の送信へ渡す。

いずれの場合も送信そのものは失敗にしない —— スタックはアップロードの後始末であって、
成否をアップロードに巻き込む理由がない。

## 10. アップロードの対象と宛先

**自動でアップロードされるものは無い。** 実行のたびに、ユーザが
「どのメディアを」「どの宛先へ」送るかを明示的に選ぶ。同時に複数の宛先を
選べる（`upload_record` の同一性は `(destination_id, target_epoch, media_file_id)` で、
**有効な記録**がその組ごとに高々 1 つなので、1 つのメディアが複数の宛先に対して独立した
状態を持てる。§8）。

`upload/` ディレクトリが暗黙に担っていた「何を送るか」の判断は、
**選択肢の提示規則**として明示的に定義する。UI・API・ワーカーはすべて
この同じ定義を使う。

### 既定で選択肢に出すもの

`media_file` M が既定で一覧に現れるのは次をすべて満たすとき。

1. `M.missing_at IS NULL`
2. 次のいずれか
   - `role = original` かつ、M がアクティブな結合グループの member **でない**
   - `role = derived` かつ、生成元グループ G について次をすべて満たす
     - `G.superseded_by_id IS NULL`
     - `G.status = merged`
     - `G.output_media_file_id = M.id`
     - `G.input_digest` が現在の構成・設定・プロファイルリビジョンから再計算した値と一致
     - 検証合格 または `G.adopted_at IS NOT NULL`

`role = derived` の条件に `superseded_by_id IS NULL` と `input_digest` の一致を
含めるのは、**グループを手動編集した後に旧派生物が選択肢へ戻ってしまう**経路を
塞ぐためである。旧グループはまだ `status = merged` のままなので、これらの条件が
ないと候補に残ってしまう。

**件数の集計も同じ判断を返す。** `/dashboard` と `/media?status=unsent` は
`SelectionService` を通らず SQL の断片（`SENDABLE_CLAUSE`）で数えるが、そこでも
**グループのプロファイルリビジョンが現行であること**を見る。`input_digest` は
リビジョンを含むので、カメラの種類を保存して版が上がると `POST /uploads` は必ず
断る —— 数え続けると、ホームの「N 件をまだ送っていません」が押しても消せない
まま残る。

これにより、現行スクリプトが暗黙に持っていた「結合成功・失敗いずれの場合も
元パートは通常の候補から外す」という規則が明文化される。

### フィルタで出せるもの

次は既定では出さないが、ユーザがフィルタで表示して選べる。「選べない」のではなく
「うっかり選ばない」ようにするための区別である。

- `status = failed` のグループの member（結合できなかったので個別に上げる）。**破棄した
  グループの member はここではなく既定の一覧に戻る**（破棄は「このまとまりは
  無し」であって、ファイルを隠すことではない）
- 検証不合格でまだ採用していない derived（中身を確認した上で）

### 宛先ごとの状態

各メディアは宛先ごとに独立した状態を持つ。実体は §9.10 の `upload_record.state`
で、画面には次のようにまとめて出す。

| 表示 | `state` |
| --- | --- |
| 未送信 | レコードが無い |
| 積み済み | `pending`。既にキューに入っている（未送信＝「押せば送れるもの」とは区別する） |
| 要確認 | `needs_recheck`。リモートに存在する可能性があり、次回 `checking` で照合する |
| 進行中 | `checking` 〜 `fixing_datetime` |
| 承認待ち | `awaiting_datetime_approval` |
| 送信済み | `complete` |
| 失敗 | `failed` |
| 無効化 | `invalidated_at` が入っている |

**「宛先 D に未送信のもの」でフィルタでき、その結果を全選択できる。**
自動候補化をしなくても「前回の続きから未送信を全部送る」が 2 操作で済む。
宛先を新しく追加しても、既存メディアが勝手に「未アップロード N 件」として
警告に出ることはない。

### 述語は 3 層に分ける

「既定で見える」「明示的に選べる」「実行してよい」を混ぜると、実行時に正当な
明示選択を拒否したり、逆に古い派生物を送ったりする。3 層に分け、`upload_record`
に `selection_rule` として**選択を許可した根拠を不変で永続化**する。

**(a) 安全条件 — claim 時に必ず評価する。層に関係なく満たす必要がある**

- `media_file.missing_at IS NULL`
- `upload_record.invalidated_at IS NULL`
- `role = derived` なら、生成元グループが `superseded_by_id IS NULL` かつ
  `output_media_file_id = M.id` かつ `input_digest` が現行と一致
- 宛先が `enabled` かつ `archived_at IS NULL`
- claim 時のリビジョンの `target_epoch` がレコードの `target_epoch` と一致

**(b) 既定で選択肢に出す条件**（前節）

**(c) 選択の根拠ごとの条件 — `selection_rule` に応じて claim 時に評価する**

| `selection_rule` | 選べる場面 | **claim 時に評価する現在の条件** |
| --- | --- | --- |
| `default` | 既定の一覧から選んだ | (b) を満たす |
| `failed_group_member` | 結合に失敗したグループの member を個別に上げる | **今も**そのグループが `failed` で、M がその active な member |
| `adopted_derived` | 検証不合格の derived を中身を見て採用した | `adopted_at IS NOT NULL` かつ生成元グループと `output_media_file_id` / `input_digest` が整合 |

**未採用の derived を選ぶ操作は「採用」そのものとして扱う。** 同じ
トランザクション内で `adopted_at` を設定し、`selection_rule = 'adopted_derived'` で
enqueue する。

ここが前版の欠陥だった。条件を「**まだ採用していない** derived」のままにすると、
enqueue した瞬間に `adopted_at` が入るので**自分自身が条件を満たさなくなり、
実行時に必ず拒否される**。claim 時に評価するのは「選べる場面」ではなく
「**その根拠が今も成立しているか**」でなければならない。

claim では **(a) を必ず評価し、`selection_rule` に対応する現在の条件を評価する**。
再試行は根拠を変えないので、`failed` → `pending` の CAS を経た後も同じ条件が使われる。

**多重防御**: グループが supersede されたり `status` が変わったときは、そのグループに
紐づく未完了の `upload_record` を `invalidated_at` で無効化する。claim 時の再評価と
合わせて二重に防ぐ。

### 既存レコードがある場合の遷移

| 現在の状態 | 再選択したとき |
| --- | --- |
| `complete` | 何もしない（no-op）。結果に「送信済み」と表示 |
| 進行中（`checking` 〜 `fixing_datetime`） | 競合として拒否。二重に claim しない |
| `awaiting_datetime_approval` | 何もしない。承認は別操作 |
| `failed` | 明示的な再試行操作の CAS でのみ `pending` へ戻す。`selection_rule` は変えない |
| `needs_recheck` | `pending` と同じく claim できる |
| `invalidated_at` が入っている | **再利用しない。** 無いものとして扱い、新しい行を作る（部分 UNIQUE 索引が隣に並ぶことを許す。§8） |

### `POST /uploads` の意味論

要求は `media_ids × destination_ids` の直積を **pair 単位の作業項目**に展開する。

1. **先に一括で検証する** — 構文、全 ID の実在、宛先が `enabled` かつ検証済み。
   ここで落ちたら**何も作らずに全体を拒否**する
2. **pair の作成は 1 トランザクション**で行う
3. **実行・失敗・再試行は pair ごとに独立**。1 件の失敗が他を巻き込まない

応答は pair ごとの結果を明示する。

| 結果 | 意味 |
| --- | --- |
| `created` | 新しく `pending` を作った |
| `retry_queued` | `failed` から `pending` へ戻した |
| `already_complete` | 送信済み。何もしていない |
| `already_active` | 進行中。何もしていない |
| `awaiting_approval` | 承認待ち。何もしていない |
| `rejected` | 安全条件または選択条件を満たさない。理由を添える |

### 送信前に向き先を再確認する（preflight）

`destination_revision` に記録した `remote_user_id` は、**登録・編集の時点の**観測値に
すぎない。宛先を編集しなくても、DNS・リバースプロキシ・Immich 本体の差し替えで
同じ `base_url` の先が別のライブラリに変わりうる。比較しなければ guard は働かない。

**ジョブは、あるリビジョンの最初の pair を claim・送信する前に、そのリビジョンの
資格情報で `/api/users/me` を取り直し、スナップショットの `remote_user_id`
（将来 `server_instance_id` が取れるならそれも）と突き合わせる。**

- 一致しない、または取得できない → **そのリビジョンの全 pair を 1 バイトも送らずに
  停止**し、向き先の再確認をユーザに求める
- 同じリビジョンについては、1 つのジョブ内で preflight を 1 回共有してよい
- 編集時に「旧リビジョンで完走させる」と決めた claim 済みジョブにも、
  **その旧リビジョンに対して同じ preflight を行う**

### 選択の確定

- `upload_record.eligibility_reason` に「なぜ選ばれたか」を人間向けに記録する
  （機械判定は `selection_rule` が担う）
- claim は §8 の `BEGIN IMMEDIATE` + 条件付き UPDATE で行う。**SQLite に行ロックは無い**
- 外部への副作用の直前と結果の commit 時に、`claim_token` とジョブのリースの
  一致を再確認する
- 再評価で条件を満たさなくなったものは送らず、理由を記録して画面に出す

## 11. API

すべて `/api` 配下。認証が有効な場合は Cookie セッションで保護し、
状態を変える操作には Origin 検証と CSRF トークンを要求する。

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/devices` | 接続中デバイスとボリューム、プロファイル判定結果、確度、信頼状態。加えて **`pending_count`**（取り込む残りの件数。`importer` が取り込む対象と**同じ条件**を 1 つの定数から使って数える）・**`scanned_at`**（一度も数えていないカードを見分ける。§13。**スキャンが最後まで走ったときに `volume_instance` へ記録する事実**で、数えた結果からは導かない —— 一致するファイルが無いカードは `source_entry` を 1 行も作らないので、行から導くと**中身が空のカードが永久に「まだ数えていない」になる**）・**`busy`**（そのカードを掴んでいる作業があるか＝「抜いていいか」の答え） |
| POST | `/volumes/{id}/trust` | ボリュームインスタンスを信頼登録する |
| POST | `/volumes/{id}/scan` | スキャン |
| POST | `/volumes/{id}/import` | 取り込みジョブを開始 |
| POST | `/volumes/{id}/close` | dirfd を解放しアンマウントする |
| GET | `/media` | 一覧。`status` / `profile` / `kind` / `from` / `to` / `q` / `page` / `collapse` / `stack`。**並びは `captured_at DESC, rel_path DESC`** —— 同じ撮影日時の行は現実に起きる（カメラの時計が止まれば連続して起きる）ので、tie-break が決まっていないとページの境目で重複・欠落する。**`rel_path` は `UNIQUE` なので単独で足りる**。`id` は乱数なので、同じ撮影日時の並びに意味が出ない。`db/selection.py` の 3 つの断片（`_ORIGINALS` / `_DERIVED` / `_MEMBERS_OF_UNMERGED`）も同じ並びで揃える —— `limit` で切るので、順序が決まらないとどれが候補に入るかが実行ごとに変わる。`collapse=stack` は RAW+JPEG の組を 1 行に畳み、主の行に `stack.members`（`id` / `rel_path` / `size_bytes`）を添える。**曖昧な組は畳まない**（全員が別々の行として残り、`stack` は付かない）。`total` は畳んだ後の件数。**`stack=members` は隠さず、組に属する行すべて（主も従も）に同じ `stack` を添えるだけ** —— 行は減らず `total` も変わらない。**送る画面はこちらを使う**: `collapse=stack` だと未送信の従（CR2）が主の陰に隠れ、返った行をそのまま送る画面では**その 1 枚が送られない**（Immich でスタックも組まれない）。両方来たときは `collapse=stack` が勝つ（隠す）—— 片方だけを 400 にすると既存の呼び出し元が壊れる |
| GET | `/media/{id}` | 詳細。**`stack`** も返す（一覧と同じ `_members_of` を通す。組でなければ `null`、曖昧な組でも `null`） |
| GET | `/media/{id}/thumbnail` | サムネイル（`at` で秒指定。10 秒刻みに丸め、1 本あたり 32 枚まで） |
| GET | `/merge-groups` | 結合グループ一覧。**既定はいま操作できるものだけ**（`superseded_by_id IS NULL` かつ `status <> 'skipped'`）。履歴は `?status=skipped` で取る。**置き換えられた行は `status` を指定しても出さない**。各行に **`profile_changed`**（作ったときからカメラの種類の版が上がったか）を添える —— 上がっていると `group_is_current` が必ず断るので、画面は採用のボタンを出さない |
| POST | `/merge-groups/detect` | 結合グループの検出ジョブを開始（プロファイルごとに 1 本） |
| POST | `/merge-groups/preview` | 閾値を変えたときの候補を再計算（保存しない） |
| POST | `/merge-groups` | 手動でグループを作成。**2 件以上**（1 件の組にはつなぎ目が無い） |
| PATCH | `/merge-groups/{id}` | 構成変更 / skip / 検証不合格の採用。**構成を変えない組み直しが「やり直し」**（結合の実装を直した後に作り直す）。構成変更も**作成と同じく 2 件以上** |
| DELETE | `/merge-groups/{id}` | 破棄の記録を消す。**`skipped` で結合結果を持たないものだけ**。消すと同じ組み合わせが再び検出されうる |
| GET | `/media/stale-derived` | もう使われていない派生物の一覧。**条件は下の DELETE の前提そのもの**（押しても断られるボタンを並べない）。**`/media/{id}` より前に置く** —— 後ろだと id として飲まれる |
| DELETE | `/media/{id}` | **古くなった派生物だけ**消す。`role = derived` かつ持ち主のグループがもう現行でない（`skipped` か superseded）かつ送信の記録が指していないこと。**元ファイルは対象外** |
| POST | `/merge-groups/{id}/merge` | 結合ジョブを開始 |
| GET | `/destinations` | 転送先の一覧。**API キーは応答に一切出さない**（マスク値も返さない） |
| POST | `/destinations` | 転送先を作る。URL を検証してから接続を確かめ、`remote_user_id` を記録する |
| PATCH | `/destinations/{id}` | 改名・有効無効の切り替え、または新しいリビジョン（URL・鍵の変更）を作る。**両方を一度に送れる** —— 名前は接続の検証が通ってから当てる（失敗した編集はどの欄も反映しない） |
| POST | `/destinations/{id}/verify` | 接続を検証し `remote_user_id` を取得・記録する |
| POST | `/destinations/{id}/archive` | 転送先を退役させる（記録は残す） |
| POST | `/destinations/{id}/upload` | その宛先の送信ジョブを開始（宛先ごとに 1 本、§9.10） |
| POST | `/destinations/{id}/recheck` | その宛先の再確認ジョブを開始 |
| GET | `/uploads/selectable` | §10 の選択肢。`destination_id` と `status` でフィルタ |
| POST | `/uploads` | media × destination の組を作る。`media_ids` と **`destination_ids`（複数可）** |
| GET | `/uploads` | 記録の一覧。`destination_id` / `state` / `stack_state` で絞り込む。**行にはファイルの位置（`rel_path`）も添える** —— 画面が内部の ID を出さずに名前で並べられるように（§13）。承認待ちの差分（`proposed`）も**行ごとに引き直さずに**この一覧の値から作る |
| POST | `/uploads/{id}/retry` | `failed` を `pending` に戻す。`selection_rule` は書き換えない（§8） |
| POST | `/uploads/{id}/approve` | 日時変更を承認して書き戻す（ジョブとして実行する） |
| POST | `/uploads/{id}/reject` | 日時変更を却下し、リモートを変えずに完了にする |
| GET | `/jobs`, `/jobs/{id}` | ジョブ一覧・詳細。**`volume_instance_id`** と **`mode`**（どちらも `params_json` 由来）を添える —— 前者は「いま動いていること」がどのカードの作業かを言うため、後者は `upload` 型が兼ねている 3 つの仕事（送る / `recheck` / `approve`）を画面が見分けるため。**取り出すのは名指しした欄だけで、`params` を丸ごと返す口は作らない。** **ラベルはサーバが作らない**（名前付けの実装を 2 つにしない。§13） |
| POST | `/jobs/{id}/cancel` | キャンセル |
| GET | `/events` | SSE。`Last-Event-ID`（または `after_event_id`）で **`job_event.id`** から再開。cursor が無ければ接続時点以後だけを流す。位置を作り直したときは `cursor_reset` を 1 本流し、**画面はそれを取り直しの合図として扱う**。応答が非 2xx（枠が埋まっているときの 503 など）なら `EventSource` は諦めるので、**画面が間隔を延ばしながら開き直す** |
| GET/PUT | `/settings` | 設定。値・出所（env / db / default）・ロック状態。env 由来の変更は 409 |
| GET | `/profiles` | 一覧。**archive 済みも返す**（画面が区別して出す） |
| GET | `/profiles/{slug}` | 1 件。定義（`definition`）付き |
| POST | `/profiles` | ユーザ定義を新規に作る。`slug` は以後不変 |
| PUT | `/profiles/{slug}` | **新リビジョンを作る。** ビルトインは 409 |
| POST | `/profiles/{slug}/duplicate` | ビルトインからユーザ定義を作る。元は変わらない |
| POST | `/profiles/{slug}/archive` | 候補から外す。**削除ではない。** ビルトインは 409 |
| POST | `/profiles/{slug}/test` | 指定ボリュームに対する判定・スキャンの試行 |
| POST | `/profiles/{slug}/recompute` | **`recompute_timestamps` ジョブを積む**（§6）。ビルトインでも受ける |

**`DELETE` は無い。** 使用済みのリビジョンは `media_file` などから参照されており、
消せない（§6）。候補から外すのは `archive`。
| GET | `/orphans` | reconciliation で見つかった孤立ファイル・欠損レコード |
| GET | `/health` | ヘルスチェック |
| POST | `/auth/login`, `/auth/logout` | 認証が有効な場合のみ |

## 12. 設定

設定は 2 種類に分かれ、扱いが違う。

| 種類 | 例 | 保存先 |
| --- | --- | --- |
| **インフラ設定** | データルート、待ち受け、認証、既定 TZ、送信のタイムアウトと再試行、ログ | 環境変数 > DB（Web 画面） > 既定値 |
| **プロファイル** | デバイスプロファイル、**転送先プロファイル** | **DB のみ。Web 画面で管理する** |

プロファイルはユーザのデータであって基盤の設定ではない。デバイスごとの
取り込みルールや、宛先の名前と API キーを環境変数で表現しようとすると、
番号付き変数の羅列になって TrueNAS の GUI で扱えなくなる。**転送先は
環境変数で設定できない。**

インフラ設定の優先順位は **環境変数 > DB（Web 画面） > 既定値**。環境変数が
指定されている項目は Web 画面でロックされ、「TrueNAS のアプリ設定で
固定されています」と表示される。

この順序を選んだのは、TrueNAS のアプリ設定画面が常に事実と一致する状態を保つため。
逆順（env は初回の種で以後 UI が勝つ）にすると、env を変更して再デプロイしても
反映されないという分かりにくい挙動になる。

| キー（`MEDIAFERRY_` 接頭辞） | 既定値 | 内容 |
| --- | --- | --- |
| `DATA_ROOT` | `/data` | データルート |
| `BROKER_SOCKET` | `/run/mediaferry/broker.sock` | mountd のソケット。`compose.yaml` が app へ渡す |
| `BIND_HOST` | `127.0.0.1` | 待ち受けアドレス。公開するには明示的に変更する |
| `HTTP_PORT` | `8080` | 待ち受けポート |
| `AUTH_PASSWORD` | 未設定 | 設定すると認証が有効になる |
| `SECRET_KEY` | 未設定 | 転送先の API キーを暗号化するマスター鍵（§12.3）。転送先が 1 件でもあれば必須 |
| `UPLOAD_TIMEOUT_SECONDS` | `86400` | HTTP タイムアウト |
| `UPLOAD_MAX_ATTEMPTS` | `3` | リトライ上限 |
| `AUTO_IMPORT` | `trusted` | `trusted` / `off`。§12.1 |
| `DEFAULT_TIMEZONE` | 未設定 | プロファイルが指定しないときの TZ |
| `LOG_LEVEL` | `info` | ログレベル |

`AUTH_PASSWORD` は API のレスポンスで常にマスクし、ログにも出さない。Argon2 で
ハッシュして保存する。転送先の API キーの扱いは §12.3 を参照。

`BIND_HOST` の既定を loopback にし、認証が無効なまま非 loopback にバインドしている
場合は起動ログと UI バナーで警告する。認証を必須にはしない（LAN 内で無設定で
使えることを優先する方針のため）が、意図せず公開している状態には気づけるようにする。

### 12.1 自動取り込みと信頼登録

`AUTO_IMPORT=trusted`（既定）では、**信頼登録済みかつ `identity_confidence = high` の
ボリュームインスタンスのみ**自動で取り込む。

初めて見るボリューム、および確度が `low` のボリュームは、スキャン結果を画面に
出すところで止まり、ユーザの承認を待つ。承認すると `trusted_at` が入り、
以後そのカードは挿すだけで取り込まれる。

**数えるのは、取り込むかどうかによらない。** 対象と判定できた接続には**必ず `scan` を
積む**（信頼の有無にも `AUTO_IMPORT` にもよらない。`AUTO_IMPORT=off` でも積む）。
「取り込まない」は「数えない」ではなく、上の「スキャン結果を画面に出すところで止まる」も
スキャンが済んでいることを前提にしている。取り込む残りの件数（§13 の「38 件を取り込む」）
は `source_entry` から導くので、**数えなければ画面は永久に「0 件」に見える**。印は接続
ごとに持つので、**挿し直すともう一度数える** —— これが「前回のスキャン以降に撮ったもの」を
拾う道になる。`scan` → `import` → `detect_groups` は同じ排他区間で順に積み、ジョブが
直列に走ることで順序が保たれる。

**ただし「以後」には確度の条件が残る。** 正確には「**このカードだと確かめられた場合に
限り**、以後は挿すだけで取り込まれる」。自動取り込みの候補は `trusted_at` に加えて
`identity_confidence = 'high'` を要求する。初回の観測は必ず `low` だが、その観測で
指紋を憶えるので、次に観測すると（同じ挿入のままでも）`high` になる。一方、
**`fs_uuid` が無い媒体と、同じ UUID の別 presence が併存している間は、何度観測しても
`high` にならない** —— そのカードは信頼登録しても自動では取り込まれない。

**画面の同意文はこの条件を文全体に掛ける。** 「以後は挿すだけでコピーされます」を先に
無条件で置いてから限定を付け足すと、確かめられない媒体では前半が成立せず、同じ
確認ダイアログの中で矛盾する。

この段階を設けるのは、`generic-dcim` フォールバックがあるため
**`DCIM` を持つ任意の USB ドライブが自動でコピーされてしまう**ため。他人のスマートフォンや
バックアップディスクを挿しただけで私的な画像が NAS に保存されるのは、
ソースを read-only で扱っていても防げないプライバシー上の問題である。

普段使うカードは一度承認すれば以後は「挿すだけ」のままなので、体験は損なわれない。

**信頼の限界を UI に明示する。** §8 のとおりボリューム同定は推測であり、
複製カードや UUID を保持した復元は誤認しうる。read-only で扱う以上、
ボリュームに永続マーカーを書いて確実に同定することはできない。

アップロードは信頼登録の有無にかかわらず**常に手動**である。

### 12.2 タイムゾーン未設定時の扱い

`timezone_policy: force_offset` のプロファイルが有効で、かつ `timezone` が
プロファイルにも `DEFAULT_TIMEZONE` にも無い場合、**取り込みを開始せず設定画面へ誘導する**。

これは初版仕様の欠陥への対処である。ビルトインの `dji-osmo` から地域固定を外して
`timezone: null` にしたため、`DEFAULT_TIMEZONE` の既定値を `UTC` にすると
初回起動直後に挿したカードが UTC として取り込まれ、`force_offset` が補正にならず
誤った時刻で確定してしまう。後から設定を変えても既存レコードは自動では直らない。

`DEFAULT_TIMEZONE` に既定値を置かず、未設定を明示的なエラーにすることで防ぐ。
変更したくなった場合は `recompute_timestamps` ジョブで明示的に再計算する。

### 12.3 転送先プロファイル

宛先は Web 画面で管理する。1 件も無い状態では何もアップロードできないので、
初回セットアップで 1 件登録する。

| 欄 | 内容 |
| --- | --- |
| `name` | 表示名。「自分の Immich」「家族用」など。一意 |
| `base_url` | **API を叩きに行くエンドポイント**（§12.4） |
| `public_url` | 画面のリンク生成にだけ使う公開 URL。省略時は `base_url` |
| API キー | `destination_credential` に版を付けて保存する |
| `enabled` | 一時的に選択肢から外す |

#### API キーの保存

Immich API は可逆な値を要求するのでハッシュ化できない。**マスター鍵による
AEAD 暗号化で保存する。**

これは厳密には封筒暗号化（credential ごとの DEK をマスター鍵で包む方式）ではない。
資格情報は多くても数件なので、マスター鍵で直接暗号化し、鍵の入れ替え時に全件を
1 トランザクションで再暗号化する方が部品が少なく確実である。**名前も実態に
合わせて「封筒暗号化」とは呼ばない。**

**形式を仕様で固定する。** 後から変えると全 credential の migration が要る。

| 項目 | 決定 |
| --- | --- |
| 方式 | 実績あるライブラリの AEAD（XChaCha20-Poly1305、無ければ AES-256-GCM） |
| 保存形式 | 自己記述。`version` + `key_id` + ランダム nonce + ciphertext/tag |
| AAD | `credential.id`、`destination_id`、`credential.revision`、スキーマ版。**行の差し替えを検出する** |
| マスター鍵 | `MEDIAFERRY_SECRET_KEY`。**パスワードではなく 256bit のランダム鍵を base64 で与える。** 長さと形式を起動時に検証する |
| 誤鍵の検出 | `key_id`（マスター鍵の指紋）を各 ciphertext に記録し、起動時に照合する。**違う鍵で「壊れた credential」として上書きしない** |
| 鍵の入れ替え | 旧鍵と新鍵の両方を与えて全 credential を 1 トランザクションで再暗号化する。途中で落ちても中途半端にならない |
| 鍵の喪失 | 復号できない credential は「要再登録」として表示し、その宛先を使うジョブは開始させない。メディアと履歴は失われない |

マスター鍵が未設定で転送先が 1 件でもあれば**起動を拒否する**。

**この暗号化が守るもの／守らないもの**を明確にしておく。

| 脅威 | 効くか |
| --- | --- |
| `DATA_ROOT` のバックアップ・スナップショットだけが流出 | **効く**。マスター鍵は含まれない |
| DB ファイル単体の持ち出し | **効く** |
| app の RCE | **効かない**。プロセスはマスター鍵を持つ |
| NAS の管理者権限を持つ者 | **効かない**。アプリ設定も読める |
| TrueNAS の**システム設定バックアップ**の流出 | **効かない**。環境変数はそちらに含まれうる |

「同じホストに鍵があるから無意味」ではない。**鍵が `DATA_ROOT` のバックアップの
外にある**ことが境界を作る。ただし「あらゆるバックアップから分離されている」
わけではなく、上表のとおり TrueNAS のシステム設定バックアップには含まれうる。
守れる範囲を `DATA_ROOT` の backup / snapshot 単体の流出に限定して理解する。

#### 秘密として扱う範囲

API キーそのもの以外にも、漏れうる経路をすべて塞ぐ。

- DB ファイルは 0600、親ディレクトリは 0700
- **WAL / SHM / 一時ファイル**も同じ扱い。バックアップ手順に含める
- `job.params_json`、`job_event`、SSE のペイロードに秘密を入れない
- 例外・スタックトレース・ORM の `repr`・診断バンドルで秘匿する
- API のレスポンスでは常にマスクし、読み出しの API は提供しない

#### 古い資格情報の破棄

版管理したまま旧 API キーを永久に持ち続けると、ローテートしても漏洩面が減らない。
**参照中のジョブが無くなった旧 revision は `secret_encrypted` を消し、
`purged_at` を立てる。** 監査には `key_fingerprint` と作成時刻だけ残す。

登録・編集時に接続を検証し、`/api/users/me` から `remote_user_id` を取得して
記録する。既存の宛先と同じアカウントを指していれば警告する。編集の結果
以前と違うアカウントを指すようになった場合は、**その宛先のアップロード履歴が
当てにならなくなる**ので、警告して明示的な確認を求める。

### 12.4 転送先の接続エンドポイント

`base_url` は **mediaferry が実際に接続するエンドポイント**であり、
ユーザがブラウザで開く URL とは別物として扱う。`public_url` は画面に出す
リンクの生成にだけ使い、通信には使わない。

**`base_url` には、CDN やリバースプロキシを経由しない直接到達できる
アドレスを指定する。** 実測では、公開 URL（Cloudflare 経由）へ
28 GiB をアップロードしようとして 622 MiB 送った時点で `502 Bad Gateway` に
なった。CDN やリバースプロキシには body size の上限とタイムアウトがあり、
数十 GiB のアップロードは通らない。同じ構成でも内部アドレスに向ければ
28.36 GiB が 84.5 秒で完走した。

mediaferry は Immich と同じホスト、あるいは同じ LAN で動くことを前提にして
いるので、外部経路を通る必要がない。`http://127.0.0.1:<port>` や
`http://immich:2283` のような内部アドレスを使う。TLS を介さないので証明書の
検証も問題にならない。

内部エンドポイントも TLS で公開されていて証明書のホスト名を合わせる必要が
ある場合は、`base_url` にそのホスト名の URL を書き、コンテナの `extra_hosts`
（`/etc/hosts`）でそのホスト名をローカル IP へ向ける。アプリ側に接続先の
上書き機構は持たない。

#### URL の検証と redirect の扱い

**API キーは `x-api-key` というカスタムヘッダで送る。HTTP クライアントは
cross-origin の redirect でもカスタムヘッダを剥がさない。** 誤設定や侵害された
エンドポイントが外部ホストへ 301 を返すと、そのまま API キーが渡ってしまう。

- `base_url` / `public_url` は **`http` / `https` のみ**。userinfo（`user:pass@`）と
  fragment を禁止し、保存時に正規化する
- **API 呼び出しは redirect を追わない**のを既定とする（`follow_redirects=False`）
- 追う必要がある場合も、**scheme・host・port が同一のときだけ**手動で追従する。
  ホストが変わる redirect には秘密を送らない
- 接続の検証とアップロードで**同じ transport の方針**を使う。片方だけ緩めない
- `public_url` は画面のリンクに描画されるので、`javascript:` などを保存できないよう
  同じ検証を掛ける

## 13. 画面

### 画面の構成

**ナビゲーションは「ホーム / 写真 / 設定」の 3 つだけ。** 広い画面では左のサイドバー、
狭い画面では**画面の上の帯**で、項目は同じ。狭い画面でも 3 項目を 1 行に横並びにし、
畳んで隠さない（名乗りは出さない）。エンドユーザは CLI に習熟していない前提なので、
**画面の区切りを内部の構造ではなく「利用者がしたいこと」に合わせる**。

**狭い画面で下に貼り付く操作バー**（写真の選択、送るの確認）は、ナビとは反対の端に
置く。ナビが上、操作バーが下で、どちらももう一方を隠さない。

| 場所 | 中身 |
| --- | --- |
| **ホーム** | **4 つの並び**（**押した操作** / いま動いていること / やること / いまの様子）、送り先ごとの状況、最近の取り込み。**「押した操作」は、ジョブを積む操作を押した人がここへ来たときだけ出す** —— 押したジョブを id で名指しして追い、走っていれば進捗、終わっていれば結果を出す。**成功は着いてから 30 秒で消え、失敗は消えない**（× でも消せる）。**閉じても作業は続く**ことを書き、**作業の履歴への導線**を置く |
| **写真** | メディア一覧。日付でまとめたグリッド、サムネイル、**組（RAW+JPEG）の札**、絞り込み、複数選択 → 送る。タイルを押すと 1 件の**くわしく画面**（`/photos/:id`）へ |
| **設定** | 自動取り込み、送り先、カメラの種類、そして**性質ごとに分けた 3 つの節** ——「ふだんは使わない操作」（つなぐ・日時の確認・接続中のカード）、「記録」（作業の履歴・つないだ後の後片付け・使っていないファイル）、「詳しい設定」（env 由来の設定と**リセット**）。env 由来の項目は錠前アイコン付きの読み取り専用 |

**写真は名前で探せ、200 件の先へ進める。** 1 度に読むのは 200 件（`MAX_PAGE_SIZE`）
なので、**ファイル名の検索**（`q`）と**前後のページ送り**（`page`）が無いと、それより
古いものへどの画面からも辿り着けない。どちらも住所に持たせ、絞り込みを変えても
探している言葉は残す（ページは 1 に戻す）。

写真の絞り込みは常に見えるところにチップで置く：**すべて / まだ送っていない / 確認が要る /
動画 / 送信済み / 送れなかった**。「まだ送っていない」「確認が要る」「送信済み」
「送れなかった」は**宛先ごと**の状態なので、宛先を伴って絞る（「宛先 D に未送信」）。
当てはまるものが 1 件も無いときは、そう書く。

**「送れなかった」を落とさない。** 送信に失敗した記録は「まだ送っていない」にも
「送信済み」にも入らないので、この絞り込みが無いと**どの画面からも見えない**
（`failed` を `pending` へ戻して送信を始め直す操作は、設定 › 送り先の
「送れなかったもの」にある）。

### 写真 —— タイルを押すと開く

**タイルを押すと、その 1 件がくわしく画面（`/photos/:id`）で開く。複数選ぶときは
タイル右上の丸を押す**（Google フォト式。押す＝選ぶではない）。

| 操作 | 結果 |
| --- | --- |
| タイルを押す | くわしく画面（`/photos/:id`）へ |
| 右上の丸を押す | 選ぶ／選ぶのをやめる（送るための選択） |

**タイルの四隅の割り当て**: **左上**＝つないだ動画の印（`role === "derived"` の
ときだけ）と**組の札**（`JPG+RAW`）を横に並べる、**右上**＝選ぶ丸、**右下**＝動画の尺。
**左下には何も置かない。**

**絞り込みに「つないだ動画」がある。** `role=derived` で絞る。**宛先ごとの
絞り込みではない**ので、宛先を選んでいなくても押せる。

**まとめて選ぶ手段を 2 つ置く。** 1 日ぶんを送るのに、タイルを 1 つずつ押させない。

- **日付の見出しの丸。** その日を全部選ぶ／全部外す。**`role="checkbox"` の 3 状態**
  （全部＝`true`、一部＝`mixed`、無し＝`false`）にする。`aria-pressed` のボタンでは
  「一部選ばれている」を表せず、読み上げで全選択と区別が付かない。**全部選ばれて
  いるときだけ外し、それ以外は選ぶ。** 触るのは**いま画面に並んでいる行だけ** ——
  絞り込みで隠れているぶんや次のページのぶんは触らない（見えていないものを選ぶ丸は、
  押した結果が確かめられない）
- **Shift+クリックの範囲。** 直前に押したタイルをアンカーに、いま並んでいる順で
  アンカーから押したところまでを**選ぶ**（外さない。外すのは個別のクリックに残す）。
  日付のまとまりはまたぐ —— 利用者が見ている並びは 1 本の流れで、まとまりは見出しに
  すぎない。**アンカーは「並び」に属する状態なので、並びが変われば捨てる** ——
  ページ送り・絞り込み・探している言葉・宛先の変更のいずれでも捨てる（選んだものは
  隠れても覚えたままだが、アンカーだけは無効になる）。アンカーの行が並びから消えて
  いるときは、押した 1 枚だけを選ぶ

**選ぶ単位はタイル（＝組）。** 組の丸を押すと `stack.members` が全員まとめて付く。
日付の丸も Shift の範囲も同じ単位で数える。

**組の片方だけが未送信のときは、残っている行が組の全員を連れて出る。** 主（JPG）を
送信済みで従（CR2）だけが未送信なら、`status=unsent` に主が乗らないので
`collapse=stack` の従外しは効かず、返るのは CR2 の行で、そこに `stack.members` として
JPG と CR2 の両方が付く。したがってタイルは `JPG+RAW` と名乗り、選ぶと 2 枚とも
選ばれる。**送信済みの JPG を選び直しても害は無い**（`POST /uploads` は
`already_complete` を返して何も書かない）が、札は「2 枚ある」と言っているので
ここに明記する。

### くわしく（`/photos/:id`）

**1 件を描くのに要るものを 1 本の応答で返す。** 画面が複数の API を継ぎ足すと、
片方だけ古い状態が出る。

| 出すもの | 出どころ |
| --- | --- |
| 絵・ファイル名・撮影日時・長さ・大きさ | `media_file` |
| つないだ動画かどうか | `role` |
| **元になったファイル**（`position` 順） | `merge_member`。1 件ずつその `/photos/:id` へ飛べる |
| **この 1 枚を作っているファイル**（組の member。主が先頭） | `stack`。行ごとにチェック・ファイル名・大きさを出し、**既定は全部オン**（一覧の丸が組ごとに選ぶのと揃える）。「送る」はチェックの付いた id だけを渡し、0 個なら押せない。組でない写真では**セクションごと出さない**。ファイル名はその member の `/photos/:id` へのリンク（**従の詳細を直接開いても同じセクションが出る**）。チェックの状態は URL に持たない |
| **宛先ごとの状況** | `upload_record`。「Immich に入っています」「Immich のゴミ箱にあります」「Immich にはもうありません」「まだ送っていません」「送れませんでした」。**宛先ごとの操作は「サーバを確かめる」だけ**（押すとその宛先の全件を照合し、無いものは「まだ送っていません」に戻る。§9.10）。「Immich にはもうありません」は原理的に出ない保険である（§9.10） |
| 消せるか、消せないなら理由 | 下の規則をサーバが判定して返す |
| **つないだ結果** | `merge_group`（`group_view`）。検証の結果を検査ごとに出し、**このグループへの操作もここに置く** |
| 操作 | 「送る」（既存の送る動線へ）と「消す」。つないだ動画には「中身を見て、これを使う」「同じ構成でやり直す」「構成を変える」「これは別々」 |

**消せるのは `role = 'derived'` の 1 件で、どの宛先にも「Immich に生きている」
観測が無いときだけ。** カードから取り込んだ元ファイルは、これまでどおり消せない。

| 記録の状態 | 判定 |
| --- | --- |
| 有効な記録が 1 つも無い、または `failed` だけ | **消せる** |
| `complete` だが再確認で `remote_asset_id` が外れた（`remote_checked_at` はある） | **消せる** |
| `remote_is_trashed = 1`（Immich のゴミ箱） | **消せる** |
| `remote_asset_id` があり、ゴミ箱でもない | 消せない（Immich に実在する） |
| `complete` だが `remote_asset_id` も `remote_checked_at` も無い | 消せない（**在るかどうかを観測していない**） |
| 送信中・確認待ち | 消せない（決着していない） |
| **元になったファイル**に送信中・確認待ちの記録がある | 消せない（消すとグループを「別々にした」に戻すので、送っている最中の根拠を動かすことになる） |
| **持ち主のグループが無い**（出所の分からない `derived`） | 消せない（孤立ファイルと同じ扱い。§18 の 7） |

**「無い」には観測を要求する。** `remote_asset_id` が無いだけでは消さず、
**`remote_checked_at` が入っていること**（一度は確かめたこと）を条件にする。
`remote_is_trashed` が NULL で `remote_asset_id` があるものは「在る」側に倒す
—— 観測していないだけで、無いことの証明ではない。`invalidated_at` が立っている
記録は数えない（§10 と同じ）。

**消すと、持ち主のグループが現行なら「別々のままにした」（`skipped`）になり、
元になったファイルが「まだ送っていない」に戻る。戻せない。** 規則は 1 か所
（`deletion_blocker`）に置き、一覧・くわしく・`DELETE` の 3 つが同じ定義を使う
—— 押しても 409 で断られるボタンを並べないため。

**1 つの出力を複数のグループが指しうる。** 構成を変えない組み直し（やり直し）は
旧グループが `output_media_file_id` を持ったまま superseded になり、新グループの
出力は `rel_path` が同じなので同じ `media_file` 行が再利用される。そこで
**「持ち主のグループ」を 1 つに決める規則も 1 か所に置く**（`db.media.owner_group`）
—— 置き換えられていない・破棄していないものを先に、同点は `id` で決める。
削除・予告（元ファイルが戻るか）・元ファイル欄・後片付けの一覧が別々に引くと、
同じ 1 件について違うグループを見て、判定と表示と実際の挙動が食い違う。
**現行かどうかは「member をまだ握っているか」で決める**（`superseded_by_id` が
無く、`skipped` でない）—— 結合が終わったかどうかではない。`merging` の途中で
落ちたグループも member を握っているので、その出力を消すならグループごと手放す。

**カードは、目の前のどれなのかが分かる形で出す。** ホームのカードの札と「カードの中身」は
**ラベルと容量**を出す（容量は `formatBytes` の書式。生のバイト数を出さない）。同じ
カメラのカードが 2 枚挿さっていると、カメラの種類も確度も同じ行になるので、**種類の
表示だけでは区別が付かない**（DJI は内蔵ストレージと SD カードが同時に見える）。
ラベルが無いカードは既定名（「名前の無いカード」、複数あれば連番）を同じ場所に出す。

**「SD カードか内蔵メモリか」は書かない。** ブローカーが渡す `VolumeInfo` にその区別は
無く、ラベルからの推測は別のカメラで嘘になる。**出せるのはラベルと容量だけ**で、
見分けはそれで足りる。

### ホームの 3 つの並び

**ホームは一覧を持たない。** `/devices` ＋ `/jobs` ＋ `/dashboard` から**毎回導く**。
導出は **1 つの純粋関数**に閉じ込め、上から次の 3 つの並びに振り分ける。在るものだけを
出し、空の並びは見出しごと出さない。

| 並び | 中身 |
| --- | --- |
| **いま動いていること** | 走っている・待っている作業を**すべて**。カードを掴んでいる作業にはそのカードのラベルと「抜いていいか」を添える |
| **やること** | カードの取り込みと、下の 4 つ |
| **いまの様子** | 仕事も動きも無いカード（数えている / 取り込むものが無い / 対象外 / 中身がまだ無い） |

**「いま動いていること」は全部出す。** 「いま取り込む」は 数える → コピー → 分かれた
動画を探す の 3 本を積むので、1 本だけ選ぶと残りが画面から消える。並べる順は**走って
いるものが先、待っているものは積んだ順**（一覧の並びは API 側の都合で変わりうるので、
順序は画面側で決める）。

**カードは「状態」ではなく「仕事」として出す。** カード 1 枚の行き先は、この順で
決まる。

1. そのカードを掴んでいる作業が動いている → **いま動いていること**（やることには
   出さない）
2. 対象と判定できていない → **いまの様子**（判定の理由を添える）
3. **まだ一度も数えていない** → **いまの様子**「中身を数えています。」
4. 取り込む残りがある → **やること**「`SD_Card` から 38 件を取り込む」。**未信頼なら
   同じ札に「このカードを信頼する」も置く**（信頼を別の札に立てると、承認と取り込みが
   別々の仕事に見える）
5. それ以外 → **いまの様子**（「取り込むものはありません。」／対象だが中身がまだ無い）

**「まだ数えていない」と「0 件」を区別する。** 挿した直後のカードはまだ数えていない
ので、**「取り込むものはありません」とは書かない** —— 数える前の 0 件は「空」では
なく、断定すると数え終わるまでの間、画面が嘘をつく。数えたうえで残りが無いときだけ
「ありません」と書く。

**中身が空のカードも、数え終われば 3 から出る。** 「数えたか」はスキャンが最後まで
走った事実（§11 の `scanned_at`）で、**数えた結果の件数ではない** —— 件数から導くと、
一致するファイルが 1 件も無いカード（撮影前の内蔵ストレージ。DJI は SD カードと同時に
見せる）はスキャンが成功しても「中身を数えています。」から永久に出られず、5 の
「対象だが中身がまだ無い」に**到達できない**。ホームと「カードの中身」が同じカードに
ついて別のことを言うのは、この画面がいちばん避けているものである。

**取り込み中のカードの「いま取り込む」は押せない。** 走り出した札は「やること」から
消えるので、ふつうはボタンごと無い。別のタブから押された競合に備えて、**その
カードを掴んでいる作業があるボタンは無効にする**（この画面が出した要求の最中かどうか
だけでは足りない）。

**札は、利用者が押さなくても消えうる。** 信頼を承認すると §12.1 の条件（信頼済み ＋
確度が `high` ＋ 対象に中身がある）が揃うので、**次の巡回（既定 5 秒）で取り込みが
自分で始まり、札は「いま動いていること」へ移る**。**移った跡には同じ場所に進行中の
作業が来て、そこには「中止する」が描かれる** —— 「いま取り込む」を押そうとしていた手が
別のボタンに当たりうる。取り込みはどちらの経路でも進むので害は小さいが、**この経路が
あることを前提に文言と並びを決める**（押せなくなったことを驚きにしないため、走っている
作業には必ずカードのラベルを添える）。

**「やること」は画面が持つ一覧ではなく、状態から毎回導く。** 次の 5 つのうち、在るものだけを
**手を動かす順**で出す。

| やること | 条件 | 開く先 |
| --- | --- | --- |
| 取り込む | 挿さっているカードに取り込む残りがある（`pending_count > 0`） | その場で取り込む（**カードの中身**ページへも入れる） |
| つなぐ | 現行の結合候補がある（`detected` / `failed`） | **つなぐ**ページ |
| 確かめる | つないだが検証に落ち、まだ採用していない結合物がある | **つなぐ**ページ |
| 送る | どこかの宛先に未送信のものがある | **送る**ページ |
| 確認 | `awaiting_datetime_approval` の記録がある | **確認**ページ |

**「確かめる」を落とさない。** 検証に落ちた結合物は送る候補に出ず（§10）、その構成
ファイルも active な member なので出ない。ここに出さないと、**ホームが「やることは
ありません」と書く一方で、つなぐ画面には「中身を見て、これを使う」が出ている**状態に
なる。

**「いま、やることはありません」と書くのは、3 つの並びがすべて空のときだけ。** 空の表
は出さない。**カードが挿さっていれば必ずどれかの並びに出る**ので、
カードの札と空表示が同時に出ることは構造上あり得ない。**読めていないものを「無い」とは
言わない** —— 3 本の問い合わせのどれかがまだ返っていないか失敗している間は、空表示を
出さない（失敗そのものはバナーが知らせる）。

**やることの札は、残っている仕事があるときだけ出る。** そのため「つなぐ」と「確認」は
**設定 › ふだんは使わない操作にも常設の入口を置く** —— 0 件の状態で入る道が無くなると、手で
グループを作ることも、過去の確認を見ることもできない。

作業ページはホームの下位に置き、**ナビゲーションの項目を増やさない**。

| ページ | 内容 |
| --- | --- |
| カードの中身 | **ラベルと容量**、判定結果・確度・信頼状態と、**判定の理由**（対象外のボリュームも理由付きで出す）。スキャン、取り込み、信頼登録の同意。**抜いていいかは下のとおり常に出す** |
| つなぐ | グループ候補の一覧。構成ファイル・ギャップ秒数・パートサイズを表示し、**なぜグループ化されたかが分かる**ようにする。閾値スライダで再計算。手動分割・結合。検証結果と継ぎ目サムネイル。失敗の再試行 |
| 確認 | `pre_existing` アセットの日時変更差分。現在値と変更案を並べて表示。一度に出すのは 200 件までで、**切れていることは書く**（裁定 20） |
| 送る | 送り先 → 対象 → 確認 の 3 段（下） |

### カードを抜いていいか

**押して確かめるのではなく、常に出す。** カードを出しているところには
「**いま抜いて大丈夫です。**」か「**作業中です。終わるまで抜かないでください。**」を
必ず添える（ホームの 3 つの並びのどこにいても、「カードの中身」ページでも）。文言を
持つ場所は 1 つだけにする。

**判定はサーバが持つ**（`/devices` の `busy` = そのカードを掴んでいる作業があるか）。
画面が `/jobs` を見て自分で決めると、`close` の内側の条件と**別の答えを出す 2 つ目の
実装**になる。

**作業が終われば、押さなくても表示が切り替わる** —— 進捗の知らせで一覧を取り直すので、
掴んでいた作業が終わった時点で「いま抜いて大丈夫です。」に変わる。これが成り立つには
**2 つが要る**。

- **決着は、成功でも失敗でも `job_event` に残す。** 進捗の配信（SSE）の出所はここだけ
  なので、失敗の経路で書かないと、サーバはカードを離しているのに画面は「作業中です。」を
  出したまま止まる
- **知らせが届かないときのために、走っている作業が空になった縁でも一度だけ取り直す。**
  **`/devices` を拍のたびには叩かない** —— あの経路の判定（§9.2 の 4）は候補ごとに
  **実際にマウントする**ので、数秒ごとに叩くと、カードを挿している限りマウントと
  アンマウントが続く（`jobs/volumes.py` の `_probe`: 「代償は『GET /devices のたびに
  mount / umount が走る』こと」）

**この断定文を出す画面は、`/devices` を取り直し続ける義務を負う。** ホームだけでなく
**「カードの中身」も同じ** —— 取り込みを押した人がそのまま見ているのはそちらなので、
更新されなければ「作業中です。」を永久に読み続ける。画面の役目は**答えを出すこと**で
あって、押させることではない以上、**答えが更新されないなら役目を果たしていない**。

**ただし 2 つ目（縁での取り直し）を持つのはホームだけ。**「カードの中身」は `/jobs` を
引いていないので、「走っている作業が空になった」という縁そのものを作れない —— **知らせが
届かない間は固まったまま**である。両方を持たせるには、あの画面にも `/jobs` を引かせて
拍を回すことになり、**それは「カードを見に来ただけの画面が作業の一覧を持つ」ことを意味
する**。いまはその代償を払わず、共通の経路（知らせ）だけで足りるものとしている。

**「取り外す」のような操作は画面に置かない。** 読み取り専用のマウントは作業の終わりに
外れているので、掴んでいる作業が無ければ押しても何も起こらない。`POST
/volumes/{id}/close` は API に残るが、画面の役目は**答えを出すこと**であって、押させる
ことではない。

### 送る

**3 段で、順番を固定する。宛先を先に決める。**

1. **どこへ送るか。** 休止中の宛先は選べないことを理由付きで出す
2. **何を送るか。** 既定は**「まだ送っていないもの、すべて」**。ほかに**「いちばん新しい撮影日のぶんだけ」**と
   「自分で選ぶ」。写真の画面で選んでから来たときは、その選択が既定になる
3. **確認。** 対象件数・合計サイズ・宛先名に加えて、**つないだ動画の内訳**と
   **撮影日の幅**、1 度に送れる分を超えているならそれも出す（下の「守ること」）。
   **宛先ごとの件数は出さない** —— 組は media × destination の全組み合わせで
   作られ、既に送ってある組は再利用されるので、宛先ごとに違う件数を書くと
   実際に作られる組の数と食い違う

**前回の続きを全部送る、が既定のまま 1 手で終わる**ようにする。

**送った結果の 1 文は「件（写真）× 宛先」で数える。**「N 件を、M 宛先へ送り始めました。」
断られたぶんも「送れない写真が N 件」と数える。**`POST /uploads` が返す pair の数を
そのまま報告しない** —— pair は media × destination の直積なので、写真の枚数でも
宛先の数でもない。同じ写真の id は重複を落とす。

この画面の他の表示（対象の件数、確認の件数）はすべてファイル単位の「件」で、
**「組」は RAW+JPEG のスタックを指す語**として札や写真タブで使っている。pair の数に
「組」と付けると、スタック 1 組（2 枚）を 1 宛先へ送っただけで「2 組を作りました」と
出て、利用者の見ているものと食い違う（実機で見つかった）。

### つなぐ

**この画面が出すのは「まだつないでいないもの」だけ**（`GET /merge-groups?pending=true`）。
`detected`（これからつなぐ）と `failed`（結合に失敗）と `merging`（走っている最中）で、
**`merged` は出さない。**

**つないだ動画への操作は、その 1 本のくわしく（`/photos/:id`）にある。** つなぎ目の
空白の判定は結合の前後を区別しないので、済んだ組を同じ画面に並べると**警告色と
「確かめてから決めてください」がもう決めた後のものに出る。**

**採用（「中身を見て、これを使う」）も一緒にくわしくへ置く。** `SENDABLE_CLAUSE` は
`passed` か `adopted_at` しか見ないので、**この入口が無いと検証不合格の動画を送る
手段が消える。** つなぐ画面から `merged` を外すのと、くわしくに採用を置くのは
**同じ変更でなければならない。**

`failed` はつなぐ画面に残す —— §10 の既定の一覧から外れており、member を個別に
送る入口はここにしかない（裁定 12）。

### ジョブを積む操作の動線

**時間のかかるジョブを積む操作は、積んだと言ってからホームへ送る。進捗と結果はホームが持つ。**

`POST` は**ジョブを積むだけ**で、状態の書き換え（例: `detected → merging`）は**ワーカーが
ジョブを拾ってから**なので、押した直後に一覧を読み直すと**押す前と同じ画面が描き直される**
（実測で 465 ms の窓）。押した人には「何も起きなかった」としか見えない。

| 操作 | どうする |
| --- | --- |
| カードの中身の「取り込む」／つなぐの「つなぐ」「再試行する」／送るの「送る」 | 通知してホームへ送る |
| **「分かれた動画を探す」** | **留まる** —— 候補がその画面に出るので、連れ出したら本末転倒 |
| 採用・破棄・組み直し | 留まる —— その場の一覧が変わる |
| ホームの「取り込む」 | 留まる —— すでにホーム |

**押したジョブの id を持って行き、ホームが名指しで追う。** すぐ終わるジョブ（検出は実測 47 ms）
だと、遷移した時点でもう終わっているので、走っているものだけを出す枠には何も出ない。

**時間はホームに着いてから数える。** 終了時刻との引き算にすると、ブラウザとサーバの時計が
ずれていたとき、遅れていれば「未来に終わったジョブ」が居座り、進んでいれば一度も出ない。

**失敗は自動で消さない。** 時間で消える失敗は、見逃した人にとって「何も起きなかった」と
区別が付かない。

**断られた写真と、開始に失敗した宛先は隠さない。** 1 本も始まらなかった送信では追うジョブが
無いが、**そのときこそ知らせが要る**ので、ジョブとは独立に出す。

### リセット

**作り直すための入口**（設定 › 詳しい設定 › リセット）。**消えるのは mediaferry が
持っているものだけで、Immich にある資産は対象ではない** —— 消しに行かないし、
消えない。§9.11 の削除の規則（「Immich に生きていない `derived` だけ」）は、
1 件ずつ判断している場面の不変条件なので、ここには掛けない。

**段は 4 つで、積み上げ**（深い段は浅い段を含む）。

| 段 | 何が消えるか | 取り消せるか |
| --- | --- | --- |
| 作業の記録 | `job` / `job_event`、別々にした組み合わせの記録、**公開が完了した `artifact_staging`** | 作り直せる（再スキャン・再検出） |
| 送信の記録 | `upload_record` | **戻らない。** 次に送ると初回の `checking` が `reject` を返して `origin` が `pre_existing` に決まり、`first_check_result` は不変なので `created_by_us` には二度と戻らない（§9.10）。日時の自動補正は 1 件ずつの承認に変わり、スタックは `skipped` になる |
| 取り込んだファイル | `media_file`・`merge_group`・`source_entry` と、`DATA_ROOT` 直下の実体。加えて**数えた印**（`volume_instance.scanned_at` と、挿さっている接続の `volume_presence.auto_scan_at`） | カードに元があれば取り込み直せる |
| すべて | 上のすべてと、カードの記録（信頼の記録を含む） | 戻らない |

**送り先とカメラの種類はどの段でも残す。** 取り込んだデータではなく設定で、消すと
接続のやり直し（API キーの入れ直し）になる。

**「取り込んだファイル」の段は、数えた印も落とす。** ホームは `pending_count == 0` を
「取り込むものは無い」と読むので（§13 の分岐は `scanned_at` を先に見る）、数えた元を
捨てたのに印を残すと、**カードに元があるのに取り込む入口が消える**。`scanned_at` と
`auto_scan_at` の両方を落として初めて、監視が数え直してホームに入口が戻る
（片方だけでは、ホームが「中身を数えています。」から動かない）。**抜けた接続の
`auto_scan_at` は履歴なので触らない**（監視も抜けた接続は拾わない）。

**断る条件は 3 つあり、`code` で書き分ける。** 画面は `code` で文面を選ぶので、
まとめると「何を待てばよいか」が書けない。

- **走っている作業がある**（`job_in_flight`）—— 消せたとしても走っている取り込みが書き込み先を失う。
  **`queued` は見ない**（まだ誰も掴んでおらず、ここに入れると監視がスキャンを積むカードでは一度も通らない）
- **回収待ちの公開がある**（`staging_pending`）—— `writing` / `staged` の `artifact_staging` は
  中断した公開の復旧に要る（起動時の reconciliation が拾う）。**消さずに断る**
- **回収待ちの送信がある**（`upload_claim_pending`）—— `upload_record.claim_job_id` も
  `job` を `ON DELETE RESTRICT` で掴む。中断した送信の claim は起動時の
  `release_interrupted` が外すまで残るので、**どの段でも**外部キーが `job` の削除を止める

**公開が完了した `artifact_staging`（`published`）は「作業の記録」の段で、`job` より先に消す。**
`job_id` を `ON DELETE RESTRICT` で掴んでいるので、順序を逆にすると外部キーが止める。
`published` の行は完了した公開の履歴であって、回収の対象ではない。

**確認の本文には、Immich が無傷なことと戻らないことの両方を書く。** 片方だけだと
読み違える。

### 画面に出す言葉

**内部の名前をそのまま出さない。**

| 内部 | 画面 |
| --- | --- |
| ボリューム / デバイス | カード |
| 転送先 / 宛先 | 送り先（または宛先そのものの名前） |
| ジョブ | 進行中の作業 / 作業の履歴 |
| 結合 / マージ | つなぐ |
| 承認待ち | 確認 |
| 破棄 | これは別々（＝別々の動画として扱う） |
| プロファイル | カメラの種類 |

### 守ること

- 破壊的でない操作（スキャン、プレビュー）は確認なしで即座に実行する
- 不可逆な操作（アップロード、既存アセットの変更）は**対象件数・合計サイズ・宛先名**を出して確認を取る。宛先を取り違えたまま送ると取り消せない
- エラーは英語のスタックトレースでなく、**何が起きて次に何をすべきか**を日本語で示す
- 進捗は必ずファイル名と件数（`12 / 87 件`）で示す。**取り込みと送信で同じ形にする** ——
  件数・いま扱っているファイル名・バイト・速度・残り時間。**長くかかる作業で進捗を
  出さないものを残さない**（送信は 71 GB を 6 分かけるので、出さないと動いているのか
  止まっているのか分からない）
- **色と形だけで意味を伝えない。** 印を出すなら凡例を添える。**写真の一覧は宛先ごとの
  状態の印を持たない** —— `GET /media` は行ごとの宛先ごとの状態を返さないので行ごとに
  違う印は描けず、いまの状態は絞り込みのチップが名乗る（凡例も置かない）
- **時刻には必ず印を添える**（「（JST）」など）。印の無い数字は、どの時計のものか
  画面から決められない。**システム時刻**（作業の履歴・確かめた時刻・観測した時刻）は
  `DEFAULT_TIMEZONE` へ**直して**出す —— 常に UTC で保存されるので、そのまま出すと
  利用者が毎回 9 時間を足して読むことになる。**撮影日時は直さない** —— 撮った土地の
  壁時計そのもので、直すと「現地で何時だったか」が読めなくなる（`force_offset` で
  復元した壁時計も同じ）。印は `media_file.captured_at_tz` から作り、**空なら
  `DEFAULT_TIMEZONE` とみなす**（`timezone_policy: none` の値は `+00:00` で保存
  されるので、オフセットだけでは本当に UTC で撮ったものと区別が付かない）。
  **日付だけの見出し**（写真の日ごとの区切り、送る画面の期間）には印を付けない。
  ゾーンは `GET /dashboard` が配る —— この 1 つのために画面ごとへ `/settings` を
  引かせない（利用者の要望と裁定、2026-08-28）
- **押せる領域は 44px 以上。** 狭い画面では、本文を潰さずにボタンを次の行へ落とす
- **外部の書体もスクリプトも読まない**（§14 の CSP）。書体は system-ui、アイコンはインライン SVG。**ライトとダークの両方で成立させる**

## 14. セキュリティ

### 特権の分離

初版仕様は 1 コンテナを `privileged: true` で動かしつつ「SD への書き込み経路が
コードに存在しない」と主張していたが、これは両立しない。アプリに RCE があれば
ブロックデバイスを直接開いて書けるし、rw で再マウントもできる。

そこで §5 のとおり `mountd` を分離し、fd 受け渡しで結合する。

| | mountd | app |
| --- | --- | --- |
| 特権 | `privileged: true` | `cap_drop: ALL`, `no-new-privileges`, rootfs read-only |
| コード量 | 小（デバイス列挙・マウント・fd 受け渡し・uevent 中継のみ） | 大 |
| ネットワーク到達性 | 無し（unix socket のみ） | Web UI を公開 |
| `/data` へのアクセス | **無し** | 有り |
| ファイルの中身 | 触らない | 読む |

アプリが侵害されても `CAP_SYS_ADMIN` は奪われない。ブローカーが受け付ける操作は
「allowlist された fs_type の USB ブロックデバイスを固定オプションで ro マウントし、
そのルートの読み取り専用 dirfd を返す」だけである。マウントオプションもマウント先も
アプリからは指定できず、`mountd` は書き込み可能な領域を一切持たない。

fd 受け渡しを選んだことで、特権側にコピー処理を持たせる案（アプリの指示で特権側が
`/data` に書く）で懸念された confused deputy が原理的に発生しない。

### その他

- ソースデバイスは常に `ro,nosuid,nodev,noexec` でマウントする。
- ソケットディレクトリは `mountd` 所有、アプリ側は read-only マウント。
  `SO_PEERCRED` でピアの UID を検証し、要求サイズ上限とタイムアウトを設ける。
- パス解決は dirfd 起点の単一構成要素のみ。`..`・絶対パス・シンボリックリンクを
  辿らない（`O_NOFOLLOW`）。
- プロファイル定義中のパスは `..`・絶対パス・シンボリックリンクを禁止し、
  解決後にマウントルートまたは `DATA_ROOT` の内側であることを検証する。
- 外部コマンドはすべて引数配列で起動する。シェル文字列を組み立てない。
- Immich API キーはログ・API レスポンス・エラーメッセージに出さない。
- 認証を有効にした場合、状態変更 API には Origin 検証と CSRF トークンを要求する。

### 残る攻撃面

攻撃者が用意したファイルシステムをカーネルの exfat ドライバでマウントする点は、
ブローカーを分離しても残る。`nosuid,nodev,noexec` と fs_type の allowlist で
被害を限定するが、カーネルの脆弱性そのものは防げない。信頼できない USB を
挿さない運用でカバーする。

## 15. テスト戦略

| 層 | 対象 | 方法 |
| --- | --- | --- |
| 単体 | `GroupDetector`、プロファイル判定、タイムスタンプ解決、衝突名の決定的系列、設定の優先順位、`input_digest`、`quick_fingerprint`、選択肢の提示規則 | pytest。純粋関数なので実 I/O 不要 |
| 単体 | `ImmichClient` の状態機械 | `respx` で HTTP をモック。`POST` 応答の `status`（created / duplicate / 応答喪失）× 初回 `checking`（accept / reject）× 中断位置の組合せを網羅し、`origin` が `created_by_us` になるのは「created をローカル commit できた場合」だけであることを確認する |
| 単体 | claim の排他 | 同じ `(destination, media)` を 2 つのジョブから同時に claim し、1 つだけが成功することを確認。`BEGIN IMMEDIATE` の CAS が効いているか |
| 単体 | 転送先の `target_epoch` | キーのローテートで履歴が引き継がれ、向き先の変更で引き継がれないことを確認 |
| 統合 | 取り込み → 検出 → 結合 → アップロード | fake ブローカー（一時ディレクトリの dirfd を渡す）。ffmpeg で生成した数秒の MP4 を使う |
| 統合 | crash consistency | §9.3 の各手順の直後にプロセスを落とし、reconciliation が回収することを確認。**手順の数だけケースを作る**。import と merge の両方で行う |
| 統合 | no-clobber 公開 | 公開直前に同名ファイルを外部から作り、上書きされないことと決定的な別名に落ちることを確認 |
| 統合 | キャンセル | 各段階でキャンセルし、中間生成物が残らないことを確認 |
| E2E | 主要画面の操作 | Playwright |
| 手動 | 実 USB での検出・マウント・取り込み・抜き差し | チェックリストとして併記 |

デバイス層を fake に差し替えられる設計にすることで、**USB 実機なしで CI が回る**。
fake ブローカーは実物と同じ `SCM_RIGHTS` プロトコルを話す（一時ディレクトリの
dirfd を渡す）ので、fd 受け渡し経路そのものもテストできる。

## 16. デプロイ

TrueNAS の Apps → Custom App（Docker Compose YAML）として導入する。

```yaml
services:
  mountd:
    image: ghcr.io/<owner>/mediaferry-mountd:<tag>
    restart: unless-stopped
    privileged: true
    volumes:
      - /dev:/dev                          # ホットプラグの伝播に必須
      - /lib/modules:/lib/modules:ro       # exfat モジュールの遅延ロード
      - <host-sock-path>:/run/mediaferry   # ソケット専用。mountd のみ RW

  app:
    image: ghcr.io/<owner>/mediaferry:<tag>
    restart: unless-stopped
    depends_on: [mountd]
    user: "<uid>:<gid>"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    volumes:
      - <host-sock-path>:/run/mediaferry:ro   # 読み取り専用。socket に connect のみ
      - <host-dataset-path>:/data
      - <tmp-volume>:/tmp
    environment:
      MEDIAFERRY_BIND_HOST: 0.0.0.0
      MEDIAFERRY_DEFAULT_TIMEZONE: <iana-tz>
    ports:
      - "<host-port>:8080"
```

`<...>` はデプロイ時に指定する。環境固有の値なのでリポジトリには記載しない。

共有マウントも `rshared` も使わない。両コンテナが共有するのはソケットのある
ディレクトリだけで、ボリュームの中身は fd で渡る。

`mountd` イメージには `util-linux`（`mount`, `blkid`）と exfat ユーティリティのみ。
`app` イメージには Python ランタイム、ffmpeg / ffprobe、ビルド済みフロントエンド資産。

**同じ `DATA_ROOT` を 2 つのプロセスに持たせない。** 起動時に
`DATA_ROOT/var/mediaferry.lock` を `flock(LOCK_EX|LOCK_NB)` で握り、**握れなければ
起動を拒否する**（`single_instance.py`）。移行も reconciliation も、握れてから
走らせる —— 後から起動した側の reconciliation は、有効期限内の `running` を
`interrupted` に倒して作業ディレクトリを消す。**待たずに断る** —— 待つ形にすると、
壊す側が「起動が遅い」だけに見える。**ファイルの存在では見張らない** ——
`flock` は開いたファイル記述に紐づくので、落ちれば OS が解放する。

## 17. リポジトリ構成

**環境固有の値を一切含めない**方針で書いてある。

```
.
├── README.md
├── compose.yaml                # サンプル。実値はプレースホルダ
├── protocol/                   # ブローカープロトコル定義（両者が参照）
├── app/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── src/mediaferry/
│   │   ├── core/               # ドメインロジック（純粋）
│   │   │   ├── profiles/       # ビルトインプロファイル定義
│   │   │   ├── grouping.py
│   │   │   ├── timestamps.py
│   │   │   ├── selection.py
│   │   │   ├── fingerprint.py
│   │   │   └── naming.py
│   │   ├── adapters/           # 副作用の境界
│   │   │   ├── broker_client.py
│   │   │   ├── publisher.py    # ArtifactPublisher
│   │   │   ├── ffmpeg.py
│   │   │   └── immich.py
│   │   ├── db/
│   │   ├── jobs/
│   │   ├── api/
│   │   └── settings.py
│   └── tests/
├── mountd/
│   ├── Dockerfile
│   ├── src/
│   └── tests/
└── web/                        # React + TS + Vite
```

`docker/` は `.chezmoiignore` に追加する。ホームへ展開する対象ではないため。

## 18. リスクと、その対処

| # | 項目 | 内容 | 対処 |
| --- | --- | --- | --- |
| 1 | fd 受け渡し | **実機で確かめてある。** コンテナ間の `SCM_RIGHTS`、別 mount namespace の dirfd への `os.listdir` / `dir_fd=` 付き `os.open`、detached マウントによる `..` の固定を実 exfat デバイスで確認した | 詳細は [`history/phase0-findings.md`](history/phase0-findings.md) |
| 2 | 巨大ファイルのアップロード | **実機で確かめてある。** 内部エンドポイント経由で 28.36 GiB を完走した（201 created、84.5 秒、343.82 MiB/s）。送信バイト数とサーバ側のサイズが入力と完全一致し、RSS の増分は 0.00 B だった。公開 URL（CDN 経由）は 622 MiB で 502 になるため §12.3 の分離が必須 | 詳細は [`history/phase0-findings.md`](history/phase0-findings.md) |
| 3 | Immich API の互換性 | **対象版 v3.1.0 で確かめてある。** サーバインスタンス ID は非公開のため、`remote_user_id` を向き先の変化を検知する guard として観測する（同一性ではない。§8）。`deviceAssetId` は資産応答に無いため、自作判別は応答の `status` と初回 `checking` の結果で行う。checksum は base64 に統一 | 詳細は [`history/phase0-findings.md`](history/phase0-findings.md) |
| 4 | DB のバックアップとリストア | SQLite が `failed_merges/` に代わる唯一の状態保持先になるため、失うと再構築が必要 | 再構築できる範囲・`.backup` による取得・マスター鍵を同じ搬出先へ置かないこと・リストア手順を [`backup.md`](backup.md) に定めた |
| 5 | 同時に複数デバイス | 2 枚のカードを同時に挿すケース | ジョブキューで直列化。`volume_presence` で個別に追跡 |
| 6 | 内蔵ストレージと SD の同時取り込み | Osmo は 2 ボリュームを同時に出し、同じ `library/dji-osmo/` に合流する | ファイル名が撮影時刻で一意なので実害は出ない見込み。衝突時は §9.3 の規則で処理する |
| 7 | 孤立ファイルの扱い | reconciliation で見つかった orphan を自動削除するとデータを失う経路になる | 削除せず画面に出し、ユーザの判断に委ねる |
| 8 | ボリューム同定の限界 | 複製カード・UUID 保持の復元を誤認しうる。read-only では永続マーカーを書けない | `identity_confidence` で自動信頼を抑制し、限界を UI に明示する |

## 19. 何が入っているか

**ここに書いた仕様はすべて実装されている。** 大きすぎて 1 本の計画に収まらない
ため段階に分けて作った。段階ごとの実装計画とレビュー記録は
[`history/`](history/README.md) にある。

**認証と CSRF が入るまで LAN へ公開しない**という制約で進めた。アプリは
非特権でも `/data` と Immich の API キーを持つため、それ以前の公開は危険だった。

`ArtifactPublisher` の契約は、取り込みを作った時点で **import と merge の両方を
想定して**固定した。後から派生物専用の crash model を足すと、取り込み側と別実装に
なる。

## 20. この仕様に至るまで

**この文書は現在の仕様だけを書く。** どう決めたか・何を試して採らなかったかは
別に置く。

| 知りたいこと | 見る場所 |
| --- | --- |
| **なぜこの設計なのか**（判断とその理由） | [`decisions.md`](decisions.md) |
| **どう作ったか**（実装計画、レビュー記録、変異試験） | [`history/`](history/README.md) |
| **実装の前に測った値** | [`history/phase0-findings.md`](history/phase0-findings.md) |
| **繰り返し出た誤りの型** | [`history/lessons.md`](history/lessons.md) |
