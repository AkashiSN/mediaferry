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

## 決定の表

| # | 決めたこと | なぜ |
| --- | --- | --- |
| 1 | **再確認が消滅と判定した `complete` を、その場で無効化する** | 送り直し専用の状態を持たなくて済む。無効化された記録は「有効な記録」でなくなるので、`GET /media?status=unsent` の定義にそのまま当てはまり、通常経路へ戻る |
| 2 | **`POST /uploads` は無効化された記録を跨いで新しい行を作る** | これが無いと 1 だけでは動かない。いまの `_pair` は `invalidated_at` を見ずに既存行を拾い、`_existing` が「無効化されている」で断る。ホームに「まだ送っていない」と出るのに送れない |
| 3 | **ゴミ箱（`trashed`）は無効化しない** | ゴミ箱に在るものは「無い」の証明ではない。判定は今と同じ（`bulk-upload-check` が `accept` を返したものだけ） |
| 4 | **くわしくから「送り直す」を消し、`POST /uploads/{id}/requeue` も消す** | ボタンが無くなれば API も要らない（`decisions.md`「画面から呼べない API は、機能が無いのと同じ」の裏返し） |
| 5 | **`presence` の `gone` は語彙として残す。移行は入れない** | 変更後は `gone` に留まる行が原理的に出ない。既存 DB はリセットするので移行も要らない。**起きないはずのことが起きたときに、生の enum ではなく本当のことを言うための保険**として残す |
| 6 | **設定 › 送り先の「送り直す」は触らない** | あれは `failed` の再試行で、送信ジョブまで積む閉じた動線。別の話 |
| 7 | **`decisions.md` の「再確認は送り直さない。見えるようにするだけ」を覆す** | 専用経路は動線が閉じておらず、嘘をつく画面になっていた。無効化して通常経路へ戻す形でも、**送信そのものは依然として利用者の明示操作**なので「黙って戻さない」は保てる |

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

## §2 `POST /uploads` が無効化された記録を跨ぐ

### いま

`db/uploads.py` の `_pair` は既存レコードを

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

### こうする

検索条件に `AND invalidated_at IS NULL` を足す。無効化された行は**無いものとして扱い、
新しい行を作る**。

これは意味づけの変更ではない。`design.md` §10 の遷移表が既に

> `invalidated_at` が入っている | **再利用しない。** epoch を進めた場合は新しい行を作る

と書いており、**書いてあるとおりにする修正**である（表の但し書きも
「epoch を進めた場合は」から外し、無効化された行は常に新しい行を作る形に直す）。

### 安全性

- **UNIQUE 制約は無い。** `upload_record_live_pair` は
  `(media_file_id, destination_id) WHERE invalidated_at IS NULL` の**部分インデックス**で、
  UNIQUE ではない。同じ組の行が 2 つ（1 つは無効化済み）並んでも制約に触れない
- **有効な行は常に 1 つ。** 古い方は無効化されている
- **`routes_media.py` は既に複数の記録を想定している**（`_PRESENCE_PRIORITY` で
  「生きている」順に選ぶ）。画面側の変更は要らない
- **`_pair` が跨ぐようになる無効化には他の出所もある**（グループの supersede、
  epoch の繰り上げ、`0006` / `0007`）。いずれも安全:
  - supersede された derived は `sendable_clause` の `superseded_by_id IS NULL` で
    落ちるので、`_choose` が rejected を返す
  - 旧 epoch の行は `_pair` が現行 epoch で引くので最初から当たらない

### 副産物 — `origin` がやり直しになる

新しい記録から送るので、**`origin` の判定も最初からになる**。資産が本当に消えていれば
初回 `checking` は `accept` → `POST /api/assets` が `created` を返す →
**`created_by_us`** になり、**日時補正が自動で通る**（§9.10）。

いまの `requeue` は古い記録を使い回すので、`first_check_result` が前回の観測のまま残り、
送り直した資産が `pre_existing` として承認待ちに積まれうる。この経路が無くなる。

---

## §3 くわしくから「送り直す」を消す

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

## §4 `POST /uploads/{record_id}/requeue` を消す

| 消すもの | どこ |
| --- | --- |
| `requeue_upload` | `app/src/mediaferry/api/routes_uploads.py` |
| `ErrorCode.NOT_REQUEUEABLE` | `app/src/mediaferry/api/errors.py` |
| `not_requeueable` の文言 | `web/src/api/errors.ts` |
| 型 | `web/src/api/types.ts`（再生成） |

`UploadRepository.check_eligibility` は**残す**。claim（`claim_next`）と
`jobs/uploader.py` も使っている。呼び出し元が 1 つ減るだけ。

---

## §5 記録を直す

| 文書 | 直すところ |
| --- | --- |
| `docs/design.md` §9.10 | 「ゴミ箱と消滅の追跡」から requeue を消し、**消滅は無効化して未送信へ戻す**に書き換え |
| `docs/design.md` §10 | 遷移表の「`invalidated_at` が入っている」の但し書き。API 一覧から `POST /uploads/{id}/requeue` を削除 |
| `docs/decisions.md` | 「再確認は送り直さない。見えるようにするだけ」を**覆した判断として書き換える**。理由（動線が閉じておらず、嘘をつく画面になっていた）を残す |
| `docs/user-guide.md` | 「設定 › 送り先の『送り直す』で送り直します」「Immich 側で完全に削除してから…」の案内を、**「サーバを確かめる」を押すと未送信に戻る**形に書き換え |
| `docs/development.md` | 「送り直せない理由が、`missing_at` 以外では消える」は**落とす**（ボタンごと無くなる）。「§9.10 の状態を送り先の一覧が出していない」は**残す**が、`gone` は未送信へ戻るので**ゴミ箱の分だけに縮める** |

**`gone` は語彙として残す**が、`docs/design.md` に「変更後は原理的に出ない。
出たらどこかが壊れている」と明記する。

---

## §6 テスト

**先に落ちるテストを書き、落ちることを確認してから実装する。**

| # | 何を確かめる | どこ |
| --- | --- | --- |
| 1 | 再確認が、消滅と判定した `complete` を `remote_missing` で無効化する | `test_upload_recheck.py` |
| 2 | **ゴミ箱の資産は無効化しない** | `test_upload_recheck.py` |
| 3 | 無効化の後、`GET /media?status=unsent` にそのメディアが戻る | `test_api_media.py` |
| 4 | `POST /uploads` が無効化された行を跨いで**新しい記録を作る**（いまは rejected になるので、これがまず落ちる） | `test_api_uploads.py` |
| 5 | 送り直した記録の `origin` が `created_by_us` になり、承認待ちに積まれない | `test_uploader.py` |
| 6 | `POST /uploads/{id}/requeue` が 404 になる（経路が消えた） | `test_api_uploads.py` |
| 7 | くわしくに「送り直す」が出ない | `PhotoDetail.test.tsx` |
| 8 | 照合の最中に動いた行は、無効化もされない（CAS の作法が保たれる） | `test_upload_recheck.py` |

### 変異試験

`PYTHONDONTWRITEBYTECODE=1` を付けて、次を 1 つずつ壊し、対応するテストが落ちることを
確認してから戻す。

- `_pair` の `AND invalidated_at IS NULL` を外す → 4 が落ちる
- 無効化の条件を `asset_id is None` から全件へ広げる → 2 が落ちる
- `invalidated_reason` を書かない → 1 が落ちる
- 無効化を `stamp_many` の外（別トランザクション）へ出す → 8 が落ちる
