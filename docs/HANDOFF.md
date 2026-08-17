# mediaferry 引き継ぎ資料

最終更新: 2026-08-18
ブランチ: `feat/mediaferry`（`main` から 24 コミット）

このファイルは、別セッションが作業を引き継ぐための出発点。
**まずここを読み、次に `phase1-plan.md` を読む。**

---

## 1. 現在地

**Phase 1 の実装計画は完成してレビューも通った。次は計画の実行（Task 1 から）。**

| Phase | 内容 | 状態 |
| --- | --- | --- |
| 0 | スパイク。未検証項目の実測とブローカーの最小実装 | **完了** |
| 1 | 基盤 + 取り込み。`ArtifactPublisher` / `Reconciler` / DB スキーマ / scan / import | **計画完成・レビュー済み。実装未着手** |
| 2 | 結合 | 未着手 |
| 3 | Immich 同期（転送先プロファイル、状態機械） | 未着手 |
| 4 | Web UI | 未着手 |
| 5 | 汎用化（Canon、プロファイル編集 UI、複数デバイス） | 未着手 |

仕様書（`design.md`）は codex のレビューを 6 巡、実装計画（`phase1-plan.md`）は
さらに 6 巡通している。**計画の最終判定は「blocker なし。着手して問題ない」**
（2026-08-17）。

計画は 25 タスクで、各タスクが「失敗するテストを書く → 最小実装 → 検証 →
コミット」で完結する。**着手は Task 1（DB 接続とマイグレーション）から。**
Task 7〜12・15・16・22 は互いに独立なので、並行して進めるならこの 9 つ。

### 検証状態

```
uv run pytest                  77 passed
uv run pytest -m needs_root     1 passed   ← detached mount の実証
uv run ruff check .            All checks passed
uv run ruff format --check .   25 files already formatted
```

---

## 2. 成果物の場所

| ファイル | 内容 | 追跡 |
| --- | --- | --- |
| `docker/mediaferry/docs/design.md` | **設計仕様書。正本。** | ✅ |
| `docker/mediaferry/docs/phase1-plan.md` | **Phase 1 の実装計画。次はこれを実行する。** 末尾の「レビュー記録」に 6 巡分の判断と根拠 | ✅ |
| `docker/mediaferry/docs/phase0-findings.md` | Phase 0 の実測結果と設計への反映 | ✅ |
| `docker/mediaferry/docs/HANDOFF.md` | このファイル | ✅ |
| `docker/mediaferry/{protocol,mountd,app,spikes}/` | Phase 0 の実装 | ✅ |
| `docs/superpowers/specs/2026-08-17-mediaferry-design.md` | 旧・仕様書。`design.md` へ移した残骸 | ❌ |
| `docs/superpowers/plans/2026-08-17-mediaferry-phase0.md` | Phase 0 実装計画。実行済みで役目を終えた | ❌ |

**`docs/superpowers/` は `~/.gitignore_global` で除外されている。** 仕様書を
`docker/mediaferry/docs/design.md` へ移したのはこのため。Phase 1 の計画も
`docs/superpowers/plans/` に書くと追跡されないので、**重要なものは
`docker/mediaferry/docs/` に置く**こと。

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

### agmsg（codex とのやり取り）

- **watcher が落ちることがある。** `delivery.sh status` の
  `watch processes: N alive` を確認する。0 なら Monitor を起動し直す。
  落ちている間のメッセージは通知されず、履歴を手で見るまで気づかない
- `history.sh` は全件を渡すと `sqlite3: Argument list too long` で落ちる。
  **件数を指定する**（`history.sh chezmoi claude-code 3`）
- **長文メッセージは配送が遅れる。** 変更点の全文を貼らず、ファイルを読ませて
  §21 の変更履歴を指す方が確実
- codex がレビューする版が**編集の反映前**だったことがある。送る前に
  仕様書の mtime を確認するか、送信後に「最新版か」を確認させる

---

## 5. 次にやること

### Phase 1 の範囲（`design.md` §20）

> 共通の `ArtifactPublisher` / `Reconciler` の契約、DB スキーマとマイグレーション、
> プロファイルリビジョン（編集 UI は後でも ID の記録は今から）、既知 DJI カードの
> 手動 scan / import、crash consistency テスト一式。API のみ、loopback バインド。
>
> 完了条件: 実 USB で取り込め、§9.3 の任意の手順で落としても reconciliation が回収する。

### 着手手順

**計画は書き終わっており、レビューも通っている。次は実行だけ。**

1. `docker/mediaferry/docs/phase1-plan.md` の Task 1 から順に実行する。
   各タスクは「失敗するテストを書く → 失敗を確認 → 最小実装 → 通ることを確認 →
   コミット」で完結する
2. **変異試験のステップを省かない。** レビューで「テストが修正を検出しない」
   指摘が 3 件出ている（回帰テストが、直す前の実装でも通る書き方だった）。
   修正を元に戻して落ちることを毎回確かめる
3. 計画から外れる判断をしたら、その場で計画側にも書き戻す
4. 詰まったら codex に相談する（下記）

### 計画で確定していて、蒸し返してはいけない判断

`phase1-plan.md` の「レビュー記録」に 6 巡分の根拠がある。特に間違えやすい 3 つ。

| 判断 | 理由 |
| --- | --- |
| **DB 接続はスコープごとに 1 本。同時共有しない** | トランザクションは接続に属していてスレッドには属さない。API の書き込みが publisher の `BEGIN IMMEDIATE` に混ざる。`check_same_thread=False` は必要（作るスレッドと使うスレッドが違う）だが、共有してよいという意味ではない |
| **`VolumeObservation` は接続の同一性であって媒体の同一性ではない** | mountd の `generation` は観測した集合の指紋が変わったときだけ進む。polling の合間に同じ UUID・容量のカードが同じノードで差し替わると据え置きになる。**開いた dirfd を使い回す根拠にできない**（判定は毎回開き直し、ジョブの handle は release で閉じる） |
| **staged 以降の失敗を「取り込み失敗」として扱わない** | ファイルは検証済みで公開に必要な情報は永続化済み。reconciliation が完遂するので、`source_entry` を failed に戻すと次のスキャンで新規と判定されて二重に取り込む |

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

スパイクの再実行は TrueNAS ホストで。手順は `phase0-findings.md` と
`compose.spike.yaml` のコメントにある。

---

## 7. 持ち越している判断

| 項目 | 状況 |
| --- | --- |
| 5 パート連続録画（70 GiB 級）のアップロード | 28.36 GiB は完走した。同じ経路で扱える見込みだが未実測。タイムアウトは比例して伸びる |
| Canon EOS 70D のプロファイル | Phase 5。カードリーダー経由の UMS のみ対応と決定済み（PTP はスコープ外） |
| 認証を既定 off のままにするか | ユーザの判断で off。`BIND_HOST` の既定を loopback にし、認証無効で非 loopback にバインドしていたら警告する緩和のみ |
| Phase 1〜3 を配布可能リリースにしない | 認証と CSRF が入る Phase 4 より前に LAN へ公開しない |

---

## 8. 作業の進め方（この案件で有効だったこと）

- **理屈で結論を出さず、測る。** dirfd の `..` 脱出も、結合の 11.4% 欠損も、
  実際に動かして初めて分かった。どちらも実装後に気づいたら手戻りが大きい
- **判定は終了ステータスで行う。** 出力を読んで「動いていそう」で通さない。
  スパイクの CLI はすべて exit code で合否を返す設計にしてある
- **テストが素通りしていないか変異試験で確かめる。** 修正を元に戻して
  落ちることを確認する。Phase 0 では 4 回やって毎回有効だった
- **codex のレビューは鵜呑みにしない。** 6 巡で反論したのは 2 点だけだったが、
  その 2 点（SHA-256 の追加、認証の必須化）は退けて正解だった。逆に
  「暗号化は無意味」という自分の理屈は誤りで、指摘を受けて撤回した
