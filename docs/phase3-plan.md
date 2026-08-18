# mediaferry Phase 3（Immich 同期）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 転送先プロファイルを Web API から管理し、選んだメディアを選んだ宛先へ送り、途中で落ちても二重に上げずに再開できるようにする。

**Architecture:** 判断は `core/` の純粋関数（URL の正規化、origin と次状態の決定、日時補正案の組み立て）に置く。副作用は `adapters/immich.py`（HTTP）と `db/`（`DestinationRepository` / `CredentialStore` / `UploadRepository`）に閉じる。`jobs/uploader.py` が §9.10 の状態機械を 1 レコードずつ進め、**外部への副作用の直前と結果の commit 時に claim とリースを再確認する**。Phase 2 で作った `_with_lease_pulse`（長い同期処理の間もリースを延ばす）を共有モジュールへ移し、巨大ファイルの送信中にも使う。

**Tech Stack:** Python 3.12 / uv workspace / SQLite（WAL）/ FastAPI / httpx / cryptography（AES-256-GCM）/ pytest / ruff

**Spec:** `docs/design.md`（正本。§8 転送先とアップロード / §9.10 Immich へのアップロード / §10 対象と宛先 / §11 API / §12.3 転送先プロファイル / §12.4 接続エンドポイント / §14 セキュリティ）。実測で確定した事項は `docs/phase0-findings.md` の ②、作業の前提は `docs/HANDOFF.md`、直前のフェーズは `docs/phase2-plan.md`。

## Phase 3 の範囲

| 入れる | 入れない（Phase 4 以降） |
| --- | --- |
| 転送先プロファイルの CRUD と接続検証（§12.3）。API キーは `core/crypto.py` で暗号化して保存 | 転送先の編集 UI と確認ダイアログ → Phase 4 |
| §9.10 の状態機械（`checking` → `uploading` → `asset_known` → `tagging` → `fixing_datetime` → `complete`） | アップロードの並列実行 → Phase 4（下記） |
| §10 (a) 安全条件と (c) `selection_rule` ごとの条件を claim 時に評価する | 手動でのグループ編集に伴う supersede → Phase 4 |
| `POST /uploads` の pair 単位の意味論と、pair ごとの結果 | 承認待ちの一覧画面・差分表示 → Phase 4（API は Phase 3） |
| 承認 / 却下、再試行、状態の再確認（ゴミ箱の追跡） | SSE（`GET /events`）→ Phase 4 |
| 送信前の preflight（`/api/users/me` の突き合わせ） | 鍵の入れ替え（`SECRET_KEY` のローテート）→ Phase 5 |

**アップロードは逐次実行にする。** §9.10 は「並列度は既定 2」と書いているが、Phase 1 の
`JobRunner` は単一の asyncio ワーカーで、DB 接続はジョブのスコープに 1 本と決めてある
（トランザクションは接続に属する）。ジョブ内で 2 本の HTTP を並行させると、状態遷移の
commit を別スレッドから行うことになり、`BEGIN IMMEDIATE` が交差する。**Phase 3 は
`UPLOAD_CONCURRENCY` を読まずに 1 件ずつ送り、設定は Phase 4 でワーカーを多重化する
ときに効かせる。** この決定は Task 14 で `design.md` §9.10 に書き戻す。

**`upload` ジョブは宛先ごとに 1 本立てる。** preflight は「あるリビジョンの最初の pair を
送る前に 1 回」で共有できる（§10）。宛先をまたぐジョブにすると、1 つの宛先の preflight 失敗が
他の宛先の送信まで巻き込む。

## Global Constraints

すべてのタスクの要件に、以下が暗黙に含まれる。Phase 1・Phase 2 と同じ内容なので、
`docs/phase2-plan.md` の同名の節と読み比べる必要はない。

- **作業ディレクトリはリポジトリのルート。** コマンドはすべてここから実行する。
- Python は `>=3.12`。ruff の `line-length = 100`、`target-version = "py312"`、
  lint は `select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`（`ANN401` のみ ignore）。
  `**/tests/*` は `S101` / `S105`〜`S107` / `ANN` が免除される。
  **`docs/` は ruff の対象外**（`extend-exclude = ["docs"]`）。
- すべてのモジュールは `from __future__ import annotations` で始める。
- **コメントと docstring は日本語。**「いま書かれているコードを現在形で説明する」だけを書く。
- **環境固有の値をリポジトリに含めない。** IP アドレス、ホスト名、API キー、
  タイムゾーンの実値をコードにもテストにも書かない。テストの URL は
  `http://immich.invalid` のような予約名か `http://127.0.0.1:<任意>` を使う。
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**（§12.3 / §14）。
  API キーは `SecretBox` で暗号化して保存し、読み出しの API は作らない。
- **DB に絶対パスを保存しない。** `DATA_ROOT` からの相対パスのみが正規形（§7）。
- **DB 接続はスコープごとに 1 本。** トランザクションは接続に属していてスレッドには属さない。
- **ジョブは固定したリビジョンを読む。** 宛先も同じで、claim 時に
  `destination_revision_id` を記録し、以後その版で送り切る。
- 外部コマンドは引数配列で起動する。シェル文字列を組み立てない（§14）。
- システム時刻は **UTC の ISO-8601 文字列**で DB に入れ、生成は `mediaferry.clock` の
  関数だけを使う。**例外は `media_file.captured_at`**（解決したオフセット付き）。
- ID は `uuid4().hex`（32 文字の TEXT）。`job_event.id` だけ整数の自動採番。
- テストのマーカー: 実 Immich を要するものは `needs_immich`、root を要するものは
  `needs_root`。既定の `pytest` では実行されない。**HTTP のテストは fake サーバ
  （`app/tests/fake_immich.py`）に対して実物の httpx で行う**（既存の broker テストと
  同じ作法。プロトコルの取り違えを見逃さない）。
- 各タスクの最後に必ず `uv run pytest`・`uv run ruff check .`・`uv run ruff format .` を通す。
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付けて実行する。** バイト数が変わらない
  書き換えでは `.pyc` の無効化条件をすり抜けて古いバイトコードが使われる。
- **変異は「成立する形」で当てる。** 例外で全件落ちる書き換えは、狙いの判断を
  検証したことにならない。
- **検出できない変異は、検出できないことをこの計画に書き戻す。** ただし
  「検出できない」と書く前に、テストを 1 つ足して落とせないか試す
  （Phase 2 では計画が検出不能としていた 5 件のうち 4 件を実装時に固定できた）。
- コミットは Conventional Commits + 日本語の本文。**本文に Claude のセッション URL を
  書かない**（`CLAUDE.md` の規約）。
- **Phase 3 は配布可能なリリースにしない。** `BIND_HOST` の既定は `127.0.0.1` のまま。

### 検証コマンド

```bash
uv sync --all-packages     # --all-packages が必須。素の sync ではメンバーが入らない
uv run pytest
uv run pytest -m needs_immich   # 実 Immich がある環境でのみ
uv run ruff check .
uv run ruff format --check .
```

---

## ファイル構成

| ファイル | 責務 |
| --- | --- |
| `app/src/mediaferry/core/destinations/__init__.py` | 空（パッケージ宣言） |
| `app/src/mediaferry/core/destinations/urls.py` | `base_url` / `public_url` の正規化と検証。純粋関数 |
| `app/src/mediaferry/core/uploads/__init__.py` | 空（パッケージ宣言） |
| `app/src/mediaferry/core/uploads/decisions.py` | `origin` の決定、次状態の決定、日時補正案の組み立て。純粋関数 |
| `app/src/mediaferry/core/lease_pulse.py` | **移設**。`_with_lease_pulse` を publisher から出して共有する |
| `app/src/mediaferry/adapters/immich.py` | `ImmichClient`。redirect を追わない HTTP、bulk-upload-check、ストリーミング送信、タグ、日時更新 |
| `app/src/mediaferry/db/credentials.py` | `CredentialStore`。AEAD の暗号文の保存と復号、旧版の破棄 |
| `app/src/mediaferry/db/destinations.py` | `DestinationRepository`。不変リビジョンと `target_epoch` の規則 |
| `app/src/mediaferry/db/uploads.py` | `UploadRepository`。pair の作成、claim（CAS）、状態遷移、無効化 |
| `app/src/mediaferry/db/selection.py` | **修正**。digest 一致の判定を claim 側と共有できる形に切り出す |
| `app/src/mediaferry/jobs/preflight.py` | `PreflightCache`。リビジョンごとに 1 回、向き先を再確認する |
| `app/src/mediaferry/jobs/uploader.py` | `Uploader`。§9.10 の状態機械を 1 レコードずつ進める |
| `app/src/mediaferry/jobs/reconcile.py` | **修正**。中断した upload の claim を解放し `needs_recheck` へ落とす |
| `app/src/mediaferry/api/routes_destinations.py` | `/destinations` 系 |
| `app/src/mediaferry/api/routes_uploads.py` | `/uploads` 系（作成・一覧・再試行・承認・却下・再確認） |
| `app/src/mediaferry/api/jobs_wiring.py` | **修正**。`run_upload` |
| `app/src/mediaferry/api/app.py` | **修正**。ルータとハンドラの登録 |
| `app/tests/fake_immich.py` | テスト用の Immich（ASGI）。実物の httpx で叩く |
| `app/tests/test_destination_urls.py` 〜 `test_upload_e2e.py` | 単体・統合 |
| `docs/design.md` | **修正**。§9.10 に逐次実行の決定、§11 に Phase 3 の API |
| `docs/HANDOFF.md` | **修正**。Phase 3 完了時の現在地 |

**マイグレーションは足さない。** `upload_destination` / `destination_credential` /
`destination_revision` / `upload_record` は Phase 1 の `0004_destinations_and_uploads.sql` に
あり、複合外部キー・不変 trigger・claim の CHECK まで入っている。`job.type` の
`upload` も `0001` の CHECK にある。

**状態の再確認（recheck）は `upload` ジョブの `params.mode` で分ける。** 新しい
`job.type` を足すと `0001` の CHECK を書き換えることになり、SQLite ではテーブルの
作り直しが要る。`mode` は `send`（既定）と `recheck` の 2 値。

### 実装順序と依存

```
1 urls ─────┐
2 credentials ├─→ 3 destinations ─┐
            │                     ├─→ 9 uploader ─┐
4 immich client ──→ 5 preflight ──┘               │
6 selection（共有）─→ 7 create_pairs ─→ 8 claim ──┘
                                                   ├─→ 13 API ─→ 14 統合 + docs
10 recheck ────────────────────────────────────────┤
11 承認 / 却下 ────────────────────────────────────┤
12 無効化と reconcile ─────────────────────────────┘
```

---

### Task 1: 接続エンドポイントの正規化と検証

**Files:**
- Create: `app/src/mediaferry/core/destinations/__init__.py`
- Create: `app/src/mediaferry/core/destinations/urls.py`
- Test: `app/tests/test_destination_urls.py`

**Interfaces:**
- Produces:
  - `normalize_endpoint(raw: str) -> str`
  - `EndpointRejected(ValueError)`
  - `ALLOWED_SCHEMES: frozenset[str]`

**なぜ独立したモジュールか:** `base_url`（通信に使う）と `public_url`（画面のリンクに
描画する）の両方が同じ検証を通る必要がある（§12.4）。片方だけ緩めると、
`javascript:` を保存できる欄が残る。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_destination_urls.py`:

```python
import pytest

from mediaferry.core.destinations.urls import EndpointRejected, normalize_endpoint


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://immich.invalid:2283", "http://immich.invalid:2283"),
        ("http://immich.invalid:2283/", "http://immich.invalid:2283"),
        ("http://immich.invalid:2283/api/", "http://immich.invalid:2283/api"),
        ("HTTP://Immich.Invalid:2283", "http://immich.invalid:2283"),
        ("https://immich.invalid", "https://immich.invalid"),
    ],
)
def test_accepted_endpoints_are_normalised(raw, expected):
    assert normalize_endpoint(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://immich.invalid",
        "//immich.invalid",
        "immich.invalid:2283",
        "",
        "   ",
    ],
)
def test_only_http_and_https_are_accepted(raw):
    with pytest.raises(EndpointRejected):
        normalize_endpoint(raw)


def test_userinfo_is_refused():
    # 資格情報を URL に埋めると、ログと画面の両方に出る経路ができる。
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http://user:pass@immich.invalid:2283")


def test_a_fragment_is_refused():
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http://immich.invalid:2283/#/photos")


def test_a_query_is_refused():
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http://immich.invalid:2283/?token=x")


def test_a_missing_host_is_refused():
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http:///api")


def test_the_default_port_is_not_written_back():
    # 既定ポートを明示すると、同じ宛先が別の文字列で 2 通り保存される。
    assert normalize_endpoint("http://immich.invalid:80") == "http://immich.invalid"
    assert normalize_endpoint("https://immich.invalid:443") == "https://immich.invalid"


def test_a_non_default_port_is_kept():
    assert normalize_endpoint("http://immich.invalid:2283") == "http://immich.invalid:2283"


def test_an_ipv6_host_keeps_its_brackets():
    assert normalize_endpoint("http://[::1]:2283") == "http://[::1]:2283"
    assert normalize_endpoint("http://[::1]") == "http://[::1]"


@pytest.mark.parametrize("raw", ["http://immich.invalid:99999", "http://immich.invalid:abc"])
def test_an_unusable_port_is_refused(raw):
    # urlsplit の ValueError をそのまま外へ出さない（400 に正規化できない）。
    with pytest.raises(EndpointRejected):
        normalize_endpoint(raw)
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_destination_urls.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.core.destinations'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/core/destinations/__init__.py`:

```python
from __future__ import annotations
```

`app/src/mediaferry/core/destinations/urls.py`:

```python
"""転送先の接続エンドポイントの検証（§12.4）.

`base_url` は mediaferry が実際に接続する先で、`public_url` は画面のリンクに
描画するだけの値。**両方に同じ検証を掛ける。** 片方だけ緩めると、
`javascript:` を保存できる欄が残る。

正規化して保存するのは、同じ宛先が違う文字列で 2 通り保存されるのを防ぐため。
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}


class EndpointRejected(ValueError):
    """スキーム・userinfo・fragment・ホストのいずれかが要件を満たさない."""


def normalize_endpoint(raw: str) -> str:
    """受理した URL を正規形で返す. 受理できなければ送出する."""
    text = raw.strip()
    if not text:
        raise EndpointRejected("URL が空")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise EndpointRejected(f"http か https でなければならない: {scheme or '(スキーム無し)'}")
    if parts.username is not None or parts.password is not None:
        # URL に埋めた資格情報は、ログにも画面にも出る経路になる。
        raise EndpointRejected("URL に userinfo を含めない")
    if parts.fragment:
        raise EndpointRejected("URL に fragment を含めない")
    if parts.query:
        raise EndpointRejected("URL に query を含めない")
    if not parts.hostname:
        raise EndpointRejected("ホスト名が無い")
    try:
        port = parts.port
    except ValueError as exc:
        # 範囲外・数値でないポートは urlsplit が読むときに落ちる。
        raise EndpointRejected(f"ポート番号として解釈できない: {parts.netloc}") from exc

    # `hostname` は urlsplit が小文字にして返す（大文字のホスト名はここで揃う）。
    host = parts.hostname
    if ":" in host:
        # IPv6 は括弧で囲み直す。素で組むと `http://::1:2283` になって壊れる。
        host = f"[{host}]"
    if port is not None and port != DEFAULT_PORTS[scheme]:
        host = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, host, path, "", ""))
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_destination_urls.py -q`
Expected: PASS（16 件。parametrize を展開した数）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `scheme not in ALLOWED_SCHEMES` の判定を消す | `test_only_http_and_https_are_accepted` |
| `parts.username` の判定を消す | `test_userinfo_is_refused` |
| `parts.fragment` の判定を消す | `test_a_fragment_is_refused` |
| `parts.query` の判定を消す | `test_a_query_is_refused` |
| `parts.hostname` の判定を消す | `test_a_missing_host_is_refused` |
| `port != DEFAULT_PORTS[scheme]` を `port is not None` にする | `test_the_default_port_is_not_written_back` |
| `port != DEFAULT_PORTS[scheme]` を `False` にする（ポートを捨てる） | `test_a_non_default_port_is_kept` |
| IPv6 の括弧付けを消す | `test_an_ipv6_host_keeps_its_brackets` |
| `parts.port` の `ValueError` を捕まえない | `test_an_unusable_port_is_refused` |
| `path.rstrip("/")` を `path` にする | `test_accepted_endpoints_are_normalised` |
| `parts.hostname.lower()` を `parts.hostname` にする | **落ちない**。`urlsplit().hostname` は既に小文字を返すので `.lower()` は冗長だった。実装から外した（テストは大文字のホスト名を通したままにして、`hostname` の性質が変わったら気づけるようにする） |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/core/destinations app/tests/test_destination_urls.py
git commit -m "feat(mediaferry): normalise the endpoints a destination talks to"
```

---

### Task 2: API キーの保管

**Files:**
- Create: `app/src/mediaferry/db/credentials.py`
- Test: `app/tests/test_credential_store.py`

**Interfaces:**
- Consumes: `mediaferry.core.crypto.SecretBox` / `SecretAad` / `WrongKeyError` / `SecretCorrupt`（既存）
- Produces:
  - `CredentialStore(conn: sqlite3.Connection, box: SecretBox)`
  - `.store(destination_id: str, secret: str) -> str`（新しい credential の id）
  - `.reveal(credential_id: str) -> str`
  - `.purge_unreferenced(destination_id: str) -> int`
  - `CredentialUnusable(RuntimeError)`

**復号できない資格情報を「壊れたもの」として上書きしない**（§12.3）。マスター鍵の
取り違えは `WrongKeyError` で区別できるので、そのまま画面へ出して再登録を促す。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_credential_store.py`:

```python
import base64
import os

import pytest

from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore, CredentialUnusable

SECRET = "immich-api-key-value"  # noqa: S105


@pytest.fixture
def box():
    return SecretBox(os.urandom(32))


@pytest.fixture
def destination(db):
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    destination_id = new_id()
    db.execute(
        "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
        " VALUES (?, 'home', 'immich', 1, ?)",
        (destination_id, now_iso()),
    )
    return destination_id


def test_a_stored_secret_comes_back(db, box, destination):
    store = CredentialStore(db, box)
    credential_id = store.store(destination, SECRET)
    assert store.reveal(credential_id) == SECRET


def test_the_ciphertext_is_not_the_secret(db, box, destination):
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    blob = db.execute(
        "SELECT secret_encrypted FROM destination_credential WHERE id = ?", (credential_id,)
    ).fetchone()[0]
    assert SECRET.encode("utf-8") not in blob


def test_the_revision_increases_per_destination(db, box, destination):
    store = CredentialStore(db, box)
    first = store.store(destination, SECRET)
    second = store.store(destination, "rotated")
    revisions = {
        row["id"]: row["revision"]
        for row in db.execute("SELECT id, revision FROM destination_credential")
    }
    assert revisions[first] == 1
    assert revisions[second] == 2


def test_a_wrong_master_key_is_reported_not_overwritten(db, box, destination):
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    other = CredentialStore(db, SecretBox(os.urandom(32)))
    with pytest.raises(CredentialUnusable):
        other.reveal(credential_id)
    # 行はそのまま残る。再登録できるように「要再登録」として見せる。
    row = db.execute(
        "SELECT secret_encrypted, purged_at FROM destination_credential WHERE id = ?",
        (credential_id,),
    ).fetchone()
    assert row["secret_encrypted"] is not None
    assert row["purged_at"] is None


def test_a_row_moved_to_another_destination_does_not_decrypt(db, box, destination):
    """AAD に destination_id を含める. 行の差し替えを復号で検出する."""
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    other_id = new_id()
    db.execute(
        "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
        " VALUES (?, 'other', 'immich', 1, ?)",
        (other_id, now_iso()),
    )
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    db.execute(
        "UPDATE destination_credential SET destination_id = ? WHERE id = ?",
        (other_id, credential_id),
    )
    with pytest.raises(CredentialUnusable):
        CredentialStore(db, box).reveal(credential_id)


def test_purging_keeps_the_referenced_credential(db, box, destination):
    """参照が絶えた旧版だけを消す. 現行を消すと宛先が使えなくなる."""
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    store = CredentialStore(db, box)
    old = store.store(destination, SECRET)
    current = store.store(destination, "rotated")
    revision_id = new_id()
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
        " base_url, credential_id, created_at) VALUES (?, ?, 1, 1, ?, ?, ?)",
        (revision_id, destination, "http://immich.invalid", current, now_iso()),
    )

    assert store.purge_unreferenced(destination) == 1

    rows = {
        row["id"]: row
        for row in db.execute("SELECT * FROM destination_credential WHERE destination_id = ?",
                              (destination,))
    }
    assert rows[old]["secret_encrypted"] is None
    assert rows[old]["purged_at"] is not None
    assert rows[old]["key_fingerprint"]  # 監査のために指紋と作成時刻は残す
    assert rows[current]["secret_encrypted"] is not None


def test_a_purged_credential_cannot_be_revealed(db, box, destination):
    store = CredentialStore(db, box)
    credential_id = store.store(destination, SECRET)
    store.purge_unreferenced(destination)
    with pytest.raises(CredentialUnusable):
        store.reveal(credential_id)


def test_the_secret_is_not_in_the_exception_text(db, box, destination):
    credential_id = CredentialStore(db, box).store(destination, SECRET)
    other = CredentialStore(db, SecretBox(base64.b64decode(base64.b64encode(os.urandom(32)))))
    with pytest.raises(CredentialUnusable) as caught:
        other.reveal(credential_id)
    assert SECRET not in str(caught.value)
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_credential_store.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.db.credentials'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/db/credentials.py`:

```python
"""転送先 API キーの保管（§12.3）.

暗号文の形式は `core/crypto.py` が持つ。ここは版の採番と、参照が絶えた
旧版の破棄だけを行う。

**復号できない資格情報を「壊れたもの」として上書きしない。** マスター鍵の
取り違えは `WrongKeyError` で区別できるので、行を残したまま「要再登録」として
画面へ出す。上書きすると、正しい鍵を思い出しても戻せない。
"""

from __future__ import annotations

import sqlite3

from ..clock import now_iso
from ..core.crypto import SecretAad, SecretBox, SecretCorrupt, WrongKeyError
from ..ids import new_id
from .connection import immediate

# AAD に入れるスキーマ版。migration で意味が変わったら上げる。
SCHEMA_VERSION = 4


class CredentialUnusable(RuntimeError):
    """復号できない、または既に破棄されている.

    **秘密そのものは絶対に含めない。** 画面にも API 応答にも出る。
    """


class CredentialStore:
    def __init__(self, conn: sqlite3.Connection, box: SecretBox) -> None:
        self._conn = conn
        self._box = box

    def store(self, destination_id: str, secret: str) -> str:
        """新しい版として保存し、その id を返す."""
        with immediate(self._conn):
            return self.store_locked(destination_id, secret)

    def store_locked(self, destination_id: str, secret: str) -> str:
        """**呼び出し側が開いたトランザクションの中で使う。**

        宛先の作成・編集は 1 トランザクションで反映する必要がある（§8）ので、
        リポジトリ側の `BEGIN IMMEDIATE` の中から呼べる形を用意する。
        docstring だけの約束にしない —— 単独で呼ばれると autocommit になり、
        孤立した credential を作れてしまう。
        """
        if not self._conn.in_transaction:
            raise RuntimeError("store_locked は呼び出し側のトランザクションの中で使う")
        credential_id = new_id()
        row = self._conn.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM destination_credential"
            " WHERE destination_id = ?",
            (destination_id,),
        ).fetchone()
        revision = row["revision"] + 1
        aad = SecretAad(
            credential_id=credential_id,
            destination_id=destination_id,
            revision=revision,
            schema_version=SCHEMA_VERSION,
        )
        self._conn.execute(
            "INSERT INTO destination_credential (id, destination_id, revision,"
            " secret_encrypted, key_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                credential_id,
                destination_id,
                revision,
                self._box.encrypt(secret, aad),
                self._box.key_id,
                now_iso(),
            ),
        )
        return credential_id

    def reveal(self, credential_id: str) -> str:
        """送信の直前にだけ呼ぶ. 戻り値をログにも DB にも書かない."""
        row = self._conn.execute(
            "SELECT * FROM destination_credential WHERE id = ?", (credential_id,)
        ).fetchone()
        if row is None:
            raise CredentialUnusable(f"資格情報 {credential_id} が無い")
        if row["secret_encrypted"] is None:
            raise CredentialUnusable(f"資格情報 {credential_id} は破棄済み。再登録が要る")
        aad = SecretAad(
            credential_id=row["id"],
            destination_id=row["destination_id"],
            revision=row["revision"],
            schema_version=SCHEMA_VERSION,
        )
        try:
            return self._box.decrypt(row["secret_encrypted"], aad)
        except WrongKeyError as exc:
            raise CredentialUnusable(
                f"資格情報 {credential_id} は別のマスター鍵で暗号化されている"
                f"（記録 {exc.found} / 現在 {exc.expected}）。鍵を戻すか再登録する"
            ) from exc
        except SecretCorrupt as exc:
            raise CredentialUnusable(f"資格情報 {credential_id} を復号できない") from exc

    def purge_unreferenced(self, destination_id: str) -> int:
        """どのリビジョンからも参照されていない版の暗号文を消す.

        版管理したまま旧 API キーを持ち続けると、ローテートしても漏洩面が
        減らない。監査のために `key_fingerprint` と作成時刻は残す。
        """
        with immediate(self._conn):
            purged = self._conn.execute(
                "UPDATE destination_credential SET secret_encrypted = NULL, purged_at = ?"
                " WHERE destination_id = ? AND secret_encrypted IS NOT NULL"
                "   AND id NOT IN (SELECT credential_id FROM destination_revision"
                "                  WHERE destination_id = ?)",
                (now_iso(), destination_id, destination_id),
            )
            return purged.rowcount
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_credential_store.py -q`
Expected: PASS（8 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `revision` を常に 1 にする | `test_the_revision_increases_per_destination` |
| AAD から `destination_id` を落とす | `test_a_row_moved_to_another_destination_does_not_decrypt` |
| `WrongKeyError` を握りつぶして行を上書きする | `test_a_wrong_master_key_is_reported_not_overwritten` |
| `secret_encrypted IS NULL` の判定を消す | `test_a_purged_credential_cannot_be_revealed` |
| `purge_unreferenced` の `NOT IN (...)` を消す | `test_purging_keeps_the_referenced_credential` |
| 例外メッセージに `secret` を入れる | **意味のある変異を作れない**。このモジュールが平文を持つのは `reveal` の return の瞬間だけで、そこから例外を投げる経路が無い（`WrongKeyError` の分岐は平文を持っていない）。`test_the_secret_is_not_in_the_exception_text` は「鍵を取り違えたときのメッセージ」を固定する回帰テストとして残す。**秘密の露出は下流で見る** —— アダプタの例外（Task 4）、`last_error`（Task 9）、API 応答と `job.params_json`（Task 13） |
| `encrypt` を素通し（平文を保存）にする | `test_the_ciphertext_is_not_the_secret` |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/db/credentials.py app/tests/test_credential_store.py
git commit -m "feat(mediaferry): keep destination api keys encrypted at rest"
```

---

### Task 3: 転送先リポジトリ（不変リビジョンと `target_epoch`）

**Files:**
- Create: `app/src/mediaferry/db/destinations.py`
- Test: `app/tests/test_destination_repository.py`

**Interfaces:**
- Consumes: `normalize_endpoint`（Task 1）、`CredentialStore`（Task 2）
- Produces:
  - `DestinationRepository(conn, credentials: CredentialStore)`
  - `RemoteIdentity(remote_user_id: str | None, server_instance_id: str | None)`
  - `.create(name, base_url, public_url, secret, identity) -> str`
  - `.add_revision(destination_id, base_url, public_url, secret, identity, same_library=None) -> str`
  - `.current(destination_id) -> sqlite3.Row`
  - `.revision(revision_id) -> sqlite3.Row`
  - `.list_destinations(include_archived=False) -> list[sqlite3.Row]`
  - `.set_enabled(destination_id, enabled) -> None` / `.archive(destination_id) -> None`
  - `.same_account_warnings(identity, exclude_id=None) -> list[str]`
  - `EpochDecisionRequired(RuntimeError)` / `DestinationNotFound(RuntimeError)`

**1 回の編集は 1 トランザクションで反映する**（§8「編集は接続の検証に成功して
から原子的に反映する」）。宛先の INSERT、credential の INSERT、リビジョンの
INSERT と現行の差し替えを別々の `BEGIN IMMEDIATE` に分けると、途中で落ちたときに
**現行リビジョンを持たない宛先**や**孤立した credential** が残り、版番号の採番も
次回衝突しうる。`CredentialStore` には「呼び出し側のトランザクションの中で使う」
内部メソッドを用意する。

**`remote_user_id` が取れなければ何も保存しない。** `/api/users/me` の応答が
壊れている、または互換性が変わって `id` が無い場合に「検証済みだが向き先の
記録が無いリビジョン」を現行にすると、以後 preflight が必ず失敗して宛先が
使えなくなる（しかも epoch は進んでいる）。**検証の失敗として扱い、原子的に
拒否する。**

**`target_epoch` の規則（§8）:**

| 編集 | 再検証の結果 | `target_epoch` |
| --- | --- | --- |
| API キーのローテート、内部 URL の変更 | `remote_user_id` が同じ | 据え置き |
| 別アカウント・別サーバへの向き替え | `remote_user_id` が違う | 進める |
| `base_url` のホストが変わり `remote_user_id` は同じ | 判別できない | **`same_library` を渡すまで拒否**（`EpochDecisionRequired`） |

3 つ目を自動判定しないのが要点。同じユーザ UUID を持つ別ライブラリ（DB を複製・
復元したサーバ）かもしれず、黙ってどちらに倒しても壊れる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_destination_repository.py`:

```python
import os

import pytest

from mediaferry.core.crypto import SecretBox
from mediaferry.core.destinations.urls import EndpointRejected
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import (
    DestinationRepository,
    EpochDecisionRequired,
    RemoteIdentity,
)

USER_A = RemoteIdentity(remote_user_id="user-a", server_instance_id=None)
USER_B = RemoteIdentity(remote_user_id="user-b", server_instance_id=None)


@pytest.fixture
def repo(db):
    return DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))


def a_destination(repo, name="home", base_url="http://immich.invalid:2283"):
    return repo.create(
        name=name, base_url=base_url, public_url=None, secret="key-1", identity=USER_A
    )


def test_creating_a_destination_stores_a_verified_revision(repo, db):
    destination_id = a_destination(repo)
    row = repo.current(destination_id)
    assert row["revision"] == 1
    assert row["target_epoch"] == 1
    assert row["base_url"] == "http://immich.invalid:2283"
    assert row["remote_user_id"] == "user-a"
    assert row["verified_at"] is not None
    assert repo.secret_of(row["id"]) == "key-1"


def test_the_url_is_normalised_before_it_is_stored(repo):
    destination_id = repo.create(
        name="trailing",
        base_url="http://immich.invalid:2283/",
        public_url="HTTPS://Photos.Invalid/",
        secret="key-1",
        identity=USER_A,
    )
    row = repo.current(destination_id)
    assert row["base_url"] == "http://immich.invalid:2283"
    assert row["public_url"] == "https://photos.invalid"


def test_an_unusable_url_is_refused_before_anything_is_written(repo, db):
    with pytest.raises(EndpointRejected):
        repo.create(
            name="bad", base_url="javascript:alert(1)", public_url=None,
            secret="key-1", identity=USER_A,
        )
    assert db.execute("SELECT count(*) FROM upload_destination").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM destination_credential").fetchone()[0] == 0


def test_rotating_the_key_keeps_the_epoch(repo):
    destination_id = a_destination(repo)
    repo.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2", identity=USER_A,
    )
    row = repo.current(destination_id)
    assert row["revision"] == 2
    assert row["target_epoch"] == 1  # 履歴を引き継ぐ
    assert repo.secret_of(row["id"]) == "key-2"


def test_pointing_at_another_account_advances_the_epoch(repo):
    destination_id = a_destination(repo)
    repo.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-1", identity=USER_B,
    )
    assert repo.current(destination_id)["target_epoch"] == 2


def test_a_changed_host_with_the_same_user_needs_an_answer(repo):
    """DB を複製・復元した別ライブラリかもしれない. 自動判定しない."""
    destination_id = a_destination(repo)
    with pytest.raises(EpochDecisionRequired):
        repo.add_revision(
            destination_id, base_url="http://other.invalid:2283", public_url=None,
            secret="key-1", identity=USER_A,
        )


def test_the_answer_decides_whether_the_history_carries_over(repo):
    destination_id = a_destination(repo)
    repo.add_revision(
        destination_id, base_url="http://other.invalid:2283", public_url=None,
        secret="key-1", identity=USER_A, same_library=True,
    )
    assert repo.current(destination_id)["target_epoch"] == 1

    repo.add_revision(
        destination_id, base_url="http://third.invalid:2283", public_url=None,
        secret="key-1", identity=USER_A, same_library=False,
    )
    assert repo.current(destination_id)["target_epoch"] == 2


def test_a_missing_identity_is_refused_atomically(repo, db):
    """向き先が分からない設定は保存しない.

    保存すると preflight が必ず失敗する宛先ができ、しかも epoch は進んでいる。
    """
    from mediaferry.db.destinations import IdentityUnknown

    destination_id = a_destination(repo)
    before = repo.current(destination_id)["id"]
    with pytest.raises(IdentityUnknown):
        repo.add_revision(
            destination_id, base_url="http://immich.invalid:2283", public_url=None,
            secret="key-2",
            identity=RemoteIdentity(remote_user_id=None, server_instance_id=None),
        )
    assert repo.current(destination_id)["id"] == before
    assert db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1
    # 資格情報も増えない。
    assert db.execute("SELECT count(*) FROM destination_credential").fetchone()[0] == 1


def test_a_failure_midway_leaves_nothing_behind(repo, db, monkeypatch):
    """1 回の編集は 1 トランザクション. 継ぎ目で落ちても中途半端にしない."""
    from mediaferry.db import destinations as module

    destination_id = a_destination(repo)
    monkeypatch.setattr(
        module.DestinationRepository, "_write_revision",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("継ぎ目で落ちた")),
    )
    with pytest.raises(RuntimeError):
        repo.add_revision(
            destination_id, base_url="http://immich.invalid:2283", public_url=None,
            secret="key-2", identity=USER_A,
        )
    assert db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1
    # 孤立した credential を残さない。
    assert db.execute("SELECT count(*) FROM destination_credential").fetchone()[0] == 1


def test_revisions_are_immutable(repo, db):
    import sqlite3

    destination_id = a_destination(repo)
    revision_id = repo.current(destination_id)["id"]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE destination_revision SET base_url = 'http://x.invalid' WHERE id = ?",
            (revision_id,),
        )


def test_the_previous_key_stays_while_a_revision_references_it(repo, db):
    destination_id = a_destination(repo)
    first = repo.current(destination_id)["credential_id"]
    repo.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2", identity=USER_A,
    )
    # 旧リビジョンは残るので、その credential も参照されたまま。
    assert db.execute(
        "SELECT secret_encrypted IS NOT NULL FROM destination_credential WHERE id = ?", (first,)
    ).fetchone()[0] == 1


def test_the_same_account_is_warned_not_refused(repo):
    a_destination(repo, name="internal")
    warnings = repo.same_account_warnings(USER_A)
    assert warnings and "internal" in warnings[0]
    # 拒否も統合もしない。同じアカウントを別名で持つのは正当な使い方。
    second = repo.create(
        name="vpn", base_url="http://vpn.invalid:2283", public_url=None,
        secret="key-1", identity=USER_A,
    )
    assert repo.current(second)["remote_user_id"] == "user-a"


def test_archiving_takes_it_out_of_the_list_but_keeps_the_history(repo, db):
    destination_id = a_destination(repo)
    repo.archive(destination_id)
    assert repo.list_destinations() == []
    assert len(repo.list_destinations(include_archived=True)) == 1
    assert db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1


def test_disabling_keeps_it_listed(repo):
    destination_id = a_destination(repo)
    repo.set_enabled(destination_id, False)
    rows = repo.list_destinations()
    assert [row["enabled"] for row in rows] == [0]
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_destination_repository.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.db.destinations'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/db/destinations.py`:

```python
"""転送先プロファイルの保存（§8 / §12.3）.

**リビジョンは不変。** 編集のたびに新しい行が増える。`target_epoch` は
向き先が変わったときだけ進み、アップロード履歴を引き継いでよいかの境界になる。

`remote_user_id` は同一性ではなく guard（Phase 0 の実測。Immich は
サーバインスタンス ID を公開していない）。同じアカウントを指す宛先を 2 つ作るのは
正当な使い方なので、**警告は出すが拒否も統合もしない**。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..clock import now_iso
from ..core.destinations.urls import normalize_endpoint
from ..ids import new_id
from .connection import immediate
from .credentials import CredentialStore


class DestinationNotFound(RuntimeError):
    pass


class IdentityUnknown(RuntimeError):
    """接続の検証で `remote_user_id` を観測できなかった.

    保存すると、preflight が必ず失敗する宛先ができる（§10）。
    """


class EpochDecisionRequired(RuntimeError):
    """同じユーザのままホストが変わった. 履歴を引き継ぐかを人が決める.

    DB を複製・復元した別ライブラリかもしれないし、経路を変えただけかもしれない。
    自動では判別できない。
    """


@dataclass(frozen=True)
class RemoteIdentity:
    """接続の検証で観測した値. 同一性ではない."""

    remote_user_id: str | None
    server_instance_id: str | None


class DestinationRepository:
    def __init__(self, conn: sqlite3.Connection, credentials: CredentialStore) -> None:
        self._conn = conn
        self._credentials = credentials

    def create(
        self,
        name: str,
        base_url: str,
        public_url: str | None,
        secret: str,
        identity: RemoteIdentity,
    ) -> str:
        """検証に成功した設定だけを保存する（§12.3）."""
        # URL と向き先の検証を先に通す。落ちたら 1 行も書かない。
        endpoints = _endpoints(base_url, public_url)
        _require_identity(identity)
        destination_id = new_id()
        # **1 トランザクション。** 途中で落ちても、現行リビジョンの無い宛先や
        # 孤立した credential を残さない（§8）。
        with immediate(self._conn):
            self._conn.execute(
                "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
                " VALUES (?, ?, 'immich', 1, ?)",
                (destination_id, name, now_iso()),
            )
            credential_id = self._credentials.store_locked(destination_id, secret)
            self._write_revision(
                destination_id=destination_id,
                revision=1,
                target_epoch=1,
                endpoints=endpoints,
                credential_id=credential_id,
                identity=identity,
            )
        return destination_id

    def add_revision(
        self,
        destination_id: str,
        base_url: str,
        public_url: str | None,
        secret: str,
        identity: RemoteIdentity,
        same_library: bool | None = None,
    ) -> str:
        """編集を新しいリビジョンとして反映する. 戻り値は revision_id."""
        endpoints = _endpoints(base_url, public_url)
        _require_identity(identity)
        with immediate(self._conn):
            # **現行の読出しと版番号の決定もトランザクションの中で行う。**
            # 外に出すと、同時に 2 つの編集が同じ revision N を読み、片方が
            # UNIQUE 違反で 500 になる。
            current = self.current(destination_id)
            epoch = _next_epoch(current, endpoints[0], identity, same_library)
            credential_id = self._credentials.store_locked(destination_id, secret)
            revision_id = self._write_revision(
                destination_id=destination_id,
                revision=current["revision"] + 1,
                target_epoch=epoch,
                endpoints=endpoints,
                credential_id=credential_id,
                identity=identity,
            )
            if epoch != current["target_epoch"]:
                # **同じトランザクションで**旧 epoch の未 claim 項目を破棄する（§8）。
                # 分けると、間で落ちたときに理由の無い pending が永久に残る。
                self._invalidate_old_epoch_locked(destination_id, epoch)
        return revision_id

    def current(self, destination_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT r.* FROM upload_destination d"
            " JOIN destination_revision r ON r.id = d.current_revision_id"
            " WHERE d.id = ?",
            (destination_id,),
        ).fetchone()
        if row is None:
            raise DestinationNotFound(f"転送先 {destination_id} に現行リビジョンが無い")
        return row

    def revision(self, revision_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM destination_revision WHERE id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise DestinationNotFound(f"リビジョン {revision_id} が無い")
        return row

    def secret_of(self, revision_id: str) -> str:
        """送信の直前にだけ呼ぶ."""
        return self._credentials.reveal(self.revision(revision_id)["credential_id"])

    def get(self, destination_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM upload_destination WHERE id = ?", (destination_id,)
        ).fetchone()

    def list_destinations(self, include_archived: bool = False) -> list[sqlite3.Row]:
        if include_archived:
            return list(
                self._conn.execute("SELECT * FROM upload_destination ORDER BY created_at")
            )
        return list(
            self._conn.execute(
                "SELECT * FROM upload_destination WHERE archived_at IS NULL ORDER BY created_at"
            )
        )

    def rename_or_toggle(
        self, destination_id: str, name: str | None = None, enabled: bool | None = None
    ) -> None:
        """接続に関わらない編集. **リビジョンを増やさない**（§8 の「編集」ではない）."""
        assignments, params = [], []
        if name is not None:
            assignments.append("name = ?")
            params.append(name)
        if enabled is not None:
            assignments.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not assignments:
            return
        with immediate(self._conn):
            self._conn.execute(
                f"UPDATE upload_destination SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608
                (*params, destination_id),
            )

    def set_enabled(self, destination_id: str, enabled: bool) -> None:
        with immediate(self._conn):
            self._conn.execute(
                "UPDATE upload_destination SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, destination_id),
            )

    def archive(self, destination_id: str) -> None:
        """物理削除しない. 履歴と監査情報を残す."""
        with immediate(self._conn):
            self._conn.execute(
                "UPDATE upload_destination SET archived_at = ?, enabled = 0 WHERE id = ?",
                (now_iso(), destination_id),
            )

    def same_account_warnings(
        self, identity: RemoteIdentity, exclude_id: str | None = None
    ) -> list[str]:
        """同じ Immich アカウントを指す宛先を挙げる. 拒否はしない."""
        if identity.remote_user_id is None:
            return []
        rows = self._conn.execute(
            "SELECT d.id AS id, d.name AS name FROM upload_destination d"
            " JOIN destination_revision r ON r.id = d.current_revision_id"
            " WHERE r.remote_user_id = ? AND d.archived_at IS NULL",
            (identity.remote_user_id,),
        )
        return [
            f"転送先「{row['name']}」が同じ Immich アカウントを指している"
            for row in rows
            if row["id"] != exclude_id
        ]

    # ------------------------------------------------------------------
    def _write_revision(
        self,
        destination_id: str,
        revision: int,
        target_epoch: int,
        endpoints: tuple[str, str | None],
        credential_id: str,
        identity: RemoteIdentity,
    ) -> str:
        """**呼び出し側が開いたトランザクションの中で使う。** 単独では開かない."""
        if not self._conn.in_transaction:
            raise RuntimeError("_write_revision は呼び出し側のトランザクションの中で使う")
        revision_id = new_id()
        base_url, public_url = endpoints
        self._conn.execute(
            "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
            " base_url, public_url, credential_id, remote_user_id, server_instance_id,"
            " verified_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                destination_id,
                revision,
                target_epoch,
                base_url,
                public_url,
                credential_id,
                identity.remote_user_id,
                identity.server_instance_id,
                now_iso(),
                now_iso(),
            ),
        )
        # 現行の差し替えは同じトランザクションで行う。分けると、
        # 「新しい版はあるが誰も使っていない」窓ができる。
        self._conn.execute(
            "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
            (revision_id, destination_id),
        )
        return revision_id


    def _invalidate_old_epoch_locked(self, destination_id: str, current_epoch: int) -> None:
        """旧 epoch の未完了レコードを破棄する. **`complete` は履歴として残す**（§8）."""
        self._conn.execute(
            "UPDATE upload_record SET invalidated_at = ?,"
            " invalidated_reason = '宛先の向き先が変わった', updated_at = ?"
            " WHERE destination_id = ? AND target_epoch < ? AND invalidated_at IS NULL"
            "   AND state <> 'complete'",
            (now_iso(), now_iso(), destination_id, current_epoch),
        )


def _require_identity(identity: RemoteIdentity) -> None:
    """向き先を観測できていない設定は保存しない."""
    if not identity.remote_user_id:
        raise IdentityUnknown(
            "接続の検証で remote_user_id を取得できなかった。設定を保存しない"
        )


def _endpoints(base_url: str, public_url: str | None) -> tuple[str, str | None]:
    """両方に同じ検証を掛ける. public_url は画面に描画されるので緩めない."""
    return normalize_endpoint(base_url), (
        None if public_url is None else normalize_endpoint(public_url)
    )


def _next_epoch(
    current: sqlite3.Row,
    base_url: str,
    identity: RemoteIdentity,
    same_library: bool | None,
) -> int:
    """向き先が変わったときだけ epoch を進める（§8）.

    `identity.remote_user_id` は `_require_identity` を通っているので非 None。
    """
    epoch = current["target_epoch"]
    if identity.remote_user_id != current["remote_user_id"]:
        # 別アカウント。履歴を引き継がない。
        return epoch + 1
    if _host_of(base_url) == _host_of(current["base_url"]):
        return epoch
    if same_library is None:
        raise EpochDecisionRequired(
            "ホストが変わったが同じユーザを指している。同じライブラリかを確認する"
        )
    return epoch if same_library else epoch + 1


def _host_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_destination_repository.py -q`
Expected: PASS（13 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `_next_epoch` の `remote_user_id != current` の判定を消す | `test_pointing_at_another_account_advances_the_epoch` |
| `_require_identity` を消す | `test_a_missing_identity_is_refused_atomically` |
| `create` / `add_revision` の**書き込み**を複数トランザクションに戻す | `test_a_failure_midway_leaves_nothing_behind` |
| `add_revision` の**読み出しと版番号の決定**だけをトランザクションの外へ出す | **落ちない**。単一スレッドでは、読んだ後に別の編集が割り込む筋書きを作れない。同時 PATCH の保険として記録する（実害は「両方が revision N を読み、片方が UNIQUE 違反で 500」） |
| `store_locked` の `in_transaction` 検査を消す | `test_store_locked_refuses_to_run_without_a_transaction`（Task 2 のテストに追加） |
| `add_revision` の `_invalidate_old_epoch_locked` を別トランザクションにする | **落ちない**（単一プロセスのテストでは間で落とせない）。原子性の保険として記録する。呼び出し自体を消す変異は Task 12 の `test_records_from_an_old_epoch_are_invalidated_with_a_reason` が捕まえる |
| ホストが変わったときも据え置きにする（`EpochDecisionRequired` を投げない） | `test_a_changed_host_with_the_same_user_needs_an_answer` |
| `same_library` の分岐を反転する | `test_the_answer_decides_whether_the_history_carries_over` |
| `_endpoints` の正規化を素通しにする | `test_the_url_is_normalised_before_it_is_stored` |
| URL の検証を宛先の INSERT より後に移す | `test_an_unusable_url_is_refused_before_anything_is_written` |
| `same_account_warnings` を常に空にする | `test_the_same_account_is_warned_not_refused` |
| `same_account_warnings` を「拒否」にする（例外を投げる） | 同上（2 つ目の作成が落ちる） |
| `archive` を物理削除にする | `test_archiving_takes_it_out_of_the_list_but_keeps_the_history` |
| `list_destinations` の `archived_at IS NULL` を消す | 同上 |
| `set_enabled(False)` を `archive` と同じ実装にする | `test_disabling_keeps_it_listed` |
| `_write_revision` の `current_revision_id` の更新を消す | `test_creating_a_destination_stores_a_verified_revision`（`current` が落ちる） |

**検出できない変異:** `destination_revision` の不変 trigger（`no_update` / `no_delete`）は
`0004` のマイグレーションが持っており、このタスクのコードには無い。
`test_revisions_are_immutable` はスキーマ側の保証を固定するための回帰テストである。

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/db/destinations.py app/tests/test_destination_repository.py
git commit -m "feat(mediaferry): version destination settings and guard the epoch"
```

---

### Task 4: Immich クライアント

**Files:**
- Create: `app/src/mediaferry/adapters/immich.py`
- Create: `app/tests/fake_immich.py`
- Test: `app/tests/test_adapter_immich.py`

**Interfaces:**
- Produces:
  - `ImmichClient(base_url: str, api_key: str, timeout_seconds: int = 86400)`
  - `.users_me() -> dict[str, Any]` / `.close() -> None` / コンテキストマネージャ
  - `.bulk_upload_check(items: Sequence[tuple[str, str]]) -> dict[str, CheckOutcome]`（`(key, sha1_hex)`）
  - `.upload_asset(path: Path, *, sha1_hex: str, device_asset_id: str, file_created_at: str, file_modified_at: str) -> UploadOutcome`
  - `.ensure_tag(name: str) -> str` / `.tag_assets(tag_id: str, asset_ids: Sequence[str]) -> None`
  - `.set_date_time_original(asset_id: str, when: str) -> None`
  - `CheckOutcome(action: str, asset_id: str | None, is_trashed: bool)`
  - `UploadOutcome(asset_id: str, status: str)`
  - `ImmichError` / `ImmichAuthFailed` / `ImmichRejected` / `ImmichUnavailable` / `ImmichRedirected` / `ImmichProtocolError`
  - `to_base64_checksum(sha1_hex: str) -> str` / `BULK_CHECK_BATCH: int`

**Phase 0 で実測済みの前提**（`phase0-findings.md` ②）:

- `x-immich-checksum` は **base64**。`bulk-upload-check` は hex / base64 の両方を
  受理するが、**両方 base64 に統一する**
- `bulk-upload-check` の応答は `action: accept` / `action: reject, reason: "duplicate",
  assetId, isTrashed`
- `POST /api/assets` の応答は `{"id": ..., "status": "created" | "duplicate"}`
- `deviceAssetId` は資産応答から読み戻せない。**送るが読まない**

**まだ実測していないエンドポイント（Task 14 の `needs_immich` テストで確かめる）:**
タグの取得・作成・付与（`GET /api/tags` / `POST /api/tags` / `PUT /api/tags/{id}/assets`）と
日時の更新（`PUT /api/assets/{id}`）は Phase 0 のプローブに含まれていない。
**対象バージョンの OpenAPI 定義と突き合わせてから実機に当てる。** 形が違っていたら
このアダプタだけを直せば済むように、呼び出し側は `ImmichClient` のメソッドしか触らない。

**redirect を追わない**（§12.4）。`x-api-key` はカスタムヘッダなので、cross-origin の
redirect でもクライアントは剥がさない。誤設定や侵害されたエンドポイントが外部へ
301 を返すと、API キーがそのまま渡る。同一 origin でも、**本文を伴う要求
（アップロード）では追わない** —— ファイルオブジェクトは 1 回目の送信で EOF に
達しているので、追うと**空か途中までの本文を送る**ことになる。

**応答は fail-open にしない。** 件数の食い違い、未知の `action` / `status`、
`reject` なのに `assetId` が無い応答は `ImmichProtocolError` にする。黙って
読み飛ばすと、recheck が「N 件確認した」と表示しながら実際には何も見ていない
状態になる。

**例外に相手の応答本文を入れない**（§12.3 / §14）。本文は相手が決める値なので、
誤設定先や侵害された proxy が受け取った `x-api-key` をそのまま返せば、
`last_error` として DB に永続化され API と画面にも出る。例外に載せるのは
**メソッド・パス・ステータス・固定の理由コード**だけにする。

- [ ] **Step 1: テスト用の Immich を書く**

`app/tests/fake_immich.py`:

```python
"""テスト用の Immich（ループバックで実際に listen する HTTP サーバ）.

**実物の httpx で、実物のソケットに対して叩く。** クライアントを差し替えて
「呼んだつもり」を確かめるテストは、ヘッダ名や encoding の取り違えを見逃す。

ASGI ではなく素の HTTP サーバにしているのは、httpx 0.28 の `ASGITransport` が
非同期用（`handle_async_request` しか持たない）で、**同期の `httpx.Client` から
使えない**ため。multipart の wire と redirect の扱いをそのまま確かめる意味でも、
実際に listen させる方が確実である。

応答の形は `docs/phase0-findings.md` ② の実測に合わせる。
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

API_KEY = "test-api-key"  # noqa: S105
USER_ID = "user-uuid-1"


class FakeImmich:
    """状態を持つ最小の Immich.

    `assets` はチェックサム（base64）から資産 ID への写像。
    """

    def __init__(self, user_id: str = USER_ID) -> None:
        self.user_id = user_id
        self.assets: dict[str, str] = {}
        self.trashed: set[str] = set()
        self.tags: dict[str, str] = {}  # name -> id
        self.tagged: dict[str, list[str]] = {}  # tag_id -> asset_ids
        self.datetimes: dict[str, str] = {}
        self.uploads: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []
        self.fail_next: int = 0  # 次の N 回を 503 にする
        self.redirect_to: str | None = None
        # 400 の本文に、受け取った API キーをそのまま返す（秘密の漏れを見る）。
        self.echo_key_in_error: bool = False
        self._server: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._server = server

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        assert self._server is not None, "start() を先に呼ぶ"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # ------------------------------------------------------------------
    def route(self, method: str, path: str, body: bytes, headers: dict[str, str]):  # noqa: ANN201
        self.requests.append((method, path))
        if self.redirect_to is not None:
            return 301, {"location": self.redirect_to}
        if headers.get("x-api-key") != API_KEY:
            return 401, {"message": "Invalid API key"}
        if self.fail_next > 0:
            self.fail_next -= 1
            return 503, {"message": "unavailable"}
        if self.echo_key_in_error:
            return 400, {"message": f"bad request from key {headers.get('x-api-key')}"}

        if method == "GET" and path == "/api/users/me":
            return 200, {"id": self.user_id, "email": "someone@example.invalid"}
        if method == "POST" and path == "/api/assets/bulk-upload-check":
            return 200, self._bulk_check(json.loads(body))
        if method == "POST" and path == "/api/assets":
            return self._upload(body, headers)
        if method == "GET" and path == "/api/tags":
            return 200, [{"id": tag_id, "name": name} for name, tag_id in self.tags.items()]
        if method == "POST" and path == "/api/tags":
            name = json.loads(body)["name"]
            tag_id = self.tags.setdefault(name, f"tag-{len(self.tags) + 1}")
            return 201, {"id": tag_id, "name": name}
        if method == "PUT" and path.startswith("/api/tags/") and path.endswith("/assets"):
            tag_id = path.split("/")[3]
            ids = json.loads(body)["ids"]
            self.tagged.setdefault(tag_id, []).extend(ids)
            return 200, [{"id": asset_id, "success": True} for asset_id in ids]
        if method == "PUT" and path.startswith("/api/assets/"):
            asset_id = path.split("/")[3]
            self.datetimes[asset_id] = json.loads(body)["dateTimeOriginal"]
            return 200, {"id": asset_id}
        return 404, {"message": f"no route for {method} {path}"}

    def _bulk_check(self, payload):  # noqa: ANN001, ANN202
        results = []
        for item in payload["assets"]:
            asset_id = self.assets.get(item["checksum"])
            if asset_id is None:
                results.append({"id": item["id"], "action": "accept"})
            else:
                results.append(
                    {
                        "id": item["id"],
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": asset_id,
                        "isTrashed": asset_id in self.trashed,
                    }
                )
        return {"results": results}

    def _upload(self, body, headers):  # noqa: ANN001, ANN202
        fields = _parse_multipart(body, headers["content-type"])
        data = fields["assetData"]
        checksum = base64.b64encode(hashlib.sha1(data, usedforsecurity=False).digest()).decode()
        if headers.get("x-immich-checksum") != checksum:
            return 400, {"message": "checksum header mismatch"}
        self.uploads.append(
            {**{k: v for k, v in fields.items() if k != "assetData"}, "size": len(data)}
        )
        existing = self.assets.get(checksum)
        if existing is not None:
            return 200, {"id": existing, "status": "duplicate"}
        asset_id = f"asset-{len(self.assets) + 1}"
        self.assets[checksum] = asset_id
        return 201, {"id": asset_id, "status": "created"}


def _handler_for(fake: FakeImmich):  # noqa: ANN202
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            """テストの出力を汚さない."""

        def _respond(self, method: str) -> None:
            length = int(self.headers.get("content-length", 0))
            body = self.rfile.read(length) if length else b""
            headers = {key.lower(): value for key, value in self.headers.items()}
            status, payload = fake.route(method, self.path, body, headers)
            if status == 301:
                self.send_response(301)
                self.send_header("location", payload["location"])
                self.send_header("content-length", "0")
                self.end_headers()
                return
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._respond("PUT")

    return Handler


def _parse_multipart(body: bytes, content_type: str) -> dict[str, Any]:
    """multipart/form-data の最小パーサ. 名前と中身だけを取り出す."""
    boundary = content_type.split("boundary=")[1].encode()
    fields: dict[str, Any] = {}
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        if b'name="' not in head:
            continue
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        content = value.rsplit(b"\r\n", 1)[0]
        fields[name] = content if name == "assetData" else content.decode()
    return fields
```

**共有のフィクスチャを `conftest.py` に置く**（Task 5 以降のすべてが使う）:

```python
@pytest.fixture
def immich():
    """ループバックで listen する fake Immich. テストごとに新しいポート."""
    from .fake_immich import FakeImmich

    server = FakeImmich()
    server.start()
    yield server
    server.stop()
```

- [ ] **Step 2: 失敗するテストを書く**

`app/tests/test_adapter_immich.py`:

```python
import base64
import hashlib

import pytest

from mediaferry.adapters.immich import (
    ImmichAuthFailed,
    ImmichClient,
    ImmichProtocolError,
    ImmichRedirected,
    ImmichRejected,
    ImmichUnavailable,
    to_base64_checksum,
)

from .fake_immich import API_KEY


@pytest.fixture
def client(immich):
    with ImmichClient(immich.url, API_KEY) as client:
        yield client


def a_file(tmp_path, payload=b"movie-bytes"):
    path = tmp_path / "DJI_0001.MP4"
    path.write_bytes(payload)
    return path, hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def an_upload(client, path, sha1):
    return client.upload_asset(
        path,
        sha1_hex=sha1,
        device_asset_id="mediaferry:m1",
        file_created_at="2026-08-17T14:30:00+00:00",
        file_modified_at="2026-08-17T14:30:00+00:00",
    )


def test_the_identity_of_the_target_is_read(client, immich):
    assert client.users_me()["id"] == immich.user_id


def test_a_wrong_api_key_is_an_auth_failure(immich):
    with ImmichClient(immich.url, "wrong") as client, pytest.raises(ImmichAuthFailed):
        client.users_me()


def test_an_unknown_checksum_is_accepted(client, tmp_path):
    _, sha1 = a_file(tmp_path)
    outcome = client.bulk_upload_check([("k1", sha1)])["k1"]
    assert outcome.action == "accept"
    assert outcome.asset_id is None


def test_a_known_checksum_comes_back_with_its_asset_id(client, tmp_path):
    path, sha1 = a_file(tmp_path)
    uploaded = an_upload(client, path, sha1)
    outcome = client.bulk_upload_check([("k1", sha1)])["k1"]
    assert outcome.action == "reject"
    assert outcome.asset_id == uploaded.asset_id
    assert outcome.is_trashed is False


def test_a_trashed_asset_is_reported_as_trashed(client, immich, tmp_path):
    path, sha1 = a_file(tmp_path)
    uploaded = an_upload(client, path, sha1)
    immich.trashed.add(uploaded.asset_id)
    assert client.bulk_upload_check([("k1", sha1)])["k1"].is_trashed is True


def test_the_checksum_is_sent_as_base64_in_both_places(client, immich, tmp_path):
    path, sha1 = a_file(tmp_path)
    an_upload(client, path, sha1)
    # fake は base64 のヘッダしか受理しない（400 なら upload_asset が送出する）。
    expected = base64.b64encode(bytes.fromhex(sha1)).decode()
    assert to_base64_checksum(sha1) == expected
    assert list(immich.assets) == [expected]


def test_the_upload_carries_the_device_asset_id(client, immich, tmp_path):
    path, sha1 = a_file(tmp_path)
    an_upload(client, path, sha1)
    assert immich.uploads[0]["deviceAssetId"] == "mediaferry:m1"
    assert immich.uploads[0]["fileCreatedAt"] == "2026-08-17T14:30:00+00:00"


def test_a_second_upload_of_the_same_bytes_is_a_duplicate(client, tmp_path):
    path, sha1 = a_file(tmp_path)
    first = an_upload(client, path, sha1)
    second = an_upload(client, path, sha1)
    assert first.status == "created"
    assert second.status == "duplicate"
    assert second.asset_id == first.asset_id


def test_a_large_file_goes_through_in_one_piece(client, immich, tmp_path):
    """8 MiB を送っても、途中で切れずに届く（ストリーミング送信の経路）."""
    path, sha1 = a_file(tmp_path, b"x" * (8 * 1024 * 1024))
    an_upload(client, path, sha1)
    assert immich.uploads[0]["size"] == 8 * 1024 * 1024


def test_a_tag_is_created_once_and_reused(client, immich):
    first = client.ensure_tag("mediaferry")
    second = client.ensure_tag("mediaferry")
    assert first == second
    assert immich.requests.count(("POST", "/api/tags")) == 1


def test_assets_are_added_to_a_tag(client, immich):
    tag_id = client.ensure_tag("mediaferry")
    client.tag_assets(tag_id, ["asset-1", "asset-2"])
    assert immich.tagged[tag_id] == ["asset-1", "asset-2"]


def test_the_capture_time_can_be_written_back(client, immich):
    client.set_date_time_original("asset-1", "2026-08-17T14:30:00+09:00")
    assert immich.datetimes["asset-1"] == "2026-08-17T14:30:00+09:00"


def test_a_redirect_to_another_host_never_gets_the_key(client, immich):
    immich.redirect_to = "http://immich-evil.invalid/api/users/me"
    with pytest.raises(ImmichRedirected):
        client.users_me()


def test_an_upload_is_never_redirected_even_within_the_same_origin(client, immich, tmp_path):
    """本文を伴う要求は追わない. ファイルは 1 回目で EOF に達している."""
    path, sha1 = a_file(tmp_path)
    immich.redirect_to = f"{immich.url}/api/assets"
    with pytest.raises(ImmichRedirected):
        an_upload(client, path, sha1)


def test_a_server_error_is_unavailable_not_rejected(client, immich):
    immich.fail_next = 1
    with pytest.raises(ImmichUnavailable):
        client.users_me()


def test_a_large_check_is_split_into_batches(client, immich):
    from mediaferry.adapters.immich import BULK_CHECK_BATCH

    items = [(f"k{i}", f"{i:040x}") for i in range(BULK_CHECK_BATCH + 5)]
    outcomes = client.bulk_upload_check(items)
    assert len(outcomes) == len(items)
    assert immich.requests.count(("POST", "/api/assets/bulk-upload-check")) == 2


def test_a_missing_result_is_a_protocol_error(client, immich, monkeypatch):
    """件数が合わない応答を黙って読み飛ばさない."""
    real = immich._bulk_check  # noqa: SLF001

    def drop_one(payload):
        body = real(payload)
        body["results"] = body["results"][:-1]
        return body

    monkeypatch.setattr(immich, "_bulk_check", drop_one)
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40), ("k2", "1" * 40)])


def test_an_unknown_action_is_a_protocol_error(client, immich, monkeypatch):
    monkeypatch.setattr(
        immich, "_bulk_check", lambda payload: {"results": [{"id": "k1", "action": "maybe"}]}
    )
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


def test_a_reject_without_an_asset_id_is_a_protocol_error(client, immich, monkeypatch):
    monkeypatch.setattr(
        immich,
        "_bulk_check",
        lambda payload: {"results": [{"id": "k1", "action": "reject", "reason": "duplicate"}]},
    )
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


def test_a_malformed_response_is_a_protocol_error(client, immich, monkeypatch):
    """scalar や配列を返す相手でも、プロトコル不一致として分類できる."""
    monkeypatch.setattr(immich, "_bulk_check", lambda payload: ["not", "an", "object"])
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


def test_an_unknown_upload_status_is_a_protocol_error(client, immich, tmp_path, monkeypatch):
    path, sha1 = a_file(tmp_path)
    monkeypatch.setattr(
        immich, "_upload", lambda body, headers: (201, {"id": "asset-x", "status": "queued"})
    )
    with pytest.raises(ImmichProtocolError):
        an_upload(client, path, sha1)


def test_the_error_text_never_carries_the_response_body(client, immich):
    """相手の応答本文を例外に載せない. 受け取った API キーを返す相手がいる."""
    immich.echo_key_in_error = True
    with pytest.raises(ImmichRejected) as caught:
        client.users_me()
    assert API_KEY not in str(caught.value)
    assert "400" in str(caught.value)
```

- [ ] **Step 3: 失敗を確認する**

Run: `uv run pytest app/tests/test_adapter_immich.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.adapters.immich'`）

- [ ] **Step 4: 最小実装**

`app/src/mediaferry/adapters/immich.py`:

```python
"""Immich の HTTP クライアント（§9.10 / §12.4）.

**redirect を追わない。** `x-api-key` はカスタムヘッダなので、cross-origin の
redirect でもクライアントは剥がさない。誤設定や侵害されたエンドポイントが
外部へ 301 を返すと、API キーがそのまま渡る。同一 origin のときだけ 1 回追い、
**本文を伴う要求では一切追わない**（ファイルは 1 回目の送信で EOF に達している
ので、追うと空か途中までの本文を送る）。

チェックサムは **base64 に統一**する（Phase 0 の実測。`x-immich-checksum` は
base64、`bulk-upload-check` は両方を受理する）。片方に揃えないと取り違えが起きる。

ファイルは**ストリーミングで送る**。数十 GiB をメモリへ載せない。

**例外に相手の応答本文を入れない。** 本文は相手が決める値で、受け取った
`x-api-key` を返す実装がありうる。載せると `last_error` として DB に永続化され、
API と画面にも出る（§12.3）。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# 1 回の bulk-upload-check に載せる件数。応答が大きくなりすぎない範囲で減らす。
BULK_CHECK_BATCH = 500
# 同一 origin の redirect だけを、この回数まで手動で追う（本文の無い要求のみ）。
MAX_SAME_ORIGIN_REDIRECTS = 3
CHECK_ACTIONS = frozenset({"accept", "reject"})
UPLOAD_STATUSES = frozenset({"created", "duplicate"})


class ImmichError(RuntimeError):
    """Immich とのやり取りが期待どおりに終わらなかった."""


class ImmichAuthFailed(ImmichError):
    """401 / 403。API キーが違うか失効している."""


class ImmichRejected(ImmichError):
    """4xx。要求そのものが受理されない（再試行しても変わらない）."""


class ImmichUnavailable(ImmichError):
    """5xx・接続不能・タイムアウト。再試行の余地がある."""


class ImmichRedirected(ImmichError):
    """別の origin へ飛ばされた、または本文を伴う要求が redirect された.

    **秘密も本文も送らずに止める。**
    """


class ImmichProtocolError(ImmichError):
    """応答の形が契約と違う.

    黙って読み飛ばすと、「N 件確認した」と表示しながら実際には何も見ていない
    状態になる。
    """


@dataclass(frozen=True)
class CheckOutcome:
    action: str  # accept / reject
    asset_id: str | None
    is_trashed: bool


@dataclass(frozen=True)
class UploadOutcome:
    asset_id: str
    status: str  # created / duplicate


def to_base64_checksum(sha1_hex: str) -> str:
    """DB は hex で持ち、Immich へは base64 で送る."""
    return base64.b64encode(bytes.fromhex(sha1_hex)).decode("ascii")


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 86400) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"x-api-key": api_key, "accept": "application/json"},
            timeout=httpx.Timeout(timeout_seconds, connect=30.0),
            # **既定で追わない。** 同一 origin のときだけ手動で追う。
            follow_redirects=False,
        )

    def __enter__(self) -> ImmichClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    def users_me(self) -> dict[str, Any]:
        """向き先の同定に使う（§8）. preflight もこれを叩く."""
        return self._request("GET", "/api/users/me").json()

    def bulk_upload_check(self, items: Sequence[tuple[str, str]]) -> dict[str, CheckOutcome]:
        """`(key, sha1_hex)` の列を照合する. 戻り値は key ごとの結果.

        **要求した key と応答の key が全単射であることを確かめる。**
        """
        outcomes: dict[str, CheckOutcome] = {}
        for start in range(0, len(items), BULK_CHECK_BATCH):
            batch = items[start : start + BULK_CHECK_BATCH]
            payload = {
                "assets": [
                    {"id": key, "checksum": to_base64_checksum(sha1)} for key, sha1 in batch
                ]
            }
            body = self._request("POST", "/api/assets/bulk-upload-check", json=payload).json()
            outcomes.update(_parsed_check(body, [key for key, _ in batch]))
        return outcomes

    def upload_asset(
        self,
        path: Path,
        *,
        sha1_hex: str,
        device_asset_id: str,
        file_created_at: str,
        file_modified_at: str,
    ) -> UploadOutcome:
        """multipart で送る. ファイルはストリーミングで読む."""
        data = {
            "deviceAssetId": device_asset_id,
            "deviceId": "mediaferry",
            "fileCreatedAt": file_created_at,
            "fileModifiedAt": file_modified_at,
            "isFavorite": "false",
        }
        with path.open("rb") as stream:
            response = self._request(
                "POST",
                "/api/assets",
                # **本文を伴うので redirect を一切追わない。**
                allow_redirect=False,
                data=data,
                files={"assetData": (path.name, stream, "application/octet-stream")},
                headers={"x-immich-checksum": to_base64_checksum(sha1_hex)},
            )
        body = _as_object(response, "POST /api/assets")
        status = body.get("status")
        if status not in UPLOAD_STATUSES:
            raise ImmichProtocolError(f"POST /api/assets の status が未知: {status!r}")
        if not body.get("id"):
            raise ImmichProtocolError("POST /api/assets の応答に id が無い")
        return UploadOutcome(asset_id=body["id"], status=status)

    def find_tag(self, name: str) -> str | None:
        """既存のタグを探す（読み取りのみ）."""
        tags = self._request("GET", "/api/tags").json()
        if not isinstance(tags, list):
            raise ImmichProtocolError("GET /api/tags の応答が配列ではない")
        for tag in tags:
            if not isinstance(tag, dict) or not isinstance(tag.get("name"), str):
                raise ImmichProtocolError("GET /api/tags の要素の形が違う")
            if tag["name"] == name:
                return _required_str(tag, "id", "GET /api/tags")
        return None

    def create_tag(self, name: str) -> str:  # noqa: D401
        """タグを作る（変更を伴う）.

        **`find_tag` と分けてある。** 呼び出し側は「変更を伴う呼び出しの直前」
        ごとに所有権と向き先を確かめる必要があり、探索と作成が 1 メソッドに
        まとまっていると、その間に guard を挟めない。
        """
        return _required_str(
            _as_object(self._request("POST", "/api/tags", json={"name": name}), "POST /api/tags"),
            "id",
            "POST /api/tags",
        )

    def ensure_tag(self, name: str) -> str:
        """探して無ければ作る. **guard を挟めないので、ジョブからは使わない。**

        `needs_immich` の疎通確認のような、所有権の要らない場面向け。
        """
        return self.find_tag(name) or self.create_tag(name)

    def tag_assets(self, tag_id: str, asset_ids: Sequence[str]) -> None:
        self._request("PUT", f"/api/tags/{tag_id}/assets", json={"ids": list(asset_ids)})

    def set_date_time_original(self, asset_id: str, when: str) -> None:
        self._request("PUT", f"/api/assets/{asset_id}", json={"dateTimeOriginal": when})

    # ------------------------------------------------------------------
    def _request(
        self, method: str, path: str, allow_redirect: bool = True, **kwargs: Any
    ) -> httpx.Response:
        url = path
        for _ in range(MAX_SAME_ORIGIN_REDIRECTS + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                # 例外の型と URL だけ。ヘッダ（API キー）も応答本文も含めない。
                raise ImmichUnavailable(
                    f"{method} {path} に失敗した: {type(exc).__name__}"
                ) from exc
            if not response.is_redirect:
                return self._checked(method, path, response)
            if not allow_redirect:
                raise ImmichRedirected(
                    f"{method} {path} が redirect された。本文を伴う要求は追わない"
                )
            url = self._same_origin_target(response)
        raise ImmichRedirected(f"{method} {path} の redirect が多すぎる")

    def _same_origin_target(self, response: httpx.Response) -> str:
        """scheme・host・port が同じときだけ追う. それ以外は秘密を送らない."""
        location = response.headers.get("location", "")
        target = urlsplit(str(response.url.join(location)))
        base = urlsplit(self._base_url)
        if (target.scheme, target.hostname, target.port) != (
            base.scheme,
            base.hostname,
            base.port,
        ):
            raise ImmichRedirected("別の origin へ redirect された")
        return str(response.url.join(location))

    def _checked(self, method: str, path: str, response: httpx.Response) -> httpx.Response:
        """**応答本文を例外へ載せない。** 相手が API キーを echo しうる."""
        if response.status_code in (401, 403):
            raise ImmichAuthFailed(f"{method} {path} が {response.status_code}")
        if response.status_code >= 500:
            raise ImmichUnavailable(f"{method} {path} が {response.status_code}")
        if response.status_code >= 400:
            logger.debug("%s %s が %s", method, path, response.status_code)
            raise ImmichRejected(f"{method} {path} が {response.status_code}")
        return response


def _as_object(response: httpx.Response, label: str) -> dict[str, Any]:
    """JSON を object として読む. 壊れた応答も protocol error に正規化する."""
    try:
        body = response.json()
    except ValueError as exc:
        raise ImmichProtocolError(f"{label} の応答が JSON ではない") from exc
    if not isinstance(body, dict):
        raise ImmichProtocolError(f"{label} の応答が object ではない")
    return body


def _required_str(body: dict[str, Any], key: str, label: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise ImmichProtocolError(f"{label} の応答に {key} が無い")
    return value


def _parsed_check(body: Any, expected: Sequence[str]) -> dict[str, CheckOutcome]:
    """応答を検証して写像にする. 欠落・重複・未知の値は protocol error.

    **型も見る。** proxy や別バージョンが scalar や list を返したとき、
    `AttributeError` ではなく「プロトコルが違う」として分類・表示できるようにする。
    """
    if not isinstance(body, dict):
        raise ImmichProtocolError("bulk-upload-check の応答が object ではない")
    results = body.get("results")
    if not isinstance(results, list):
        raise ImmichProtocolError("bulk-upload-check の応答に results が無い")
    outcomes: dict[str, CheckOutcome] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ImmichProtocolError("bulk-upload-check の results の要素が object ではない")
        key = result.get("id")
        if not isinstance(key, str):
            raise ImmichProtocolError("bulk-upload-check の結果に文字列の id が無い")
        if key in outcomes:
            raise ImmichProtocolError(f"bulk-upload-check の応答に重複した id: {key!r}")
        action = result.get("action")
        if action not in CHECK_ACTIONS:
            raise ImmichProtocolError(f"bulk-upload-check の action が未知: {action!r}")
        asset_id = result.get("assetId")
        if action == "reject" and not asset_id:
            raise ImmichProtocolError("reject なのに assetId が無い")
        trashed = result.get("isTrashed")
        if action == "reject" and not isinstance(trashed, bool):
            # **既定を False にしない。** 欄が無い応答を「ゴミ箱に無い」と
            # 決めつけると、消された資産を送信済みとして扱う根拠が消える。
            raise ImmichProtocolError("reject なのに isTrashed が bool でない")
        outcomes[key] = CheckOutcome(
            action=action, asset_id=asset_id, is_trashed=bool(trashed)
        )
    missing = [key for key in expected if key not in outcomes]
    extra = [key for key in outcomes if key not in expected]
    if missing or extra:
        raise ImmichProtocolError(
            f"bulk-upload-check の応答が要求と一致しない（欠落 {len(missing)} 件 /"
            f" 余分 {len(extra)} 件）"
        )
    return outcomes
```

- [ ] **Step 5: 通ることを確認する**

Run: `uv run pytest app/tests/test_adapter_immich.py -q`
Expected: PASS（21 件）

- [ ] **Step 6: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `follow_redirects=True` にする | `test_a_redirect_to_another_host_never_gets_the_key` |
| `_same_origin_target` の origin 比較を消す | 同上 |
| `upload_asset` の `allow_redirect=False` を外す | `test_an_upload_is_never_redirected_even_within_the_same_origin` |
| `to_base64_checksum` を hex のまま返す | `test_the_checksum_is_sent_as_base64_in_both_places`（fake が 400 を返す） |
| `x-immich-checksum` ヘッダを付けない | 同上 |
| `bulk_upload_check` の `checksum` を hex にする | `test_a_known_checksum_comes_back_with_its_asset_id`（照合できず accept になる） |
| `is_trashed` を `result.get("isTrashed", False)` に戻す | `test_a_trashed_asset_is_reported_as_trashed` は落ちないが、**`isTrashed` を落とした応答を受理してしまう**。`_parsed_check` の bool 判定を消す変異として `test_a_reject_without_an_asset_id_is_a_protocol_error` と同じ形のテストで捕まえる |
| `_parsed_check` の全単射チェックを消す | `test_a_missing_result_is_a_protocol_error` |
| 応答の型検証（`isinstance`）を消す | `test_a_malformed_response_is_a_protocol_error` |
| `ensure_tag` を分けずに 1 メソッドへ戻す | Task 9 の「タグごとに guard」が書けなくなる。`test_a_tag_is_created_once_and_reused` は通るので、**呼び出し側の変異**として Task 9 側で捕まえる |
| `action` の検証を消す | `test_an_unknown_action_is_a_protocol_error` |
| `reject` の `assetId` 検証を消す | `test_a_reject_without_an_asset_id_is_a_protocol_error` |
| `upload_asset` の `status` 検証を消す | `test_an_unknown_upload_status_is_a_protocol_error` |
| `_checked` に `response.text[:200]` を戻す | `test_the_error_text_never_carries_the_response_body` |
| `BULK_CHECK_BATCH` の分割を消す（全件を 1 回で送る） | `test_a_large_check_is_split_into_batches` |
| `ensure_tag` の既存検索を消す | `test_a_tag_is_created_once_and_reused` |
| 401 を `ImmichRejected` にする | `test_a_wrong_api_key_is_an_auth_failure` |
| 5xx を `ImmichRejected` にする | `test_a_server_error_is_unavailable_not_rejected` |
| `deviceAssetId` を送らない | `test_the_upload_carries_the_device_asset_id` |
| `path.read_bytes()` を渡す形にする（ストリーミングをやめる） | **落ちない**。結果が同じになるため。8 MiB のテストは経路が通ることしか見ていない。検出できない変異として記録する（メモリ使用量の観測はテストの範囲外） |

`isTrashed` の欠落を捕まえるテストを足す:

```python
def test_a_reject_without_is_trashed_is_a_protocol_error(client, immich, monkeypatch):
    """`isTrashed` の欠落を False に丸めない. 消された資産を送信済みにしてしまう."""
    monkeypatch.setattr(
        immich,
        "_bulk_check",
        lambda payload: {
            "results": [{"id": "k1", "action": "reject", "reason": "duplicate",
                         "assetId": "asset-1"}]
        },
    )
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])
```

- [ ] **Step 7: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/adapters/immich.py app/tests/fake_immich.py \
        app/tests/conftest.py app/tests/test_adapter_immich.py
git commit -m "feat(mediaferry): talk to immich without following redirects"
```

---

### Task 5: 送信前の preflight

**Files:**
- Create: `app/src/mediaferry/jobs/preflight.py`
- Test: `app/tests/test_preflight.py`

**Interfaces:**
- Consumes: `ImmichClient`（Task 4）、`DestinationRepository`（Task 3）
- Produces:
  - `PreflightCache(repo: DestinationRepository, open_client: Callable[[sqlite3.Row], ImmichClient], ttl_seconds: float = PREFLIGHT_TTL_SECONDS)`
  - `.assert_target(revision_id: str) -> None`
  - `PreflightFailed(RuntimeError)` / `PREFLIGHT_TTL_SECONDS: float`

**なぜ要るか（§10）:** `destination_revision.remote_user_id` は**登録・編集の時点の**
観測値にすぎない。宛先を編集しなくても、DNS・リバースプロキシ・Immich 本体の
差し替えで同じ `base_url` の先が別のライブラリに変わる。比較しなければ guard は働かない。

**リビジョンごとに 1 回だけ確認し、成功の判定には寿命を持たせる。** 毎 pair で
叩くと 1000 件のアップロードで 1000 回の `/api/users/me` になるが、**「ジョブ中
1 回」では 70 GiB × 数本の 20 時間級ジョブに粗すぎる**（その間に DNS・proxy・
Immich 本体が差し替わりうる）。成功は `PREFLIGHT_TTL_SECONDS` だけ有効とし、
期限が切れていれば次の副作用の前に取り直す。

**失敗の判定は寿命を持たせない。** 一度止めた宛先へ、同じジョブ内で何度も
試さない。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_preflight.py`:

```python
import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.jobs.preflight import PreflightCache, PreflightFailed

from .fake_immich import API_KEY, FakeImmich

BASE_URL = "http://immich.invalid:2283"


@pytest.fixture
def world(db, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    repo = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = repo.create(
        name="home", base_url=BASE_URL, public_url=None, secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    opened = []

    def open_client(revision):
        opened.append(revision["id"])
        return ImmichClient(revision["base_url"], API_KEY)

    return server, repo, destination_id, PreflightCache(repo, open_client), opened


def test_a_matching_target_passes(world):
    _, repo, destination_id, preflight, _ = world
    preflight.assert_target(repo.current(destination_id)["id"])


def test_the_check_is_shared_within_the_job(world):
    _, repo, destination_id, preflight, opened = world
    revision_id = repo.current(destination_id)["id"]
    preflight.assert_target(revision_id)
    preflight.assert_target(revision_id)
    assert opened == [revision_id]


def test_the_check_is_repeated_after_the_ttl(world):
    """長いジョブでは、途中で向き先が差し替わりうる."""
    from mediaferry.jobs.preflight import PreflightCache

    server, repo, destination_id, _, opened = world
    revision_id = repo.current(destination_id)["id"]
    preflight = PreflightCache(repo, _opener(server, opened), ttl_seconds=0)

    preflight.assert_target(revision_id)
    server.user_id = "someone-else"
    with pytest.raises(PreflightFailed):
        preflight.assert_target(revision_id)


def _opener(server, opened):
    from mediaferry.adapters.immich import ImmichClient

    def open_client(revision):
        opened.append(revision["id"])
        return ImmichClient(revision["base_url"], API_KEY)

    return open_client


def test_a_different_user_stops_the_revision(world):
    server, repo, destination_id, preflight, _ = world
    # 同じ URL の先が別のライブラリに差し替わった。
    server.user_id = "someone-else"
    with pytest.raises(PreflightFailed):
        preflight.assert_target(repo.current(destination_id)["id"])


def test_an_unreachable_target_stops_the_revision(world):
    server, repo, destination_id, preflight, _ = world
    server.fail_next = 1
    with pytest.raises(PreflightFailed):
        preflight.assert_target(repo.current(destination_id)["id"])


def test_a_failure_is_remembered_so_the_rest_do_not_try(world):
    server, repo, destination_id, preflight, opened = world
    server.user_id = "someone-else"
    revision_id = repo.current(destination_id)["id"]
    for _ in range(3):
        with pytest.raises(PreflightFailed):
            preflight.assert_target(revision_id)
    assert opened == [revision_id]


def test_a_revision_without_a_recorded_user_is_refused(world, db):
    _, repo, destination_id, preflight, _ = world
    db.execute(
        "UPDATE upload_destination SET current_revision_id = NULL WHERE id = ?",
        (destination_id,),
    )
    # 記録が無いリビジョンは突き合わせようがない。送らずに止める。
    revision_id = db.execute(
        "SELECT id FROM destination_revision WHERE destination_id = ?", (destination_id,)
    ).fetchone()[0]
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("DELETE FROM destination_revision WHERE id = ?", (revision_id,))
    db.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(Exception):  # noqa: B017 - リビジョンが無い時点で続行しない
        preflight.assert_target(revision_id)
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_preflight.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.jobs.preflight'`）

**注:** 最後のテストは `destination_revision` の削除 trigger に阻まれる。
実装を書いた後で、**リビジョンが読めない場合に `PreflightFailed` を出す**形へ直し、
テストも `pytest.raises(PreflightFailed)` に書き換える（Step 5 で扱う）。

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/jobs/preflight.py`:

```python
"""送信前の向き先の再確認（§10）.

`destination_revision.remote_user_id` は登録・編集の時点の観測値にすぎない。
宛先を編集しなくても、DNS・リバースプロキシ・Immich 本体の差し替えで同じ
`base_url` の先が別のライブラリに変わる。**あるリビジョンの最初の pair を
送る前に 1 回、`/api/users/me` を取り直して突き合わせる。**

結果は 1 ジョブ内で共有する。1000 件のアップロードで 1000 回叩かない。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import time

from ..adapters.immich import ImmichClient, ImmichError
from ..db.destinations import DestinationNotFound, DestinationRepository

# 成功の判定が有効な時間。20 時間級のジョブでも、この間隔で取り直す。
PREFLIGHT_TTL_SECONDS = 900.0


class PreflightFailed(RuntimeError):
    """向き先が変わっている、または確認できない.

    **そのリビジョンの pair は 1 バイトも送らない。**
    """


class PreflightCache:
    def __init__(
        self,
        repo: DestinationRepository,
        open_client: Callable[[sqlite3.Row], ImmichClient],
        ttl_seconds: float = PREFLIGHT_TTL_SECONDS,
    ) -> None:
        self._repo = repo
        self._open_client = open_client
        self._ttl = ttl_seconds
        self._failed: dict[str, PreflightFailed] = {}
        self._verified_at: dict[str, float] = {}

    def assert_target(self, revision_id: str) -> None:
        failure = self._failed.get(revision_id)
        if failure is not None:
            # 一度失敗したリビジョンへは、同じジョブ内でもう試さない。
            raise failure
        checked = self._verified_at.get(revision_id)
        if checked is not None and time.monotonic() - checked < self._ttl:
            return
        try:
            self._check(revision_id)
        except PreflightFailed as exc:
            self._failed[revision_id] = exc
            raise
        self._verified_at[revision_id] = time.monotonic()

    def _check(self, revision_id: str) -> None:
        try:
            revision = self._repo.revision(revision_id)
        except DestinationNotFound as exc:
            raise PreflightFailed(str(exc)) from exc
        expected = revision["remote_user_id"]
        if expected is None:
            raise PreflightFailed(
                f"リビジョン {revision_id} には向き先の記録が無い。接続を検証し直す"
            )
        try:
            with self._open_client(revision) as client:
                observed = client.users_me().get("id")
        except ImmichError as exc:
            raise PreflightFailed(f"向き先を確認できない: {exc}") from exc
        if observed != expected:
            raise PreflightFailed(
                f"向き先が変わっている（記録 {expected} / 現在 {observed}）。"
                "転送先の設定を確認し直す"
            )
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_preflight.py -q`
Expected: PASS（6 件。最後のテストは Step 5 で書き換える）

- [ ] **Step 5: 最後のテストを実装に合わせて直す**

`destination_revision` は削除できない（`0004` の trigger）。**リビジョンが読めない
場面は「現行リビジョンが未設定の宛先」で作れる**ので、そちらで固定する。

```python
def test_a_revision_without_a_recorded_identity_is_refused(world, db):
    _, repo, destination_id, preflight, _ = world
    revision_id = repo.current(destination_id)["id"]
    # 検証していない（remote_user_id が無い）リビジョンは突き合わせようがない。
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("DROP TRIGGER destination_revision_no_update")
    db.execute(
        "UPDATE destination_revision SET remote_user_id = NULL WHERE id = ?", (revision_id,)
    )
    db.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(PreflightFailed):
        preflight.assert_target(revision_id)


def test_an_unknown_revision_is_refused(world):
    _, _, _, preflight, _ = world
    with pytest.raises(PreflightFailed):
        preflight.assert_target("no-such-revision")
```

**trigger を落としてから UPDATE するのは、テストがこの分岐へ到達する唯一の方法。**
アプリの経路では `remote_user_id` が NULL のリビジョンは作られない（`create` /
`add_revision` は検証済みの値しか書かない）が、手で DB を触った場合や、将来
「検証を後回しにする」経路を足した場合の保険として残す。

- [ ] **Step 6: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `observed != expected` の比較を消す | `test_a_different_user_stops_the_revision` |
| `ImmichError` を握りつぶして通す | `test_an_unreachable_target_stops_the_revision` |
| 結果のキャッシュを消す（毎回叩く） | `test_the_check_is_shared_within_the_job` |
| TTL を無限にする（`ttl_seconds` を無視する） | `test_the_check_is_repeated_after_the_ttl` |
| 失敗をキャッシュしない（成功だけ覚える） | `test_a_failure_is_remembered_so_the_rest_do_not_try` |
| `expected is None` の判定を消す | `test_a_revision_without_a_recorded_identity_is_refused` |
| `DestinationNotFound` を素通しにする | `test_an_unknown_revision_is_refused`（例外の種類が変わる） |

- [ ] **Step 7: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/jobs/preflight.py app/tests/test_preflight.py
git commit -m "feat(mediaferry): re-check where a revision points before sending"
```

---

### Task 6: 選択条件の共有（§10 (a) の derived 条件）

**Files:**
- Modify: `app/src/mediaferry/db/selection.py`
- Test: `app/tests/test_selection.py`（追記）

**Interfaces:**
- Produces:
  - `group_is_current(conn, registry, group_id: str, media_file_id: str) -> bool`
  - `expected_digest(conn, registry, group_id: str) -> str | None`
- 既存の `SelectionService.selectable` の挙動は**変えない**

**なぜ切り出すか:** §10 (a) の「derived なら、生成元グループが `superseded_by_id IS NULL`
かつ `output_media_file_id = M.id` かつ `input_digest` が現行と一致」は、
**一覧（(b)）と claim 時（(a)）の両方**で評価する。写しを作ると、一覧には出るのに
claim で必ず拒否される（またはその逆の）状態ができる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_selection.py` に追記:

```python
def test_a_current_group_reports_itself_as_current(db, profile):
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    assert group_is_current(db, ProfileRegistry(db), group_id, output_id) is True


def test_a_stale_digest_is_not_current(db, profile):
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id, digest="stale-digest")
    assert group_is_current(db, ProfileRegistry(db), group_id, output_id) is False


def test_another_media_file_is_not_the_output_of_this_group(db, profile):
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    other_id = a_derived(db, profile, name="OTHER")
    group_id = a_group(db, profile, members, output_id=output_id)
    assert group_is_current(db, ProfileRegistry(db), group_id, other_id) is False


def test_a_superseded_group_is_not_current(db, profile):
    from mediaferry.db.selection import group_is_current

    output_id = a_derived(db, profile)
    old = a_group(db, profile, [], output_id=output_id)
    newer = a_merge_group(db, (profile.profile_id, profile.revision_id), "digest-new")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, old))
    assert group_is_current(db, ProfileRegistry(db), old, output_id) is False


def test_an_unknown_group_is_not_current(db, profile):
    from mediaferry.db.selection import group_is_current

    assert group_is_current(db, ProfileRegistry(db), "no-such-group", "no-such-media") is False


def test_the_list_and_the_single_check_agree(db, profile):
    """一覧の判定と claim 側の判定が食い違わない."""
    from mediaferry.db.selection import group_is_current

    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    listed = SelectionService(db, ProfileRegistry(db)).selectable()
    assert [item.media_file_id for item in listed] == [output_id]
    assert group_is_current(db, ProfileRegistry(db), group_id, output_id) is True
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_selection.py -q`
Expected: FAIL（`ImportError: cannot import name 'group_is_current'`）

- [ ] **Step 3: 最小実装**

`db/selection.py` の末尾に足し、`_matching_digests` から共有部分を呼ぶ形にする。

```python
def expected_digest(
    conn: sqlite3.Connection, registry: ProfileRegistry, group_id: str
) -> str | None:
    """現行の構成・設定・リビジョンから計算し直した digest.

    グループが無ければ None。**保存値との比較は呼び出し側が行う。**
    """
    row = conn.execute(
        "SELECT profile_id FROM merge_group WHERE id = ?", (group_id,)
    ).fetchone()
    if row is None:
        return None
    members = [
        (member["media_file_id"], member["sha1"])
        for member in conn.execute(
            "SELECT m.id AS media_file_id, m.sha1 AS sha1 FROM merge_member mm"
            " JOIN media_file m ON m.id = mm.media_file_id"
            " WHERE mm.merge_group_id = ? AND mm.active = 1 ORDER BY mm.position",
            (group_id,),
        )
    ]
    profile = registry.by_id(row["profile_id"])
    return input_digest(members, profile.definition.merge, profile.revision_id)


def group_is_current(
    conn: sqlite3.Connection, registry: ProfileRegistry, group_id: str, media_file_id: str
) -> bool:
    """§10 (a) の derived 条件. claim 時と一覧の両方がこれを使う.

    supersede されておらず、その media_file がこのグループの出力で、
    入力の同一性が現行と一致していること。
    """
    row = conn.execute(
        "SELECT superseded_by_id, output_media_file_id, input_digest FROM merge_group"
        " WHERE id = ?",
        (group_id,),
    ).fetchone()
    if row is None or row["superseded_by_id"] is not None:
        return False
    if row["output_media_file_id"] != media_file_id:
        return False
    return expected_digest(conn, registry, group_id) == row["input_digest"]
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_selection.py -q`
Expected: PASS（22 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `superseded_by_id is not None` の判定を消す | `test_a_superseded_group_is_not_current` |
| `output_media_file_id != media_file_id` の判定を消す | `test_another_media_file_is_not_the_output_of_this_group` |
| digest の比較を常に真にする | `test_a_stale_digest_is_not_current` |
| `row is None` の判定を消す | `test_an_unknown_group_is_not_current`（例外になる） |
| `expected_digest` の `mm.active = 1` を消す | **落ちない**。`active` は `superseded_by_id IS NULL` の写しで、supersede されたグループは先に弾かれる。スキーマの trigger が保証する冗長として記録する |
| `ORDER BY mm.position` を外す | **落ちない可能性が高い**（2 件の挿入順と position が一致する）。**position の逆順で挿入するテストを足して確かめる**（下記） |

順序のテストを足す:

```python
def test_the_digest_follows_the_member_position_not_the_insert_order(db, profile):
    from mediaferry.db.selection import expected_digest
    from mediaferry.core.merge.digest import input_digest

    first, second = a_pair(db, profile)
    group_id = a_merge_group(db, (profile.profile_id, profile.revision_id), "d")
    # position とは逆の順で挿入する。
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 1, 1)",
        (group_id, second[0]),
    )
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group_id, first[0]),
    )
    assert expected_digest(db, ProfileRegistry(db), group_id) == input_digest(
        [first, second], profile.definition.merge, profile.revision_id
    )
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/db/selection.py app/tests/test_selection.py
git commit -m "feat(mediaferry): share the derived safety predicate with the claim path"
```

---

### Task 7: pair の作成（`POST /uploads` の意味論）

**Files:**
- Create: `app/src/mediaferry/db/uploads.py`
- Test: `app/tests/test_upload_pairs.py`

**Interfaces:**
- Consumes: `group_is_current`（Task 6）、`DestinationRepository`（Task 3）
- Produces:
  - `UploadRepository(conn, registry: ProfileRegistry, destinations: DestinationRepository)`
  - `.create_pairs(media_ids: Sequence[str], destination_ids: Sequence[str]) -> list[PairResult]`
  - `PairResult(media_file_id: str, destination_id: str, result: str, record_id: str | None, reason: str | None)`
  - `UploadRequestInvalid(ValueError)`
  - `RESULTS: frozenset[str]`

**§10 の意味論をそのまま実装する。**

1. **先に一括で検証する** — 全 ID の実在、宛先が `enabled` かつ `archived_at IS NULL`
   かつ現行リビジョンを持つ。ここで落ちたら**何も作らずに全体を拒否**する
2. **pair の作成は 1 トランザクション**
3. 既存レコードがある場合の遷移は §10 の表に従う

| 結果 | 意味 |
| --- | --- |
| `created` | 新しく `pending` を作った |
| `retry_queued` | `failed` から `pending` へ戻した |
| `already_complete` / `already_active` / `awaiting_approval` | 何もしていない |
| `rejected` | 安全条件または選択条件を満たさない。理由を添える |

**未採用の derived を選ぶ操作は「採用」そのものとして扱う。** 同じトランザクションで
`adopted_at` を立て、`selection_rule = 'adopted_derived'` で作る。条件を
「まだ採用していない derived」にすると、作った瞬間に自分自身が条件を満たさなくなる。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_upload_pairs.py`:

```python
import json
import os

import pytest

from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import PairResult, UploadRepository, UploadRequestInvalid

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_selection import a_derived, a_group, a_pair

IDENTITY = RemoteIdentity(remote_user_id="user-a", server_instance_id=None)


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


@pytest.fixture
def destinations(db):
    return DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))


@pytest.fixture
def uploads(db, destinations):
    return UploadRepository(db, ProfileRegistry(db), destinations)


def a_destination(destinations, name="home"):
    return destinations.create(
        name=name, base_url=f"http://{name}.invalid:2283", public_url=None,
        secret="key-1", identity=IDENTITY,
    )


def results_of(pairs):
    return {(pair.media_file_id, pair.destination_id): pair.result for pair in pairs}


def test_a_plain_original_becomes_a_pending_record(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([media_id], [destination_id])

    assert [pair.result for pair in pairs] == ["created"]
    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["state"] == "pending"
    assert row["selection_rule"] == "default"
    assert row["origin"] == "unknown"
    assert row["target_epoch"] == destinations.current(destination_id)["target_epoch"]
    assert row["eligibility_reason"]


def test_the_cross_product_is_expanded(db, profile, destinations, uploads):
    media = [a_media_file(db, (profile.profile_id, profile.revision_id),
                          rel_path=f"library/dji-osmo/DCIM/X{i}.MP4") for i in (1, 2)]
    targets = [a_destination(destinations, "home"), a_destination(destinations, "family")]

    pairs = uploads.create_pairs(media, targets)

    assert len(pairs) == 4
    assert set(results_of(pairs).values()) == {"created"}


def test_an_unknown_media_id_rejects_the_whole_request(db, profile, destinations, uploads):
    destination_id = a_destination(destinations)
    with pytest.raises(UploadRequestInvalid):
        uploads.create_pairs(["no-such-media"], [destination_id])
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 0


def test_a_disabled_destination_rejects_the_whole_request(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    destinations.set_enabled(destination_id, False)
    with pytest.raises(UploadRequestInvalid):
        uploads.create_pairs([media_id], [destination_id])
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 0


def test_an_archived_destination_rejects_the_whole_request(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    destinations.archive(destination_id)
    with pytest.raises(UploadRequestInvalid):
        uploads.create_pairs([media_id], [destination_id])


def test_a_missing_file_is_rejected_per_pair(db, profile, destinations, uploads):
    present = a_media_file(db, (profile.profile_id, profile.revision_id),
                           rel_path="library/dji-osmo/DCIM/OK.MP4")
    gone = a_media_file(db, (profile.profile_id, profile.revision_id),
                        rel_path="library/dji-osmo/DCIM/GONE.MP4",
                        missing_at="2026-08-17T00:00:00+00:00")
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([present, gone], [destination_id])

    assert results_of(pairs)[(gone, destination_id)] == "rejected"
    assert results_of(pairs)[(present, destination_id)] == "created"
    # 1 件の拒否が他を巻き込まない。
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 1


def test_a_member_of_an_active_group_is_rejected(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id)
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([members[0][0]], [destination_id])

    assert pairs[0].result == "rejected"
    assert "グループ" in pairs[0].reason


def test_a_member_of_a_failed_group_is_allowed_with_its_rule(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="failed", verification=None)
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([members[0][0]], [destination_id])

    assert pairs[0].result == "created"
    assert db.execute("SELECT selection_rule FROM upload_record").fetchone()[0] == (
        "failed_group_member"
    )


def test_a_verified_derived_is_default(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([output_id], [destination_id])

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert pairs[0].result == "created"
    assert row["selection_rule"] == "default"
    assert row["merge_group_id"] == group_id


def test_choosing_an_unadopted_derived_adopts_it(db, profile, destinations, uploads):
    """採用そのものとして扱う. 別操作にすると、作った瞬間に条件を満たさなくなる."""
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id,
                       verification=json.dumps({"passed": False}))
    destination_id = a_destination(destinations)

    pairs = uploads.create_pairs([output_id], [destination_id])

    assert pairs[0].result == "created"
    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["selection_rule"] == "adopted_derived"
    assert db.execute(
        "SELECT adopted_at FROM merge_group WHERE id = ?", (group_id,)
    ).fetchone()[0] is not None


def test_a_derived_from_a_stale_group_is_rejected(db, profile, destinations, uploads):
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id, digest="stale-digest")
    destination_id = a_destination(destinations)

    assert uploads.create_pairs([output_id], [destination_id])[0].result == "rejected"


def test_a_complete_record_is_a_no_op(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    uploads.create_pairs([media_id], [destination_id])
    revision_id = destinations.current(destination_id)["id"]
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ?", (revision_id,)
    )

    pairs = uploads.create_pairs([media_id], [destination_id])

    assert pairs[0].result == "already_complete"
    assert db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 1


def test_an_active_record_is_not_claimed_twice(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    uploads.create_pairs([media_id], [destination_id])
    revision_id = destinations.current(destination_id)["id"]
    db.execute(
        "UPDATE upload_record SET state = 'uploading', claim_job_id = 'j', claim_token = 't',"
        " claim_expires_at = '2999-01-01T00:00:00+00:00', destination_revision_id = ?",
        (revision_id,),
    )
    assert uploads.create_pairs([media_id], [destination_id])[0].result == "already_active"


def test_a_waiting_record_is_left_to_the_approval_flow(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    uploads.create_pairs([media_id], [destination_id])
    db.execute("UPDATE upload_record SET state = 'awaiting_datetime_approval'")
    assert uploads.create_pairs([media_id], [destination_id])[0].result == "awaiting_approval"


def test_a_failed_record_is_queued_again_without_changing_its_rule(
    db, profile, destinations, uploads
):
    members = a_pair(db, profile)
    a_group(db, profile, members, status="failed", verification=None)
    destination_id = a_destination(destinations)
    uploads.create_pairs([members[0][0]], [destination_id])
    db.execute("UPDATE upload_record SET state = 'failed', attempts = 3, last_error = 'boom'")

    pairs = uploads.create_pairs([members[0][0]], [destination_id])

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert pairs[0].result == "retry_queued"
    assert row["state"] == "pending"
    # 選択の根拠は書き換えない。再試行は「なぜ送信を許可したか」を変えない。
    assert row["selection_rule"] == "failed_group_member"
    assert row["attempts"] == 3


def test_an_invalidated_record_is_not_reused(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    destination_id = a_destination(destinations)
    uploads.create_pairs([media_id], [destination_id])
    db.execute(
        "UPDATE upload_record SET invalidated_at = '2026-08-17T00:00:00+00:00',"
        " invalidated_reason = 'group changed'"
    )
    pairs = uploads.create_pairs([media_id], [destination_id])
    assert pairs[0].result == "rejected"
    assert "無効" in pairs[0].reason


def test_pairs_for_two_destinations_are_independent(db, profile, destinations, uploads):
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    home = a_destination(destinations, "home")
    family = a_destination(destinations, "family")
    uploads.create_pairs([media_id], [home])
    db.execute("UPDATE upload_record SET state = 'complete', destination_revision_id = ?",
               (destinations.current(home)["id"],))

    pairs = uploads.create_pairs([media_id], [home, family])

    assert results_of(pairs)[(media_id, home)] == "already_complete"
    assert results_of(pairs)[(media_id, family)] == "created"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_upload_pairs.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.db.uploads'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/db/uploads.py`:

```python
"""アップロードの pair と状態遷移（§8 / §9.10 / §10）.

`POST /uploads` は `media_ids × destination_ids` の直積を pair 単位の作業項目へ
展開する。**先に一括で検証し、落ちたら何も作らない。** 作成は 1 トランザクション
で行い、実行・失敗・再試行は pair ごとに独立させる。

`selection_rule` は**選択を許可した根拠**で、作成時に決まって以後は変わらない。
再試行は根拠を変えない（`failed` → `pending` の CAS だけ）。上書きすると
「なぜ最初に送信を許可したか」が失われ、claim が安全条件しか見なくなる。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from ..clock import now_iso
from ..ids import new_id
from .connection import immediate
from .destinations import DestinationRepository
from .jobs import JobContext
from .profiles import ProfileRegistry
from .selection import group_is_current

RESULTS = frozenset(
    {"created", "retry_queued", "already_complete", "already_active", "awaiting_approval",
     "rejected"}
)

ACTIVE_STATES = ("checking", "uploading", "asset_known", "tagging", "fixing_datetime")
CLAIMABLE_STATES = ("pending", "needs_recheck")


class UploadRequestInvalid(ValueError):
    """要求そのものが成立しない. **何も作らずに全体を拒否する。**"""


@dataclass(frozen=True)
class PairResult:
    media_file_id: str
    destination_id: str
    result: str
    record_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _Choice:
    """その pair を許可する根拠. 許可できなければ `reason` だけが入る."""

    selection_rule: str | None
    merge_group_id: str | None
    eligibility_reason: str
    adopt_group_id: str | None = None


class UploadRepository:
    def __init__(
        self,
        conn: sqlite3.Connection,
        registry: ProfileRegistry,
        destinations: DestinationRepository,
    ) -> None:
        self._conn = conn
        self._registry = registry
        self._destinations = destinations

    def create_pairs(
        self, media_ids: Sequence[str], destination_ids: Sequence[str]
    ) -> list[PairResult]:
        media = self._load_media(media_ids)
        revisions = self._load_destinations(destination_ids)

        results: list[PairResult] = []
        with immediate(self._conn):
            for media_id in media_ids:
                choice = self._choose(media[media_id])
                for destination_id in destination_ids:
                    results.append(
                        self._pair(media[media_id], revisions[destination_id], choice)
                    )
        return results

    # ------------------------------------------------------------------
    def _load_media(self, media_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        if not media_ids:
            raise UploadRequestInvalid("メディアが 1 件も指定されていない")
        marks = ", ".join("?" * len(media_ids))
        rows = {
            row["id"]: row
            for row in self._conn.execute(
                f"SELECT * FROM media_file WHERE id IN ({marks})",  # noqa: S608
                list(media_ids),
            )
        }
        missing = [media_id for media_id in media_ids if media_id not in rows]
        if missing:
            raise UploadRequestInvalid(f"知らないメディア: {', '.join(missing)}")
        return rows

    def _load_destinations(self, destination_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        if not destination_ids:
            raise UploadRequestInvalid("宛先が 1 件も指定されていない")
        revisions: dict[str, sqlite3.Row] = {}
        for destination_id in destination_ids:
            row = self._destinations.get(destination_id)
            if row is None:
                raise UploadRequestInvalid(f"知らない宛先: {destination_id}")
            if row["archived_at"] is not None:
                raise UploadRequestInvalid(f"宛先「{row['name']}」は保管済み")
            if not row["enabled"]:
                raise UploadRequestInvalid(f"宛先「{row['name']}」は無効になっている")
            if row["current_revision_id"] is None:
                raise UploadRequestInvalid(f"宛先「{row['name']}」は接続を検証していない")
            revisions[destination_id] = self._destinations.current(destination_id)
        return revisions

    def _choose(self, media: sqlite3.Row) -> _Choice:
        """§10 (b)(c) のどの根拠で選べるかを決める."""
        if media["missing_at"] is not None:
            return _Choice(None, None, "ファイルが見つからない")
        if media["role"] == "derived":
            return self._choose_derived(media)
        return self._choose_original(media)

    def _choose_original(self, media: sqlite3.Row) -> _Choice:
        member = self._conn.execute(
            "SELECT g.id AS group_id, g.status AS status FROM merge_member mm"
            " JOIN merge_group g ON g.id = mm.merge_group_id"
            " WHERE mm.media_file_id = ? AND mm.active = 1",
            (media["id"],),
        ).fetchone()
        if member is None:
            return _Choice("default", None, "結合グループに属さないオリジナル")
        if member["status"] in ("failed", "skipped"):
            return _Choice(
                "failed_group_member",
                member["group_id"],
                f"結合できなかったグループ（{member['status']}）の構成ファイル",
            )
        return _Choice(None, None, f"アクティブな結合グループの構成ファイル（{member['status']}）")

    def _choose_derived(self, media: sqlite3.Row) -> _Choice:
        group = self._conn.execute(
            "SELECT * FROM merge_group WHERE output_media_file_id = ?", (media["id"],)
        ).fetchone()
        if group is None:
            return _Choice(None, None, "生成元のグループが分からない派生物")
        if group["status"] != "merged":
            return _Choice(None, None, f"グループが {group['status']} のまま")
        if not group_is_current(self._conn, self._registry, group["id"], media["id"]):
            return _Choice(None, None, "生成元のグループが現在の構成と一致しない")
        if group["adopted_at"] is not None or _passed(group["verification_json"]):
            return _Choice("default", group["id"], "検証に合格した（または採用済みの）結合物")
        # **選ぶ操作が採用そのもの。** 別操作にすると、作った瞬間に
        # 「まだ採用していない」という条件を自分自身が満たさなくなる。
        return _Choice(
            "adopted_derived",
            group["id"],
            "検証不合格の結合物を、中身を確認した上で採用した",
            adopt_group_id=group["id"],
        )

    def _pair(
        self, media: sqlite3.Row, revision: sqlite3.Row, choice: _Choice
    ) -> PairResult:
        destination_id = revision["destination_id"]
        existing = self._conn.execute(
            "SELECT * FROM upload_record WHERE destination_id = ? AND target_epoch = ?"
            "   AND media_file_id = ?",
            (destination_id, revision["target_epoch"], media["id"]),
        ).fetchone()
        if existing is not None:
            return self._existing(media, destination_id, existing)
        if choice.selection_rule is None:
            return PairResult(
                media["id"], destination_id, "rejected", reason=choice.eligibility_reason
            )
        if choice.adopt_group_id is not None:
            self._conn.execute(
                "UPDATE merge_group SET adopted_at = COALESCE(adopted_at, ?), updated_at = ?"
                " WHERE id = ?",
                (now_iso(), now_iso(), choice.adopt_group_id),
            )
        record_id = new_id()
        self._conn.execute(
            "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
            " selection_rule, origin, eligibility_reason, merge_group_id, checksum,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?, 'unknown', ?, ?, ?, ?, ?)",
            (
                record_id,
                destination_id,
                revision["target_epoch"],
                media["id"],
                choice.selection_rule,
                choice.eligibility_reason,
                choice.merge_group_id,
                media["sha1"],
                now_iso(),
                now_iso(),
            ),
        )
        return PairResult(media["id"], destination_id, "created", record_id=record_id)

    def _existing(
        self, media: sqlite3.Row, destination_id: str, row: sqlite3.Row
    ) -> PairResult:
        """§10「既存レコードがある場合の遷移」."""
        if row["invalidated_at"] is not None:
            return PairResult(
                media["id"], destination_id, "rejected", row["id"],
                f"無効化されている: {row['invalidated_reason']}",
            )
        if row["state"] == "complete":
            return PairResult(media["id"], destination_id, "already_complete", row["id"])
        if row["state"] in ACTIVE_STATES:
            return PairResult(media["id"], destination_id, "already_active", row["id"])
        if row["state"] == "awaiting_datetime_approval":
            return PairResult(media["id"], destination_id, "awaiting_approval", row["id"])
        if row["state"] == "failed":
            self._conn.execute(
                "UPDATE upload_record SET state = 'pending', claim_job_id = NULL,"
                " claim_token = NULL, claim_expires_at = NULL, updated_at = ?"
                " WHERE id = ? AND state = 'failed'",
                (now_iso(), row["id"]),
            )
            return PairResult(media["id"], destination_id, "retry_queued", row["id"])
        # pending / needs_recheck は既に claim できる状態。二重に作らない。
        return PairResult(media["id"], destination_id, "created", row["id"])


def _passed(verification_json: str | None) -> bool:
    """検証の合否. `passed` が真の bool のときだけ合格（§10）."""
    import json

    if verification_json is None:
        return False
    try:
        return json.loads(verification_json).get("passed") is True
    except (AttributeError, TypeError, ValueError):
        return False
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_upload_pairs.py -q`
Expected: PASS（16 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `_load_media` の実在確認を消す | `test_an_unknown_media_id_rejects_the_whole_request` |
| `enabled` の確認を消す | `test_a_disabled_destination_rejects_the_whole_request` |
| `archived_at` の確認を消す | `test_an_archived_destination_rejects_the_whole_request` |
| `missing_at` の確認を消す | `test_a_missing_file_is_rejected_per_pair` |
| 1 件の拒否で全体を落とす（例外にする） | 同上（`present` が作られない） |
| `failed` / `skipped` の分岐を消す | `test_a_member_of_a_failed_group_is_allowed_with_its_rule` |
| アクティブな member を `default` で通す | `test_a_member_of_an_active_group_is_rejected` |
| `group_is_current` の呼び出しを消す | `test_a_derived_from_a_stale_group_is_rejected` |
| `adopt_group_id` の UPDATE を消す | `test_choosing_an_unadopted_derived_adopts_it` |
| `selection_rule` を常に `default` にする | 同上と `test_a_member_of_a_failed_group_is_allowed_with_its_rule` |
| `already_complete` を `created` にする | `test_a_complete_record_is_a_no_op` |
| `ACTIVE_STATES` の判定を消す | `test_an_active_record_is_not_claimed_twice` |
| `awaiting_datetime_approval` の分岐を消す | `test_a_waiting_record_is_left_to_the_approval_flow` |
| `failed` の再投入で `attempts` を 0 に戻す | `test_a_failed_record_is_queued_again_without_changing_its_rule` |
| `invalidated_at` の判定を消す | `test_an_invalidated_record_is_not_reused` |
| `target_epoch` を常に 1 にする | **落ちない**。テストのデータはすべて epoch 1。**epoch を進めた宛先のケースを Task 8 で足す**（claim 側で epoch 不一致を見る） |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/db/uploads.py app/tests/test_upload_pairs.py
git commit -m "feat(mediaferry): expand upload requests into per pair work items"
```

---

### Task 8: claim と状態遷移（CAS）

**Files:**
- Modify: `app/src/mediaferry/db/uploads.py`
- Test: `app/tests/test_upload_claim.py`

**Interfaces:**
- Produces（`UploadRepository` に追加）:
  - `.claim_next(destination_id: str, job_id: str, token: str, lease_seconds: int = 60) -> sqlite3.Row | None`
  - `.check_eligibility(row: sqlite3.Row) -> str | None`（満たさない理由。満たすなら `None`）
  - `.prepare_side_effect(ctx: JobContext, record_id: str, expect_state: str, lease_seconds: int = 60) -> None`
  - `.extend_claim(record_id: str, token: str, lease_seconds: int = 60) -> None`
  - `.advance_owned(ctx: JobContext, record_id: str, state: str, expect_state: str, **fields) -> None`
  - `.finish_owned(ctx: JobContext, record_id: str, state: str, expect_state: str, **fields) -> None`
  - `.advance(record_id: str, token: str, state: str, expect_state: str, **fields: object) -> None`
  - `.finish(record_id: str, token: str, state: str, expect_state: str, **fields: object) -> None`
  - `.release_to(record_id: str, token: str, state: str, **fields: object) -> None`
  - `.refuse(record_id: str, token: str, reason: str) -> None`
  - `.invalidate_for_group(group_id: str, reason: str) -> int`
  - `.get(record_id) -> sqlite3.Row | None` / `.list_records(destination_id=None, state=None, limit=200) -> list[sqlite3.Row]`
  - `ClaimLost(RuntimeError)`

**SQLite に行ロックは無い**（§8）。`BEGIN IMMEDIATE` の中の条件付き UPDATE で
所有権を取る。**外部への副作用の直前と、その結果を commit する時点で、
`claim_token` とジョブのリースの両方を再確認する**（§8）。

`claim_token` の一致だけでは足りない。3 つの理由がある。

| 見ないと起きること | 対策 |
| --- | --- |
| `JobStore.extend_lease` は `cancelling` でもリースを延ばす。利用者がキャンセルを押した後も 28 GiB の送信が完走し、タグと日時まで変更される | 副作用の直前に `ctx.assert_lease()`（`cancelling` を通さない）を **同じ `BEGIN IMMEDIATE` の中で**呼ぶ |
| claim の期限が切れた後も、古い worker が同じ token で書き込める | 条件に `claim_expires_at > now` を入れる |
| グループの supersede で無効化された後も進める | 条件に `invalidated_at IS NULL` を入れる |

**`prepare_side_effect` を通ってから HTTP を呼ぶ。** 戻ってきた結果を commit する
ときは **`advance_owned` / `finish_owned`**（`ctx` を取り、同じ
`BEGIN IMMEDIATE` の中で `ctx.assert_lease()` も行う）を使う。

**commit 側で claim だけを見ては足りない。** HTTP を待っている間にキャンセルが
commit されると、claim は生きたままなので `asset_known` や `complete` を書けて
しまい、**画面はキャンセル済みなのにタグと日時まで進む**。`advance` /
`finish`（`ctx` を取らない版）は、リースをもう見られない後始末の経路
（`_release_unknown` など）だけで使う。

**claim してから (a)(c) を評価する。** 評価を CAS の中に書けない（`selection_rule`
ごとに別のテーブルを見る）ので、取ってから確かめ、満たさなければ `refuse` で
無効化して次へ進む。無効化は §10 の「多重防御」と同じ扱いにする。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_upload_claim.py`:

```python
import os

import pytest

from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import ClaimLost, UploadRepository

from .test_schema_artifacts import a_media_file
from .test_selection import a_derived, a_group, a_pair

IDENTITY = RemoteIdentity(remote_user_id="user-a", server_instance_id=None)


@pytest.fixture
def world(db):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home", base_url="http://immich.invalid:2283", public_url=None,
        secret="key-1", identity=IDENTITY,
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    return profile, destinations, destination_id, uploads


def a_pending(db, world, **over):
    profile, _, destination_id, uploads = world
    media_id = a_media_file(
        db, (profile.profile_id, profile.revision_id), **over
    )
    uploads.create_pairs([media_id], [destination_id])
    return db.execute("SELECT * FROM upload_record WHERE media_file_id = ?", (media_id,)).fetchone()


def test_claiming_takes_ownership_and_records_the_revision(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    revision = destinations.current(destination_id)

    row = uploads.claim_next(destination_id, job_id="job-1", token="tok-1")

    assert row["state"] == "checking"
    assert row["claim_job_id"] == "job-1"
    assert row["destination_revision_id"] == revision["id"]
    assert row["claim_expires_at"] is not None


def test_a_second_claim_gets_nothing(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    revision = destinations.current(destination_id)
    assert uploads.claim_next(destination_id, "job-1", "tok-1") is not None
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is None


def test_an_abandoned_claim_is_recovered_by_the_reconciler_not_by_a_takeover(db, world):
    """**期限切れの横取りは起こらない契約にする。**

    `0004` の CHECK は「claim の 3 欄はすべて NULL かすべて非 NULL」かつ
    「進行中の状態なら claim を持つ」と定めている。つまり `pending` /
    `needs_recheck` の行に期限だけを残すことはできず、進行中の行は
    `claim_next` の対象外。**放置された claim を回収するのは起動時の
    reconciliation だけ**（Task 12）。`claim_next` の
    `claim_expires_at < now` は §8 の SQL をそのまま写した保険で、
    この CHECK が生きている限り到達しない。
    """
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    uploads.claim_next(destination_id, "job-1", "tok-1")
    # 期限を過去にしても、進行中の行は claim_next の対象にならない。
    db.execute(
        "UPDATE upload_record SET claim_expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", record["id"]),
    )
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is None

    assert uploads.release_interrupted() == 1
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is not None


def test_a_record_from_another_epoch_is_not_claimed(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    # 宛先を別アカウントへ向け替える（epoch が進む）。
    destinations.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2", identity=RemoteIdentity(remote_user_id="user-b", server_instance_id=None),
    )
    assert uploads.claim_next(destination_id, "job-1", "tok-1") is None


def test_an_invalidated_record_is_not_claimed(db, world):
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'x' WHERE id = ?",
        ("2026-08-17T00:00:00+00:00", record["id"]),
    )
    assert uploads.claim_next(destination_id, "job-1", "tok-1") is None


def test_needs_recheck_is_claimable(db, world):
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute("UPDATE upload_record SET state = 'needs_recheck' WHERE id = ?", (record["id"],))
    assert uploads.claim_next(destination_id, "job-1", "tok-1") is not None


def test_a_missing_file_fails_the_eligibility_check(db, world):
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute(
        "UPDATE media_file SET missing_at = ? WHERE id = ?",
        ("2026-08-17T00:00:00+00:00", record["media_file_id"]),
    )
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    assert uploads.check_eligibility(claimed) is not None


def test_a_group_that_stopped_failing_invalidates_its_member(db, world):
    """`failed_group_member` の根拠は claim 時に「今も」成立している必要がある."""
    profile, destinations, destination_id, uploads = world
    members = a_pair(db, profile)
    group_id = a_group(db, profile, members, status="failed", verification=None)
    uploads.create_pairs([members[0][0]], [destination_id])
    db.execute("UPDATE merge_group SET status = 'merged' WHERE id = ?", (group_id,))

    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    assert uploads.check_eligibility(claimed) is not None


def test_an_adopted_derived_stays_eligible(db, world):
    profile, destinations, destination_id, uploads = world
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id,
            verification='{"passed": false}')
    uploads.create_pairs([output_id], [destination_id])

    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    assert uploads.check_eligibility(claimed) is None


def test_a_disabled_destination_fails_the_eligibility_check(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    revision = destinations.current(destination_id)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    destinations.set_enabled(destination_id, False)
    assert uploads.check_eligibility(claimed) is not None


def test_refusing_invalidates_and_releases(db, world):
    _, destinations, destination_id, uploads = world
    claimed = None
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.refuse(claimed["id"], "tok-1", "生成元のグループが変わった")

    row = uploads.get(claimed["id"])
    assert row["invalidated_at"] is not None
    assert row["invalidated_reason"] == "生成元のグループが変わった"
    assert row["claim_job_id"] is None


def test_refusing_survives_the_state_check(db, world):
    """`refuse` は state も pending へ戻す. 進行中のまま claim を外せない."""
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.refuse(claimed["id"], "tok-1", "ファイルが見つからない")

    row = uploads.get(claimed["id"])
    assert row["state"] == "pending"
    assert row["invalidated_at"] is not None
    # 無効化されているので、次の claim では拾われない。
    assert uploads.claim_next(destination_id, "job-2", "tok-2") is None


def test_a_cancelled_job_cannot_perform_a_side_effect(db, world):
    """`extend_lease` は cancelling でも延ばす. `assert_lease` は通さない."""
    from mediaferry.db.jobs import JobStore

    _, destinations, destination_id, uploads = world
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    with pytest.raises(Exception):  # LeaseLost（assert_lease が通さない）
        uploads.prepare_side_effect(ctx, claimed["id"], "checking")


def test_an_invalidated_record_cannot_perform_a_side_effect(db, world):
    from mediaferry.db.jobs import JobStore

    _, destinations, destination_id, uploads = world
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'グループが変わった'"
        " WHERE id = ?",
        ("2026-08-17T00:00:00+00:00", claimed["id"]),
    )

    with pytest.raises(ClaimLost):
        uploads.prepare_side_effect(ctx, claimed["id"], "checking")


def test_an_expired_claim_cannot_commit_a_side_effect(db, world):
    from mediaferry.db.jobs import JobStore

    _, destinations, destination_id, uploads = world
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
    db.execute(
        "UPDATE upload_record SET claim_expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", claimed["id"]),
    )

    # 送信は終わったが、書き戻す資格はもう無い。
    with pytest.raises(ClaimLost):
        uploads.advance(
            claimed["id"], ctx.lease_token, "asset_known", expect_state="checking",
            remote_asset_id="asset-1",
        )


def test_a_state_that_moved_under_us_stops_the_commit(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    uploads.advance(claimed["id"], "tok-1", "uploading", expect_state="checking")

    # 期待した状態ではない（誰かが動かした）。
    with pytest.raises(ClaimLost):
        uploads.advance(claimed["id"], "tok-1", "asset_known", expect_state="checking")


def test_advancing_keeps_the_claim(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.advance(claimed["id"], "tok-1", "uploading", expect_state="checking")

    row = uploads.get(claimed["id"])
    assert row["state"] == "uploading"
    assert row["claim_job_id"] == "job-1"


def test_a_stale_token_cannot_move_the_record(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")
    with pytest.raises(ClaimLost):
        uploads.advance(claimed["id"], "tok-old", "uploading", expect_state="checking")
    assert uploads.get(claimed["id"])["state"] == "checking"


def test_finishing_clears_the_claim(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.advance(
        claimed["id"], "tok-1", "asset_known", expect_state="checking", remote_asset_id="asset-1"
    )
    uploads.finish(claimed["id"], "tok-1", "complete", expect_state="asset_known")

    row = uploads.get(claimed["id"])
    assert row["state"] == "complete"
    assert (row["claim_job_id"], row["claim_token"], row["claim_expires_at"]) == (None, None, None)
    # どの設定へ送ったかは残す。
    assert row["destination_revision_id"] is not None


def test_releasing_puts_it_back_for_a_recheck(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1")

    uploads.release_to(claimed["id"], "tok-1", "needs_recheck")

    row = uploads.get(claimed["id"])
    assert row["state"] == "needs_recheck"
    assert row["claim_job_id"] is None


def test_the_claim_can_be_extended(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    claimed = uploads.claim_next(destination_id, "job-1", "tok-1", 1)
    before = uploads.get(claimed["id"])["claim_expires_at"]

    uploads.extend_claim(claimed["id"], "tok-1", 3600)

    assert uploads.get(claimed["id"])["claim_expires_at"] > before


def test_invalidating_a_group_hits_only_unfinished_records(db, world):
    profile, destinations, destination_id, uploads = world
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    uploads.create_pairs([output_id], [destination_id])
    done = a_pending(db, world, rel_path="library/dji-osmo/DCIM/DONE.MP4")
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ? WHERE id = ?",
        (destinations.current(destination_id)["id"], done["id"]),
    )

    assert uploads.invalidate_for_group(group_id, "グループが変わった") == 1

    assert uploads.get(done["id"])["invalidated_at"] is None
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_upload_claim.py -q`
Expected: FAIL（`ImportError: cannot import name 'ClaimLost'`）

- [ ] **Step 3: 最小実装**

`db/uploads.py` に追記する。

```python
class ClaimLost(RuntimeError):
    """自分の claim_token では、その行を動かせない.

    キャンセルされた古いジョブが、新しいジョブの状態を上書きするのを防ぐ。
    """


# 進行中に置ける状態と、claim を外す状態（`0004` の CHECK と一致させる）。
TERMINAL_STATES = ("complete", "failed", "awaiting_datetime_approval")
RELEASED_STATES = ("pending", "needs_recheck")
```

`UploadRepository` のメソッド:

```python
    def claim_next(
        self,
        destination_id: str,
        job_id: str,
        token: str,
        lease_seconds: int = 60,
    ) -> sqlite3.Row | None:
        """CAS で 1 件だけ所有権を取る. 取れなければ None.

        **`SELECT ... FOR UPDATE` は無い。** 更新できた 1 ジョブだけが実行者になる。

        **現行リビジョンは pair ごとに、同じトランザクションの中で解決する**（§8）。
        ジョブの開始時に 1 回だけ読んで固定すると、途中で API キーを変えた場合に
        未 claim の pair まで旧リビジョンで送る（旧 credential は purge されて
        いるかもしれない）。epoch が進んでいれば、そもそも対象から外れる。
        """
        marks = ", ".join("?" * len(CLAIMABLE_STATES))
        with immediate(self._conn):
            revision = self._conn.execute(
                "SELECT r.* FROM upload_destination d"
                " JOIN destination_revision r ON r.id = d.current_revision_id"
                " WHERE d.id = ? AND d.enabled = 1 AND d.archived_at IS NULL",
                (destination_id,),
            ).fetchone()
            if revision is None:
                return None
            row = self._conn.execute(
                "SELECT id FROM upload_record"
                " WHERE destination_id = ? AND target_epoch = ? AND invalidated_at IS NULL"
                f"   AND state IN ({marks})"  # noqa: S608
                "   AND (claim_expires_at IS NULL OR claim_expires_at < ?)"
                " ORDER BY created_at LIMIT 1",
                (
                    destination_id,
                    revision["target_epoch"],
                    *CLAIMABLE_STATES,
                    now_iso(),
                ),
            ).fetchone()
            if row is None:
                return None
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'checking', claim_job_id = ?,"
                " claim_token = ?, claim_expires_at = ?, destination_revision_id = ?,"
                " updated_at = ?"
                f" WHERE id = ? AND invalidated_at IS NULL AND state IN ({marks})",  # noqa: S608
                (
                    job_id,
                    token,
                    _expiry(lease_seconds),
                    revision["id"],
                    now_iso(),
                    row["id"],
                    *CLAIMABLE_STATES,
                ),
            )
            if updated.rowcount != 1:
                return None
            return self._conn.execute(
                "SELECT * FROM upload_record WHERE id = ?", (row["id"],)
            ).fetchone()

    def check_eligibility(self, row: sqlite3.Row) -> str | None:
        """§10 (a) と、`selection_rule` に対応する (c) を**今の状態で**評価する.

        claim 時に評価するのは「選べる場面」ではなく「その根拠が今も成立して
        いるか」。混同すると、採用した瞬間に自分自身が条件を満たさなくなる。
        """
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (row["media_file_id"],)
        ).fetchone()
        if media is None or media["missing_at"] is not None:
            return "ファイルが見つからない"
        if row["invalidated_at"] is not None:
            return f"無効化されている: {row['invalidated_reason']}"
        destination = self._destinations.get(row["destination_id"])
        if destination is None or destination["archived_at"] is not None:
            return "宛先が保管済み"
        if not destination["enabled"]:
            return "宛先が無効になっている"
        revision = self._destinations.revision(row["destination_revision_id"])
        if revision["target_epoch"] != row["target_epoch"]:
            return "宛先の向き先が変わっている"

        if media["role"] == "derived" and not group_is_current(
            self._conn, self._registry, row["merge_group_id"] or "", media["id"]
        ):
            return "生成元のグループが現在の構成と一致しない"
        return self._check_rule(row, media)

    def _check_rule(self, row: sqlite3.Row, media: sqlite3.Row) -> str | None:
        rule = row["selection_rule"]
        if rule == "failed_group_member":
            member = self._conn.execute(
                "SELECT g.status AS status FROM merge_member mm"
                " JOIN merge_group g ON g.id = mm.merge_group_id"
                " WHERE mm.media_file_id = ? AND mm.active = 1 AND g.id = ?",
                (media["id"], row["merge_group_id"]),
            ).fetchone()
            if member is None or member["status"] not in ("failed", "skipped"):
                return "結合できなかったグループの構成ファイル、という根拠が成立しない"
            return None
        if rule == "adopted_derived":
            adopted = self._conn.execute(
                "SELECT adopted_at FROM merge_group WHERE id = ?", (row["merge_group_id"],)
            ).fetchone()
            if adopted is None or adopted["adopted_at"] is None:
                return "採用の記録が無い"
            return None
        # default は (b) を満たすこと。derived の条件は上で見たので、
        # ここではアクティブなグループの member でないことを確かめる。
        if media["role"] == "original":
            member = self._conn.execute(
                "SELECT g.status AS status FROM merge_member mm"
                " JOIN merge_group g ON g.id = mm.merge_group_id"
                " WHERE mm.media_file_id = ? AND mm.active = 1",
                (media["id"],),
            ).fetchone()
            if member is not None and member["status"] not in ("failed", "skipped"):
                return "アクティブな結合グループの構成ファイルになっている"
        return None

    def prepare_side_effect(
        self, ctx: JobContext, record_id: str, expect_state: str, lease_seconds: int = 60
    ) -> None:
        """外部への副作用の直前に呼ぶ（§8）.

        **リースと claim を 1 つの `BEGIN IMMEDIATE` の中で確かめる。** 分けると
        その隙間にキャンセルが commit でき、「キャンセル済みと表示した後に
        送信・タグ付与・日時変更が行われる」経路が残る。`ctx.assert_lease()` は
        `cancelling` を通さない（`extend_lease` は通すので、これが必要）。
        """
        with immediate(self._conn):
            ctx.assert_lease()
            updated = self._conn.execute(
                "UPDATE upload_record SET claim_expires_at = ?, updated_at = ?"
                " WHERE id = ? AND claim_token = ? AND state = ? AND invalidated_at IS NULL"
                "   AND claim_expires_at > ?",
                (
                    _expiry(lease_seconds),
                    now_iso(),
                    record_id,
                    ctx.lease_token,
                    expect_state,
                    now_iso(),
                ),
            )
            if updated.rowcount != 1:
                raise ClaimLost(f"レコード {record_id} の所有権を失っている")

    def extend_claim(self, record_id: str, token: str, lease_seconds: int = 60) -> None:
        self._cas(record_id, token, "claim_expires_at = ?", (_expiry(lease_seconds),), strict=True)

    def advance_owned(
        self, ctx: JobContext, record_id: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """外部副作用の結果を commit する. **リースも同じ取引の中で確かめる。**"""
        with immediate(self._conn):
            ctx.assert_lease()
            self._locked_cas(
                record_id, ctx.lease_token, "state = ?", (state,), expect_state, **fields
            )

    def finish_owned(
        self, ctx: JobContext, record_id: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """終端へ倒して claim を外す. **リースも同じ取引の中で確かめる。**"""
        if state not in TERMINAL_STATES:
            raise ValueError(f"終端ではない状態: {state}")
        with immediate(self._conn):
            ctx.assert_lease()
            self._locked_cas(
                record_id,
                ctx.lease_token,
                "state = ?, claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
                (state,),
                expect_state,
                **fields,
            )

    def advance(
        self, record_id: str, token: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """進行中の状態へ進める. claim は保ったまま.

        **`expect_state` を必ず渡す。** 外部副作用の結果を commit する時点でも、
        自分が期待した状態のままであることを確かめる（§8）。
        """
        self._cas(
            record_id, token, "state = ?", (state,), strict=True, expect_state=expect_state,
            **fields,
        )

    def finish(
        self, record_id: str, token: str, state: str, expect_state: str, **fields: object
    ) -> None:
        """終端（complete / failed / awaiting）へ倒し、claim を外す.

        **未来の期限を残したまま終端にしない。** 残ると、明示操作しても期限まで
        claim できなくなる（§8）。
        """
        if state not in TERMINAL_STATES:
            raise ValueError(f"終端ではない状態: {state}")
        self._cas(
            record_id,
            token,
            "state = ?, claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
            (state,),
            strict=True,
            expect_state=expect_state,
            **fields,
        )

    def release_to(self, record_id: str, token: str, state: str, **fields: object) -> None:
        """再び claim できる状態へ戻し、claim を外す."""
        if state not in RELEASED_STATES:
            raise ValueError(f"claim できる状態ではない: {state}")
        self._cas(
            record_id,
            token,
            "state = ?, claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
            (state,),
            **fields,
        )

    def refuse(self, record_id: str, token: str, reason: str) -> None:
        """claim してから条件を満たさないと分かった行を無効化する（§10 の多重防御）.

        **`state` も `pending` へ戻す。** `0004` の CHECK が「進行中の状態なら
        claim を持つ」と定めているので、`checking` のまま claim を外すと
        `IntegrityError` になる。無効化された行は claim の条件
        （`invalidated_at IS NULL`）で弾かれるので、`pending` に戻しても
        拾われない。
        """
        self._cas(
            record_id,
            token,
            "state = 'pending', invalidated_at = ?, invalidated_reason = ?,"
            " claim_job_id = NULL, claim_token = NULL, claim_expires_at = NULL",
            (now_iso(), reason),
        )

    def invalidate_for_group(self, group_id: str, reason: str) -> int:
        """グループが変わったときに、未完了のレコードをまとめて無効化する."""
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?,"
                " updated_at = ? WHERE merge_group_id = ? AND invalidated_at IS NULL"
                "   AND state <> 'complete'",
                (now_iso(), reason, now_iso(), group_id),
            )
            return updated.rowcount

    def get(self, record_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM upload_record WHERE id = ?", (record_id,)
        ).fetchone()

    def list_records(
        self, destination_id: str | None = None, state: str | None = None, limit: int = 200
    ) -> list[sqlite3.Row]:
        clauses, params = [], []
        if destination_id is not None:
            clauses.append("destination_id = ?")
            params.append(destination_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return list(
            self._conn.execute(
                f"SELECT * FROM upload_record{where} ORDER BY updated_at DESC LIMIT ?",  # noqa: S608
                (*params, limit),
            )
        )

    def _cas(
        self,
        record_id: str,
        token: str,
        assignment: str,
        params: tuple,
        strict: bool = False,
        expect_state: str | None = None,
        **fields: object,
    ) -> None:
        """`claim_token` が自分のものである行だけを動かす.

        `strict` を立てると、期限切れと無効化も条件に入れる。**外部副作用の
        結果を書く経路では必ず立てる。** 逆に、降りるための解放
        （`release_to` / `refuse`）では立てない —— 期限が切れていても、
        自分が持っていた行は片付けられる必要がある。
        """
        extra = "".join(f", {name} = ?" for name in fields)
        with immediate(self._conn):
            self._locked_cas(
                record_id, token, assignment, params, expect_state, strict=strict, **fields
            )

    def _locked_cas(
        self,
        record_id: str,
        token: str,
        assignment: str,
        params: tuple,
        expect_state: str | None,
        strict: bool = True,
        **fields: object,
    ) -> None:
        """**呼び出し側が開いたトランザクションの中で使う。** 条件は `_cas` と同じ."""
        extra = "".join(f", {name} = ?" for name in fields)
        clauses = ["id = ?", "claim_token = ?"]
        guard: list[object] = [record_id, token]
        if strict:
            clauses += ["claim_expires_at > ?", "invalidated_at IS NULL"]
            guard.append(now_iso())
        if expect_state is not None:
            clauses.append("state = ?")
            guard.append(expect_state)
        updated = self._conn.execute(
            f"UPDATE upload_record SET {assignment}{extra}, updated_at = ?"  # noqa: S608
            f" WHERE {' AND '.join(clauses)}",
            (*params, *fields.values(), now_iso(), *guard),
        )
        if updated.rowcount != 1:
            raise ClaimLost(f"レコード {record_id} の claim を失っている")
```

ファイル冒頭に足すもの:

```python
from datetime import timedelta

from ..clock import iso, utcnow


def _expiry(seconds: int) -> str:
    return iso(utcnow() + timedelta(seconds=seconds))
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_upload_claim.py -q`
Expected: PASS（17 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `claim_next` の CAS を無条件 UPDATE にする | `test_a_second_claim_gets_nothing` |
| `claim_expires_at < ?` の判定を消す | **落ちない**。`0004` の CHECK により、claim を持つ行は必ず進行中の状態で、`claim_next` の対象外。**この条件は §8 の SQL を写した到達不能な保険**（`test_an_abandoned_claim_is_recovered_by_the_reconciler_not_by_a_takeover` が契約として固定している）。検出できない変異として記録する |
| `refuse` が `state` を戻さない（`checking` のまま） | `test_refusing_survives_the_state_check`（`0004` の CHECK で `IntegrityError`） |
| `prepare_side_effect` の `ctx.assert_lease()` を消す | `test_a_cancelled_job_cannot_perform_a_side_effect` |
| `prepare_side_effect` の `invalidated_at IS NULL` を消す | `test_an_invalidated_record_cannot_perform_a_side_effect` |
| `_cas` の `strict` を常に偽にする | `test_an_expired_claim_cannot_commit_a_side_effect` |
| `advance` / `finish` の `expect_state` を無視する | `test_a_state_that_moved_under_us_stops_the_commit` |
| `claim_next` が現行リビジョンを引かずに引数の revision を使う | `test_a_record_from_another_epoch_is_not_claimed`（宛先を向け替えた後に旧 epoch を拾う） |
| `target_epoch` の条件を消す | `test_a_record_from_another_epoch_is_not_claimed` |
| `invalidated_at IS NULL` を消す | `test_an_invalidated_record_is_not_claimed` |
| `CLAIMABLE_STATES` から `needs_recheck` を外す | `test_needs_recheck_is_claimable` |
| `destination_revision_id` を記録しない | `test_claiming_takes_ownership_and_records_the_revision`（CHECK 制約でも落ちる） |
| `check_eligibility` の `missing_at` を消す | `test_a_missing_file_fails_the_eligibility_check` |
| `enabled` の判定を消す | `test_a_disabled_destination_fails_the_eligibility_check` |
| `_check_rule` の `failed_group_member` の再評価を消す | `test_a_group_that_stopped_failing_invalidates_its_member` |
| `adopted_derived` の条件を「まだ採用していない」にする | `test_an_adopted_derived_stays_eligible`（**前版の欠陥。必ず拒否される**） |
| `_cas` の `claim_token = ?` を消す | `test_a_stale_token_cannot_move_the_record` |
| `finish` で claim を消さない | `test_finishing_clears_the_claim`（`0004` の CHECK でも落ちる） |
| `release_to` で claim を消さない | `test_releasing_puts_it_back_for_a_recheck` |
| `extend_claim` を何もしない実装にする | `test_the_claim_can_be_extended` |
| `invalidate_for_group` の `state <> 'complete'` を消す | `test_invalidating_a_group_hits_only_unfinished_records` |
| `refuse` が `invalidated_reason` を書かない | `test_refusing_invalidates_and_releases` |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/db/uploads.py app/tests/test_upload_claim.py
git commit -m "feat(mediaferry): claim upload records with a conditional update"
```

---

### Task 9: アップロードの状態機械

**Files:**
- Create: `app/src/mediaferry/core/uploads/__init__.py`
- Create: `app/src/mediaferry/core/uploads/decisions.py`
- Create: `app/src/mediaferry/core/lease_pulse.py`（`adapters/publisher.py` から移設）
- Modify: `app/src/mediaferry/adapters/publisher.py`
- Create: `app/src/mediaferry/jobs/uploader.py`
- Test: `app/tests/test_upload_decisions.py`
- Test: `app/tests/test_uploader.py`

**Interfaces:**
- Consumes: `ImmichClient`（Task 4）、`PreflightCache`（Task 5）、`UploadRepository`（Task 7・8）、`DestinationRepository`（Task 3）
- Produces:
  - `tags_to_apply(rule: ImmichRule, origin: str) -> tuple[str, ...]`
  - `origin_after_upload(first_check_result: str | None, upload_status: str) -> str`
  - `datetime_plan(rule: ImmichRule, policy: str, captured_at: str, origin: str) -> DatetimePlan`
  - `DatetimePlan(proposed: str | None, automatic: bool, reason: str)`
  - `with_lease_pulse(ctx, work, also=None, ownership_errors=(LeaseLost,))`（移設。`publisher._with_lease_pulse` は別名で残す）
  - `Uploader(conn, uploads, destinations, registry, data_root, open_client, preflight, max_attempts=3)`
  - `.run(ctx: JobContext, destination_id: str) -> UploadOutcome`
  - `UploadOutcome(sent: int, skipped: int, failed: int, awaiting: int)`

**§9.10 の手順をそのまま実装する。**

1. `checking` — `bulk-upload-check`。既存なら `asset_known` へ飛ぶ。
   **`isTrashed` を無視しない**（ゴミ箱の資産も重複として弾かれる）
2. `uploading` — multipart のストリーミング送信。`x-immich-checksum` は base64
3. `asset_known` — `remote_asset_id` を commit。ここで初めて「サーバ側に存在する」が確定
4. `tagging` — 追加操作のみ。`origin` が `created_by_us` でなければ `tag_pre_existing` に従う
5. `fixing_datetime` — 条件を満たすときだけ。満たさなければ `awaiting_datetime_approval`
6. `complete`

**巨大ファイルの送信中もリースを延ばす。** 28 GiB の送信は 84.5 秒（Phase 0 の実測）で、
リース（60 秒）より長い。`httpx` の送信は途中で止められないので、Phase 2 で作った
`_with_lease_pulse` を共有モジュールへ移して使う。**送信を別スレッドへ出し、待つ側が
`ctx.heartbeat()` と `uploads.extend_claim()` を打つ。** DB へ触るのは待つ側だけなので、
接続はスコープごとに 1 本のまま。

**キャンセルは送信の前後で扱いを変える。** 送信前なら `pending` へ戻す（何も起きて
いない）。送信中に観測したら `needs_recheck` へ落とす（**サーバ側の成否が不明**）。

- [ ] **Step 1: 判断の純粋関数を書く（失敗するテストから）**

`app/tests/test_upload_decisions.py`:

```python
import pytest

from mediaferry.core.profiles.model import ImmichRule
from mediaferry.core.uploads.decisions import (
    datetime_plan,
    origin_after_upload,
    tags_to_apply,
)

CAPTURED = "2026-08-17T14:30:00+09:00"


def a_rule(**over):
    values = {"tags": ("mediaferry", "dji"), "tag_pre_existing": False,
              "fix_datetime_after_upload": True}
    values.update(over)
    return ImmichRule(**values)


def test_a_created_asset_gets_the_tags():
    assert tags_to_apply(a_rule(), "created_by_us") == ("mediaferry", "dji")


def test_a_pre_existing_asset_is_not_tagged_by_default():
    assert tags_to_apply(a_rule(), "pre_existing") == ()


def test_a_pre_existing_asset_is_tagged_when_the_profile_says_so():
    assert tags_to_apply(a_rule(tag_pre_existing=True), "pre_existing") == ("mediaferry", "dji")


def test_an_unknown_origin_is_treated_like_pre_existing():
    assert tags_to_apply(a_rule(), "unknown") == ()
    assert tags_to_apply(a_rule(tag_pre_existing=True), "unknown") == ("mediaferry", "dji")


def test_a_created_status_proves_we_made_it():
    assert origin_after_upload("accept", "created") == "created_by_us"


def test_a_duplicate_after_a_reject_is_pre_existing():
    assert origin_after_upload("reject", "duplicate") == "pre_existing"


def test_a_duplicate_after_an_accept_is_unknown():
    """チェックとアップロードの間に別のクライアントが割り込みうる."""
    assert origin_after_upload("accept", "duplicate") == "unknown"


def test_a_missing_first_check_is_unknown():
    assert origin_after_upload(None, "duplicate") == "unknown"


def test_the_capture_time_is_written_back_automatically_when_we_made_it():
    plan = datetime_plan(a_rule(), "force_offset", CAPTURED, "created_by_us")
    assert plan.proposed == CAPTURED
    assert plan.automatic is True


def test_a_pre_existing_asset_needs_approval():
    plan = datetime_plan(a_rule(), "force_offset", CAPTURED, "pre_existing")
    assert plan.proposed == CAPTURED
    assert plan.automatic is False


def test_an_unknown_origin_needs_approval():
    assert datetime_plan(a_rule(), "force_offset", CAPTURED, "unknown").automatic is False


def test_no_timezone_policy_means_no_proposal():
    plan = datetime_plan(a_rule(), "none", CAPTURED, "pre_existing")
    assert plan.proposed is None
    assert plan.automatic is False


def test_a_profile_can_turn_the_correction_off():
    plan = datetime_plan(a_rule(fix_datetime_after_upload=False), "force_offset", CAPTURED,
                         "created_by_us")
    assert plan.proposed is None
    assert plan.reason
```

実装 `app/src/mediaferry/core/uploads/__init__.py`:

```python
from __future__ import annotations
```

`app/src/mediaferry/core/uploads/decisions.py`:

```python
"""アップロードの判断（§9.10）.

HTTP も DB も知らない。**「自分が作った資産か」を証明できるかどうかで、
既存資産を書き換えてよいかが決まる。**
"""

from __future__ import annotations

from dataclasses import dataclass

from ..profiles.model import ImmichRule


@dataclass(frozen=True)
class DatetimePlan:
    """撮影日時の補正案.

    `automatic` が偽なら `awaiting_datetime_approval` へ進み、ユーザの明示承認を
    待つ。`proposed` が None なら補正案そのものが無いので承認も要らない。
    """

    proposed: str | None
    automatic: bool
    reason: str


def tags_to_apply(rule: ImmichRule, origin: str) -> tuple[str, ...]:
    """付けるタグ. **追加操作だけ**で、既存タグは消さない.

    自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めた
    タグを付けてしまわないようにする。
    """
    if origin == "created_by_us" or rule.tag_pre_existing:
        return rule.tags
    return ()


def origin_after_upload(first_check_result: str | None, upload_status: str) -> str:
    """`POST /api/assets` の応答から origin を決める.

    `created` が返れば自分が作ったと確定する。`duplicate` は、初回の
    `checking` が `reject` だったときだけ「以前から存在した」と言える。
    **初回が `accept` だったことは自作の証明にならない**（チェックと
    アップロードの間に別のクライアントが割り込みうる）。
    """
    if upload_status == "created":
        return "created_by_us"
    if first_check_result == "reject":
        return "pre_existing"
    return "unknown"


def datetime_plan(
    rule: ImmichRule, policy: str, captured_at: str, origin: str
) -> DatetimePlan:
    """撮影日時を書き戻すか、承認を待つか、何もしないかを決める."""
    if not rule.fix_datetime_after_upload:
        return DatetimePlan(None, False, "プロファイルが日時の補正を行わない設定")
    if policy == "none":
        return DatetimePlan(None, False, "タイムゾーンを解決していないので補正案が無い")
    if origin == "created_by_us":
        return DatetimePlan(captured_at, True, "自分がアップロードした資産")
    # 別経路で既に上がっていて、ユーザが手で直しているかもしれない。
    return DatetimePlan(captured_at, False, "自作と証明できない資産なので承認を待つ")
```

- [ ] **Step 2: `_with_lease_pulse` を共有モジュールへ移す**

`app/src/mediaferry/core/lease_pulse.py`（`adapters/publisher.py` から関数と定数を移す）:

```python
"""中断できない長い処理の間、リースを延ばし続ける.

`os.fsync`（30 GiB の直後は数十秒）、ffprobe（timeout がリースと同値）、
巨大ファイルの HTTP 送信（28 GiB で 84.5 秒）は、いずれも 1 回でリース
（60 秒）を超えうるのに途中で止められない。

**処理は別スレッドで走らせ、待つ側が heartbeat を打つ。** DB へ触るのは
待つ側だけなので、接続はスコープごとに 1 本のままで済む。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from ..db.jobs import LEASE_SECONDS, JobContext, LeaseLost

# リース (60 秒) の 1/3 ごとに延ばす。処理の長さは環境で桁が変わるので、
# 量ではなく時間で決める。
HEARTBEAT_INTERVAL = LEASE_SECONDS / 3


def with_lease_pulse[T](
    ctx: JobContext,
    work: Callable[[], T],
    also: Callable[[], None] | None = None,
    ownership_errors: tuple[type[BaseException], ...] = (LeaseLost,),
) -> T:
    """`work` を待ちながら heartbeat を打つ.

    `also` を渡すと、heartbeat のたびに一緒に呼ぶ（アップロードでは
    `upload_record.claim_expires_at` の延長に使う）。

    **`ownership_errors` には `also` が投げうる例外も含める。** アップロードは
    `ClaimLost` を投げるので、`(LeaseLost, ClaimLost)` を渡す。含め忘れると、
    claim の延長が失敗した瞬間に待つ側だけが例外で抜け、**走っているスレッドが
    後から 30 GiB を送り終える**（呼び出し側は失敗したと見ているのに副作用は進む）。
    `core` は `db.uploads` を知らないので、集合は呼び出し側から渡す。

    所有権を失っても、処理の完了を待ってから送出する。**走っているスレッドを
    残したまま抜けると、後から副作用が起きる。**
    """
    outcome: list[T] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome.append(work())
        except BaseException as exc:  # noqa: BLE001 - 呼び出し側へそのまま渡す
            failure.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    lost: BaseException | None = None
    while True:
        thread.join(timeout=HEARTBEAT_INTERVAL)
        if not thread.is_alive():
            break
        if lost is None:
            try:
                # **先に assert_lease を呼ぶ。** `extend_lease` は `cancelling` でも
                # 延ばすので、heartbeat だけでは 28 GiB の送信中のキャンセルに
                # 気づけない（`assert_lease` は `cancelling` を通さない）。
                ctx.assert_lease()
                ctx.heartbeat()
                if also is not None:
                    also()
            except ownership_errors as exc:
                # **打てなくなっても待ち続ける。** ここで抜けると、走っている
                # スレッドが後から 30 GiB を送り終える。呼び出し側は「失敗した」と
                # 見ているのに副作用だけが進む。
                lost = exc
    if lost is not None:
        raise lost
    if failure:
        raise failure[0]
    return outcome[0]
```

`adapters/publisher.py` 側は、実装を消して**別名で読み替える**（既存の呼び出しと
テストをそのまま通す）:

```python
from ..core.lease_pulse import HEARTBEAT_INTERVAL, with_lease_pulse as _with_lease_pulse
```

**`HEARTBEAT_INTERVAL` を publisher から再エクスポートする理由:** 既存のテストが
`monkeypatch.setattr("mediaferry.adapters.publisher.HEARTBEAT_INTERVAL", 0)` で
差し替えている。`with_lease_pulse` は `core.lease_pulse.HEARTBEAT_INTERVAL` を読むので、
**publisher 側だけを差し替えても効かない。** テストの差し替え先を
`mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL` へ直す（`test_publisher.py` の 3 か所）。

Run: `uv run pytest app/tests/test_publisher.py app/tests/test_crash_consistency.py -q`
Expected: PASS（移設の前後で挙動は変わらない）

- [ ] **Step 3: 失敗するテストを書く（状態機械）**

`app/tests/test_uploader.py`:

```python
import os
from datetime import UTC, datetime

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.jobs.preflight import PreflightCache, PreflightFailed
from mediaferry.jobs.uploader import Uploader

from .fake_immich import API_KEY, FakeImmich
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"
CAPTURED = "2026-08-17T14:30:00+09:00"


@pytest.fixture
def world(db, data_root):
    import hashlib

    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home", base_url=server.url, public_url=None, secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)

    directory = data_root / "library" / "dji-osmo" / "DCIM"
    directory.mkdir(parents=True)
    (directory / "A.MP4").write_bytes(PAYLOAD)
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
        size_bytes=len(PAYLOAD),
        captured_at=CAPTURED,
        mtime_ns=1_700_000_000_000_000_000,
    )
    uploads.create_pairs([media_id], [destination_id])

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id})
    ctx = store.claim_next()
    uploader = Uploader(
        db, uploads, destinations, ProfileRegistry(db), data_root, open_client,
        preflight=PreflightCache(destinations, open_client),
    )
    return server, uploader, ctx, uploads, destinations, destination_id, media_id


def record_of(db):
    return db.execute("SELECT * FROM upload_record").fetchone()


def test_a_new_asset_is_uploaded_tagged_and_dated(world, db):
    server, uploader, ctx, _, _, destination_id, media_id = world

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 1
    row = record_of(db)
    assert row["state"] == "complete"
    assert row["origin"] == "created_by_us"
    assert row["remote_asset_id"] == "asset-1"
    assert row["first_check_result"] == "accept"
    assert server.uploads[0]["deviceAssetId"] == f"mediaferry:{media_id}"
    # 既定の DJI プロファイルはタグを持つ。自作なので付ける。
    assert server.tagged
    assert server.datetimes[row["remote_asset_id"]] == CAPTURED


def test_an_asset_that_already_exists_is_not_uploaded_again(world, db):
    import base64
    import hashlib

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"

    uploader.run(ctx, destination_id)

    row = record_of(db)
    assert server.uploads == []
    assert row["remote_asset_id"] == "asset-existing"
    assert row["origin"] == "pre_existing"
    assert row["first_check_result"] == "reject"
    # 自作と証明できないので、日時は自動で書き換えない。
    assert row["state"] == "awaiting_datetime_approval"
    assert server.datetimes == {}


def test_a_trashed_asset_is_recorded_as_trashed(world, db):
    import base64
    import hashlib

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"
    server.trashed.add("asset-existing")

    uploader.run(ctx, destination_id)

    assert record_of(db)["remote_is_trashed"] == 1


def test_a_pre_existing_asset_is_not_tagged_by_default(world, db):
    import base64
    import hashlib

    server, uploader, ctx, _, _, destination_id, _ = world
    checksum = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()
    server.assets[checksum] = "asset-existing"

    uploader.run(ctx, destination_id)

    assert server.tagged == {}


def test_the_preflight_stops_everything_before_a_byte_is_sent(world, db):
    """preflight は claim の後だが、**リモートに触る前**なので pending へ戻す."""
    server, uploader, ctx, _, _, destination_id, _ = world
    server.user_id = "someone-else"

    with pytest.raises(PreflightFailed):
        uploader.run(ctx, destination_id)

    assert server.uploads == []
    row = record_of(db)
    assert row["state"] == "pending"
    assert row["claim_job_id"] is None


def test_a_cancel_while_the_upload_is_in_flight_stops_the_commit(world, db, monkeypatch):
    """送信は成功しても、キャンセル後の commit は通さない（§8）.

    通すと、画面はキャンセル済みなのにタグと日時まで進む。
    """
    server, uploader, ctx, _, _, destination_id, _ = world
    real = ImmichClient.upload_asset

    def cancel_then_upload(self, *args, **kwargs):
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", cancel_then_upload)
    uploader.run(ctx, destination_id)

    row = record_of(db)
    # サーバには上がったかもしれないので needs_recheck。タグも日時も付けない。
    assert row["state"] == "needs_recheck"
    assert server.tagged == {}
    assert server.datetimes == {}


def test_the_target_is_re_checked_before_the_tags_when_the_ttl_expired(world, db, monkeypatch):
    """送信が TTL を跨いだら、タグと日時の前に向き先を取り直す."""
    server, uploader, ctx, _, _, destination_id, _ = world
    uploader._preflight._ttl = 0  # noqa: SLF001 - TTL 切れを再現する
    real = ImmichClient.upload_asset

    def upload_then_move(self, *args, **kwargs):
        outcome = real(self, *args, **kwargs)
        # 送信中に別のライブラリへ差し替わった。
        server.user_id = "someone-else"
        return outcome

    monkeypatch.setattr(ImmichClient, "upload_asset", upload_then_move)
    with pytest.raises(PreflightFailed):
        uploader.run(ctx, destination_id)

    # 別ライブラリにタグも日時も書かない。
    assert server.tagged == {}
    assert server.datetimes == {}


def test_a_server_error_is_retried_and_then_failed(world, db, monkeypatch):
    server, uploader, ctx, _, _, destination_id, _ = world
    # **preflight の後で落とす。** `server.fail_next` にすると preflight が先に
    # 落ちて、再試行の分岐を一度も通らない。
    monkeypatch.setattr("mediaferry.jobs.uploader.BACKOFF_BASE_SECONDS", 0.01)
    from mediaferry.adapters.immich import ImmichUnavailable

    def unavailable(*args, **kwargs):
        raise ImmichUnavailable("POST /api/assets/bulk-upload-check が 503")

    monkeypatch.setattr(ImmichClient, "bulk_upload_check", unavailable)

    outcome = uploader.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.failed == 1
    assert row["state"] == "failed"
    assert row["attempts"] == 3
    assert row["last_error"]
    # 秘密は残さない。
    assert API_KEY not in row["last_error"]


def test_an_auth_failure_is_not_retried(world, db, monkeypatch):
    """鍵が失効した場合、何度試しても変わらない. 再試行に回さず落とす.

    preflight を通った後に 401 になる筋書きを作る（鍵の失効は送信の途中でも
    起きる）。preflight の段で落とすと、この分岐を一度も通らない。
    """
    from mediaferry.adapters.immich import ImmichAuthFailed

    server, uploader, ctx, _, _, destination_id, _ = world

    def refuse(*args, **kwargs):
        raise ImmichAuthFailed("POST /api/assets が 401")

    monkeypatch.setattr(ImmichClient, "upload_asset", refuse)
    with pytest.raises(ImmichAuthFailed):
        uploader.run(ctx, destination_id)

    row = record_of(db)
    assert row["attempts"] == 0
    # 送信の成否が不明なまま降りるので、次回は checking から照合し直す。
    assert row["state"] == "needs_recheck"


def test_a_cancel_before_sending_leaves_it_pending(world, db):
    _, uploader, ctx, _, _, destination_id, _ = world
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    outcome = uploader.run(ctx, destination_id)

    assert outcome.sent == 0
    assert record_of(db)["state"] == "pending"


def test_a_cancel_during_the_send_asks_for_a_recheck(world, db, monkeypatch):
    """サーバ側の成否が不明なので、次回 checking から照合し直す."""
    server, uploader, ctx, _, _, destination_id, _ = world

    def cancel_then_upload(*args, **kwargs):
        db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))
        raise KeyboardInterrupt

    monkeypatch.setattr(ImmichClient, "upload_asset", cancel_then_upload)
    with pytest.raises(KeyboardInterrupt):
        uploader.run(ctx, destination_id)

    assert record_of(db)["state"] == "needs_recheck"


def test_a_record_that_lost_its_grounds_is_refused_not_sent(world, db):
    server, uploader, ctx, _, _, destination_id, _ = world
    db.execute(
        "UPDATE media_file SET missing_at = '2026-08-17T00:00:00+00:00' WHERE role = 'original'"
    )

    outcome = uploader.run(ctx, destination_id)

    assert outcome.skipped == 1
    assert server.uploads == []
    row = record_of(db)
    assert row["invalidated_at"] is not None


def test_the_lease_is_extended_while_the_file_is_sent(world, db, monkeypatch):
    import time

    server, uploader, ctx, _, _, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    beats = []
    monkeypatch.setattr(ctx, "heartbeat", lambda: beats.append(1))
    real = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(0.3)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)
    uploader.run(ctx, destination_id)

    assert beats
    assert record_of(db)["state"] == "complete"


def test_the_job_stops_when_there_is_nothing_left(world, db):
    _, uploader, ctx, _, _, destination_id, _ = world
    uploader.run(ctx, destination_id)
    outcome = uploader.run(ctx, destination_id)
    assert (outcome.sent, outcome.failed, outcome.skipped) == (0, 0, 0)
```

- [ ] **Step 4: 失敗を確認する**

Run: `uv run pytest app/tests/test_uploader.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.jobs.uploader'`）

- [ ] **Step 5: 最小実装**

`app/src/mediaferry/jobs/uploader.py`:

```python
"""Immich へのアップロード（§9.10）.

1 ジョブが 1 宛先を担当し、claim できるレコードが無くなるまで 1 件ずつ進める。
**逐次実行**である。ジョブ内で並行させると状態遷移の commit を別スレッドから
行うことになり、接続をスコープごとに 1 本に保てない（`UPLOAD_CONCURRENCY` は
ワーカーを多重化するときに効かせる）。

各段階は冪等で、どこで落ちても `checking` からやり直せる。**送信中の中断は
サーバ側の成否が不明なので `needs_recheck` に落とす。** チェックサム照合で
既存が見つかれば `asset_known` へ進むため、二重アップロードにはならない。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..adapters.immich import (
    ImmichAuthFailed,
    ImmichClient,
    ImmichError,
    ImmichProtocolError,
    ImmichRedirected,
    ImmichUnavailable,
)
from ..clock import now_iso
from ..core.lease_pulse import with_lease_pulse
from ..core.uploads.decisions import datetime_plan, origin_after_upload, tags_to_apply
from ..db.jobs import JobContext, LeaseLost
from ..db.profiles import ProfileRegistry
from ..db.uploads import ClaimLost, UploadRepository
from .preflight import PreflightCache

logger = logging.getLogger(__name__)

# 再試行の間隔（秒）。指数バックオフ。キャンセルを見るために刻んで待つ。
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 30.0
CANCEL_POLL_SECONDS = 0.2
# 失敗の理由に残す長さ。**秘密は含めない**（例外の文字列に URL は入るが鍵は入らない）。
ERROR_CHARS = 500


@dataclass(frozen=True)
class UploadOutcome:
    sent: int
    skipped: int
    failed: int
    awaiting: int


@dataclass
class _Progress:
    """1 レコードの進み具合.

    `touched_remote` は「リモートに触った可能性があるか」。降りるときの
    戻し先（`pending` か `needs_recheck` か）を決める。
    """

    settled: bool = False
    touched_remote: bool = False


class Uploader:
    def __init__(
        self,
        conn: sqlite3.Connection,
        uploads: UploadRepository,
        destinations,  # noqa: ANN001 - DestinationRepository
        registry: ProfileRegistry,
        data_root: Path,
        open_client: Callable[[sqlite3.Row], ImmichClient],
        preflight: PreflightCache,
        max_attempts: int = 3,
    ) -> None:
        self._conn = conn
        self._uploads = uploads
        self._destinations = destinations
        self._registry = registry
        self._data_root = data_root
        self._open_client = open_client
        self._preflight = preflight
        self._max_attempts = max_attempts

    def run(self, ctx: JobContext, destination_id: str) -> UploadOutcome:
        sent = skipped = failed = awaiting = 0
        while True:
            if ctx.cancelled():
                break
            # **リビジョンは pair ごとに、claim と同じトランザクションで決まる**（§8）。
            record = self._uploads.claim_next(destination_id, ctx.job_id, ctx.lease_token)
            if record is None:
                break
            state = self._guarded(ctx, record)
            sent += state == "complete"
            failed += state == "failed"
            awaiting += state == "awaiting_datetime_approval"
            skipped += state in ("pending", "needs_recheck", "refused")
        return UploadOutcome(sent=sent, skipped=skipped, failed=failed, awaiting=awaiting)

    # ------------------------------------------------------------------
    def _guarded(self, ctx: JobContext, record: sqlite3.Row) -> str:
        """**claim を取った後の全経路をここで囲む。**

        資格情報の復号、クライアントの構築、プロファイルの解決まで try の外に
        置くと、そこで落ちたときにレコードが `checking` + claim のまま残る。
        `claim_next` は進行中の状態を拾わないので、**次の起動まで誰も触れなく
        なる**。決着が付かずに抜ける経路はすべて解放してから送出する。
        """
        progress = _Progress()
        try:
            reason = self._uploads.check_eligibility(record)
            if reason is not None:
                # 送らずに無効化して理由を残す（§10）。
                self._uploads.refuse(record["id"], ctx.lease_token, reason)
                ctx.emit(
                    "warning", f"送信を見送った: {reason}", {"upload_record_id": record["id"]}
                )
                progress.settled = True
                return "refused"
            state = self._one(ctx, record, progress)
            progress.settled = True
            return state
        finally:
            if not progress.settled:
                # **副作用の境界を越えたかで戻し先を変える。** 越える前の失敗
                # （preflight、資格情報の復号、クライアントの構築）は確定的なので
                # `pending` へ。越えた後だけ「サーバ側の成否が不明」＝
                # `needs_recheck` にする。
                if progress.touched_remote:
                    self._release_unknown(ctx, record)
                else:
                    self._release_pending(ctx, record)

    def _one(self, ctx: JobContext, record: sqlite3.Row, progress: _Progress) -> str:
        revision = self._destinations.revision(record["destination_revision_id"])
        # **1 バイトも送る前に向き先を確かめる。** ここで落ちれば、まだ何も
        # 起きていない（`progress.touched_remote` は偽のまま）。
        self._preflight.assert_target(revision["id"])
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (record["media_file_id"],)
        ).fetchone()
        profile = self._registry.by_id(media["profile_id"])
        with self._open_client(revision) as client:
            try:
                return self._steps(ctx, client, record, media, profile, revision["id"], progress)
            except ImmichUnavailable as exc:
                return self._retry_or_fail(ctx, record, exc)
            except (ImmichAuthFailed, ImmichRedirected, ImmichProtocolError):
                # 鍵が違う・向き先がおかしい・応答の形が違う。再試行しても
                # 変わらないので、ジョブごと止めて人に見せる。送信の途中かも
                # しれないので、claim は `_guarded` の finally が外す。
                raise
            except ImmichError as exc:
                self._uploads.finish(
                    record["id"], ctx.lease_token, "failed",
                    expect_state=self._uploads.get(record["id"])["state"],
                    last_error=str(exc)[:ERROR_CHARS],
                    attempts=record["attempts"] + 1,
                )
                return "failed"

    def _guard(
        self,
        ctx: JobContext,
        record: sqlite3.Row,
        revision_id: str,
        state: str,
        progress: _Progress | None = None,
    ) -> None:
        """**ネットワークへ触る直前に必ず通す。**

        向き先の再確認（TTL 内ならキャッシュが返るので通信は増えない）と、
        リース・claim の確認をまとめる。レコードの先頭で 1 回だけにすると、
        70 GiB の送信が TTL を跨いだ後の tag / 日時 PUT が**別のライブラリへ
        飛ぶ**（asset ID が偶然存在すれば他人の資産を書き換える）。
        """
        self._preflight.assert_target(revision_id)
        self._uploads.prepare_side_effect(ctx, record["id"], state)
        if progress is not None:
            # ここを抜けた後は、リモートに触った可能性がある。
            progress.touched_remote = True

    def _steps(
        self,
        ctx: JobContext,
        client: ImmichClient,
        record: sqlite3.Row,
        media: sqlite3.Row,
        profile,  # noqa: ANN001 - ProfileRef
        revision_id: str,
        progress: _Progress,
    ) -> str:
        # 1. checking —— 外部への要求なので、直前に所有権と向き先を確かめる。
        self._guard(ctx, record, revision_id, "checking", progress)
        outcome = client.bulk_upload_check([(record["id"], media["sha1"])])[record["id"]]
        first_check = record["first_check_result"] or outcome.action
        if outcome.action == "reject":
            origin = "pre_existing" if first_check == "reject" else "unknown"
            self._uploads.advance_owned(
                ctx, record["id"], "asset_known", expect_state="checking",
                first_check_result=first_check,
                remote_asset_id=outcome.asset_id,
                remote_is_trashed=1 if outcome.is_trashed else 0,
                remote_checked_at=now_iso(),
                origin=origin,
            )
        else:
            self._uploads.advance_owned(
                ctx, record["id"], "uploading", expect_state="checking",
                first_check_result=first_check,
                remote_checked_at=now_iso(),
            )
            # 送信の直前にもう一度。ここを通ってから初めて 1 バイトを送る。
            self._guard(ctx, record, revision_id, "uploading", progress)
            uploaded = self._send(ctx, client, record, media)
            origin = origin_after_upload(first_check, uploaded.status)
            # 2〜3. asset_known。ここで初めて「サーバ側に存在する」が確定する。
            # **commit も所有権付きで行う。** 送信中にキャンセルされていたら
            # ここで止まり、`_guarded` が needs_recheck へ落とす。
            self._uploads.advance_owned(
                ctx, record["id"], "asset_known", expect_state="uploading",
                remote_asset_id=uploaded.asset_id,
                origin=origin,
            )

        row = self._uploads.get(record["id"])
        # 4. tagging —— **変更を伴う呼び出しごとに guard する。**
        self._uploads.advance_owned(ctx, record["id"], "tagging", expect_state="asset_known")
        for name in tags_to_apply(profile.definition.immich, row["origin"]):
            self._guard(ctx, record, revision_id, "tagging", progress)
            tag_id = client.find_tag(name)
            if tag_id is None:
                self._guard(ctx, record, revision_id, "tagging", progress)
                tag_id = client.create_tag(name)
            self._guard(ctx, record, revision_id, "tagging", progress)
            client.tag_assets(tag_id, [row["remote_asset_id"]])

        # 5. fixing_datetime
        plan = datetime_plan(
            profile.definition.immich,
            profile.definition.timestamp.timezone_policy,
            media["captured_at"],
            row["origin"],
        )
        if plan.proposed is None:
            self._uploads.finish_owned(ctx, record["id"], "complete", expect_state="tagging")
            return "complete"
        if not plan.automatic:
            ctx.emit(
                "info", f"日時の補正に承認が要る: {plan.reason}",
                {"upload_record_id": record["id"]},
            )
            self._uploads.finish_owned(
                ctx, record["id"], "awaiting_datetime_approval", expect_state="tagging"
            )
            return "awaiting_datetime_approval"
        self._uploads.advance_owned(
            ctx, record["id"], "fixing_datetime", expect_state="tagging"
        )
        # 既存資産の日時を書き換える。**最も取り返しがつかない副作用**なので、
        # 直前に必ず確かめる。
        self._guard(ctx, record, revision_id, "fixing_datetime", progress)
        client.set_date_time_original(row["remote_asset_id"], plan.proposed)
        self._uploads.finish_owned(
            ctx, record["id"], "complete", expect_state="fixing_datetime"
        )
        return "complete"

    def _send(self, ctx: JobContext, client: ImmichClient, record: sqlite3.Row, media: sqlite3.Row):  # noqa: ANN202
        """**送信中もリースと claim を延ばす。** 28 GiB は 84.5 秒（Phase 0 の実測）."""
        path = self._data_root / media["rel_path"]
        device_asset_id = f"mediaferry:{media['id']}"
        modified = datetime.fromtimestamp(media["mtime_ns"] / 1e9, tz=UTC).isoformat()
        return with_lease_pulse(
            ctx,
            lambda: client.upload_asset(
                path,
                sha1_hex=media["sha1"],
                device_asset_id=device_asset_id,
                file_created_at=media["captured_at"],
                file_modified_at=modified,
            ),
            also=lambda: self._uploads.extend_claim(record["id"], ctx.lease_token),
            # claim の延長が失敗しても、送信スレッドを残したまま抜けない。
            ownership_errors=(LeaseLost, ClaimLost),
        )

    def _retry_or_fail(self, ctx: JobContext, record: sqlite3.Row, exc: Exception) -> str:
        attempts = record["attempts"] + 1
        current = self._uploads.get(record["id"])["state"]
        if attempts >= self._max_attempts:
            self._uploads.finish(
                record["id"], ctx.lease_token, "failed", expect_state=current,
                attempts=attempts, last_error=str(exc)[:ERROR_CHARS],
            )
            ctx.emit("error", "アップロードに失敗した（上限まで再試行）",
                     {"upload_record_id": record["id"]})
            return "failed"
        self._uploads.release_to(
            record["id"], ctx.lease_token, "pending",
            attempts=attempts, last_error=str(exc)[:ERROR_CHARS],
        )
        self._sleep(ctx, min(BACKOFF_BASE_SECONDS**attempts, BACKOFF_MAX_SECONDS))
        return "pending"

    def _release_pending(self, ctx: JobContext, record: sqlite3.Row) -> None:
        """リモートに触る前に降りた. **確定的な失敗**なので `pending` へ戻す."""
        try:
            self._uploads.release_to(record["id"], ctx.lease_token, "pending")
        except ClaimLost:
            logger.warning("claim を失った状態で降りた: %s", record["id"])

    def _release_unknown(self, ctx: JobContext, record: sqlite3.Row) -> None:
        """サーバ側の成否が不明なまま降りる. 次回 `checking` から照合し直す.

        **冪等にする。** すでに終端まで進んでいたり、claim を失っていたりする
        場合は何もしない（`ClaimLost` を握りつぶす）。ここで送出すると、
        本来の失敗理由が隠れる。
        """
        try:
            self._uploads.release_to(record["id"], ctx.lease_token, "needs_recheck")
        except ClaimLost:
            logger.warning("claim を失った状態で降りた: %s", record["id"])

    def _sleep(self, ctx: JobContext, seconds: float) -> None:
        """待つ間もキャンセルを見る."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if ctx.cancelled():
                return
            time.sleep(CANCEL_POLL_SECONDS)
```

- [ ] **Step 6: 通ることを確認する**

Run: `uv run pytest app/tests/test_upload_decisions.py app/tests/test_uploader.py -q`
Expected: PASS（13 + 12 件）

**通らない場合に先に疑うところ:** `_retry_or_fail` が `pending` へ戻すと、同じ
ジョブの次の周回でまた claim される（`test_a_server_error_is_retried_and_then_failed`
はこれで 3 回試して `failed` になる）。無限ループを避けるため、**`attempts` は
必ず増やす**。

- [ ] **Step 7: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| preflight の呼び出しを消す | `test_the_preflight_stops_everything_before_a_byte_is_sent` |
| preflight の失敗で `needs_recheck` へ落とす | `test_the_preflight_stops_everything_before_a_byte_is_sent`（リモートに触っていないので `pending`） |
| `advance_owned` / `finish_owned` を `advance` / `finish` に戻す | `test_a_cancel_while_the_upload_is_in_flight_stops_the_commit` |
| `_guard` をレコードの先頭で 1 回だけにする | `test_the_target_is_re_checked_before_the_tags_when_the_ttl_expired` |
| `_guard` から preflight を外す | 同上 |
| `check_eligibility` の呼び出しを消す | `test_a_record_that_lost_its_grounds_is_refused_not_sent` |
| `outcome.action == "reject"` の分岐を消して常に送る | `test_an_asset_that_already_exists_is_not_uploaded_again` |
| `is_trashed` を記録しない | `test_a_trashed_asset_is_recorded_as_trashed` |
| `origin_after_upload` を常に `created_by_us` にする | `test_an_asset_that_already_exists_is_not_uploaded_again`（承認待ちにならない） |
| `tags_to_apply` を無視して常に付ける | `test_a_pre_existing_asset_is_not_tagged_by_default` |
| `plan.automatic` を無視して常に書き戻す | `test_an_asset_that_already_exists_is_not_uploaded_again`（`datetimes` が空でなくなる） |
| `with_lease_pulse` を素の呼び出しにする | `test_the_lease_is_extended_while_the_file_is_sent` |
| `also`（claim の延長）を渡さない | **落ちない**。テストの claim は既定 60 秒で切れない。**`lease_seconds` を 1 にして claim を取り、送信を 1.5 秒にするテストを足す**（下記） |
| 送信前の `ctx.cancelled()` を消す | `test_a_cancel_before_sending_leaves_it_pending` |
| `BaseException` の捕捉を消す（`needs_recheck` にしない） | `test_a_cancel_during_the_send_asks_for_a_recheck` |
| `needs_recheck` を `failed` にする | 同上 |
| `ImmichAuthFailed` を再試行に回す | `test_an_auth_failure_is_not_retried` |
| `attempts` を増やさない | `test_a_server_error_is_retried_and_then_failed`（無限ループになり timeout） |
| `last_error` に例外の全文（URL 込み）ではなく API キーを入れる | 同上の秘密の assert |

claim の延長を見るテストを足す:

```python
def test_the_claim_is_extended_while_the_file_is_sent(world, db, monkeypatch):
    """リースだけ延ばしても、claim が切れれば次のジョブに横取りされる."""
    import time

    server, uploader, ctx, uploads, destinations, destination_id, _ = world
    monkeypatch.setattr("mediaferry.core.lease_pulse.HEARTBEAT_INTERVAL", 0.05)
    real = ImmichClient.upload_asset

    def slow_upload(self, *args, **kwargs):
        time.sleep(0.5)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ImmichClient, "upload_asset", slow_upload)
    # claim を 1 秒で切れるようにしてから走らせる。
    monkeypatch.setattr(uploads, "claim_next", _claim_with(uploads, seconds=1))

    uploader.run(ctx, destination_id)

    assert record_of(db)["state"] == "complete"


def _claim_with(uploads, seconds):
    real = uploads.claim_next

    def claim(revision, job_id, token, lease_seconds=60):
        return real(revision, job_id, token, seconds)

    return claim
```

- [ ] **Step 8: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/core/uploads app/src/mediaferry/core/lease_pulse.py \
        app/src/mediaferry/adapters/publisher.py app/src/mediaferry/jobs/uploader.py \
        app/tests/test_upload_decisions.py app/tests/test_uploader.py \
        app/tests/test_publisher.py
git commit -m "feat(mediaferry): drive the immich upload state machine"
```

---

### Task 10: 状態の再確認（ゴミ箱と消滅の追跡）

**Files:**
- Create: `app/src/mediaferry/jobs/recheck.py`
- Modify: `app/src/mediaferry/db/uploads.py`（`stamp_remote` を足す）
- Test: `app/tests/test_upload_recheck.py`

**Interfaces:**
- Consumes: `UploadRepository`（Task 8）、`ImmichClient`（Task 4）、`PreflightCache`（Task 5）
- Produces:
  - `Rechecker(uploads, destinations, open_client, preflight)`
  - `.run(ctx: JobContext, destination_id: str) -> RecheckOutcome`
  - `RecheckOutcome(checked: int, trashed: int, vanished: int, restored: int)`

**`remote_is_trashed` は `checking` 時点のスナップショットにすぎない**（§9.10）。
`complete` になったレコードは再照合されないので、ゴミ箱の保持期限（既定 30 日）を
過ぎて資産が消えても「送信済み」のまま残る。

**自動で再アップロードはしない。** 利用者が意図的に消したものを黙って戻さない。
消えていた資産は「リモートに存在しない」と分かる形にして、ユーザが明示的に
`pending` へ戻せるようにする（`POST /uploads/{id}/requeue`。Task 13）。

**照合するのは現行 `target_epoch` のレコードだけ。** 旧 epoch の `complete` は
**別のライブラリへ送った履歴**なので、現行の資格情報で照合すると
`remote_asset_id` を別ライブラリの ID で上書きし、監査履歴が壊れる。
旧 epoch を確認する機能を将来作るなら、その epoch を明示的に選び、
固定したリビジョンの資格情報で照合する必要がある。

**「リモートに存在しない」の表し方:** 列を足さずに、`state = 'complete'` かつ
`remote_asset_id IS NULL` かつ `remote_checked_at IS NOT NULL` で表す。
再照合で `accept`（＝サーバに無い）が返ったときに `remote_asset_id` を落とす。
**`0004` に列を足さない**（CHECK を書き換えるとテーブルの作り直しになる）。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_upload_recheck.py`:

```python
import base64
import hashlib
import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.jobs.preflight import PreflightCache
from mediaferry.jobs.recheck import Rechecker

from .fake_immich import API_KEY, FakeImmich
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"
CHECKSUM = base64.b64encode(hashlib.sha1(PAYLOAD, usedforsecurity=False).digest()).decode()


_BOXES: dict[int, SecretBox] = {}


@pytest.fixture
def world(db, data_root, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    box = SecretBox(os.urandom(32))
    _BOXES[id(db)] = box
    destinations = DestinationRepository(db, CredentialStore(db, box))
    destination_id = destinations.create(
        name="home", base_url=server.url, public_url=None, secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(
        db, (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
    )
    uploads.create_pairs([media_id], [destination_id])
    db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = 'asset-1',"
        " remote_is_trashed = 0, destination_revision_id = ?",
        (destinations.current(destination_id)["id"],),
    )
    server.assets[CHECKSUM] = "asset-1"

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id, "mode": "recheck"})
    ctx = store.claim_next()
    rechecker = Rechecker(
        uploads, destinations, open_client, PreflightCache(destinations, open_client)
    )
    return server, rechecker, ctx, destination_id, db


def record_of(db):
    return db.execute("SELECT * FROM upload_record").fetchone()


def _box_of(db):
    """テスト内で作り直しても同じ鍵になるよう、fixture が使った箱を再利用する."""
    return _BOXES[id(db)]


def test_an_asset_that_is_still_there_is_just_stamped(world):
    server, rechecker, ctx, destination_id, db = world

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.checked == 1
    assert row["remote_checked_at"] is not None
    assert row["remote_asset_id"] == "asset-1"
    assert row["remote_is_trashed"] == 0


def test_an_asset_in_the_trash_is_flagged(world):
    server, rechecker, ctx, destination_id, db = world
    server.trashed.add("asset-1")

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.trashed == 1
    assert record_of(db)["remote_is_trashed"] == 1


def test_an_asset_restored_from_the_trash_clears_the_flag(world):
    server, rechecker, ctx, destination_id, db = world
    db.execute("UPDATE upload_record SET remote_is_trashed = 1")

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.restored == 1
    assert record_of(db)["remote_is_trashed"] == 0


def test_a_vanished_asset_is_shown_as_missing_not_resent(world):
    server, rechecker, ctx, destination_id, db = world
    server.assets.clear()  # 保持期限を過ぎて完全に消えた

    outcome = rechecker.run(ctx, destination_id)

    row = record_of(db)
    assert outcome.vanished == 1
    # 自動では送り直さない。利用者が意図的に消したものを黙って戻さない。
    assert row["state"] == "complete"
    assert row["remote_asset_id"] is None
    assert row["remote_checked_at"] is not None
    assert server.uploads == []


def test_records_from_an_old_epoch_are_not_touched(world):
    """旧 epoch は別ライブラリへ送った履歴. 現行の資格情報で照合しない."""
    server, rechecker, ctx, destination_id, db = world
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity

    before = record_of(db)["remote_asset_id"]
    destinations = DestinationRepository(db, CredentialStore(db, _box_of(db)))
    # **別アカウントへ向け替える**（ホストが同じままだと epoch は進まない）。
    destinations.add_revision(
        destination_id, base_url=server.url, public_url=None, secret=API_KEY,
        identity=RemoteIdentity(remote_user_id="another-user", server_instance_id=None),
    )
    assert destinations.current(destination_id)["target_epoch"] == 2
    server.user_id = "another-user"  # preflight を通す
    server.assets.clear()  # 新しいライブラリには何も無い

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    assert record_of(db)["remote_asset_id"] == before


def test_only_complete_records_are_rechecked(world):
    server, rechecker, ctx, destination_id, db = world
    db.execute("UPDATE upload_record SET state = 'pending', destination_revision_id = NULL")

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 0


def test_the_preflight_runs_before_the_recheck(world):
    server, rechecker, ctx, destination_id, db = world
    server.user_id = "someone-else"
    from mediaferry.jobs.preflight import PreflightFailed

    with pytest.raises(PreflightFailed):
        rechecker.run(ctx, destination_id)
    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 0


def test_records_are_checked_in_one_batch(world):
    server, rechecker, ctx, destination_id, db = world
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    profile = ProfileRegistry(db).current("dji-osmo")
    revision_id = record_of(db)["destination_revision_id"]
    for index in range(3):
        media_id = a_media_file(
            db, (profile.profile_id, profile.revision_id),
            rel_path=f"library/dji-osmo/DCIM/B{index}.MP4", sha1=f"{index:040d}",
        )
        db.execute(
            "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
            " selection_rule, origin, remote_asset_id, checksum, destination_revision_id,"
            " created_at, updated_at)"
            " VALUES (?, ?, 1, ?, 'complete', 'default', 'created_by_us', ?, ?, ?, ?, ?)",
            (new_id(), destination_id, media_id, f"asset-b{index}", f"{index:040d}",
             revision_id, now_iso(), now_iso()),
        )

    rechecker.run(ctx, destination_id)

    # 4 件を 1 回で照合する（1 件ずつ叩かない）。
    assert server.requests.count(("POST", "/api/assets/bulk-upload-check")) == 1


def test_a_cancelled_recheck_stops_early(world):
    server, rechecker, ctx, destination_id, db = world
    db.execute("UPDATE job SET status = 'cancelling' WHERE id = ?", (ctx.job_id,))

    outcome = rechecker.run(ctx, destination_id)

    assert outcome.checked == 0
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_upload_recheck.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.jobs.recheck'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/jobs/recheck.py`:

```python
"""送信済みレコードの状態を確かめ直す（§9.10「ゴミ箱と消滅の追跡」）.

`remote_is_trashed` は `checking` 時点のスナップショットにすぎない。ゴミ箱の
保持期限を過ぎて資産が消えても「送信済み」のまま残るので、宛先ごとの明示操作で
照合し直す。

**自動で再アップロードはしない。** 消えていた資産は `remote_asset_id` を外して
「リモートに存在しない」と分かる形にし、ユーザが明示的に `pending` へ戻す。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ..adapters.immich import ImmichClient
from ..clock import now_iso
from ..db.jobs import JobContext
from ..db.uploads import UploadRepository
from .preflight import PreflightCache

# 1 回の照合に載せる件数は ImmichClient が分割する。ここでは全件を渡す。
COMPLETE = "complete"


@dataclass(frozen=True)
class RecheckOutcome:
    checked: int
    trashed: int
    vanished: int
    restored: int


class Rechecker:
    def __init__(
        self,
        uploads: UploadRepository,
        destinations,  # noqa: ANN001 - DestinationRepository
        open_client: Callable[[sqlite3.Row], ImmichClient],
        preflight: PreflightCache,
    ) -> None:
        self._uploads = uploads
        self._destinations = destinations
        self._open_client = open_client
        self._preflight = preflight

    def run(self, ctx: JobContext, destination_id: str) -> RecheckOutcome:
        revision = self._destinations.current(destination_id)
        # 向き先が変わっていたら、別ライブラリの照合結果で上書きしてしまう。
        self._preflight.assert_target(revision["id"])
        if ctx.cancelled():
            return RecheckOutcome(0, 0, 0, 0)

        # **現行 epoch だけを照合する。** 旧 epoch は別ライブラリへの履歴。
        # **黙って打ち切らない。** 上限で切ると「N 件確認した」の N が実際の
        # 件数と食い違い、消滅を見落とす。
        records = [
            row
            for row in self._uploads.records_for_recheck(
                destination_id, revision["target_epoch"]
            )
            if row["checksum"] is not None
        ]
        if not records:
            return RecheckOutcome(0, 0, 0, 0)

        with self._open_client(revision) as client:
            outcomes = client.bulk_upload_check(
                [(row["id"], row["checksum"]) for row in records]
            )

        trashed = vanished = restored = 0
        for row in records:
            outcome = outcomes.get(row["id"])
            if outcome is None:
                continue
            if outcome.action == "accept":
                # サーバに無い。**送り直さない。** 見えるようにするだけ。
                self._stamp(row, asset_id=None, is_trashed=0)
                vanished += 1
                ctx.emit(
                    "warning", "リモートに存在しない資産がある",
                    {"upload_record_id": row["id"]},
                )
                continue
            self._stamp(row, asset_id=outcome.asset_id, is_trashed=1 if outcome.is_trashed else 0)
            if outcome.is_trashed and not row["remote_is_trashed"]:
                trashed += 1
            if not outcome.is_trashed and row["remote_is_trashed"]:
                restored += 1
        return RecheckOutcome(
            checked=len(records), trashed=trashed, vanished=vanished, restored=restored
        )

    def _stamp(self, row: sqlite3.Row, asset_id: str | None, is_trashed: int) -> None:
        """claim を取らずに更新する. `complete` の行は誰も所有していない."""
        self._uploads.stamp_remote(
            row["id"], asset_id=asset_id, is_trashed=is_trashed, checked_at=now_iso()
        )
```

`UploadRepository` に足すメソッド（対象の列挙と、claim を持たない `complete` の
行の更新）:

```python
    def records_for_recheck(self, destination_id: str, target_epoch: int) -> list[sqlite3.Row]:
        """再確認の対象. **現行 epoch の `complete` だけ**を、全件返す.

        旧 epoch は別ライブラリへ送った履歴なので、現行の資格情報で照合しない。
        件数の上限は置かない（打ち切ると「N 件確認した」が嘘になる）。
        """
        return list(
            self._conn.execute(
                "SELECT * FROM upload_record WHERE destination_id = ? AND target_epoch = ?"
                "   AND state = 'complete' AND invalidated_at IS NULL"
                " ORDER BY created_at",
                (destination_id, target_epoch),
            )
        )
```


```python
    def stamp_remote(
        self, record_id: str, asset_id: str | None, is_trashed: int, checked_at: str
    ) -> None:
        """再確認の結果を書く. **`complete` の行だけ**を対象にする.

        進行中の行は所有者がいるので、claim を持たないこの経路では触らない。
        """
        with immediate(self._conn):
            self._conn.execute(
                "UPDATE upload_record SET remote_asset_id = ?, remote_is_trashed = ?,"
                " remote_checked_at = ?, updated_at = ? WHERE id = ? AND state = 'complete'",
                (asset_id, is_trashed, checked_at, now_iso(), record_id),
            )
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_upload_recheck.py -q`
Expected: PASS（8 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `accept` を「存在する」と扱う | `test_a_vanished_asset_is_shown_as_missing_not_resent` |
| 消えた資産を `pending` へ戻す（自動再送） | 同上（`state` が変わる） |
| `is_trashed` を書かない | `test_an_asset_in_the_trash_is_flagged` |
| ゴミ箱から戻った資産の `remote_is_trashed` を落とさない | `test_an_asset_restored_from_the_trash_clears_the_flag` |
| `list_records` の `state` の絞り込みを外す | `test_only_complete_records_are_rechecked` |
| preflight を照合の後に移す | `test_the_preflight_runs_before_the_recheck` |
| 1 件ずつ `bulk_upload_check` を呼ぶ | `test_records_are_checked_in_one_batch` |
| `ctx.cancelled()` の確認を消す | `test_a_cancelled_recheck_stops_early` |
| `records_for_recheck` の `target_epoch = ?` を消す | `test_records_from_an_old_epoch_are_not_touched` |
| `records_for_recheck` に上限（`LIMIT 10000`）を戻す | **落ちない**（テストは 4 件）。**黙って打ち切らない**ことが要点なので、上限を置かない実装を維持する。検出できない変異として記録する |
| `stamp_remote` の `state = 'complete'` を消す | **落ちない**。テストは `complete` の行しか作らない。**進行中の行を用意して、再確認が触らないことを見るテストを足す**（下記） |

進行中の行を守るテストを足す:

```python
def test_a_record_in_flight_is_not_stamped_by_a_recheck(world):
    server, rechecker, ctx, destination_id, db = world
    db.execute(
        "UPDATE upload_record SET state = 'uploading', claim_job_id = 'other',"
        " claim_token = 'other-token', claim_expires_at = '2999-01-01T00:00:00+00:00'"
    )
    rechecker.run(ctx, destination_id)
    assert record_of(db)["remote_checked_at"] is None
```

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/jobs/recheck.py app/src/mediaferry/db/uploads.py \
        app/tests/test_upload_recheck.py
git commit -m "feat(mediaferry): re-check what the destination still holds"
```

---

### Task 11: 承認と却下

**Files:**
- Create: `app/src/mediaferry/jobs/approvals.py`
- Test: `app/tests/test_upload_approval.py`

**Interfaces:**
- Produces:
  - `ApprovalService(conn, uploads, destinations, registry, open_client, preflight)`
  - `.approve(ctx: JobContext, record_id: str) -> None`（`upload` ジョブの `mode="approve"` から呼ぶ）
  - `.reject(record_id: str) -> None`（同期。リモートに触らない）
  - `ApprovalNotPossible(RuntimeError)`

**承認と却下の両方を用意する**（§9.10）。却下が無いと、既に正しい日時が入っている
資産について「補正不要」と判断しても承認待ちを消せず、一覧に残り続ける。

| 操作 | 結果 |
| --- | --- |
| 承認 | `dateTimeOriginal` を書き戻してから `complete` |
| 却下 | **リモートを一切変更せずに `complete`** |

**承認は `upload` ジョブの `mode = "approve"` として実行する。** 1 件の `PUT` で
終わる処理だが、**外部への副作用には所有権が要る**（§8）。理由は 3 つ。

1. `0004` の CHECK は `fixing_datetime` を「claim を持つ状態」と定めている。
   ジョブのリース無しには、この状態を通れない
2. 承認と却下が同時に走ると、**却下が `complete` を commit した後に承認が
   リモートの日時を変更する**（「却下はリモートを変えない」が破れる）
3. 承認の途中で落ちたとき、claim があれば起動時の reconciliation が回収できる

**却下は同期のままでよい。** リモートに触らないので、`awaiting → complete` の
CAS 1 本で足り、承認ジョブが先に claim していれば CAS が 0 行になって負ける。

**承認は `invalidated_at` を見る。** 無効化されたレコードの日時を書き換えない。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_upload_approval.py`:

```python
import hashlib
import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.jobs.approvals import ApprovalNotPossible, ApprovalService
from mediaferry.jobs.preflight import PreflightCache, PreflightFailed

from .fake_immich import API_KEY, FakeImmich
from .test_schema_artifacts import a_media_file

CAPTURED = "2026-08-17T14:30:00+09:00"


@pytest.fixture
def world(db, data_root, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home", base_url=server.url, public_url=None, secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(
        db, (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(b"x", usedforsecurity=False).hexdigest(),
        captured_at=CAPTURED,
    )
    uploads.create_pairs([media_id], [destination_id])
    db.execute(
        "UPDATE upload_record SET state = 'awaiting_datetime_approval', origin = 'pre_existing',"
        " remote_asset_id = 'asset-1', destination_revision_id = ?",
        (destinations.current(destination_id)["id"],),
    )

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    service = ApprovalService(
        db, uploads, destinations, ProfileRegistry(db), open_client,
        PreflightCache(destinations, open_client),
    )
    store = JobStore(db)
    store.enqueue("upload", {"destination_id": destination_id, "mode": "approve"})
    ctx = store.claim_next()
    return server, service, db, uploads, ctx


def record_of(db):
    return db.execute("SELECT * FROM upload_record").fetchone()


def test_approving_writes_the_capture_time_and_completes(world):
    server, service, db, _, ctx = world

    service.approve(ctx, record_of(db)["id"])

    assert server.datetimes["asset-1"] == CAPTURED
    assert record_of(db)["state"] == "complete"


def test_rejecting_changes_nothing_remote(world):
    server, service, db, _, ctx = world

    service.reject(record_of(db)["id"])

    assert server.datetimes == {}
    assert record_of(db)["state"] == "complete"


def test_a_record_that_is_not_waiting_cannot_be_approved(world):
    server, service, db, _, ctx = world
    db.execute("UPDATE upload_record SET state = 'pending', destination_revision_id = NULL")
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, record_of(db)["id"])


def test_an_unknown_record_is_refused(world):
    _, service, _, _, ctx = world
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, "no-such-record")


def test_approving_re_checks_where_the_revision_points(world):
    server, service, db, _, ctx = world
    server.user_id = "someone-else"
    with pytest.raises(PreflightFailed):
        service.approve(ctx, record_of(db)["id"])
    assert server.datetimes == {}
    assert record_of(db)["state"] == "awaiting_datetime_approval"


def test_rejecting_does_not_need_the_remote(world):
    """却下はリモートに触らないので、向き先が変わっていても消せる."""
    server, service, db, _, ctx = world
    server.user_id = "someone-else"

    service.reject(record_of(db)["id"])

    assert record_of(db)["state"] == "complete"


def test_a_rejection_that_won_the_race_stops_the_approval(world):
    """却下が先に complete を commit したら、承認はリモートに触らない."""
    server, service, db, _, ctx = world
    record_id = record_of(db)["id"]

    service.reject(record_id)

    with pytest.raises(Exception):  # ClaimLost か ApprovalNotPossible
        service.approve(ctx, record_id)
    assert server.datetimes == {}
    assert record_of(db)["state"] == "complete"


def test_an_invalidated_record_cannot_be_approved(world):
    server, service, db, _, ctx = world
    db.execute(
        "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = 'グループが変わった'",
        ("2026-08-17T00:00:00+00:00",),
    )
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, record_of(db)["id"])
    assert server.datetimes == {}


def test_a_failed_approval_goes_back_to_waiting(world, monkeypatch):
    """書き換えたか分からないまま complete にしない."""
    server, service, db, _, ctx = world
    from mediaferry.adapters.immich import ImmichClient, ImmichUnavailable

    def boom(*args, **kwargs):
        raise ImmichUnavailable("PUT /api/assets/asset-1 が 503")

    monkeypatch.setattr(ImmichClient, "set_date_time_original", boom)
    with pytest.raises(ImmichUnavailable):
        service.approve(ctx, record_of(db)["id"])

    row = record_of(db)
    assert row["state"] == "awaiting_datetime_approval"
    assert row["claim_job_id"] is None


def test_approving_without_an_asset_id_is_refused(world):
    server, service, db, _, ctx = world
    db.execute("UPDATE upload_record SET remote_asset_id = NULL")
    with pytest.raises(ApprovalNotPossible):
        service.approve(ctx, record_of(db)["id"])
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_upload_approval.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mediaferry.jobs.approvals'`）

- [ ] **Step 3: 最小実装**

`app/src/mediaferry/jobs/approvals.py`:

```python
"""承認待ちの解消（§9.10「承認待ちの解消」）.

`pre_existing` / `unknown` の資産は、別経路で既にアップロードされ、ユーザが
手動で時刻を修正済みかもしれない。**承認を得てから書き戻す。**

却下も用意する。無いと、既に正しい日時が入っている資産について「補正不要」と
判断しても承認待ちを消せず、一覧に残り続ける。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ..adapters.immich import ImmichClient
from ..clock import now_iso
from ..db.connection import immediate
from ..core.lease_pulse import with_lease_pulse
from ..db.jobs import JobContext, LeaseLost
from ..db.profiles import ProfileRegistry
from ..db.uploads import ClaimLost, UploadRepository
from .preflight import PreflightCache

WAITING = "awaiting_datetime_approval"


class ApprovalNotPossible(RuntimeError):
    """承認・却下できる状態ではない."""


class ApprovalService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        uploads: UploadRepository,
        destinations,  # noqa: ANN001 - DestinationRepository
        registry: ProfileRegistry,
        open_client: Callable[[sqlite3.Row], ImmichClient],
        preflight: PreflightCache,
    ) -> None:
        self._conn = conn
        self._uploads = uploads
        self._destinations = destinations
        self._registry = registry
        self._open_client = open_client
        self._preflight = preflight

    def approve(self, ctx: JobContext, record_id: str) -> None:
        """撮影日時を書き戻してから `complete` にする.

        **claim を取ってから外部へ触る。** 取らないと、同時に走った却下が
        `complete` を commit した後にリモートを変更しうる。
        """
        row = self._waiting(record_id)
        if row["remote_asset_id"] is None:
            raise ApprovalNotPossible("リモートの資産 ID が分からない")
        media = self._conn.execute(
            "SELECT captured_at FROM media_file WHERE id = ?", (row["media_file_id"],)
        ).fetchone()
        revision = self._destinations.revision(row["destination_revision_id"])
        # 別のライブラリの資産の日時を書き換えない。
        self._preflight.assert_target(revision["id"])
        # awaiting → fixing_datetime を CAS で取る。ここで負けたら却下が先。
        self._uploads.claim_for_approval(record_id, ctx.job_id, ctx.lease_token)
        settled = False
        try:
            self._uploads.prepare_side_effect(ctx, record_id, "fixing_datetime")
            with self._open_client(revision) as client:
                # **PUT も pulse で囲む。** 遅い相手だと 60 秒を超え、claim が
                # 切れて「リモートは変更済みなのに commit できない」状態になる。
                with_lease_pulse(
                    ctx,
                    lambda: client.set_date_time_original(
                        row["remote_asset_id"], media["captured_at"]
                    ),
                    also=lambda: self._uploads.extend_claim(record_id, ctx.lease_token),
                    ownership_errors=(LeaseLost, ClaimLost),
                )
            self._uploads.finish_owned(
                ctx, record_id, "complete", expect_state="fixing_datetime"
            )
            settled = True
        finally:
            if not settled:
                # 書き換えたかどうか分からない。承認待ちへ戻して人に見せる。
                self._uploads.release_from_approval(record_id, ctx.lease_token)

    def reject(self, record_id: str) -> None:
        """**リモートを一切変更せずに** `complete` にする."""
        self._waiting(record_id)
        self._settle(record_id)

    # ------------------------------------------------------------------
    def _waiting(self, record_id: str) -> sqlite3.Row:
        row = self._uploads.get(record_id)
        if row is None:
            raise ApprovalNotPossible(f"レコード {record_id} が無い")
        if row["state"] != WAITING:
            raise ApprovalNotPossible(f"承認待ちではない（{row['state']}）")
        if row["invalidated_at"] is not None:
            # 無効化されたレコードの日時を書き換えない（§10 の多重防御）。
            raise ApprovalNotPossible(f"無効化されている: {row['invalidated_reason']}")
        return row

    def _settle(self, record_id: str) -> None:
        """claim を持たない行なので、状態だけを動かす."""
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'complete', updated_at = ?"
                " WHERE id = ? AND state = ?",
                (now_iso(), record_id, WAITING),
            )
            if updated.rowcount != 1:
                raise ApprovalNotPossible(f"レコード {record_id} は既に動いている")
```

`UploadRepository` に承認用の 2 メソッドを足す:

```python
    def claim_for_approval(self, record_id: str, job_id: str, token: str,
                           lease_seconds: int = 60) -> None:
        """`awaiting_datetime_approval` → `fixing_datetime` を CAS で取る.

        却下と競合したら 0 行になる（先に `complete` へ倒れている）。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'fixing_datetime', claim_job_id = ?,"
                " claim_token = ?, claim_expires_at = ?, updated_at = ?"
                " WHERE id = ? AND state = 'awaiting_datetime_approval'"
                "   AND invalidated_at IS NULL AND claim_job_id IS NULL",
                (job_id, token, _expiry(lease_seconds), now_iso(), record_id),
            )
            if updated.rowcount != 1:
                raise ClaimLost(f"レコード {record_id} は承認できる状態ではない")

    def release_from_approval(self, record_id: str, token: str) -> None:
        """承認の途中で降りる. 承認待ちへ戻して人に見せる."""
        with contextlib.suppress(ClaimLost):
            self._cas(
                record_id,
                token,
                "state = 'awaiting_datetime_approval', claim_job_id = NULL,"
                " claim_token = NULL, claim_expires_at = NULL",
                (),
            )
```

`db/uploads.py` の冒頭に `import contextlib` を足す。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_upload_approval.py -q`
Expected: PASS（7 件）

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `approve` が日時を書かない | `test_approving_writes_the_capture_time_and_completes` |
| `reject` が日時を書く | `test_rejecting_changes_nothing_remote` |
| `_waiting` の状態判定を消す | `test_a_record_that_is_not_waiting_cannot_be_approved` |
| `row is None` の判定を消す | `test_an_unknown_record_is_refused` |
| `approve` の preflight を消す | `test_approving_re_checks_where_the_revision_points` |
| `reject` にも preflight を掛ける | `test_rejecting_does_not_need_the_remote` |
| `remote_asset_id is None` の判定を消す | `test_approving_without_an_asset_id_is_refused` |
| `claim_for_approval` を呼ばずに PUT する | `test_a_rejection_that_won_the_race_stops_the_approval` |
| `_waiting` の `invalidated_at` の判定を消す | `test_an_invalidated_record_cannot_be_approved` |
| 失敗時に `release_from_approval` を呼ばない | `test_a_failed_approval_goes_back_to_waiting`（`fixing_datetime` + claim のまま残る） |
| `_settle` の `state = ?` 条件を消す | **落ちない**（単一スレッドのテストでは競合しない）。`BEGIN IMMEDIATE` の中の CAS は、承認ジョブと却下が同時に動く場合の保険。構造的にテスト不能な変異として記録する |

- [ ] **Step 6: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/jobs/approvals.py app/tests/test_upload_approval.py
git commit -m "feat(mediaferry): let the user approve or refuse a datetime fix"
```

---

### Task 12: 中断したアップロードの回収と無効化

**Files:**
- Modify: `app/src/mediaferry/jobs/reconcile.py`
- Modify: `app/src/mediaferry/db/uploads.py`
- Test: `app/tests/test_reconciler.py`（追記）

**Interfaces:**
- Produces:
  - `ReconcileReport.uploads_released: int` / `ReconcileReport.uploads_invalidated: int`
  - `UploadRepository.release_interrupted() -> int`
  - `UploadRepository.invalidate_stale() -> int`
  - `UploadRepository.invalidate_old_epoch(destination_id: str, current_epoch: int, reason: str) -> int`
  - `DestinationRepository.purge_superseded_credentials(destination_id: str) -> int`

**なぜ要るか:** 進行中（`checking` 〜 `fixing_datetime`）のまま落ちたレコードは、
claim が残ったままなので**期限が切れるまで誰も触れない**。起動時に claim を外し、
`needs_recheck` へ落とす（**サーバ側の成否が不明**なので `pending` ではない）。

**多重防御（§10）:** グループが変わって根拠が成立しなくなったレコードを、
起動時にまとめて無効化する。claim 時の再評価と合わせて二重に防ぐ。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_reconciler.py` に追記:

```python
def _an_upload_record(db, state, **over):
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
    from mediaferry.db.uploads import UploadRepository

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home", base_url="http://immich.invalid:2283", public_url=None, secret="k",
        identity=RemoteIdentity(remote_user_id="user-a", server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    uploads.create_pairs([media_id], [destination_id])
    fields = {
        "state": state,
        "claim_job_id": "job-old",
        "claim_token": "tok-old",
        "claim_expires_at": "2999-01-01T00:00:00+00:00",
        "destination_revision_id": destinations.current(destination_id)["id"],
    }
    fields.update(over)
    assignment = ", ".join(f"{name} = ?" for name in fields)
    db.execute(f"UPDATE upload_record SET {assignment}", tuple(fields.values()))  # noqa: S608
    return db.execute("SELECT * FROM upload_record").fetchone()


def test_an_interrupted_upload_is_released_for_a_recheck(db, data_root):
    record = _an_upload_record(db, "uploading")

    report = _reconcile(db, data_root)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record["id"],)).fetchone()
    assert report.uploads_released == 1
    # サーバ側の成否が不明なので pending ではない。
    assert row["state"] == "needs_recheck"
    assert (row["claim_job_id"], row["claim_token"], row["claim_expires_at"]) == (None, None, None)


def test_a_finished_upload_is_left_alone(db, data_root):
    record = _an_upload_record(
        db, "complete", claim_job_id=None, claim_token=None, claim_expires_at=None
    )

    report = _reconcile(db, data_root)

    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record["id"],)).fetchone()
    assert report.uploads_released == 0
    assert row["state"] == "complete"


def test_a_waiting_upload_keeps_waiting(db, data_root):
    record = _an_upload_record(
        db, "awaiting_datetime_approval", claim_job_id=None, claim_token=None,
        claim_expires_at=None,
    )
    _reconcile(db, data_root)
    row = db.execute("SELECT * FROM upload_record WHERE id = ?", (record["id"],)).fetchone()
    assert row["state"] == "awaiting_datetime_approval"


def test_a_record_whose_grounds_are_gone_is_invalidated(db, data_root):
    """derived の生成元が現行と一致しなくなったレコードを止める."""
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
    from mediaferry.db.uploads import UploadRepository

    from .test_selection import a_derived, a_group, a_pair

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home", base_url="http://immich.invalid:2283", public_url=None, secret="k",
        identity=RemoteIdentity(remote_user_id="user-a", server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    group_id = a_group(db, profile, members, output_id=output_id)
    uploads.create_pairs([output_id], [destination_id])
    # 構成ファイルが差し替わって digest が合わなくなった。
    db.execute("UPDATE media_file SET sha1 = 'edited' WHERE id = ?", (members[0][0],))

    report = _reconcile(db, data_root)

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert report.uploads_invalidated == 1
    assert row["invalidated_at"] is not None
    assert group_id in row["invalidated_reason"] or "グループ" in row["invalidated_reason"]


def test_a_healthy_record_is_not_invalidated(db, data_root):
    _an_upload_record(db, "pending", claim_job_id=None, claim_token=None, claim_expires_at=None)

    report = _reconcile(db, data_root)

    assert report.uploads_invalidated == 0
    assert db.execute("SELECT invalidated_at FROM upload_record").fetchone()[0] is None
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest app/tests/test_reconciler.py -q`
Expected: FAIL（`AttributeError: 'ReconcileReport' object has no attribute 'uploads_released'`）

- [ ] **Step 3: 最小実装**

`UploadRepository` に足す:

```python
    def release_interrupted(self) -> int:
        """進行中のまま残ったレコードを `needs_recheck` へ落とす.

        **`pending` ではない。** `uploading` で落ちた場合、サーバ側で成功して
        いるかもしれない。次回 `checking` から照合し直せば二重にはならない。
        起動時に呼ぶので、走っているジョブは既に倒れている。
        """
        marks = ", ".join("?" * len(ACTIVE_STATES))
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET state = 'needs_recheck', claim_job_id = NULL,"
                " claim_token = NULL, claim_expires_at = NULL, updated_at = ?"
                f" WHERE state IN ({marks})",  # noqa: S608
                (now_iso(), *ACTIVE_STATES),
            )
            return updated.rowcount

    def invalidate_stale(self) -> int:
        """根拠が成立しなくなった未完了のレコードを無効化する（§10 の多重防御）."""
        invalidated = 0
        for row in self._conn.execute(
            "SELECT * FROM upload_record WHERE invalidated_at IS NULL AND state <> 'complete'"
        ).fetchall():
            reason = self._stale_reason(row)
            if reason is None:
                continue
            self._conn.execute(
                "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?,"
                " updated_at = ? WHERE id = ?",
                (now_iso(), reason, now_iso(), row["id"]),
            )
            invalidated += 1
        return invalidated

    def _stale_reason(self, row: sqlite3.Row) -> str | None:
        """グループに紐づく根拠だけを見る. 宛先の有効・無効は claim 時に見る."""
        if row["merge_group_id"] is None:
            return None
        media = self._conn.execute(
            "SELECT * FROM media_file WHERE id = ?", (row["media_file_id"],)
        ).fetchone()
        if media is None:
            return f"メディア {row['media_file_id']} が無い"
        if media["role"] == "derived" and not group_is_current(
            self._conn, self._registry, row["merge_group_id"], media["id"]
        ):
            return f"グループ {row['merge_group_id']} が現在の構成と一致しない"
        return self._check_rule(row, media)
```

`jobs/reconcile.py`:

```python
@dataclass
class ReconcileReport:
    ...
    merges_blocked: int = 0
    uploads_released: int = 0
    uploads_invalidated: int = 0
    credentials_purged: int = 0
    ...
```

```python
    def run(self) -> ReconcileReport:
        report = ReconcileReport()
        self._store.sweep_interrupted()
        self._recover_staging(report)
        self._settle_merges(report)
        self._settle_uploads(report)
        self._sync_missing(report)
        self._collect_orphans(report)
        self._clean_job_dirs(report)
        return report

    def _settle_uploads(self, report: ReconcileReport) -> None:
        """中断したアップロードの claim を外し、根拠が消えた行を無効化する.

        `_settle_merges` の後に走らせる。そこでグループの状態が確定するので、
        「今のグループの状態」で根拠を評価できる。

        **旧 epoch の sweep と旧鍵の破棄もここで行う。** どちらも宛先の編集時に
        1 度は走るが、**その直後に落ちた場合に取り残される**（理由の無い pending が
        永久に残り、旧鍵は次の編集まで消えない）。起動時にもう一度均す。
        """
        if self._uploads is None or self._destinations is None:
            return
        report.uploads_released = self._uploads.release_interrupted()
        report.uploads_invalidated = self._uploads.invalidate_stale()
        for row in self._destinations.list_destinations(include_archived=True):
            current = self._destinations.get_current_or_none(row["id"])
            if current is None:
                continue
            report.uploads_invalidated += self._uploads.invalidate_old_epoch(
                row["id"], current["target_epoch"], "宛先の向き先が変わった"
            )
            report.credentials_purged += self._destinations.purge_superseded_credentials(
                row["id"]
            )
```

`Reconciler.__init__` に **keyword-only** で
`uploads: UploadRepository | None = None` と
`destinations: DestinationRepository | None = None` を足し、**片方だけ渡したら
起動時に落とす**。

```python
    def __init__(
        self,
        conn: sqlite3.Connection,
        data_root: Path,
        publisher: ArtifactPublisher,
        store: JobStore,
        *,
        uploads: UploadRepository | None = None,
        destinations: DestinationRepository | None = None,
    ) -> None:
        # **黙って skip しない。** 片方だけ渡す配線ミスをすると、claim の解放も
        # 旧 epoch の sweep も旧鍵の破棄も、何も言わずに行われなくなる。
        if (uploads is None) != (destinations is None):
            raise ValueError("uploads と destinations は組で渡す")
        ...
```

既存の呼び出し 4 か所（位置引数 4 つ）はそのまま通る。`app.py` は
マスター鍵がある場合だけ両方を渡す。

`DestinationRepository` に `get_current_or_none(destination_id)` を足す
（現行リビジョンが無い宛先で例外にしない）:

```python
    def get_current_or_none(self, destination_id: str) -> sqlite3.Row | None:
        try:
            return self.current(destination_id)
        except DestinationNotFound:
            return None
```

- [ ] **Step 7-2: 起動時の配線を固定するテスト**

```python
def test_startup_purges_superseded_keys_and_sweeps_old_epochs(db, data_root):
    """編集の直後に落ちても、次の起動で均される."""
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
    from mediaferry.db.uploads import UploadRepository

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    destination_id = destinations.create(
        name="home", base_url="http://immich.invalid:2283", public_url=None, secret="k1",
        identity=RemoteIdentity(remote_user_id="user-a", server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    media_id = a_media_file(db, (profile.profile_id, profile.revision_id))
    uploads.create_pairs([media_id], [destination_id])
    old_credential = destinations.current(destination_id)["credential_id"]
    # 編集はしたが、その後の後始末が走る前に落ちた状態を作る。
    destinations.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None, secret="k2",
        identity=RemoteIdentity(remote_user_id="user-b", server_instance_id=None),
    )
    db.execute("UPDATE upload_record SET invalidated_at = NULL, invalidated_reason = NULL")

    report = Reconciler(
        db, data_root, ArtifactPublisher(db, data_root, StubProbe()), JobStore(db),
        uploads=uploads, destinations=destinations,
    ).run()

    assert report.uploads_invalidated == 1
    assert report.credentials_purged == 1
    assert db.execute(
        "SELECT secret_encrypted FROM destination_credential WHERE id = ?", (old_credential,)
    ).fetchone()[0] is None
```

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest app/tests/test_reconciler.py app/tests/test_crash_consistency.py -q`
Expected: PASS

**`_reconcile` ヘルパに `uploads` を渡すよう直す**（`test_reconciler.py` の既存の
ヘルパ）:

```python
def _reconcile(db, data_root):
    import os

    from mediaferry.core.crypto import SecretBox
    from mediaferry.db.credentials import CredentialStore
    from mediaferry.db.destinations import DestinationRepository
    from mediaferry.db.uploads import UploadRepository

    publisher = ArtifactPublisher(db, data_root, StubProbe())
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    return Reconciler(
        db, data_root, publisher, JobStore(db), uploads=uploads, destinations=destinations
    ).run()
```

- [ ] **Step 5: epoch を進めた宛先の未 claim 項目を破棄する**

§8 が「未 claim のキュー項目は、`target_epoch` が据え置きなら新リビジョンで続行、
**epoch が進んだなら破棄して理由を記録する**」と定めている。claim は epoch で
絞るので旧 epoch の行は取られないが、**理由が無いまま `pending` に残り続ける**。

テスト（`app/tests/test_upload_claim.py` に追記）:

```python
def test_records_from_an_old_epoch_are_invalidated_with_a_reason(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    destinations.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2",
        identity=RemoteIdentity(remote_user_id="user-b", server_instance_id=None),
    )
    epoch = destinations.current(destination_id)["target_epoch"]

    assert uploads.invalidate_old_epoch(destination_id, epoch, "向き先が変わった") == 1

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["invalidated_at"] is not None
    assert row["invalidated_reason"] == "向き先が変わった"


def test_records_of_the_current_epoch_are_left_alone(db, world):
    _, destinations, destination_id, uploads = world
    a_pending(db, world)
    epoch = destinations.current(destination_id)["target_epoch"]
    assert uploads.invalidate_old_epoch(destination_id, epoch, "x") == 0


def test_a_completed_record_from_an_old_epoch_stays_as_history(db, world):
    """旧 epoch の記録は監査履歴として残す（§8）."""
    _, destinations, destination_id, uploads = world
    record = a_pending(db, world)
    db.execute(
        "UPDATE upload_record SET state = 'complete', destination_revision_id = ? WHERE id = ?",
        (destinations.current(destination_id)["id"], record["id"]),
    )
    destinations.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2",
        identity=RemoteIdentity(remote_user_id="user-b", server_instance_id=None),
    )
    epoch = destinations.current(destination_id)["target_epoch"]

    assert uploads.invalidate_old_epoch(destination_id, epoch, "向き先が変わった") == 0
    assert uploads.get(record["id"])["invalidated_at"] is None
```

実装（`UploadRepository`）:

```python
    def invalidate_old_epoch(self, destination_id: str, current_epoch: int, reason: str) -> int:
        """epoch を進めた宛先の、旧 epoch の未完了レコードを破棄する（§8）.

        **`complete` は残す。** 旧 epoch の記録は監査履歴として意味がある。
        claim は epoch で絞るので送られはしないが、理由が無いまま `pending` で
        残ると、利用者から見て「いつまでも送られない項目」になる。
        """
        with immediate(self._conn):
            updated = self._conn.execute(
                "UPDATE upload_record SET invalidated_at = ?, invalidated_reason = ?,"
                " updated_at = ? WHERE destination_id = ? AND target_epoch < ?"
                "   AND invalidated_at IS NULL AND state <> 'complete'",
                (now_iso(), reason, now_iso(), destination_id, current_epoch),
            )
            return updated.rowcount
```

- [ ] **Step 6: 参照されなくなった資格情報を破棄する**

§12.3 が「**参照中のジョブが無くなった旧 revision は `secret_encrypted` を消し、
`purged_at` を立てる**」と定めている。`destination_revision` は不変なので、
Task 2 の `purge_unreferenced`（どのリビジョンからも参照されていないもの）だけでは
**永久に何も消えない**。現行でないリビジョンの資格情報を、進行中の
`upload_record` が無くなった時点で消す。

テスト（`app/tests/test_destination_repository.py` に追記）:

```python
def test_a_superseded_key_is_purged_when_nothing_is_in_flight(repo, db):
    destination_id = a_destination(repo)
    old_credential = repo.current(destination_id)["credential_id"]
    repo.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2", identity=USER_A,
    )

    assert repo.purge_superseded_credentials(destination_id) == 1

    row = db.execute(
        "SELECT secret_encrypted, purged_at, key_fingerprint FROM destination_credential"
        " WHERE id = ?", (old_credential,)
    ).fetchone()
    assert row["secret_encrypted"] is None
    assert row["purged_at"] is not None
    # 監査には指紋と作成時刻を残す。
    assert row["key_fingerprint"]


def test_a_key_still_used_by_a_running_job_is_kept(repo, db):
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    destination_id = a_destination(repo)
    old_revision = repo.current(destination_id)
    media_id = _a_media_file(db)
    db.execute(
        "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
        " selection_rule, origin, claim_job_id, claim_token, claim_expires_at,"
        " destination_revision_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'uploading', 'default', 'unknown', 'job-1', 'tok-1',"
        " '2999-01-01T00:00:00+00:00', ?, ?, ?)",
        (new_id(), destination_id, old_revision["target_epoch"], media_id,
         old_revision["id"], now_iso(), now_iso()),
    )
    repo.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2", identity=USER_A,
    )

    assert repo.purge_superseded_credentials(destination_id) == 0


def test_a_key_needed_by_a_pending_approval_is_kept(repo, db):
    """承認待ちのレコードは、その版の鍵で日時を書き戻す（Task 11）."""
    from mediaferry.clock import now_iso
    from mediaferry.ids import new_id

    destination_id = a_destination(repo)
    old_revision = repo.current(destination_id)
    media_id = _a_media_file(db)
    db.execute(
        "INSERT INTO upload_record (id, destination_id, target_epoch, media_file_id, state,"
        " selection_rule, origin, remote_asset_id, destination_revision_id,"
        " created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'awaiting_datetime_approval', 'default', 'pre_existing',"
        " 'asset-1', ?, ?, ?)",
        (new_id(), destination_id, old_revision["target_epoch"], media_id,
         old_revision["id"], now_iso(), now_iso()),
    )
    repo.add_revision(
        destination_id, base_url="http://immich.invalid:2283", public_url=None,
        secret="key-2", identity=USER_A,
    )

    assert repo.purge_superseded_credentials(destination_id) == 0


def test_the_current_key_is_never_purged(repo, db):
    destination_id = a_destination(repo)
    assert repo.purge_superseded_credentials(destination_id) == 0
    assert repo.secret_of(repo.current(destination_id)["id"]) == "key-1"


def _a_media_file(db):
    from mediaferry.db.profiles import ProfileRegistry

    from .test_schema_artifacts import a_media_file

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    return a_media_file(db, (profile.profile_id, profile.revision_id))
```

実装（`DestinationRepository`）:

```python
    def purge_superseded_credentials(self, destination_id: str) -> int:
        """現行でないリビジョンの資格情報を、使い終わっていれば消す（§12.3）.

        `destination_revision` は不変なので、リビジョンから参照が外れることは
        ない。**「進行中の `upload_record` がそのリビジョンを指していないこと」**を
        使い終わりの条件にする。版管理したまま旧鍵を持ち続けると、ローテートしても
        漏洩面が減らない。
        """
        marks = ", ".join("?" * len(_IN_FLIGHT))
        rows = self._conn.execute(
            "SELECT r.credential_id AS credential_id FROM destination_revision r"
            " JOIN upload_destination d ON d.id = r.destination_id"
            " WHERE r.destination_id = ? AND r.id <> d.current_revision_id"
            "   AND NOT EXISTS (SELECT 1 FROM upload_record u"
            f"                  WHERE u.destination_revision_id = r.id AND u.state IN ({marks}))",  # noqa: S608
            (destination_id, *_IN_FLIGHT),
        ).fetchall()
        purged = 0
        for row in rows:
            purged += self._credentials.purge(row["credential_id"])
        return purged
```

`CredentialStore` に 1 件版を足す（Task 2 の `purge_unreferenced` は残す）:

```python
    def purge(self, credential_id: str) -> int:
        """1 件の暗号文を消す. 既に消えていれば 0 を返す."""
        with immediate(self._conn):
            purged = self._conn.execute(
                "UPDATE destination_credential SET secret_encrypted = NULL, purged_at = ?"
                " WHERE id = ? AND secret_encrypted IS NOT NULL",
                (now_iso(), credential_id),
            )
            return purged.rowcount
```

`db/destinations.py` の先頭に足す:

```python
# そのリビジョンの鍵がまだ要る状態。**承認待ちを必ず含める。**
# 含めないと、宛先を編集した直後に「承認に要る旧鍵」を消してしまい、
# 承認画面は残るのに永久に承認できないレコードができる。
_IN_FLIGHT = (
    "checking",
    "uploading",
    "asset_known",
    "tagging",
    "fixing_datetime",
    "awaiting_datetime_approval",
)
```

**呼び出しは Task 13 の `PATCH /destinations/{id}`（新リビジョンを作った直後）と、
起動時の reconciliation に置く。** 進行中のジョブが終わってから消えるように、
両方から呼ぶ。

- [ ] **Step 7-3: 配線ミスを起動時に落とすテスト**

```python
def test_the_reconciler_refuses_a_half_wired_pair(db, data_root):
    """片方だけ渡すと、回収がすべて黙って skip される（気づけない）."""
    import pytest

    from mediaferry.db.uploads import UploadRepository

    with pytest.raises(ValueError):
        Reconciler(
            db, data_root, ArtifactPublisher(db, data_root, StubProbe()), JobStore(db),
            uploads=UploadRepository(db, ProfileRegistry(db), None),
        )
```

- [ ] **Step 8: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| 片方だけでも動くようにする（`ValueError` を消す） | `test_the_reconciler_refuses_a_half_wired_pair` |
| `release_interrupted` を `pending` へ落とす | `test_an_interrupted_upload_is_released_for_a_recheck` |
| `invalidate_old_epoch` の `target_epoch < ?` を消す | `test_records_of_the_current_epoch_are_left_alone` |
| `invalidate_old_epoch` の `state <> 'complete'` を消す | `test_a_completed_record_from_an_old_epoch_stays_as_history` |
| `purge_superseded_credentials` の `r.id <> d.current_revision_id` を消す | `test_the_current_key_is_never_purged` |
| `NOT EXISTS (...)` の進行中の判定を消す | `test_a_key_still_used_by_a_running_job_is_kept` |
| `_IN_FLIGHT` から `awaiting_datetime_approval` を外す | `test_a_key_needed_by_a_pending_approval_is_kept` |
| `ACTIVE_STATES` の絞り込みを外す | `test_a_finished_upload_is_left_alone` / `test_a_waiting_upload_keeps_waiting` |
| claim の 3 欄を消さない | `test_an_interrupted_upload_is_released_for_a_recheck`（`0004` の CHECK でも落ちる） |
| `invalidate_stale` の `group_is_current` を消す | `test_a_record_whose_grounds_are_gone_is_invalidated` |
| `invalidate_stale` が健全な行も無効化する | `test_a_healthy_record_is_not_invalidated` |
| `state <> 'complete'` を消す | `test_a_finished_upload_is_left_alone`（送信済みが無効化される） |
| `_settle_uploads` の `invalidate_old_epoch` / `purge_superseded_credentials` を消す | `test_startup_purges_superseded_keys_and_sweeps_old_epochs` |
| `_settle_uploads` を `_settle_merges` の前に置く | **落ちない**。テストのグループは reconciliation で状態が変わらない。**`merging` のまま残ったグループの derived を対象にするテストを Task 14 の統合で見る**（そこでは `_settle_merges` が `merged` へ倒してから評価する必要がある） |

- [ ] **Step 9: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/jobs/reconcile.py app/src/mediaferry/db/uploads.py \
        app/src/mediaferry/db/destinations.py app/src/mediaferry/db/credentials.py \
        app/tests/test_reconciler.py app/tests/test_upload_claim.py \
        app/tests/test_destination_repository.py
git commit -m "feat(mediaferry): settle uploads that were interrupted"
```

---

### Task 13: API とワイヤリング

**Files:**
- Create: `app/src/mediaferry/api/routes_destinations.py`
- Create: `app/src/mediaferry/api/routes_uploads.py`
- Modify: `app/src/mediaferry/api/jobs_wiring.py`
- Modify: `app/src/mediaferry/api/app.py`
- Modify: `app/src/mediaferry/api/deps.py`
- Test: `app/tests/test_api_destinations.py`
- Test: `app/tests/test_api_uploads.py`

**Interfaces（§11 のうち Phase 3 の分）:**

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/destinations` | 一覧。**API キーは返さない** |
| POST | `/destinations` | 接続を検証してから作成。同じアカウントを指す宛先は警告 |
| PATCH | `/destinations/{id}` | 検証してから新リビジョン。`same_library` が要るときは 409 |
| POST | `/destinations/{id}/verify` | 現行リビジョンの向き先を確認する |
| POST | `/destinations/{id}/archive` | 保管（物理削除しない） |
| POST | `/destinations/{id}/upload` | この宛先の `upload` ジョブを開始 |
| POST | `/destinations/{id}/recheck` | 状態の再確認ジョブを開始（`mode=recheck`） |
| POST | `/uploads` | `media_ids × destination_ids` を pair に展開 |
| GET | `/uploads` | レコード一覧（`destination_id` / `state` で絞る） |
| POST | `/uploads/{id}/retry` | `failed` → `pending` |
| POST | `/uploads/{id}/requeue` | **リモートから消えた** `complete` を `pending` へ戻す |
| POST | `/uploads/{id}/approve` / `/reject` | 承認（ジョブを立てる）・却下（同期） |

- `JobWorld.run_upload(ctx, conn) -> None`

**API キーはリクエストの本文でだけ受け取り、応答には決して出さない**（§12.3）。
読み出しの API は作らない。

**マスター鍵が未設定で転送先が 1 件でもあれば起動を拒否する**（§12.3）。
`app.py` の起動手順に足す。

- [ ] **Step 1: 失敗するテストを書く（転送先）**

`app/tests/test_api_destinations.py`:

```python
import base64
import os

import pytest

from mediaferry.db.connection import Database

from .fake_immich import API_KEY, FakeImmich


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("MEDIAFERRY_SECRET_KEY", base64.b64encode(os.urandom(32)).decode())


@pytest.fixture
def api_db(client, data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


def a_body(immich, **over):
    """アプリは差し替え無しで fake へ接続する（`base_url` がループバックの実 URL）."""
    body = {"name": "home", "base_url": immich.url, "api_key": API_KEY}
    body.update(over)
    return body


def test_creating_a_destination_verifies_the_connection(secret_env, immich, client):
    response = client.post("/api/destinations", json=a_body(immich))
    assert response.status_code == 200
    body = response.json()
    assert body["remote_user_id"] == immich.user_id
    assert body["warnings"] == []


def test_the_api_key_never_comes_back(secret_env, immich, client):
    client.post("/api/destinations", json=a_body(immich))
    listed = client.get("/api/destinations").json()["destinations"]
    assert API_KEY not in str(listed)
    assert "api_key" not in str(listed)


def test_a_wrong_key_is_refused_and_stores_nothing(secret_env, immich, client, api_db):
    response = client.post("/api/destinations", json=a_body(immich, api_key="wrong"))
    assert response.status_code == 502
    assert api_db.execute("SELECT count(*) FROM upload_destination").fetchone()[0] == 0


def test_an_unusable_url_is_a_400(secret_env, immich, client):
    assert client.post("/api/destinations", json=a_body(immich, base_url="javascript:x")).status_code == 400


def test_a_second_destination_on_the_same_account_is_warned(secret_env, immich, client):
    client.post("/api/destinations", json=a_body(immich))
    body = client.post("/api/destinations", json=a_body(immich, name="vpn")).json()
    assert body["warnings"]


def test_rotating_the_key_keeps_the_epoch(secret_env, immich, client, api_db):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(f"/api/destinations/{destination_id}", json={"api_key": API_KEY})
    assert response.status_code == 200
    epochs = [
        row[0] for row in api_db.execute("SELECT target_epoch FROM destination_revision")
    ]
    assert epochs == [1, 1]


@pytest.fixture
def second_immich():
    """別ホストに見える 2 台目（ポートが違えば `_host_of` は別ホストと見なす）."""
    from .fake_immich import FakeImmich

    server = FakeImmich()
    server.start()
    yield server
    server.stop()


def test_a_changed_host_needs_an_answer(secret_env, immich, second_immich, client):
    """**到達できる 2 台目を使う。** 届かない URL だと検証で 502 になり、
    epoch の判断まで到達しない."""
    second_immich.user_id = immich.user_id  # 同じユーザを指したまま経路だけ変える
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(
        f"/api/destinations/{destination_id}",
        json={"base_url": second_immich.url, "api_key": API_KEY},
    )
    assert response.status_code == 409
    assert "same_library" in response.json()["detail"]


def test_renaming_does_not_create_a_revision(secret_env, immich, client, api_db):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    assert client.patch(
        f"/api/destinations/{destination_id}", json={"name": "family"}
    ).status_code == 200
    assert api_db.execute("SELECT count(*) FROM destination_revision").fetchone()[0] == 1
    assert api_db.execute("SELECT name FROM upload_destination").fetchone()[0] == "family"


def test_a_failed_verification_does_not_disable_the_destination(secret_env, immich, client,
                                                                api_db):
    """検証に失敗した編集は、どの欄も反映しない（§12.3）."""
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(
        f"/api/destinations/{destination_id}",
        json={"enabled": False, "base_url": "http://unreachable.invalid:2283",
              "api_key": API_KEY},
    )
    assert response.status_code == 502
    assert api_db.execute("SELECT enabled FROM upload_destination").fetchone()[0] == 1


def test_the_answer_can_be_given(secret_env, immich, second_immich, client, api_db):
    second_immich.user_id = immich.user_id
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    response = client.patch(
        f"/api/destinations/{destination_id}",
        json={"base_url": second_immich.url, "api_key": API_KEY, "same_library": False},
    )
    assert response.status_code == 200
    assert api_db.execute(
        "SELECT max(target_epoch) FROM destination_revision"
    ).fetchone()[0] == 2


def test_advancing_the_epoch_invalidates_the_queued_records(secret_env, immich, client, api_db):
    """epoch が進んだら、旧 epoch の未 claim 項目は理由付きで破棄する（§8）."""
    from mediaferry.db.profiles import ProfileRegistry

    from .test_schema_artifacts import a_media_file

    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    profile = ProfileRegistry(api_db).current("dji-osmo")
    media_id = a_media_file(api_db, (profile.profile_id, profile.revision_id))
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    immich.user_id = "someone-else"  # 別アカウントへ向き替える

    body = client.patch(
        f"/api/destinations/{destination_id}", json={"api_key": API_KEY}
    ).json()

    assert body["target_epoch"] == 2
    assert body["invalidated_records"] == 1
    row = api_db.execute("SELECT invalidated_reason FROM upload_record").fetchone()
    assert row["invalidated_reason"]


def test_verifying_reports_where_it_points(secret_env, immich, client):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    body = client.post(f"/api/destinations/{destination_id}/verify").json()
    assert body["matches"] is True

    immich.user_id = "someone-else"
    body = client.post(f"/api/destinations/{destination_id}/verify").json()
    assert body["matches"] is False


def test_archiving_takes_it_out_of_the_list(secret_env, immich, client):
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    assert client.post(f"/api/destinations/{destination_id}/archive").status_code == 200
    assert client.get("/api/destinations").json()["destinations"] == []


def test_a_destination_needs_a_master_key(immich, client):
    """`SECRET_KEY` が無ければ作らせない（§12.3）."""
    assert client.post("/api/destinations", json=a_body(immich)).status_code == 400


def test_starting_up_with_destinations_but_no_key_is_refused(
    secret_env, immich, client, data_root, broker, monkeypatch
):
    from mediaferry.api.app import create_app

    client.post("/api/destinations", json=a_body(immich))
    monkeypatch.delenv("MEDIAFERRY_SECRET_KEY")
    with pytest.raises(RuntimeError):
        from fastapi.testclient import TestClient

        with TestClient(create_app(broker_factory=lambda: broker)):
            pass
```

- [ ] **Step 2: 失敗するテストを書く（アップロード）**

`app/tests/test_api_uploads.py`:

```python
import base64
import hashlib
import os

import pytest

from mediaferry.db.connection import Database
from mediaferry.db.profiles import ProfileRegistry

from .fake_immich import API_KEY, FakeImmich
from .test_api_destinations import a_body, secret_env  # noqa: F401
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"


@pytest.fixture
def api_db(client, data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    yield conn
    conn.close()


@pytest.fixture
def world(secret_env, immich, client, api_db, data_root):  # noqa: F811
    destination_id = client.post("/api/destinations", json=a_body(immich)).json()["id"]
    profile = ProfileRegistry(api_db).current("dji-osmo")
    directory = data_root / "library" / "dji-osmo" / "DCIM"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "A.MP4").write_bytes(PAYLOAD)
    media_id = a_media_file(
        api_db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
    )
    return immich, destination_id, media_id, api_db


def test_uploads_are_created_per_pair(world, client):
    _, destination_id, media_id, api_db = world

    body = client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    ).json()

    assert [pair["result"] for pair in body["pairs"]] == ["created"]
    assert api_db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 1


def test_an_unknown_media_id_rejects_the_request(world, client):
    _, destination_id, _, api_db = world
    response = client.post(
        "/api/uploads", json={"media_ids": ["nope"], "destination_ids": [destination_id]}
    )
    assert response.status_code == 400
    assert api_db.execute("SELECT count(*) FROM upload_record").fetchone()[0] == 0


def test_the_records_can_be_listed_and_filtered(world, client):
    _, destination_id, media_id, _ = world
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    body = client.get(f"/api/uploads?destination_id={destination_id}&state=pending").json()
    assert [row["state"] for row in body["records"]] == ["pending"]
    assert body["records"][0]["media_file_id"] == media_id


def test_a_failed_record_can_be_retried(world, client):
    _, destination_id, media_id, api_db = world
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    api_db.execute("UPDATE upload_record SET state = 'failed'")

    assert client.post(f"/api/uploads/{record_id}/retry").status_code == 200

    row = api_db.execute("SELECT state, selection_rule FROM upload_record").fetchone()
    assert row["state"] == "pending"
    # 再試行は「なぜ最初に送信を許可したか」を変えない（§8）。
    assert row["selection_rule"] == "default"


def test_retrying_something_that_is_not_failed_is_a_409(world, client):
    _, destination_id, media_id, api_db = world
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    assert client.post(f"/api/uploads/{record_id}/retry").status_code == 409


def _an_awaiting_record(client, api_db, destination_id, media_id):
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    revision_id = api_db.execute(
        "SELECT current_revision_id FROM upload_destination"
    ).fetchone()[0]
    api_db.execute(
        "UPDATE upload_record SET state = 'awaiting_datetime_approval',"
        " remote_asset_id = 'asset-1', destination_revision_id = ?",
        (revision_id,),
    )
    return record_id


def test_rejecting_completes_without_touching_the_remote(world, client):
    server, destination_id, media_id, api_db = world
    record_id = _an_awaiting_record(client, api_db, destination_id, media_id)

    assert client.post(f"/api/uploads/{record_id}/reject").status_code == 200

    assert api_db.execute("SELECT state FROM upload_record").fetchone()[0] == "complete"
    assert server.datetimes == {}


def test_approving_enqueues_a_job_that_owns_the_side_effect(world, client):
    """承認は同期で PUT せず、claim を取れるジョブとして走らせる（Task 11）."""
    import json

    server, destination_id, media_id, api_db = world
    record_id = _an_awaiting_record(client, api_db, destination_id, media_id)

    job_id = client.post(f"/api/uploads/{record_id}/approve").json()["job_id"]

    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["mode"] == "approve"
    assert params["upload_record_id"] == record_id
    # ジョブが走るまでリモートは変わらない。
    assert server.datetimes == {}


def test_approving_something_that_is_not_waiting_is_a_409(world, client):
    _, destination_id, media_id, api_db = world
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    assert client.post(f"/api/uploads/{record_id}/approve").status_code == 409


def test_a_vanished_asset_can_be_sent_again(world, client):
    """再確認で「リモートに存在しない」と分かったものだけ送り直せる（§9.10）."""
    _, destination_id, media_id, api_db = world
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    revision_id = api_db.execute(
        "SELECT current_revision_id FROM upload_destination"
    ).fetchone()[0]
    api_db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = NULL,"
        " remote_checked_at = '2026-08-17T00:00:00+00:00', destination_revision_id = ?",
        (revision_id,),
    )

    assert client.post(f"/api/uploads/{record_id}/requeue").status_code == 200

    assert api_db.execute("SELECT state FROM upload_record").fetchone()[0] == "pending"


def test_a_healthy_complete_record_cannot_be_requeued(world, client):
    """送信済みのものを、確認もせずに送り直させない."""
    _, destination_id, media_id, api_db = world
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )
    record_id = api_db.execute("SELECT id FROM upload_record").fetchone()[0]
    revision_id = api_db.execute(
        "SELECT current_revision_id FROM upload_destination"
    ).fetchone()[0]
    api_db.execute(
        "UPDATE upload_record SET state = 'complete', remote_asset_id = 'asset-1',"
        " remote_checked_at = '2026-08-17T00:00:00+00:00', destination_revision_id = ?",
        (revision_id,),
    )
    assert client.post(f"/api/uploads/{record_id}/requeue").status_code == 409


def test_starting_an_upload_enqueues_a_job_for_that_destination(world, client, api_db):
    _, destination_id, media_id, _ = world
    client.post(
        "/api/uploads", json={"media_ids": [media_id], "destination_ids": [destination_id]}
    )

    job_id = client.post(f"/api/destinations/{destination_id}/upload").json()["job_id"]

    import json

    params = json.loads(
        api_db.execute("SELECT params_json FROM job WHERE id = ?", (job_id,)).fetchone()[0]
    )
    assert params["destination_id"] == destination_id
    assert params["mode"] == "send"
    # **秘密を params に入れない。**
    assert API_KEY not in json.dumps(params)


def test_a_recheck_is_the_same_job_type_with_another_mode(world, client, api_db):
    _, destination_id, _, _ = world
    job_id = client.post(f"/api/destinations/{destination_id}/recheck").json()["job_id"]
    import json

    row = api_db.execute("SELECT type, params_json FROM job WHERE id = ?", (job_id,)).fetchone()
    assert row["type"] == "upload"
    assert json.loads(row["params_json"])["mode"] == "recheck"
```

- [ ] **Step 3: 失敗を確認する**

Run: `uv run pytest app/tests/test_api_destinations.py app/tests/test_api_uploads.py -q`
Expected: FAIL（404。ルータがまだ無い）

- [ ] **Step 4: 最小実装**

`app/src/mediaferry/api/deps.py` に足す（`SecretBox` の解決）:

```python
def secret_box(
    app_state: AppState = Depends(state),  # noqa: B008
    conn: sqlite3.Connection = Depends(conn),  # noqa: B008
) -> SecretBox:
    """マスター鍵から `SecretBox` を作る. 未設定なら 400 で断る（§12.3）.

    `SettingsService` は接続を要求するので、`deps.conn` と同じ依存を使う。
    """
    settings = SettingsService(conn, app_state.env).snapshot()
    if settings.secret_key is None:
        raise HTTPException(
            status_code=400,
            detail="MEDIAFERRY_SECRET_KEY が未設定。転送先の API キーを保存できない",
        )
    return SecretBox(settings.secret_key)
```

**依存の名前が衝突する。** `deps.py` は既に `conn` という関数を持つので、
`Depends(conn)` は自己参照になる。実装では `def secret_box(app_state=Depends(state),
connection=Depends(conn))` のように**引数名を変える**か、モジュール内で
`_conn = conn` と別名を作ってから使う。

`app/src/mediaferry/api/routes_destinations.py`:

```python
"""転送先プロファイル（§11 / §12.3）.

**API キーは本文で受け取るだけ。応答には決して出さない。** 読み出しの API も
作らない。接続の検証に成功した設定だけを保存する。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..adapters.immich import ImmichClient, ImmichError
from ..core.destinations.urls import EndpointRejected
from ..db.credentials import CredentialStore
from ..db.destinations import (
    DestinationNotFound,
    DestinationRepository,
    EpochDecisionRequired,
    RemoteIdentity,
)
from ..db.jobs import JobStore
from .deps import conn as get_conn
from .deps import secret_box as get_box

router = APIRouter()


@router.get("/destinations")
def list_destinations(conn=Depends(get_conn), box=Depends(get_box)) -> dict[str, Any]:  # noqa: ANN001, B008
    repo = _repo(conn, box)
    return {"destinations": [_view(repo, row) for row in repo.list_destinations()]}


@router.post("/destinations")
def create_destination(
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    repo = _repo(conn, box)
    base_url, public_url, api_key = _fields(body)
    identity = _verify(base_url, api_key)
    try:
        destination_id = repo.create(
            name=body["name"], base_url=base_url, public_url=public_url,
            secret=api_key, identity=identity,
        )
    except EndpointRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": destination_id,
        "remote_user_id": identity.remote_user_id,
        "warnings": repo.same_account_warnings(identity, exclude_id=destination_id),
    }


@router.patch("/destinations/{destination_id}")
def edit_destination(
    destination_id: str,
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    repo = _repo(conn, box)
    current = _found(repo, destination_id)
    unknown = set(body) - {"name", "enabled", "base_url", "public_url", "api_key",
                           "same_library"}
    if unknown:
        raise HTTPException(status_code=400, detail=f"知らない欄: {sorted(unknown)}")
    if set(body) <= {"name", "enabled"}:
        # 接続に関わらない編集は、検証もリビジョンも要らない。
        repo.rename_or_toggle(destination_id, name=body.get("name"),
                              enabled=body.get("enabled"))
        return {"id": destination_id}
    base_url = body.get("base_url", current["base_url"])
    public_url = body.get("public_url", current["public_url"])
    api_key = body.get("api_key")
    if api_key is None:
        # 鍵を変えない編集でも、保存には可逆な値が要る。
        api_key = repo.secret_of(current["id"])
    identity = _verify(base_url, api_key)
    try:
        repo.add_revision(
            destination_id, base_url=base_url, public_url=public_url, secret=api_key,
            identity=identity, same_library=body.get("same_library"),
        )
    except EpochDecisionRequired as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{exc}。same_library を true か false で指定する",
        ) from exc
    except EndpointRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    current = repo.current(destination_id)
    # epoch の破棄は `add_revision` が同じトランザクションで済ませている（Task 3）。
    invalidated = _uploads_of(conn, box).invalidate_old_epoch(
        destination_id, current["target_epoch"], "宛先の向き先が変わった"
    )
    # 参照が絶えた旧鍵を消す。ローテートしても漏洩面が減らないままにしない（§12.3）。
    repo.purge_superseded_credentials(destination_id)
    return {
        "id": destination_id,
        "target_epoch": current["target_epoch"],
        "invalidated_records": invalidated,
        "warnings": repo.same_account_warnings(identity, destination_id),
    }


@router.post("/destinations/{destination_id}/verify")
def verify_destination(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    repo = _repo(conn, box)
    current = _found(repo, destination_id)
    identity = _verify(current["base_url"], repo.secret_of(current["id"]))
    return {
        "remote_user_id": identity.remote_user_id,
        "recorded_user_id": current["remote_user_id"],
        "matches": identity.remote_user_id == current["remote_user_id"],
    }


@router.post("/destinations/{destination_id}/archive")
def archive_destination(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    repo = _repo(conn, box)
    _found(repo, destination_id)
    repo.archive(destination_id)
    return {"status": "ok"}


@router.post("/destinations/{destination_id}/upload")
def start_upload(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    return _enqueue(conn, _repo(conn, box), destination_id, "send")


@router.post("/destinations/{destination_id}/recheck")
def start_recheck(
    destination_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    return _enqueue(conn, _repo(conn, box), destination_id, "recheck")


# ----------------------------------------------------------------------
def _repo(conn, box) -> DestinationRepository:  # noqa: ANN001
    return DestinationRepository(conn, CredentialStore(conn, box))


def _uploads_of(conn, box):  # noqa: ANN001, ANN202
    from ..db.profiles import ProfileRegistry
    from ..db.uploads import UploadRepository

    return UploadRepository(conn, ProfileRegistry(conn), _repo(conn, box))


def _fields(body: dict[str, Any]) -> tuple[str, str | None, str]:
    try:
        return body["base_url"], body.get("public_url"), body["api_key"]
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"{exc} が要る") from exc


def _verify(base_url: str, api_key: str) -> RemoteIdentity:
    """接続を検証し、向き先を観測する. 失敗した設定は保存しない."""
    try:
        with ImmichClient(base_url, api_key) as client:
            body = client.users_me()
    except EndpointRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImmichError as exc:
        # 502。こちらの要求は正しく、相手に届かないか拒まれている。
        raise HTTPException(status_code=502, detail=f"転送先に接続できない: {exc}") from exc
    return RemoteIdentity(remote_user_id=body.get("id"), server_instance_id=None)


def _found(repo: DestinationRepository, destination_id: str):  # noqa: ANN202
    row = repo.get(destination_id)
    if row is None or row["archived_at"] is not None:
        raise HTTPException(status_code=404, detail="その転送先は無い")
    try:
        return repo.current(destination_id)
    except DestinationNotFound as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _enqueue(conn, repo: DestinationRepository, destination_id: str, mode: str):  # noqa: ANN001, ANN202
    _found(repo, destination_id)
    # **params に秘密を入れない**（画面と SSE に出る）。
    return {
        "job_id": JobStore(conn).enqueue(
            "upload", {"destination_id": destination_id, "mode": mode}
        )
    }


def _view(repo: DestinationRepository, row) -> dict[str, Any]:  # noqa: ANN001
    current = repo.current(row["id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "base_url": current["base_url"],
        "public_url": current["public_url"],
        "remote_user_id": current["remote_user_id"],
        "target_epoch": current["target_epoch"],
        "revision": current["revision"],
        "verified_at": current["verified_at"],
    }
```

`app/src/mediaferry/api/routes_uploads.py`:

```python
"""アップロードのレコード（§11）."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..adapters.immich import ImmichClient
from ..clock import now_iso
from ..db.connection import immediate
from ..db.credentials import CredentialStore
from ..db.destinations import DestinationRepository
from ..db.profiles import ProfileRegistry
from ..db.uploads import UploadRepository, UploadRequestInvalid
from ..db.jobs import JobStore
from ..jobs.approvals import ApprovalNotPossible, ApprovalService
from ..jobs.preflight import PreflightCache
from .deps import conn as get_conn
from .deps import secret_box as get_box

router = APIRouter()


@router.post("/uploads")
def create_uploads(
    body: dict[str, Any] = Body(...),  # noqa: B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """`media_ids × destination_ids` を pair に展開する（§10）."""
    try:
        pairs = _uploads(conn, box).create_pairs(
            body.get("media_ids", []), body.get("destination_ids", [])
        )
    except UploadRequestInvalid as exc:
        # 何も作らずに全体を拒否する。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "pairs": [
            {
                "media_file_id": pair.media_file_id,
                "destination_id": pair.destination_id,
                "result": pair.result,
                "upload_record_id": pair.record_id,
                "reason": pair.reason,
            }
            for pair in pairs
        ]
    }


@router.get("/uploads")
def list_uploads(
    destination_id: str | None = None,
    state: str | None = None,
    limit: int = 200,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, Any]:
    rows = _uploads(conn, box).list_records(destination_id, state, limit)
    return {"records": [_view(row) for row in rows]}


@router.post("/uploads/{record_id}/retry")
def retry_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """`failed` → `pending` の明示操作. **`selection_rule` は変えない**（§8）."""
    uploads = _uploads(conn, box)
    if uploads.get(record_id) is None:
        raise HTTPException(status_code=404, detail="そのレコードは無い")
    with immediate(conn):
        updated = conn.execute(
            "UPDATE upload_record SET state = 'pending', claim_job_id = NULL,"
            " claim_token = NULL, claim_expires_at = NULL, updated_at = ?"
            " WHERE id = ? AND state = 'failed' AND invalidated_at IS NULL",
            (now_iso(), record_id),
        )
    if updated.rowcount != 1:
        raise HTTPException(status_code=409, detail="失敗した状態ではないので再試行できない")
    return {"status": "ok"}


@router.post("/uploads/{record_id}/requeue")
def requeue_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """リモートから消えた資産を、利用者の明示操作で送り直す（§9.10）.

    **自動では戻さない。** 対象は「再確認でサーバに無いと分かった `complete`」
    （`remote_asset_id IS NULL` かつ `remote_checked_at IS NOT NULL`）だけ。
    通常の `complete` は拒否する。
    """
    uploads = _uploads(conn, box)
    row = uploads.get(record_id)
    if row is None:
        raise HTTPException(status_code=404, detail="そのレコードは無い")
    reason = uploads.check_eligibility(row)
    if reason is not None:
        raise HTTPException(status_code=409, detail=f"送り直せない: {reason}")
    with immediate(conn):
        updated = conn.execute(
            "UPDATE upload_record SET state = 'pending', remote_is_trashed = NULL,"
            " updated_at = ? WHERE id = ? AND state = 'complete'"
            "   AND remote_asset_id IS NULL AND remote_checked_at IS NOT NULL"
            "   AND invalidated_at IS NULL",
            (now_iso(), record_id),
        )
    if updated.rowcount != 1:
        raise HTTPException(
            status_code=409, detail="リモートに存在しないと確認できたレコードだけ送り直せる"
        )
    return {"status": "ok"}


@router.post("/uploads/{record_id}/approve")
def approve_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """**承認はジョブとして実行する**（外部への副作用に所有権が要る。Task 11）."""
    uploads = _uploads(conn, box)
    row = uploads.get(record_id)
    if row is None:
        raise HTTPException(status_code=404, detail="そのレコードは無い")
    if row["state"] != "awaiting_datetime_approval":
        raise HTTPException(status_code=409, detail=f"承認待ちではない（{row['state']}）")
    if row["invalidated_at"] is not None:
        raise HTTPException(status_code=409, detail="無効化されている")
    with immediate(conn):
        # **同じレコードの承認ジョブを二重に積まない。** 積めてしまうと、
        # 1 本目が終わった後の残りが軒並み失敗として画面に並ぶ。
        active = conn.execute(
            "SELECT 1 FROM job WHERE type = 'upload'"
            "   AND status IN ('queued', 'running', 'cancelling')"
            "   AND params_json LIKE ?",
            (f'%"upload_record_id": "{record_id}"%',),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="この承認は既に実行待ち")
        job_id = JobStore(conn).enqueue(
            "upload",
            {
                "destination_id": row["destination_id"],
                "mode": "approve",
                "upload_record_id": record_id,
            },
        )
    return {"job_id": job_id}


@router.post("/uploads/{record_id}/reject")
def reject_upload(
    record_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    box=Depends(get_box),  # noqa: ANN001, B008
) -> dict[str, str]:
    """**却下はリモートに触らない**ので同期で終える（Task 11）."""
    destinations = DestinationRepository(conn, CredentialStore(conn, box))
    service = ApprovalService(
        conn, _uploads(conn, box), destinations, ProfileRegistry(conn),
        lambda revision: ImmichClient(revision["base_url"], destinations.secret_of(revision["id"])),
        PreflightCache(
            destinations,
            lambda revision: ImmichClient(
                revision["base_url"], destinations.secret_of(revision["id"])
            ),
        ),
    )
    try:
        service.reject(record_id)
    except ApprovalNotPossible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok"}


# ----------------------------------------------------------------------
def _uploads(conn, box) -> UploadRepository:  # noqa: ANN001
    destinations = DestinationRepository(conn, CredentialStore(conn, box))
    return UploadRepository(conn, ProfileRegistry(conn), destinations)


def _view(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "destination_id": row["destination_id"],
        "media_file_id": row["media_file_id"],
        "state": row["state"],
        "selection_rule": row["selection_rule"],
        "origin": row["origin"],
        "remote_asset_id": row["remote_asset_id"],
        "remote_is_trashed": bool(row["remote_is_trashed"]),
        "remote_checked_at": row["remote_checked_at"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "eligibility_reason": row["eligibility_reason"],
        "invalidated_at": row["invalidated_at"],
        "invalidated_reason": row["invalidated_reason"],
        "updated_at": row["updated_at"],
    }
```

`jobs_wiring.py` に足すハンドラ:

```python
    def run_upload(self, ctx: JobContext, conn: sqlite3.Connection) -> None:
        settings = SettingsService(conn, self._env).snapshot()
        if settings.secret_key is None:
            raise RuntimeError("MEDIAFERRY_SECRET_KEY が未設定なので転送先を開けない")
        destinations = DestinationRepository(conn, CredentialStore(conn, SecretBox(
            settings.secret_key
        )))
        uploads = UploadRepository(conn, ProfileRegistry(conn), destinations)

        def open_client(revision: sqlite3.Row) -> ImmichClient:
            return ImmichClient(
                revision["base_url"],
                destinations.secret_of(revision["id"]),
                settings.upload_timeout_seconds,
            )

        preflight = PreflightCache(destinations, open_client)
        destination_id = ctx.params["destination_id"]
        if ctx.params.get("mode") == "approve":
            # 承認は 1 件だけを扱う。外部副作用の所有権はジョブのリースが持つ。
            ApprovalService(
                conn, uploads, destinations, ProfileRegistry(conn), open_client, preflight
            ).approve(ctx, ctx.params["upload_record_id"])
            ctx.emit("info", "日時の補正を承認して書き戻した")
            return
        if ctx.params.get("mode") == "recheck":
            outcome = Rechecker(uploads, destinations, open_client, preflight).run(
                ctx, destination_id
            )
            ctx.emit(
                "info",
                f"再確認: {outcome.checked} 件 / ゴミ箱 {outcome.trashed} 件"
                f" / 消滅 {outcome.vanished} 件 / 復元 {outcome.restored} 件",
            )
            return
        uploader = Uploader(
            conn, uploads, destinations, ProfileRegistry(conn), settings.data_root,
            open_client, preflight, settings.upload_max_attempts,
        )
        outcome = uploader.run(ctx, destination_id)
        ctx.emit(
            "info",
            f"アップロード完了: 送信 {outcome.sent} 件 / 承認待ち {outcome.awaiting} 件"
            f" / 見送り {outcome.skipped} 件 / 失敗 {outcome.failed} 件",
        )
```

`app.py`:

```python
from .routes_destinations import router as destinations_router
from .routes_uploads import router as uploads_router
...
            ProfileRegistry(startup).sync_builtins()
            # **転送先が 1 件でもあってマスター鍵が無ければ起動しない**（§12.3）。
            _assert_master_key(startup, settings)
...
        runner.register("upload", world.run_upload)
...
    app.include_router(destinations_router, prefix="/api")
    app.include_router(uploads_router, prefix="/api")
```

```python
def _assert_master_key(conn: sqlite3.Connection, settings: Settings) -> None:
    """鍵が無いまま起動すると、資格情報を復号できないジョブが走る."""
    if settings.secret_key is not None:
        return
    count = conn.execute(
        "SELECT count(*) FROM upload_destination WHERE archived_at IS NULL"
    ).fetchone()[0]
    if count:
        raise RuntimeError(
            f"転送先が {count} 件あるが MEDIAFERRY_SECRET_KEY が未設定。"
            "鍵を与えるまで起動しない"
        )
```

`Reconciler` の呼び出しにも `UploadRepository` を渡す（Task 12）。鍵が無い場合は
`None` を渡して upload の回収だけを飛ばす。

- [ ] **Step 5: 通ることを確認する**

Run: `uv run pytest app/tests/test_api_destinations.py app/tests/test_api_uploads.py app/tests/test_api.py -q`
Expected: PASS

- [ ] **Step 6: 変異試験**

| 変異 | 落ちるべきテスト |
| --- | --- |
| `_verify` を呼ばずに保存する | `test_a_wrong_key_is_refused_and_stores_nothing` |
| `_verify` の失敗を 200 で返す | 同上 |
| 応答に `api_key` を含める | `test_the_api_key_never_comes_back` |
| `EpochDecisionRequired` を 500 のまま通す | `test_a_changed_host_needs_an_answer` |
| `same_library` を無視する | `test_the_answer_can_be_given` |
| `verify` の `matches` を常に真にする | `test_verifying_reports_where_it_points` |
| `invalidate_old_epoch` の呼び出しを消す | `test_advancing_the_epoch_invalidates_the_queued_records` |
| `purge_superseded_credentials` の呼び出しを消す | **落ちない**（API のテストは鍵の破棄を見ていない）。`test_destination_repository.py::test_a_superseded_key_is_purged_when_nothing_is_in_flight` がメソッド自体を固定している。呼び出し側の変異は検出できないものとして記録する |
| `archive` を無視する | `test_archiving_takes_it_out_of_the_list` |
| `secret_box` の未設定チェックを消す | `test_a_destination_needs_a_master_key` |
| `_assert_master_key` を消す | `test_starting_up_with_destinations_but_no_key_is_refused` |
| `UploadRequestInvalid` を 500 のまま通す | `test_an_unknown_media_id_rejects_the_request` |
| `retry` の `state = 'failed'` 条件を消す | `test_retrying_something_that_is_not_failed_is_a_409` |
| `retry` が `selection_rule` を書き換える | `test_a_failed_record_can_be_retried`（`selection_rule` を見る assert を入れてある。スキーマの trigger も `IntegrityError` にする） |
| `mode` を params に入れない | `test_a_recheck_is_the_same_job_type_with_another_mode` |
| `approve` を同期の PUT に戻す | `test_approving_enqueues_a_job_that_owns_the_side_effect` |
| `approve` の状態判定を消す | `test_approving_something_that_is_not_waiting_is_a_409` |
| `requeue` の `remote_asset_id IS NULL` 条件を消す | `test_a_healthy_complete_record_cannot_be_requeued` |
| `requeue` を消す（`retry` で兼ねる） | `test_a_vanished_asset_can_be_sent_again` |
| `reject` がリモートへ PUT する | `test_rejecting_completes_without_touching_the_remote` |
| params に API キーを入れる | `test_starting_an_upload_enqueues_a_job_for_that_destination` |

- [ ] **Step 7: コミット**

```bash
uv run ruff check . && uv run ruff format .
git add app/src/mediaferry/api app/tests/test_api_destinations.py app/tests/test_api_uploads.py
git commit -m "feat(mediaferry): expose destinations and uploads over the api"
```

---

### Task 14: 統合テストとドキュメント

**Files:**
- Create: `app/tests/test_upload_e2e.py`
- Create: `app/tests/test_immich_live.py`（`needs_immich`）
- Modify: `docs/design.md`
- Modify: `docs/HANDOFF.md`
- Modify: `README.md`
- Modify: `docs/phase3-plan.md`（この計画。実装との差分を書き戻す）

- [ ] **Step 1: 一連の流れを通すテストを書く**

`app/tests/test_upload_e2e.py`:

```python
import hashlib
import os

import pytest

from mediaferry.adapters.immich import ImmichClient
from mediaferry.core.crypto import SecretBox
from mediaferry.db.credentials import CredentialStore
from mediaferry.db.destinations import DestinationRepository, RemoteIdentity
from mediaferry.db.jobs import JobStore
from mediaferry.db.profiles import ProfileRegistry
from mediaferry.db.uploads import UploadRepository
from mediaferry.jobs.preflight import PreflightCache
from mediaferry.jobs.reconcile import Reconciler
from mediaferry.jobs.uploader import Uploader

from .fake_immich import API_KEY, FakeImmich
from .test_publisher import StubProbe
from .test_schema_artifacts import a_media_file

PAYLOAD = b"video-bytes"
CAPTURED = "2026-08-17T14:30:00+09:00"


@pytest.fixture
def world(db, data_root, immich):
    server = immich  # conftest のフィクスチャ（ループバックで listen している）
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    destinations = DestinationRepository(db, CredentialStore(db, SecretBox(os.urandom(32))))
    home = destinations.create(
        name="home", base_url=server.url, public_url=None, secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    family = destinations.create(
        name="family", base_url="http://family.invalid:2283", public_url=None, secret=API_KEY,
        identity=RemoteIdentity(remote_user_id=server.user_id, server_instance_id=None),
    )
    uploads = UploadRepository(db, ProfileRegistry(db), destinations)
    directory = data_root / "library" / "dji-osmo" / "DCIM"
    directory.mkdir(parents=True)
    (directory / "A.MP4").write_bytes(PAYLOAD)
    media_id = a_media_file(
        db, (profile.profile_id, profile.revision_id),
        rel_path="library/dji-osmo/DCIM/A.MP4",
        sha1=hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest(),
        size_bytes=len(PAYLOAD), captured_at=CAPTURED,
    )

    def open_client(revision):
        return ImmichClient(revision["base_url"], API_KEY)

    def a_job(destination_id):
        store = JobStore(db)
        store.enqueue("upload", {"destination_id": destination_id})
        return store.claim_next()

    def an_uploader():
        return Uploader(
            db, uploads, destinations, ProfileRegistry(db), data_root, open_client,
            PreflightCache(destinations, open_client),
        )

    return server, destinations, uploads, home, family, media_id, a_job, an_uploader


def test_one_media_goes_to_two_destinations_independently(world, db):
    server, _, uploads, home, family, media_id, a_job, an_uploader = world

    pairs = uploads.create_pairs([media_id], [home, family])
    assert [pair.result for pair in pairs] == ["created", "created"]

    an_uploader().run(a_job(home), home)

    states = {
        row["destination_id"]: row["state"]
        for row in db.execute("SELECT destination_id, state FROM upload_record")
    }
    # 片方だけ送っても、もう片方は未送信のまま独立している。
    assert states[home] == "complete"
    assert states[family] == "pending"

    an_uploader().run(a_job(family), family)

    states = {
        row["destination_id"]: row["state"]
        for row in db.execute("SELECT destination_id, state FROM upload_record")
    }
    assert states[family] == "complete"
    # 2 つ目の宛先は同じ fake を見ているので、重複として扱われる。
    assert len(server.uploads) == 1


def test_a_second_run_sends_nothing(world, db):
    _, _, uploads, home, _, media_id, a_job, an_uploader = world
    uploads.create_pairs([media_id], [home])
    an_uploader().run(a_job(home), home)

    outcome = an_uploader().run(a_job(home), home)

    assert (outcome.sent, outcome.failed) == (0, 0)


def test_an_interrupted_upload_is_recovered_at_startup(world, db, data_root, monkeypatch):
    server, destinations, uploads, home, _, media_id, a_job, an_uploader = world
    uploads.create_pairs([media_id], [home])
    ctx = a_job(home)

    # 送信の途中で落ちた（サーバ側の成否は不明）。
    def die(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(ImmichClient, "upload_asset", die)
    with pytest.raises(KeyboardInterrupt):
        an_uploader().run(ctx, home)
    assert db.execute("SELECT state FROM upload_record").fetchone()[0] == "needs_recheck"

    monkeypatch.undo()
    report = Reconciler(
        db, data_root, _publisher(db, data_root), JobStore(db),
        uploads=uploads, destinations=destinations,
    ).run()
    assert report.uploads_released == 0  # 既に needs_recheck へ落ちている

    an_uploader().run(a_job(home), home)

    row = db.execute("SELECT * FROM upload_record").fetchone()
    assert row["state"] == "complete"
    # 二重にアップロードしていない。
    assert len(server.uploads) == 1


def test_a_record_whose_group_changed_is_never_sent(world, db):
    server, _, uploads, home, _, _, a_job, an_uploader = world
    from .test_selection import a_derived, a_group, a_pair

    profile = ProfileRegistry(db).current("dji-osmo")
    members = a_pair(db, profile)
    output_id = a_derived(db, profile)
    a_group(db, profile, members, output_id=output_id)
    uploads.create_pairs([output_id], [home])
    # 構成ファイルが差し替わって digest が合わなくなった。
    db.execute("UPDATE media_file SET sha1 = 'edited' WHERE id = ?", (members[0][0],))

    outcome = an_uploader().run(a_job(home), home)

    assert outcome.skipped == 1
    assert server.uploads == []
    assert db.execute(
        "SELECT invalidated_at FROM upload_record WHERE media_file_id = ?", (output_id,)
    ).fetchone()[0] is not None


def test_a_group_settled_at_startup_changes_what_can_be_sent(world, db, data_root):
    """`_settle_merges` → `_settle_uploads` の順序を、実際の効果で確かめる.

    `merging` のまま残ったグループは起動時に `detected` へ戻る（出力が無い場合）。
    その member を対象にした `default` の pair は、**根拠が成立しなくなる**ので
    無効化されなければならない。順序が逆だと、グループが `merging` のままの状態で
    評価してしまい、この判定に至らない。
    """
    from .test_selection import a_group, a_pair

    _, destinations, uploads, home, _, _, a_job, _ = world
    profile = ProfileRegistry(db).current("dji-osmo")
    members = a_pair(db, profile)
    group_id = a_group(db, profile, members, status="failed", verification=None)
    # failed のグループの member として送信を許可する。
    uploads.create_pairs([members[0][0]], [home])
    # 実際には結合の途中だった（起動時に detected へ戻る）。
    db.execute("UPDATE merge_group SET status = 'merging' WHERE id = ?", (group_id,))

    report = Reconciler(
        db, data_root, _publisher(db, data_root), JobStore(db),
        uploads=uploads, destinations=destinations,
    ).run()

    assert report.merges_released == 1
    row = db.execute(
        "SELECT * FROM upload_record WHERE media_file_id = ?", (members[0][0],)
    ).fetchone()
    # 「結合できなかったグループの member」という根拠は、もう成立しない。
    assert row["invalidated_at"] is not None


def _publisher(db, data_root):
    from mediaferry.adapters.publisher import ArtifactPublisher

    return ArtifactPublisher(db, data_root, StubProbe())
```

Run: `uv run pytest app/tests/test_upload_e2e.py -q`
Expected: PASS（4 件）

- [ ] **Step 2: 実 Immich に当てるテストを書く（`needs_immich`）**

**タグと日時更新のエンドポイントは Phase 0 で実測していない**（Task 4 に記載）。
既定の `pytest` では走らないが、対象バージョンに当てて形を確かめる手段を用意する。

`app/tests/test_immich_live.py`:

```python
"""実 Immich に対する疎通確認.

環境変数 `MEDIAFERRY_TEST_IMMICH_URL` と `MEDIAFERRY_TEST_IMMICH_KEY` を与えて
`uv run pytest -m needs_immich` で走らせる。**作った資産は必ず消す。**

Phase 0 のプローブ（`spikes/immich_probe.py`）が確かめていない
「タグの作成・付与」と「日時の更新」をここで確かめる。
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid

import pytest

from mediaferry.adapters.immich import ImmichClient

pytestmark = pytest.mark.needs_immich


@pytest.fixture
def client():
    url = os.environ.get("MEDIAFERRY_TEST_IMMICH_URL")
    key = os.environ.get("MEDIAFERRY_TEST_IMMICH_KEY")
    if not url or not key:
        pytest.skip("MEDIAFERRY_TEST_IMMICH_URL / _KEY が要る")
    with ImmichClient(url, key) as client:
        yield client


def test_the_identity_is_readable(client):
    assert client.users_me()["id"]


def test_an_unknown_checksum_is_accepted(client):
    sha1 = hashlib.sha1(uuid.uuid4().bytes, usedforsecurity=False).hexdigest()
    assert client.bulk_upload_check([("k", sha1)])["k"].action == "accept"
    # base64 で送っていることを、応答の形と併せて確かめる。
    assert base64.b64encode(bytes.fromhex(sha1))


def test_the_whole_upload_path_works_against_a_real_server(client, tmp_path):
    """**upload → 照合 → タグ → 日時 → 後片付け**を実機で通す.

    タグと日時のエンドポイントは Phase 0 で実測していない（Task 4）。ここを
    通さないと、全部間違っていても Phase 3 の完了条件が PASS になる。

    **作ったものは必ず消す。** 消せなかったらテストは失敗させる（実ライブラリに
    ゴミを残さない）。**既存の資産には触らない**（毎回ユニークな中身を作る）。
    """
    import os

    payload = uuid.uuid4().bytes * 64  # 毎回ユニーク。既存資産と衝突しない
    path = tmp_path / f"mediaferry-test-{uuid.uuid4().hex[:8]}.jpg"
    path.write_bytes(_a_unique_jpeg(payload))
    sha1 = hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()
    tag_name = f"mediaferry-test-{uuid.uuid4().hex[:8]}"

    asset_id = None
    try:
        # 1. 未知のはず
        assert client.bulk_upload_check([("k", sha1)])["k"].action == "accept"

        # 2. アップロード（created が返ることが origin の根拠になる）
        uploaded = client.upload_asset(
            path,
            sha1_hex=sha1,
            device_asset_id=f"mediaferry:{uuid.uuid4().hex}",
            file_created_at="2026-08-17T14:30:00+00:00",
            file_modified_at="2026-08-17T14:30:00+00:00",
        )
        asset_id = uploaded.asset_id
        assert uploaded.status == "created"

        # 3. 照合で自分の資産が返る
        outcome = client.bulk_upload_check([("k", sha1)])["k"]
        assert outcome.action == "reject"
        assert outcome.asset_id == asset_id
        assert outcome.is_trashed is False

        # 4. タグの作成・再利用・付与
        tag_id = client.ensure_tag(tag_name)
        assert client.ensure_tag(tag_name) == tag_id
        client.tag_assets(tag_id, [asset_id])

        # 5. 日時の書き戻し
        client.set_date_time_original(asset_id, "2026-08-17T14:30:00+09:00")
    finally:
        # 後片付けの失敗も FAIL にする（実ライブラリにゴミを残さない）。
        _cleanup(os.environ["MEDIAFERRY_TEST_IMMICH_URL"],
                 os.environ["MEDIAFERRY_TEST_IMMICH_KEY"], asset_id, tag_name)


def _a_unique_jpeg(seed: bytes) -> bytes:
    """最小の有効な JPEG。中身は毎回変える（既存資産と重複させない）."""
    import io

    from PIL import Image  # type: ignore[import-not-found]

    image = Image.frombytes("RGB", (8, 8), (seed * 3)[: 8 * 8 * 3])
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _cleanup(url: str, key: str, asset_id: str | None, tag_name: str) -> None:
    """作った資産とタグを消す. **消せなければ送出する。**"""
    import httpx

    with httpx.Client(base_url=url, headers={"x-api-key": key}, timeout=60.0) as raw:
        if asset_id is not None:
            response = raw.request(
                "DELETE", "/api/assets", json={"ids": [asset_id], "force": True}
            )
            assert response.status_code < 400, f"資産を消せなかった: {response.status_code}"
        tags = raw.get("/api/tags").json()
        for tag in tags:
            if tag["name"] == tag_name:
                response = raw.delete(f"/api/tags/{tag['id']}")
                assert response.status_code < 400, f"タグを消せなかった: {response.status_code}"
```

**Pillow はテスト用の依存として `dev` グループに足す**（`app/pyproject.toml`）。
有効な JPEG を自前で組むより、既知のライブラリで作る方が「Immich が受理しない
形だった」という失敗の切り分けが楽になる。

**このテストで形が違っていたら、`adapters/immich.py` のそのメソッドだけを直す。**
呼び出し側は `ImmichClient` のメソッドしか触っていない。**`DELETE /api/assets` と
`DELETE /api/tags/{id}` も Phase 0 で実測していない**ので、後片付けが失敗したら
まずそこを疑う（`immich_probe.py` は資産の削除を確認済みなので、資産側は通る
見込み）。

Run: `uv run pytest -m needs_immich -q`（実 Immich がある環境でのみ）

- [ ] **Step 3: 全体を通す**

```bash
uv run pytest
uv run pytest -m needs_root
uv run ruff check .
uv run ruff format --check .
```

Expected: すべて PASS

- [ ] **Step 4: `docs/design.md` を直す**

1. §9.10 の末尾（「失敗は指数バックオフで…並列度は既定 2」の段落）に、実装で
   確定した事項を書き足す:

```markdown
**Phase 3 の実装では逐次に送る。** `UPLOAD_CONCURRENCY` は読まない。ジョブ内で
2 本の HTTP を並行させると、状態遷移の commit を別スレッドから行うことになり、
「DB 接続はスコープごとに 1 本」（§8）を保てない。並列度は、ワーカーを多重化する
Phase 4 で効かせる。

**`upload` ジョブは宛先ごとに 1 本立てる。** preflight（下記）をリビジョン単位で
共有でき、1 つの宛先の失敗が他の宛先の送信を巻き込まない。状態の再確認は
同じジョブ種別の `params.mode = "recheck"` で行う（`job.type` の CHECK を
書き換えるとテーブルの作り直しになるため）。

**巨大ファイルの送信中もリースと claim を延ばす。** 28 GiB の送信は 84.5 秒
（Phase 0 の実測）でリース（60 秒）より長い。送信は途中で止められないので、
別スレッドへ出して待つ側が heartbeat を打つ（`core/lease_pulse.py`）。

**「リモートに存在しない」は列を足さずに表す。** 再確認で `accept`（サーバに無い）が
返ったレコードは `remote_asset_id` を NULL にし、`state = 'complete'` かつ
`remote_asset_id IS NULL` かつ `remote_checked_at IS NOT NULL` で「消滅」を表す。
```

2. §11 の API 表に Phase 3 で足したものを反映する（`/destinations/{id}/verify`、
   `/destinations/{id}/archive`、`/destinations/{id}/upload`、
   `/destinations/{id}/recheck`、`/uploads/{id}/retry`、`/uploads/{id}/approve`、
   `/uploads/{id}/reject`）。

3. §20 の表で Phase 3 を **完了**にする。

4. §21 に節を足す:

```markdown
### Phase 3 の実装で確定した事項（実装を終えた日付を入れる）

| 判断 | 理由 |
| --- | --- |
| **アップロードは逐次に行う。`UPLOAD_CONCURRENCY` は Phase 4 まで効かない** | ジョブ内で並行させると状態遷移の commit を別スレッドから行うことになり、接続をスコープごとに 1 本に保てない |
| **`upload` ジョブは宛先ごとに 1 本。再確認は `params.mode`** | preflight をリビジョン単位で共有でき、失敗の影響範囲が宛先で閉じる。`job.type` の CHECK を書き換えるとテーブルの作り直しになる |
| **claim してから (a)(c) を評価し、満たさなければ `invalidated_at` で止める** | `selection_rule` ごとに見るテーブルが違うので CAS の条件式に書けない。評価の結果は「無効化」として残し、claim 時の再評価と多重防御を揃える |
| **`adopted_derived` の claim 条件は「採用されているか」** | 「まだ採用していない derived」にすると、採用して enqueue した瞬間に自分自身が条件を満たさず必ず拒否される |
| **送信中の中断は `needs_recheck`。`pending` ではない** | サーバ側で成功しているかもしれない。次回 `checking` から照合すれば二重にはならないが、自作の証明は失われるので `origin` は `unknown` のまま |
| **`origin` は `POST /api/assets` が `created` を返し、それを commit できたときだけ `created_by_us`** | 初回 `checking` が `accept` だったことは自作の証明にならない（間に別クライアントが割り込みうる） |
| **タグは追加のみ。`created_by_us` 以外は `tag_pre_existing` に従う** | 自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めたタグを付けない |
| **日時の補正は `created_by_us` のときだけ自動。それ以外は承認待ち** | 別経路で上がっていて、ユーザが手で直しているかもしれない |
| **却下は「リモートを変えずに complete」** | 却下が無いと、補正不要と判断しても承認待ちを消せない |
| **preflight はリビジョンごとに 1 回。失敗も覚える** | 毎 pair で叩くと 1000 件で 1000 回になる。失敗を覚えないと、止まった宛先へ何度も試す |
| **API キーは本文で受け取るだけ。応答にも `job.params_json` にも出さない** | 画面と SSE に出る経路を作らない（§12.3） |
| **転送先が 1 件でもあってマスター鍵が無ければ起動しない** | 復号できない資格情報でジョブが走ると、失敗の理由が分かりにくい形で溜まる |
| **復号できない資格情報は上書きしない** | `WrongKeyError` で鍵の取り違えと区別できる。上書きすると、正しい鍵を思い出しても戻せない |
| **`base_url` の変更で `remote_user_id` が同じなら、履歴を引き継ぐかをユーザに尋ねる** | DB を複製・復元した別ライブラリかもしれず、自動判定できない |
| **消滅した資産は自動で送り直さない** | 利用者が意図的に消したものを黙って戻さない |
```

- [ ] **Step 5: `docs/HANDOFF.md` を直す**

- §1 の表で Phase 3 を **完了**にし、検証状態のテスト件数を実測値に直す
- §3 に「Phase 3 で確定した契約」の表を足す（上の §21 を要約。Phase 4 が
  蒸し返さないように）
- §5「次にやること」を Phase 4（Web UI）の入口に書き換える。**Phase 4 で最初に
  やること**として「ワーカーの多重化（`UPLOAD_CONCURRENCY`）」「承認待ちの画面」
  「宛先ごとの状態バッジ」を挙げる
- §7「持ち越している判断」を更新する:
  - 実 Immich でのタグ・日時更新エンドポイントの確認（`test_immich_live.py`）
  - 5 パート連続録画（70 GiB 級）のアップロード（Phase 0 では 28.36 GiB まで）
  - `SECRET_KEY` のローテート（旧鍵と新鍵を与えて全件を再暗号化する手順は未実装）

- [ ] **Step 6: `README.md` に対応バージョンを書く**

§9.10 の末尾が「エンドポイントとフィールド名は Phase 0 で対象バージョンの
OpenAPI 定義から確定し、**対応バージョンを README に明記する**」と定めている。

```markdown
## 対応する Immich

Phase 0 で実測した **v3.1.0**（sourceCommit 8aa95c6）を対象にしている。
アップロードとチェックサム照合は実測済み、タグと日時更新は
`uv run pytest -m needs_immich` で確認する。
```

- [ ] **Step 7: この計画に差分を書き戻してコミット**

実装で計画から外れた判断、検出できなかった変異、追加したテストを、この
`docs/phase3-plan.md` の該当タスクへ書き戻す。

```bash
git add docs/ README.md app/tests/test_upload_e2e.py app/tests/test_immich_live.py
git commit -m "docs(mediaferry): record what phase 3 settled"
```

---

## Phase 3 の完了条件（§20）

> 実 Immich にアップロードでき、途中で落としても再開し、既存アセットを勝手に
> 変更しない。2 つの宛先へ同じメディアを送って独立に追跡できる。

| 条件 | 確かめ方 |
| --- | --- |
| 実 Immich にアップロードできる | `test_immich_live.py::test_the_whole_upload_path_works_against_a_real_server`（upload → 照合 → タグ → 日時 → 後片付けまで実機で通す）+ fake に対する `test_uploader.py` |
| 途中で落としても再開する | `test_upload_e2e.py::test_an_interrupted_upload_is_recovered_at_startup` |
| 既存アセットを勝手に変更しない | `test_uploader.py::test_an_asset_that_already_exists_is_not_uploaded_again`（承認待ちになり、日時を書かない） |
| 2 つの宛先を独立に追跡できる | `test_upload_e2e.py::test_one_media_goes_to_two_destinations_independently` |
| 向き先が変わったら送らない | `test_uploader.py::test_the_preflight_stops_everything_before_a_byte_is_sent` |
| API キーが漏れない | `test_api_destinations.py::test_the_api_key_never_comes_back`、`test_api_uploads.py` の params の assert |

## Phase 3 でやらないこと（意図的な除外）

| 項目 | いつ |
| --- | --- |
| アップロードの並列実行（`UPLOAD_CONCURRENCY`） | Phase 4（ワーカーの多重化と一緒に） |
| 承認待ちの差分表示、宛先ごとの状態バッジ、確認ダイアログ | Phase 4（画面） |
| SSE（`GET /events`） | Phase 4 |
| `SECRET_KEY` のローテート（全 credential の再暗号化） | Phase 5 |
| 手動でのグループ編集に伴う supersede と、それに連動する無効化 | Phase 4（Phase 3 は起動時の sweep と claim 時の再評価で守る） |
| `recompute_timestamps` / `deep_verify` ジョブ | 未定（`job.type` には入っている） |

## 実装の前に決めておくこと

**着手する前に codex のレビューを 2 巡通す。** Phase 2 では 1 巡目の blocker を
直した後の 2 巡目で、さらに blocker が 2 件出た。どちらも「直した箇所の周辺」
だった。**修正が新しい境界を作るので、そこをもう一度見せる。**

レビューで特に見てほしい点:

1. **claim と外部副作用の順序** — `advance` / `finish` の CAS が、HTTP の前後の
   どちらに置かれているか。`asset_known` を commit する前に落ちた場合の扱い
2. **`needs_recheck` へ落とす条件** — `BaseException` の捕捉で本当に十分か。
   `finally` にすべき経路が無いか
3. **preflight の粒度** — リビジョン単位で 1 回にしているが、長いジョブの途中で
   向き先が変わる可能性をどう扱うか
4. **秘密の扱い** — `reveal()` の戻り値がログ・例外・`repr` に載る経路が無いか
5. **`invalidate_stale` の副作用** — 起動時に走るので、誤って有効なレコードを
   無効化すると利用者が気づきにくい

依頼の作法は `docs/HANDOFF.md` §5「レビューの依頼先」にある。**先にコミットして
から、hash とファイル名を渡して読ませる。**

## レビュー記録

### 1 巡目（2026-08-18、codex。blocker 8 / major 6 / minor 1 + 補足 1）

実装着手の前に依頼した。**全件を反映した。** 逐次実行・宛先ごとに 1 ジョブ・
`params.mode` での再確認という 3 つの設計判断は妥当と確認された。

| # | 指摘 | 反映先 |
| --- | --- | --- |
| 1 [blocker] | 同期 `httpx.Client` に `ASGITransport` は使えない（`handle_async_request` しか無い）。fake Immich のテストが 1 つも立ち上がらない | Task 4（fake をループバックで listen する実 HTTP サーバに作り替え、`transport` 引数を廃止。`conftest.py` の `immich` フィクスチャで共有） |
| 2 [blocker] | `refuse()` が `state = 'checking'` のまま claim を外すので `0004` の CHECK で `IntegrityError` | Task 8（`state = 'pending'` も同じ CAS で戻す。無効化された行は claim 条件で弾かれる） |
| 3 [blocker] | `_cas` が token しか見ない。副作用の直前に `assert_lease` が無く、`extend_lease` は `cancelling` でも延ばすので、キャンセル後も 28 GiB の送信・タグ・日時変更が完走する | Task 8（`prepare_side_effect` を追加。`assert_lease` と CAS を 1 つの `BEGIN IMMEDIATE` に入れ、`claim_expires_at > now` と `invalidated_at IS NULL` と `expect_state` を全経路に） |
| 4 [blocker] | `with_lease_pulse` が `ClaimLost` を捕まえず、claim の延長に失敗すると送信スレッドを残して抜ける | Task 9（`ownership_errors` を呼び出し側から渡す。`core` は `db.uploads` を知らないままにする） |
| 5 [blocker] | `_one` の try が狭く、クライアント構築や資格情報の復号で落ちると `checking` + claim のまま回収不能 | Task 9（`_guarded` で claim 後の全経路を囲み、未決着で抜けたら必ず解放） |
| 6 [blocker] | リビジョンをジョブ開始時に固定すると、§8 の「未 claim は新リビジョンで続行」に反する。編集後の pair が旧 URL・旧鍵（purge 済みかもしれない）へ送られる | Task 8（`claim_next` が現行リビジョンを claim と同じトランザクションで解決）+ Task 5（preflight の成功判定に TTL） |
| 7 [blocker] | recheck が旧 epoch の履歴を現行ライブラリで照合し、`remote_asset_id` を上書きする | Task 10（`records_for_recheck` が現行 epoch だけを返す。上限も廃止） |
| 8 [blocker] | 承認に所有権が無く、却下と競合する。`_IN_FLIGHT` に `awaiting` が無いので、承認に要る旧鍵を purge する | Task 11（承認を `upload` ジョブの `mode="approve"` にし、`claim_for_approval` の CAS で所有してから PUT。却下は同期のまま）+ `_IN_FLIGHT` に `awaiting_datetime_approval` を追加 |
| 15 [blocker] | live テストが upload・タグ・日時更新を一度も実行していない。未実測と書いた 3 つが全部間違っていても完了条件が PASS になる | Task 14（upload → 照合 → タグ → 日時 → 後片付けまで実機で通す。後片付けの失敗も FAIL） |
| 9 [major] | 宛先の作成・編集が複数トランザクションに分裂し、現行リビジョンの無い宛先や孤立 credential が残りうる | Task 3（`store_locked` / `_write_revision` を呼び出し側のトランザクションの中で使う形にし、編集を 1 トランザクションに） |
| 10 [major] | 応答の検証が fail-open（件数・未知の action・`assetId` 欠落・未知の status）。本文を伴う upload の redirect でファイルが EOF から再送される | Task 4（`ImmichProtocolError` と全単射の検証。upload は `allow_redirect=False`） |
| 11 [major] | `ImmichRejected` に応答本文を載せており、相手が API キーを echo すると `last_error` として DB・API・画面へ流れる | Task 4（例外はメソッド・パス・ステータスのみ。echo する fake で回帰テスト） |
| 12 [major] | 旧鍵の purge と旧 epoch の sweep が起動時に配線されていない。編集直後に落ちると取り残される | Task 12（`_settle_uploads` で両方を実行。`credentials_purged` を report に追加）+ Task 3（epoch の破棄をリビジョン作成と同じトランザクションへ） |
| 13 [major] | 消滅した資産を `pending` へ戻す API が無い（`retry` は `failed` のみ） | Task 13（`POST /uploads/{id}/requeue`。対象は「再確認でサーバに無いと分かった `complete`」だけ） |
| 14 [major] | `remote_user_id = None` を「検証済みリビジョン」として保存できる | Task 3（`IdentityUnknown` で原子的に拒否。「missing identity が epoch を進める」テストを差し替え） |
| 16 [minor] | IPv6 が括弧無しで再構築され、不正ポートの `ValueError` が漏れる | Task 1（括弧付けと `EndpointRejected` への正規化） |
| 補足 | `test_an_expired_claim_can_be_taken_over` の fixture が CHECK 違反。期限切れの横取りを仕様にするか決める必要がある | Task 8（**横取りは起こらない**契約として明記。回収は起動時の reconciliation だけ。`claim_expires_at < now` は §8 の SQL を写した到達不能な保険として記録） |

**実測で確かめた指摘（鵜呑みにしていない）:**

- #1: `httpx 0.28.1` の `ASGITransport` に `handle_request` が無いことを確認
- #2 と補足: `0004` の CHECK 制約を読み、`refuse()` と fixture が違反することを確認

**退けた指摘はない。** 1 件だけ実装方針を変えて反映した: #4 の「`core` から
`ClaimLost` を捕まえる」は、`core/lease_pulse.py` が `db.uploads` を import する
ことになり層が逆転するので、**捕まえる例外の集合を呼び出し側から渡す形**にした
（意図は同じ）。

### 2 巡目（2026-08-18、codex。blocker 8 / major 5 / minor 1 + 補足）

**Phase 2 と同じく、1 巡目の修正が作った境界から新しい blocker が出た。**
指摘は 2 つの性質に分かれた。

**A. 文書内のコードが動かない（#1 #2 #7 #10 #14）** —— 未定義変数
（`prepare_side_effect` の `token`）、自己参照フィクスチャ（`server = immich` を
`def immich()` の中に書いた一括置換の副作用）、テストと実装の食い違い
（preflight を claim 後へ移したのにテストは `pending` を期待）、テストが前提の
状態を作れていない（`same_library=False` はホストが同じだと epoch を進めない）、
JSON の型検証漏れ。**いずれも `pytest` を 1 回回せば数秒で出る類**で、
計画が Markdown の中に完全なコードを持っていることの副作用である。

**B. 1 巡目の修正が開いた設計の穴（#3 #4 #5 #6 #8 #9 #11 #12 #13）** ——
契約を変える指摘。

| # | 指摘 | 反映先 |
| --- | --- | --- |
| 3 [blocker] | commit 側が claim しか見ない。HTTP 待機中にキャンセルされても `asset_known` / `complete` を書けて、タグと日時まで進む | Task 8（`advance_owned` / `finish_owned`。`ctx.assert_lease()` と strict CAS を 1 つの `BEGIN IMMEDIATE` に） |
| 4 [blocker] | pulse は `heartbeat` だけ呼ぶが、`extend_lease` は `cancelling` でも延ばす。28 GiB の送信中のキャンセルを検出できない | Task 9（pulse ごとに `ctx.assert_lease()` を先に呼ぶ） |
| 5 [blocker] | TTL 付き preflight がレコード先頭で 1 回だけ。送信が TTL を跨ぐと、タグと日時が別ライブラリへ飛びうる | Task 9（`_guard` を作り、**ネットワーク副作用ごとに** preflight + claim を確認。TTL 内はキャッシュが返るので通信は増えない） |
| 6 [major] | タグの guard が複数の副作用（`ensure_tag` の GET → POST、`tag_assets` の PUT）を 1 回で包んでいる | Task 4（`find_tag` / `create_tag` に分割）+ Task 9（変更を伴う呼び出しごとに guard） |
| 7 [blocker] | preflight を claim 後へ移したのに、失敗時の期待状態が旧設計のまま。確定的な失敗まで「成否不明」と表示する | Task 9（`_Progress.touched_remote` で境界を持ち、越える前は `pending`、越えた後だけ `needs_recheck`） |
| 8 [blocker] | 承認の PUT が pulse の外。60 秒を超えると claim が切れ、リモートは変更済みなのに commit できない | Task 11（PUT を `with_lease_pulse` で囲み、commit は `finish_owned`） |
| 9 [major] | 承認 API を連打すると重複ジョブを無制限に積める | Task 13（同じレコードの実行待ちジョブがあれば 409） |
| 11 [major] | `store_locked` / `_write_revision` がトランザクションを強制しない。`current` の読出しと版番号の決定が取引の外 | Task 3（`conn.in_transaction` を検査。読出しと計算も `BEGIN IMMEDIATE` の中へ） |
| 12 [major] | `Reconciler` に片方だけ渡すと、回収が全部黙って skip される | Task 12（keyword-only にして、組で渡さなければ `ValueError`） |
| 13 [major] | PATCH が `enabled` を別トランザクションで先に反映する。`name` を扱わない。host 変更のテストが届かない URL を使っている | Task 13（検証後に 1 トランザクション、`rename_or_toggle` を分離、2 台目の fake で host 変更を再現） |
| 14 [minor] | JSON の型を仮定しており、scalar や配列を返す相手で `AttributeError` になる | Task 4（`_as_object` / `_required_str` で `ImmichProtocolError` に正規化） |

**補足への対応:** `claim_expires_at` の strict 条件は「takeover 経路が無い」ことと
分けて、短いリースで検出できるテストを Task 8 に置いた
（`test_an_expired_claim_cannot_commit_a_side_effect`）。`_settle_merges` →
`_settle_uploads` の順序は、Task 14 に「起動時にグループが決着した結果、
`failed_group_member` の根拠が消える」ケースを足して実効で固定した。

**A については、計画の上でも 1 行で直る 4 件（#1 #2 #7 #10）を直したうえで、
残りは実装時の TDD で潰す方針にした。** 文書内のコードは実行も型検査もされない
ので、同じ類の欠陥はレビューを何巡回しても出続ける。**次はレビューではなく
実装へ進み、`pytest` が落とす。**

### 3 巡目（実装後に依頼する）

Task 1 から実装し、**動くコードの差分**を見せてレビューする。文書のレビューでは
拾えない層（実際の SQLite の挙動、httpx の実装差、CHECK 制約の実効）が、
そこで初めて確かめられる。
