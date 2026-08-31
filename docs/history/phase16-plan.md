# Phase 16 実装計画 — 画面が「いま何が起きているか」を正しく言う

> **エージェント向け:** このリポジトリは TDD で進める。**各タスクは「落ちるテストを
> 書く → 落ちることを確認する → 最小実装 → 通ることを確認する → 変異試験 → コミット」**
> の順で行う。手順は `- [ ]` のチェックボックスで追う。

**目標:** 2026-08-31 の実機検証で見つかった、**画面が実態と違うことを言う** 2 件を直す。
どちらも「サーバは正しく判断しているのに、画面がそれを言わない」型。

- **Task A** —— 作業の履歴が、再確認と日時の承認を「送信」と描く
- **Task B** —— 変更の無い日時の確認が、ホームの「やること」に立ち、決断を迫る

**記録:** 見つけた経緯は
[`hardware-verification.md`](hardware-verification.md) の
「移行スカッシュ後の DB で、持ち越しを 8 件まとめて閉じた（2026-08-31）」。
利用者向けの現状は [`../known-issues.md`](../known-issues.md)、
**Task B の方針は [`../decisions.md`](../decisions.md) に決めてある**。

**着手しないもの:** 同じ回で見つかった 3 件目（**未送信で絞ると、組のタイルの
どちらが未送信なのか読めない**）は、**Phase 10 の「組は 1 タイルに畳む」と正面から
ぶつかるので方針が決まっていない**。この計画には入れない。

## 全体の制約

- `uv sync --all-packages` が必須（素の `sync` ではメンバーが入らない）
- テストは `uv run pytest`、lint は `uv run ruff check .` と `uv run ruff format --check .`
- 画面は `npm --prefix web test` / `npx tsc --noEmit` / `npm --prefix web run lint`
- **E2E は既定の検査に入っていない。** `npm --prefix web run test:e2e` を**単独で**回す
  （並行させると偽陽性が出る）。終わったら孤児サーバを掃除する
  （`pkill -f '\.venv/bin/python3 -m mediaferry'`。**このマシンには別セッションも居る**）
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付ける**（バイト数が変わらない書き換えは
  `.pyc` の無効化条件をすり抜ける）
- **変異試験で `git checkout` を使わない。** scratchpad に控えを取ってから壊す
- **待ちには必ず上限を置く。** `join(timeout=…)` で見る。素直に書くと、変異が失敗では
  なく無限待ちになる（Phase 15 の回で pytest ごと 10 分止めた）
- **`docs/` は ruff の対象外**（`extend-exclude`）
- コメントと docstring は**日本語**。**過去の経緯はコードに書かない**（`docs/` に残す）
- コミットは Conventional Commits + 日本語の本文。**なぜそうしたか**を本文に残す
- **コミット本文に Claude のセッション URL を入れない**
- **同じ作業ツリーを 2 セッションで共有することがある。** 触る前に `git status` を見る

## ファイルの見取り図

| ファイル | 何をしているか | どちらのタスク |
| --- | --- | --- |
| `web/src/components/JobCard.tsx` | `JOB_TYPE_LABELS` が **`type` だけ**で札を決める（`upload: "送信"`） | A |
| `web/src/components/JobProgress.tsx` | `statusLabel` / 進捗の行。`upload: "送信中"` を持つ | A |
| `app/src/mediaferry/db/jobs.py` | `list_jobs` が `job` の行と `last_message` を返す。**`params_json` も返っている** | A |
| `app/src/mediaferry/jobs/uploader.py` | `_upload_one` の「5. fixing_datetime」。`datetime_plan` が `automatic=False` を返すと `awaiting_datetime_approval` へ倒す（`400-427` 付近） | B |
| `app/src/mediaferry/jobs/uploader.py` | `_observed_datetime`（`436-`）が**その場で現在値を読んでいる** | B |
| `app/src/mediaferry/api/routes_system.py` | `dashboard` の `awaiting_total` が `state` だけで数える | B |
| `app/src/mediaferry/api/routes_uploads.py` | `_datetime_diff` が `identical` を**瞬間で**計算する | B（読むだけ） |
| `web/src/screens/work/Approve.tsx` | `identical` の分岐（`164`）。**「却下する」は分岐の外**にある | B |
| `web/src/screens/Home.tsx` | `704` の「写真の日時を直していいか、N 件の確認があります」 | B |
| `web/src/hooks/homeSections.ts` | `COUNTED` の `{ kind: "approve", of: c => c.awaiting_total }` | B |

---

## Task A: 作業の履歴が、押した操作の名前で出る

### いま何が起きているか

`upload` 型のジョブは `params.mode` で **3 つの別の仕事**を兼ねている。

| `mode` | 実際の仕事 | 積む場所 |
| --- | --- | --- |
| （無し） | Immich へ送る | `POST /uploads` のあとの送信 |
| `recheck` | サーバと照合する | `POST /destinations/{id}/recheck` |
| `approve` | 日時の書き戻しを承認する | `POST /uploads/{id}/approve` |

`JobCard.tsx` の `JOB_TYPE_LABELS` は `type` だけを見るので、**どれを押しても
「送信」と出る**。実機で「状態を再確認する」を押した利用者は、履歴に
「送信 / 完了」しか出ないため **「何も起こらなかった」と判断した**。
中身の 1 文（`再確認: 3 件 / ゴミ箱 0 件 / …`）だけが本当のことを言っていた。

§13 の「画面に出す言葉」に対しても偽 —— **押した操作と履歴の名前が対応していない。**

### 決めること

**札は `type` と `mode` の組で決める。**

| `type` / `mode` | 札 |
| --- | --- |
| `upload` / 無し | 送信 |
| `upload` / `recheck` | **再確認** |
| `upload` / `approve` | **日時の承認** |

**サーバは変えない。** `list_jobs` は既に `params_json` を返しているので、画面が
読める。**API に新しい欄を足さない**（`type` と `params` で決まるものを、サーバが
表示用に写すと出所が 2 つになる）。

- [ ] **Step 1: 落ちるテストを書く**

`web/src/components/JobCard.test.tsx`（無ければ作る）に、**3 つの札**を固定する。

```
- upload / mode 無し      → 「送信」
- upload / mode=recheck   → 「再確認」
- upload / mode=approve   → 「日時の承認」
- 未知の mode             → 「送信」に落ちる（**知らない値で札を空にしない**）
```

**`params` が読めない形（`params_json` が壊れている・欄が無い）でも落ちないこと**も
固定する。履歴は過去の行を出す画面なので、**古い形の行が混ざる**。

- [ ] **Step 2: 落ちることを確認する**

```bash
npm --prefix web test -- JobCard
```

期待: `再確認` / `日時の承認` を探す 2 本が落ちる（**要素が無い**ため）。
「送信」の 2 本は最初から通る —— それでよい（退行の錠）。

- [ ] **Step 3: 最小実装**

`JobCard.tsx` の札の決め方を `type` + `mode` にする。**`JOB_TYPE_LABELS` は残す**
（`mode` を持たない種別がそのまま使う）。`JobProgress.tsx` の「送信中」も同じ規則で
分ける必要があるか確かめる —— **走っている最中の表示にも出る**。

- [ ] **Step 4: 通ることを確認する**

```bash
npm --prefix web test && npx --prefix web tsc --noEmit && npm --prefix web run lint
```

- [ ] **Step 5: 変異試験**

| 変異 | 期待 |
| --- | --- |
| `mode` を見ずに `type` だけで決める形へ戻す | 検出 |
| `recheck` と `approve` の札を入れ替える | 検出 |
| 未知の `mode` を空文字にする | 検出 |
| `params` が読めないときに例外を投げる | 検出 |

- [ ] **Step 6: コミット**

```
fix(web): 作業の履歴を、押した操作の名前で出す
```

**本文に書くこと:** `upload` 型が `mode` で 3 つの仕事を兼ねていること、札が `type`
だけで決まっていたこと、**実機で「何も起こらなかった」と読まれたこと**、サーバに
欄を足さずに画面で決める理由。

---

## Task B: 変更が無いなら、確認を求めない

### いま何が起きているか

`datetime_plan` は `origin != created_by_us` なら**値を見ずに**「承認を待つ」を返す。
その直後、`uploader.py` は `_observed_datetime` で**リモートの現在値を読んでいる**
（承認の画面に出す差分のため）。**つまり「同じ瞬間かどうか」はその場で分かる。**

ところが判定は使われず、記録は必ず `awaiting_datetime_approval` へ倒れる。結果、

- `awaiting_total`（`routes_system.py`）は `state` だけで数えるので **4 件と出る**
- ホーム（`Home.tsx:704`）に「写真の日時を直していいか、**4 件**の確認があります」
  「勝手に書き換えないので、**こちらで決めてください**」が立つ
- 枠のバッジの「やること」にも乗る（`homeSections.ts` の `COUNTED`）
- 日時の確認の見出しは「**書き換えていいかどうかを決めてください**」のまま
- 行の操作は「変更なし」という**小さな地の文**だけ（`<span className="small">`。
  「承認する」があった場所なので目が滑る）。**「却下する」は `identical` の分岐の外**に
  あるので必ず出る

**変えるものが無いのに、片付ける手段が「却下」しか無い。** しかも「却下」は
「変更を拒む」意味なので、変えるものが無い場面の語彙として合っていない。

実機（JST の Canon のカード）では **4 件とも `identical` が真**になり、
**決めることが無いのに「やること」が 4 件立った。**

### 決めてあること（`../decisions.md`）

> **観測した現在値が提案と同じ瞬間なら、承認を待たずに `complete` にする。**

承認が守っているのは「**他人が上げた写真を勝手に書き換えない**」ことなので、
**書き換えないなら守るものが無い**。**外部への副作用を起こさない**ので §9.10 の
「明示操作でしか起こさない」には触れない。

**「数えるときに `identical` を除くだけ」の案は採らない** —— 承認待ちの行が画面の
どこからも見えなくなり、この案件が過去に踏んだ「**画面から呼べない API は、機能が
無いのと同じ**」と同じ形の穴になる。

### 気をつけること

- **比較は瞬間で行う。** Immich は日時を UTC へ正規化して返すので、`+09:00` で書いた
  値は `+00:00` の表記で戻る。**文字列で比べると同じ瞬間が常に「違う」になる**
  （それが #36 で直したこと。`routes_uploads.py` の `_datetime_diff` に前例がある）
- **読めなかった現在値を「同じ」にしない。** `_observed_datetime` は相手が答えられ
  なければ `None` を返す。**`None` は「分からない」であって「変更なし」ではない** ——
  そこで `complete` にすると、承認を飛ばして黙って終わらせることになる
- **`_datetime_diff` と判定を二重に持たない。** 同じ「瞬間で比べる」を 2 か所に書くと、
  片方だけ直したときに画面と状態機械が食い違う。**共通の関数へ寄せる**
  （`core/uploads/decisions.py` が HTTP も DB も知らない層なので、そこが自然）

- [ ] **Step 1: 落ちるテストを書く（判定そのもの）**

`app/tests/test_upload_decisions.py` に、**同じ瞬間かどうかを判定する関数**の錠を書く。

```
- "2026-08-31T20:06:12+09:00" と "2026-08-31T11:06:12+00:00" → 同じ
- "2026-08-31T20:06:12+09:00" と "2026-08-31T20:06:13+09:00" → 違う
- 現在値が None                                              → **同じにしない**
- 提案が None                                                → **同じにしない**
- 読めない文字列                                             → **同じにしない**
```

- [ ] **Step 2: 落ちることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q app/tests/test_upload_decisions.py
```

期待: 関数が無いので `ImportError` / `AttributeError`。**「機能が無い」で落ちること**を
確かめる（誤字ではない）。

- [ ] **Step 3: 判定を実装し、`_datetime_diff` をそれに寄せる**

`core/uploads/decisions.py` に置く。`routes_uploads.py` の `identical` の計算を
**その関数の呼び出しに置き換える**（挙動は変えない。テストが緑のままであることを見る）。

- [ ] **Step 4: 落ちるテストを書く（状態機械）**

`app/tests/test_uploader.py` に足す。**fake の Immich が、こちらが送った日時と
同じ瞬間を UTC 表記で返す**筋書きを作る。

```
- origin=pre_existing / 現在値が提案と同じ瞬間  → **complete**（承認を待たない）
- origin=pre_existing / 現在値が違う瞬間        → awaiting_datetime_approval
- origin=pre_existing / 現在値が読めない        → awaiting_datetime_approval
- origin=created_by_us                          → これまでどおり自動で書き戻す
```

**`complete` になった行が `remote_datetime_original` と `remote_checked_at` を持つ**
ことも固定する（送り先の一覧から「いつ時点の観測か」を追えなくなる）。

- [ ] **Step 5: 落ちることを確認する**

期待: 1 本目が `awaiting_datetime_approval` のまま落ちる。

- [ ] **Step 6: 最小実装**

`uploader.py` の `if not plan.automatic:` の枝で、`_observed_datetime` の結果と
`plan.proposed` を比べる。同じ瞬間なら `complete` へ倒す。

**`ctx.emit` の 1 文も分ける** —— いまは「日時の補正に承認が要る」しか出ない。
**同じだったときは、そう言う**（`日時は既に合っている` 等）。履歴の 1 文は
「なぜ件数が変わったのか」を後から追う唯一の手がかり（`list_jobs` の docstring）。

- [ ] **Step 7: 通ることを確認する**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

- [ ] **Step 8: 画面の始末**

**`identical` の行は原理的に出なくなる**が、**画面の分岐は消さない。**

- **古い DB には `awaiting_datetime_approval` のまま残っている行がある**（実機に 4 件
  ある。この計画を当てる時点で題材が生きている）
- 相手が日時を変えれば、また `identical` の行は作れる

そのうえで `Approve.tsx` を直す。

- 「変更なし」を**ボタンと同じ重さで**出す（`<span className="small">` を改める）
- **`identical` のときは「却下する」ではなく「片付ける」**（語彙。却下は変更を拒む意味）
- 見出しの「書き換えていいかどうかを決めてください」を、**変更のある行が 1 つも無い
  ときは出さない**

**vitest で錠を書いてから直す。** 「`identical` の行に『承認する』が無い」
「`identical` だけの一覧で、決断を迫る見出しが出ない」を固定する。

- [ ] **Step 9: 変異試験**

| 変異 | 期待 |
| --- | --- |
| 瞬間ではなく文字列で比べる | 検出（`+09:00` と `+00:00` の筋書きが落ちる） |
| 現在値が `None` のとき「同じ」にする | 検出 |
| `identical` でも `awaiting_datetime_approval` へ倒す | 検出 |
| `complete` にするとき `remote_checked_at` を書かない | 検出 |
| `emit` の 1 文を分けない | 検出（履歴の文言の錠） |

**`_observed_datetime` が `None` を返す経路は、fake を黙らせて作る。**

- [ ] **Step 10: E2E**

```bash
npm --prefix web run test:e2e
```

**単独で回す。** 終わったら孤児サーバを掃除する。

- [ ] **Step 11: コミット**

```
fix(uploads): 日時が既に合っているなら、承認を求めない
```

**本文に書くこと:** 承認が守っているのは「勝手に書き換えない」ことで、書き換えない
なら守るものが無いこと。現在値はその場で読んでいること。**外部への副作用を起こさない
ので §9.10 に触れないこと。** 数えるときに除くだけの案を採らなかった理由。

---

## Task C: 記録を直す

- [ ] `../known-issues.md` から 2 行を落とす（「履歴に『送信』としか出ない」
      「日時が同じなのに『確認があります』と言われる」）
- [ ] `../decisions.md` の方針の行から「**未実装**」を外す
- [ ] `../design.md` §9.10 に、**同じ瞬間なら承認を待たない**ことを書く
      （状態遷移が変わるので、仕様の側に無いと読めない）
- [ ] `hardware-verification.md` の 3 番に決着を記す（**当時の記録は残す**）

## Task D: 全体の確認

- [ ] **全部通す**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest
uv run ruff check . && uv run ruff format --check .
npm --prefix web test && npx --prefix web tsc --noEmit && npm --prefix web run lint
npm --prefix web run test:e2e   # 単独で
```

- [ ] **リンク切れが 0 件であることを確かめる**（`docs/` を触ったので）

- [ ] **実機で踏み直す。** 実機には**この題材が生きている** ——
      `IMG_0019` / `IMG_0020` の 4 件が `awaiting_datetime_approval` のまま残っている。

  - 直した版を入れる**前に**、ホームに「4 件の確認があります」が立っていることを見る
  - 入れ替えたあと、**もう一度リセットの「送信の記録」の段 → 送り直し**で、
    `origin = pre_existing` かつ日時が同じ経路を作る
  - **ホームに「やること」が立たない**こと、**日時の確認に何も並ばない**ことを見る
  - 作業の履歴に「**再確認**」「**日時の承認**」の札が出ることも一緒に見る（Task A）

- [ ] **PR を出す。** 本文は**段落ごとに 1 行**（`~/.claude/CLAUDE.md` の規約）。
      **セッション URL を入れない。**

## 実機の状態（2026-08-31 時点）

| | |
| --- | --- |
| 宛先 | `https://mediaferry.akashisn.info`（nginx 越し）。**セッション cookie は利用者からもらう** |
| スキーマ | 版 1（移行を 1 本へ畳んだ後） |
| 取り込み済み | 20 ファイル（Canon の JPG+CR2 が 10 組） |
| 送り先 | Immich が 2 つ（Soichiro / Risa） |
| 送信済み | `IMG_0019` / `IMG_0020` の 4 件。**`origin = pre_existing`、4 件とも `awaiting_datetime_approval`、`identical = True`** |

**この 4 件は Task B の題材そのもの。** 直す前と後で見比べられる。

**API を直に叩くと題材が消えることがある。** 画面から踏んでもらい、こちらは観測に
徹する（過去に一度、API から叩いて題材を消した）。
