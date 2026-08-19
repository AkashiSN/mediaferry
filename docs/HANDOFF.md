# mediaferry 引き継ぎ資料

最終更新: 2026-08-19
ブランチ: **`phase5-generalization`**（`main` から 6 コミット。単独リポジトリ、計 140 コミット）
先頭は Phase 5 の Task 5（プロファイル編集 API）。**`main` へはまだマージしていない。**

このファイルは、別セッションが作業を引き継ぐための出発点。
**まずここを読み、次に `design.md` §20 と該当フェーズの計画を読む。**

---

## 1. 現在地

**Phase 0〜4 は実装・検証とも完了。** 実 Immich での確認も済んでいる
（2026-08-19、v3.1.0）。**Phase 5 は計画（レビュー 2 巡）と実装の Task 0〜5 が完了。**
**次は Task 6（`recompute_timestamps`）から。**

| Phase | 内容 | 状態 |
| --- | --- | --- |
| 0 | スパイク。未検証項目の実測とブローカーの最小実装 | **完了** |
| 1 | 基盤 + 取り込み。`ArtifactPublisher` / `Reconciler` / DB スキーマ / scan / import / API | **完了**（実 USB の確認だけ残り） |
| 2 | 結合。検出 / ffmpeg / 検証 / 公開 / 回収 / 選択肢 / API | **完了**（`phase2-plan.md` の 14 タスク） |
| 3 | Immich 同期（転送先プロファイル、状態機械、タグ、日時補正、複数宛先） | **完了**（`phase3-plan.md` の 14 タスク。レビュー 7 巡。**実 Immich でも確認済み**） |
| 4 | Web UI（認証・CSRF・SSE・サムネイル・8 画面・E2E） | **完了**（`phase4-plan.md` の 19 タスク。計画レビュー 1 巡 + 実装差分レビュー 1 巡） |
| 5 | 汎用化（Canon、プロファイル編集 UI、複数デバイス） | **実装中**（10 タスク中 6 つ完了。計画レビュー 2 巡は反映済み） |
| 6 | `UPLOAD_CONCURRENCY` の多重化、RAW/JPEG のスタッキング | **未着手**（Phase 5 から送った） |

### Phase 5 の進捗（`docs/phase5-plan.md`）

| Task | 内容 | 状態 | 変異試験 |
| --- | --- | --- | --- |
| 0 | ブローカー接続の再接続（`list_volumes` だけ再送） | **完了** | 8 中 6 検出 |
| 1 | `0010`（`auto_import_at` / `provisional`） | **完了** | — |
| 2 | `VolumeWatcher`（`AUTO_IMPORT=trusted` を効かせる） | **完了** | 17 中 13 検出 |
| 3 | `timestamp.source: exif` | **完了** | 11 中 10 検出 |
| 4 | ビルトイン `generic-dcim` / `canon-eos`、`regex` への移行 | **完了** | 12 中 12 検出 |
| 5 | プロファイル編集 API | **完了** | 10 中 10 検出 |
| 6 | `recompute_timestamps` ジョブ（`0011`） | **未着手** | |
| 7 | プロファイル編集 UI（設定画面） | **未着手** | |
| 8 | デバイス画面（信頼登録 UX、複数デバイス） | **未着手** | |
| 9 | 受け入れ（E2E）とドキュメント | **未着手** | |

**Task 6 が残るバックエンドの山。** 計画レビューの blocker 3（再計算の入力が
永続化されていない）と blocker 4（provenance）の両方がここに集中している。

### レビューで分かったこと（この案件の中核）

**codex のレビューは 11 巡回した**（Phase 3 に 7 巡、Phase 4 に 2 巡、Phase 5 の計画に 2 巡）。
巡ごとの詳細は各 `phaseN-plan.md` の「レビュー記録」にある。**傾向がはっきりしている。**

| 何を見せたか | 出てくるもの |
| --- | --- |
| **計画に埋め込んだコード**（Phase 3 の 1・2 巡目） | 機械的な欠陥ばかり（blocker 8 件ずつ）。**文書のコードは型検査も実行もできない**ので、毎巡同じ層が残る |
| **計画の範囲と契約**（Phase 4 の計画レビュー） | **足りないもの**（backend の状態が実在しない、Task が無い、E2E の土台が無い） |
| **実装差分**（Phase 3 の 3〜7 巡目、Phase 4 の実装レビュー） | **本当の層**（並行性、相手が値を選べる応答、実装した順序）と、**つながっていないもの**（API はあるが画面から呼べない） |

**直した箇所の周りをもう一度見せると、また出る。** Phase 3 は 5・6・7 巡目とも
blocker が 2 件ずつ出た。**巡を重ねても止まらない。** Phase 5 の計画も同じで、
1 巡目の blocker 4 件を直した 2 巡目に**新しい blocker が 3 件出た**（いずれも
1 巡目で作り直した箇所の周り）。**ただし実装に入ると、その 3 件はどれも
「実装を読めば分かる」層だった** —— 計画に対するレビューはそこで打ち切り、
実装差分を見せる側へ移して正解だった。

**レビュー役は毎回 `--fresh` で回す**（§5 の agmsg の節）。継ぎ足すと、**自分が提案した
対処の周りが盲点になる** —— 7 巡目で出た blocker 2 件は、どちらも 5・6 巡目のセッション
自身の提案だった（版の書き換え、`sha256:` 接頭辞）。

**繰り返し出た誤りの型**（次も同じ形で出る）:

- **形から素性を推定する。** 「64 hex なら指紋」「unreserved なら安全」は、同じ形の秘密を
  素通りさせる。信用できるのは cohort（版）だけ。値自身に持たせた印も、**相手が値を
  選べる場所では出所にならない**
- **直した検査が新しい境界を作る。** 拒否の列挙は fail-open、移行は新しい状態（混在 DB）を
  作り、guard は片側だけ強くなる
- **順序**。trigger が先に走る、`await` の後で参照が消える、mount の順で API が飲まれる
- **受け入れの経路に入っていない機能は、無いのと同じ**（API はあるが画面から呼べない）

### 検証状態

```
uv run pytest                  1106 passed, 4 deselected   ← Phase 5 Task 0〜5 を含む
uv run pytest -m needs_root      1 passed   ← detached mount の実証
uv run pytest -m needs_immich    3 passed   ← 実 Immich v3.1.0 で確認済み（2026-08-19）
uv run pytest -m needs_system    6 passed   ← 実プロセスを起動する E2E の土台（SSE の線上の挙動を含む）
uv run ruff check .            All checks passed
uv run ruff format --check .   178 files already formatted
npm --prefix web run lint / typecheck   通る
```

**全体テストは 2 分 45 秒ほどかかる。** 待つ前提で回すこと（バックグラウンドへ
逃がして待つのが速い）。**テストの実行中にソースやテストを書き換えない** ——
一度それで 8 分かかる不可解な実行を作った（`ruff format` が収集済みのテスト
ファイルを書き換えた）。

**結合のテストは実 ffmpeg を使う**（`shutil.which("ffmpeg")` が無いときだけ skip）。
開発コンテナには `~/.local/bin/ffmpeg` が入っている。

フロントは `web/` にある。

```
npm --prefix web ci        # 依存
npm --prefix web test      # vitest（30 件）
npm --prefix web run lint / typecheck / build
npm --prefix web run test:e2e   # Playwright。実プロセス + fake broker + fake Immich 2 台
npm --prefix web run typegen    # OpenAPI から型を作り直す（API を変えたら回す）
```

`ruff format` の件数がソースの本数より多いのは、`docs/` を `extend-exclude` で
外していても**ルート直下の `CLAUDE.md` は対象に入る**ため（Markdown 内の
コードブロックが整形される）。

`docker restart` や電源断に相当する試験は、§9.3 の手順 11 段すべてで子プロセスを
`os._exit` で落として **import / merge / merge_prepared の 3 経路**を回収できる
ことまで確認済み。回収後は結合グループの状態（`detected` か `merged`）も
assert している。

### 残っていること

1. **実 USB での手動確認（`phase1-manual-checklist.md` の 12 項目）。**
   開発コンテナ（入れ子の非特権 LXC）ではマウントが AppArmor に阻まれるので、
   TrueNAS ホストで実行する必要がある。

   特に **11 番（mtime の解釈の実測）は実装の前提の確認**。前提が崩れていれば
   `timestamps.py` の `_wall_clock` と `publisher._collision_stamp` を直す。
   Phase 2 の派生物の mtime（`merger._recording_end_ns`）も同じ前提に乗っている。
   **12 番（`attached_pic`）は Phase 2 で足した項目**（`streams._is_thumbnail`）。
2. **実データでの TS フォールバックの確認。** テストは lavfi のクリップで両経路を
   通しているが、実 DJI では Phase 0 の時点で concat 経路が通っており、TS 経路は
   まだ実データで走っていない。

3. **Canon EOS 70D の実カードでの確認。** `canon-eos` のプロファイルは仕様と
   知識から書いており、**実データを一度も見ていない**。特に次の 3 つは
   `phase1-manual-checklist.md` へ送ってある（Task 9 で足す）。
   - `DCIM/100CANON/` の構成とボリュームラベルが想定どおりか
   - **4GB 分割が連番から判別できるか**（`merge.enabled` を有効化してよいかの判断）
   - **MOV の `creation_time` が壁時計か UTC か**（第 4 の timestamp source を足すかの判断）

**1〜3 はいずれも「この環境では確かめられない」もの**（1 は TrueNAS ホスト、
2 は実 DJI、3 は実 Canon のデータが要る）。**Phase 5 のコードは Task 6〜9 が残っている。**

---

## 2. 成果物の場所

| ファイル | 内容 | 追跡 |
| --- | --- | --- |
| `docs/design.md` | **設計仕様書。正本。** | ✅ |
| `docs/phase1-plan.md` | Phase 1 の実装計画。**実行済み**。実装との差分は都度書き戻してある | ✅ |
| `docs/phase2-plan.md` | Phase 2（結合）の実装計画。**実行済み**。codex のレビュー 2 巡を反映し、実装で外れた判断と検出できなかった変異を書き戻してある | ✅ |
| `docs/phase3-plan.md` | Phase 3（Immich 同期）の実装計画。**実行済み**。codex のレビュー 7 巡を反映し、実装で外れた判断と検出できなかった変異を書き戻してある | ✅ |
| `docs/phase4-plan.md` | Phase 4（Web UI）の実装計画。**実行済み**（19 タスク）。計画レビュー 1 巡と実装差分レビュー 1 巡を反映し、「実装を終えて」に**実装で初めて分かったこと**を書き戻してある | ✅ |
| `docs/phase5-plan.md` | Phase 5（汎用化）の実装計画。**実行中**（10 タスク中 6 つ完了）。計画レビュー 2 巡を反映し、実施した変異試験の結果と「実装で分かったこと」を書き戻してある | ✅ |
| `docs/phase1-backup.md` | バックアップとリストア、再構築できる範囲（§18-4） | ✅ |
| `docs/phase1-manual-checklist.md` | 実 USB での確認手順 | ✅ |
| `docs/phase0-findings.md` | Phase 0 の実測結果と設計への反映 | ✅ |
| `docs/HANDOFF.md` | このファイル | ✅ |
| `{protocol,mountd,app,spikes}/` | 実装 | ✅ |
| `web/` | フロントエンド（React + TS + Vite）。8 画面 + vitest + Playwright。`src/api/types.ts` は OpenAPI からの**生成物**で追跡する（再生成し忘れは Python のテストが検出） | ✅ |

**`docs/superpowers/` は `~/.gitignore_global` で除外されている。** 重要なものは
`docs/` に置くこと。

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

### Phase 2 で確定した契約（蒸し返さないこと）

**実装・検証とも済んでいる。** 根拠は `design.md` §21 の「Phase 2 の実装で
確定した事項」と `phase2-plan.md` の各タスク。**6 件は codex のレビューで
blocker として指摘されたもの**で、理屈で戻すと同じ穴に落ちる。

| 判断 | 理由 |
| --- | --- |
| **結合物の公開は `publish_prepared`（`work` → `staging` を `os.link`）** | `write` コールバックのままだと 30 GiB をもう一度書く。`work/` と `staging/` は同一ファイルシステム（§7）なので link で移せる。11 手順と回収の性質は `publish` と同じ |
| **`publish_prepared` の SHA-1 走査中も heartbeat とキャンセル確認を続ける** | 30 GiB の走査はリース（60 秒）より長い。打たないと手順 7 の `assert_lease` で失効し、**正しく生成・検証済みの結合物が捨てられる** |
| **staged より前の「中断できない長い処理」は `_with_lease_pulse` で囲む** | `os.fsync`（30 GiB の直後は数十秒）と ffprobe（timeout がリースと同値）は途中で止められず、chunk の合間の heartbeat では守れない。処理を別スレッドへ出し、待つ側が打つ（DB へ触るのは待つ側だけなので接続は 1 本のまま）。**取り込み側にも同じ穴がある**（§7） |
| **キャンセルは例外で `JobRunner` まで上げない** | `_run_one` は例外をすべて `failed` にするので、利用者が押したキャンセルが失敗として記録される。ハンドラが受け止めて正常 return し、`finish_claimed` の `cancelling -> cancelled` に決着させる（取り込みも同じ形で降りている） |
| **map はパートごとに、そのパート自身の ffprobe 結果から作る** | 保持 signature が同じでも、保持しない data track の挿入位置が違えば絶対 index は変わる。先頭の index を使い回すと、**同じ codec の別トラックを失敗せずに拾う** |
| **concat demuxer は preflight してから使う** | demuxer は最初のファイルの構成を全体に適用する。全ストリームの構成と保持対象の index の並びが一致しないときは、試さずに TS へ送る |
| **TS 経路は mpegts が運べないストリームを外して記録する** | `mpegts` は QuickTime の data track（`tmcd` / `djmd`）を運べない。map に残すと mux が拒否して**検証できる出力そのものができない**（既定の DJI プロファイルは `timecode: true` なので fallback が常に使えなくなる） |
| **検証結果は公開の前に commit する** | 公開の途中で落ちても検証をやり直さない |
| **`merging` のまま残ったグループは、出力の有無で merged / detected へ倒す** | 倒さないと再試行もできない。`_recover_staging` の後に走らせる。**回収できない `artifact_staging` を抱えたグループは動かさない**（再試行させると、古い staged 行と新しい公開が同じグループを指す） |
| **公開後にできる操作は採用だけ。破棄と再結合は Phase 4** | どちらも公開済みの `media_file` を取り残す。旧グループを `superseded_by_id` で向け直す仕組みが要り、それは手動編集と共通なので画面と一緒に入れる。**何も公開していない `failed` からの結合実行は Phase 2 でできる** |
| **派生物の mtime は「壁時計を UTC として解釈した epoch」** | 取り込みの mtime と同じ表現にする。オフセット付きの瞬間を使うと `library/` と `derived/` で衝突接尾辞の壁時計がずれる |
| **期待サイズは `bit_rate` が取れた保持ストリームだけで組み立てる** | `tmcd` は `bit_rate` を持たない。それを理由に全体を `inconclusive` にすると、**既定のプロファイルでサイズ検査が常に無効**になる（Phase 0 で直した検査が死ぬ）。ばらつきも合計ではなく対応するストリームごとに見る |
| **検証器の版は `verification_json.pipeline_version`。`input_digest` には入れない** | `input_digest` は §8 で入力の同一性の判定と定義されている。混ぜると、閾値を 1 つ変えただけで既存の結合物がそろって選択肢から消え、再結合するまで戻らない。**codex の指摘を退けた 1 件**（先方も受け入れた） |
| **検出は「アクティブな member」を境界として扱う** | 列から取り除くだけだと、その前後がつながって別の録画を 1 つのグループにする。写真も候補の列に入れない（duration を持たないので境界として働き、前後の分割録画が検出されなくなる） |
| **TS 片のストリームの並びは種別順に揃える**（実装で判明） | `concat:` は mpegts の生バイトを継ぐので、パートごとに並びが違うと後続を読めない（`No start code is found`）。map の index はパート自身のままで、並べる順だけ揃える |
| **サイズ検査の許容誤差 2% は実機の大きさが前提**（実装で判明） | コンテナのオーバーヘッドは 16 GiB では 0.002% だが、数百 KB の合成クリップでは 7〜8% になる。lavfi のクリップで組んだ e2e は必ずサイズ検査に落ちるので、採用（`adopt`）まで通す形にしてある |
| **`record_verification` と `mark_merged` は成立条件を DB 側で確かめる** | 呼び出し順のバグ 1 つで「merged なのに出力が無い」行ができ、選択肢の側が隠すので静かに残る |


### Phase 3 で確定した契約（蒸し返さないこと）

**実装・検証とも済んでいる**（実 Immich での確認だけ残り）。根拠は `design.md`
§21 の「Phase 3 の実装で確定した事項」と `phase3-plan.md` の各タスク。
**4 巡のレビューで blocker として挙がったものを含む。**

| 判断 | 理由 |
| --- | --- |
| **送信は宛先ごとに 1 本のジョブで、1 件ずつ直列に処理する** | 律速はネットワークと Immich 側の取り込み。同時本数を増やしても増えるのは失敗の同時多発だけで、接続をスコープごとに 1 本に保つ約束も崩れる |
| **リモートに触る手前は必ず `prepare_side_effect` を通る** | `assert_lease` は `cancelling` を拒むが `extend_lease` は拒まない。心拍だけを頼りにすると、**キャンセル要求の後にアップロードを始める** |
| **キャンセルで途切れた送信は `needs_recheck`。`failed` にしない** | サーバ側の成否が不明なまま失敗にすると、次に「未送信」として上げ直して重複する |
| **`LeaseLost` をジョブの外へ出さない** | `_run_one` が例外をすべて `failed` にするので、利用者が押したキャンセルがジョブの失敗として記録される（結合・取り込みと同じ形） |
| **リビジョンを変えたら、旧 epoch の未完了レコードを*同じトランザクションで*無効化する** | 別トランザクションにすると、その隙間で claim された記録が旧 epoch のまま送られる |
| **claim は宛先の現行リビジョンを同じトランザクションで解決する** | 先に読むと、claim までの間に編集されたリビジョンで送ることになる |
| **preflight は成功を 15 分、失敗はリビジョンが変わるまで憶える** | 向き先が違うまま試し続けると、**間違った Immich に少しずつ資産が積み上がる**。直し方は宛先の編集＝リビジョンの更新なので、そこまで憶える |
| **`origin` が `created_by_us` でなければ日時の補正は承認待ち** | 別経路で上がっていて、ユーザが手で直しているかもしれない。同じリモートを指す 2 つ目の宛先も、送信せずに既存資産を引き受けて承認待ちになる |
| **`ImmichClient` は redirect を追わない。本文を伴う要求では特に** | `x-api-key` はカスタムヘッダなので cross-origin の redirect でも剥がれず、**外部へ 301 を返す誤設定で鍵がそのまま渡る**。本文つきの要求は 1 回目で EOF に達しているので、追うと空の本文を送る |
| **応答も例外もメッセージに秘密と相手の本文を含めない** | 転送先の一覧は API キーのマスク値すら返さない。応答本文は相手が決める値で、送った鍵をそのまま返す実装がありうる |
| **URL の検証は接続の検証より先** | 逆にすると `javascript:` のような値でもまず接続を試し、400 ではなく 502 を返す |
| **`refuse` は所有を落とすと同時に `state` を `pending` へ戻す** | 進行中の状態のまま所有だけ外すと `upload_record` の CHECK に触れる |
| **承認はジョブ、却下は同期** | 承認はリモートの日時を書き換えるのでリースとキャンセルの下で行う。同じレコードの承認が実行待ちなら 409 で断る（積めると 1 本目の後の残りが軒並み失敗として並ぶ） |
| **相手は「こちらが読む値」を選べる。`remote_user_id` は指紋（SHA-256）で保存する** | 侵害された転送先は、受け取った `x-api-key` を `users/me` の `id` や `POST /api/assets` の `status` として返せる。生の値を保存・引用すると、**暗号化したはずの鍵の平文が DB の列・API 応答・例外文（`job.error` とログ）に現れる**。用途は等値比較の guard だけなので指紋で足りる。3 巡目の blocker |
| **`create_pairs` は現行リビジョンを INSERT と同じトランザクションで読む** | 外で読むと、読んだ後・書く前に epoch が進んで無効化まで済み、旧 epoch の行がすり抜ける。claim されないまま次の起動の掃除まで残る |
| **最初の 1 バイトの直前に §10 の根拠を見直す**（`verify_eligibility`） | claim 直後の判定はその時点の状態でしかない。判定から送信までの間に結合をやり直されると、結合中のグループの構成ファイルを送る。タグ・日時の前では見直さない（見送っても取り消せない） |
| **一度確定した `created_by_us` は降格させない** | 後処理の 503 で再開すると、自分が上げた資産は当然 `reject` で返る。付け直すと `unknown` になり、タグが付かず日時が承認待ちになる |
| **リースの確認は preflight より先** | preflight の `users/me` も鍵付きの要求。キャンセル済みのジョブから出さない |
| **識別子は「許す形」を並べて検め、dot-segment は別に拒む。境界の両方向で通す** | `assetId` は `remote_asset_id` として保存され API 応答にも出る。タグの id は次の要求の URL に入る。**「正常な形」で鍵を返されると、例外文を綺麗にしただけでは平文が通る**（4 巡目の blocker）。**拒む文字を並べる検査は fail-open**（符号化した鍵 `%74%65%73%74…` は「使えない文字」を含まないのに、httpx は path に載せるし可逆に戻せる）。RFC 3986 の unreserved だけを長さの上限付きで通し、**送る値も同じ検査に通す**（検査の無かった版が保存した行が DB に残っている）。5 巡目の blocker |
| **guard は prepare → preflight → prepare の 2 段** | 再確認は相手待ちでリース（60 秒）より長くなりうる。前だけに置くと「直前に確かめた」保証が消える |
| **2 段 guard は後段も同じ強さで確かめる** | 後段だけ §10 の根拠の見直しを落とすと、相手を待っている間に結合をやり直されたとき、いま結合中のグループの構成ファイルを送る。**保証が消えるのはキャンセルだけではない。** 5 巡目の major |
| **`duplicate` で返った資産は他人のものかもしれない** | 初回が `accept` でも応答が `duplicate` なら自作の証明は無い（`origin` は `unknown`）。こちらはまだ何も変えていないので、タグ付けが最初の変更になる。その前に §10 を見直す。5 巡目の major |
| **保存済みの相手由来の観測は、値を見ずに cohort ごと落とす**（`0007`） | 受け取る側の検査は新しく受け取る値にしか効かない。**形で選り分けると `test-api-key` のように識別子の形をした鍵が残る**し、SQLite の `length`・`GLOB` は埋め込み NUL で打ち切られる。`remote_asset_id` / `remote_checked_at` / `remote_is_trashed` は**まとめて**捨てる（片方だけ残すと「どの資産の、いつの観測か分からないゴミ箱状態」が一覧に出る）。`complete` は再確認で戻り、`awaiting_datetime_approval` は指す資産が無いので無効化する |
| **出所は値の中身（形も接頭辞も）で決めない。信用できるのは版（cohort）だけ** | 「64 hex なら指紋」も「`sha256:` で始まるなら指紋」も、**相手が同じ形の値を選べる**（API キーを発行するのは相手。SQLite の `LIKE` は ASCII の大小文字も区別しない）。区別が付かない相手由来の列は、`0007` で捨てて再取得させる（preflight は記録が無ければ閉じるので、宛先を保存し直せば新しいリビジョンに今の観測が入る）。6 巡目の blocker → 7 巡目の blocker |
| **再確認は batch ごとにキャンセルとリースの両方を見る** | 最初の batch も飛ばさない（preflight は相手待ちで、その間にキャンセルが commit されうる）。`cancelled()` はジョブの状態しか見ないので、`running` のままリースだけ失効した worker が残りを送り続ける。5 巡目の major |
| **照合の結果は、照合したときの行にしか書かない** | `complete` は終端ではない。消滅と判定された行は requeue でき、送り直しが済めばまた `complete` に戻る。id と現在の状態だけを条件にすると、新しい `remote_asset_id` を古い観測（消滅＝NULL）で消す。動いた行は書かずに飛ばし、**「確認した」とも数えない。** 5 巡目の major |
| **再確認もリモートへ触る前にリースを見て、待っている間は心拍を打つ** | 最初のリモート要求は preflight の `users/me`。`cancelled()` はジョブの状態しか見ないので、`running` のままリースだけ失効した worker を止められない。さらに `assert_lease` は見るだけで延ばさないので、**心拍が無いと 60 秒を超える正常な再確認が必ず失敗する**（`JobRunner` が failed にする）。相手待ちは `with_lease_pulse` で囲む。6 巡目の major |
| **適用済みの版のファイルは書き換えない** | 書き換えると前の版で作った DB が `MigrationError` で開けなくなる。**移行が走る前に落ちるので、データを直す機会も無い**。`test_a_database_from_the_previous_release_still_opens` が版の checksum を凍結している（版を足したら、その一覧にも足す）。7 巡目の blocker |
| **相手待ちはどこでも心拍で囲む。囲むのは待ちだけ** | preflight の `users/me` もクライアントの timeout（既定 86400 秒）まで待ちうる。`assert_target` の `wait` に `with_lease_pulse` を渡す（`Rechecker` / `Uploader` / `ApprovalService` の 3 つとも）。**クライアントの構築＝資格情報の復号は呼び出し側のスレッドに残す**（DB へ触るのは待つ側だけ、という約束を崩さない）。7 巡目の major |
| **既存資産へのタグ付けの前にも §10 を見直す** | 「もうリモートにあるので取り消せない」は、こちらが作った資産にしか当てはまらない |
| **再確認の結果は 1 つのトランザクションで書き、分割も Rechecker 側で行う** | 1 行ずつ commit すると中途半端な状態が残る。adapter に全件を渡すと、内部ループの合間にキャンセルを見られない |
| **既存 DB の観測値も移行で指紋へ変換する**（`0005`） | 変換しないと旧平文が残り、preflight が全宛先を「向き先が変わった」と誤判定する。`destination_revision` は不変なので trigger を外して変換し作り直す。**SQLite に SHA-256 が無い**ので、runner が `mediaferry_fingerprint` を接続へ登録する |
| **既に無効化された行の理由と時刻は上書きしない** | claim を持っている間に別の経路で無効化されることがある。上書きすると監査で見えるのが二次的な文言に変わる |
| **fake Immich はループバックで実際に listen させる** | httpx 0.28 の `ASGITransport` は非同期用で、同期の `httpx.Client` から使えない。実物の httpx を通すのでプロトコルの取り違えも見逃さない |

### Phase 4 で確定した契約（蒸し返さないこと）

**実装・検証とも済んでいる。** 根拠は `design.md` §21 の「Phase 4 の実装で確定した事項」と
`phase4-plan.md`。**認証・CSRF・非 loopback バインドはここで初めて入った。**

| 判断 | 理由 |
| --- | --- |
| **入口の防御は既定で全経路に掛ける**（ルータ単位の `dependencies`） | 状態を変える API は 20 本以上ある。ルータごとに書くと**次に足すルータで書き忘れる**。例外は `/health` と `/auth/*` だけ |
| **`Host` を信頼できる集合と突き合わせる**（IP と `localhost` は既定で通し、名前は許可制） | Origin と Host の一致は **DNS rebinding を防がない**（どちらも攻撃者のホスト名になる）。IP を直に打つのは利用者の正当な操作 |
| **CSRF は二重送信 Cookie。セッションに紐付けない** | 認証を切っていても、罠サイトを開いたブラウザから `127.0.0.1` を叩ける。紐付けないので認証の有無に関わらず効く |
| **`/auth/login` は CSRF だけ免除。Origin と Host の検証は掛ける** | 丸ごと例外にすると、罠サイトからログインを試行させられる |
| **セッションは指紋で保存する。パスワードの世代は保存済みハッシュへの `verify` で判定する** | 起動のたびにハッシュし直すと salt が毎回変わり、**再起動のたびに全員ログアウトになる** |
| **認証は既定 off、`BIND_HOST` の既定も loopback のまま** | 利用者の判断（LAN 内で無設定で使えることを優先）。Phase 4 が変えたのは「認証を入れれば公開してよい状態になる」ことだけ |
| **API の失敗は `{code, detail, meta}` の封筒に統一する** | 画面は `code` を見て日本語を決める。`detail` をそのまま出すと、内部の文言や相手由来の値が利用者へ流れる |
| **SSE の再開 cursor は `job_event.id`。初回は履歴を流さない** | `seq` はジョブ内の連番なのでジョブをまたげない。長く運用した後に新しいタブを開くだけで全件流れると詰まる |
| **`BaseHTTPMiddleware` を使わない** | 応答を一旦受け止めてから流すので、終わらない応答（SSE）が相手に届かない |
| **SSE の資源は「終わり方を選ばずに」返す** | 相手が切れると Starlette は `aclose()` を呼ばずにタスクを取り消す。`StopAsyncIteration` だけ見ていると、8 回切れば `/events` が恒久的に 503 |
| **サムネイルは DB に入れず、位置と容量に上限を置く。キャッシュはアプリに 1 つ** | 再生成できるキャッシュであって派生物ではない。`at` が自由だと 1 本で何千枚も作れる。要求ごとに作ると single-flight が効かない |
| **一覧の並びは `captured_at DESC, id DESC`** | 同じ撮影日時の行があるので、tie-break が無いとページの境目で重複・欠落する |
| **承認の「現在値」は承認を求めた時点で観測して保存する** | 画面を開くたびに N 件ぶんの HTTP を出さない。読めなかった値は**「変更なし」にしない** |
| **結合の編集を拒むのは「これから送られる根拠」のときだけ** | `complete` まで拒むと、一度送ったグループが永久に直せない |
| **supersede は 1 トランザクション。無効化 → 向け直し → 新 member の順** | 割れると部分索引が 2 行を許す。trigger が旧 member を `active = 0` にするので、**無効化を後にすると 1 件も当たらない** |
| **送信は画面から見ても 2 段階**（`POST /uploads` → 宛先ごとの `upload`） | 組を作ることと送り始めることは別。**組ごとの結果を読み、断られた組と失敗した宛先を隠さない** |
| **不可逆な操作の確認は、操作の種類ごとの直和で持つ** | 件数・合計サイズ・宛先名はアップロードにしか意味が無い |
| **選んだものは、絞り込みで隠れても覚える（大きさも一緒に）** | 表示中の行から合計を出すと、隠した分が抜けて確認の数字が実際と食い違う |
| **進捗が届いたら一覧を取り直す。中身は読まない。受け取った総数で判定する** | 何がどう変わったかをブラウザ側で組み立てると規則が 2 箇所に散る。配列の長さは保持の上限で止まる |

### Phase 5 で確定した契約（蒸し返さないこと）

**Task 0〜5 は実装・検証とも済んでいる。** 根拠は `phase5-plan.md` の各タスクと
そこに書き戻した変異試験の結果。**計画レビュー 2 巡で blocker として挙がったものを含む。**

| 判断 | 理由 |
| --- | --- |
| **検出の起動源は `list_volumes` の generation ポーリング。`subscribe`（netlink uevent）は入れない** | `enumerate_volumes` は `_is_usb` で絞ってから `blkid` を掛けるので、USB ブロックデバイスが無い間はサブプロセスが起きない。高いのは列挙ではなく `refresh()`（判定のたびに実際にマウントする）で、**それは uevent 駆動でも同じ罠**。分けるべきはトリガの種類ではなく、安い変化検出と高い判定 |
| **`refresh()`（マウントを伴う判定）は観測トークンの変化でしか走らせない。enqueue の判定は毎 tick DB の現在値から組み直す** | `trust()` は `trusted_at` を `UPDATE` するだけで mountd の指紋を動かさない。門の内側で判定すると、**カードを挿したまま承認しても自動取り込みが始まらない**。同じことが `AUTO_IMPORT` の変更とプロファイルの編集でも起きる。**利用者が DB を変えたことは観測トークンには現れない**（1 巡目 blocker） |
| **判定材料はすべて DB に置く**（`0010` の `volume_instance.provisional`） | 1 つでも `VolumeView` にしか無いと、毎 tick の組み直しが成立しない |
| **`volume_instance.profile_id` / `profile_revision_id` は「前回の判定の写し」。現在の真実ではない** | 候補の抽出で `device_profile` を JOIN し、**archive されていないこと**と**その版が今も現行であること**を確かめる。確かめないと、判定の後・積む前に別接続で archive / `PUT` が commit でき、archive 済みのプロファイルや編集前の版で取り込みを積む（2 巡目 blocker） |
| **観測トークンは空集合を専用の番兵として扱い、「未観測」と必ず区別する** | `generation` と `broker_epoch` は `VolumeInfo` の中にしかない（`_do_list` は volumes を返すだけ）ので、最後の 1 枚を抜くと読む場所が無い。同一視すると、前回の停止時に live のまま残った presence がある状態で空で起動したとき、**最初の tick が `detach_absent` を飛ばして抜けたカードに積む**（2 巡目 major） |
| **門の入力には「プロファイルの現行版の指紋」も含める** | `require` を変えても mountd の観測は動かない。編集・複製・archive のどれでもこの値が動くので、明示的な wake を別に作らずに済む |
| **`VolumeWatcher` は 1 つのスコープを丸ごと持つ**（専用の DB 接続・`ProfileRegistry`・`VolumeService`・`BrokerClient`） | `refresh()` は自分の `BrokerClient` を使うので、API 側の `VolumeService` を借りると、停止のために専用ソケットを閉じても黙り込んだ `refresh` が止まらない。接続だけ共有すると 1 本の接続を 2 つの `RLock` で同時に使うことになる（2 巡目 blocker） |
| **`lifespan` の停止順は watcher が先、runner が後** | 逆にすると、runner が降りた後に自動取り込みが積まれる |
| **`VolumeService.refresh()` の pass 1〜2 は `BEGIN IMMEDIATE` で囲む** | インスタンスが 1 つしか無かったから autocommit で成立していただけ。watcher が 2 つ目を持つと、pass の合間に相手の書き込みが挟まる。**判定（pass 3）はマウントを伴って長いので外に出す** |
| **`BrokerClient` の再接続は `list_volumes` だけ。閉じている最中は再接続しない** | `open_volume` は fd を伴うので再送すると 2 度目のマウントが起きる。`close_volume` の handle は発行した接続に束縛されている。停止は接続を閉じて `recv` を解くので、その `OSError` を再接続が拾うと停止が効かない（2 巡目 major） |
| **EXIF はステージ済みのファイルから読む。手順 4 の後・手順 5 の中** | ソースを 2 度読むと、コピー中に書き換えられた場合に「取り込んだ中身と読んだ EXIF が違う」状態を作れる。前で読むと未完成のファイル、後で読むと `metadata_json` に載らない（2 巡目 blocker）。`ArtifactRequest` の `captured` と `resolve_captured` は `__post_init__` でどちらか一方に強制する |
| **画像以外では EXIF を読みに行かない** | `exifread` は認識できない入力に例外ではなく WARNING を出す（実測）。Canon は MOV も `source: exif` のプロファイルを通るので、呼べば動画 1 本ごとに警告が並ぶ。振り分けは `MediaProbe` と同じ `PHOTO_EXTENSIONS` で行う |
| **プロファイルの正規表現は `regex` + `timeout` で当てる。`re` は使わない** | 長さの上限では catastrophic backtracking を防げない。保存時に敵対的な標本を試すのも `(z+)+$` が `a` の標本を素通りするので保証にならない。**実測**: `re` は `(a+)+$` に 10 秒でも終わらないが `regex` は 0.000 秒、`regex` でも破綻する `(a\|a)+$` には `timeout=` が 0.500 秒で発火する（2 巡目 major） |
| **`PatternTimeout` は「一致しなかった」ではなく失敗として扱う** | 黙って不一致にすると原因が画面から分からない。判定では対象外に落として理由を出し、取り込みでは `fallback` へ落とす |
| **`canon-eos` は `hints.usb_ids` を空にし、`merge` を無効にする** | カードリーダー経由が前提なので、見える USB ID はリーダーのもの。4GB 分割の判別根拠が実データ無しに得られず、**誤結合は公開済みの `media_file` を取り残す**ので高くつく |
| **`generic-dcim` はメーカー固有の RAW を拾わない** | 汎用が拾うと機種プロファイルを作る動機が消えて `library/generic-dcim/` に何でも溜まる。`MTS`（AVCHD）も入れない（`BDMV` の構造とセットで意味を持つので、単体で拾うと分割された動画が個別に取り込まれる） |
| **ビルトインへの mutation は `duplicate` を除いて全部拒む。ガードは 1 箇所に集約する** | `_upsert_revision` は `builtin` を見ないので、編集を許すと次のアプリ更新で `sync_builtins` が黙って上書きする。**`archive` も同じ** —— `sync_builtins` は `archived_at` を戻さないので、一度 archive すると再起動しても復活しない（2 巡目 major） |
| **プロファイルの読み取りは `all()`（archive 済みを含む）と `active()`（含まない）に分ける** | 同じ一覧を両方の用途に使うと、どちらかが必ず間違う。一覧 API は `all()` —— archive は削除ではないので、一覧から消すと「外した」のか「消えた」のか区別が付かない |
| **自動取り込みが効くのは 2 度目以降の挿入から** | `_identity_confidence` は憶えた指紋が無ければ必ず `low` を返す（「初めて見るカードは §12.1 のとおり必ず承認を待つ」）。§12.1 の「一度承認すれば**以後は**挿すだけ」の「以後」がここに対応する |
| **自動経路の at-most-once だけを保証する** | 手動の `POST /volumes/{id}/import` は `auto_import_at` を見ないので、同じ presence に手動 1 本と自動 1 本が積まれることはある。import は冪等なので受け入れる。ジョブ全体の重複排除は `JobStore` の契約を変える話として切り離す |

---

## 4. 環境の癖と罠

### TrueNAS ホスト

- **共有データセット** `/mnt/ssd/develop-server/` が開発コンテナとホストの両方から
  同じパスで見える。ソースは `/mnt/ssd/develop-server/mediaferry/` に配置済み
  （`git archive HEAD | tar -x -C ...` で更新）
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
- **`pytest-timeout` は入っていない。** `uv run pytest --timeout=90 --version` は
  通るが（`--version` が引数検証より先に返る）、実際に使うと
  `unrecognized arguments` になる。**「入っている」と誤読しないこと**
- **回帰でテストが「ハング」する形を書かない。** 応答しない相手を待つ試験や
  ReDoS の試験を素直に書くと、実装を壊したときに失敗ではなく無限待ちになる
  （`to_thread` のスレッドはインタプリタ終了時に join される）。**待ちには必ず
  上限を置き、別スレッドで走らせて `join(timeout=…)` で見る。** Phase 5 では
  これで 2 度詰まった（`SilentBroker` と ReDoS の試験）

### ブローカー（mountd）とテストの土台

- **`BrokerServer._observe` は lister が返す `broker_epoch` と `generation` を捨てて、
  自分の値で刻み直す。** 世代は観測した集合 `(volume_key, fs_uuid, fs_type, size_bytes)`
  の指紋から算出する。したがって**テストで「抜き挿し」を表すには、集合そのものを
  変えるしかない**（`generation` の欄を書き換えても客体には届かない）。逆に言えば、
  観測トークンは完全にサーバ側で決まるので app 側が値を作れない
- **epoch を試験から指定することはできない**（起動ごとの乱数）。トークンの比較規則は
  スタブの client で単体試験にし、線の上の挙動は実 `BrokerServer` で見る
- **`conftest` の `broker_factory` は呼ぶたびに新しい接続を作る。** handle は発行した
  接続に束縛される（§11）ので、同じ client を使い回すと「`VolumeWatcher` は専用の
  ブローカー接続を持つ」を検証できない（watcher の停止が取り込みの相手を切る経路が
  素通りする）
- **`FakeMountManager.mounts` がマウント回数を数える。** 「判定のたびにマウントする」
  代償を測るのに使う

### ffmpeg / ffprobe

- 開発コンテナに `~/.local/bin/ffmpeg` と `ffprobe` が入っている。**結合の
  テストは実バイナリを使う**（`shutil.which("ffmpeg")` が無いときだけ skip。
  `needs_root` のようなマーカーは付けない。既存の `test_adapter_ffprobe.py` と
  同じ作法）
- テスト用のクリップは lavfi で作る（`testsrc` + `sine`）。`-map` の順が
  そのまま出力のストリーム順になるので、**並びの違うパートを意図的に作れる**
- `-timecode 00:00:00:00` を付けると `tmcd` の data ストリームが増える。
  TS 経路が運べないケースの再現に使う

---

## 5. 次にやること

### 手順

**Phase 5 の Task 6 から続ける。** 計画は `docs/phase5-plan.md`、進捗表は §1。

1. **Task 6: `recompute_timestamps` ジョブ（`0011`）。** 残るバックエンドの山で、
   計画レビューの blocker が 2 つ集中している。
   - **provenance**: `media_file.profile_revision_id` は「そのレコードが使った不変の版」
     という既存契約。値だけ新しくすると嘘になり、版を進めると timestamp 以外も
     適用したと偽る。`0011` で `captured_at_revision_id` を分離し、**trigger で
     「必ず値を持つ」と「同じプロファイルの版である」を強制する**
   - **再計算の入力**: `media_file` だけでは再計算できない。`filename` はカード上の
     原名に当てるが `media_file.rel_path` は公開先（衝突接尾辞つき）で、
     `source_rel_path` はどこにも保存されていない。**role ごとに入力を決め、
     `original` → `derived` の順序を固定する**（derived は先頭 active member から derive）
   - **送信済みの扱い**: `awaiting_datetime_approval` へ直接戻してはいけない
     （`tagging` からしか入れず、現在値の観測に Immich のクライアントが要る）。
     **`needs_recheck` へ戻して既存のパイプラインに再実行させる**
2. **Task 7〜8: 画面**（プロファイル編集、デバイス）。契約と受け入れ条件は計画にある
3. **Task 9: 受け入れ（E2E）とドキュメント。** `design.md` §21 に「Phase 5 の実装で
   確定した事項」を足し、**`subscribe` を採らなかった判断とその根拠を §9.2 の近くに残す**
   （設計にはプロトコルとして残っているので、なぜ使っていないかが分からないと
   次の担当が実装しようとする）
4. **実装差分のレビューを `--fresh` で 1 巡以上回す**（§5 の agmsg の節）
5. 終わったら `main` へマージする（`superpowers:finishing-a-development-branch`）

### この環境で確かめられないもの

§1「残っていること」の 1〜3。TrueNAS ホスト、実 DJI、実 Canon が要る。

### Phase 5 から Phase 6 へ送ったもの

- **`UPLOAD_CONCURRENCY` の多重化。** Phase 4 → Phase 5 → Phase 6 と 2 度送っている。
  現行の `JobRunner` は全ジョブ種で共通の単一 worker で、`claim_next()` は type も
  宛先も見ない。「宛先ごとに 1 本」を保つには claim のトランザクションで宛先単位の
  排他が要り、Phase 3 で固めたリースと停止の契約に触れる
- **RAW / JPEG の Immich 上でのスタッキング。** API は実 Immich v3.1.0 で確認済み
  （読み取りのみ、2026-08-19）。`POST /stacks` `{assetIds}`、`GET /stacks?primaryAssetId=`、
  `PUT /stacks/{id}` `{primaryAssetId}`、`DELETE /stacks/{id}/assets/{assetId}`、
  `AssetResponseDto.stack`。**`POST /stacks` は「渡した資産が既存スタックの primary
  なら、その既存スタックを吸収する」**ので、Phase 3 の「既存アセットを勝手に変更
  しない」に直接当たる —— 送る前に `AssetResponseDto.stack` を見る必要がある。
  詳細は `phase5-plan.md` の「RAW / JPEG のスタッキングを Phase 6 へ送る（実測つき）」

### 変異試験のやり方（ドライバはリポジトリに無い）

**ドライバはセッションの scratchpad に置いて使い捨てにしている。** 引き継いだら
下を書き直す（20 行ほど）。要点は「中断されても原文へ戻せること」と
「`PYTHONDONTWRITEBYTECODE=1` を付けて呼ぶこと」。

```python
# mutate.py <対象ソース> "<テストファイル…>" <変異定義 JSON>
# JSON は [{"name": ..., "old": ..., "new": ...}] の配列。
#   1 つの変異が複数箇所に及ぶときは {"name": ..., "pairs": [{"old":…, "new":…}, …]}。
#   **対で効く条件（SELECT と CAS の両方に置いた検査など）は、片方だけ壊しても
#   もう片方が塞ぐので単独では検出できない。pairs で同時に壊す。**
# 開始時に <対象>.orig を残し、次回起動時に残っていればそこから復元する。
# 各変異ごとに: 置換 → pytest -q --tb=no -rf →
#   落ちたテスト名を「[検出]」、1 つも落ちなければ「[素通り]」として出す。
# 最後に必ず原文へ戻す（finally）。
```

**`.orig` からの復元は飾りではない。Phase 5 で 2 回それに救われた。**
タイムアウトや `pkill` で SIGTERM を受けると **`finally` は走らない**ので、
変異が適用されたままソースに残る。次回起動時の復元が無ければ、壊れた実装のまま
先へ進むことになる。**変異試験はバックグラウンドへ逃がし、`<対象>.orig` の有無で
完了を待つ。** 走っている間に他のテストを流さないこと（変異中のモジュールを読む）。

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python mutate.py \
  app/src/mediaferry/jobs/uploader.py "app/tests/test_uploader.py" mut.json
```

**`.pyc` の話は本当に効く。** バイト数が変わらない書き換え（`>` を `<`、
`a or b` を `b or a`）では無効化条件をすり抜けて古いバイトコードが使われる。

**変異は「成立する形」で当てる。** レビュー 4 巡目で、`range` の刻みだけを変える
変異が素通りした。原因はテストの弱さではなく**変異の当て方**で、スライスの側が
元の定数を見たままなので分割が消えていなかった。**素通りを見たら、まず「その変異は
本当に狙いの判断を壊しているか」を疑う。**

**Phase 5 で見つかった「素通り」の型**（58 件中 47 件を検出し、素通り 11 件を精査した）:

| 型 | 例 | 見分け方 |
| --- | --- | --- |
| **狙いの分岐に届いていない** | `exif` の宣言を見る条件を壊したのに、ファイル名が当たる筋書きで試していたので `filename` の枝が先に返っていた | 変異した行に到達しているかを確かめる |
| **観測できる差が無い** | 解決の位置を手順 4 の前後で動かしても、実体は手順 2〜3 で書き終わっているので同じサイズが見える | 出力ではなく**手順の記録**と突き合わせる（`_checkpoint` を記録する） |
| **結果が同じになる筋書きしか試していない** | 動画で EXIF を読んでも `None` が返って fallback に落ちるので `captured_at_source` は変わらない | **呼んだかどうか**をスパイで見る |
| **条件同士が互いをマスクしている** | `detached_at` を `SELECT` と CAS の両方に置いてあるので、片方だけ壊しても塞がる | `pairs` で対を同時に壊す。**対で検出できれば、冗長さは意図であって削ってよい根拠にはならない** |
| **変異が判断を壊していない** | `EMPTY` の定数の値そのものは効いておらず、効くのは `_seen` の初期値 | 壊れる形へ変異を書き直す |
| **構造的に検出できない** | 旧 socket の `close()`（CPython の参照カウントが閉じる）、`details=False`（出力に差が出ない） | **記録に残す** |

**ReDoS の試験を書くときは、その式が本当に破綻するかを測ってから使う。**
`(?P<ts>\d{4})(a|a)+$` は「悪性のつもり」で書いたが、`\d{4}` が先頭で即座に
失敗するので backtracking に入らず、変異試験は素通りしたままだった。

### 作業の作法

1. 各タスクは「失敗するテストを書く → 失敗を確認 → 最小実装 → 通ることを確認 →
   変異試験 → コミット」で完結させる
2. **変異試験のステップを省かない。** Phase 1 では、計画のテストが「通っては
   いるが実装の判断を検証していない」箇所が **30 件以上**見つかった。特に多い
   パターンは次の 3 つ:
   - 別の分岐で先に落ちていて、狙いの分岐を一度も通っていない
   - 結果が同じになる筋書きしか試していない（差が出るのは「読んだバイト数」
     「試した名前の数」のような量だけ）
   - 順序規則を、たまたま同じ順になるデータで試している
3. **検出できない変異は、検出できないことを計画に書く。** 構造的にテスト不能な
   保険は実在する（`claim_next` の CAS 条件、`_materialise_link` の `os.fsync`、
   `sort_keys`、スキーマの trigger が保証する冗長条件など）。ただし
   **「検出できない」と書いてある変異の多くは、テストを 1 つ足せば検出できた**
   —— Phase 2 では計画が検出不能としていた 5 件のうち 4 件を、実装時に固定できた。
   まず落とせないか試し、それから記録する
4. **変異は「成立する形」で当てる。** 例外で全件落ちる書き換え（dict を
   `sorted` に渡す等）は、狙いの判断を検証したことにならない。
   **`start_new_session=True` を外す変異は当ててはいけない**（子がテストランナーと
   同じプロセスグループに入り、キャンセル試験の `killpg` が pytest ごと撃つ）
5. 計画から外れる判断をしたら、その場で計画側にも書き戻す
6. 詰まったら codex に相談する

### レビューの依頼

**先にコミットしてから、hash とファイル名を渡して読ませる。** 差分の全文を本文へ
貼ると、反映前の版をレビューされることがある（実際に 1 度起きた）。

**何を見せるかで、出るものが変わる。** 文書に埋め込んだコードは型検査も実行も
できないので、機械的な欠陥が毎巡残る（§1 の表）。**実装差分を見せると、
文書では出なかった層**（SQLite の並行性、相手が値を選べる応答、実装した順序）
**が出る。** 直した箇所の周辺をもう一度見せると、また出る。

### codex への経路（agmsg）

codex とのやり取りは agmsg 経由。チーム `mediaferry`、こちらが `mediaferry-dev`
（claude-code）、レビュー役が `mediaferry-reviewer`（codex）。スクリプトは
`~/.agents/skills/agmsg/scripts/` にある。**依頼のたびに起動し、終わったら必ず
片付ける。**

```bash
S=~/.agents/skills/agmsg/scripts
TEAM=mediaferry FROM=mediaferry-dev REVIEWER=mediaferry-reviewer

# この役割の bridge の pid だけを取る。行を team/role で固定するのは、別の codex
# 役割が増えたときに pid が複数行になって後段の ps が壊れないようにするため
bridge_pid() {
  "$S/delivery.sh" status codex "$(pwd)" \
    | sed -n "s|^Codex bridge: $TEAM/$REVIEWER alive (pid \([0-9]*\)).*|\1|p"
}
# ラベル完全一致でペイン id を取る（grep の部分一致だと別役割を拾う）
pane_id() {
  herdr pane list | python3 -c "import sys,json;print(next((p['pane_id'] \
    for p in json.load(sys.stdin)['result']['panes'] \
    if p.get('label')=='$REVIEWER'),''))"
}

# 1. 起動（この環境は tmux ではなく herdr のペインになる）
"$S/spawn.sh" codex "$REVIEWER" --project "$(pwd)"
PANE=$(pane_id)

# 2. bridge が起きるまで待つ。spawn は codex の readiness を待たずに返るので、
#    1 度見て not running でも失敗ではない
BPID=""
for _ in $(seq 30); do BPID=$(bridge_pid); [ -n "$BPID" ] && break; sleep 1; done

# 3. 依頼。alive を確かめられたときだけ送る（待ちが空振りしたら送らない）
if [ -n "$BPID" ]; then
  "$S/send.sh" "$TEAM" "$FROM" "$REVIEWER" "<依頼>"   # hash とファイル名を渡す
else
  echo "bridge が上がらない。送らずに調べること"
fi

# 4. 片付け。--force を最初から付ける
"$S/despawn.sh" "$TEAM" "$FROM" "$REVIEWER" --force   # → status=forced

# 5. 確認。status=forced は証明にならないので 3 つを別々に見る
"$S/identities.sh" "$(pwd)" codex                     # → 空なら登録は消えた
[ -n "$PANE" ] && pane_id | grep -q . && echo "ペインが残っている: $PANE"
#    bridge は即死しない。launcher が登録の消失を 2 tick 連続で見てから落とす
#    設計で、poll は 0.3→2 秒に伸びる（実測で終了まで 3 秒。即時の ps 1 回だと
#    正常系でも「生存」と誤読する）。消えるまで期限付きで待つ。args も見るのは
#    pid が再利用されていた場合に別プロセスを「生存」と誤読しないため
for _ in $(seq 15); do
  ps -p "$BPID" -o args= 2>/dev/null | grep -q codex-bridge.js || break
  sleep 1
done
ps -p "$BPID" -o args= 2>/dev/null | grep -q codex-bridge.js \
  && echo "bridge が残っている: $BPID"
```

**`--force` を最初から付けること。素の graceful を先に打ってはいけない。**
graceful な despawn は actas ロックだけを土台にしている（前提チェック、teardown の
実行主体であるメンバー側 `watch.sh`、完了判定のポーリング、3 つとも）。codex は
`actas-claim` を一度も走らせないためロックが常に `free` で、graceful は
`status=ok note=no-live-lock` を返して**何も片付けない**。しかもその離脱の直前に
`rm -f "$SPAWN_REC"` が走り、`--force` が読むはずの placement レコード
（`spawn.sh` が `herdr:<pane_id><TAB><project><TAB>codex` を書いている）を消す。
結果、続けて `--force` を打っても `no placement record` で exit 1 になる。
**その起動分は `despawn.sh --force` で後追いできない**（spawn し直せば placement は
また作られるし、手での teardown は残っている）。順序は一方通行。

`--force` はペインを閉じ、登録を落とし、placement レコードを消す。**bridge を
kill するコードは `despawn.sh` にも `reset.sh` にも無いが、それでも bridge は
止まる。** 止めているのは `codex-bridge-launcher.sh` の子で、毎 tick
`identities.sh` を見て自分の役割が 2 連続で空なら pidfile の bridge を kill して
`exit 0` する（同ファイルの `deregistered_ticks`。2 連続を要求するのは、
`identities.sh` が読み取り失敗でも空を返すため）。**手動の kill は要らない。**
この節を読んで `despawn.sh` だけを見ると「force は bridge を止めない」と読めるので、
根拠の在り処をここに書いておく。

**`delivery.sh status codex` は片付けの確認に使えない。** 登録を列挙する実装なので、
登録が消えた後は bridge が残っていても
`no identities registered for this project` と出る。停止の確認は上の手順 5 のように
pid を直接見る。同じ理由で `despawn.sh` の `status=forced` も証明にならない
（ペイン close も `reset.sh` も失敗を握り潰したうえで常に `forced` を返す）。

**プロジェクト共用の `codex app-server` は残る。** 次の spawn が使い回すので放置で
よい。明示的に落とすなら `delivery.sh set off codex "$(pwd)"`（`stop_codex_bridge`
が bridge と app-server の両方を落とす）だが、**delivery 設定まで `off` になる**ので
使ったら `set monitor` で戻すこと。

**起動しっぱなしにしない理由**: codex CLI が居なくなっても bridge プロセスだけが
生き残ることがあり、その状態で `mediaferry-reviewer` 宛に送るとメッセージを黙って
飲み込む。手で `herdr pane close <pane_id>` だけした場合がこれに当たる（登録が
残るので bridge も残る。ペインを閉じても SessionEnd フックは走らない）。

**この状態を bridge の `kill` で直そうとしない。効かない。** 登録と app-server が
残っている限り launcher の子は生きていて、pidfile が消えたのを見て bridge を
起動し直す（`codex-bridge-launcher.sh` の再利用判定。pidfile が無い / pid が死んで
いる枝は、そのまま起動へ落ちる）。**ペインを閉じただけなら placement レコードは
残っているので、`despawn.sh ... --force` がそのまま使える**（消すのは graceful の
`free` 分岐と `--force` 自身だけ）。それも通らないときは
`delivery.sh set off codex "$(pwd)"` で launcher の寿命元である app-server ごと
落とし、`set monitor` で戻す。

（2026-08-18 に確認。graceful → `--force` の順で打って詰まり、`--force` を最初から
打つ手順で spawn から片付けまで 1 サイクル通ることを実測した。）

**spawn は前回のセッションを resume する。レビューは `--fresh` で回す。**

`spawn.sh` は **role（team + agent 名）ごとに前回の CLI セッション id を憶えている**
（`~/.agents/skills/agmsg/run/role-session.<team>__<agent>`）。codex の `type.conf` は
`resume_arg=resume` なので、起動コマンドは `codex resume <uuid> …` になり、**前回の
文脈を引き継いで立ち上がる**。`despawn --force` はこの記録を消さないので、次の spawn も
同じセッションに戻る（rollout が消えていれば fresh に倒れる）。

継ぎ足しなので**コンテキストは巡ごとに積み上がる**。5・6 巡目を同じセッションで回した
時点の実測は、rollout 5.4 MB、`last_token_usage.input_tokens` 141,233 に対して
`model_context_window` 258,400（約 55%）。このまま行くと 7〜8 巡目で窓に当たり、
codex 側の自動圧縮が過去巡を要約に落とす。

**7 巡目からは、毎巡 `--fresh` を付ける。**

```bash
$S/spawn.sh codex "$REVIEWER" --project "$(pwd)" --fresh
```

理由は 2 つ。(1) レビューに要る文脈は**リポジトリ側に全部ある**（依頼文で対象コミットと
重点箇所と `HANDOFF.md` §3 を渡している）ので、継ぎ足しの利点が小さい。(2) 同じ
セッションのレビュー役は**自分の過去の判断に引きずられる**ので、独立した目としては弱く
なる。

**「1 度 `--fresh` にすれば以後も新しい」ではない。** `--fresh` で起動すると role の
記録は**その新しいセッション**に置き換わる（実測: 7 巡目の spawn 直後に
`session=` が新 uuid になった）。次に `--fresh` 無しで起動すると、今度はその
セッションを継ぎ足す。**毎回付ける。**

fresh のレビュー役は前巡を知らないので、依頼文は自己完結させる（対象コミット、
背景 1 段落、先に読ませる docs、重点箇所）。7 巡目の依頼文がその形。

**やり取りの実務。**

- **返信は待ち構えなくていい。** monitor モードなので、codex の返信は Monitor
  （`agmsg inbox stream`）に流れてくる。かつては `history.sh` を回して待って
  いたが、その必要は無い。待ち時間の目安は、4800 行の計画のレビューで
  返信まで約 6 分だった
- **長文メッセージは配送が遅れる。** 変更点の全文を貼らず commit hash と
  ファイル名を渡すのは、上の「レビューの依頼」とは別に agmsg 側の理由からも
  正しい
- **過去ログは `history.sh <team> <agent> <件数>`。** 既定は 20 件で、全件を
  吐く口は無い（3 番目の引数が件数。数字以外を渡すと黙って 20 に戻る）
- **名簿は `team.sh <team>`。** チーム名は名前空間なので、別チームに同名の
  メンバーが居ても衝突しない（一意であることが要るのはチームの中だけ）
- **codex の生死は `delivery.sh status codex` の `Codex bridge:` 行で見る。**
  `watch processes:` の数は claude-code 側の `watch.sh` を数えたもので、
  `delivery.sh status claude-code` に出る別物。codex 側の状態ではない

**2026-08-18 より前のレビュー履歴は残っていない。** この日 agmsg 自体がリセット
され、チーム定義も履歴も消えた。Phase 3 の 2 巡目までは `chezmoi` チームの codex
とやり取りしていたが、そのチームごと無い。`mediaferry` チームの旧名簿
（`deckhand` / `lookout`）も同様。探しても出てこないので、探さないこと。

---

## 6. 開発コマンド

```bash
uv sync --all-packages        # --all-packages が必須。素の sync ではメンバーが入らない
uv run pytest
uv run pytest -m needs_root     # ユーザ名前空間が使える環境でのみ通る
uv run pytest -m needs_system   # 実プロセスを起動する（E2E の土台）
uv run pytest -m needs_immich   # 実 Immich が要る（下記の env）
uv run ruff check . && uv run ruff format --check .

npm --prefix web ci
npm --prefix web test           # vitest
npm --prefix web run lint / typecheck / build
npm --prefix web run test:e2e   # Playwright（実プロセス + fake broker + fake Immich 2 台）
npm --prefix web run typegen    # OpenAPI から web/src/api/types.ts を作り直す
```

**API を変えたら型を作り直す。** 忘れても `test_api_types_are_current.py` が
落とすので気づける（npm が無い環境でも回る）。

**実 Immich のテストは接続情報を env で渡す。** 会話や履歴に鍵を出さないよう、
リポジトリの外のファイルに置いて読み込む運用にしてある。

```bash
set -a; . ~/.config/mediaferry/test-immich.env; set +a   # URL と API キー
uv run pytest -m needs_immich
```

アプリの起動は `python -m mediaferry`（`BIND_HOST` の既定は `127.0.0.1`）。
画面はビルド済み資産を `MEDIAFERRY_WEB_ROOT`（既定 `/srv/web`）から配る。
開発中は `npm --prefix web run dev`（`/api` をローカルへ proxy する）。
スパイクの再実行は TrueNAS ホストで。手順は `phase0-findings.md` と
`compose.spike.yaml` のコメントにある。

---

## 7. 持ち越している判断

| 項目 | 状況 |
| --- | --- |
| 実 USB での確認 | `phase1-manual-checklist.md` に手順を用意済み。未実施 |
| **`0007` が既存 DB に再検証を要求する** | 相手由来の観測（`remote_user_id` / `server_instance_id` / `remote_asset_id`）を捨てるので、開いた直後はどの宛先も「向き先の記録が無い」で**閉じる**（送信は始まらない）。**宛先を保存し直す（PATCH）と新しいリビジョンに今の観測が入って直る。** 送信済みレコードの識別子は宛先ごとの再確認がチェックサム照合で戻す |
| **`0005` を版を足さずに書き換えた** | 古い `0005` を適用済みの DB は runner が `MigrationError` で開けない（配布前なので開発用 DB は作り直す前提）。**以後この手は使わない** —— 7 巡目の blocker になった |
| 認証の既定 | **off のまま**（利用者の判断）。`BIND_HOST` の既定も loopback。`TRUSTED_HOSTS` は IP と `localhost` を既定で通し、ホスト名だけ許可制 |
| フロントの依存 | `web/package-lock.json` を追跡している。Playwright のブラウザは `npx playwright install chromium` で入れる（CI で回すなら `--with-deps`） |
| mtime の解釈 | `timestamps.py` は「カードの時刻欄に UTC オフセットが無い」前提。チェックリスト 11 番で実測する。**派生物の mtime も同じ前提に乗る** |
| ~~取り込み側のリースの穴~~ | **塞いだ**（Phase 2 の Task 7）。`_with_lease_pulse` が共通の `_publish` に入ったので、16 GiB のコピー後の `os.fsync` と ffprobe も守られる。回帰テストは `test_publisher.py::test_a_slow_fsync_does_not_lose_the_lease` |
| `_publish` の外の `fsync_dir` | ジョブ用ディレクトリを作った直後の `fsync_dir` は `_with_lease_pulse` の外にある。ディレクトリの fsync はメタデータだけなので実運用では一瞬で終わるが、極端に遅い環境では守られていない |
| `disposition.attached_pic` | 結合の「最初の映像ストリームのみ」の判定が、埋め込みサムネイルをこれで見分ける。実機の DJI ファイルで立っているかは未確認（`keep_streams.video` が `primary` の間は影響しない）。チェックリスト 12 番で見る |
| TS フォールバックの実運用 | Phase 0 の実測では DJI は concat 経路で通っており、TS 経路は**まだ実データで走っていない**。テストは lavfi のクリップで両経路を通す（`tmcd` の脱落まで再現している） |
| サイズ検査の許容誤差と合成クリップ | 2% は 16 GiB 級の実ファイルが前提（オーバーヘッド 0.002%）。数百 KB の合成クリップでは 7〜8% ずれるので、e2e は「不合格でも公開され、採用すれば選択肢に出る」経路で通している |
| 5 パート連続録画（70 GiB 級）のアップロード | 28.36 GiB は完走した。同じ経路で扱える見込みだが未実測。タイムアウトは比例して伸びる |
| Canon EOS 70D のプロファイル | **書いたが実データを一度も見ていない**（Task 4）。`merge` は無効、`hints.usb_ids` は空。実カードでの確認は §1「残っていること」3 |
| **`0011`（`captured_at_revision_id`）** | Task 6 で入れる。**まだ無い。** `0010` までは適用済み |
| **`main` へのマージ** | `phase5-generalization` は 6 コミット。Task 9 まで終えてからマージする |
| 認証を既定 off のままにするか | ユーザの判断で off。`BIND_HOST` の既定を loopback にし、認証無効で非 loopback にバインドしていたら警告する緩和のみ |
| Phase 1〜3 を配布可能リリースにしない | 認証と CSRF が入る Phase 4 より前に LAN へ公開しない |
| SSE（`GET /events`） | Phase 4 の Web UI と一緒に入れる。Phase 1 は `GET /api/jobs/{id}/events?after_seq=` のポーリング |

---

## 8. 作業の進め方（この案件で有効だったこと）

**Phase 5 で分かったこと**（フェーズを跨いで効く）:

- **自分が書いたテストも素通りする。** Phase 5 の実装中、**書いた直後のテストが
  既定値と一致するだけで通っていた**ことが 1 度あった（`provisional` の永続化。
  fixture が `False` で DB の既定値も 0）。**両方の値を通す筋書きを作ってから
  実装する**
- **変異試験の素通りは、まずテストの穴を疑う。** Phase 5 では素通り 11 件のうち
  **7 件がテストの穴**で、構造的に検出できないのは 3 件だけだった（残り 1 件は
  変異の当て方が悪かった）。§5 の表に型をまとめてある
- **既存のテストが落ちたら、まず「挙動が正しく変わったのか」を見る。** Task 4 で
  4 件落ちたうち 1 件は、差し替えカードの中身がまさに Canon の構成だったため
  `canon-eos` として確定するようになったもの。**テストを通すために直すのではなく、
  新しい挙動をより直接的に書く形へ変えた**（`profile_slug` が変わることを見る）
- **試験の土台の弱さが、契約の検証を素通りさせる。** `broker_factory` が毎回同じ
  接続を返していたので「watcher は専用の接続を持つ」を検証できなかった。
  **土台を直してから契約を試す**
- **計画に書いた対処が、実装を読むと成立しないことがある。** Task 6 の
  `awaiting_datetime_approval` への差し戻しは、実装を読んで `needs_recheck` へ
  変えた（その状態は `tagging` からしか入れず、現在値の観測に Immich の
  クライアントが要る）。**レビューが来る前に自分で見つけられた**

**Phase 4 で分かったこと**（フェーズを跨いで効く）:

- **試験の土台を先に作る。** E2E の土台（実プロセス + fake broker + fake Immich 2 台）を
  画面より前に作ったので、SSE が `TestClient` で詰まったときに「実プロセスでは動く」と
  切り分けられた。UI の型が API とずれていたことも、**React の例外**として教えてくれた
  （`pageerror` を拾う）
- **試験できない層は、試験できる層へ寄せる。** SSE は `TestClient` で試せないので、
  位置の決め方と資源の返し方は生成器を直接呼ぶ単体試験に、線の上の挙動は実プロセスに
  分けた。**片方だけだと、変異試験に載らないか、本物を通らない**
- **「実装した」と「つながっている」は別。** API はあるのに画面から呼べない機能が 4 つ
  あり、E2E が緑のまま素通りしていた。**受け入れの経路に入っていない機能は、無いのと同じ**
- **素通りした変異は、まずテストの当て方を疑う。** Phase 4 で素通りした 9 件のうち 7 件は
  テストの穴（1 回の poll で届く範囲しか見ていない、途中まで書けてから失敗する経路を
  通っていない、テストが互いをマスクしている）だった。**構造的に検出できないのは 2 件だけ**

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
  「暗号化は無意味」という自分の理屈は誤りで、指摘を受けて撤回した。
  Phase 2 の計画では 12 件を反映し、1 件（検証器の版を `input_digest` に入れる）
  を**設計の定義を根拠に退けて、先方も受け入れた**
- **レビューは 1 巡で終わらせない。** Phase 2 の計画では、1 巡目の blocker 4 件を
  直した後の 2 巡目で、**さらに blocker が 2 件出た**。しかもどちらも
  「直した箇所の周辺」だった（pulse を read loop に入れたが、その後の `fsync` と
  ffprobe が守られていない／キャンセル例外を上げると `JobRunner` が `failed` に
  する）。**修正が新しい境界を作るので、そこをもう一度見せる**
- **既存コードとの接続部を疑う。** Phase 2 で見つかった blocker のうち 2 件は、
  新しいコードではなく**既存の `JobRunner` と `ArtifactPublisher` との境界**に
  あった。片方は Phase 1 の取り込み側にも同じ形で存在していた（§7）
- **計画にコードを全部書くと、レビューで実際の穴が出る。** 「ここで heartbeat を
  打つ」と散文で書いていたら、`fsync` と ffprobe が守られていないことは
  見つからなかった。手順の順序と例外の流れまで書いてあったから指摘できた
- **計画の「検出できない変異」を鵜呑みにしない。** Phase 2 では計画が検出不能と
  していた 5 件のうち 4 件を、テストを 1 つ足すだけで固定できた（`LeaseLost` の
  待ち、`fsync` の囲み、`truncated`、片側のフレーム判定）。「観測には競合の
  再現が要る」と書いてあっても、たいていは決定的に組める
- **計画が「既存のテストが落ちる」と書いていても、そのテストの実在を確かめる。**
  Phase 2 では `test_a_short_write_is_aborted` を前提にした変異があったが、
  その名前のテストは Phase 1 に存在しなかった
- **合成データは実データの比率を持たない。** lavfi の小さいクリップは MP4 の
  オーバーヘッドが 7〜8% を占め、実機（0.002%）を前提にした 2% のサイズ検査に
  必ず落ちる。閾値の妥当性を合成データで測らない
- **実装で初めて分かることが残る。** Phase 2 では、計画に無かった処理が 1 つ要った
  （TS 片の並びを揃える `_ts_layout`）。`concat:` が生バイトを継ぐことは、
  実際に 2 本つないでみるまで表に出なかった
