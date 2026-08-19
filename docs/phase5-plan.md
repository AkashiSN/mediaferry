# mediaferry Phase 5（汎用化）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DJI 以外のカードを扱えるようにする。`generic-dcim` と `canon-eos` のビルトインを
足し、`timestamp.source: exif` を実装し、プロファイルを画面から編集できるようにする。
あわせて **`AUTO_IMPORT=trusted` を実際に効かせる** —— 信頼登録済みのカードを挿すと、
画面を開かなくても取り込みが始まる状態にする。

**Architecture:** 既存の層をそのまま使う（判断は `core/` の純粋関数、副作用は `adapters/` と
`db/`、長い処理は `jobs/`）。Phase 5 で足すのは **時間の軸で動く主体**（`VolumeWatcher`）と、
**プロファイルという設定の書き換え経路**（編集 API と画面、`recompute_timestamps`）である。
機種差はコードの分岐ではなく設定の差分として表す（§6）という原則を、DJI 以外で初めて
実際に使う。

**Tech Stack:** Python 3.12 / uv workspace / SQLite（WAL）/ FastAPI / httpx / exifread /
regex / pytest / ruff ・ React 19 + TypeScript + Vite / Playwright（受け入れのみ）

**Spec:** `docs/design.md`（正本。§6 デバイスプロファイル / §9.2 デバイス検出とボリューム判定 /
§11 API / §12.1 自動取り込みと信頼登録 / §13 画面 / §20 実装フェーズ）。前提は
`docs/HANDOFF.md`（特に §3 の「蒸し返さないこと」）、直前のフェーズは `docs/phase4-plan.md`。

---

## Phase 5 の範囲

| 入れる | 入れない |
| --- | --- |
| ビルトイン `generic-dcim` / `canon-eos` | PTP 接続 → **スコープ外と決定済み**（§2。カードリーダー経由の UMS のみ） |
| `timestamp.source: exif` の実装（`exifread`） | ffprobe の `creation_time` を第 4 の source にする → 下記「やらないこと」 |
| プロファイル編集 API（ビルトインは複製、新リビジョン、archive） | プロファイルの import / export → 作らない（YAGNI） |
| プロファイル編集 UI（設定画面） | プロファイルの構文を GUI で組み立てる（正規表現ビルダ等）→ 作らない |
| `recompute_timestamps` ジョブ（§6） | 既存データの再取り込み → しない（`captured_at` の再計算だけ） |
| **`AUTO_IMPORT=trusted` を効かせる**（`VolumeWatcher`） | **`subscribe`（netlink uevent）→ 入れない**（下記の判断） |
| 信頼登録 UX（デバイス画面。限界の明示、対象外の理由、自動取り込みの状態） | 自動アップロード → **しない**（§12.1「アップロードは常に手動」） |
| 複数デバイスの同時接続（受け入れ経路として通す） | ジョブの並列実行 → §19 リスク 5 のとおりキューで直列化のまま |
| **ブローカー接続の再接続**（常時ポーリングの前提） | |
| | **`UPLOAD_CONCURRENCY` の多重化 → Phase 6**（独立した設計課題。下記） |
| | **RAW / JPEG の Immich 上でのスタッキング → Phase 6**（API の実測結果は下記に残す） |

### `UPLOAD_CONCURRENCY` は Phase 6 へ送る

Phase 4 がこれを Phase 5 へ送ったが、ここでも扱わない。理由は Phase 4 の判断のまま:
現行の `JobRunner` は**全ジョブ種で共通の単一 worker** で `claim_next()` は type も宛先も
見ないため、同時実行数を上げると import / merge / scan まで並列になる。「宛先ごとに 1 本」を
保つには claim のトランザクションで宛先単位の排他が要り、Phase 3 で固めたリースと停止の
契約に触れる。**Phase 5 の完了条件（EOS 70D の SD カードを取り込める）は逐次実行で満たせる。**

Phase 5 は `JobRunner` と並ぶ 2 本目の長寿命タスク（`VolumeWatcher`）を足すので、
**停止の作法が 2 箇所になる**。多重化を同じフェーズに入れると停止の契約を同時に 3 方向から
触ることになり、レビューの焦点がぼける。

---

## 検出の起動源を `subscribe` にしなかった理由

設計 §11 は `subscribe`（uevent のストリーム）をプロトコルに載せており、§3 は
`DeviceMonitor`（netlink uevent の購読）を mountd のコンポーネントとして挙げている。
§4 の実測でも「コンテナ内で kernel uevent を受信できる」ことは確認済みで、
Phase 1 は「ポーリングで足りる」として送っただけだった。**それでも Phase 5 では
`list_volumes` のポーリングを採る。** 実装を読んで判断が変わった。

1. **`_observe` は安い。** `enumerate_volumes` は `/sys/class/block` を舐めて
   **`_is_usb` で絞ってから** `blkid` を掛ける。USB ブロックデバイスが無い間は
   **サブプロセスが 1 つも起きない**。カード 1 枚で `blkid` が 1〜2 回。イベント駆動で
   節約できるコストがほとんど無い
2. **高いのは `list_volumes` ではなく `refresh()`。** `VolumeService._probe` は判定のたびに
   `open_volume` で**実際にマウントし**、名前を走査してマニフェストを取り、閉じる
   （Phase 1 Task 23 の「判定は必ず開き直す」）。これを数秒ごとに回すと、カードを
   数秒おきにマウント／アンマウントし続ける。**これはイベント駆動でも同じ罠**で、
   `add` のたびに `refresh()` を呼ぶ設計なら何も変わらない
3. 分けるべきなのは**トリガの種類ではなく、安い変化検出と高い判定**だった。
   `list_volumes` が返す `generation` は「観測した集合の指紋が変わったときだけ進む」値で
   （`mountd/server.py::_observe`）、まさにこの用途に合う

`subscribe` を入れて得られる差は「ポーリング間隔ぶんの待ちが消える」ことだけで、代わりに
netlink の購読・接続の寿命管理・購読中の idle timeout の例外・縮退経路が乗る。
**この案件では割に合わない。**

なお **netlink の取りこぼしは黙って起きない**（受信バッファが溢れると `ENOBUFS` が返る）。
`subscribe` を選ばなかったのは取りこぼしを恐れたからではない。将来 uevent へ移す場合も、
mountd 側が `_observe` を呼んで `generation` を進める形は変わらないので、
**app 側の `VolumeWatcher` は「generation が進んだら `refresh()`」のまま使える。**

### `generation` の判定材料（`mountd/server.py::_observe` からの引き写し）

```python
volumes = self.lister()
fingerprint = tuple(sorted(
    (v.volume_key, v.fs_uuid, v.fs_type, v.size_bytes) for v in volumes
))
if fingerprint != self._last_fingerprint:
    self._last_fingerprint = fingerprint
    self._current_generation += 1
```

- `volume_key` は `major:minor`。`fs_uuid` / `fs_type` は `blkid -o export`、`size_bytes` は
  sysfs の `size` × 512
- **集合全体で 1 つの指紋、カウンタも 1 本。** 「どれが変わったか」は分からない
  （変わったら `refresh()` を回すという用途には十分）
- **`_observe` が呼ばれたときにしか評価されない。** ポーリングそのものが評価の駆動になる
- `broker_epoch` は mountd 起動ごとの乱数。再起動で generation が 0 に戻っても混同しない。
  **watcher は `(broker_epoch, generation)` の組で比較する**
- **ただし値は `VolumeInfo` の中にしかない。** `_do_list` は `{"ok", "volumes"}` を返すだけなので、
  **空集合では読む場所が無い**。空を専用の番兵として扱う（Task 2）

**盲点は 2 つ。どちらも Phase 1 で決着済みで、新しい穴ではない。**

- 同じ `major:minor` で UUID・型・容量まで同じカードに差し替わると進まない
  （`VolumeObservation` は接続の同一性であって媒体の同一性ではない、の根拠）
- ポーリング間隔の中で抜いて挿し直すと指紋が同じなので見えない。自動取り込みの観点では
  「同じカードが戻っただけ」なので実害が無い

---

## この計画の書き方（コードをどこまで埋めるか）

Phase 4 の書き分けを踏襲する。

- **順序と例外の流れが安全性に効くところは、コードで書く**（watcher の enqueue と印付けの
  トランザクション、再接続で再送してよい要求の条件、EXIF を読む位置と境界）
- **画面とコンポーネントは、契約と受け入れ条件で書く**（呼ぶ API、確認に出す情報、
  エラー時の日本語）

**レビューは計画に 1 巡、実装差分に 1 巡以上、毎回 `--fresh` で回す**（理由は
`HANDOFF.md` §5）。

---

## Global Constraints

Phase 1〜4 と同じ。差分だけ書く。

- **プロファイルの定義はユーザが書き換えられる値になる。** `require.roots` と
  `merge.output_name` のパス検証（`..`・絶対パス・シンボリックリンクの禁止）は
  ビルトインだけでなく**編集経路でも同じ検査を通す**（§6）。`ProfileInvalid` を
  API のエラー封筒へ落とす
- **正規表現はユーザが書く。長さの上限だけでは足りない。** 短い式でも catastrophic
  backtracking で停止しなくなり（`(a+)+$` 等）、マッチは `VolumeService` の
  **`RLock` の中で**最大 2000 件のファイル名に当たるので、1 本の悪い式で
  `GET /devices` ごと固まる。**マッチ側を `regex` に替えて `timeout` を渡す**（Task 4）
- **EXIF は信頼できない入力。** 読むのは 1 タグだけ、サムネイルと MakerNote を読まない、
  例外はすべて握って `fallback` へ落とす。**壊れた 1 枚で取り込み全体を止めない**
- **watcher は「勝手に動く主体」である。** 画面を開いていなくても DB を書く。
  したがって **`AUTO_IMPORT` の判定は積む直前に読み直す**（`AppState.settings` は起動時の
  スナップショットで、`AUTO_IMPORT` は `Tier.RUNTIME`）
- **秘密を出さない**の規約は変わらない。プロファイルは秘密を持たないが、
  `POST /profiles/{slug}/test` の応答に**ボリュームの中身のファイル名を出さない**
  （他人のカードを挿したときに中身が漏れる）

### 検証コマンド

```bash
uv sync --all-packages
uv run pytest
uv run pytest -m needs_system
uv run ruff check . && uv run ruff format --check .
npm --prefix web ci && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run build
npm --prefix web run typegen      # API を変えたら型を作り直す
npm --prefix web run test:e2e
```

---

## ファイル構成

| ファイル | 責務 |
| --- | --- |
| `app/src/mediaferry/adapters/broker_client.py` | **修正**。接続が切れたら 1 回だけ再接続し、**`list_volumes` だけ**再送する |
| `app/src/mediaferry/adapters/exif.py` | **新規**。ステージ済みファイルから `DateTimeOriginal` を 1 つ読む |
| `app/src/mediaferry/core/timestamps.py` | **修正**。`source: exif` の枝。値は呼び出し側が注入する（純粋関数のまま） |
| `app/src/mediaferry/core/profiles/builtin/generic-dcim.yaml` | **新規** |
| `app/src/mediaferry/core/profiles/builtin/canon-eos.yaml` | **新規** |
| `app/src/mediaferry/core/profiles/model.py` | **修正**。正規表現の検証と長さの上限 |
| `app/src/mediaferry/core/profiles/matching.py` | **修正**。マッチを `regex` + `timeout` にする |
| `app/src/mediaferry/db/profiles.py` | **修正**。ユーザ定義の作成・編集・複製・archive。**ビルトインの直接編集を拒む** |
| `app/src/mediaferry/db/migrations/0010_auto_import.sql` | **新規**。`volume_presence.auto_import_at` と `volume_instance.provisional` |
| `app/src/mediaferry/db/migrations/0011_captured_at_revision.sql` | **新規**。`media_file.captured_at_revision_id` |
| `app/src/mediaferry/adapters/publisher.py` | **修正**。手順 5 で `captured` を遅延解決する口（Task 3） |
| `app/pyproject.toml` / `uv.lock` | **修正**。`exifread` と `regex` |
| `app/src/mediaferry/jobs/watcher.py` | **新規**。`VolumeWatcher`。generation のポーリングと自動取り込み |
| `app/src/mediaferry/jobs/recompute.py` | **新規**。`recompute_timestamps` ジョブ |
| `app/src/mediaferry/jobs/importer.py` | **修正**。EXIF の抽出位置（§9.3 手順 5） |
| `app/src/mediaferry/api/routes_system.py` | **修正**。プロファイルの CRUD |
| `app/src/mediaferry/api/app.py` | **修正**。`VolumeWatcher` の起動と停止 |
| `web/src/screens/Settings.tsx` | **修正**。プロファイル編集 |
| `web/src/screens/Devices.tsx` | **修正**。信頼登録 UX、複数ボリューム、自動取り込みの状態 |
| `docs/design.md` / `docs/HANDOFF.md` | **修正**。Phase 5 で確定した事項 |

### 実装順序と依存

```
0 再接続 ─→ 2 VolumeWatcher ─→ 8 デバイス画面 ─┐
1 0010 移行 ─┘                                  │
                                                ├─→ 9 受け入れ
3 exif ─→ 4 ビルトイン 2 種 ─┬─→ 6 recompute（0011）─┤
                              └─→ 5 編集 API ─→ 7 設定画面 ─┘
```

**移行は 2 本。** `0010`（Task 1）は watcher の判定材料、`0011`（Task 6）は recompute の
provenance。**どちらも追加のみ**で、適用済みの版は書き換えない（`HANDOFF.md` §3）。
版を足したら `test_a_database_from_the_previous_release_still_opens` の checksum 一覧にも足す。

**Task 0 を最初に置く。** 常時ポーリングする主体を足すと、いま「`GET /devices` が 1 回
失敗するだけ」の問題（mountd の再起動で接続が死ぬ）が**恒久的な故障**に変わる。
watcher より先に塞ぐ。

---

### Task 0: ブローカー接続の再接続

**Files:** Modify `app/src/mediaferry/adapters/broker_client.py` / Test `app/tests/test_broker_client.py`

**なぜ最初か:** `BrokerClient.__init__` は起動時に 1 度だけ `connect` し、以後その
ソケットを使い続ける。mountd が再起動すると `_call` は落ち続け、**アプリを再起動するまで
戻らない**。今は `GET /devices` を叩いたときだけ見えるが、`VolumeWatcher` は数秒ごとに
叩くので、この穴が常時の故障になる。

**判断: 再送してよいのは `list_volumes` だけ。**

```python
def _call(self, payload, expect_fd=False):
    with self._lock:
        try:
            return self._send_and_receive(payload, expect_fd)
        except (ConnectionClosed, OSError) as exc:
            if payload.get("type") != REQ_LIST_VOLUMES:
                raise
            # 副作用の無い読み取りだけを再送する。
            #  - open_volume は fd を伴う。再送すると mountd 側で 2 度目の
            #    マウントが起き、1 つ目の handle が誰にも閉じられずに残る
            #  - close_volume の handle は「発行した接続」に束縛されている
            #    （§11）。接続を張り直した時点で対象が存在しないので、
            #    再送しても no such handle になるだけ
            self._reconnect()
            return self._send_and_receive(payload, expect_fd)
```

**再接続は 1 回だけ試す。** ループさせない —— mountd が落ちている間、watcher の 1 tick が
延々と粘ると停止要求に応じられなくなる。失敗はそのまま上へ返し、**次の tick で改めて試す**
（tick そのものがリトライになっている）。

**閉じている最中は再接続しない**（計画レビュー 2 巡目の major）。停止は watcher の socket を
閉じて `recv` を解く。その `OSError` をこの再接続が拾うと、**「`list_volumes` だから再送可」と
判断して新しい socket を作り、また待ちに入る** —— 停止が効かない。

```python
def close(self) -> None:
    self._closing = True        # 先に立てる。閉じてから立てると隙間で再接続できる
    with contextlib.suppress(OSError):
        self._sock.shutdown(socket.SHUT_RDWR)   # 待っている recv を確実に解く
    self._sock.close()
```

- **`_closing` を先に立てる。** `_call` は再送の判定より先にこれを見て、立っていれば
  そのまま例外を上げる
- **`shutdown` を挟む。** `close` だけでは、別スレッドで blocking している `recv` が
  即座に解ける保証が弱い。`shutdown(SHUT_RDWR)` は待っている側に EOF を届ける
- **socket の deadline は保険として残す。** `shutdown` が効かない環境でも、
  deadline があれば有限時間で降りられる

**開いている handle は再接続で失われる。** 接続が切れた時点で mountd 側の
`handle_connection` の `finally` が全ハンドルを release しているので、これは新しい損失では
ない。`VolumeService._open` に残った `VolumeHandle` は無効になるが、**dirfd は受け取り済み
なので読み出し自体は安全**（§9.2 の「一度受け取った dirfd はデバイスが抜かれても安全」）。
再接続時に `_open` を掃除しない —— 掃除すると、走っているジョブの足元で handle が消える。

**`_reconnect` の契約:** 旧 socket を**必ず** close してから新しい socket を作る。
`connect` に失敗したら**新しい socket も閉じてから**例外を上げ、`self._sock` は
「閉じた socket」で一貫させる（半端に開いたままにしない）。接続の作り方は
`__init__` と共有し、`from_socket` で作った client（テスト用）は**再接続できない**ことを
明示する —— 元の socket_path を知らないため。

**受け入れ:**
- 実 `BrokerServer` を落として上げ、次の `list_volumes` が値を返す
- `open_volume` は接続が切れていても**再送されない**（例外が上がる）
- 再接続に失敗した `list_volumes` は例外を上げる（握り潰さない）
- **`list_volumes` を何度も落として再接続させても、プロセスの fd が増えない**
- `from_socket` で作った client は再接続を試みず、そのまま例外を上げる
- **`close()` した client は再接続しない。** `list_volumes` の `recv` を待っている最中に
  別スレッドから `close()` しても、**新しい接続が復活せず**、待っていた呼び出しが
  有限時間で例外になる（停止の回帰。major 4）

**変異試験:**
- `!=` を `==` にする（`list_volumes` 以外を再送する）→ `open_volume` の試験が落ちること
- 再接続を 2 回以上ループさせる → 停止の試験が落ちること
- 再接続時に `_open` を空にする → 走っているジョブの試験が落ちること
- **`_closing` の確認を外す** → 「閉じた client が再接続しない」が落ちること
- **`_closing` を立てる順を `close()` の後にする** → 同上（隙間で再接続できる）
- **`shutdown` を外して `close` だけにする** → 停止の試験が落ちること

**実施結果（2026-08-19）: 8 件中 6 件を検出。残り 2 件は構造的に検出できない。**

| 変異 | 結果 |
| --- | --- |
| `list_volumes` 以外も再送する / 何でも再送する | 検出 |
| `_closing` の確認を外す / 立てる順を後にする | 検出 |
| `close()` で `shutdown` しない | 検出 |
| `from_socket` でも再接続する | **最初は素通り。テストを強くして検出**（下記） |
| 再接続で旧 socket を閉じない | **検出できない**（下記） |
| `connect` 失敗時に socket を閉じない | **検出できない**（下記） |

**`from_socket` の素通りは、テストの弱さだった。** `_socket_path` が `None` なので
繋ぎ直そうとしても `connect("None")` が `ENOENT` になり、テストが期待していた
`(OSError, ProtocolError)` を**たまたま満たしていた**。「例外が出た」ではなく
「繋ぎ直そうとしていない」（`errno != ENOENT`）を見る形に直して検出できるようにした。

**残る 2 件は CPython の参照カウントに消される。** `self._sock` を差し替えた時点、
および `_connect` が例外で抜けた時点で、旧 socket は最後の参照を失って
`socket.__del__` が閉じる。**20 回再接続して fd が増えない試験が通ること自体が証拠。**
明示的な `close()` は他の実装（参照カウントを持たない処理系）と、参照が残る形へ
書き換えられたときのための保険として残す。

---

### Task 1: `auto_import_at` と `provisional`（`0010`）

**Files:** Create `app/src/mediaferry/db/migrations/0010_auto_import.sql` / Test `app/tests/test_db_migrate.py`, `app/tests/test_schema_sources.py`

```sql
-- 自動取り込みを積んだ接続に印を付ける。列は volume_instance ではなく
-- volume_presence に置く。「このカードを取り込んだか」ではなく
-- 「この接続について積んだか」を憶えるためで、抜き挿しすればまた積まれる。
ALTER TABLE volume_presence ADD COLUMN auto_import_at TEXT;

-- 判定が「暫定マッチ」だったかを残す。identity_confidence は既に
-- volume_instance に保存されているが provisional は VolumeView にしか無く、
-- **DB だけを見て自動取り込みの可否を決められない**（Task 2）。
ALTER TABLE volume_instance ADD COLUMN provisional INTEGER NOT NULL DEFAULT 0
    CHECK (provisional IN (0, 1));
```

**`provisional` を列にする理由は Task 2 にある。** watcher は毎 tick「積んでよいか」を
**DB の現在値から**組み直す。判定材料が 1 つでも view にしか無いと、その組み直しが
成立しない。`_probe` は `identity_confidence` と同じ `UPDATE` でこの列も書く。

**なぜ presence か:** `volume_presence` は「接続 1 つ = 1 行」で、列挙のたびに増えない
（`0002` のコメント）。ここに印を置けば、**再起動しても同じ接続なら積み直さない**。
`volume_instance` に置くと 2 度と自動取り込みされないカードができ、
watcher のタイマに置くとプロセスが落ちるたびに二重に積む。

**適用済みの版は書き換えない。** `test_a_database_from_the_previous_release_still_opens` の
checksum 一覧に `0010` を足す（`HANDOFF.md` §3、Phase 3 の 7 巡目 blocker）。

**受け入れ:** 既存 DB に適用でき、`auto_import_at` が NULL 許容、`provisional` が
既定 0 で追加される。checksum の凍結試験が新しい版を含んだうえで通る。
`_probe` が `provisional` を書き、`GET /devices` の値と DB の値が一致する。

---

### Task 2: `VolumeWatcher`

**Files:** Create `app/src/mediaferry/jobs/watcher.py` / Modify `app/src/mediaferry/api/app.py`,
`app/src/mediaferry/jobs/volumes.py` / Test `app/tests/test_volume_watcher.py`

**責務は 3 つだけ。**

1. `list_volumes` を一定間隔で呼び、**観測トークンが変わったときだけ**
   `VolumeService.refresh()` を回す（＝マウントを伴う判定はここでしか起きない）
2. **毎 tick**、`AUTO_IMPORT=trusted` のとき条件を満たす presence に `import` を積む
3. 集合から消えた presence に紐づく**未実行**のジョブを無効化する（§9.2 の `remove` 規則）

**2 は 1 の門の内側に入れない。** ここが計画レビュー 1 巡目の blocker だった。

**間隔は設定にしない。コンストラクタ引数にする**（`JobRunner(database, poll_interval=0.5)` と
同じ形）。運用で変えたくなる値ではなく、試験で速く回したいだけの値なので、
設定項目を増やさない。

#### 判定は毎 tick、DB の現在値から組み直す

**`refresh()` の戻り値（`VolumeView`）を判定に使ってはいけない。**

`VolumeService.trust()` は `volume_instance.trusted_at` を `UPDATE` するだけで、
**mountd の指紋は動かない**。カードを挿したまま画面で承認しても観測トークンは変わらず、
直前の view は `trusted = False` のままなので、**承認しても自動取り込みが始まらない**。
同じことが `AUTO_IMPORT` の変更、プロファイルの編集（Task 5）、`recompute` の後にも起きる。
**「利用者が DB を変えた」ことは、観測トークンには現れない。**

そこで責務を分ける。

| | 何で駆動するか | 費用 |
| --- | --- | --- |
| **probe（`refresh()`）** | 観測トークンの変化 | 高い（ボリュームごとに mount / 走査 / umount） |
| **enqueue の判定** | **毎 tick** | 安い（DB の `SELECT` と `UPDATE` だけ。マウントしない） |

```python
# 積める候補を DB から組み直す。**排他区間の中で読む。**
# 外で読むと、読んだ後・積む前に detach / archive / 信頼解除が commit されうる。
CANDIDATES = """
    SELECT p.id AS presence_id, p.volume_instance_id, v.profile_id,
           v.profile_revision_id, p.broker_epoch, p.generation, p.major, p.minor,
           p.device_node, p.sysfs_path, v.fs_uuid, v.fs_type
      FROM volume_presence p
      JOIN volume_instance v ON v.id = p.volume_instance_id
      -- **プロファイルの現在性も同じ排他区間で確かめる。**
      JOIN device_profile d ON d.id = v.profile_id
     WHERE p.detached_at IS NULL          -- 抜けた接続に印を付けない
       AND p.auto_import_at IS NULL       -- まだ積んでいない
       AND v.trusted_at IS NOT NULL       -- 信頼登録済み
       AND v.identity_confidence = 'high' -- 同定の確度（§8）
       AND v.provisional = 0              -- 「対象だが中身が無い」は対象外（§12.1）
       AND v.profile_id IS NOT NULL       -- 対象外ボリュームではない
       AND d.archived_at IS NULL          -- archive されたプロファイルでは積まない
       -- 判定に使った版が今も現行であること。編集されていたら積まない
       AND v.profile_revision_id = d.current_revision_id
"""

def _enqueue_ready(self) -> list[str]:
    # AUTO_IMPORT は Tier.RUNTIME。起動時のスナップショットを見てはいけない。
    if SettingsService(self._conn, self._env).snapshot().auto_import != "trusted":
        return []
    jobs = []
    with immediate(self._conn):
        for row in self._conn.execute(CANDIDATES).fetchall():
            # 印を付けるのと同じ条件で、同じトランザクションの中で取る。
            marked = self._conn.execute(
                "UPDATE volume_presence SET auto_import_at = ?"
                " WHERE id = ? AND auto_import_at IS NULL AND detached_at IS NULL",
                (now_iso(), row["presence_id"]),
            ).rowcount
            if marked:
                jobs.append(JobStore(self._conn).enqueue("import", _params(row)))
    return jobs
```

**判断の理由:**

- **判定材料をすべて DB に置く。** そのために `0010` で `volume_instance.provisional` を
  足す（Task 1）。1 つでも view にしか無いと、この組み直しが成立しない
- **`volume_instance.profile_id` / `profile_revision_id` は「前回の判定の写し」であって
  現在の真実ではない**（計画レビュー 2 巡目の blocker）。`device_profile` を JOIN して
  **archive されていないこと**と**その版が今も現行であること**を確かめる。
  確かめないと、refresh の後・enqueue の前に別接続で archive / `PUT` が commit でき、
  **archive 済みのプロファイルや編集前の版で取り込みを積む**
- **条件付き UPDATE（CAS）で印を取る。** SQLite に行ロックは無い（`HANDOFF.md` §3）。
  `SELECT` してから `UPDATE` すると、その隙間に届いた次の tick と競合する
- **印付けと `enqueue` は同じ `BEGIN IMMEDIATE` の中。** `JobStore.enqueue` は自分で
  commit しないので、両方が原子的に成立するか、両方 rollback されるかのどちらかになる。
  **「印を先に取れば crash しても 1 回ぶんで済む」ではない**（同一トランザクションなので
  crash では両方が消え、次の tick で改めて積まれる）。**要点は順序ではなく原子性。**
  計画レビュー 1 巡目で指摘された誤りをここで直している
- **`params` は DB の行から組み立てる。** ジョブは選択した瞬間の presence を params に
  持つ（Phase 1 の契約）。`volume_instance_id` だけを渡してはならない。
  行は排他区間の中で読んでいるので、`selection_for()` で引き直す必要は無い
- **`detached_at IS NULL` を `SELECT` と `UPDATE` の両方に置く。** 片方だけだと、
  抜けた接続に印を付ける経路が残る

#### 実装で分かったこと（計画に無かった 3 件）

1. **`BrokerServer` は lister が返す `broker_epoch` と `generation` を捨てて、自分の値で
   刻み直す**（`_observe`）。世代はサーバが観測した集合
   `(volume_key, fs_uuid, fs_type, size_bytes)` の指紋から算出する。したがって
   **試験で「抜き挿し」を表すには、集合そのものを変えるしかない**（`generation` の欄を
   書き換えても客体には届かない）。逆に言えば、**観測トークンは完全にサーバ側で決まる**ので、
   app 側が値を作れないことが保証されている
2. **自動取り込みが効くのは 2 度目以降の「観測」から（挿し直しは要らない）。**
   `_identity_confidence` は憶えた指紋が
   無ければ必ず `low` を返す（「初めて見るカードは §12.1 のとおり必ず承認を待つ」）。
   §12.1 の「一度承認すれば以後は挿すだけ」の**「以後」がここに対応する**。
   ただし**指紋を憶えるのは最初の `refresh()`** なので、`high` になるのは
   同じ挿入のまま次の tick から。**承認すると、いま挿してあるカードの中身が
   数秒後に取り込まれる**（Task 8 の文言と確認ダイアログはそう書く）
3. **`VolumeService.refresh()` はトランザクションを張っていなかった。** インスタンスが 1 つ
   しか無かったから成立していただけで、watcher が 2 つ目を持つと pass 1〜2 の合間に相手の
   書き込みが挟まる。pass 1（観測の反映）と pass 2（`detach_absent`）を
   `BEGIN IMMEDIATE` で囲んだ。**判定（pass 3）はマウントを伴って長いので外に出す**

#### 二重 enqueue の範囲（明示）

**この CAS が保証するのは「自動経路の at-most-once」だけ。** 手動の
`POST /volumes/{id}/import` は `auto_import_at` を見ないので、**同じ presence に手動 1 本と
自動 1 本が積まれることはある。** これは受け入れる —— import は冪等で
（`source_entry.state` が `published` のものは飛ばす）、2 本目は何も公開せずに終わる。
ジョブ全体の重複排除（presence 単位の一意キー、または active-job の CAS）は
**Phase 5 の範囲外**とし、`JobStore` の契約を変える話として切り離す。

#### 観測トークン —— 空集合には `generation` が無い

`_do_list` は `{"ok": True, "volumes": [...]}` を返すだけで、**`generation` と
`broker_epoch` は `VolumeInfo` の中にしか無い**（`mountd/server.py::_do_list`）。
最後の 1 枚を抜くと `volumes: []` になり、**比較すべき値が読めない。**

**プロトコルは変えない。空集合を専用の番兵として扱う。**

```python
EMPTY = ("", -1)          # 「空集合を観測した」
UNOBSERVED = None         # 「まだ一度も観測していない」—— EMPTY と必ず区別する

def _token(volumes: list[VolumeInfo]) -> tuple[str, int]:
    # 非空なら (broker_epoch, generation)。全ボリュームに同じ値が刻まれている。
    return (volumes[0].broker_epoch, volumes[0].generation) if volumes else EMPTY
```

- 空 → 非空: `EMPTY` から `(e, g)` へ変わる → `refresh()` ✓
- 非空 → 空: `(e, g)` から `EMPTY` へ変わる → `refresh()`（`detach_absent` を走らせる）✓
- 非空 → 非空（変化あり）: `generation` が進む ✓
- 空 → 空: 変化なし。**やることも無い** ✓

**`UNOBSERVED` を `EMPTY` と同一視してはいけない**（計画レビュー 2 巡目の major）。
記憶の初期値を「空」にすると、**前回の停止時の live な presence が DB に残ったまま、
空で起動した最初の tick が「変化なし」と判定して `refresh()` を飛ばす**。
`detach_absent` が走らないので、**抜けているカードの presence に自動取り込みを積む**。
**最初に成功した `list_volumes` は、空でも必ず `refresh()` を回す。**

**新しい盲点は増えない。** `broker_epoch` を組に含めるのは、mountd 再起動で
`generation` が 0 に戻るため。

#### 門はもう 1 つの入力を持つ —— プロファイルの現行版

観測トークンだけを門にすると、**プロファイルを編集しても再判定されない**。
`require` が変わってカードが一致しなくなっても、mountd の指紋は動かない。

```python
# 門 = (観測トークン, 現行リビジョンの指紋)。どちらかが変われば refresh。
profile_token = tuple(sorted((ref.profile_id, ref.revision_id) for ref in registry.active()))
```

DB の `SELECT` 1 本で取れるので毎 tick 評価してよい。**プロファイルの編集・複製・archive の
どれでもこの指紋が動く**ので、明示的な wake の仕組みを別に作らなくて済む。

**`GET /devices` が先に新しいトークンを観測しても、watcher は自分の記憶と比べるので
もう一度 `refresh()` する。** 同じ変化で全 probe が 2 回走る。**許容する代償**として
記録しておく（`GET /devices` 側の観測を watcher と共有すると、画面を開いただけで
watcher の状態が動くことになり、そちらの方が読みにくい）。

**`refresh()` が走る場面と、その代償:**

`refresh()` は live 集合の**全ボリューム**を `open_volume` でマウントし、名前を走査
（最大 2000 件）してマニフェスト digest（最大 500 件）を取り、閉じる。`_probe` の
コメントが Phase 1 の判断を残している —— 「代償は `GET /devices` のたびに mount / umount が
走ること。**Phase 1 は手動操作しか無いので許容する**」。**Phase 5 は自動で呼ぶ主体を足すので、
この前提が変わる。** 門を作る理由がここにある。

| 場面 | refresh の回数 | 評価 |
| --- | --- | --- |
| **カード挿入** | 1 | **狙い。** 自動取り込みはここから始まる |
| **カード抜去** | 1 | 残っているボリュームも巻き込んでマウントし直す（下記） |
| mountd 再起動 | 1 | `broker_epoch` が変わる |
| **`blkid` の一時失敗** | 2 | デバイスがビジー等で `fs_type` が取れないとそのボリュームが集合から落ち、次の tick で戻る。落ちる／戻るで 2 回 |
| デバイス画面を開く・操作する | 1 | 既存の挙動。watcher とは独立（`/devices` は SSE では自動再取得していない） |
| **カードを挿しっぱなしで放置** | **0** | ポーリングは `list_volumes` だけ。USB ブロックデバイスが無ければ `blkid` すら起きない |

門を作らずに毎 tick `refresh()` を呼ぶと、**カードを挿している限り 5 秒ごとに永久に**
mount → 走査 → digest → umount が回る。**16 GiB の取り込みが走っている最中も、その裏で
同じカードをマウントし続ける**（ジョブの handle とは別に `_probe` が自分の handle を開くので
ジョブは壊れないが、I/O を食い合う）。

**「変わったボリュームだけ probe する」最適化は入れない。** `_identity_confidence` は
`_has_other_live_presence` を見ており、**B が現れた／消えたことで A の確度が変わる**。
A を飛ばすと A の確度が古いまま残る。集合全体を見る現行の作りが正しく、抜去の 1 回は
受け入れる代償とする。

**フィードバックループは起きない。** `refresh()` は `(volume_key, fs_uuid, fs_type,
size_bytes)` のどれも変えないので、自分で自分を再トリガしない。

**消えた presence の掃除:**

```python
# 集合から消えた接続に紐づく、まだ claim されていないジョブを無効化する。
# 走っているジョブには触らない —— expect 検証と StaleSelection が既に守っており、
# ここで触ると「実行中のジョブを外から failed にする」経路を新設することになる。
#
# **開いている handle にも触らない。** detach_absent は volume_presence の
# 列を更新するだけで、アンマウントではない。handle を閉じるのはジョブ終了時の
# release / POST /volumes/{id}/close / 停止時の close_all の 3 つだけ。
```

**片方を抜いても、もう片方のコピーは止まらない。** これは Phase 5 が守るべき性質だが、
**現状どのテストも守っていない**（自動で `refresh()` を呼ぶ主体が無かったため）。
成立している根拠は 3 つ:

1. **マウントは handle ごとに独立している。** `MountManager.mount` は要求のたびに
   `mount` → `open_tree(OPEN_TREE_CLONE)` → **即座に `MNT_DETACH`** する。以後その
   ファイルシステムはどの名前空間のパスにも現れず、参照は渡した dirfd だけ。
   **共有された名前付きマウントが存在しない**ので、巻き添えの経路が無い
2. **`MountManager.release` は umount ではない。** `_mounted` からその handle だけを
   pop して dirfd を閉じる。取り付けは mount の時点で既に外れている
3. **`detach_absent` は DB の `UPDATE`。** handle には触れない

抜かれたのがコピー中のカード自身だった場合は、**強制終了ではなく読み出しの失敗**になる。
dirfd は有効なままでパスのすり替わりは起きず（§9.2）、読み取りが `EIO` になって
そのファイルの取り込みが `failed` になる。

#### 停止と、ブローカー接続の持ち方

**`wait_for` では止まらない。** `asyncio.to_thread` に出した呼び出しは task の cancel で
止まらず、`recv_message` には timeout が無い（`BrokerClient._call`）。停止フラグの待ちに
`wait_for` を掛けても、**ブローカーが黙り込んだら thread は生き続ける**。
計画レビュー 1 巡目の blocker。

**watcher は専用の `BrokerClient` を持つ。** `VolumeService` と共有しない。

- 共有した場合、停止のために socket を閉じると**走っている import の handle 接続まで切る**。
  ハンドルは発行した接続に束縛されている（§11）ので、`close_volume` の相手が消える
- 共有した場合、`_call` の `_lock` により、watcher の poll が `open_volume`
  （マウントを伴う）の後ろで待たされる。逆も起きる
- 専用にすれば **socket に timeout を掛けられる**（`recv_fds` は `TimeoutError` を投げる）

したがって停止は 2 段で成立させる。

1. **RPC ごとの deadline。** watcher の `BrokerClient` の socket に timeout を設定する。
   mountd の idle timeout は 300 秒なので、poll 間隔より十分長く・deadline より十分長い
   関係は保たれる。timeout は失敗として扱い、次の tick で再試行する（Task 0 の再接続に乗る）
2. **`stop()` は自分のソケットを閉じる。** 閉じれば `recv` は即座に解ける。
   閉じてよいのは**自分専用の接続だから**

**`lifespan` の停止順を明記する。**

```
watcher.stop() → await watcher_task   # 先に止める。新しい enqueue を止めるため
runner.stop()  → await worker         # 走っているジョブの完了を待つ（既存のまま）
volumes.close_all() / volumes_conn.close()
```

**watcher を先に止めないと、`runner` が降りた後に queued のジョブが残る。**
残っても次回の起動で拾われるので破壊的ではないが、「停止したのにキューが伸びた」状態は
作らない。

#### watcher は「一組」を丸ごと持つ

**専用のブローカー接続だけを持たせる案は成立しない**（計画レビュー 2 巡目の blocker）。
`VolumeService.refresh()` は **`self._client`** と**インスタンス内の `RLock`** を使う。
watcher が API 側の `VolumeService` を呼べば、refresh は結局**共有の `BrokerClient`** を
通るので、watcher 専用 socket を閉じても黙り込んだ refresh は止まらない。かといって
専用 client の `VolumeService` をもう 1 つ作って `volumes_conn` を使い回すと、
**1 本の接続を 2 つの `RLock` で同時に使う**ことになり、`db/connection.py` の
「接続を同時共有しない」に反する。handle の map も 2 つに割れる。

**watcher は 1 つのスコープを丸ごと持つ。**

```
watcher スコープ = 専用 DB 接続 1 本
                 + その接続の ProfileRegistry
                 + その接続と専用 BrokerClient で作った VolumeService
```

- API 側（`GET /devices`）とジョブ側は今までどおり `AppState.volumes` を使う。
  **watcher のものとは別のインスタンス**
- **ジョブの handle は API 側の `VolumeService` にしか無い。** watcher 側は probe しか
  行わないので `_open` は常に空のまま。停止で watcher 一式を閉じても、走っている
  取り込みの handle には触れない
- 2 つの `VolumeService` は**別々の接続**なので、競合は SQLite の WAL と
  `BEGIN IMMEDIATE` で解決する。「接続はスコープごとに 1 本」はこれで守られる

**そのために `VolumeService.refresh()` の書き込みを 1 つの `BEGIN IMMEDIATE` に入れる。**
現状の `refresh()` は `RLock` は取るが**トランザクションを張っていない**（`upsert_device` /
`resolve_volume_instance` / `sync_presence` / `detach_absent` / `_probe` の `UPDATE` が
autocommit で流れる）。**インスタンスが 1 つしか無かったから成立していた**だけで、
2 つになると pass 1〜3 の途中に相手の書き込みが挟まる。**Task 2 で囲む。**

**停止順:** watcher を止める → watcher の `VolumeService.close_all()` と接続を閉じる →
runner を止める → API 側の `volumes.close_all()`。

**`AUTO_IMPORT=off` でも watcher は回す。** 一覧を新鮮に保つ役目があるので止めない。
**積むかどうかだけを設定で決める。**

**受け入れ:**
- `volumes` fixture を書き換えて `generation` を進めると `refresh()` が 1 回走る
- 集合が変わらない tick では `refresh()` が**走らない**（＝マウントが起きない）
- **最後の 1 枚を抜いて `volumes: []` になっても変化として検出され、`refresh()` が走る**
  （`detach_absent` が動く）。**空のまま tick を重ねても `refresh()` は走らない**
- 空から 1 枚挿すと `refresh()` が走る
- mountd を再起動して `generation` が 0 に戻っても、`broker_epoch` の違いで変化と分かる
- 信頼済み・`high`・非 provisional のボリュームに `import` が 1 本だけ積まれる
- **カードを挿したまま `POST /volumes/{id}/trust` を呼ぶと、観測トークンが変わらなくても
  次の tick で `import` が積まれる**（blocker 1 の回帰）
- 同じ接続のまま tick を何度回しても 2 本目は積まれない
- 抜いて挿し直す（`volume_presence` の行が変わる）と、また積まれる
- **抜けた（`detached_at` が立った）presence には印が付かず、積まれない**
- `AUTO_IMPORT=off` では積まれない。**起動後に `off` → `trusted` へ変えたら、観測トークンが
  変わらなくても次の tick で積まれる**
- `trusted` でない／`low`／`provisional`／プロファイル未確定では積まれない
- 消えた presence の未実行ジョブが無効化され、**実行中のジョブは触られない**
- **2 枚挿した状態で片方のジョブが dirfd を掴んでいる間に、もう片方を集合から外して
  `refresh()` を走らせても、掴んでいる handle が閉じられない**（`VolumeService.opened()` に
  残る）**し、読み出しが続けられる**
- 変わらない tick では `refresh()` が走らないので、**取り込み中に裏でマウントが繰り返されない**
- **`stop()` を呼ぶと、ブローカーが応答を返さなくても有限時間で `run_forever()` が返る**
  （応答しない fake ブローカーを相手に測る）
- **watcher を止めても、走っている import の handle 接続は切れない**（専用接続の回帰）
- mountd を落として上げると、watcher は例外で死なず次の tick で復帰する
- `lifespan` の停止で watcher が runner より先に止まる（停止後にキューが伸びない）

**変異試験:**
- 変化の比較から `broker_epoch` を落とす → mountd 再起動で generation が 0 に戻る筋書きが落ちること
- `generation` の比較を消して毎 tick `refresh()` する → 「変わらない tick では refresh しない」が落ちること
- **空集合のトークンを「未取得」と同一視する**（`None` を「まだ読んでいない」として扱う）
  → 「空のまま tick を重ねても refresh しない」が落ちること
- **enqueue の判定を観測トークンの門の内側へ入れる** → 挿したまま trust する試験が落ちること
- **判定を DB からではなく `refresh()` の戻り値から作る** → 同上
- `UPDATE` の `AND auto_import_at IS NULL` を落とす → 二重に積まない試験が落ちること
- **`SELECT` か `UPDATE` の片方から `detached_at IS NULL` を落とす** → 抜けた presence の試験が
  落ちること
- 印付けと enqueue を別トランザクションにする → 二重に積まない試験が落ちること
- 設定の読み直しを起動時スナップショットに変える → `off` → `trusted` の試験が落ちること
- `provisional` / `identity_confidence` / `trusted_at` / `profile_id` の各条件を 1 つずつ落とす
  → 対応する試験が落ちること
- 掃除の対象に実行中のジョブを含める → 「実行中は触らない」が落ちること
- **`refresh()` の pass 2 で、消えたボリュームの handle も閉じる** → 「片方を抜いても
  もう片方のコピーが止まらない」が落ちること
- **watcher のブローカー接続を `VolumeService` と共有する** → 「watcher を止めても import の
  handle 接続が切れない」が落ちること
- **socket の timeout を外す** → 「応答しないブローカー相手に有限時間で停止する」が落ちること
- **`lifespan` の停止順を runner 先行に入れ替える** → 停止後にキューが伸びない試験が落ちること

**実施結果（2026-08-19）: 17 件中 13 件を個別に検出。残り 4 件の内訳は下記。**

**最初は 9 件しか検出できなかった。素通り 8 件を 1 件ずつ精査して 5 件を落とせるようにした。**

| 素通りしていた変異 | 原因 | 対処 |
| --- | --- | --- |
| トークンから `broker_epoch` を落とす | **試験の穴。** epoch と一緒に generation も変えていたので、epoch を見ていなくても世代の違いで検出でき、epoch を検証したことになっていなかった | generation を固定して epoch だけを変える |
| `SELECT` から `archived_at` / 現行リビジョンの一致を落とす | **試験の穴。** tick 全体で試すと、プロファイルの指紋が変わって判定し直され、**別の理由**（`profile_id` が NULL になる）で積まれなくなる。条件そのものを検証していなかった | tick を分解し、**判定の後・積む前**に編集を挟む窓を直接作る（この条件が実際に効くのはその窓だけ） |
| 印付けと enqueue を別トランザクションにする | **試験の穴。** 単一スレッドでは autocommit でも同じ結果になる | `JobStore.enqueue` を失敗させ、**印が残っていないこと**を見る |
| 空集合を「未観測」と同一視する | **変異が判断を壊していない。** `EMPTY` の値そのものは効いておらず、効くのは `_seen` の初期値が `None` であること | 変異を「未観測を『空を観測済み』として始める」に置き換えたら検出できた（前回の live presence が detach されない筋書き） |

**残る 3 件は、意図した二重の防御の「片側だけ」を壊す変異。**
`detached_at` と `auto_import_at` は `SELECT`（候補の抽出）と `UPDATE`（CAS）の
両方に置いてある。片方だけ壊してももう片方が塞ぐので、単独では検出できない。
**対の両方を同時に壊す変異は検出できる**ので、対としては効いている。

| 変異 | 単独 | 対で |
| --- | --- | --- |
| `detached_at` を `SELECT` から / CAS から | 素通り（互いにマスク） | **検出** |
| `auto_import_at` を CAS から | 素通り（`SELECT` がマスク） | **検出** |

**この形はリポジトリに前例がある**（`claim_next` の CAS 条件は `BEGIN IMMEDIATE` が
claimer を直列化するので単独では到達しない）。**冗長さは意図であって、削ってよい根拠にはならない** ——
`SELECT` は候補を絞り、CAS は「読んでから書くまでの隙間」を塞ぐ。役割が違う。

**検出できない見込みの変異は、確認してから記録に残す**（`HANDOFF.md` §5 の 3 番。
「検出できない」と書いた変異の多くはテストを 1 つ足せば落とせる）。

---

### Task 3: `timestamp.source: exif`

**Files:** Create `app/src/mediaferry/adapters/exif.py` / Modify `app/src/mediaferry/core/timestamps.py`,
`app/src/mediaferry/adapters/publisher.py`, `app/src/mediaferry/jobs/importer.py`,
`app/pyproject.toml`, `uv.lock` / Test `app/tests/test_exif.py`, `app/tests/test_timestamps.py`,
`app/tests/test_publisher.py`, `app/tests/test_crash_consistency.py`

**依存:** `exifread` を app に足す。純 Python で画像をデコードしない。

**読む位置は「ソース」ではなく「ステージ済みのファイル」。** これが中心の判断。

- §9.3 の手順 5 が「メタデータをここで確定させる」と決めており、ffprobe も同じ位置で走る
- ステージ済みファイルは SHA-1 で検証済みのバイト同一なコピーで、**こちらの持ち物**。
  dirfd 起点の単一構成要素・`O_NOFOLLOW` という規約を EXIF のために持ち出さなくて済む
- ソース側を 2 度読むと、コピー中に書き換えられた場合に「取り込んだ中身と読んだ EXIF が
  違う」状態を作れる

**`core/timestamps.py` は純粋関数のまま保つ。** 値は呼び出し側が注入する。

```python
def resolve_captured_at(
    defn, rel_path, mtime_ns, default_timezone, exif_wall: datetime | None = None,
) -> CapturedAt:
```

`_wall_clock` に `source == "exif"` の枝を足し、`exif_wall` があればそれを、無ければ
`fallback` に落ちる。**`Importer` の事前検証ループ（`TimezoneUnresolved` を早く出すための
空振り呼び出し）は `exif_wall=None` のまま**でよい —— あのループが確かめているのは
プロファイル単位の条件で、ファイルごとの値ではない。

#### 差し込み口が今は無い（計画レビュー 1 巡目の blocker）

現行の `Importer._publish_one` は `resolve_captured_at` を呼んで `ArtifactRequest.captured`
を作り、**その後で** `publish` を呼ぶ。`ArtifactPublisher` が staging を作るのはさらに後で、
手順 5 で `request.captured` を `metadata_json` に書く。**`Importer` の側に staging のパスは
無く、publisher を触らずに「ステージから読む」は書けない。**

そこで `ArtifactRequest` に**遅延解決の口**を足す。

```python
@dataclass(frozen=True)
class ArtifactRequest:
    ...
    captured: CapturedAt | None            # どちらか一方を必ず与える
    resolve_captured: Callable[[Path], CapturedAt] | None = None

    def __post_init__(self) -> None:
        # **両方でも両方 None でも拒む。** 「どちらか一方」をコメントで書くだけだと、
        # 後から caller（merge 等）を足したときに、静かにどちらかが優先される。
        if (self.captured is None) == (self.resolve_captured is None):
            raise ValueError("captured と resolve_captured はどちらか一方だけを与える")
```

- `ArtifactPublisher` は**手順 4（サイズと SHA-1 の検証）の後、手順 5（メタデータの確定）の
  中で** `captured` が `None` なら `resolve_captured(staging_abs)` を呼ぶ
- **手順 5 より前でも後でもいけない。** 前だと検証前のバイト列を読むことになり、
  後だと `metadata_json` に載らないまま手順 7 で commit される
- **`resolve_captured` は例外を投げない契約**（`adapters/exif.py` が握る）。投げると、
  検証まで済んだファイルが手順 5 で落ちて staging に残る
- **両方与える／どちらも与えないは `ArtifactRequest` の構築時に弾く。** 既存の caller
  （`Importer` と `Merger`）も新しい形に合わせる
- `Importer` は `source: exif` のプロファイルで**画像のときだけ** `resolve_captured` を渡す。
  それ以外は今までどおり `captured` を渡す

**crash consistency に新しい窓は開かない。** 解決は手順 5 の中、つまり手順 7 の commit より
前に完結する。落ちれば手順 6 までの回収規則（作業が無かったことになる）がそのまま効く。
`test_crash_consistency.py` の 11 段に `source: exif` のプロファイルを 1 本通して確かめる。

**`adapters/exif.py` の境界:**

```python
def read_datetime_original(path: Path) -> datetime | None:
    """EXIF の DateTimeOriginal を壁時計として返す. 読めなければ None."""
    # 読むのは 1 タグだけ。details=False でサムネイルと MakerNote を読まない。
    # 例外はすべて握る —— 壊れた 1 枚で取り込み全体を止めない。
```

- **オフセットは読まない。** EOS 70D は EXIF 2.31 の `OffsetTimeOriginal` より前の機種で、
  持っていない。持っている機種が出てきたら、そのときプロファイルの
  `timezone_policy` で扱う（値の解釈をコードの分岐に持ち込まない）
- **MOV には効かない。** Canon の動画は `fallback: mtime` に落ちる
- **画像以外では呼ばない。** `exifread` は認識できない入力に対して例外ではなく
  logger `exifread` の WARNING（`File format not recognized.`）を出す（実測）。
  Canon は MOV も `source: exif` のプロファイルを通るので、呼べば**動画 1 本ごとに警告が出る**。
  `ArtifactRequest.kind` で振り分け、画像のときだけ `resolve_captured` を渡す。
  保険として起動時に `logging.getLogger("exifread").setLevel(logging.ERROR)` も入れる

**実測済み**（`exifread` 3.5.1）: `DateTimeOriginal` は `%Y:%m:%d %H:%M:%S` の ASCII。
タグ無し・EXIF 破損・JPEG でない・空ファイルのいずれでも**例外は出ず `None` 相当**が返る。

**受け入れ:**
- `DateTimeOriginal` を持つ JPEG から壁時計が取れる
- タグが無い JPEG、EXIF が壊れた JPEG、JPEG ですらないファイルで `None` が返り、例外が出ない
- `source: exif` のプロファイルで、EXIF がある画像は `captured_at_source = "exif"`、
  無いものは `fallback` の値になる
- `timezone_policy: none` では壁時計がそのまま（UTC として）記録される
- **動画では `exifread` が呼ばれない**（logger にレコードが 1 件も出ない）
- **`captured` と `resolve_captured` を両方与えると `ValueError`、どちらも与えなくても
  `ValueError`**（minor 1）
- **手順 5 で解決される**：検証済みの staging を読んでおり、`metadata_json` に載って
  手順 7 で commit される
- `test_crash_consistency.py` の 11 段を `source: exif` のプロファイルでも通す

**変異試験:**
- `details=False` を `True` にする → （検出できない見込み。**まず落とせないか試してから記録する**）
- 例外の握りを外す → 壊れた EXIF の試験が落ちること
- `exif_wall` を無視して常に fallback にする → `captured_at_source` の試験が落ちること
- **`resolve_captured` の呼び出しを手順 5 より前（検証前）へ動かす** → 手順の試験が落ちること
- **`resolve_captured` の呼び出しを手順 7 の後へ動かす** → `metadata_json` の試験が落ちること
- **`kind` の振り分けを外して動画でも呼ぶ** → 「動画では呼ばれない」が落ちること

**実施結果（2026-08-19）: 11 件中 10 件を検出。**

**最初は 7 件しか検出できなかった。素通り 4 件のうち 3 件はテストの穴だった。**

| 素通りしていた変異 | 原因 | 対処 |
| --- | --- | --- |
| `source` の宣言を見ずに `exif_wall` を使う | **狙いの分岐に届いていなかった。** ファイル名が当たる筋書きで試していたので `filename` の枝が先に返り、`exif` の枝を一度も通らない | 名前が当たらない筋書き（`PANO_0001.JPG`）で試す |
| 解決を手順 4 より前へ動かす | **観測できる差が無かった。** 実体は手順 2〜3 で書き終わっているので、手順 4 の前でも後でも同じサイズが見える | `_checkpoint` を記録し、**手順の記録そのものと突き合わせる**（`STEP_VERIFIED` < 解決 < `STEP_METADATA`） |
| 動画でも EXIF を読む | **結果が同じになる筋書きしか試していなかった。** 動画で読みに行っても `None` が返って fallback に落ちるので、`captured_at_source` は読んでも読まなくても `mtime` | `read_datetime_original` をスパイして**呼んだかどうか**を見る。渡るのはステージ済みのパス（拡張子の無い UUID）なので、名前ではなく回数と中身で判定する |

**残る 1 件は `details=False` を `True` にする変異で、検出できない。**
`details=True` にすると `exifread` はサムネイルと MakerNote も読むが、
`DateTimeOriginal` の値は変わらない。**出力に差が出ないので、出力で検証する
テストでは捕まえられない。** これは読む量を絞るための防御であって、値を決める
判断ではない（§14 の「信頼できない入力から読む量を減らす」）。

---

### Task 4: ビルトイン `generic-dcim` と `canon-eos`

**Files:** Create `app/src/mediaferry/core/profiles/builtin/generic-dcim.yaml`,
`canon-eos.yaml` / Modify `app/src/mediaferry/core/profiles/model.py` /
Test `app/tests/test_profile_matching.py`, `app/tests/test_profile_model.py`

`matching.py` は既に `GENERIC_SLUG = "generic-dcim"` を参照していて、**定義だけが無い**。

**`canon-eos`:**

| 項目 | 値 | 理由 |
| --- | --- | --- |
| `hints.usb_ids` | **空** | カードリーダー経由が前提（§2）。見える USB ID はリーダーのもので、機種の手がかりにならない |
| `hints.volume_labels` | `["EOS_DIGITAL"]` | Canon の既定ラベル。**単独では確定しない**（§6） |
| `require.roots` | `["DCIM"]` | |
| `require.filename_pattern` | `^(IMG_\d{4}\.(JPG\|CR2)\|MVI_\d{4}\.MOV)$` | `iter_names` はサブディレクトリを辿るので `DCIM/100CANON/` の下に当たる |
| `scan.extensions` | `["JPG", "CR2", "MOV"]` | Immich は CR2 を扱える |
| `timestamp` | `exif` / fallback `mtime` / `none` | §6 が「Canon はこちら（EXIF にローカル時刻を書くため補正が不要）」と決めている |
| `merge.enabled` | **`false`** | 下記 |
| `immich.tags` | `["Canon EOS 70D"]`、`tag_pre_existing: true` | |
| `immich.fix_datetime_after_upload` | **`false`** | `timezone_policy: none` なので補正する対象が無い |

**`merge.enabled: false` にする理由:** EOS 70D の 4GB 分割が、ファイル名の連番から
「分割」と「連続した別撮影」として区別できるかを、**実データ無しには確かめられない**。
DJI の `min_part_size_gib: 15` に当たる根拠が無い。**誤結合は取り消しに手間がかかる**
（公開済みの `media_file` を取り残す）ので、無効にして Phase 4 で入った手動結合に委ねる。
実カードが手に入ったら `phase1-manual-checklist.md` の項目で確かめて有効化する。

**`generic-dcim`:** `hints` 無し、`require` は「`DCIM` の下にファイルが 1 件以上」、
`timestamp` は `exif` / `mtime` / `none`、`merge` 無効、タグ無し、日時補正無し。
`resolve_profile` は既に `generic-dcim` を最後へ回す tie-break を持っている。

**`model.py` の追加検証**（プロファイルがユーザの書き換え対象になるため）:

- `filename_pattern` / `sequence_pattern` / `timestamp.pattern` を `re.compile` に通し、
  **長さの上限**を掛ける。壊れた式を保存できると、そのボリュームが恒久的に「対象外」になる
- `merge.enabled: false` のとき `sequence_pattern` / `output_name` の必須を外す
  （Canon と generic は持たない）

**catastrophic backtracking への対処 —— マッチ側を `regex` に替える**

長さの上限は効かない。`(a+)+$` は 8 文字で、41 文字の入力に対して事実上停止しない。
マッチは `VolumeService` の `RLock` の中で最大 2000 件に当たるので、**1 本の悪い式で
`GET /devices` も watcher も固まる**。

1 巡目では「保存時に子プロセスで敵対的標本を試す」案を書いたが、
**2 巡目で「標本は容易に迂回できる」と指摘され、取り下げた。** `(z+)+$` は `a` の標本を
素通りする。**固定標本は lint であって、固まらないことの保証にならない。**

**実測して替える**（2026-08-19、この環境で計測）。

| 式 | `re` | `regex`（timeout なし） | `regex`（`timeout=0.5`） |
| --- | --- | --- | --- |
| `(a+)+$` に `"a"*40+"!"` | **10 秒で打ち切り（破綻）** | **0.000 秒** | 0.000 秒 |
| `(a*)*b` に同上 | — | **0.000 秒** | 0.000 秒 |
| `(a|a)+$` に同上 | — | 8 秒で打ち切り（破綻） | **`TimeoutError` を 0.500 秒で送出** |
| `^DJI_(\d{14})_` に実ファイル名 | — | 0.0001 秒 | 0.0001 秒 |

- **`regex` は代表的な破綻パターンを自前の最適化で潰す**（`(a+)+$` / `(a*)*b`）
- **潰しきれないもの（`(a|a)+$`）には `timeout=` が実測どおり壁時計の上限として効く**
- 構文は `re` の上位互換なので、既存のビルトインの式はそのまま動く

**したがって、プロファイルのマッチは `regex` に統一し、必ず `timeout` を渡す。**

- 対象は `matching.py`（`filename_pattern`）と `timestamps.py`（`timestamp.pattern`）と
  結合の `sequence_pattern`
- **`TimeoutError` は「一致しなかった」ではなく失敗として扱う。** そのボリュームを
  「対象外」に落として理由を出す（黙って不一致にすると、原因が画面から分からない）
- 保存時の検証は `regex.compile` と長さの上限のまま。**固まらないことの保証は実行時の
  `timeout` が持つ**ので、保存時に敵対的標本を試す必要は無くなった
- 実行時の件数上限は既存のまま（`NAME_SCAN_LIMIT = 2000`）

**受け入れ:**
- Canon 風の合成カード（`DCIM/100CANON/IMG_0001.JPG` 等）が `canon-eos` に確定する
- `DCIM` に無関係なファイルだけを置いたカードが `generic-dcim` に落ちる
- `DCIM` を持たないボリュームが「対象外」になる
- **DJI のカードが `generic-dcim` に落ちない**（順位付けの回帰）
- 壊れた正規表現、長すぎる正規表現が `ProfileInvalid` になる
- **`(a|a)+$` のような、`regex` の最適化を抜ける式でも、`timeout` で有限時間で降りる**
  （そのボリュームは「対象外」になり、理由が出る）
- 正常な式（ビルトインの 3 つを含む）は timeout に掛からない
- `regex` に替えた後もビルトインの判定結果が変わらない（回帰）

**変異試験:**
- `resolve_profile` の tie-break から `generic-dcim` の項を落とす → DJI が generic に落ちる試験が落ちること
- `merge.enabled` の必須外しを逆にする → Canon の定義が読めなくなること
- 正規表現の長さ上限を外す → 対応する試験が落ちること
- **`timeout` を渡さない／十分大きくする** → 悪性の式の試験が落ちること
- **`regex` を `re` に戻す** → 悪性の式の試験が落ちること
- **`TimeoutError` を「不一致」として握る** → 「理由が出る」試験が落ちること

**実施結果（2026-08-19）: 12 件中 12 件すべて検出。**

最初は 11 件中 10 件で、素通りした 1 件は **`search()` の経路を通るテストが
無かった**こと（`matching` は `match()`、`timestamp.pattern` と
`sequence_pattern` は `search()` を使う）。`timestamp.pattern` に悪性の式を
与える試験を足して検出できるようにした。

**その試験を書くときに 2 回外した。** 記録に残す。

1. `ts` の名前付きグループが要る（既存の検証）。`(a|a)+$` は保存時に弾かれる
2. `(?P<ts>\d{4})(a|a)+$` にしたら**破綻しなくなった** —— `\d{4}` が先頭で
   即座に失敗するので backtracking に入らない。変異試験は素通りしたままだった。
   **「悪性の式のつもり」で書いた式が本当に破綻するかを測ってから使う**
   （`(?P<ts>(a|a)+)$` は実測で 6 秒でも終わらない）

**ReDoS の試験は必ずスレッドの上限つきで書く。** 直に呼ぶと、`re` へ戻す回帰の
ときにテストが「失敗」ではなく**ハング**する（この試験はまさにその状態を再現
するために書かれている）。回帰は assert で落ちなければならない。

---

### Task 5: プロファイル編集 API

**Files:** Modify `app/src/mediaferry/db/profiles.py`, `app/src/mediaferry/api/routes_system.py` /
Test `app/tests/test_profile_registry.py`, `app/tests/test_api_profiles.py`

スキーマ（`builtin` 列、`archived_at`、版の不変 trigger、複合外部キー）は `0002` で揃って
いるので**移行は要らない**。

| メソッド | 経路 | 内容 |
| --- | --- | --- |
| GET | `/profiles` | **修正**。`builtin` / `archived_at` / 定義本体を返す |
| GET | `/profiles/{slug}` | 現行リビジョンの定義 |
| POST | `/profiles` | 新規作成（`slug` はここで確定、以後不変） |
| PUT | `/profiles/{slug}` | 新リビジョンを作る。**`builtin` なら 409** |
| POST | `/profiles/{slug}/duplicate` | ビルトインからユーザ定義を作る |
| POST | `/profiles/{slug}/archive` | archive（削除はしない） |

**中心の判断: ビルトインへの mutation は `duplicate` を除いて全部拒む。** 現行の
`_upsert_revision` は `builtin` を見ないので、放置すると**次のアプリ更新で `sync_builtins` が
ユーザの編集を黙って上書きする**。「編集しようとすると複製が作られる」（§6）は画面の作法で、
API は明確に断る。

**`PUT` だけでなく `archive` も拒む**（計画レビュー 1 巡目の major）。`sync_builtins` は
`archived_at` を戻さないので、**一度 archive されたビルトインは再起動しても復活しない**
——「読み取り専用」が破れたまま元に戻せなくなる。ガードは**1 つの関数に集約し、
`duplicate` 以外のすべての mutation が通る**形にする（ルータごとに書くと次に足す口で
書き忘れる、という Phase 4 の判断と同じ）。

**`slug` は作成後不変。** ライブラリのパス（`library/<slug>/`）に使われるので、変えると
過去の取り込みが宙に浮く。`PUT` の本文に `slug` が含まれていて現在と違えば 400。

**archive は削除ではない。** 使用済みのリビジョンを指す行が `volume_instance`・
`media_file`・`merge_group` にあり、外部キーが `ON DELETE RESTRICT`。archive すると
`active()` から外れて新しい判定に使われなくなるだけで、過去の解釈は変わらない。

**受け入れ:**
- ビルトインへの `PUT` が 409 で断られる
- **ビルトインへの `archive` も 409 で断られる**
- ユーザ定義の `archive` は通る
- `duplicate` が `builtin = 0` の新プロファイルを作り、**元のビルトインは変わらない**
- `PUT` が新リビジョンを作り、**旧リビジョンは残る**（版の不変 trigger に触れない）
- 不正な定義（未知のキー、壊れた正規表現、`..` を含む root）が `ProfileInvalid` →
  400 の封筒になり、**リビジョンは作られない**
- archive 済みのプロファイルが `resolve_profile` の候補に入らない
- archive 済みのリビジョンを指す既存レコードが読める
- `slug` の変更が 400

**変異試験:**
- `builtin` の拒否を外す → `PUT` と `archive` の両方の試験が落ちること
- **ガードを `PUT` にだけ残して `archive` から外す** → archive の試験が落ちること

**実施結果（2026-08-19）: 10 件中 10 件すべて検出。**

素通りした 1 件は「一覧が archive 済みを隠す」で、**それを見るテストが無かった**。
archive は削除ではない（使用済みの版は参照が残る）ので、一覧から消すと
「外した」のか「消えた」のか区別が付かない。**一覧には出したうえで印を付ける**
契約にして、試験を足した。

**実装で分かったこと**: `ProfileRef` に `builtin` と `archived` を載せた。
画面はビルトインに錠前を出し、編集の代わりに「複製して編集」を出す（§6）ので、
一覧の 1 件ごとにその区別が要る。読み取り用の `all()`（archive 済みを含む）と
判定用の `active()`（含まない）を分けた —— **同じ一覧を両方の用途に使うと、
どちらかが必ず間違う。**
- 検証を commit の後に動かす → 「不正な定義でリビジョンが作られない」が落ちること
- `active()` の `archived_at IS NULL` を外す → archive の試験が落ちること

---

### Task 6: `recompute_timestamps` ジョブ

**Files:** Create `app/src/mediaferry/jobs/recompute.py`,
`app/src/mediaferry/db/migrations/0011_captured_at_revision.sql` /
Modify `app/src/mediaferry/api/jobs_wiring.py`, `app/src/mediaferry/api/app.py`,
`app/src/mediaferry/api/routes_system.py`, `app/src/mediaferry/adapters/publisher.py` /
Test `app/tests/test_recompute.py`, `app/tests/test_db_migrate.py`

§6 が「タイムスタンプ解釈やタイムゾーンを変えた場合、既存データへの再計算は自動では
行わず、`recompute_timestamps` ジョブとして明示的に実行する」と決めている。

**ファイルは動かさない。** ライブラリのパスは `library/<slug>/<カード上の相対パス>` で
**`captured_at` を含まない**（§7）ので、公開済みの実体は 1 つも動かない。

#### provenance を壊さない（計画レビュー 1 巡目の blocker）

`media_file.profile_revision_id` は「**そのレコードが使用した不変のリビジョン**」という
既存の契約（§6）。再計算で `captured_at` だけを新しい定義から作ると、
**旧リビジョンを指しながら値は新リビジョン由来**という嘘の行ができる。かといって
`profile_revision_id` を新しい版へ書き換えると、今度は
**timestamp 以外の新定義（`scan` / `merge` / `immich`）もそのファイルに適用したと偽る**。

**列を分ける。`0011` で `media_file.captured_at_revision_id` を足す。**

```sql
-- captured_at を算出したときに使ったプロファイルリビジョン。
-- profile_revision_id（取り込みに使った版、不変）とは別の問い。
ALTER TABLE media_file ADD COLUMN captured_at_revision_id TEXT
    REFERENCES profile_revision(id) ON DELETE RESTRICT;

-- 既存行は取り込み時の版で算出されている。
UPDATE media_file SET captured_at_revision_id = profile_revision_id;

-- **単一 FK では「同じプロファイルの版であること」を守れない**（別プロファイルの
-- 版も指せる）。SQLite の ALTER TABLE では複合 FK も NOT NULL も後から足せないので、
-- trigger で同じ強さを作る。profile_revision には UNIQUE (profile_id, id) があるので
-- 突き合わせられる（volume_instance と media_file が使っている複合 FK と同じ根拠）。
CREATE TRIGGER media_file_captured_revision_insert
BEFORE INSERT ON media_file
WHEN NEW.captured_at_revision_id IS NULL
  OR NOT EXISTS (SELECT 1 FROM profile_revision
                  WHERE id = NEW.captured_at_revision_id
                    AND profile_id = NEW.profile_id)
BEGIN
    SELECT RAISE(ABORT, 'captured_at_revision_id must be a revision of the same profile');
END;

CREATE TRIGGER media_file_captured_revision_update
BEFORE UPDATE OF captured_at_revision_id, profile_id ON media_file
WHEN NEW.captured_at_revision_id IS NULL
  OR NOT EXISTS (SELECT 1 FROM profile_revision
                  WHERE id = NEW.captured_at_revision_id
                    AND profile_id = NEW.profile_id)
BEGIN
    SELECT RAISE(ABORT, 'captured_at_revision_id must be a revision of the same profile');
END;
```

**table rebuild ではなく trigger にする理由:** `media_file` は `upload_record` /
`artifact_staging` / `merge_group_member` から参照されている。作り直すと参照側の扱いに
気を遣うことになり、**移行の失敗が最も高くつく表**でそれをやる利は無い。
trigger なら「必ず値を持ち、同じプロファイルの版である」という 2 つの契約を、
既存の `UNIQUE (profile_id, id)` を使って同じ強さで守れる。

- 取り込み時は `profile_revision_id` と同じ値が入る
- `recompute` はこの列**だけ**を新しい版へ進め、`profile_revision_id` は触らない
- 画面は 2 つがずれている行を「日時を再計算済み」として出せる

**外部キーを付ける**ので、参照されているリビジョンは削除できない（既存の
`ON DELETE RESTRICT` と同じ扱い）。

#### 判断

- **対象はプロファイル単位**（`POST /profiles/{slug}/recompute`）。ボリューム単位ではない
- 直すのは `captured_at` / `captured_at_source` / `captured_at_tz` / `captured_at_note` と
  `captured_at_revision_id` の 5 列だけ
- **再計算の入力は role ごとに違う**（下記）
- **リモートは黙って書き換えない。** 送信済みで `captured_at` が変わったレコードは、
  **`needs_recheck` へ戻す**（下記）。**`recompute` 自身は Immich に触らない**
- キャンセルとリースは他のジョブと同じ作法。件数が多いので、
  **バッチごとにキャンセルとリースの両方を見る**（Phase 3 の Rechecker と同じ形）

#### 再計算の入力（計画レビュー 2 巡目の blocker）

**`media_file` だけでは再計算できない。** 3 つとも実装で確かめた。

1. **`timestamp.source: filename` はカード上の原名に当てる。** `media_file.rel_path` は
   **公開先の名前**で、衝突時には接尾辞が付いている（§9.3）。原名を当てると外れる
2. **`ArtifactRequest.source_rel_path` はどこにも保存されていない。** `media_file` にも
   `metadata_json` にも無く、`desired_rel_path` を組み立てるためだけに使われている
3. **derived の `captured_at` は算出ではなく継承。** `Merger` は
   `_captured_of(members[0])` で先頭 member の値をそのまま渡している。
   「`exif` なら公開済みファイルを読む」を一律に当てると、**結合出力そのものの
   EXIF / mtime を読むことになり、意味が変わる**

**role ごとに規則を決め、順序を固定する。**

| role | `filename` | `exif` | `mtime` |
| --- | --- | --- | --- |
| `original` | **`source_entry.rel_path`**（`source_entry.media_file_id` で JOIN）に当てる | 公開済みファイルを読む | `media_file.mtime_ns` |
| `derived` | **当てない** | **読まない** | **使わない** |

**`derived` は先頭の active member から再導出する。** 取り込みと同じ形（継承）を保つ。
したがって**順序が要る**。

```
1. original を再計算する（source_entry / 公開済みファイル / mtime から）
2. derived を、その結合グループの先頭の active member の captured_at から derive する
```

**`source_entry` が無い `original` は再計算しない。** 理由を添えて飛ばし、件数を出す
（カードを再フォーマットして `source_entry` が消えている行がありうる。**勝手に mtime へ
落とすと、正しかった値を壊す**）。

#### 送信済みのものをどう知らせるか（計画レビュー 1 巡目の major）

「一覧に出して知らせる」だけでは配送先も完了条件も無い（Phase 4 と同型の欠け）。
**既にある経路に載せる。**

**`awaiting_datetime_approval` へ直接戻してはいけない。** 一度そう書いたが、実装を読んで
取り下げた。理由は 3 つとも構造的なもの:

1. **その状態は `tagging` からしか入れない。** `Uploader` が
   `finish_owned(..., expect_state="tagging")` で CAS しており、外から `complete` を
   直接そこへ動かすと、誰も通っていない遷移を新設することになる
2. **「現在値」を埋められない。** 承認画面が出す `remote_datetime_original` は、
   承認待ちにするその瞬間に Immich へ 1 度問い合わせて控えた値
   （`Uploader._observed_datetime`）。`recompute` はローカルのジョブで Immich の
   クライアントを持たない。**空のまま承認待ちにすると、Phase 4 の「読めなかった値を
   『変更なし』にしない」に反する**
3. クライアントを持たせれば、preflight・リース・キャンセル・向き先の再確認という
   **Phase 3 が 7 巡かけて固めた契約を丸ごと持ち込む**ことになる

**代わりに `needs_recheck` へ戻す。**

```
complete ──(recompute。captured_at が変わり、かつプロファイルが日時を書き戻す)──> needs_recheck
```

- `needs_recheck` は **`CLAIMABLE_STATES` に入っている**ので、その宛先の次の
  アップロードジョブが普通に claim して、`checking` から**パイプラインを再実行する**
- 再実行は**リースと preflight と guard の下**で走り、`tagging` の後で日時の判断を
  新しい `captured_at` でやり直す。承認が要るなら、そこで**現在値を観測したうえで**
  `awaiting_datetime_approval` に入る。要らなければ `complete` に戻る
- **`recompute` は Immich に触らない**という性質が保たれる

**戻すのは「プロファイルが日時を書き戻す」ものだけ。** `immich.fix_datetime_after_upload`
が偽のプロファイル（`canon-eos` と `generic-dcim` はどちらも偽）では、こちらがリモートの
日時を書いたことが無いので、ローカルの `captured_at` が変わってもリモートに差は生じない。
**戻すと、何も変わらない再送を全件に強いる。**

遷移は `recompute` の当該 `media_file` の更新と**同じトランザクション**で、
`state = 'complete'` を条件にした CAS で行う。割ると「値は新しいのに `complete` のまま」が
残る。

**受け入れ:**
- プロファイルの `timezone` を変えて実行すると、そのプロファイルの `media_file` の
  `captured_at` が変わる
- **他のプロファイルの行は変わらない**
- ファイルが 1 つも動かない（`final_rel_path` が不変）
- **`profile_revision_id` は変わらず、`captured_at_revision_id` だけが進む**
- 送信済みの資産のリモート日時は**この時点では変わらない**（`recompute` は Immich に触らない）
- **送信済みで値が変わったレコードが `needs_recheck` になる**
- **値が変わらなかった送信済みレコードは `complete` のまま**
- **`fix_datetime_after_upload` が偽のプロファイルでは、値が変わっても `complete` のまま**
- `needs_recheck` になったレコードは、次のアップロードジョブが claim して再実行し、
  **現在値を観測したうえで** `awaiting_datetime_approval` か `complete` に落ち着く
- キャンセルすると途中で止まり、**処理済みの分は commit されている**
- `source: exif` の `original` は、公開済みファイルの EXIF から再計算される
- **`source: filename` の `original` は `source_entry.rel_path`（カード上の原名）に当たる。
  衝突接尾辞の付いた `media_file.rel_path` では外れることを、接尾辞のある行で確かめる**
- **`derived` は先頭の active member の `captured_at` から derive され、
  結合出力そのものの EXIF / mtime は読まれない**
- **`original` を全部直してから `derived` を直す**（順序の回帰）
- `source_entry` が無い `original` は飛ばされ、件数と理由が出る
- `0011` が既存 DB に適用でき、既存行の `captured_at_revision_id` が
  `profile_revision_id` と等しくなる

**変異試験:**
- プロファイルの絞り込みを外す → 「他のプロファイルの行は変わらない」が落ちること
- バッチの合間のキャンセル確認を外す → キャンセルの試験が落ちること
- リモートも書き換える → 送信済み資産の試験が落ちること
- **`profile_revision_id` も一緒に進める** → provenance の試験が落ちること
- **`captured_at_revision_id` を更新しない** → 同上
- **`filename` の入力を `media_file.rel_path` にする** → 衝突接尾辞の試験が落ちること
- **`derived` にも `exif` / `filename` を当てる** → derived の試験が落ちること
- **original と derived の順序を入れ替える** → 順序の試験が落ちること
- **`source_entry` の無い行を mtime で埋める** → 「飛ばす」試験が落ちること
- **値が変わっていない送信済みレコードも `needs_recheck` にする** → 「変わらなければ据え置き」が
  落ちること
- **`fix_datetime_after_upload` の判定を落として全件戻す** → 対応する試験が落ちること
- **差し戻しを別トランザクションにする** → 途中で落ちたときに「値は新しいのに `complete` の
  まま」が残る筋書きが落ちること
- **CAS の `state = 'complete'` 条件を外す** → 進行中のレコードを踏む筋書きが落ちること

#### 実装で分かったこと

- **計画の `merge_group_member` は実在しない。** 表の名前は `merge_member`（`0003`）。
- **衝突接尾辞は、先頭に錨を打った pattern では判別材料にならない。**
  `DJI_20260817143000_0001_D.MP4` の別名は `DJI_20260817143000_0001_D_<stamp>.MP4` で、
  `^DJI_(?P<ts>\d{14})_` はどちらにも同じ ts で当たる。**「原名に当てる」を検証するには、
  錨の無い pattern（`_(?P<ts>\d{14})`）を持つプロファイルが要る** —— 原名が当たらず
  fallback の `mtime` へ落ち、別名なら接尾辞の壁時計を拾う、という差が出る形にした。
- **`derived` に規則を当ててしまう変異は、値だけでは捕まらない。** 出力名の壁時計は
  先頭パートのものなので、`filename` を当てても同じ瞬間になる。**`captured_at_source`
  まで見る**（先頭 member が `mtime` へ落ちている筋書きにすると、当てた側は `filename`
  になる）。
- **`recompute` は `ProfileRef` だけを受け取り、Immich のクライアントを持たない。**
  「リモートを触らない」は引数の形で守られている。
- **supersede した結合の出力は「先頭の active member」を持たない**ので飛ばす。
  非 active から derive し直すと、退役した出力に新しい値を書くことになる。
- **他プロファイルの行を踏む変異は、`0011` の trigger が塞ぐ**（`captured_at_revision_id`
  が別プロファイルの版になり ABORT する）。絞り込みの試験は、その手前で
  「`source_entry` が無いから飛ばす」に落ちないよう、隣のプロファイルにも原名を持たせる。

**変異試験の結果（22 件中 21 件を検出）。** 素通り 1 件は下記。

| 素通り | 見立て |
| --- | --- |
| **バッチの合間のキャンセル確認を外す（単独）** | **相方がマスクしている。** `request_cancel` は `running` を `cancelling` にするので、続けて呼ぶ `assert_lease` が必ず `LeaseLost` を上げ、その受け口が同じ「中止して降りる」へ落とす。**対（先の確認 + `LeaseLost` の受け口）で壊すと検出できる**ので、冗長さは意図であって削ってよい根拠にはならない（Rechecker と同じ形） |

**素通りから直したもの**（いずれもテストの穴で、実装は変えていない）:

- **絞り込みの試験**が「飛ばす」分岐で先に落ちていた → 隣のプロファイルに原名を持たせた
- **非 active な member を使う筋書きが無かった** → supersede した結合の出力を足した
- **動画で EXIF を読んでも結果が変わらない**（`None` が返って fallback に落ちる）
  → **呼んだかどうか**をスパイで見る試験に変えた

---

### Task 7: プロファイル編集 UI（設定画面）

**Files:** Modify `web/src/screens/Settings.tsx`, `web/src/api/` / Test `web/src/screens/screens.test.tsx`

**契約:**

- 一覧はビルトインとユーザ定義を**区別して**出す。ビルトインには錠前を出し、
  編集ボタンの代わりに「複製して編集」を出す
- 編集は **YAML のテキストエリア 1 枚**。GUI で構文を組み立てない（`filename_pattern` は
  正規表現で、フォームに落とすと表現力が落ちるうえ規則が 2 箇所に散る）
- 保存すると新リビジョンができることを画面に書く。**リビジョン番号を出す**
- 検証エラー（`ProfileInvalid`）は**行が分かる形**で日本語にして出す
- `POST /profiles/{slug}/test` を「接続中のボリュームで試す」ボタンとして出す
  （Phase 4 で API だけ入っていて**画面から呼べていない**。§8「受け入れの経路に
  入っていない機能は、無いのと同じ」）
- `timestamp` を変えた保存の後に **`recompute_timestamps` を促す**。自動では走らせない
- archive は不可逆でないが、**使用中のプロファイルを archive すると新しい判定に使われなく
  なる**ことを確認に出す

**受け入れ（vitest）。E2E に寄せず、操作ごとに 1 つずつ持つ:**

- ビルトインで編集ボタンが出ず、「複製して編集」が出る
- **新規作成**が `POST /profiles` を呼び、作られた slug を表示する
- 保存が `PUT` を呼び、**リビジョン番号が上がって表示される**
- 検証エラー（`ProfileInvalid`）が日本語で、**どこが悪いかが分かる形**で出る
- **`test` ボタン**が `POST /profiles/{slug}/test` を呼び、判定結果と理由を出す
- **`archive`** が確認を経て呼ばれ、ビルトインでは 409 の日本語が出る
- **`recompute` の起動**が呼ばれ、ジョブ画面への導線が出る
- `timestamp` を変えた保存の後に recompute を促す文言が出る

#### 実装で分かったこと

- **`js-yaml` を足した**（`web/package.json` の依存が 1 つ増えた最初の例）。JSON の
  テキストエリアでも「GUI で構文を組み立てない」は満たせるが、プロファイルは
  正規表現だらけで、JSON だとバックスラッシュを二重に書くことになる（`\\d{14}`）。
  ビルトインも YAML で書いてあるので、写して直す動線が素直につながる。
  **v5 は自前の型を持つ**ので `@types/js-yaml` は要らない（入れると衝突する）。
- **`errors.ts` の `validation_failed` は定型文だけで `detail` を捨てていた。**
  「入力の形式が正しくありません」だけでは、1 枚の YAML のどこを直せばよいか
  分からない。`detail` は「こちらが書いた日本語だけ」という API の契約があるので、
  この code に限って添える形にした（`WITH_DETAIL`）。
- **YAML の構文エラーはサーバへ送る前に落とす。** 送っても 400 が返るだけで、
  行番号を知っているのはパーサだけ。
- **ビルトインの `archive` は画面に出さない。** API も 409 で拒むが、`sync_builtins` が
  `archived_at` を戻さない以上、押せてしまう時点で設計が甘い。計画の受け入れ条件
  「ビルトインでは 409 の日本語が出る」は、**「ボタンを出さない」の確認に置き換えた**。
- **「複製して編集」は slug を先に決めさせる。** slug は作成後 immutable
  （`library/<slug>/` に使う）ので、`<元>-copy` を黙って確定させると取り返しがつかない。
  `POST /profiles/{slug}/duplicate` を実際に呼ぶ経路にした（Phase 4 の「API はあるが
  画面から呼べない」を作らない）。
- **`userEvent.type` は `{` と `[` を修飾キーの記法として読む。** YAML / JSON を
  流し込む試験は `fireEvent.change` を使う。

**変異試験: 11 件中 11 件を検出。**

---

### Task 8: デバイス画面（信頼登録 UX と複数デバイス）

**Files:** Modify `web/src/screens/Devices.tsx`, `web/src/api/` / Test `web/src/screens/screens.test.tsx`

**契約:**

- **複数ボリュームを並べ、それぞれ独立に操作できる**（Osmo は内蔵と SD の 2 つを同時に出す）
- 各ボリュームに: プロファイル名、判定理由、`identity_confidence`、信頼状態、
  **自動取り込みの状態**（「信頼済み。挿すと自動で取り込みます」／「未承認。承認すると
  以後は自動で取り込みます」／「確度が低いため自動取り込みしません」）
- **「対象だが中身が無い」（provisional）と「対象外」を区別して出す**（§6 / Phase 0 の発見 B）
- **対象外のボリュームも理由付きで出す**（§13）
- **信頼の限界を明示する**（§12.1）。「同じ UUID の別のカードや、復元したカードを
  取り違えることがあります」を承認のダイアログに出す
- 承認は不可逆ではないが、**以後そのカードが自動で NAS へコピーされるようになる**ので
  確認を取る（§12.1 のプライバシー上の理由）
- `AUTO_IMPORT` が `off` のときは、その旨と設定画面への導線を出す

**受け入れ（vitest）:** 2 ボリュームが独立に描画される。provisional と対象外の文言が違う。
承認ダイアログに限界の記述が出る。`off` のときに導線が出る。

#### 実装で分かったこと

- **`AUTO_IMPORT` は `GET /settings` から読む。** `/devices` に足すと同じ値の出所が
  2 つになる。画面が 2 本 GET する代償のほうが安い。
- **ボタンのラベルにボリューム名を入れる。** 2 枚並ぶと「スキャン」が 2 つになり、
  `getByRole({ name })` が「独立に操作できる」を検証できない（**土台の弱さが契約の
  検証を素通りさせる**）。
- **自動取り込みの文言は「いま始まるか」から作る**（実装差分レビュー 2・3 巡目で
  2 度直した）。判定は `watcher.py` の `CANDIDATES` と同じ条件
  （`AUTO_IMPORT`・`provisional`・`identity_confidence`）で行い、**画面と確認ダイアログが
  同じ関数を見る**（`autoImportOutlook`）。片方だけで判定すると同意の内容と実挙動がずれる。
  始まる場合は「**いま入っている中身も含めて**、数秒後から」、始まらない場合は
  「信頼は記録するが、いまは始まらない」と理由付きで書く。**どちらも断言しない側へ
  倒さない** —— 「次に挿したときから」も「必ずコピーされます」も、実挙動と違う。
- **確認ダイアログには「確認を取る理由」も書く。** 「取り違えることがある」だけだと、
  *なぜ* 確認するのか（以後そのカードが自動で NAS へコピーされる）が落ちる。
  変異試験で最初に素通りしたのがここだった。

**変異試験: 11 件中 11 件を検出**（Devices 9 件 + ConfirmDialog 2 件）。

---

### Task 9: 受け入れとドキュメント

**Files:** Modify `web/e2e/journey.spec.ts`, `app/tests/system/` / Modify `docs/design.md`,
`docs/HANDOFF.md`, `docs/phase1-manual-checklist.md`

**E2E（Playwright）で通す筋書き:**

1. **Canon 風の合成カードを挿す** → `canon-eos` と判定され、理由が画面に出る
2. **2 枚同時に挿す**（DJI 風と Canon 風）→ 両方が並び、独立にスキャンできる
3. **承認して信頼登録** → 自動取り込みが始まり、ジョブが積まれる（画面を操作せずに）
4. **プロファイルを複製して編集** → 新リビジョンができ、`test` で判定が変わる
5. **`recompute_timestamps`** → 一覧の撮影日時が変わり、ファイルは動かない

**`phase1-manual-checklist.md` に足す項目**（実機が要るもの）:

- Canon EOS 70D の実カードで `canon-eos` に確定するか（ラベル、`DCIM/100CANON/` の構成）
- **EOS 70D の 4GB 分割が連番から判別できるか**（`merge.enabled` を有効化してよいかの判断）
- **Canon の MOV の `creation_time` が壁時計か UTC か**（第 4 の timestamp source を
  足すかの判断）
- CR2 を Immich が受け取るか

**`design.md` に書き戻す:** §21 に「Phase 5 の実装で確定した事項」を足す。
**`subscribe` を採らなかった判断とその根拠**を §9.2 の近くに残す（設計にはプロトコルとして
残っているので、なぜ使っていないかが分からないと次の担当が実装しようとする）。

#### 実装で分かったこと

- **E2E が Task 8 の抜けを 1 つ捕まえた。** 判定の理由を「対象外のときだけ」出していた。
  一致したときの理由（`DCIM に一致するファイルが 2 件`）も、プロファイルを直す手がかりに
  なる。**vitest では気付けなかった**（画面の一部だけを見ていたので、「理由が無い」ことが
  試験の穴ではなく仕様に見えた）。
- **土台を 2 枚差しにした。** `system_app` は 1 枚しか差していなかったので、「複数デバイスを
  独立に扱える」を受け入れの経路に載せられなかった。`FakeMountManager` を `volume_key` で
  振り分ける形へ広げ、`lister` が 2 本返すようにした。
- **`DEFAULT_TIMEZONE` を env に置いたままでは、再計算の筋書きが通らない。** env にあると
  `locked` になり画面から変えられない。`system_app(default_timezone=None)` と
  `serve.py --timezone-from-db` を足して、DB 側の設定として持てる状態を作った。
- **Canon の合成カードは EXIF 付きの JPEG で作る**（`exif_fixtures.a_jpeg_with`）。
  `timestamp.source: exif` が公開済みファイルから読めることまで通る。
- **`identity_confidence` は tick 数に依存する。** watcher が 1 度観測して指紋を憶えた後に
  API の `refresh` が走るので、初回の `GET /devices` でも `high` になりうる。
  受け入れで固定してよいのは `trusted` の側だけ。

---

## Phase 5 の完了条件（§20）

- [x] **EOS 70D の SD カードを取り込める** —— 合成カードと fake ブローカーで通す。
      実カードは `phase1-manual-checklist.md` へ（**実機が無いので、この環境で確かめられる
      のはここまで**）
- [x] `generic-dcim` にフォールバックし、`DCIM` を持たないボリュームは対象外になる
- [x] プロファイルを画面から複製・編集でき、新リビジョンができる。ビルトインは守られる
- [x] 信頼登録済みのカードを挿すと、画面を操作せずに取り込みが始まる
- [x] **カードを挿したまま画面で承認しても、次の tick で取り込みが始まる**（観測トークンは
      動かないため。計画レビュー 1 巡目の blocker 1）
- [x] **停止要求から有限時間で降りる**（ブローカーが応答しない場合を含む）
- [x] **EXIF が読めないファイル（タグ無し・破損・動画）で取り込みが止まらず、`fallback` の
      値と出所が記録される**
- [x] **`source: exif` のプロファイルで §9.3 の 11 段の crash 試験が通る**
- [x] **`recompute` の後、`profile_revision_id` は不変で `captured_at_revision_id` だけが
      進んでいる**
- [x] **`recompute` はリモートに触らず、影響を受けた送信済みレコードだけが
      `needs_recheck` になる**
- [x] **ユーザが書いた悪性の正規表現でアプリが固まらない**（`timeout` で降り、理由が出る）
- [x] 2 枚のカードを同時に挿して独立に扱える
- [x] mountd を再起動してもアプリが復帰する
- [x] `uv run pytest` / `-m needs_system` / ruff / npm の lint・typecheck・build・test:e2e が通る

---

## Phase 5 でやらないこと（意図的な除外）

| 項目 | 理由 |
| --- | --- |
| `subscribe`（netlink uevent） | 上記「検出の起動源を `subscribe` にしなかった理由」 |
| ffprobe の `creation_time` を第 4 の timestamp source にする | **Canon の MOV が壁時計を書くか UTC を書くかを実データ無しに確かめられない。** 推測で source を足すと、間違っていたときに `captured_at` を一斉に作り直すことになる。チェックリストで測ってから決める |
| `canon-eos` の結合 | 4GB 分割の判別根拠が実データ無しに得られない。誤結合は公開済みの `media_file` を取り残す |
| `UPLOAD_CONCURRENCY` の多重化 | Phase 6。独立した設計課題（上記） |
| **RAW と JPEG の Immich 上でのスタッキング** | **Phase 6。** 下記に実測結果を残す |
| PTP 接続 | スコープ外と決定済み（§2） |
| 自動アップロード | §12.1「アップロードは信頼登録の有無にかかわらず常に手動」 |
| プロファイルの import / export | YAGNI。テキストエリアからコピーできる |
| 継ぎ目サムネイル / 動画のプレビュー / `SECRET_KEY` のローテート | Phase 4 が送ったもの。Phase 5 の完了条件に関係しない |

---

### RAW / JPEG のスタッキングを Phase 6 へ送る（実測つき）

70D で RAW+JPEG を同時記録すると `IMG_1234.CR2` と `IMG_1234.JPG` の対ができ、Immich では
別々の写真として並ぶ。**スタッキングで束ねられる。実 Immich v3.1.0 の API 仕様で確認した
（2026-08-19、読み取りのみ）。**

| 経路 | 形 |
| --- | --- |
| `POST /stacks` | `{assetIds: [...]}` → 201、`{id, primaryAssetId, assets[]}` |
| `GET /stacks?primaryAssetId=` | 主資産から引ける |
| `PUT /stacks/{id}` | `{primaryAssetId}` で主を差し替え |
| `DELETE /stacks/{id}/assets/{assetId}` | 1 枚だけ外す |
| `AssetResponseDto.stack` | `{id, primaryAssetId, assetCount}` —— 資産を読めば既にスタック済みか分かる |

**`POST /stacks` の説明にある地雷を記録しておく。**

> If any of the provided asset IDs are **primary assets of an existing stack, the existing
> stack will be merged** into the newly created stack.

作成専用ではなく**既存のスタックを吸収する**。利用者が手で作った組に対してこちらが送ると
作り直される。Phase 3 で固めた「既存アセットを勝手に変更しない」に正面から当たるので、
**送る前に `AssetResponseDto.stack` を見る**必要がある。`assetIds` のどれが primary になるかは
仕様に書かれていないので、**`PUT` で明示的に決める**。

**Phase 5 に入れない理由:** スタックは「相手を変える操作」で、タグ付けや日時補正（§9.6）と
同じくアップロードの状態機械・`origin` の判定・承認の経路に触れる。Phase 5 は既に停止契約
（Task 2）と公開手順（Task 3）と provenance（Task 6）に触っており、そこへ 4 つ目を足すと
レビューの焦点が散る。**組を決めるのはローカル**（同じディレクトリで basename が同じ）で、
これは結合のグループ検出と同じ性質なのでプロファイルの規則として書ける —— 設計の形は
見えているので、急ぐ必要が無い。

**RAW の取り込み自体は Phase 5 に入っている**（`canon-eos` の `scan.extensions` に `CR2`）。
スタックしないので Immich には 2 枚として並ぶ。

---

## 実装の前に決めておくこと

**いずれも既定を置いてある。異論は計画レビューで出す。**

| 項目 | 決め | 理由 |
| --- | --- | --- |
| `VolumeWatcher` のポーリング間隔 | **5 秒** | mountd の idle timeout が 300 秒なので接続は保たれる。挿してから取り込みが始まるまでの体感がこの値。`_observe` は USB が無ければサブプロセスを起こさないので、待機中のコストはほぼ無い |
| `exifread` の版 | **`>=3.0`** | 3.0 で API が整理されている |
| `generic-dcim` の `scan.extensions` | **`JPG, JPEG, PNG, HEIC, DNG, MP4, MOV, AVI`** | Immich が扱える一般的な形式に絞る。**メーカー固有の RAW（CR2 / NEF / ARW 等）は入れない** —— 汎用プロファイルで拾うと、機種プロファイルを作る動機が消えて `library/generic-dcim/` に何でも溜まる。`MTS`（AVCHD）も入れない（`BDMV` 構造とセットで意味を持つので、単体で拾うと分割動画が個別に取り込まれる） |

---

## レビュー記録

### 計画レビュー 1 巡目（2026-08-19、codex `--fresh`。blocker 4 / major 5 / minor 2）

**反論できたものは無い。4 件は実装を読んで裏を取った。**

| # | 指摘 | 反映 |
| --- | --- | --- |
| blocker 1 | **信頼登録は観測トークンを動かさない。** `trust()` は `trusted_at` を `UPDATE` するだけなので、カードを挿したまま承認しても watcher が再評価せず、「承認すると自動取り込みが始まる」が成立しない | Task 2 を作り直した。**probe だけを観測トークンの門に入れ、enqueue の判定は毎 tick DB の現在値から組み直す**。そのために `0010` で `volume_instance.provisional` を足した（判定材料が 1 つでも view にしか無いと組み直せない） |
| blocker 2 | **停止契約が実現不能。** `recv_message` に timeout が無く `to_thread` は cancel で止まらない。共有 `BrokerClient` を閉じると走っている import の handle 接続まで切る | watcher に**専用のブローカー接続**を持たせ、socket に deadline を掛けた。`lifespan` の停止順（watcher → runner）も明記した。DB 接続は `VolumeService` と共有のまま |
| blocker 3 | **EXIF の差し込み口が無い。** `Importer` は `captured` を `publish` の前に作り、その時点で staging は存在しない | `ArtifactRequest` に `resolve_captured` を足し、**手順 4 の後・手順 5 の中**で呼ぶ契約にした。Files に `publisher.py` と crash consistency の試験を追加 |
| blocker 4 | **`recompute` が provenance を壊す。** `profile_revision_id` は「そのレコードが使った不変の版」。値だけ新しくすると嘘になり、版を進めると timestamp 以外も適用したと偽る | `0011` で **`media_file.captured_at_revision_id` を分離**した。`recompute` はこの列だけを進める |
| major 1 | 自動と手動の二重 enqueue は塞がっていない。`UPDATE` に `detached_at IS NULL` が無い | **範囲を「自動経路の at-most-once」と明記**。`detached_at IS NULL` を `SELECT` と `UPDATE` の両方に置いた |
| major 2 | **空集合には `generation` が無い。** `_do_list` は `volumes` を返すだけで、トークンは `VolumeInfo` の中にしかない | **空集合を番兵として扱う仕様**を明記（プロトコルは変えない）。`GET /devices` が先に観測した場合に全 probe が 2 回走ることも許容コストとして記録 |
| major 3 | ビルトイン保護が `PUT` だけ。`sync_builtins` は `archived_at` を戻さないので、archive されると復活しない | **`duplicate` 以外の全 mutation を 1 つのガードに集約**し、archive の受け入れ試験を追加 |
| major 4 | 正規表現の長さ上限では catastrophic backtracking を防げない。しかも `RLock` の中で 2000 件に当たる | **保存時に子プロセスで deadline 付きの試験マッチ**を行う。緩和であって証明ではないと明記 |
| major 5 | `recompute` の「送信済みを知らせる」に配送先・完了条件が無い（Phase 4 と同型） | **`needs_recheck` へ戻し、既存のアップロードのパイプラインに再実行させる**。当初は `awaiting_datetime_approval` へ直接戻す案を書いたが、実装を読んで取り下げた（下記） |

**major 5 の反映は、書いた後に自分で作り直した。** 最初は `complete` を
`awaiting_datetime_approval` へ直接戻す案にしたが、実装を読むと成立しない。
(1) その状態は `Uploader` が `expect_state="tagging"` で CAS しており、外から入る遷移が無い。
(2) 承認画面が出す「現在値」（`remote_datetime_original`）は、承認待ちにするその瞬間に
Immich へ問い合わせて控えた値で、ローカルのジョブでは埋められない —— 空のまま承認待ちに
すると Phase 4 の「読めなかった値を『変更なし』にしない」に反する。
(3) クライアントを持たせると preflight・リース・キャンセルという Phase 3 の契約を丸ごと
持ち込むことになる。**`needs_recheck` は `CLAIMABLE_STATES` に入っている**ので、
そこへ戻せば既存のパイプラインがリースと guard の下で再実行し、現在値を観測したうえで
承認待ちか `complete` に落ち着く。`recompute` が Immich に触らない性質も保たれる。
| minor 1 | `_reconnect` の契約と fd が増えない試験が未記載 | Task 0 に契約と受け入れを追加 |
| minor 2 | lockfile が Files に無い。Task 7 の vitest が操作を覆っていない | 両方追加 |
| 補足 | 「印を先に取れば crash 時に 1 回ぶんで済む」は**同一トランザクションでは誤り**（両方 rollback される） | **要点は順序ではなく原子性**と書き直した |

**この巡で自分でも 1 件見つけた。** `exifread` は認識できない入力に対して例外ではなく
WARNING を出す（実測）。Canon は MOV も `source: exif` を通るので、**画像以外では呼ばない**
振り分けを Task 3 に足した。

### 計画レビュー 2 巡目（2026-08-19、codex `--fresh`。blocker 4 / major 4 / minor 2）

対象は `83d428b`。**1 巡目で作り直した箇所の周りから、また blocker が出た**
（Phase 3 で 3 巡続いたのと同じ形）。**反論できたものは無い。**

| # | 指摘 | 反映 |
| --- | --- | --- |
| blocker 1 | **watcher の所有境界が三すくみ。** `refresh()` は `self._client` を使うので、専用 socket を持たせても API 側の `VolumeService` を呼べば共有 client を通る。別 `VolumeService` を作って `volumes_conn` を使い回せば「接続を同時共有しない」に反する | **watcher に 1 つのスコープを丸ごと持たせた**（専用 DB 接続 + `ProfileRegistry` + `VolumeService` + `BrokerClient`）。あわせて **`refresh()` の書き込みを `BEGIN IMMEDIATE` で囲む**（現状トランザクションが無く、インスタンスが 1 つだから成立していただけ） |
| blocker 2 | **「毎 tick DB の現在値」がプロファイルの現在性を満たしていない。** `volume_instance` にキャッシュされた `profile_id` / `profile_revision_id` だけを読み、`archived_at` と `current_revision_id` を見ていない | `device_profile` を JOIN し、**archive されていないこと**と**その版が今も現行であること**を条件に足した。さらに**門の入力に「現行リビジョンの指紋」を加えた**ので、プロファイルの編集で再 probe が走る |
| blocker 3 | **recompute の再計算入力が永続化されていない。** `filename` はカード上の原名に当てるのに `media_file.rel_path` は公開先（衝突接尾辞つき）で、`source_rel_path` はどこにも保存されていない。derived は継承なのに一律で公開済みファイルを読む形になっていた | **role ごとに入力を決め、順序を固定した**。`original` は `source_entry.rel_path` を JOIN、`derived` は先頭 active member から derive、`original` → `derived` の順。`source_entry` の無い行は飛ばして件数を出す |
| blocker 4 | `complete` → `awaiting_datetime_approval` の直遷移が状態機械を迂回する | **`62b9662` で先に自分でも見つけて直していた**（`needs_recheck` へ）。先方も同じ結論 |
| major 1 | `0011` の制約が弱い。nullable のままで、単一 FK なので別プロファイルの版も指せる | **trigger で「必ず値を持つ」と「同じプロファイルの版である」を強制**した。`media_file` は参照が多いので table rebuild は採らない |
| major 2 | **初回観測が空集合のケースが無い。** 記憶の初期値を「空」にすると、前回の live presence が残ったまま最初の tick が「変化なし」になり、stale な presence に自動取り込みを積む | `UNOBSERVED` と `EMPTY` を**別の値**にし、**最初に成功した `list_volumes` は空でも必ず `refresh()` を回す**ことにした |
| major 3 | **固定標本による ReDoS 緩和は弱すぎる。** `(z+)+$` は `a` の標本を素通りする | **測って作り直した。** `re` は `(a+)+$` で破綻するが `regex` は 0.000 秒で処理し、`regex` でも破綻する `(a|a)+$` では `timeout=` が実測どおり発火する。**マッチを `regex` + `timeout` に替えた**（実行時の保証になる） |
| major 4 | **`stop()` の close と Task 0 の再接続が衝突する。** close で解けた `recv` の `OSError` を「`list_volumes` だから再送可」と拾い、また待ちに入る | `_closing` を**先に**立て、`shutdown(SHUT_RDWR)` → `close()` の順にした。deadline は保険として残す |
| minor 1 | `captured` と `resolve_captured` の排他がコメントだけ | `__post_init__` で両方／どちらも無しを弾き、試験を足した |
| minor 2 | 完了条件に recompute の provenance / remote 状態と EXIF の fallback / crash が無い | 6 件足した |

**依頼した確認点への回答で、通ったもの:** `0010` / `0011` は追加のみで順序も backfill も妥当。
`resolve_captured` を手順 4 後・手順 5 内で呼ぶ位置は §9.3 の回収境界と両立する
（手順 7 より前なので、失敗すれば `writing` と staging を破棄できる）。範囲表に対して
Task 自体が欠けている項目は無い。

### 実装差分のレビュー 6 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 1 / minor 1）

**5 巡目で足した索引が、別の問い合わせを退行させていた。**

| 指摘 | 判定 | 対処 |
| --- | --- | --- |
| **major 1**: `0013`（`profile_id, role, rel_path`）が、プロファイルで絞った一覧（`ORDER BY captured_at DESC, id DESC` + `LIMIT 50`）にも選ばれる。**`media_file_captured_at` を辿れば先頭ページで止まれた**のに、そのプロファイルの全行を拾ってから並べ替えるようになった | **妥当。** EXPLAIN の実測つき。**索引を足したら、他の問い合わせの実行計画も見る**（この案件で初めて出た型） | `0014` で `media_file (profile_id, captured_at DESC, id DESC)` を足し、**`_filters` の `IN (SELECT ...)` を `= (SELECT ...)` へ変えた**。`IN` だと SQLite は複数の値を取りうると見なして、索引があっても並べ替えを外せない（slug は UNIQUE なので意味は変わらない） |
| **minor 1**: `HANDOFF.md` の現在地（コミット数・先頭・検証状態の数値）が反映 5 コミットぶん古い | **妥当** | 直した |

**この巡で足した回帰試験:**

- プロファイルで絞った一覧の EXPLAIN に一時 B-tree が出ない
- プロファイルで絞れる／知らない slug は 0 件（`IN` → `=` で意味が変わらないこと）

**変異試験で 1 件素通り → テストの当て方を直した。** 一覧の EXPLAIN を**手で書き写した
問い合わせ**に当てていたので、`_filters` を `IN` に戻す変異が素通りした。
**`_filters` が組み立てた WHERE をそのまま使う**形にして検出できた
（§8 の「素通りはまずテストの当て方を疑う」。3 巡連続で同じ型）。最終的に 3 件中 3 件を検出。

### 実装差分のレビュー 5 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 2 / minor 1）

**4 巡目の対処が作った境界から 2 件。** どちらも「直したつもりの層に、まだ縛れて
いない次元が残っていた」形。

| 指摘 | 判定 | 対処 |
| --- | --- | --- |
| **major 1**: ページの**返却件数**は縛れたが、**探索する行数**は縛れていない。`media_file` にあるのは `rel_path` 単独の UNIQUE 索引だけで、`profile_id` / `role` は後段 filter。別プロファイルの大きなライブラリがあると 1 ページ読むだけでその全行を走査しうる（`original` は `rel_path` の並び上 `derived/` も先に通る）。**`fetch` の最中は heartbeat もキャンセル観測も無い** | **妥当。** EXPLAIN の実測つき。4 巡目の対処は `LIMIT` を足しただけで、駆動索引を変えていなかった | `0013` で `media_file (profile_id, role, rel_path)` を足す。**`0012` は書き換えない**（適用済みの版を書き換えると前の版で作った DB が開けなくなる。§7 に「以後この手は使わない」と記録がある） |
| **major 2**: `derived` の継承元をページに materialize してから書くので、その間に API 側の別接続で結合をやり直せる。旧 member を持ち回るため、**退役した出力の `captured_at_revision_id` が新版へ進む** | **妥当。** §3 の「supersede した結合の出力は再導出しない」に反する | **継承元を排他区間の中で解決する**（`resolve_inside`）。抽出の側からは相関副問い合わせを外した。DB を読むだけなので排他の中でも長くならない（EXIF を伴う `original` は外のまま） |
| **minor 1**: `design.md` §21 の「Phase 5 の実装で確定した事項」が、レビュー反映後の正本と同期していない | **妥当。** §21 だけを読む次の担当が古い契約を持つ | 差し戻しのトランザクション、supersede 出力、`autoImportOutlook`、設定未解決の `null`、ページ送りを §21 へ足した |

**確認された「指摘なし」:** keyset に重複・無限ループは無い（`rel_path` は immutable かつ
UNIQUE）、`original` 完了後に `derived` の順序は保たれている、`0012` の列順と既存 DB への
追加、`autoImportOutlook` の `null` 分岐と 3 か所での利用、リース／キャンセルの 4 層が
互いを不正にマスクしていないこと、`_Cards` の錠。

**複数 worker を許すなら別途必要になること**（現状は 1 プロセス 1 `JobRunner` なので
直列化される。Phase 6 の `UPLOAD_CONCURRENCY` に触るときに読むこと）: cursor より下への
insert は取りこぼし、cursor より上への継続 insert は終端を遅らせる。**上限の snapshot が
要る。**

**変異試験で 1 件素通り → テストの当て方を直した。** 「継承元を排他の外で解決する」
変異は、`_recomputed_derived` が自分で引き直すので**値だけを見ても区別が付かない**
（割り込めた世界と割り込めなかった世界で「正しい値」が違う）。**割り込もうとした側が
書き込みロックで弾かれること**を見る形にして検出できた（`busy_timeout = 0` で待たせない）。
最終的に 4 件中 4 件を検出。

### 実装差分のレビュー 4 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 2 / minor 1）

**今回は「直した箇所の周り」ではなく、3 巡かけて誰も見ていなかった層から出た**
（対象の抽出そのものと、設定が未解決のときの画面）。

| 指摘 | 判定 | 対処 |
| --- | --- | --- |
| **major 1**: 対象の抽出（`_originals` / `_derived`）が**リースとキャンセルの外**にあり、全件を先に materialize する。`source_entry.media_file_id` に索引が無く、`EXPLAIN QUERY PLAN` は `media_file` 1 行ごとに `SCAN s` + 一時 B-tree。大きなライブラリでは**最初の `assert_lease` に届く前に 60 秒を超えて**正常なジョブが落ち、その間キャンセルも観測されない | **妥当。** `BATCH_SIZE` が書き込みにしか効いていなかった。EXPLAIN の実測も添えられていた | **読み出しも `batch_size` で区切る**（keyset。`rel_path` は `media_file` で UNIQUE、再計算はその列を動かさないので巡回中に行が前後しない）。`0012` で `source_entry (media_file_id, observed_at, id)` と `merge_group (output_media_file_id)` の索引を足した |
| **major 2**: `/settings` が未解決・失敗の間、`?? "trusted"` で既定へ倒すので、**実設定が off でも「いまの中身を数秒後にコピー」と同意を取れる**。`settings.error` もバナーに出ていない | **妥当。3 巡目で 1 か所に集めた条件に、`null` の場合が無かった** | 未解決は `null` のまま持ち、`autoImportOutlook` が「設定をまだ読めていない」を返す。**信頼ボタンも押させない**（同意の内容が作れない）。`settings.error` もバナーへ |
| **minor 1**: `_Cards` の `os.open` が錠の外なので、open 後・登録前に `release_all` が走ると fd を取り逃がす。`release_all` も走査中の `mount` を残せる | **妥当** | open と登録を同じ排他に入れ、`release_all` は台帳を 1 度に空にする（`verify` は相手を待ちうるので錠の外のまま） |

**`CANDIDATES` の残り 3 条件について**（4 巡目で明示的に確認された）: `detached_at` は
`GET /devices` の `refresh` が現存 snapshot だけを返すため、`archived_at` と
`current_revision_id` は `VolumeService._probe` が `registry.active` / `current` から
解決して DB へ書くため、**応答時点では画面の 3 条件から落としてよい**。
応答後の archive / 編集 / detach はどのクライアント値も stale にしうるが、
実行を守るのは watcher 側の全条件。

**この巡で足した回帰試験:**

- 対象が `batch_size` ごとにページングされて読まれる
- `EXPLAIN QUERY PLAN` に `SCAN s` と一時 B-tree が出ない（`0012`）
- 設定が未解決・失敗の間は「始まる」と断言せず、信頼ボタンも押せない
- 設定の失敗もバナーに出る

**変異試験で 1 件素通り → テストの当て方を直した。** 「派生物の抽出で `SCAN g` が
出ない」は、索引を落とすと SQLite が `merge_member` 側から回すので `SCAN mm` に
変わるだけで、`SCAN g` はどちらでも出ない。**`SCAN mm` も出ないことを足して**
初めて検出できた（§8 の「素通りはまずテストの当て方を疑う」）。最終的に
6 件中 6 件を検出。

### 実装差分のレビュー 3 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 2 / minor 2）

**また 2 巡目の対処が作った境界から出た**（この案件で 5 度目）。

| 指摘 | 判定 | 対処 |
| --- | --- | --- |
| **major 1**: 行をまたぐ heartbeat が `assert_lease` を欠いている。`extend_lease` は `cancelling` でも延ばすので、**キャンセル済みのリースを延ばし続け、残りの EXIF を最後まで読んでから**書き込みの確認でようやく止まる（100 枚なら数十分） | **妥当。** `core/lease_pulse.py` が `assert_lease` を先に呼ぶのと同じ理由。2 巡目で足した仕掛けは「リースを失わない」ことだけを見ており、**キャンセルに気づく側が抜けていた** | `ctx.assert_lease()` → `ctx.heartbeat()` の順にする。回帰試験は「1 枚は間隔より短い」筋書きで、`with_lease_pulse` が 1 度も打たない経路を通す |
| **major 2**: 「未承認」を先に判定するので、**watcher が積まない 3 つの状態**（`AUTO_IMPORT=off` / `provisional` / 確度が `high` でない）でも「いまの中身を数秒後に自動取り込み」と断言する | **妥当。同じ穴を 2 度開けた**（2 巡目は逆向きに断言していた） | **画面と確認ダイアログが同じ関数を見る**形にする（`autoImportOutlook`）。判定は `watcher.py` の `CANDIDATES` と同じ 3 条件。始まらない場合は「信頼は記録するが、いまは始まらない」を理由付きで出す |
| **minor 1**: `_ledger` は `mount` の採番と挿入だけを守り、継承した `release` / `release_all` は無錠で `_open` を触る | **妥当。** cross-wire は塞がっているが、fd の取り逃がしが残る | 両方を同じ錠で override する |
| **minor 2**: 文書の確定契約がまだ内部矛盾している（`phase5-plan.md` の Task 8 と `HANDOFF.md` §3 に「次に挿したときから」が残る、`HANDOFF.md` の対象コミット数が古い） | **妥当。** 2 巡目の直し漏れ | 直した |

**この巡で足した回帰試験:**

- 各読み取りが間隔より短い連続バッチで、**キャンセル後に残りを読まずに降りる**
- `AUTO_IMPORT=off` / `provisional` / 確度が低い、それぞれで「始まらない」と書く
- 始まらない状態の確認ダイアログが「いまは始まらない」と書き、断言しない

**変異試験（3 巡目の反映後）:** 新しい guard を壊す変異 5 件をすべて検出
（`assert_lease` を落とす 1 件、`autoImportOutlook` の 3 条件を 1 つずつ潰す 3 件、
ダイアログの分岐を潰す 1 件）。

**ここまでの傾向**（5 巡ぶん）: **blocker は 1 件も出ていない。major は毎巡 2〜3 件で、
そのすべてが「直前の巡で直した箇所が作った境界」から出ている。** 特に
「文言が実挙動を断言する」形は 2 巡連続で、向きを変えただけの直しが次の巡で
逆側の嘘になった。**条件を 1 か所に集めて両方の出口が同じ関数を見る**形にして、
ようやく止まった。

### 実装差分のレビュー 2 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 3 / minor 2）

**1 巡目で直した箇所の周りから、また出た**（この案件で 4 度目の同じ形）。

| 指摘 | 判定 | 対処 |
| --- | --- | --- |
| **major 1**: `with_lease_pulse` は処理が間隔より短く終わると**1 度も打たない**（`thread.join(timeout=間隔)` が先に返る）。1 枚 1 秒の EXIF が 100 枚で 100 秒になり、書き込みの `assert_lease` で**正常なジョブが落ちる** | **妥当。** 1 巡目の対処（1 枚ごとの pulse）は「1 枚が長い」場合しか守っていなかった。回帰試験も各 1.5 秒・間隔 0.2 秒で、間隔を跨ぐ筋書きしか通していなかった | **仕掛けを 2 つにする。** 1 枚ごとの pulse は残し、**行をまたいだ経過時間でも打つ**。変異試験で、どちらを外しても別々の試験が落ちることを確かめた（冗長ではなく、守る場合が違う） |
| **major 2**: 信頼の確認 UI が「次に挿したときから」と書いているが、実装は trust の次 tick で**いま live な presence を積む**。利用者は「現在の中身はまだコピーされない」と読めるのに、実際は数秒後にコピーされる | **妥当。プライバシーの同意の対象がずれている**（§12.1）。しかも E2E と計画の受け入れは「挿したまま承認すると始まる」を*正*として通していた —— **文言だけが実装と逆を向いていた** | 文言を実挙動へ合わせる（「いま入っている中身も含めて、数秒後から」）。`docs/HANDOFF.md` と `phase5-plan.md` の「2 度目以降の**挿入**から」も「2 度目以降の**観測**から（挿し直しは要らない）」へ直した |
| **major 3**: `_Cards.mount` が共有の `self.target` を書き換えてから開くので、2 接続が同時に開くと**別のカードの dirfd を返す**。「2 枚を独立に判定する」の土台そのものが競合する | **妥当。** barrier を `verify` に挟んだ試験で、両方が同じ inode を返すことを再現した | 開く先を `mount` のローカルに閉じ、共有する台帳（採番と handle 表）だけを錠で守る |
| **minor 1**: `LeaseLost` の受け口が `ctx.cancelled()`（= `status != running`）を見るので、`interrupted` も「利用者が押したキャンセル」として扱う | **妥当だが、直さない。** `sweep_interrupted` は起動時の reconcile でしか走らず、`reap_expired_leases` はどこからも呼ばれていない。**アプリは 1 プロセスで DB を持つ**ので、走っている自分のジョブが `interrupted` になる経路が無い。`Rechecker._cancelled_or_raise` も同じ形なので、ここだけ変えると受け口が 2 種類になる | 直さず、この判断を記録する（**将来 `reap_expired_leases` を配線するなら、両方の受け口を同時に厳密化する**） |
| **minor 2**: `HANDOFF.md` の現在地が c7b7041 の後と食い違う（レビュー未実施・Task 6〜9 が残る・10 タスク中 6 完了） | **妥当。** 更新漏れ | 直した |

**この巡で足した回帰試験:**

- **各読み取りは間隔より短いが、合計がリースを越える**（major 1 の本体）
- 2 枚を同時に開いても、それぞれ別のディレクトリの dirfd を得る（major 3）
- 承認の確認に「いま入っている中身」が出る（画面とダイアログの両方）

**変異試験（2 巡目の反映後）:** 新しい guard を壊す変異 6 件をすべて検出
（リースの 2 つの仕掛けを片方ずつ外す 2 件と間隔を伸ばす 1 件、文言 2 件、harness 1 件）。

### 実装差分のレビュー 1 巡目（2026-08-19、codex `--fresh`。blocker 0 / major 2 / minor 0）

対象は Task 6〜9 の 4 コミット（`4bcda2e..eab6dc2`）。**2 件とも `recompute` の同じ層
（リースの境界と provenance）に出た。**

| 指摘 | 判定 | 対処 |
| --- | --- | --- |
| **major 1**: バッチの頭で `assert_lease` を済ませてから EXIF 読み取りを含む再計算に入り、その後で初めて `BEGIN IMMEDIATE` に入るので、**失効・キャンセル後のバッチを書ける** | **妥当。** §9.3 手順 7 が「確認と遷移を 1 つの `BEGIN IMMEDIATE` に入れる」と決めているのと同型 | 書き込みの側でも `ctx.assert_lease()` を取り直す（`immediate` の冒頭）。EXIF の読み取りは `with_lease_pulse` で囲む。**この 2 つはレビューが来る前に片方を自分で見つけていた**（`_exif_wall` の pulse）が、書き込み側の取り直しは抜けていた |
| **major 2**: `source_entry` 欠落で `original` を飛ばすと、その member を継ぐ `derived` だけが新しい `captured_at_revision_id` へ進み、**値は旧版由来なのに新版で算出したと記録する** | **妥当。** `0011` で列を分けた意味がそこで消える | `_recomputed_derived` で member の `captured_at_revision_id` も読み、対象の版まで進んでいなければ `derived` も理由付きで飛ばす |

**指摘が無かったもの:** `0011` の埋め戻し・FK・trigger 2 本、YAML → JSON の受け取りと
`validation_failed` の `detail` を出す変更、2 枚差しの harness。

**環境の差:** レビュー側の sandbox は loopback socket を禁じているので、Playwright の
E2E は先方では完走できない（こちら側では 2 spec とも通る）。

**この巡で足した回帰試験:**

- 長い EXIF 解決がリースを跨いでも失わない（`with_lease_pulse` の回帰）
- 確認と書き込みの間にキャンセルが commit されても、そのバッチは書かれない
- 継承元が旧版のままなら `derived` も飛ばす

**変異試験（レビュー反映後）:** 23 件中 22 件を検出（素通りは既知のマスク 1 件）。
新しい 2 つの guard を壊す変異 3 件はいずれも検出。

### 実装差分のレビュー

（実装後にここへ足す。**毎巡 `--fresh`。**）
