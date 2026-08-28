# Phase 15 設計 — 送り直しをやめ、再確認から通常経路へ戻す

**この文書が実装の正本。** 現在の仕様は [`../design.md`](../design.md)、決着した判断は
[`../decisions.md`](../decisions.md)。

## この回が何か

**実機（2026-08-28）で、くわしくの「送り直す」が動かないことが分かった。**
押しても送信が始まらず、画面は「送っている最中です」と表示し続ける。

原因は動線が閉じていないこと。`PhotoDetail` の「送り直す」は
`POST /uploads/{id}/requeue` を叩いて `complete` → `pending` に戻すだけで、
**送信ジョブを積まない**。`pending` の presence は `sending` なので、
画面には「送っている最中です」と出る。**送っていないのに送っていると言う。**

送り直しの経路は 2 つあり、片方だけが閉じていた。

| どこ | 何をする | 動線 |
| --- | --- | --- |
| くわしく（`/photos/:id`）の宛先ごと | `POST /uploads/{id}/requeue`。`complete` → `pending` | **閉じていない**（ジョブを積まない） |
| 設定 › 送り先「送れなかったもの」 | `POST /uploads/{id}/retry` の後に `POST /destinations/{id}/upload` | 閉じている |

**直し方は「閉じていない方を閉じる」ではなく、「専用の経路そのものを無くす」に倒す**
（利用者の判断）。送り直し専用の状態遷移を持つのをやめ、**再確認が「無い」と判定したら
その記録を無効化して、メディアを通常の「まだ送っていない」へ戻す。**

**設計の途中で、スタックの扱いを詰めるうちに穴が 2 つ出た**（§3）。1 つはこの変更が
持ち込むもの（`record_for`）、もう 1 つは前から在るもの（スタックの解除を検知しない）。
どちらもこの回で塞ぐ。

## 決定の表

| # | 決めたこと | なぜ |
| --- | --- | --- |
| 1 | **再確認が消滅と判定した `complete` を、その場で無効化する** | 送り直し専用の状態を持たなくて済む。無効化された記録は「有効な記録」でなくなるので、`GET /media?status=unsent` の定義にそのまま当てはまり、通常経路へ戻る |
| 2 | **`POST /uploads` は無効化された記録を跨いで新しい行を作る** | これが無いと 1 だけでは動かない。いまの `_pair` は `invalidated_at` を見ずに既存行を拾い、`_existing` が「無効化されている」で断る。ホームに「まだ送っていない」と出るのに送れない |
| 3 | **`record_for` も無効化された行を返さない** | 2 の副作用で、同じ `(宛先, epoch, メディア)` に行が 2 つ並ぶ。`record_for` は `ORDER BY` を持たないので**古い無効化済みを返し**、第 2 パスが「相方が無効化済み」と読んで**送り直しても永久に組めない** |
| 4 | **ゴミ箱（`trashed`）は無効化しない** | ゴミ箱に在るものは「無い」の証明ではない。判定は今と同じ（`bulk-upload-check` が `accept` を返したものだけ） |
| 5 | **再確認が、スタックの現存とメンバー集合も照合する** | いまは資産の有無しか見ないので、**Immich 側でスタックだけ解除されても気づかない**。`stack_state = 'stacked'` と、もう存在しない `remote_stack_id` が残り続け、設定 › 送り先の「N 組」が嘘になる |
| 6 | **解けていた組は未評価へ戻す（＝次の第 2 パスで組み直す）** | **利用者の判断。** MF が組むべきと判断した組は、常に Immich へ反映させる。**利用者が手で解除した組も作り直される**——この副作用を承知のうえで、表示と実体が食い違ったまま残る方を避ける |
| 7 | **くわしくから「送り直す」を消し、`POST /uploads/{id}/requeue` も消す** | ボタンが無くなれば API も要らない（`decisions.md`「画面から呼べない API は、機能が無いのと同じ」の裏返し） |
| 8 | **`presence` の `gone` は語彙として残す。移行は入れない** | 変更後は `gone` に留まる行が原理的に出ない。既存 DB はリセットするので移行も要らない。**起きないはずのことが起きたときに、生の enum ではなく本当のことを言うための保険**として残す |
| 9 | **設定 › 送り先の「送り直す」は触らない** | あれは `failed` の再試行で、送信ジョブまで積む閉じた動線。別の話 |
| 10 | **`decisions.md` の「再確認は送り直さない。見えるようにするだけ」を覆す** | 専用経路は動線が閉じておらず、嘘をつく画面になっていた。無効化して通常経路へ戻す形でも、**送信そのものは依然として利用者の明示操作**なので「黙って戻さない」は保てる |

---

## §1 再確認が消滅を無効化する

### いま

`jobs/recheck.py` は消滅と判定した行に `stamp_many` で
`remote_asset_id = NULL, remote_checked_at = now` を書き、警告を 1 本出して終わる。

```python
# **送り直さない。** 見えるようにするだけ。
ctx.emit("warning", "リモートに存在しない資産がある", {"upload_record_id": row["id"]})
```

### こうする

`stamp_many` が、**消滅（`asset_id is None`）と判定した行にだけ**
`invalidated_at = now, invalidated_reason = 'remote_missing'` も書く。

- **同じトランザクションの中で書く。** 観測と無効化が別の取引に分かれると、
  「消えたと記録したが未送信に戻っていない」中途半端な状態が残る
- **CAS の条件は変えない。** `expect_asset_id` / `expect_checked_at` を条件に
  入れる作法はそのまま（照合の最中に動いた行には書かない）
- **`_reopen_stack_of` の呼び出しもそのまま。** `remote_asset_id` が変わるので
  組は開く（§9.11）

`Stamp` に「無効化するか」を持たせるのではなく、**`asset_id is None` を無効化の条件に
する**。消滅の判定は既に呼び出し側（`_action_of` の `accept`）で決まっており、
`stamp_many` に渡る `Stamp` の `asset_id` がそれをそのまま表している。

警告の `emit` は残す。ジョブの記録から「何件がどれだったか」を辿れなくなるため。
文言は「リモートに存在しないので、まだ送っていないものに戻した」に変える。

### 影響

- `records_for_recheck` は `invalidated_at IS NULL` で絞っているので、
  **無効化された行は次の再確認の対象から外れる**（照合が軽くなる）
- §9.11 の第 2 パスは `complete` かつ `invalidated_at IS NULL` を見るので、
  相方が消滅した組は `skipped` になる。資産が無いのだから正しい

---

## §2 無効化された行を跨ぐ 2 か所

### 2.1 `POST /uploads`（`_pair`）

いま `db/uploads.py` の `_pair` は既存レコードを

```sql
SELECT * FROM upload_record
 WHERE destination_id = ? AND target_epoch = ? AND media_file_id = ?
```

で引く。**`invalidated_at` を見ていない。** 無効化した行を拾うので `_existing` が

```python
if row["invalidated_at"] is not None:
    return PairResult(..., "rejected", ..., f"無効化されている: {row['invalidated_reason']}")
```

を返す。**ホームには「まだ送っていない」と出るのに、送ろうとすると断られる。**

検索条件に `AND invalidated_at IS NULL` を足し、無効化された行は**無いものとして扱い、
新しい行を作る**。

これは意味づけの変更ではない。`design.md` §10 の遷移表が既に

> `invalidated_at` が入っている | **再利用しない。** epoch を進めた場合は新しい行を作る

と書いており、**書いてあるとおりにする修正**である（表の但し書きも
「epoch を進めた場合は」から外し、無効化された行は常に新しい行を作る形に直す）。

### 2.2 `record_for`

`record_for`（`db/uploads.py:899`）も同じ条件を持たない。**`_pair` を直すと、送り直した
メディアには `(宛先, epoch, メディア)` が同じ行が 2 つ並ぶ**（古い無効化済みと新しい有効）。
`ORDER BY` が無いので `fetchone()` は `upload_record_by_media` を rowid 順に舐め、
**古い無効化済みを返す。**

`jobs/stacker.py:172` と `:232` が組の相方をこれで引き、`_candidate_of` が
`invalidated=True` の候補を作る → `resolve_group` が断る → **送り直しても永久に
`skipped`**。しかも「相方が無効化済み」という、利用者から見て意味の分からない理由で。

`record_for` にも `AND invalidated_at IS NULL` を足す。相方が無効化済みしか持たない
メディアは `record_for` が `None` を返し、`_candidate_of` の
「この宛先へ送っていない。組は成立しない」に落ちる。**決着は同じ `skipped` で、
理由の文言だけが正確になる。**

`Candidate.invalidated` は**保険として残す**。`unstacked_batch` も `record_for` も
無効化を除くので構造的に到達しないが、「無効化された行が来たら組まない」という
fail-closed をコードから消さない。**検出できない変異として `development.md` に記録する。**

### 安全性

- **表制約の `UNIQUE` を、部分 UNIQUE 索引へ置き換える移行が要る**（§2.3）。
  `upload_record` には `0004` の表制約
  `UNIQUE (destination_id, target_epoch, media_file_id)` が在り、**無効化された行の
  隣に新しい行を作れない**。守りたい不変条件は「**有効な**記録は 1 組につき高々 1 つ」
  であって「行は 1 つ」ではないので、条件を `WHERE invalidated_at IS NULL` に付け替える
- **有効な行は常に 1 つ。** 古い方は無効化されており、新しい部分索引がそれを強制する
- **数え上げは全部そろっている。** `/dashboard` の `unsent_total` と宛先ごとの内訳、
  `/media?status=unsent`、`deletion_blocker`、`_destinations`（くわしくの presence）、
  `merges.py` の編集可否は**すべて `invalidated_at IS NULL` を持っている**。
  今回足すのは `_pair` と `record_for` の 2 か所だけ
- **`_pair` が跨ぐようになる無効化には他の出所もある**（グループの supersede、
  epoch の繰り上げ、`0006` / `0007`）。いずれも安全:
  - supersede された derived は `sendable_clause` の `superseded_by_id IS NULL` で
    落ちるので、`_choose` が rejected を返す
  - 旧 epoch の行は `_pair` が現行 epoch で引くので最初から当たらない

### 2.3 移行 —— 表制約の `UNIQUE` を部分 UNIQUE 索引へ

`upload_record` は `0004` で

```sql
UNIQUE (destination_id, target_epoch, media_file_id)
```

を**表制約**として持つ。SQLite は表制約を後から落とせないので、**テーブルを作り直す**
（`0026` が `media_file` で通した 12 手順と同じ形。`-- mediaferry:foreign-keys-off` の
目印を付け、runner が `PRAGMA foreign_key_check` を COMMIT の前に確かめる）。

置き換え先:

```sql
CREATE UNIQUE INDEX upload_record_live_identity
    ON upload_record (destination_id, target_epoch, media_file_id)
    WHERE invalidated_at IS NULL;
```

**守る不変条件は変わらない** —— 「1 つの (宛先, epoch, メディア) に**有効な**記録は
高々 1 つ」。変わるのは、無効化された行が監査履歴としてその隣に残れることだけ。

**行を使い回す案は採らない。** `upload_record_first_check_immutable` が
`first_check_result` の書き換えを止めるので `origin` の判定をやり直せず、
`design.md` §10 の「無効化された記録は再利用しない」とその理由（**なぜ最初に送信を
許可したかが失われる**）にも反する。

**行を消す案も採らない。** 再確認が破壊的になる —— 相手が誤って `accept` を返した
だけで、その宛先の送信記録が消える。

### 副産物 — `origin` がやり直しになる

新しい記録から送るので、**`origin` の判定も最初からになる**。資産が本当に消えていれば
初回 `checking` は `accept` → `POST /api/assets` が `created` を返す →
**`created_by_us`** になり、**日時補正が自動で通る**（§9.10）。

いまの `requeue` は古い記録を使い回すので、`first_check_result` が前回の観測のまま残り、
送り直した資産が `pre_existing` として承認待ちに積まれうる。この経路が無くなる。

---

## §3 スタックの扱い

### 3.1 片方だけ消えた組は、送り直せば組み直る

**この経路は既存の仕掛けで通る**（今回の変更で壊れないことを確かめた）。

1. `bulk_upload_check`: RAW は `accept`（無い）、JPEG は `reject`（在る）
2. `stamp_many` が RAW を処理するとき資産 ID が `A → NULL` に変わるので、
   **`_reopen_stack_of` が `remote_stack_id` を共有する `stacked` の行を全部**
   未評価へ戻す → **RAW と JPEG の両方**が `stack_state = NULL`
   （「組は 1 つの結果。片方だけ戻すと以後ずっと組めない」）
3. §1 の追加で RAW の記録が無効化される
4. 第 2 パスが JPEG を拾う（`unstacked_batch` は無効化を除く）→ 相方が居ない →
   **JPEG は `skipped`**
5. RAW が「まだ送っていない」に戻る。利用者が通常経路で送る → 新しい記録 →
   `created_by_us` → `complete`
6. 第 2 パスが新しい RAW を拾う → 相方の JPEG は `complete` / `created_by_us` /
   `skipped` → 組が成立。**`mark_stacked` は `stack_state IS NULL OR
   stack_state = 'skipped'` を許す**ので（「見送り済みの相方は引き上げる」）、
   JPEG も一緒に `stacked` になる
7. 送る直前に相手の姿を読み直す → 両方 `stack: null` → **`POST /stacks` で組み直す**

**6 が通るのは §2.2 を直した後だけ。** 直さないと 6 で「相方が無効化済み」と読んで止まる。

### 3.2 スタックだけ解除されたときを検知する

**いまは気づかない。** 再確認は `bulk-upload-check` しか使わず**資産の有無しか見ない**
（`AssetResponseDto.stack` を読まない）。`stacked` の行は第 2 パスの対象外
（`stack_state IS NULL` だけを拾う）ので、相手の姿を読み直す経路にも入らない。
結果、`stack_state = 'stacked'` と**もう存在しないスタックを指す `remote_stack_id`**
が残り、設定 › 送り先の「N 組」が嘘になる。

#### adapter

`ImmichClient.stacks()` を足す。`GET /api/stacks` を**パラメータ無し**で叩き、
`_as_array` → `_stack_from` で読む。`stack_by_primary` と同じ作法で、
**形が違えば protocol error にする**（`_stack_from` がそれを持っている）。

`RemoteStack` は `stack_id` / `primary_asset_id` / `asset_ids` を持つので、
**1 要求で「在るか」と「メンバー集合が一致するか」の両方が照合できる。**

**件数の上限は置かない。** 打ち切ると照合が嘘になる（`records_for_recheck` と同じ）。

#### 再確認の新しい段

**資産の照合（`stamp_many`）の後に走らせる。** 順序に意味がある —— 消滅した資産は
`_reopen_stack_of` で既に組を開いており、開いた行は `stack_state IS NULL` になって
この段の対象（`stacked`）から自然に外れる。逆順だと同じ組を 2 度開く。

1. **対象を集める**: `destination_id` × 現行 epoch × `state = 'complete'` ×
   `invalidated_at IS NULL` × `stack_state = 'stacked'` の行を、`remote_stack_id` で
   まとめる。**0 組なら `stacks()` を呼ばない**（空振りの要求を出さない）
2. **相手を読む**: `stacks()` を 1 回。`with_lease_pulse` で囲む。前後で
   `ctx.cancelled()` と `ctx.assert_lease()` を見る（`bulk_upload_check` と同じ作法）
3. **判定する**（組ごと）:

   | 相手の状態 | すること |
   | --- | --- |
   | 同じ `stack_id` が無い | **解けている。** 未評価へ戻す |
   | 在るが `asset_ids` の集合が我々の組の `remote_asset_id` 集合と一致しない | **崩れている。** 未評価へ戻す |
   | 一致 | 触らない |

4. **書く**: 解けている／崩れている組の行を**全部** `stack_state = NULL,
   remote_stack_id = NULL, stack_reason = NULL` に戻す。`_reopen_stack_of` と同じ形の
   CAS（`target_epoch` と `remote_stack_id` と `stack_state = 'stacked'` を条件に入れる）を、
   **`assert_lease` と同じ 1 つのトランザクション**で当てる
5. **数える**: `RecheckOutcome` に `unstacked` を足し、
   `ctx.emit("info", "組が解けていたので戻した: N 組")` を出す

#### 戻した後

同じジョブの第 2 パスが拾い、**相手の姿を読み直してから**決める（§9.11 の既存の表）。

- 全員 `stack: null`（解除された） → `POST /stacks` で**組み直す**
- 誰かが別のスタックに入っている（崩れていた） → `_adopted` が集合を見て、
  一致しなければ **`skipped`「相手側に別のスタックがある」**

**ループしない。** 組み直した組は次の再確認で一致するので触らない。`skipped` に
落ちた行は `stacked` ではないので、この段の対象に入らない。

#### 承知している副作用

**利用者が Immich で手で解除した組も、次の再確認で作り直される。**

これは §9.11 が `origin` の条件を厳しくしている理由（「利用者が手で作った組を
作り直しうる」）と緊張する。また、この回で決めた「Immich 側の観測は反映するが、
**外部への副作用は利用者の明示操作でしか起こさない**」とも正面から衝突する
（`POST /stacks` は外部への副作用である）。

**それでも組み直す方に倒す（利用者の判断）。** MF が組むべきと判断した組は常に
Immich へ反映させ、表示と実体が食い違ったまま残る方を避ける。
**`decisions.md` に、緊張の中身ごと残す。**

組みたくない組がある場合の逃げ道は、カメラの種類の `stack` 節を切ること
（プロファイル単位）。**1 組だけ外す手段は無い。** それが要るようになったら、
そのときに設計する。

---

## §4 くわしくから「送り直す」を消す

`web/src/screens/PhotoDetail.tsx` の宛先ごとの行から次を消す。

- `requeue()` 関数
- `presence === "gone"` のときのボタン
- 「元のファイルがいま見当たらないので、送り直せません。」の但し書き

**残るのは「サーバを確かめる」だけ。** これが利用者から見た唯一の操作になる:
押す → その宛先の全件を Immich に照合 → 無いものは「まだ送っていない」に戻る →
通常の送る画面から送る。

`missing_at` の但し書きが消せるのは、**`sendable_clause` が既に
`missing_at IS NULL` を見ている**ため。元ファイルが見当たらないメディアは
未送信の候補に出てこない。**条件を 2 か所に持たなくて済む。**

`docs/development.md` の持ち越し「**送り直せない理由が、`missing_at` 以外では消える**」は、
ボタンごと無くなるので落とす。

---

## §5 `POST /uploads/{record_id}/requeue` を消す

| 消すもの | どこ |
| --- | --- |
| `requeue_upload` | `app/src/mediaferry/api/routes_uploads.py` |
| `ErrorCode.NOT_REQUEUEABLE` | `app/src/mediaferry/api/errors.py` |
| `not_requeueable` の文言 | `web/src/api/errors.ts` |
| 型 | `web/src/api/types.ts`（再生成） |

`UploadRepository.check_eligibility` は**残す**。claim（`claim_next`）と
`jobs/uploader.py` も使っている。呼び出し元が 1 つ減るだけ。

---

## §6 記録を直す

| 文書 | 直すところ |
| --- | --- |
| `docs/design.md` §9.10 | 「ゴミ箱と消滅の追跡」から requeue を消し、**消滅は無効化して未送信へ戻す**に書き換え。再確認の段に**スタックの照合**を足す |
| `docs/design.md` §9.11 | 「相手の状態 → すること」の表の手前に、**解けた組・崩れた組を未評価へ戻す**経路を足す |
| `docs/design.md` §10 | 遷移表の「`invalidated_at` が入っている」の但し書き。API 一覧から `POST /uploads/{id}/requeue` を削除 |
| `docs/decisions.md` | (a)「再確認は送り直さない。見えるようにするだけ」を**覆した判断として書き換える**（動線が閉じておらず、嘘をつく画面になっていた）。(b) **解けた組を作り直す判断**を、手で解除した組も作り直されるという緊張ごと足す |
| `docs/user-guide.md` | 「設定 › 送り先の『送り直す』で送り直します」「Immich 側で完全に削除してから…」の案内を、**「サーバを確かめる」を押すと未送信に戻る**形に書き換え。**Immich で組を解除しても再確認で戻ることも書く** |
| `docs/development.md` | 「送り直せない理由が、`missing_at` 以外では消える」は**落とす**（ボタンごと無くなる）。「§9.10 の状態を送り先の一覧が出していない」は**残す**が、`gone` は未送信へ戻るので**ゴミ箱の分だけに縮める**。**`Candidate.invalidated` が到達不能になったことを、検出できない変異として記録する** |

**`gone` は語彙として残す**が、`docs/design.md` に「変更後は原理的に出ない。
出たらどこかが壊れている」と明記する。

---

## §7 テスト

**先に落ちるテストを書き、落ちることを確認してから実装する。**

| # | 何を確かめる | どこ |
| --- | --- | --- |
| 1 | 再確認が、消滅と判定した `complete` を `remote_missing` で無効化する | `test_upload_recheck.py` |
| 2 | **ゴミ箱の資産は無効化しない** | `test_upload_recheck.py` |
| 3 | 無効化の後、`GET /media?status=unsent` にそのメディアが戻る | `test_api_media.py` |
| 4 | `POST /uploads` が無効化された行を跨いで**新しい記録を作る**（いまは rejected になるので、これがまず落ちる） | `test_upload_pairs.py` |
| 5 | `record_for` が、無効化済みと有効が並ぶメディアで**有効な方**を返す | `test_stack_repository.py` |
| 6 | **片方だけ消えた組が、送り直しで組み直る**（§3.1 の 1〜7 を通す） | `test_stacker.py` |
| 7 | 送り直した記録の `origin` が `created_by_us` になり、承認待ちに積まれない | `test_uploader.py` |
| 8 | 再確認が、**リモートに無いスタック**を指す組を未評価へ戻す | `test_upload_recheck.py` |
| 9 | 再確認が、**メンバー集合が一致しない**組を未評価へ戻す | `test_upload_recheck.py` |
| 10 | 一致している組には**触らない**（`updated_at` も動かさない） | `test_upload_recheck.py` |
| 11 | `stacked` の行が 0 件なら `stacks()` を**呼ばない** | `test_upload_recheck.py` |
| 12 | 戻した組が、第 2 パスで `POST /stacks` により組み直される | `test_stacker.py` |
| 13 | `POST /uploads/{id}/requeue` が 404 になる（経路が消えた） | `test_api_uploads.py` |
| 14 | くわしくに「送り直す」が出ない | `PhotoDetail.test.tsx` |
| 15 | 照合の最中に動いた行は、無効化もされない（CAS の作法が保たれる） | `test_upload_recheck.py` |
| 16 | スタックの照合の最中にキャンセルされたら、1 行も書かずに降りる | `test_upload_recheck.py` |
| 17 | `GET /api/stacks` の応答が壊れていたら protocol error（DB を書かない） | `test_adapter_immich.py` |

### 変異試験

`PYTHONDONTWRITEBYTECODE=1` を付けて、次を 1 つずつ壊し、対応するテストが落ちることを
確認してから戻す。

- `_pair` の `AND invalidated_at IS NULL` を外す → 4 が落ちる
- `record_for` の `AND invalidated_at IS NULL` を外す → 5 と 6 が落ちる
- 無効化の条件を `asset_id is None` から全件へ広げる → 2 が落ちる
- `invalidated_reason` を書かない → 1 が落ちる
- 無効化を `stamp_many` の外（別トランザクション）へ出す → 15 が落ちる
- スタックの照合を「集合の一致」ではなく「`stack_id` の存在」だけにする → 9 が落ちる
- スタックの照合を `stamp_many` の**前**に置く → 同じ組を 2 度開く（6 で観測する）
