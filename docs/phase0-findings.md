# Phase 0 実測結果

実施日: 2026-08-17（一部未実施）
環境: TrueNAS 25.10.6 / kernel 6.12.99-production+truenas / Docker 28.3.1（ホスト）

`(要実施)` の行は TrueNAS ホストと実 Immich が必要なため未記入。埋めるまで
Phase 0 は完了しない。

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

**`..` の固定は合否である。** 侵害された app は「`..` を使わない」という規約を
無視できるので、抜けられる時点で仕様書 §14 の境界（RCE を脅威モデルに含む）が
成立しない。

Phase 1 の Scanner は、それに加えて単一のパス構成要素だけを `O_NOFOLLOW` で開き、
`..` と絶対パスを使わない。これは多層防御であって、単独の境界ではない。

## ② Immich API（仕様書 §18-3）

`immich_probe.py --write --cleanup` の終了ステータス: `(要実施)` ← **一次判定**

| 項目 | 結果 |
| --- | --- |
| 対象 Immich バージョン | `(要実施)` |
| サーバインスタンス同定に使う値 | `(要実施)`（endpoint + field。version / licensed は不可） |
| 識別子が再起動をまたいで安定しているか | `(要実施)` |
| 認証ユーザ ID の取得元 | `(要実施)` |
| **`x-immich-checksum` の encoding** | `(要実施)`（Task 10 に渡す） |
| **`bulk-upload-check` の checksum encoding** | `(要実施)`（ヘッダと異なりうる） |
| `bulk-upload-check` の action 語彙 | `(要実施)` |
| `bulk-upload-check` が既存資産 ID を返すか | `(要実施)` |
| `deviceAssetId` の読み戻し | `(要実施)` |
| 後片付けの成否 | `(要実施)` |

判定:

- `upload_destination` の同一性を何で構成するか（取れない場合の代替と移行 UX）
- `bulk-upload-check` が資産 ID を返さない場合、§9.10 の再開設計をどう変えるか
- `deviceAssetId` を読めない場合、既存資産の日時補正を全て手動承認にするか

## ③ 巨大ファイルのアップロード（仕様書 §18-2）

`large_upload.py` の終了ステータス: `(要実施)` ← **一次判定**

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
