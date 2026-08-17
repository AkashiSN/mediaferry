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

### TrueNAS ホストでの実測（要実施）

`spike_cli` の終了ステータス: `(要実施)` ← **これが一次判定**

| 項目 | 結果 |
| --- | --- |
| 列挙できたボリューム（device_node / fs_uuid / label） | `(要実施)` |
| 期待ボリューム数と実際 | `(要実施)` |
| 全ボリュームを open できたか | `(要実施)` |
| 全ボリュームで実ファイルを読めたか | `(要実施)` |
| 許可外 UID が `unauthorized` で拒否されたか（DAC でなく SO_PEERCRED で） | `(要実施)` |
| `MOUNTD_SOCKET_GID` が必要だったか | `(要実施)` |
| Docker の userns-remap | `(要実施)` |
| **dirfd の `..` がボリュームルートに固定されたか** | `(要実施)`（**不可なら Phase 0 未完了**） |
| ソースへの新規作成が拒否されたか | `(要実施)`（errno） |
| 既存ファイルの `O_WRONLY` が拒否されたか | `(要実施)`（errno） |
| ソケットの unlink が拒否されたか | `(要実施)`（errno） |
| app の実効ケーパビリティ / NoNewPrivs / euid | `(要実施)` |

判定: `(要実施)`

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
