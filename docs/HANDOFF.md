# mediaferry 引き継ぎ資料

最終更新: 2026-08-18
ブランチ: `main`（単独リポジトリ、96 コミット。先頭は Phase 3 のレビュー 4 巡目の反映）

このファイルは、別セッションが作業を引き継ぐための出発点。
**まずここを読み、次に `design.md` §20 と該当フェーズの計画を読む。**

---

## 1. 現在地

**Phase 1〜3 は実装・検証とも完了。** Phase 3 は計画のレビューを 2 巡してから
14 タスクを実行し、**実装差分のレビューを 2 巡（3・4 巡目）回して全件反映済み**。
**残るのは実 Immich に当てる `needs_immich` の 3 件。** 次のフェーズは 4（Web UI）。

**レビューの巡目と、そこで出たもの**（詳細は `phase3-plan.md` の「レビュー記録」）:

| 巡 | 対象 | 結果 |
| --- | --- | --- |
| 1 | `phase3-plan.md`（**文書に埋め込んだコード**） | blocker 8 / major 6 / minor 1 |
| 2 | 同上（1 巡目の修正後） | blocker 8 / major 5 / minor 1。**文書のコードは型検査も実行もできない**ので、機械的な欠陥が毎巡残った |
| 3 | 実装差分 `7f6794c..2d04165` | blocker 1 / major 4 / minor 1。**文書では出なかった層**（SQLite の並行性、相手が値を選べる応答、実装した順序） |
| 4 | 3 巡目の修正差分 `2d04165..bf548df` | blocker 2 / major 3 / minor 1。**全件が「直した箇所の周辺」** |

**この傾向は Phase 2 と同じ。** 直した箇所の周りをもう一度見せると、また出る。

| Phase | 内容 | 状態 |
| --- | --- | --- |
| 0 | スパイク。未検証項目の実測とブローカーの最小実装 | **完了** |
| 1 | 基盤 + 取り込み。`ArtifactPublisher` / `Reconciler` / DB スキーマ / scan / import / API | **完了**（実 USB の確認だけ残り） |
| 2 | 結合。検出 / ffmpeg / 検証 / 公開 / 回収 / 選択肢 / API | **完了**（`phase2-plan.md` の 14 タスク。実装との差分は書き戻し済み） |
| 3 | Immich 同期（転送先プロファイル、状態機械、タグ、日時補正、複数宛先） | **完了**（`phase3-plan.md` の 14 タスク。codex レビュー 4 巡を反映。実装との差分は書き戻し済み。**実 Immich での確認だけ残り**） |
| 4 | Web UI | 未着手 |
| 5 | 汎用化（Canon、プロファイル編集 UI、複数デバイス） | 未着手 |

### 検証状態

```
uv run pytest                  838 passed, 4 deselected
uv run pytest -m needs_root      1 passed   ← detached mount の実証
uv run pytest -m needs_immich    3 件       ← 実 Immich が要る。既定では走らない
uv run ruff check .            All checks passed
uv run ruff format --check .   144 files already formatted
```

**結合のテストは実 ffmpeg を使う**（`shutil.which("ffmpeg")` が無いときだけ skip）。
開発コンテナには `~/.local/bin/ffmpeg` が入っている。

`ruff format` の件数がソースの本数より多いのは、`docs/` を `extend-exclude` で
外していても**ルート直下の `CLAUDE.md` は対象に入る**ため（Markdown 内の
コードブロックが整形される）。

`docker restart` や電源断に相当する試験は、§9.3 の手順 11 段すべてで子プロセスを
`os._exit` で落として **import / merge / merge_prepared の 3 経路**を回収できる
ことまで確認済み。回収後は結合グループの状態（`detected` か `merged`）も
assert している。

### 残っていること

1. **実 Immich での確認（`-m needs_immich` の 3 件）。** `app/tests/test_immich_live.py`。
   `MEDIAFERRY_TEST_IMMICH_URL` と `MEDIAFERRY_TEST_IMMICH_KEY` を渡して実行する。
   **タグの取得・作成・付与と日時の更新は Phase 0 のプローブに入っていない**ので、
   ここが初めての実測になる。形が違っていたら `adapters/immich.py` だけを直す。
2. **実 USB での手動確認（`phase1-manual-checklist.md` の 12 項目）。**
   開発コンテナ（入れ子の非特権 LXC）ではマウントが AppArmor に阻まれるので、
   TrueNAS ホストで実行する必要がある。

   特に **11 番（mtime の解釈の実測）は実装の前提の確認**。前提が崩れていれば
   `timestamps.py` の `_wall_clock` と `publisher._collision_stamp` を直す。
   Phase 2 の派生物の mtime（`merger._recording_end_ns`）も同じ前提に乗っている。
   **12 番（`attached_pic`）は Phase 2 で足した項目**（`streams._is_thumbnail`）。
3. **実データでの TS フォールバックの確認。** テストは lavfi のクリップで両経路を
   通しているが、実 DJI では Phase 0 の時点で concat 経路が通っており、TS 経路は
   まだ実データで走っていない。

---

## 2. 成果物の場所

| ファイル | 内容 | 追跡 |
| --- | --- | --- |
| `docs/design.md` | **設計仕様書。正本。** | ✅ |
| `docs/phase1-plan.md` | Phase 1 の実装計画。**実行済み**。実装との差分は都度書き戻してある | ✅ |
| `docs/phase2-plan.md` | Phase 2（結合）の実装計画。**実行済み**。codex のレビュー 2 巡を反映し、実装で外れた判断と検出できなかった変異を書き戻してある | ✅ |
| `docs/phase3-plan.md` | Phase 3（Immich 同期）の実装計画。**実行済み**。codex のレビュー 2 巡を反映し、実装で外れた判断と検出できなかった変異を書き戻してある | ✅ |
| `docs/phase1-backup.md` | バックアップとリストア、再構築できる範囲（§18-4） | ✅ |
| `docs/phase1-manual-checklist.md` | 実 USB での確認手順 | ✅ |
| `docs/phase0-findings.md` | Phase 0 の実測結果と設計への反映 | ✅ |
| `docs/HANDOFF.md` | このファイル | ✅ |
| `{protocol,mountd,app,spikes}/` | 実装 | ✅ |

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
| **相手から受け取った識別子は adapter の境界で検める** | `assetId` は `remote_asset_id` として保存され API 応答にも出る。タグの id は次の要求の URL に入る。**「正常な形」で鍵を返されると、例外文を綺麗にしただけでは平文が通る。** 4 巡目の blocker |
| **guard は prepare → preflight → prepare の 2 段** | 再確認は相手待ちでリース（60 秒）より長くなりうる。前だけに置くと「直前に確かめた」保証が消える |
| **既存資産へのタグ付けの前にも §10 を見直す** | 「もうリモートにあるので取り消せない」は、こちらが作った資産にしか当てはまらない |
| **再確認の結果は 1 つのトランザクションで書き、分割も Rechecker 側で行う** | 1 行ずつ commit すると中途半端な状態が残る。adapter に全件を渡すと、内部ループの合間にキャンセルを見られない |
| **既存 DB の観測値も移行で指紋へ変換する**（`0005`） | 変換しないと旧平文が残り、preflight が全宛先を「向き先が変わった」と誤判定する。`destination_revision` は不変なので trigger を外して変換し作り直す。**SQLite に SHA-256 が無い**ので、runner が `mediaferry_fingerprint` を接続へ登録する |
| **既に無効化された行の理由と時刻は上書きしない** | claim を持っている間に別の経路で無効化されることがある。上書きすると監査で見えるのが二次的な文言に変わる |
| **fake Immich はループバックで実際に listen させる** | httpx 0.28 の `ASGITransport` は非同期用で、同期の `httpx.Client` から使えない。実物の httpx を通すのでプロトコルの取り違えも見逃さない |

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

1. **実 Immich に `-m needs_immich` を当てる**（§1「残っていること」1）。
   タグと日時更新のエンドポイントは Phase 0 で実測していない
2. **5 巡目のレビューを依頼するかを決める。** 3・4 巡目とも、**指摘は全件
   「直した箇所の周辺」から出ている**（Phase 2 の 2 巡目と同じ傾向）。4 巡目の
   修正で新しくなったのは、adapter の識別子検査・`0005` の移行・2 段 guard・
   `stamp_many`。回すならそこを見せる
3. **`phase1-manual-checklist.md` を TrueNAS ホストで実行する。** 特に 11 番
   （mtime の解釈）は実装の前提の確認なので、結果を `phase0-findings.md` に残す。
   12 番（`attached_pic`）は Phase 2 で足した項目
4. **Phase 4（Web UI）の計画を書く。** 範囲は `design.md` §20 と §13。
   ここで初めて認証・CSRF・非 loopback バインドが入る。**Phase 3 で先送りした
   ものが 2 つある**: 結合グループの破棄と再結合（`superseded_by_id`）、
   `GET /uploads/pending-approval` の差分表示

1・2 は独立して進められる。3 は TrueNAS ホストが要る。

### Phase 3 で実装しなかったもの（意図的な先送り）

- **`UPLOAD_CONCURRENCY` は効かない。** 逐次実行に倒してあり、多重化は
  Phase 4 のワーカー多重化と一緒に入れる
- **マイグレーションは `0005_fingerprint_remote_identity.sql` の 1 本だけ。**
  テーブルは `0004_destinations_and_uploads.sql` で足りていた。`0005` はデータの
  作り替え（観測値 → 指紋）で、レビュー 4 巡目で足したもの
- **`GET /uploads/pending-approval` は作っていない。** `GET /uploads?state=...`
  で絞れるので足りている。差分（現在のリモートの日時 vs 補正案）の表示は
  画面と一緒に決める

### 変異試験のやり方（ドライバはリポジトリに無い）

**ドライバはセッションの scratchpad に置いて使い捨てにしている。** 引き継いだら
下を書き直す（20 行ほど）。要点は「中断されても原文へ戻せること」と
「`PYTHONDONTWRITEBYTECODE=1` を付けて呼ぶこと」。

```python
# mutate.py <対象ソース> "<テストファイル…>" <変異定義 JSON>
# JSON は [{"name": ..., "old": ..., "new": ...}] の配列。
# 開始時に <対象>.orig を残し、次回起動時に残っていればそこから復元する。
# 各変異ごとに: old を new に 1 箇所だけ置換 → pytest -q --tb=no -rf →
#   落ちたテスト名を「[検出]」、1 つも落ちなければ「[素通り]」として出す。
# 最後に必ず原文へ戻す（finally）。
```

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
| mtime の解釈 | `timestamps.py` は「カードの時刻欄に UTC オフセットが無い」前提。チェックリスト 11 番で実測する。**派生物の mtime も同じ前提に乗る** |
| ~~取り込み側のリースの穴~~ | **塞いだ**（Phase 2 の Task 7）。`_with_lease_pulse` が共通の `_publish` に入ったので、16 GiB のコピー後の `os.fsync` と ffprobe も守られる。回帰テストは `test_publisher.py::test_a_slow_fsync_does_not_lose_the_lease` |
| `_publish` の外の `fsync_dir` | ジョブ用ディレクトリを作った直後の `fsync_dir` は `_with_lease_pulse` の外にある。ディレクトリの fsync はメタデータだけなので実運用では一瞬で終わるが、極端に遅い環境では守られていない |
| `disposition.attached_pic` | 結合の「最初の映像ストリームのみ」の判定が、埋め込みサムネイルをこれで見分ける。実機の DJI ファイルで立っているかは未確認（`keep_streams.video` が `primary` の間は影響しない）。チェックリスト 12 番で見る |
| TS フォールバックの実運用 | Phase 0 の実測では DJI は concat 経路で通っており、TS 経路は**まだ実データで走っていない**。テストは lavfi のクリップで両経路を通す（`tmcd` の脱落まで再現している） |
| サイズ検査の許容誤差と合成クリップ | 2% は 16 GiB 級の実ファイルが前提（オーバーヘッド 0.002%）。数百 KB の合成クリップでは 7〜8% ずれるので、e2e は「不合格でも公開され、採用すれば選択肢に出る」経路で通している |
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
