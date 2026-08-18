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
| アップロードのワーカー多重化（`UPLOAD_CONCURRENCY` を効かせる） | |

**`UPLOAD_CONCURRENCY` をここで効かせる。** Phase 3 は「宛先ごとに 1 本のジョブで 1 件ずつ直列」に倒し、設定を読んでいない（`phase3-plan.md` の冒頭）。多重化は**ジョブを増やす方向**で行う —— 1 つのジョブ内で 2 本の HTTP を並行させると、状態遷移の commit を別スレッドから行うことになり `BEGIN IMMEDIATE` が交差する（DB 接続はスコープごとに 1 本、という §3 の契約に触れる）。**宛先が違えばジョブが違う**ので、`JobRunner` が同時に走らせるジョブ本数を `UPLOAD_CONCURRENCY` にする。同じ宛先への 2 本目は作らない（preflight とリースの共有が壊れる）。

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
1 auth core ─→ 2 sessions ─→ 3 security（Origin/CSRF）─┐
                                                        ├─→ 10 static ─→ 11 UI 土台 ─→ 12〜16 画面 ─→ 17 受け入れ
4 SSE ──────────────────────────────────────────────────┤
5 thumbnails ───────────────────────────────────────────┤
6 絞り込みとページング ─────────────────────────────────┤
7 承認待ちの差分 ───────────────────────────────────────┤
8 結合の手動編集と supersede ───────────────────────────┤
9 転送先の PATCH ───────────────────────────────────────┘
   （1〜9 はバックエンド。10 以降がフロント）
```

**バックエンドを先に全部通す。** 画面から作ると、足りない API を「画面の都合」で足すことになり、§11 の形から離れる。

---

### Task 1: パスワードのハッシュとセッション ID（`core/auth.py`）

**Files:**
- Create: `app/src/mediaferry/core/auth.py`
- Test: `app/tests/test_auth_core.py`
- Modify: `app/pyproject.toml`（`argon2-cffi` を足す）

**Interfaces:**
- Produces: `hash_password(plain: str) -> str` / `verify_password(stored: str, plain: str) -> bool` / `new_session_id() -> str` / `session_fingerprint(session_id: str) -> str`

**なぜ独立したモジュールか:** 「平文を保存しない」「比較は定数時間」「セッション ID を推測させない」という判断はドメインの外側でも内側でもない小さな純粋層で、API とストアの両方から呼ぶ。ここに閉じておけば、Argon2 のパラメータを変えるときに触る場所が 1 つで済む。

**決めること（この計画で確定させる）:**
- `AUTH_PASSWORD` は **env にしか無い**（`Tier.BOOTSTRAP`、`secret=True`）。DB へ平文を置く経路は作らない（`settings.py` のコメントがそう宣言している）
- 起動時に env の平文を Argon2 でハッシュし、**メモリ上のハッシュだけ**を保持する。DB にも書かない（書くと「env を変えたのに古いパスワードで入れる」が起きる）
- **セッション ID は DB に生値で保存しない。** `session_fingerprint`（SHA-256）を保存し、Cookie の値と突き合わせる。DB のバックアップが漏れても、そこから有効な Cookie を作れない（転送先の `remote_user_id` と同じ理屈。`HANDOFF.md` §3）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_a_hash_does_not_contain_the_password():
    stored = hash_password("correct horse")
    assert "correct horse" not in stored
    assert stored.startswith("$argon2")

def test_verification_accepts_the_password_and_rejects_others():
    stored = hash_password("correct horse")
    assert verify_password(stored, "correct horse")
    assert not verify_password(stored, "correct horses")

def test_a_broken_hash_is_refused_without_raising():
    # 壊れた保存値で 500 にしない（認証は落とさず拒む）。
    assert not verify_password("not-a-hash", "correct horse")

def test_session_ids_are_unpredictable_and_unique():
    ids = {new_session_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(len(value) >= 32 for value in ids)

def test_the_stored_fingerprint_is_not_the_session_id():
    session_id = new_session_id()
    assert session_fingerprint(session_id) != session_id
    assert session_fingerprint(session_id) == session_fingerprint(session_id)
```

- [ ] **Step 2: 最小実装**（`argon2.PasswordHasher`、`secrets.token_urlsafe(32)`、`hashlib.sha256`）
- [ ] **Step 3: 変異試験** —— `verify_password` の例外握りを外す / `new_session_id` の長さを縮める / `session_fingerprint` を恒等関数にする
- [ ] **Step 4: コミット**

---

### Task 2: セッションの保存と失効（`db/sessions.py` + `0008_sessions.sql`）

**Files:**
- Create: `app/src/mediaferry/db/sessions.py`, `app/src/mediaferry/db/migrations/0008_sessions.sql`
- Test: `app/tests/test_sessions.py`
- Modify: `app/tests/test_db_migrate.py`（**版の一覧に 0008 を足す**。Task 0 ではなく、ここで足すのを忘れると回帰テストが落ちる）

**Interfaces:**
- Produces: `SessionStore.create(now) -> tuple[session_id, expires_at]` / `.verify(session_id) -> bool` / `.revoke(session_id)` / `.revoke_all()` / `.purge_expired()`

**スキーマ:**

```sql
CREATE TABLE auth_session (
    fingerprint TEXT PRIMARY KEY,       -- SHA-256。生の session id は保存しない
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
```

**決めること:**
- **有効期限は 14 日、`last_seen_at` の更新で延長する**（画面を開いたまま寝かせても翌朝使える）。延長は 1 時間に 1 回までにして、毎リクエストの書き込みを避ける
- **`AUTH_PASSWORD` が変わったら全セッションを失効させる。** 起動時に「今のパスワードのハッシュ」と DB に残る世代印を比べ、違えば `revoke_all()`。パスワードを変える理由は普通「漏れたから」なので、既存の Cookie が生き残ってはいけない
- 掃除（`purge_expired`）は起動時と 1 日 1 回

- [ ] **Step 1: 失敗するテストを書く**（作成 → 検証 → 失効 → 期限切れは不可 / 生の id が DB に無い / パスワード世代が変わると全部無効）
- [ ] **Step 2: 最小実装**
- [ ] **Step 3: 変異試験** —— 期限の比較を反転 / 失効を no-op に / 生の id を保存する / 世代印の比較を外す
- [ ] **Step 4: コミット**

---

### Task 3: 入口の防御（`api/security.py`）

**Files:**
- Create: `app/src/mediaferry/api/security.py`, `app/src/mediaferry/api/routes_auth.py`
- Test: `app/tests/test_api_auth.py`, `app/tests/test_api_csrf.py`
- Modify: `app/src/mediaferry/api/app.py`

**Interfaces:**
- Produces: `require_session`（依存関数）/ `require_same_origin`（依存関数）/ `issue_csrf(response)` / `CSRF_COOKIE`, `CSRF_HEADER`
- `POST /auth/login`（本文 `{"password": ...}`）/ `POST /auth/logout` / `GET /auth/session`（`{"required": bool, "authenticated": bool}`）

**なぜここが薄い層で要るか:** 状態を変える API は既に 20 本以上ある。個々のルータで「認証を見る」「Origin を見る」を書くと、次に足したルータで**書き忘れる**。**既定で全部に掛かる形**（ルータ単位の `dependencies=`）にして、`/auth/login` と `/health` だけを例外にする。

**決めること（安全性の判断。順序が効く）:**

1. **Origin/Host の検証は、認証の有無に関わらず行う。** 認証が無効なら CSRF は無意味に見えるが、**ブラウザが罠サイトから `127.0.0.1:8080` を叩ける**（drive-by CSRF、DNS rebinding）。無設定の LAN 運用でこそ効く
2. **状態を変えるメソッド（POST/PUT/PATCH/DELETE）だけに掛ける。** GET は Origin を持たない経路がある（`curl`、`fetch` の単純要求）ので、掛けると素の API 利用を壊す
3. **CSRF は二重送信 Cookie。** `XSRF-TOKEN`（HttpOnly でない）を発行し、要求は `X-CSRF-Token` で送り返す。サーバは**同値であることだけ**を見る（セッションに紐付けない ＝ 認証無効でも同じ経路で動く）
4. **Cookie は `HttpOnly` / `SameSite=Lax` / `Path=/`。** `Secure` は**要求が https のときだけ**付ける（LAN の http で付けるとログインできない）
5. 検証の順序は **Origin → CSRF → セッション**。相手に一番情報を与えない順（未認証でも Origin 違反は 403 で落ちる）

```python
async def require_same_origin(request: Request) -> None:
    """状態を変える要求だけに掛ける（GET には掛けない）."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        # ブラウザ以外（curl など）は Origin を送らない。Host との一致で見る。
        referer = request.headers.get("referer")
        if referer is not None and not _same_origin(referer, request):
            raise HTTPException(status_code=403, detail="別のサイトからの要求は受け付けない")
        return
    if not _same_origin(origin, request):
        raise HTTPException(status_code=403, detail="別のサイトからの要求は受け付けない")
```

- [ ] **Step 1: 失敗するテストを書く**
  - ログインしないと 401、ログインすると 200、ログアウトすると 401 に戻る
  - **`AUTH_PASSWORD` 未設定なら全部素通り**（既定 off の方針。`GET /auth/session` は `required: false`）
  - 別オリジンの `Origin` を付けた POST は 403、GET は 200
  - `X-CSRF-Token` が無い / Cookie と違う POST は 403
  - **`/health` と `/auth/login` は例外**（ログインできないと詰む）
  - ログインの失敗にレート制限（同一 IP で 10 回/分を超えたら 429）
  - **応答にもログにもパスワードが出ない**（`test_secret_leaks.py` と同じ作法で確かめる）
- [ ] **Step 2: 最小実装**
- [ ] **Step 3: 変異試験** —— `SAFE_METHODS` に POST を入れる / Origin の比較をホスト名だけにする（スキームとポートを無視）/ CSRF の比較を `!=` に / 例外リストへ他のパスを足す / レート制限を外す
- [ ] **Step 4: コミット**

---

### Task 4: 非 loopback バインドの解禁と警告

**Files:**
- Modify: `app/src/mediaferry/settings.py`, `app/src/mediaferry/api/routes_system.py`, `app/src/mediaferry/__main__.py`
- Test: `app/tests/test_settings.py`, `app/tests/test_api.py`

**決めること:**
- **既定は `127.0.0.1` のまま。** 変えない（意図せず公開されるより、明示的に開ける方が安全）
- 認証が無いまま非 loopback で待ち受けている場合、**起動ログの警告は既にある**（`settings.py`）。Phase 4 では `GET /settings` の応答に `warnings: [{"code": "unauthenticated_exposure", "message": ...}]` を足し、**UI のバナー**として常時出す
- §20 の「ここで初めて非 loopback バインドを既定にできる」は、**「配布可能なリリースにできる」**の意味として読む。既定値そのものは変えない（`HANDOFF.md` §7 の「認証を既定 off のまま」を維持）

- [ ] Step 1〜4（テスト → 実装 → 変異 → コミット）

---

### Task 5: SSE（`GET /events`）

**Files:**
- Create: `app/src/mediaferry/api/routes_events.py`
- Test: `app/tests/test_api_events.py`

**Interfaces:** `GET /events`（`text/event-stream`）。`Last-Event-ID` または `?after_seq=` で再開。イベントは `job_event` を元に `{"job_id", "seq", "level", "message", "data"}`。

**決めること（Phase 1 の判断を引き継ぐ）:**
- **`job_event.id`（自動採番）を SSE の `id:` にする。** `seq` はジョブ内の連番なので、ジョブをまたぐ再開位置にならない
- **接続ごとに DB 接続を 1 本開く**（§3 の契約）。閉じるのは接続が切れたとき
- **ポーリングで実装する**（0.5 秒間隔で新しい `job_event` を読む）。SQLite に通知は無い。**取りこぼさないこと**が要件で、遅延 0.5 秒は §13 の要求（進捗表示）に足りる
- **15 秒ごとにコメント行（`: keep-alive`）を送る。** 途中のリバースプロキシが無通信で切る
- 同時接続数に上限を置く（既定 8）。超えたら 503。**上限が無いと、タブを開きっぱなしにするだけで DB 接続が増える**

- [ ] **Step 1: 失敗するテストを書く** —— 再開位置が効く / 取りこぼさない / keep-alive が出る / 切断で接続が閉じる / 上限を超えると 503 / **認証が有効なら未ログインで 401**
- [ ] Step 2〜4

---

### Task 6: サムネイル（`GET /media/{id}/thumbnail`）

**Files:**
- Create: `app/src/mediaferry/adapters/thumbnails.py`
- Modify: `app/src/mediaferry/api/routes_media.py`
- Test: `app/tests/test_thumbnails.py`

**決めること（パスと資源の判断）:**
- 置き場所は **`DATA_ROOT/cache/thumbnails/<media_id>/<at>.jpg`**。`cache/` は §7 のレイアウトに足す。**DB には入れない**（派生物ではなく再生成できるキャッシュ。DB に絶対パスを置かない規約とも整合する）
- **`at` は整数秒だけを受け付ける**（0〜動画長）。文字列をそのままファイル名にしない
- 生成は ffmpeg を引数配列で起動し、**タイムアウトを付ける**（既定 30 秒）。失敗したら 422 とし、**空ファイルを残さない**（`.part` に書いて `os.replace`）
- 応答は `ETag`（media の `sha1` + `at`）と `Cache-Control: private, max-age=604800`
- **写真はデコードして縮小、動画は 1 フレーム抜く。** どちらも長辺 512px

- [ ] **Step 1: 失敗するテストを書く** —— 実 ffmpeg で 1 枚出る / 2 度目はキャッシュから（ffmpeg を呼ばない）/ `at` に `../` や小数を渡すと 422 / 生成失敗で空ファイルが残らない / ETag が効く
- [ ] Step 2〜4

---

### Task 7: 一覧の絞り込みとページング

**Files:** Modify `routes_media.py`, `routes_uploads.py`, `routes_merges.py` / Test: `app/tests/test_api_listing.py`

**決めること:**
- **並びは `(captured_at DESC, id DESC)` で固定**（同時刻が複数あるので id を tie-break に入れる。入れないとページの境目で行が重複・欠落する）
- ページングは `page` + `page_size`（既定 50、上限 200）。**総件数も返す**（画面が「12 / 87 件」を出す。§13）
- `q` は `original_filename` の部分一致（SQLite の `LIKE`、`%` と `_` をエスケープ）
- `status` は **宛先ごとの状態**（§13 の「宛先 D に未送信」）。`destination_id` と併せて指定する

- [ ] Step 1〜4（**境界のテストを厚く**: ページの境目、同時刻、`%` を含む検索語、上限超え）

---

### Task 8: 承認待ちの差分（Phase 3 の先送り）

**Files:** Modify `routes_uploads.py` / Test: `app/tests/test_api_uploads.py`

**決めること:**
- `GET /uploads?state=awaiting_datetime_approval` に **`proposed`（補正案）と `remote_current`（現在のリモートの値）** を含める
- **`remote_current` はその場で取りに行かない。** 一覧の描画で N 件分の HTTP を出すことになる。`upload_record` に保存済みの観測（`remote_checked_at` の時点の値）を返し、**画面に「いつ時点の値か」を出す**。最新が要るなら宛先の再確認ジョブを回す導線を出す
- 差分が無い（提案と現在が同じ）レコードは **`identical: true`** を付ける。画面はそれを「変更なし」と表示して、承認の必要が無いことを示す

- [ ] Step 1〜4

---

### Task 9: 結合グループの手動編集と supersede（Phase 3 の先送り）

**Files:** Modify `db/merges.py`, `api/routes_merges.py` / Test: `app/tests/test_merge_supersede.py`

**Interfaces:** `POST /merge-groups`（手動作成）/ `PATCH /merge-groups/{id}`（構成変更・skip・不合格の採用）/ `POST /merge-groups/{id}/discard`（破棄）/ 再結合は「新グループを作って旧を supersede」

**なぜ Phase 3 で先送りしたか（`HANDOFF.md` §3）:** 破棄と再結合はどちらも**公開済みの `media_file` を取り残す**。旧グループを `superseded_by_id` で向け直す仕組みが要り、それは手動編集と共通なので画面と一緒に入れる、と決めた。**スキーマは既にある**（`0003` の `superseded_by_id`、部分索引、`active` の trigger）。

**決めること（トランザクションの判断）:**
- **supersede は 1 つの `BEGIN IMMEDIATE` で行う。** 「新グループの作成」「旧グループの `superseded_by_id` 設定」「member の付け替え」が割れると、`input_digest` の部分索引（`WHERE superseded_by_id IS NULL`）が一時的に 2 行を許して UNIQUE 違反になる
- **公開済みの派生物は消さない。** 旧グループの `media_file` は残し、選択肢（§10）から外れるだけにする。**削除はデータを失う経路**（§3 の「孤立ファイルは報告するだけ」と同じ方針）
- **進行中のアップロードを持つグループは編集できない。** `upload_record` が `pending` 以外で参照している間は 409（送信中に根拠を動かすと、§10 の再確認が「根拠が消えた」で無効化する）

- [ ] Step 1〜4（**変異は「3 つの UPDATE を別トランザクションにする」を必ず含める**）

---

### Task 10: 転送先の PATCH

**Files:** Modify `api/routes_destinations.py` / Test: `app/tests/test_api_destinations.py`

**決めること:**
- **改名と有効無効は新しいリビジョンを作らない**（向き先が変わらないので `target_epoch` を進める理由が無い）
- **`base_url` / `api_key` の変更は新しいリビジョンを作る**（§12.3。既存の `POST /destinations` と同じ経路を通す）
- 応答に **API キーはマスク値も含めない**（§11）

- [ ] Step 1〜4

---

### Task 11: `web/` の足場と静的配信

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`, `web/src/main.tsx`
- Create: `app/src/mediaferry/api/static.py`
- Modify: `app/Dockerfile`（多段ビルド）, `pyproject.toml`（ruff の `extend-exclude` に `web`）
- Test: `app/tests/test_api_static.py`

**決めること:**
- **同一オリジンで配る。** FastAPI が `/` 以下でビルド成果物を返し、`/api` はそのまま。別ポートで配ると CORS と Cookie の設定が増え、CSRF の前提（同一オリジン）も崩れる
- **SPA フォールバックは `/api` と `/health` を除いた GET のみ。** 何でも `index.html` を返すと、消したはずの API が 200 を返すようになって気づけない
- **CSP を静的応答に付ける**: `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`（§14。外部からスクリプトも書体も読まない）
- 開発時は Vite の dev server が `/api` を `127.0.0.1:8080` へ proxy する。**proxy 先はローカルに固定**（環境固有の値を焼かない）
- **Dockerfile は多段**（node でビルド → 成果物だけを app イメージへコピー）。実行イメージに node を残さない

- [ ] **Step 1: 失敗するテストを書く** —— `/` が `index.html`、`/assets/*` が配られる、`/api/unknown` は 404（`index.html` を返さない）、CSP ヘッダが付く、`..` を含む要求で `web/dist` の外へ出られない
- [ ] Step 2〜4

---

### Task 12: 画面の共通土台

**Files:** `web/src/api/client.ts`, `web/src/api/types.ts`, `web/src/hooks/useEvents.ts`, `web/src/components/{ConfirmDialog,ErrorBanner,JobProgress,Layout}.tsx`

**契約:**
- `client.ts` は **CSRF トークンを Cookie から読んで `X-CSRF-Token` に載せる**。401 を受けたらログイン画面へ、403（CSRF）は「画面を再読み込みしてください」を出す
- `useEvents.ts` は `EventSource` で `/api/events` を購読し、**再接続時は `Last-Event-ID` を自動で送る**（ブラウザの既定動作）。ジョブ ID ごとに購読者へ配る
- `ErrorBanner` は **API の `detail` をそのまま出さない**。`code` → 日本語の対応表を持ち、未知の code だけ「詳細: <detail>」を添える
- `ConfirmDialog` は **件数・合計サイズ・宛先名**を必須の props にする（§13。型で強制する）

- [ ] 型は **手書きしない**。`GET /openapi.json` から `openapi-typescript` で生成し、`web/src/api/types.ts` をコミットする（生成物を追跡し、API を変えたら差分が出るようにする）

---

### Task 13: ダッシュボードとジョブ画面

- ダッシュボード: 接続中デバイス / 実行中ジョブ / **宛先ごとの同期状況サマリ** / 最近の取り込み / **孤立ファイルと承認待ちの警告** / **認証が無いまま公開している警告バナー**（Task 4）
- ジョブ: 一覧（実行中・履歴）、詳細（進捗バー、`job_event` のログ、キャンセル）。進捗は **ファイル名と件数**（§13）

**受け入れ:** 取り込みジョブを開始して、**画面を再読み込みせずに**進捗が進み、完了で一覧の状態が変わる（SSE が効いている）。キャンセルを押すと、走っているジョブが `cancelled` で終わる（`failed` にならない —— §9.9 の契約が UI から見えること）。

---

### Task 14: デバイス画面

- ボリューム一覧と**判定結果・確度・信頼状態**、対象外ボリュームも**理由付き**で表示（§13）
- 初回は承認（信頼登録）ボタン。スキャン → 結果表示 → 取り込み
- **`POST /volumes/{id}/close`** の導線（アンマウント）

**受け入れ:** 未信頼のカードを挿すと一覧に出て、理由が読める。信頼 → スキャン → 取り込みまで CLI に触れず通る。

---

### Task 15: ライブラリ画面

- 一覧（サムネイル、撮影日時、**宛先ごとの状態バッジ**）、フィルタ（`status` / `profile` / `kind` / 期間 / `q`、**「宛先 D に未送信」**）
- 複数選択 → **宛先を複数選択** → 確認ダイアログ（件数・合計サイズ・宛先名）→ `POST /uploads` → ジョブ開始
- **選択肢の規則は API に従う**（`GET /uploads/selectable`）。ブラウザ側で「送れるかどうか」を再実装しない

**受け入れ:** 2 つの宛先へ同じメディアを送り、**宛先ごとに独立して**状態が進む（§20 の完了条件）。既に送信済みのものは既定で選択肢に出ない。

---

### Task 16: 結合・転送先・承認待ち・設定画面

- **結合**: 候補一覧に**構成ファイル・ギャップ秒数・パートサイズ**を出し、**なぜグループ化されたかが分かる**ようにする（§13）。閾値スライダは `POST /merge-groups/preview`（保存しない）。手動の分割・結合、検証結果の表示、**不合格でも採用**できる導線、失敗の再試行、破棄と再結合（Task 9）
- **転送先**: 一覧と編集、接続検証、**同じアカウントを指す宛先の警告**、`archive`。API キーの欄は「新しい値を入れる」だけ（既存値は出ない）
- **承認待ち**: 現在値と補正案を**並べて**表示（Task 8）。`identical` は「変更なし」として承認を促さない
- **設定**: 値・出所（env / db / default）・**ロック状態**。env 由来は錠前アイコン付きの読み取り専用で、変更しようとすると 409（§12）

---

### Task 17: 受け入れとドキュメント

- [ ] Playwright で **§20 の完了条件**をなぞる 1 本を書く: 認証を有効にしてログイン → デバイスを信頼 → スキャン → 取り込み → 結合 → 2 宛先へ送信 → 承認待ちを承認 → ジョブ履歴で確認
- [ ] `docs/design.md` に Phase 4 で確定した事項を書き戻す（§11 に `/auth/*` と `/events` の実装形、§12 に警告の出し方、§13 に画面の実装との差分）
- [ ] `docs/HANDOFF.md` を更新（現在地、Phase 4 で確定した契約、次のフェーズ）
- [ ] **`--fresh` でレビューを 1 巡**（実装差分に対して。`HANDOFF.md` §5 の手順）

---

## Phase 4 の完了条件（§20）

> エンドユーザが CLI に触れず一連の操作を完了できる。

| 条件 | 確かめ方 |
| --- | --- |
| CLI に触れず一連の操作が通る | Task 17 の Playwright の 1 本 |
| 認証を有効にできる | `test_api_auth.py`（ログイン・ログアウト・未認証で 401） |
| 認証が無くても LAN で使える | `test_api_auth.py`（`AUTH_PASSWORD` 未設定で素通り、警告は出る） |
| 別サイトから操作されない | `test_api_csrf.py`（Origin 違反と CSRF 不一致が 403） |
| 進捗が画面に届く | `test_api_events.py`（再開位置・取りこぼし無し）＋ Task 13 の受け入れ |
| 不可逆な操作の前に確認が出る | `ConfirmDialog` の props（型で強制）＋ Task 15 の受け入れ |
| 秘密が UI に出ない | `test_secret_leaks.py` に「`/auth/login` の応答とログにパスワードが出ない」を足す |

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
4. **UPLOAD_CONCURRENCY はジョブ本数で効かせる**（1 ジョブ内で HTTP を並行させない）。同じ宛先への 2 本目は作らない

## レビュー記録

（実装差分に対して `--fresh` で回す。結果はここに追記する）
