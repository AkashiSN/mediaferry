# Phase 0 実測結果

実施日: 2026-08-17
環境: TrueNAS 25.10.6 / kernel 6.12.99-production+truenas / Docker 28.3.1（ホスト）
Immich: v3.1.0

進捗: ① fd 受け渡し **解消** / ② Immich API **解消** / ③ 巨大ファイル 実施中。
`(要実施)` の行が残っている間は Phase 0 を完了としない。

## 事前に確定した事実（開発環境で実測済み）

`..` 脱出の有無をユーザ名前空間（`unshare -Urm`）で測定した。これが detached
マウント方式を採る根拠になっている。

| 方式 | `openat(dirfd, "..")` |
| --- | --- |
| 通常のマウント | **親へ脱出。親ディレクトリのファイルを実際に読めた** |
| `open_tree(OPEN_TREE_CLONE)` | ルートに固定 |
| `fsmount()` | ルートに固定 |
| クローン後に元を `MNT_DETACH` | クローンは生存、`..` も固定のまま |
| ファイルシステム内部（`DCIM` から `..`） | 正常にルートへ戻れる。`../..` はルート止まり |

同じことを `mountd/tests/test_nsmount.py::test_detached_clone_pins_dotdot_and_survives_detach`
（`-m needs_root`）で自動化してあり、PASS を確認済み。

## ① コンテナ間 fd 受け渡し（仕様書 §18-1）

### 開発環境で確認済みの部分

USB 実機なしで検証できる範囲は済んでいる。

| 項目 | 結果 |
| --- | --- |
| コンテナ間の `SCM_RIGHTS` による dirfd 受け渡し | **成功** |
| 別 mount namespace の dirfd への `os.listdir` | **成功** |
| `dir_fd=` 連鎖でのファイル読み出し | **成功**（内容一致） |
| 同じ dirfd の繰り返し列挙 | **成功** |
| ソケットディレクトリを `:ro` でマウントしても `connect` できるか | **できる**（読み取り専用マウントが `MAY_WRITE` を拒むのは通常ファイル・ディレクトリ・シンボリックリンクだけで、ソケット inode は対象外） |
| 通常ディレクトリの fd に対する `..` 判定 | **False を返す**（ガードが正しく機能） |
| `mediaferry-mountd` / `mediaferry-app` イメージのビルド | **成功** |
| 両イメージでのモジュール import と `mount` / `blkid` の存在 | **成功** |
| `compose.spike.yaml` の起動と mountd のソケット生成 | **成功**（約 2 秒） |
| `MOUNTD_SOCKET_GID` による chown | **成功**（`root:10001 srw-rw----`） |
| 非 root の app（uid 10001）からの `connect` | **成功** |
| 許可外 UID（10002 / gid 10001）の拒否 | **成功**。`exit=4` と `unauthorized: peer uid is not allowed`。DAC ではなく `SO_PEERCRED` で弾かれている |
| USB 無しのときの判定 | **正しく FAIL**（`exit=2`、`listed=0`） |

USB 実機が無い状態での配線確認で、次の 2 つの欠陥を見つけて直した。

- **`security_checks` を一度も実行していないのに PASS と表示していた。** `security_ok`
  の初期値が `True` で、ボリューム 0 件だとループを通らずそのまま通過していた。
  全体判定は FAIL になるので誤った合格にはならないが、この行を findings に
  転記すると「セキュリティ確認済み」と記録されてしまう。検査した件数を数え、
  `checked > 0 かつ checked == opened` を条件に加えた。
- **ブローカーに拒否されたときトレースバックで落ちていた。** 結果の解釈が
  曖昧になるので `BrokerError` を捕まえ、`exit=4` と明示メッセージにした。
  connect 自体の失敗（`exit=1`、DAC で弾かれた疑い）と区別できる。

### TrueNAS ホストでの実測（2026-08-17 実施）

`spike_cli` の終了ステータス: **0（PASS）** ← 一次判定
`app-wrong-uid` の終了ステータス: **4**（`SO_PEERCRED` による拒否）

| 項目 | 結果 |
| --- | --- |
| 列挙できたボリューム | `/dev/sdj1` exfat `Pocket4` uuid=4356-50A7 116,047,982,592 B（内蔵）<br>`/dev/sdk` exfat `SD_Card` uuid=26B1-2FD6 512,711,688,192 B（microSD） |
| 期待ボリューム数と実際 | 2 / 2 |
| 全ボリュームを open できたか | 可（2/2） |
| 全ボリュームで実ファイルを読めたか | 可（2/2） |
| 許可外 UID が `unauthorized` で拒否されたか | **可**。`exit=4` かつ `unauthorized: peer uid is not allowed`。DAC ではなく `SO_PEERCRED` で拒否されている |
| `MOUNTD_SOCKET_GID` が必要だったか | **要**。指定なしでは非 root の app が接続できない |
| Docker の userns-remap | 無効（app の euid が指定どおり 10001 になった） |
| **dirfd の `..` がボリュームルートに固定されたか** | **可（2 ボリュームとも PASS）** |
| ソースへの新規作成が拒否されたか | 可（errno=30 = EROFS） |
| 既存ファイルの `O_WRONLY` が拒否されたか | 可（errno=30 = EROFS） |
| ソケットの unlink が拒否されたか | 可（errno=30 = EROFS） |
| app の実効ケーパビリティ / NoNewPrivs / euid | `CapEff=0000000000000000` / `NoNewPrivs=1` / `euid=10001` |
| USB デバイスの識別情報 | vid=2ca3 pid=0020 serial=`123456789ABCDEF` |

判定: **仕様書 §5 の fd 受け渡し方式を採用する。** detached マウントが実 exfat
デバイスで機能し、`..` の脱出が塞がっていることを実測で確認した。

**`..` の固定は合否である。** 侵害された app は「`..` を使わない」という規約を
無視できるので、抜けられる時点で仕様書 §14 の境界（RCE を脅威モデルに含む）が
成立しない。

Phase 1 の Scanner は、それに加えて単一のパス構成要素だけを `O_NOFOLLOW` で開き、
`..` と絶対パスを使わない。これは多層防御であって、単独の境界ではない。

### 仕様に反映すべき発見

**A. USB の `serial` は機体固有ではない**

`serial` は `123456789ABCDEF` で、Linux ガジェットの既定値だった。機体を識別する
文字列は `product`（`OsmoPocket4-ANGZP3K002QM4K`）側にある。Osmo が 2 台あれば
`serial` は衝突する。

仕様書 §8 の `source_device` は `serial` を持つが、これを一意な識別子として
扱ってはならない。`product` 文字列も併せて保持し、デバイス同定は
`(vid, pid, product, serial)` の組で行う。なお `volume_instance` の同定は
`(fs_uuid, fs_type, size_bytes)` で行う設計なので、こちらへの影響は無い。

**B. 内蔵ストレージの `DCIM/` が空でも正当なボリュームである**

`/dev/sdj1`（内蔵 116GB）は `DCIM/` と `MISC/` を持つが、`DCIM/` に中身が無い
（まだ内蔵に録画していない）。仕様書 §6 の `require.min_matching_files: 1` は
「`filename_pattern` に一致する実ファイルが 1 件以上あること」を確定の必要条件に
しているため、**この正当なボリュームが `dji-osmo` にマッチしない**。

このままだと `generic-dcim` に落ち、`require` を満たさなければ「対象外」になる。
後で内蔵に録画したときだけマッチするので、信頼登録が別プロファイルで行われて
しまう。

対処（Phase 1 で反映する）:

- `require.roots` を満たし、かつ `hints`（`usb_ids` / `volume_labels`）に一致する
  ボリュームは、`min_matching_files` を満たさなくても**暫定マッチ**として扱う。
  ただし確度は `low` とし、自動取り込みの対象にはしない
- 一度でも `filename_pattern` に一致するファイルを観測したら確度を `high` に上げる
- 空のボリュームを「対象外」ではなく「対象だが中身が無い」と表示し、ユーザが
  区別できるようにする

**C. `.LRF` プロキシ動画は無視できない大きさ**

`DJI_20260808125404_0002_D.LRF` が 1,960,378,714 B（1.83 GiB）あった。MP4 と対で
存在するため、除外しないと転送量がおよそ 1.11 倍になる。プロファイルの
`scan.extensions` で除外する設計は妥当。

**D. 分割動画の実データが揃っている**

`DJI_20260808125404_0002` 〜 `0006` が連続録画の分割とみられる（0002〜0005 が
約 16.0 GiB、0006 が 10.8 GiB）。最大の MP4 は 17,187,041,745 B = 16.01 GiB で、
仕様書が前提にしている ~16GiB での自動分割と一致する。Task 10 の 32GiB 試験用
ファイルは 0002 と 0003 を結合して作れる。

`MISC/OP-041.db`（SQLite）と `MISC/THM/`（`.SCR` / `.THM` サムネイル）も存在するが、
`scan.extensions` に含まれないため除外される。


## ② Immich API（仕様書 §18-3）

`immich_probe.py --write --cleanup` の終了ステータス: **0（PASS）** ← 一次判定
全 10 命題が PASS。`RESULT: PASS — 仕様書 §18-3 は解消`

| 項目 | 結果 |
| --- | --- |
| 対象 Immich バージョン | **v3.1.0**（sourceCommit 8aa95c6） |
| サーバインスタンス同定に使う値 | **無い。** `/api/server/about` が返すのは version / build / sourceCommit で、いずれもリリースに紐づき同版の全サーバで一致する。`/api/server/config` にも `instanceId` は無い |
| 転送先の同定に使う値 | **`/api/users/me#id`（認証ユーザ UUID）** |
| 識別子が再起動をまたいで安定しているか | **可**。Immich 再起動の前後で一致 |
| **`x-immich-checksum` の encoding** | **base64**（201 created） |
| **`bulk-upload-check` の checksum encoding** | **hex / base64 のどちらも受理**。実装は base64 に統一する |
| `bulk-upload-check` の action 語彙 | `accept` / `reject`。reject 時は `reason: "duplicate"`, `assetId`, `isTrashed` を伴う |
| `bulk-upload-check` が既存資産 ID を返すか | **可**。`assetId` が返る |
| `POST /api/assets` の応答 | `{"id": ..., "status": "created"}` |
| `deviceAssetId` の読み戻し | **不可**。資産応答に欄が無い |
| 後片付けの成否 | 可（DELETE → 204） |

`GET /api/assets/{id}` が返すフィールド:

```
checksum, createdAt, duplicateId, duration, exifInfo, fileCreatedAt,
fileModifiedAt, hasMetadata, height, id, isArchived, isEdited, isFavorite,
isOffline, isTrashed, libraryId, livePhotoVideoId, localDateTime,
originalFileName, originalMimeType, originalPath, owner, ownerId, people,
resized, stack, tags, thumbhash, type, updatedAt, visibility, width
```

クライアントが送った値で残るのは `originalFileName` だけだった。

### 判定と設計への反映

**A. 転送先の同定は `remote_user_id` 単独で行う**

サーバインスタンス ID が無いため。`/api/users/me` の `id` は Immich の DB が
生成する UUID で、インストールごとに異なり再起動をまたいでも変わらないことを
実測した。`base_url` は同定に使わない（リバースプロキシやドメイン変更で変わる）。

残る穴は「DB を複製した 2 台のサーバで同じユーザ UUID」だが、その場合ライブラリの
中身も同一なので実害は小さい。同じ `remote_user_id` を以前と異なる `base_url` で
観測したら警告を出し、同一の転送先か新しい転送先かをユーザに確認させる。

**B. 自作資産の判別は `deviceAssetId` ではなく状態機械で行う**

`deviceAssetId` が読み戻せないので、次の 2 つを使う。

1. `POST /api/assets` の応答 `status`（`created` なら自分が作ったと確定）
2. 初回 `checking` の結果（`upload_record.first_check_result`）。`accept` だった
   後に `duplicate` になったなら自分のアップロードによるもの

最も危険な「ユーザが手動で時刻を直した古い資産を上書きする」ケースは、初回
`checking` が `reject` になるので `pre_existing` に分類され、自動補正の対象から
外れる。守りたいケースは守れる。

**C. checksum の encoding は base64 に統一する**

`bulk-upload-check` は両方受理するが、`x-immich-checksum` は base64 で成功した。
片方に揃えないと取り違えが起きる。Task 10 には
`--header-checksum-encoding base64 --bulk-checksum-encoding base64` を渡す。

## ③ 巨大ファイルのアップロード（仕様書 §18-2）

### 結合そのものの実測（副産物だが仕様の欠陥を 1 件見つけた）

`DJI_20260808125404_0002_D.MP4`（17,187,041,745 B）と
`DJI_20260808131923_0003_D.MP4`（17,186,388,142 B）を、仕様書 §9.8 と同じ
`ffmpeg -f concat -safe 0 -c copy -fflags +genpts` で結合した。

| 項目 | 結果 |
| --- | --- |
| 所要時間 | 8 分 11 秒（`speed=6.34x`。SD カードからの読み出しが律速） |
| 入力合計 | 34,373,429,887 B（32.01 GiB） |
| 出力 | 30,452,085,873 B（**28.36 GiB**、**−11.4%**） |
| duration | 3035.928708 秒 vs 入力合計 3036.033 秒（差 0.10 秒） |
| 映像フレーム数 | 72,789 vs 36,396 × 2 = 72,792（**3 フレーム**の欠損） |
| 連続性 | 0002=12:54:04、0003=13:19:23、0004=13:44:41 と各 1518 秒で完全に連続 |

**サイズが 11.4% 減る原因はストリームの脱落だった。**

| # | 種別 | タグ | ビットレート | 結合後 |
| --- | --- | --- | --- | --- |
| 0 | video hevc | `hvc1` | 79,924,667 | 保持 |
| 1 | audio aac | `mp4a` | 317,374 | 保持 |
| 2 | data | `djmd`（DJI メタデータ） | 11,336 | **脱落** |
| 3 | data | `dbgi`（DJI ジャイロ/デバッグ） | **10,306,940** | **脱落** |
| 4 | data | `tmcd`（タイムコード） | N/A | 保持 |
| 5 | video mjpeg | サムネイル | N/A | **脱落** |

`dbgi` の 10.3 Mbps × 3036 秒 ≒ 3.91 GB、`djmd` が 4.3 MB。実測の欠損
3,921,344,014 B とほぼ一致し、残差 5.5 MB はサムネイルとコンテナのオーバーヘッド。

**仕様書 §9.8 の「サイズ 結合後 ≈ Σ パート（許容誤差 1%）」は、正常な結合を
必ず不合格にする欠陥だった。** 実際に走らせなければ気づけなかった。検証条件を
次のように置き換えた。

- duration ±1 秒 × パート数（現行どおり。実測 0.10 秒）
- 映像・音声ストリームが揃っていること
- 映像フレーム数の差が継ぎ目あたり数フレーム以内
- **保持されたストリームのビットレートから算出した期待値**と ±2% 以内。
  上の例では (79,924,667 + 317,374) × 3036 ÷ 8 = 30,451,573,000 B に対し
  実測 30,452,085,873 B で**差 0.002%**

データトラックの脱落は**許容する**（ユーザ判断）。Immich は `dbgi` を使わないので
視聴上の損失はなく、転送量が 11% 減る。後から Gyroflow で手ブレ補正をかけたい
場合は `library/` のオリジナルを使う。脱落したストリームは `verification_json` に
記録して画面に出す。

### アップロード

`large_upload.py` の終了ステータス: `(要実施)` ← **一次判定**

2 パート結合の現実的なサイズは 28.36 GiB で、既定の下限 31 GiB を下回る。
`--min-size-gib 28` を明示して実行する。5 パートの連続録画（0002〜0006）を
結合すると 70 GiB 級になるが、今回の結果が通れば同じ経路で扱える見込み。
ただしタイムアウトは比例して伸びる。

| 項目 | 結果 |
| --- | --- |
| 試験したファイルサイズ | `(要実施)`（31 GiB 以上。下回る場合は理由を明記） |
| 送信バイト数 / ファイルサイズ | `(要実施)`（一致必須） |
| HTTP 結果 | `(要実施)` |
| 所要時間とスループット | `(要実施)` |
| RSS の baseline / peak / 増分 | `(要実施)`（増分の上限は `min(512MiB, size/10)`） |
| サーバ側のサイズ / 入力サイズ | `(要実施)`（一致必須） |
| 削除後に再び accept になったか | `(要実施)` |
| 前段プロキシの body size 上限 | `(要実施)` |

判定: 結合物を Immich に上げる / NAS のみに保持する。
後者なら §10 の eligibility をどう反転させるか（`role = derived` を自動対象から
外し、アクティブグループ member の `role = original` を対象に含める）を明記する。

## 計画からの逸脱

実装中に見つけた計画の不備と、その対処。

| 事象 | 対処 |
| --- | --- |
| Task 1 がワークスペース根に 3 メンバーを宣言してコミットするのに、Task 2 では `protocol` しか作らないため `uv sync` が解決できない | Task 1 の時点で 3 パッケージの骨組み（`pyproject.toml` と `__init__.py`）を作るようにした |
| 素の `uv sync` ではワークスペースメンバーが venv に入らず、テストが import に失敗する | `uv sync --all-packages` を使う。README に理由を明記 |
| src レイアウトのため ruff が自前パッケージをサードパーティ扱いし、import の並びが安定しない | 根の `pyproject.toml` に `[tool.ruff.lint.isort] known-first-party` を追加 |
