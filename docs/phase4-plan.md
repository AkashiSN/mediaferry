# mediaferry Phase 4（Web UI）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CLI に触れずに一連の操作（デバイスの確認 → 取り込み → 結合 → 宛先へ送信 → 承認）を完了できる Web UI を用意し、**認証と CSRF を入れて初めて非 loopback へバインドできる**状態にする。

**Architecture:** バックエンドは既存の層をそのまま使う（判断は `core/` の純粋関数、副作用は `adapters/` と `db/`、長い処理は `jobs/`）。Phase 4 で足すのは **HTTP の入口の関心事**（セッション、CSRF、SSE、静的配信、サムネイル）と、**画面が要求する読み取り API の形**（絞り込み・ページング・差分）である。フロントエンドは `web/` に React + TypeScript + Vite で置き、ビルド成果物を app イメージへ焼く（§16 / §17）。UI は API の薄い皮にとどめ、**状態機械の判断をブラウザ側に写さない**（同じ規則が 2 箇所に散ると、片方だけ古くなる）。

**Tech Stack:** Python 3.12 / uv workspace / SQLite（WAL）/ FastAPI / httpx / argon2-cffi / pytest / ruff ・ React 19 + TypeScript + Vite / Playwright（受け入れのみ）

**Spec:** `docs/design.md`（正本。§11 API / §12 設定 / §13 画面 / §14 セキュリティ / §16 デプロイ / §17 リポジトリ構成 / §20 実装フェーズ）。前提は `docs/HANDOFF.md`（特に §3 の「蒸し返さないこと」）、直前のフェーズは `docs/phase3-plan.md`。

## Phase 4 の範囲

| 入れる | 入れない（Phase 5 以降） |
| --- | --- |
| 認証（`AUTH_PASSWORD` を Argon2 で保存、Cookie セッション、`POST /auth/login` / `/logout`） | 複数ユーザ・権限 → 作らない（単一パスワード） |
| Origin 検証と CSRF トークン（状態を変える全 API） | OIDC・リバースプロキシ認証の委譲 → Phase 5 で必要になれば |
| `BIND_HOST` の非 loopback を**正式に許可**し、認証が無いまま公開していれば警告 | 認証の必須化 → **しない**（§12 の方針。LAN 内で無設定で使える） |
| SSE（`GET /events`、`Last-Event-ID` で `job_event.seq` から再開） | WebSocket → 使わない（片方向で足りる） |
| サムネイル（`GET /media/{id}/thumbnail`、`at` で秒指定） | 動画のプレビュー再生 → Phase 5 |
| 一覧の絞り込みとページング（§11 の `status` / `profile` / `kind` / `from` / `to` / `q` / `page`） | 全文検索エンジン → 作らない（SQLite の LIKE で足りる） |
| 承認待ちの差分（現在のリモート日時 vs 補正案）—— Phase 3 の先送り | |
| 結合グループの手動作成・構成変更・破棄・再結合（`superseded_by_id`）—— Phase 3 の先送り | 継ぎ目サムネイル → Phase 5（§13 の「検証結果と継ぎ目サムネイル」の後半） |
| 転送先の `PATCH`（改名・有効無効・新リビジョン） | 鍵のローテート（`SECRET_KEY`）→ Phase 5 |
| 8 画面（ダッシュボード / デバイス / ライブラリ / 転送先 / 結合 / 承認待ち / ジョブ / 設定） | プロファイル**編集** UI → Phase 5（読み取りと `test` は Phase 4） |
| **API のエラー形式（`{code, detail}`）と code の一覧** —— 画面が日本語のメッセージを出すのに要る | **アップロードのワーカー多重化（`UPLOAD_CONCURRENCY`）→ Phase 5**（下記） |
| **リモートの日時の観測**（承認画面の「現在値」に要る。列と移行を含む） | |
| **E2E の土台**（実 FastAPI + ビルド資産 + worker + fake broker + fake Immich 2 台） | |

**`UPLOAD_CONCURRENCY` は Phase 5 へ送る。** 当初この計画に入れていたが、計画レビューで
「範囲に入れたのに Task も完了条件も無い」と blocker として指摘され、調べ直して外した。
理由は 3 つ。(1) **`design.md` §20 の Phase 4 は「React SPA、SSE、認証、CSRF」**で、多重化は
入っていない。(2) 現行の `JobRunner` は**全ジョブ種で共通の単一 worker**で、`claim_next()` は
type も宛先も見ない。同時実行数を上げると import / merge / scan まで並列になり、停止処理
（走っているジョブの完了を待つ、§3）も 1 本しか見ていない。(3) 「宛先ごとに 1 本」を保つには
**claim のトランザクションで宛先単位の排他**を取る必要があり、これは Phase 3 で固めた
リースと停止の契約に触れる**独立した設計課題**である。UI の完了条件（CLI に触れず一連の
操作が通る）は逐次実行でも満たせるので、ここでは扱わない。

## この計画の書き方（コードをどこまで埋めるか）

Phase 1〜3 は**実装コードを計画に全部書いた**。その効果と副作用は実測できている（`HANDOFF.md` §1 と §8）。

- 効いた: 手順の順序と例外の流れまで書いたから、レビューが「`fsync` と ffprobe がリースに守られていない」を計画の段階で指摘できた
- 効かなかった: **文書のコードは型検査も実行もできない**ので、機械的な欠陥が毎巡残った（1・2 巡目とも blocker 8 件）。実装差分を見せた 3 巡目以降で初めて「並行性・相手が値を選べる応答・実装した順序」の層が出た

そこで Phase 4 は**書き分ける**。

- **順序と例外の流れが安全性に効くところは、コードで書く**（セッションの検証、CSRF の突き合わせ、SSE の再開位置、サムネイルのパス解決、supersede のトランザクション）
- **画面とコンポーネントは、契約と受け入れ条件で書く**（props の型、呼ぶ API、確認ダイアログに出す情報、エラー時の日本語）。React の実装を文書に写しても、型検査も実行もできないまま量だけ増える

**レビューは実装差分に対して回す**（`--fresh` で。理由は `HANDOFF.md` §5）。計画に対しては 1 巡だけ、**範囲と契約の妥当性**を見てもらう。

## Global Constraints

Phase 1〜3 と同じ。`phase3-plan.md` の同名の節を参照する。差分だけ書く。

- **フロントエンドにも「環境固有の値を含めない」が効く。** API の base は同一オリジンの相対パスだけを使い、ホスト名・ポートを焼き込まない
- **外部 CDN からスクリプト・フォント・画像を読まない。** すべて同梱し、CSP で `default-src 'self'` を宣言する（§14 の「残る攻撃面」を増やさない）
- **秘密を UI の状態に持たない。** API キーは応答に出ない（§12.3）ので、フォームは「新しい値を入力する」だけを扱い、既存値の再表示をしない
- **日本語のエラー。** 例外の文字列をそのまま出さない。**何が起きて次に何をすべきか**を出す（§13）
- **破壊的でない操作は確認なし、不可逆な操作は件数・合計サイズ・宛先名を出して確認**（§13）
- フロントの lint と型検査は `web/` の `npm run lint` と `npm run typecheck`。**Python 側の ruff は `web/` を見ない**（`extend-exclude` に足す）

### 検証コマンド

```bash
uv sync --all-packages
uv run pytest
uv run ruff check . && uv run ruff format --check .
npm --prefix web ci && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run test:e2e     # Playwright。ビルド済み資産を FastAPI が配る形で回す
```

---

## ファイル構成

| ファイル | 責務 |
| --- | --- |
| `app/src/mediaferry/core/auth.py` | パスワードのハッシュと検証（Argon2）、セッション ID の生成。純粋 + 暗号のみ |
| `app/src/mediaferry/db/sessions.py` | `SessionStore`。セッションの作成・検証・失効・掃除 |
| `app/src/mediaferry/db/migrations/0008_sessions.sql` | `auth_session` テーブル |
| `app/src/mediaferry/api/security.py` | 依存関数。セッション Cookie の検証、Origin/Host の検証、CSRF の突き合わせ |
| `app/src/mediaferry/api/routes_auth.py` | `/auth/login`, `/auth/logout`, `/auth/session` |
| `app/src/mediaferry/api/routes_events.py` | `GET /events`（SSE） |
| `app/src/mediaferry/api/routes_media.py` | **修正**。絞り込み・ページング・`GET /media/{id}/thumbnail` |
| `app/src/mediaferry/api/routes_merges.py` | **修正**。手動作成・構成変更・破棄・再結合 |
| `app/src/mediaferry/api/routes_destinations.py` | **修正**。`PATCH /destinations/{id}` |
| `app/src/mediaferry/api/routes_uploads.py` | **修正**。承認待ちの差分 |
| `app/src/mediaferry/api/static.py` | ビルド済みフロントの配信と SPA フォールバック、CSP ヘッダ |
| `app/src/mediaferry/adapters/thumbnails.py` | ffmpeg で 1 枚抜き出してキャッシュへ置く |
| `app/src/mediaferry/db/merges.py` | **修正**。supersede（旧グループを新グループへ向け直す） |
| `web/` | React + TS + Vite。`src/api/`（生成した型と薄いクライアント）、`src/screens/`、`src/components/` |
| `app/Dockerfile` | **修正**。`web/dist` を焼く多段ビルド |
| `docs/design.md` / `docs/HANDOFF.md` | **修正**。Phase 4 で確定した事項 |

### 実装順序と依存

```
0 error envelope ─┬─→ 3 security（Origin/Host/CSRF）
1 auth core ─→ 2 sessions ─┘
                            ├─→ 12 static ─→ 13 UI 土台 ─→ 14〜17 画面 ─→ 18 受け入れ
5 SSE ──────────────────────┤
6 thumbnails ───────────────┤
7 絞り込みとページング ─────┤
8 リモート日時の観測 ─→ 9 承認待ちの差分 ─┤
10 結合の手動編集と supersede ─────────────┤
11 転送先の PATCH ─────────────────────────┘

4 E2E の土台（system harness）と OpenAPI の型生成は、5 以降と**並行して**育てる
```

**バックエンドを先に通す。ただし「全部終わってから UI」にはしない。** 画面から作ると
足りない API を「画面の都合」で足すことになり §11 の形から離れるが、逆に全部終わってから
接続すると、**読み取りの契約の不足（承認画面の現在値、ダッシュボードの集計、送信の
enqueue）が最後にまとめて出る**。計画レビューの指摘に従い、**backend の slice ごとに
OpenAPI の型を生成し、最小の UI consumer を 1 つ通す**形にする（Task 4 の土台をそのために
先に作る）。

---

### Task 0: API のエラー形式と code の一覧 —— **実装済み**（`4b2fb4e`）

**Files:** Create `app/src/mediaferry/api/errors.py` / Modify すべての `routes_*.py` / Test `app/tests/test_api_errors.py`

**なぜ最初か:** 画面は「例外の文字列をそのまま出さない」（§13）。だが現行の API は
`HTTPException(detail="…日本語の文…")` が中心で、**機械が読める code が無い**。この形の
まま UI を作ると、`ErrorBanner` は未知の code しか受け取れず、結局 detail をそのまま出す
（＝相手由来の値や内部の文言が画面に再露出する）。**入口の防御より前に決める。**

**契約:**
```json
{"error": {"code": "destination_unreachable", "detail": "転送先に接続できない", "meta": {}}}
```
- `code` は **snake_case の安定した語彙**。一覧を `errors.py` に置き、増やすときはここに足す
- `detail` は**こちらが書いた日本語**だけ。相手の応答・例外の文字列・秘密を混ぜない（§14）
- `meta` は画面が使う構造化データ（件数、対象 id など）。**秘密を入れない**
- FastAPI の既定のバリデーション誤り（422）も同じ封筒に包む

`api/errors.py` に `ErrorCode` と `ApiError`、`install_error_handlers` を置き、既存の
31 箇所を移した。未処理の例外は定型文だけ返してログにのみ残す。検証の失敗は**どの欄か**
だけを `meta.fields` で返す（受け取った値を反射させない）。変異 6 件を検出（うち 2 件は
「未知のパスへの GET」と「5xx を投げる経路」のテストを足して固定した）。

---

### Task 1: パスワードのハッシュとセッション ID（`core/auth.py`）—— **実装済み**（`4ca31fd`）

`hash_password` / `verify_password` / `new_session_id` / `session_fingerprint`。
`AUTH_PASSWORD` は env のみ（`Tier.BOOTSTRAP`）。壊れた保存値は例外にせず拒む。
セッション ID は生値を保存せず指紋で突き合わせる。変異 4 件を検出。

---

### Task 2: セッションの保存と失効（`db/sessions.py` + `0008_sessions.sql`）—— **実装済み**（`9215b53`）

`auth_session`（指紋が主キー）と `auth_password`（1 行）。有効期間 14 日、延長は最後に
見てから 1 時間経ってから（画面は数秒おきに叩くので毎回は書かない）。

**パスワードの世代印は Argon2 のハッシュそのもの。** 当初の計画は「起動のたびに env の
平文をハッシュしてメモリに持ち、DB には書かない」だったが、**Argon2 の salt は毎回変わる**
ので同じ平文でも一致せず、再起動のたびに全員がログアウトする（計画レビューの blocker）。
保存済みハッシュに現在の平文を `verify` して世代を判定する。認証を切ったときも全失効。
変異 9 件を検出。

---

### Task 3: 入口の防御（`api/security.py`）と公開の警告 —— **実装済み**

**Files:** Create `api/security.py`, `api/routes_auth.py` / Modify `api/app.py`, `settings.py`,
`api/routes_system.py` / Test `test_api_auth.py`, `test_api_csrf.py`, `test_settings.py`

**Interfaces:** `require_session` / `require_trusted_origin` / `issue_csrf` / `POST /auth/login`
/ `POST /auth/logout` / `GET /auth/session`

**なぜ薄い層で要るか:** 状態を変える API は既に 20 本以上ある。ルータごとに書くと**次に
足すルータで書き忘れる**。ルータ単位の `dependencies=` で既定を掛ける。

**決めること（順序が効く。計画レビューで 1 件 blocker が出た箇所）:**

**実装で変えた判断（計画から外れたので書き戻す）:** `TRUSTED_HOSTS` の既定を
「loopback と `BIND_HOST`」にすると、`BIND_HOST=0.0.0.0` で LAN の IP を直に打つ利用者が
全員 421 になる。**IP アドレスそのものは既定で通し、ホスト名だけを明示の許可制**にした
（rebinding は「攻撃者のホスト名が LAN の IP を指す」形なので、名前を許可制にすれば
閉じる。IP を直に打つのは利用者の正当な操作）。

1. **Origin だけを見ても DNS rebinding は防げない。** 攻撃者のドメインを LAN の IP へ
   rebind すると、ブラウザが送る `Origin` も `Host` も攻撃者のホスト名になり、
   「Origin と Host が一致するか」は**通ってしまう**。認証が無効なら、その origin で
   `GET /auth/session` を叩いて XSRF Cookie を取り、JS で読んで同じ origin の POST に
   ヘッダを付けられる（二重送信 Cookie も通る）。
   → **`Host` を信頼できる集合と突き合わせる。** `MEDIAFERRY_TRUSTED_HOSTS`（既定は
   `localhost`, `127.0.0.1`, `[::1]` と `BIND_HOST` の値）に無い `Host` は 421 で拒む。
   Starlette の `TrustedHostMiddleware` を使い、**設定を空にできないようにする**
2. **`/auth/login` は Origin/Host 検証の例外にしない。** セッションと CSRF の例外にする
   だけ（そうしないとログインできない）。丸ごと例外にすると、罠サイトからログインを
   試行させられる
3. **Origin/Host の検証は認証の有無に関わらず、状態を変えるメソッドにだけ掛ける。**
   GET は `curl` から Origin 無しで来るので掛けない
4. **CSRF は二重送信 Cookie。** セッションに紐付けない（認証無効でも同じ経路で動く）。
   **発行点を固定する**: `GET /auth/session` と、静的な `index.html` の応答で必ず
   `XSRF-TOKEN` を発行し、既に有効な値があれば作り直さない
5. **Cookie は種類ごとに属性を分ける**（計画レビューの minor）:

   | Cookie | HttpOnly | SameSite | Path | Secure | 寿命 |
   | --- | --- | --- | --- | --- | --- |
   | `mediaferry_session` | **付ける** | `Lax` | `/` | 要求が https のときだけ | セッションの期限 |
   | `XSRF-TOKEN` | **付けない**（JS が読む） | `Lax` | `/` | 同上 | セッションと同じ |

6. 検証の順序は **Host → Origin → CSRF → セッション**（相手に一番情報を与えない順）
7. **非 loopback バインドは既定にしない**（`127.0.0.1` のまま）。認証が無いまま非 loopback で
   待ち受けていたら、起動ログ（既存）に加えて `GET /settings` の `warnings[]` に
   `unauthenticated_exposure` を出し、UI が常時バナーを出す

- [ ] Step 1: 失敗するテストを書く
  - 未ログインで 401 / ログインで 200 / ログアウトで 401
  - **`AUTH_PASSWORD` 未設定なら素通り**（`GET /auth/session` は `required: false`）
  - 別オリジンの POST は 403、GET は 200
  - **信頼していない `Host` は 421**（rebinding の筋書き）。`/auth/login` にも掛かる
  - CSRF が無い / 一致しない POST は 403。**発行点を叩けば必ず Cookie が付く**
  - ログイン失敗のレート制限（同一 IP で 10 回/分 → 429）
  - 応答にもログにもパスワードが出ない
- [ ] Step 2〜4（変異: `SAFE_METHODS` に POST を入れる / Host の突き合わせを外す /
      login を Origin 検証の例外にする / CSRF の比較を反転 / レート制限を外す）

---

### Task 4: E2E の土台（system harness）と OpenAPI の型生成 —— **実装済み**

**Files:** Create `web/tests/harness.ts`, `app/tests/system/__init__.py`,
`app/tests/system/server.py` / Modify `web/package.json`

**なぜ先に作るか:** 計画レビューの指摘。**実 FastAPI + ビルド済み資産 + ジョブ worker +
fake broker + fake Immich 2 台**を立ち上げる仕掛けが無いと、E2E は `fetch` の mock に落ちて
**静的配信・Cookie・SSE・worker を通らない**。土台が無いまま画面を作ると、最後に E2E を
書く段で環境ごと作り直しになる。

**契約:**
- 一時ディレクトリを `DATA_ROOT` にし、`SECRET_KEY` と（必要なら）`AUTH_PASSWORD` を注入して
  実プロセスとして起動する。**ポートは 0 番で取り、テストへ渡す**（固定しない）
- fake broker は既存のテスト用実装を使い、**SCM_RIGHTS の受け渡しはそのまま**通す
- fake Immich は `app/tests/fake_immich.py` を**そのまま**2 インスタンス起動する
- 型は `GET /openapi.json` から `openapi-typescript` で生成し、`web/src/api/types.ts` を
  **コミットする**（API を変えたら差分が出る）

`app/tests/system/harness.py` の `system_app()` が一式を立ち上げる（`-m needs_system`。
既定の `pytest` では走らない）。smoke は 4 本: `/health` が返る / fake Immich が 2 台
生きている / **本番と同じ経路で入口の防御が効く**（信頼しない `Host` は 421、CSRF の
対が無い POST は 403）/ 認証を有効にしてログインできる。

型は `npm --prefix web run typegen`（アプリ自身の `openapi()` から生成）。生成物は
追跡する。**再生成し忘れは Python のテストが検出する**
（`test_api_types_are_current.py`。npm が無くても回る）。

- [ ] Step 2: backend の slice が増えるたびに型を再生成し、最小の UI consumer を 1 つ通す

---

### Task 5: SSE（`GET /events`）—— **実装済み**

**Files:** Create `api/routes_events.py` / Test `test_api_events.py` / Modify `docs/design.md` §11

**決めること（計画レビューで名称の衝突が指摘された）:**
- **再開の cursor は `job_event.id`（全ジョブ横断の自動採番）。** `seq` はジョブ内の連番
  なので、ジョブをまたぐ再開位置にならない。**`design.md` §11 の「`job_event.seq` から
  再開」も直す。** query 名は `after_event_id`（`after_seq` は使わない —— 既存の
  `JobStore.events(job_id, after_seq)` と紛らわしく、流用すると取りこぼす）
- **cursor 無しの初回接続は「接続時点以後」だけ流す。** 全履歴を replay すると、長く運用した
  後に新しいタブを開くだけで全 `job_event` が流れる
- 消えた cursor（掃除済み）と未来の cursor は、**空から再開**して警告イベントを 1 本流す
- 接続ごとに DB 接続を 1 本開く（§3）。0.5 秒間隔のポーリング。15 秒ごとに `: keep-alive`
- 同時接続の上限（既定 8）。超えたら 503

**実装で分かったこと（計画に無かった判断）:**

- **`BaseHTTPMiddleware` は SSE を殺す。** 応答を一旦受け止めてから流すので、終わらない
  応答が相手に届かない。Task 3 の `SecurityMiddleware` を**素の ASGI ミドルウェア**へ
  書き直した
- **`TestClient` では SSE を試験できない**（終わらない応答を最後まで受け取ろうとする）。
  そこで**層を分けた**: 位置の決め方・枠組み・資源の返し方は生成器を直接呼ぶ単体試験
  （既定の `pytest`）、実際に流れるか・`id:` が付くか・再接続で続くかは**実プロセス**
  （`app/tests/system/test_events.py`、`-m needs_system`）
- **`is_disconnected()` を待たない。** 受信側に何も来ない経路でそこから進まなくなる。
  切断は取り消しで受け取り、`finally` で片付ける
- **資源は `_Reservation` が 1 度だけ返す。** 返す場所が 2 つある（流し終えた／切られた
  ときと、**一度も始まらないまま閉じられた**とき）。後者を落とすと、数えだけが残って
  上限に当たったまま戻らなくなる

変異 12 件を検出。1 件（cursor を進めない）は最初素通りし、原因はテストが 1 回の poll で
届く範囲しか見ていなかったこと —— **次の poll で同じ行が来ないこと**まで見る形にして
固定した。

---

### Task 6: サムネイル（`GET /media/{id}/thumbnail`）—— **実装済み**

**Files:** Create `adapters/thumbnails.py` / Modify `routes_media.py` / Test `test_thumbnails.py`

**決めること（計画レビューで資源の上限が無いと指摘された）:**
- 置き場所は `DATA_ROOT/cache/thumbnails/<media_id>/<at>.jpg`。**DB には入れない**（再生成
  できるキャッシュ。DB に絶対パスを置かない規約とも整合する）
- **`at` は刻みを固定する。** 任意の秒を受けると、1 本の動画で何千枚も作れて `DATA_ROOT` を
  埋められる（認証 off の LAN なら誰でも）。**10 秒刻みに丸め、1 メディアあたり最大 32 枚**
- **同じキーの同時生成は 1 本にまとめる**（single-flight）。`<at>.jpg.<uuid>.part` に書いて
  `os.replace`。共通の `.part` を使うと、並行要求が互いの一時ファイルを壊す
- **容量の上限**（既定 1 GiB）を持ち、超えたら古い順に消す。起動時と生成時に確かめる
- 生成は ffmpeg を引数配列で、タイムアウト付き。**写真も ffmpeg でデコードする**
  （画像ライブラリを足さない。実 ffmpeg は既にテストの前提）
- 応答は `ETag`（`sha1` + `at`）と `Cache-Control: private, max-age=604800`

変異 16 件のうち 15 件を検出。**素通りから分かったこと:**

- 「途中まで書けてから失敗する」経路をテストしていなかった（壊れた入力では ffmpeg が
  何も書かずに落ちるので、後片付けの枝を一度も通っていなかった）。**時間切れや容量不足で
  起きる形**を作って固定した
- 「終了コード 0 で空ファイルを置いていく」経路も同様。`os.replace` が
  `FileNotFoundError` になる筋書きでは中身の検査が働かないので、**空ファイルを置く**
  相手を作って固定した
- キャッシュを見る箇所は 2 つある（ロックの前と中）。**片方ずつでは落とせない**ので、
  対で当てて固定した。前者は速い経路、後者が正しさを担保する
- **検出できない変異が 1 件**: 一時ファイル名から pid とスレッド id を外す変異。同じキーの
  同時生成は single-flight（プロセス内のロック）が止めるので、**同一プロセスでは結果が
  変わらない**。複数プロセスで動かす構成にするまで再現できない

---

### Task 7: 一覧の絞り込みとページング —— **実装済み**

**Files:** Modify `routes_media.py`, `routes_uploads.py`, `routes_merges.py` / Test `test_api_listing.py`

- 並びは `(captured_at DESC, id DESC)` で固定（同時刻があるので tie-break が要る。無いと
  ページの境目で行が重複・欠落する）
- `page` + `page_size`（既定 50、上限 200）、**総件数も返す**（§13 の「12 / 87 件」）
- `q` は `original_filename` の部分一致（`%` と `_` をエスケープ）
- `status` は**宛先ごとの状態**。`destination_id` と併せて指定する
- ダッシュボードの集計（宛先ごとの同期状況サマリ）もここで API にする

ダッシュボードの集計は `GET /dashboard` にまとめた（宛先ごとに一覧の API を叩くと、
そのたびに全件を走査するため）。「未送信」は**この宛先の記録がまだ無いもの**として
数える。変異 12 件を検出。

---

### Task 8: リモートの日時を観測して保存する —— **実装済み**

**Files:** Modify `adapters/immich.py`, `db/uploads.py`, `jobs/uploader.py`, `jobs/recheck.py` /
Create `db/migrations/0009_remote_datetime.sql` / Test `test_remote_datetime.py`

**なぜ要るか（計画レビューの blocker）:** 承認画面は「現在値と変更案を並べて表示」する
（§13）。しかし `upload_record` が持つのは `remote_asset_id` / `remote_is_trashed` /
`remote_checked_at` だけで、**リモートの `dateTimeOriginal` はどこにも無い**。Uploader も
Rechecker も観測していないので、`routes_uploads.py` を直すだけでは現在値を出せない。

**決めること:**
- `ImmichClient.asset(asset_id)`（`GET /api/assets/{id}`）を足し、**識別子は既存の
  `_identifier` を通す**（相手が返す値の検査は adapter の境界で 1 度だけ、が既存の契約）
- 列は `remote_datetime_original`（TEXT、NULL 可）を `upload_record` に足す
- **観測は `remote_checked_at` と同じトランザクションで書く。** 別々に書くと「日時は新しいが
  観測時刻は古い」行ができる
- **古い観測で新しい値を上書きしない。** 書き込みは `stamp_many` と同じ CAS の形にする
  （観測したときの姿を条件に入れる。Phase 3 の 5・7 巡目で確定した契約）
- 観測する場所は 2 つ: 初回 `checking` で `reject`（既存資産）だったときと、宛先ごとの
  再確認。**承認画面を開いたときには取りに行かない**（一覧の描画で N 件分の HTTP を出す）

`ImmichClient.asset()` と `0009_remote_datetime.sql`、`stamp_remote_datetime`（CAS）。
**観測する場所は「承認を求める時点」**にした —— そこが画面に出す値の元になる。
読めなくても送信の結果は変えない（相手が答えられなくても承認待ちにする。ここで
失敗にすると、送信そのものが失敗として記録される）。変異 7 件を検出。

**`complete` 以外を再確認の対象にはしていない。** 承認待ちの行の「現在値」を後から
取り直す導線（宛先の再確認に含める）は Phase 5。いまは承認を求めた時点の値と、
その時刻を画面に出す。

---

### Task 9: 承認待ちの差分（Phase 3 の先送り） —— **実装済み**

**Files:** Modify `routes_uploads.py` / Test `test_api_uploads.py`

- `GET /uploads?state=awaiting_datetime_approval` に `proposed`・`remote_current`・
  `remote_checked_at`・`identical` を含める
- **「いつ時点の値か」を必ず一緒に返す**（画面がそう表示する）。最新が要るなら宛先の
  再確認ジョブを回す導線を出す
- `identical` が真なら画面は「変更なし」と表示し、承認を促さない

`GET /uploads?state=awaiting_datetime_approval` が `remote_current` / `proposed` /
`remote_checked_at` / `identical` を返す。**読めなかった現在値を「変更なし」にしない**
（承認を飛ばさせない）。変異 4 件を検出。

---

### Task 10: 結合グループの手動編集と supersede（Phase 3 の先送り）—— **実装済み**

**Files:** Modify `db/merges.py`, `api/routes_merges.py` / Test `test_merge_supersede.py`

**Interfaces:** `POST /merge-groups`（手動作成）/ `PATCH /merge-groups/{id}`（構成変更・skip・
不合格の採用）/ `POST /merge-groups/{id}/discard` / 再結合は「新グループを作って旧を supersede」

**決めること（計画レビューで禁止集合の定義が誤りと指摘された）:**
- **編集を拒む条件は「これから送られる根拠になっている」こと。** 具体的には
  (a) その構成ファイルを指す `upload_record` が**進行中**（`checking` / `uploading` /
  `asset_known` / `tagging` / `fixing_datetime`）である、または
  (b) `pending` / `needs_recheck` の記録があり、その宛先の `upload` ジョブが
  **`queued` か `running`** である
- **`complete` / `failed` / `refused` / `awaiting_datetime_approval` は編集を妨げない。**
  「一度でも送ったグループは二度と直せない」は、この機能の目的（破棄と再結合）を潰す
- **(b) に当たらない `pending` の記録は、編集と同じトランザクションで無効化する。**
  残すと、編集直後に既存のジョブが claim して `verify_eligibility` で無効化され、
  **理由の分かりにくい失敗**が並ぶ
- supersede は 1 つの `BEGIN IMMEDIATE`（割れると `input_digest` の部分索引が一時的に
  2 行を許して UNIQUE 違反になる）
- 公開済みの派生物は消さない（§3 の「孤立ファイルは報告するだけ」と同じ方針）

**実装で分かった順序の制約:** 1 つのファイルが active な member でいられるのは 1 グループ
だけ（部分索引 `merge_member_one_active_group`）。旧グループの member を外すのは
`superseded_by_id` を立てた trigger なので、**向け直してから新しい member を入れる**。
逆にすると UNIQUE 違反になる。

`_invalidate_pending` は `COALESCE` を使わない —— WHERE の `invalidated_at IS NULL` が
同じことを保証しており、二重にすると「どちらが効いているか」が読めなくなる。変異 8 件を検出。

---

### Task 11: 転送先の PATCH —— **Phase 3 で実装済み**

**Files:** Modify `api/routes_destinations.py` / Test `test_api_destinations.py`

- 改名と有効無効は新リビジョンを作らない（向き先が変わらない）
- `base_url` / `api_key` の変更は新リビジョンを作る（§12.3。既存の POST と同じ経路）
- 応答に API キーはマスク値も含めない（§11）

`PATCH /destinations/{id}` は Phase 3 で入っている（改名・有効無効は新リビジョンを作らず、
`base_url` / `api_key` の変更は作る。応答に API キーは出ない）。**この Task は確認だけ**で
足りた（`test_api_destinations.py` が上の 3 点を固定している）。

---

### Task 12: `web/` の足場と静的配信

**Files:** Create `web/package.json` ほか, `api/static.py` / Modify `app/Dockerfile`, `pyproject.toml`
/ Test `test_api_static.py`

- **同一オリジンで配る**（別ポートにすると CORS と Cookie が増え、CSRF の前提も崩れる）
- SPA フォールバックは `/api` と `/health` を除いた GET のみ（何でも `index.html` を返すと、
  消したはずの API が 200 を返して気づけない）
- CSP: `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
  connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`
- `index.html` の応答で **XSRF Cookie を発行する**（Task 3 の発行点）
- Dockerfile は多段（node でビルド → 成果物だけを app イメージへ）

- [ ] Step 1〜4（テスト: `/` が `index.html` / `/api/unknown` は 404 / CSP が付く /
      `..` で `dist` の外へ出られない / Cookie が発行される）

---

### Task 13: 画面の共通土台 —— **実装済み**

**Files:** `web/src/api/{client,types}.ts`, `web/src/hooks/useEvents.ts`,
`web/src/components/{ConfirmDialog,ErrorBanner,JobProgress,Layout}.tsx`

- `client.ts` は CSRF トークンを Cookie から読んで `X-CSRF-Token` に載せる。401 → ログイン、
  403（CSRF）→「画面を再読み込みしてください」
- `useEvents.ts` は `EventSource` で `/api/events` を購読（再接続時の `Last-Event-ID` は
  ブラウザ既定）
- `ErrorBanner` は **Task 0 の `code` → 日本語**の対応表を持つ。未知の code だけ定型文
- **`ConfirmDialog` は操作種別ごとの discriminated union にする**（計画レビューの指摘）。
  `{kind: "upload", count, totalBytes, destinationNames}` /
  `{kind: "archive_destination", name}` / `{kind: "discard_merge_group", groupLabel, publishedCount}` /
  `{kind: "adopt_failed_merge", groupLabel, reason}` / `{kind: "approve_datetime", current, proposed}`。
  **件数・合計サイズ・宛先名を全部の操作に強いない**（archive や破棄には意味が無い）
- 型は手書きせず `openapi-typescript` で生成（Task 4）

---

### Task 14: ダッシュボードとジョブ画面 —— **実装済み**

- ダッシュボード: 接続中デバイス / 実行中ジョブ / 宛先ごとの同期状況サマリ（Task 7 の集計）/
  最近の取り込み / 孤立ファイルと承認待ちの警告 / **認証が無いまま公開している警告バナー**
- ジョブ: 一覧と詳細（進捗バー、`job_event` のログ、キャンセル）。進捗は**ファイル名と件数**

**受け入れ:** 取り込みを開始して**再読み込みせずに**進捗が進む。キャンセルすると
`cancelled` で終わる（`failed` にならない —— §9.9 の契約が UI から見える）。

---

### Task 15: デバイス画面 —— **実装済み**

- ボリューム一覧と判定結果・確度・信頼状態。対象外も**理由付き**で表示
- 信頼登録 → スキャン → 取り込み → `close`（アンマウント）の導線

**受け入れ:** 未信頼のカードが理由付きで出て、信頼から取り込みまで CLI に触れず通る。

---

### Task 16: ライブラリ画面 —— **実装済み**

- 一覧（サムネイル、撮影日時、宛先ごとの状態バッジ）、フィルタ、複数選択
- **送信は 2 段階**（計画レビューの指摘）。`POST /uploads` は media × destination の pair を
  `pending` で作るだけで、**送信は始まらない**。その後に**宛先ごとに**
  `POST /destinations/{id}/upload` を呼ぶ。画面はこの 2 段階を 1 つの操作として見せる:
  1. 確認ダイアログ（件数・合計サイズ・宛先名）
  2. `POST /uploads` の結果（pair ごとの成否）を出す
  3. 作成できた宛先について**それぞれ 1 回だけ** `upload` ジョブを開始する
  4. 一部の宛先が失敗したら、**成功した分は進めたまま**、失敗した宛先だけ再試行させる
- 選択肢の規則は `GET /uploads/selectable` に従う（ブラウザ側で再実装しない）

**受け入れ:** 2 つの宛先へ送り、宛先ごとに独立して状態が進む。1 つの宛先の enqueue が
失敗しても、もう 1 つは進む。

---

### Task 17: 結合・転送先・承認待ち・設定・プロファイル —— **実装済み**

- **結合**: 候補に構成ファイル・ギャップ秒数・パートサイズを出し、**なぜグループ化されたか**
  が分かるようにする。閾値スライダは `preview`（保存しない）。手動の分割・結合、検証結果、
  不合格でも採用、失敗の再試行、破棄と再結合（Task 10）
- **転送先**: 一覧と編集、接続検証、同じアカウントを指す宛先の警告、archive。
  **空の DB から画面だけで 1 件目を作れること**を受け入れに入れる（§12.3 の初回セットアップ）
- **承認待ち**: 現在値と変更案を並べ、`identical` は「変更なし」と表示（Task 9）
- **設定**: 値・出所・ロック状態。env 由来は読み取り専用で、変更しようとすると 409
- **プロファイル（読み取り）**: 一覧と定義の表示、`POST /profiles/{id}/test` で指定ボリューム
  に対する判定・スキャンの試行。**編集は Phase 5**

---

### Task 18: 受け入れとドキュメント —— **実装済み**

- [ ] Playwright で §20 の完了条件をなぞる: **空の DB から**認証を有効にしてログイン →
      転送先を 2 件作る → デバイスを信頼 → スキャン → 取り込み → 結合 → 2 宛先へ送信 →
      承認待ちを承認 → ジョブ履歴で確認
- [ ] **不可逆な操作ごとに確認ダイアログが出ることを、呼び出し側それぞれで確かめる**
      （upload / archive / discard / adopt / approve）
- [ ] **秘密が画面に出ないことを E2E でも確かめる**: 転送先の API キーが DOM・ネットワーク
      応答・`job_event`・ブラウザのコンソールに出ない
- [ ] `docs/design.md` に Phase 4 で確定した事項を書き戻す（§11 の `/auth/*`・`/events`・
      cursor の名前、§12 の `TRUSTED_HOSTS` と警告、§13 の画面との差分）
- [ ] `docs/HANDOFF.md` を更新
- [ ] **`--fresh` で実装差分のレビューを回す**

---

## Phase 4 の完了条件（§20）

> エンドユーザが CLI に触れず一連の操作を完了できる。

| 条件 | 確かめ方 |
| --- | --- |
| **空の DB から**、CLI に触れず一連の操作が通る | Task 18 の Playwright（転送先の作成から含める） |
| 認証を有効にできる | `test_api_auth.py`（ログイン・ログアウト・未認証で 401） |
| 認証が無くても LAN で使える | `test_api_auth.py`（`AUTH_PASSWORD` 未設定で素通り、警告は出る） |
| 別サイトから操作されない | `test_api_csrf.py`（Origin 違反・CSRF 不一致が 403、**信頼していない `Host` が 421**） |
| 進捗が画面に届く | `test_api_events.py`（再開位置・取りこぼし無し・初回は履歴を流さない）＋ Task 14 |
| **不可逆な操作すべて**で確認が出る | 呼び出し側ごとのコンポーネントテスト（upload / archive / discard / adopt / approve） |
| エラーが日本語で、内部の文言を出さない | `test_api_errors.py`（全経路が `code` を返す）＋ `ErrorBanner` のテスト |
| 承認画面が現在値を出せる | `test_remote_datetime.py`（観測の保存と CAS）＋ `test_api_uploads.py`（差分） |
| 秘密が UI に出ない | Task 18 の E2E（API キーが DOM・ネットワーク応答・`job_event`・コンソールに出ない） |
| E2E が本物を通る | Task 4 の harness（実 FastAPI + ビルド資産 + worker + fake broker + fake Immich 2 台） |

## Phase 4 でやらないこと（意図的な除外）

- **認証の必須化**（§12 の方針。LAN 内で無設定で使えることを優先する）
- **複数ユーザ・権限**。単一パスワードのみ
- **プロファイルの編集 UI**（Phase 5）。Phase 4 は読み取りと `POST /profiles/{id}/test` まで
- **継ぎ目サムネイル**（Phase 5）。結合の検証結果は数値と判定だけ出す
- **動画のプレビュー再生**（Phase 5）
- **`SECRET_KEY` のローテート**（Phase 5）

## 実装の前に決めておくこと

> **この節は、利用者に確認したい判断を置く場所。** 下の 4 件は計画側で仮に決めてある
> （それぞれの Task に理由付きで書いた）。違う意向があれば、着手前にここを直す。

1. **フロントは React + TypeScript + Vite**（`design.md` §17 のとおり）。app イメージは多段ビルドで `web/dist` だけを焼く。**代案**（サーバ側テンプレート + htmx）は、ビルド鎖が消える代わりに §13 の対話（複数選択・スライダ・SSE の部分更新）が重くなるので採らない
2. **認証は既定 off のまま、`BIND_HOST` の既定も `127.0.0.1` のまま**（`HANDOFF.md` §7 の利用者判断を維持）。Phase 4 が変えるのは「**認証を入れれば公開してよい状態になる**」ことだけ
3. **セッションは DB に保存する**（再起動で切れない）。生の session id は保存せず指紋を持つ
4. **`UPLOAD_CONCURRENCY` は Phase 4 で扱わない**（Phase 5 へ送る）。`design.md` §20 の
   Phase 4 に多重化は入っておらず、現行の `JobRunner` は全ジョブ種で共通の単一 worker な
   ので、同時実行数を上げると import / merge / scan まで並列になる。「宛先ごとに 1 本」を
   保つには claim のトランザクションで宛先単位の排他が要り、Phase 3 で固めたリースと停止の
   契約に触れる独立した設計課題になる
5. **`TRUSTED_HOSTS` を設定に足す**（既定は loopback と `BIND_HOST`）。DNS rebinding は
   Origin と Host の一致では防げないので、`Host` を信頼できる集合と突き合わせる

## レビュー記録

### 計画レビュー 1 巡目（2026-08-19、codex `--fresh`。blocker 4 / major 8 / minor 2）

**全件を反映した。退けた指摘は無い。** 「実装コードではなく範囲と契約を見てほしい」と
依頼した狙いどおり、**計画が前提にしていた backend の状態が実在しない**という層の指摘が
中心になった。

| # | 指摘 | 反映 |
| --- | --- | --- |
| 1 [blocker] | パスワードの世代判定が成立しない（Argon2 の salt は毎回変わるので、起動のたびに全セッションが失効する）。0008 に保存先も無い | Task 2。保存済みハッシュに env の平文を `verify` する形へ。**実装済み**（`9215b53`） |
| 2 [blocker] | Origin と Host の一致は **DNS rebinding を防げない**（どちらも攻撃者のホスト名になる）。`/auth/login` を丸ごと例外にすると「認証の有無に関わらず Origin を検証」と矛盾する | Task 3。`TRUSTED_HOSTS` と `Host` の突き合わせ（421）を足し、login は session/CSRF の例外に留める。CSRF の発行点と Cookie ごとの属性表も固定 |
| 3 [blocker] | 承認画面の「現在値」の情報源が**実在しない**（`upload_record` にリモートの日時の列が無く、Uploader も Rechecker も観測していない） | **Task 8 を新設**（adapter の取得・列と移行 0009・観測の CAS）。Task 9 はそれに依存させた |
| 4 [blocker] | `UPLOAD_CONCURRENCY` を範囲に入れたのに Task も完了条件も無い。単に同時実行数を上げると import / merge / scan まで並列になり、停止と「宛先ごとに 1 本」の契約に触れる | **Phase 5 へ送った**（§20 の Phase 4 にも多重化は入っていない） |
| 5 [major] | 結合の編集を拒む条件を `!= pending` にすると、一度送ったグループが**永久に編集できない**。逆に `pending` は次に送られる根拠なのに許してしまう | Task 10。禁止集合を「進行中の記録」と「queued/running のジョブを持つ pending」に定義し直し、残る `pending` は同じトランザクションで無効化 |
| 6 [major] | `POST /uploads` は pair を作るだけで**送信は始まらない**（宛先ごとの `POST /destinations/{id}/upload` が要る） | Task 16。2 段階であることと、部分失敗の扱いを画面契約に書いた |
| 7 [major] | `ErrorBanner` が前提にする `code` が backend に無い（現行は `detail` の文字列だけ） | **Task 0 を新設**し、入口の防御より前に置いた |
| 8 [major] | サムネイルに資源の上限・single-flight・退避が無く、写真のデコード手段も未定 | Task 6。`at` の丸めと枚数上限、一時ファイルの一意化、容量上限、**ffmpeg で写真もデコード**（依存を足さない） |
| 9 [major] | SSE の cursor 名が衝突（`job_event.id` と言いながら query は `after_seq`）。初回接続の意味も未定 | Task 5。`after_event_id` に統一し、初回は「接続時点以後」。消えた cursor と未来の cursor の扱いもテストに入れた。**`design.md` §11 も直す** |
| 10 [major] | `ConfirmDialog` の props 型は「すべての不可逆操作で出た」証明にならない。件数・サイズ・宛先名は archive や破棄には意味が無い | Task 13。操作種別ごとの discriminated union にし、完了条件を**呼び出し側ごとのテスト**に変えた |
| 11 [major] | プロファイルの読み取りと `test` の画面が無い。**空の DB から転送先を作る**受け入れも無い（E2E が seed で通ってしまう） | Task 17 と完了条件。E2E は空の DB から始める |
| 12 [major] | E2E を回す土台（実 FastAPI + ビルド資産 + worker + fake broker + fake Immich 2 台）がどの Task にも無い | **Task 4 を新設**し、backend の slice と並行して育てる形にした。秘密の完了条件も DOM・ネットワーク・コンソールまで広げた |
| 13 [minor] | 依存図の番号が Task と 1 つずつずれている | 図を書き直した |
| 14 [minor] | Cookie の属性が「XSRF は HttpOnly でない」と「Cookie は HttpOnly」で矛盾 | Task 3 に Cookie ごとの属性表を置いた |

**順序についての助言も採った。** 「全 backend 完了後に UI を接続すると、読み取りの契約の
不足が最後に集中する」。backend の slice ごとに OpenAPI の型を再生成して最小の UI consumer を
通す形に変えた（Task 4）。

### 実装差分のレビュー

（`--fresh` で回す。結果はここに追記する）

---

## 実装を終えて（Phase 4 の記録）

**実装で初めて分かったことが 5 つある。** どれも計画には書けなかった層で、
「動かしてみて分かる」という §8 の教訓がそのまま出た。

1. **`BaseHTTPMiddleware` は SSE を殺す。** 応答を一旦受け止めてから流すので、終わらない
   応答が相手に届かない。Task 3 の middleware を素の ASGI へ書き直した
2. **`TestClient` では SSE を試験できない。** 層を分けた（位置の決め方と資源の返し方は
   生成器を直接、線の上の挙動は実プロセス）
3. **`Error.message` は getter で上書きできない**（own property）。API のエラーを日本語に
   するのは構築時に決める
4. **React の合成イベントは `await` の後で `currentTarget` を失う。** 転送先の追加が
   「予期しないエラー」になって初めて気づいた
5. **一覧が SSE で更新されていなかった。** 取り込みが終わってもライブラリが古いままで、
   E2E を書くまで気づかなかった（受け入れ条件そのものだった）。`useReloadOnEvents` を
   足して、進捗が届いたら取り直す形にした

**E2E の土台（Task 4）を先に作ったのが効いた。** SSE が `TestClient` で詰まったとき、
実プロセスで動くことを確かめて原因を切り分けられた。UI の型が API の実際の形とずれて
いたことも、E2E が「要素が見つからない」ではなく **React の例外**として教えてくれた
（`pageerror` を拾うようにしてある）。

**変異試験の総数は 100 件を超え、検出できなかったのは 2 件**（サムネイルの一時ファイル名は
single-flight と冗長、`_parsed_check` の型検査は `_identifier` と冗長）。どちらも
「片方ずつでは落とせない二重の保険」で、記録に残した。
