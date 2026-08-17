# mediaferry 引き継ぎ資料

最終更新: 2026-08-18
ブランチ: `feat/mediaferry`（`main` から 51 コミット）

このファイルは、別セッションが作業を引き継ぐための出発点。
**まずここを読み、次に `design.md` §20 と該当フェーズの計画を読む。**

---

## 1. 現在地

**Phase 1 は実装・検証とも完了。次は Phase 2（結合）の計画づくりから。**

| Phase | 内容 | 状態 |
| --- | --- | --- |
| 0 | スパイク。未検証項目の実測とブローカーの最小実装 | **完了** |
| 1 | 基盤 + 取り込み。`ArtifactPublisher` / `Reconciler` / DB スキーマ / scan / import / API | **完了**（実 USB の確認だけ残り） |
| 2 | 結合 | 未着手 |
| 3 | Immich 同期（転送先プロファイル、状態機械） | 未着手 |
| 4 | Web UI | 未着手 |
| 5 | 汎用化（Canon、プロファイル編集 UI、複数デバイス） | 未着手 |

### 検証状態

```
uv run pytest                  411 passed
uv run pytest -m needs_root      1 passed   ← detached mount の実証
uv run ruff check .            All checks passed
uv run ruff format --check .   87 files already formatted
```

`docker restart` や電源断に相当する試験は、§9.3 の手順 11 段すべてで子プロセスを
`os._exit` で落として import と merge の両方を回収できることまで確認済み。

### 残っていること

**実 USB での手動確認（`phase1-manual-checklist.md` の 11 項目）だけ未実施。**
開発コンテナ（入れ子の非特権 LXC）ではマウントが AppArmor に阻まれるので、
TrueNAS ホストで実行する必要がある。

特に **11 番（mtime の解釈の実測）は実装の前提の確認**なので、Phase 2 へ進む前に
済ませたい。前提が崩れていれば `timestamps.py` の `_wall_clock` と
`publisher._collision_stamp` を直す必要がある。

---

## 2. 成果物の場所

| ファイル | 内容 | 追跡 |
| --- | --- | --- |
| `docker/mediaferry/docs/design.md` | **設計仕様書。正本。** | ✅ |
| `docker/mediaferry/docs/phase1-plan.md` | Phase 1 の実装計画。**実行済み**。実装との差分は都度書き戻してある | ✅ |
| `docker/mediaferry/docs/phase1-backup.md` | バックアップとリストア、再構築できる範囲（§18-4） | ✅ |
| `docker/mediaferry/docs/phase1-manual-checklist.md` | 実 USB での確認手順 | ✅ |
| `docker/mediaferry/docs/phase0-findings.md` | Phase 0 の実測結果と設計への反映 | ✅ |
| `docker/mediaferry/docs/HANDOFF.md` | このファイル | ✅ |
| `docker/mediaferry/{protocol,mountd,app,spikes}/` | 実装 | ✅ |

**`docs/superpowers/` は `~/.gitignore_global` で除外されている。** 重要なものは
`docker/mediaferry/docs/` に置くこと。

---

## 3. 実測で覆った設計判断（蒸し返さないこと）

Phase 0 の価値はここにある。**いずれも「実際に動かして」判明したもの**で、
理屈で戻すと同じ穴に落ちる。根拠は `phase0-findings.md`。

| 判断 | 理由 |
| --- | --- |
| **マウントは detached にする**（`mount` → `open_tree(OPEN_TREE_CLONE)` → `MNT_DETACH`） | 通常マウントの dirfd は `openat(dirfd, "..")` で親へ抜け、**親ディレクトリのファイルを実際に読めた**。規約では RCE を脅威モデルに含む §14 の境界を満たせない |
| **結合の検証にファイルサイズの単純比較を使わない** | `-c copy` は DJI の `dbgi`（ジャイロ、10.3 Mbps）等を落とすので、正常な結合でも **11.4% 縮む**。旧条件（Σ パートと ±1%）では全ての結合が不合格になっていた |
| **`remote_user_id` は同一性ではなく向き先変化の guard** | Immich v3.1.0 はサーバインスタンス ID を公開していない。`/api/server/about` は version/build のみで同版の全サーバで一致する |
| **`origin` は `status: created` を commit できた場合だけ `created_by_us`** | `deviceAssetId` が資産応答に無い。初回 `checking` が `accept` でも自作の証明にならない（間に別クライアントが割り込みうる） |
| **`isTrashed` を無視しない** | Immich のゴミ箱にある資産も重複として再アップロードを弾く |
| **接続先 URL と表示用 URL を分ける** | 公開 URL（Cloudflare 経由）では 622 MiB で 502。内部経路なら 28.36 GiB が 84.5 秒で完走 |
| **USB の `serial` を一意な識別子にしない** | Linux ガジェットの既定値 `123456789ABCDEF` だった。機体固有の文字列は `product` 側 |
| **空の `DCIM` でも正当なボリューム** | Osmo の内蔵ストレージは `DCIM` を持つが空だった |
| **checksum は base64 に統一** | `bulk-upload-check` は hex/base64 両方を受理するが `x-immich-checksum` は base64 |

### レビューで覆った設計判断

| 判断 | 理由 |
| --- | --- |
| **SQLite に行ロックは無い。`BEGIN IMMEDIATE` + 条件付き UPDATE（CAS）で claim する** | `SELECT ... FOR UPDATE` は存在しない。`UNIQUE` は行の重複作成しか防げない |
| **`selection_rule` は不変。再試行で上書きしない** | 上書きすると選択の根拠が失われ、claim が安全条件しか見なくなって古い派生物を送る |
| **claim 時に評価するのは「選べる場面」ではなく「その根拠が今も成立しているか」** | 「まだ採用していない derived」を条件にすると、採用して enqueue した瞬間に自分自身が条件を満たさず**必ず拒否される** |
| **宛先の版（`destination_revision` / `target_epoch`）を固定する** | 可変の宛先行に履歴を直結すると、キュー投入後に API キーを編集したとき**確認画面で示した宛先と違う先へ送られる** |
| **送信前に `/api/users/me` で向き先を再確認する** | 宛先を編集しなくても DNS・プロキシ・Immich 本体の差し替えで同じ URL の先が変わる |
| **API 呼び出しは redirect を追わない** | `x-api-key` はカスタムヘッダなので cross-origin redirect でも剥がれず、鍵が外部へ漏れる |
| **API キーは AEAD で暗号化。「封筒暗号化」とは呼ばない** | マスター鍵を `DATA_ROOT` の外（環境変数）に置けば DB/backup 単体の流出には効く。ただし credential ごとの DEK は挟まないので envelope ではない |

### Phase 1 で確定した契約（蒸し返さないこと）

**Phase 2 の結合は、これらをそのまま使う。** derived 専用の別実装を作ると、
結合物だけが回収不能になる。

| 判断 | 理由 |
| --- | --- |
| **公開は `ArtifactPublisher` の 11 手順を通す。import と merge で同じ** | 手順 6 までで落ちれば作業がなかったことになり、7 以降なら永続情報だけで完遂する。この境界を共有しないと片方だけ回収不能になる |
| **公開は `os.link`。`os.replace` を使わない** | EEXIST で失敗する no-clobber 性が要る。既存を黙って上書きすると、再フォーマットで連番が再利用されたカードが過去の取り込みを消す |
| **リースの確認と `staged` への遷移を 1 つの `BEGIN IMMEDIATE` に入れる** | 分けると隙間にキャンセルが commit でき、「キャンセル済みと表示した後に公開される」経路が残る |
| **`staged` 以降の失敗を「取り込み失敗」として扱わない**（`PublishInterrupted`） | ファイルは検証済みで公開に必要な情報は永続化済み。`source_entry` を failed に戻すと、次のスキャンで新規と判定されて二重に取り込む |
| **DB 接続はスコープごとに 1 本。同時共有しない** | トランザクションは接続に属していてスレッドには属さない。API の書き込みが publisher の `BEGIN IMMEDIATE` に混ざる。`check_same_thread=False` は必要（作るスレッドと使うスレッドが違う）だが、共有してよいという意味ではない |
| **ジョブは選択した瞬間の presence を params に持つ** | `volume_instance_id` だけを渡して実行時に最新の presence を選ぶと、抜き差しでカードが入れ替わっていても現在値から正しい `expect` を組み立て、ブローカーの TOCTOU 検証をすり抜ける |
| **ジョブは固定したプロファイルリビジョンを読む** | 現行を読み直すと、キューで待っている間の編集で、確認画面と違う規則で処理される |
| **`VolumeObservation` は接続の同一性であって媒体の同一性ではない** | mountd の `generation` は観測した集合の指紋が変わったときだけ進む。polling の合間に同じ UUID・容量のカードが同じノードで差し替わると据え置きになる。**開いた dirfd を使い回す根拠にできない**（判定は毎回開き直し、ジョブの handle は release で閉じる） |
| **プロファイルの一致度とボリュームの同定確度を混ぜない** | 中身が DJI のファイルであることは「前回と同じカードだ」の証明にならない。混ぜると同じ UUID の別カードが `trusted_at` を引き継ぐ |
| **孤立ファイルは報告するだけ。削除しない** | 自動削除はデータを失う経路になる |
| **停止は走っているジョブの完了を待つ** | `to_thread` のハンドラは task の cancel では止まらない。待たずに接続と dirfd を閉じると、コピー中のスレッドから見て資源が突然消える。timeout を付けて worker を cancel しても同じ |

---

## 4. 環境の癖と罠

### TrueNAS ホスト

- **共有データセット** `/mnt/ssd/develop-server/` が開発コンテナとホストの両方から
  同じパスで見える。ソースは `/mnt/ssd/develop-server/mediaferry/` に配置済み
  （`git archive HEAD docker/mediaferry | tar -x -C ... --strip-components=2` で更新）
- **既定シェルは zsh**。以下は bash と違うので手順書に書かない
  - 行内コメント（`cmd  # 説明`）が**無効**。`#` 以降が引数として渡る
  - `tail -1` が `option used in invalid context` になる。`tail -n 1` を使う
  - `${PIPESTATUS[0]}` が展開されない。パイプを避けてリダイレクトで受ける
- Immich の内部エンドポイントは `http://172.16.100.21:80`（環境固有。リポジトリには書かない）

### 開発コンテナ（この LXC）

- **入れ子の非特権 LXC。AppArmor がマウントを阻む。** privileged コンテナ内でも
  `mount` は通らないので、マウント絡みの検証は TrueNAS ホストで行う
- `unshare -Urm` は使える。`needs_root` のテストはこれで通る
- **`/dev` を汚さないこと。** 過去に `mount -o loop` を privileged コンテナで走らせて
  `/dev/loop0`〜`loop1048575` を 104 万個作り、`docker run --privileged` が
  spec サイズ超過で動かなくなった。`/dev` は tmpfs なので再起動で戻る

### テストと lint

- **`ruff format` は Markdown 内のコードブロックも整形する。** `docs/` は
  `extend-exclude` で対象外にしてある。外すと仕様書と計画そのものが書き換わる
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付ける。** 変異の前後でバイト数が
  変わらない書き換え（`>` を `<` にする、`a or b` を `b or a` にする等）では、
  `.pyc` の無効化条件（mtime の秒＋サイズ）をすり抜けて古いバイトコードが使われ、
  変異が効いているかを読み違える
- `os.listdir(-1)` は **EBADF にならずカレントディレクトリを返す**。閉じた
  `VolumeHandle` の `dirfd` は -1 なので、「閉じた」ことを `pytest.raises(OSError)`
  で確かめられない。`handle.closed` を見る

### agmsg（codex とのやり取り）

- **watcher が落ちることがある。** `delivery.sh status` の
  `watch processes: N alive` を確認する。0 なら Monitor を起動し直す
- `history.sh` は全件を渡すと `sqlite3: Argument list too long` で落ちる。
  **件数を指定する**（`history.sh chezmoi claude-code 3`）
- **長文メッセージは配送が遅れる。** 変更点の全文を貼らず、ファイルを読ませて
  §21 の変更履歴を指す方が確実
- codex がレビューする版が**編集の反映前**だったことがある。送る前に
  仕様書の mtime を確認するか、送信後に「最新版か」を確認させる

---

## 5. 次にやること

### 手順

1. **`phase1-manual-checklist.md` を TrueNAS ホストで実行する。** 特に 11 番
   （mtime の解釈）は実装の前提の確認なので、結果を `phase0-findings.md` に残す
2. Phase 2（結合）の実装計画を書く。範囲は `design.md` §20 の 2 行目と §9.7
3. 計画を codex にレビューさせる（下記）

### Phase 2 の範囲（`design.md` §20）

> グループ検出、結合、検証、§10 の選択肢規則。公開は Phase 1 の
> `ArtifactPublisher` をそのまま使う。
>
> 完了条件: 分割動画が結合され、検証結果と選択肢が API で取れる。

Phase 1 側で既に用意してあるもの:

- `merge_group` / `merge_member` のスキーマ（supersede の不可逆性、active の
  両方向 trigger まで含む）
- `ArtifactPublisher` の `kind="merge"` 経路（crash 試験も通っている）
- `MediaProbe`（`duration_seconds` は §9.7 の境界判定が使う。失敗を 0 秒に
  丸めていない）
- プロファイルの `merge` 節（`tolerance_seconds` / `min_part_size_gib` /
  `sequence_pattern` / `output_name` / `keep_streams`）

### 作業の作法

1. 各タスクは「失敗するテストを書く → 失敗を確認 → 最小実装 → 通ることを確認 →
   コミット」で完結させる
2. **変異試験のステップを省かない。** Phase 1 では、計画のテストが「通っては
   いるが実装の判断を検証していない」箇所が **30 件以上**見つかった。特に多い
   パターンは次の 3 つ:
   - 別の分岐で先に落ちていて、狙いの分岐を一度も通っていない
   - 結果が同じになる筋書きしか試していない（差が出るのは「読んだバイト数」
     「試した名前の数」のような量だけ）
   - 順序規則を、たまたま同じ順になるデータで試している
3. **検出できない変異は、検出できないことを計画に書く。** Phase 1 では
   `claim_next` の CAS 条件（`BEGIN IMMEDIATE` が claimer を直列化するので到達
   しない）のように、構造的にテスト不能な保険が複数あった
4. 計画から外れる判断をしたら、その場で計画側にも書き戻す
5. 詰まったら codex に相談する

### レビューの依頼先

```bash
bash ~/.agents/skills/agmsg/scripts/send.sh chezmoi claude-code codex "<本文>"
bash ~/.agents/skills/agmsg/scripts/history.sh chezmoi claude-code 3
```

codex は `chezmoi` チームに参加済み。落ちていたら
`bash ~/.agents/skills/agmsg/scripts/spawn.sh codex codex --project "$(pwd)" --team chezmoi`

---

## 6. 開発コマンド

```bash
cd docker/mediaferry
uv sync --all-packages        # --all-packages が必須。素の sync ではメンバーが入らない
uv run pytest
uv run pytest -m needs_root   # ユーザ名前空間が使える環境でのみ通る
uv run ruff check .
uv run ruff format --check .
```

アプリの起動は `python -m mediaferry`（`BIND_HOST` の既定は `127.0.0.1`）。
スパイクの再実行は TrueNAS ホストで。手順は `phase0-findings.md` と
`compose.spike.yaml` のコメントにある。

---

## 7. 持ち越している判断

| 項目 | 状況 |
| --- | --- |
| 実 USB での確認 | `phase1-manual-checklist.md` に手順を用意済み。未実施 |
| mtime の解釈 | `timestamps.py` は「カードの時刻欄に UTC オフセットが無い」前提。チェックリスト 11 番で実測する |
| 5 パート連続録画（70 GiB 級）のアップロード | 28.36 GiB は完走した。同じ経路で扱える見込みだが未実測。タイムアウトは比例して伸びる |
| Canon EOS 70D のプロファイル | Phase 5。カードリーダー経由の UMS のみ対応と決定済み（PTP はスコープ外） |
| 認証を既定 off のままにするか | ユーザの判断で off。`BIND_HOST` の既定を loopback にし、認証無効で非 loopback にバインドしていたら警告する緩和のみ |
| Phase 1〜3 を配布可能リリースにしない | 認証と CSRF が入る Phase 4 より前に LAN へ公開しない |
| SSE（`GET /events`） | Phase 4 の Web UI と一緒に入れる。Phase 1 は `GET /api/jobs/{id}/events?after_seq=` のポーリング |

---

## 8. 作業の進め方（この案件で有効だったこと）

- **理屈で結論を出さず、測る。** dirfd の `..` 脱出も、結合の 11.4% 欠損も、
  `os.listdir(-1)` がカレントディレクトリを返すことも、実際に動かして初めて
  分かった。どれも実装後に気づいたら手戻りが大きい
- **判定は終了ステータスで行う。** 出力を読んで「動いていそう」で通さない
- **テストが素通りしていないか変異試験で確かめる。** Phase 1 では毎タスクで
  実施し、30 件以上の穴が見つかった。**「テストが通った」は「実装が正しい」の
  証拠にならない**
- **計画のテストも疑う。** Phase 1 では計画側のテストに 5 件のバグがあった
  （狙いと違う制約で落ちていた、前提を満たさない値を使っていた、不変 trigger を
  発火させる無意味な行があった、fixture がクライアント側だけを差し替えて
  サーバと不整合になっていた、ビルトインが 1 つしか無い前提を見落としていた）
- **codex のレビューは鵜呑みにしない。** 設計で反論したのは 2 点だけだったが、
  その 2 点（SHA-256 の追加、認証の必須化）は退けて正解だった。逆に
  「暗号化は無意味」という自分の理屈は誤りで、指摘を受けて撤回した
