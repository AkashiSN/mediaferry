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
pytest / ruff ・ React 19 + TypeScript + Vite / Playwright（受け入れのみ）

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
- **正規表現はユーザが書く。** `filename_pattern` / `sequence_pattern` / `timestamp.pattern` は
  `re.compile` に通して検証し、**長さの上限**を掛ける（壊れた式で判定が毎回例外を投げると、
  そのボリュームが恒久的に「対象外」になる）
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
| `app/src/mediaferry/db/profiles.py` | **修正**。ユーザ定義の作成・編集・複製・archive。**ビルトインの直接編集を拒む** |
| `app/src/mediaferry/db/migrations/0010_auto_import.sql` | **新規**。`volume_presence.auto_import_at` |
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
3 exif ─→ 4 ビルトイン 2 種 ─┬─→ 6 recompute ──┤
                              └─→ 5 編集 API ─→ 7 設定画面 ─┘
```

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

**開いている handle は再接続で失われる。** 接続が切れた時点で mountd 側の
`handle_connection` の `finally` が全ハンドルを release しているので、これは新しい損失では
ない。`VolumeService._open` に残った `VolumeHandle` は無効になるが、**dirfd は受け取り済み
なので読み出し自体は安全**（§9.2 の「一度受け取った dirfd はデバイスが抜かれても安全」）。
再接続時に `_open` を掃除しない —— 掃除すると、走っているジョブの足元で handle が消える。

**受け入れ:**
- 実 `BrokerServer` を落として上げ、次の `list_volumes` が値を返す
- `open_volume` は接続が切れていても**再送されない**（例外が上がる）
- 再接続に失敗した `list_volumes` は例外を上げる（握り潰さない）

**変異試験:**
- `!=` を `==` にする（`list_volumes` 以外を再送する）→ `open_volume` の試験が落ちること
- 再接続を 2 回以上ループさせる → 停止の試験が落ちること
- 再接続時に `_open` を空にする → 走っているジョブの試験が落ちること

---

### Task 1: `volume_presence.auto_import_at`（`0010`）

**Files:** Create `app/src/mediaferry/db/migrations/0010_auto_import.sql` / Test `app/tests/test_db_migrate.py`, `app/tests/test_schema_sources.py`

```sql
-- 自動取り込みを積んだ接続に印を付ける。列は volume_instance ではなく
-- volume_presence に置く。「このカードを取り込んだか」ではなく
-- 「この接続について積んだか」を憶えるためで、抜き挿しすればまた積まれる。
ALTER TABLE volume_presence ADD COLUMN auto_import_at TEXT;
```

**なぜ presence か:** `volume_presence` は「接続 1 つ = 1 行」で、列挙のたびに増えない
（`0002` のコメント）。ここに印を置けば、**再起動しても同じ接続なら積み直さない**。
`volume_instance` に置くと 2 度と自動取り込みされないカードができ、
watcher のタイマに置くとプロセスが落ちるたびに二重に積む。

**適用済みの版は書き換えない。** `test_a_database_from_the_previous_release_still_opens` の
checksum 一覧に `0010` を足す（`HANDOFF.md` §3、Phase 3 の 7 巡目 blocker）。

**受け入れ:** 既存 DB に適用でき、列が NULL 許容で追加される。checksum の凍結試験が
新しい版を含んだうえで通る。

---

### Task 2: `VolumeWatcher`

**Files:** Create `app/src/mediaferry/jobs/watcher.py` / Modify `app/src/mediaferry/api/app.py`,
`app/src/mediaferry/jobs/volumes.py` / Test `app/tests/test_volume_watcher.py`

**責務は 3 つだけ。**

1. `list_volumes` を一定間隔で呼び、`(broker_epoch, generation)` が変わったときだけ
   `VolumeService.refresh()` を回す
2. `AUTO_IMPORT=trusted` のとき、条件を満たす presence に `import` を積む
3. 集合から消えた presence に紐づく**未実行**のジョブを無効化する（§9.2 の `remove` 規則）

**間隔は設定にしない。コンストラクタ引数にする**（`JobRunner(database, poll_interval=0.5)` と
同じ形）。運用で変えたくなる値ではなく、試験で速く回したいだけの値なので、
設定項目を増やさない。

**接続:** **DB 接続を新たに開かない。** `VolumeService` が持っている接続と `RLock` の下で
動く。`GET /devices` も同じ接続で `refresh()` を呼ぶので、ここに 2 本目を持ち込むと
「接続はスコープごとに 1 本」が崩れる。ブローカーも同じ `BrokerClient` を使う
（`_call` は `_lock` で直列化されている）。

**積むことと印を付けることを 1 つの `BEGIN IMMEDIATE` に入れる。**

```python
def _maybe_enqueue(self, view) -> str | None:
    # 条件は「積む直前」に確かめる。AUTO_IMPORT は Tier.RUNTIME なので
    # 起動時のスナップショットを見てはいけない。
    if SettingsService(self._conn, self._env).snapshot().auto_import != "trusted":
        return None
    if not view.trusted or view.identity_confidence != "high" or view.provisional:
        return None
    # **いま判定に使った view の selection をそのまま使う。** selection_for() で
    # 引き直すと、判定した接続と積む接続が食い違いうる（引き直しの間に
    # refresh が走れば別の presence が返る）。
    selection = view.selection
    if selection is None:
        return None
    with immediate(self._conn):
        # 印が付いていないことを、印を付けるのと同じトランザクションで確かめる。
        # 分けると、隙間に届いた次の tick が同じ接続にもう 1 本積む。
        marked = self._conn.execute(
            "UPDATE volume_presence SET auto_import_at = ?"
            " WHERE id = ? AND auto_import_at IS NULL",
            (now_iso(), selection.presence_id),
        ).rowcount
        if not marked:
            return None
        return JobStore(self._conn).enqueue("import", selection.to_params())
```

**判断の理由:**

- **条件付き UPDATE（CAS）で印を取る。** SQLite に行ロックは無い（`HANDOFF.md` §3）。
  `SELECT` してから `UPDATE` すると、`refresh()` が遅れている間に届いた次の tick と
  競合する
- **印を先に取ってから積む。** 逆にすると、積んだ直後に落ちたときに印の無いジョブが残り、
  再起動後にもう 1 本積まれる。印が先なら、落ちても「積まれなかった 1 回」で済む
  （利用者は画面から手動で積める）
- **`view.selection` をそのまま渡す。** ジョブは選択した瞬間の presence を params に持つ
  （Phase 1 の契約）。`volume_instance_id` だけを渡してはならないし、判定に使った view と
  別に引き直してもならない
- **`provisional` を除く。** 暫定マッチは「対象だが中身が無い」ボリューム。
  §12.1 が自動取り込みの対象から外している

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

**停止:** `JobRunner` と同じ形。`stop()` で停止フラグを立て、`run_forever()` の完了を待つ。
**ブローカー待ちで固まらないこと** —— `list_volumes` はソケット越しなので、
`asyncio.to_thread` へ逃がしたうえで、停止フラグを見る待ちに `wait_for` で timeout を掛ける
（`JobRunner.run_forever` の `poll_interval` の待ちと同じ形）。

**`AUTO_IMPORT=off` でも watcher は回す。** 一覧を新鮮に保つ役目があるので止めない。
**積むかどうかだけを設定で決める。**

**受け入れ:**
- `volumes` fixture を書き換えて `generation` を進めると `refresh()` が 1 回走る
- 集合が変わらない tick では `refresh()` が**走らない**（＝マウントが起きない）
- 信頼済み・`high`・非 provisional のボリュームに `import` が 1 本だけ積まれる
- 同じ接続のまま tick を何度回しても 2 本目は積まれない
- 抜いて挿し直す（`volume_presence` の行が変わる）と、また積まれる
- `AUTO_IMPORT=off` では積まれない。**起動後に `off` → `trusted` へ変えたら積まれる**
- `trusted` でない／`low`／`provisional` では積まれない
- 消えた presence の未実行ジョブが無効化され、**実行中のジョブは触られない**
- **2 枚挿した状態で片方のジョブが dirfd を掴んでいる間に、もう片方を集合から外して
  `refresh()` を走らせても、掴んでいる handle が閉じられない**（`VolumeService.opened()` に
  残る）**し、読み出しが続けられる**
- 変わらない tick では `refresh()` が走らないので、**取り込み中に裏でマウントが繰り返されない**
- `stop()` を呼ぶと、ブローカーが応答しなくても有限時間で `run_forever()` が返る
- mountd を落として上げると、watcher は例外で死なず次の tick で復帰する

**変異試験:**
- 変化の比較から `broker_epoch` を落とす → mountd 再起動で generation が 0 に戻る筋書きが落ちること
- `generation` の比較を消して毎 tick `refresh()` する → 「変わらない tick では refresh しない」が落ちること
- `UPDATE` の `AND auto_import_at IS NULL` を落とす → 二重に積まない試験が落ちること
- 印付けと enqueue を別トランザクションにする → 同上
- 設定の読み直しを起動時スナップショットに変える → `off` → `trusted` の試験が落ちること
- `provisional` / `identity_confidence` / `trusted` の各条件を 1 つずつ落とす → 対応する試験が落ちること
- 掃除の対象に実行中のジョブを含める → 「実行中は触らない」が落ちること
- **`refresh()` の pass 2 で、消えたボリュームの handle も閉じる** → 「片方を抜いても
  もう片方のコピーが止まらない」が落ちること

**検出できない見込みの変異は、確認してから記録に残す**（`HANDOFF.md` §5 の 3 番。
「検出できない」と書いた変異の多くはテストを 1 つ足せば落とせる）。

---

### Task 3: `timestamp.source: exif`

**Files:** Create `app/src/mediaferry/adapters/exif.py` / Modify `app/src/mediaferry/core/timestamps.py`,
`app/src/mediaferry/jobs/importer.py`, `app/pyproject.toml` / Test `app/tests/test_exif.py`,
`app/tests/test_timestamps.py`

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

**受け入れ:**
- `DateTimeOriginal` を持つ JPEG から壁時計が取れる
- タグが無い JPEG、EXIF が壊れた JPEG、JPEG ですらないファイルで `None` が返り、例外が出ない
- `source: exif` のプロファイルで、EXIF がある画像は `captured_at_source = "exif"`、
  無いものは `fallback` の値になる
- `timezone_policy: none` では壁時計がそのまま（UTC として）記録される

**変異試験:**
- `details=False` を `True` にする → （検出できない見込み。**記録に残す**）
- 例外の握りを外す → 壊れた EXIF の試験が落ちること
- `exif_wall` を無視して常に fallback にする → `captured_at_source` の試験が落ちること
- 読む位置をソース側に変える → ステージ済みを読む前提の試験が落ちること

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

**受け入れ:**
- Canon 風の合成カード（`DCIM/100CANON/IMG_0001.JPG` 等）が `canon-eos` に確定する
- `DCIM` に無関係なファイルだけを置いたカードが `generic-dcim` に落ちる
- `DCIM` を持たないボリュームが「対象外」になる
- **DJI のカードが `generic-dcim` に落ちない**（順位付けの回帰）
- 壊れた正規表現、長すぎる正規表現が `ProfileInvalid` になる

**変異試験:**
- `resolve_profile` の tie-break から `generic-dcim` の項を落とす → DJI が generic に落ちる試験が落ちること
- `merge.enabled` の必須外しを逆にする → Canon の定義が読めなくなること
- 正規表現の長さ上限を外す → 対応する試験が落ちること

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

**中心の判断: 編集 API は `builtin = 1` を拒む。** 現行の `_upsert_revision` は `builtin` を
見ないので、放置すると**次のアプリ更新で `sync_builtins` がユーザの編集を黙って上書きする**。
「編集しようとすると複製が作られる」（§6）は画面の作法で、API は明確に断る。

**`slug` は作成後不変。** ライブラリのパス（`library/<slug>/`）に使われるので、変えると
過去の取り込みが宙に浮く。`PUT` の本文に `slug` が含まれていて現在と違えば 400。

**archive は削除ではない。** 使用済みのリビジョンを指す行が `volume_instance`・
`media_file`・`merge_group` にあり、外部キーが `ON DELETE RESTRICT`。archive すると
`active()` から外れて新しい判定に使われなくなるだけで、過去の解釈は変わらない。

**受け入れ:**
- ビルトインへの `PUT` が 409 で断られる
- `duplicate` が `builtin = 0` の新プロファイルを作り、**元のビルトインは変わらない**
- `PUT` が新リビジョンを作り、**旧リビジョンは残る**（版の不変 trigger に触れない）
- 不正な定義（未知のキー、壊れた正規表現、`..` を含む root）が `ProfileInvalid` →
  400 の封筒になり、**リビジョンは作られない**
- archive 済みのプロファイルが `resolve_profile` の候補に入らない
- archive 済みのリビジョンを指す既存レコードが読める
- `slug` の変更が 400

**変異試験:**
- `builtin` の拒否を外す → 409 の試験が落ちること
- 検証を commit の後に動かす → 「不正な定義でリビジョンが作られない」が落ちること
- `active()` の `archived_at IS NULL` を外す → archive の試験が落ちること

---

### Task 6: `recompute_timestamps` ジョブ

**Files:** Create `app/src/mediaferry/jobs/recompute.py` / Modify `app/src/mediaferry/api/jobs_wiring.py`,
`app/src/mediaferry/api/app.py`, `app/src/mediaferry/api/routes_system.py` /
Test `app/tests/test_recompute.py`

§6 が「タイムスタンプ解釈やタイムゾーンを変えた場合、既存データへの再計算は自動では
行わず、`recompute_timestamps` ジョブとして明示的に実行する」と決めている。

**ファイルは動かさない。** ライブラリのパスは `library/<slug>/<カード上の相対パス>` で
**`captured_at` を含まない**（§7）ので、再計算は `media_file` の
`captured_at` / `captured_at_source` / `captured_at_tz` / `captured_at_note` の 4 列だけを直す。

**判断:**

- **対象はプロファイル単位**（`POST /profiles/{slug}/recompute`）。ボリューム単位ではない
- **`source: exif` の場合は公開済みファイルを読み直す。** ステージは残っていない
- **送信済みのリモート資産は書き換えない。** リモートの日時補正は §9.6 の承認の経路を
  通す仕事で、ここでやると承認を飛ばして相手を書き換えることになる。
  **`captured_at` が変わった `media_file` のうち、既に送信済みのものは一覧に出して知らせる**
- キャンセルとリースは他のジョブと同じ作法。件数が多いので、
  **バッチごとにキャンセルとリースの両方を見る**（Phase 3 の Rechecker と同じ形）

**受け入れ:**
- プロファイルの `timezone` を変えて実行すると、そのプロファイルの `media_file` の
  `captured_at` が変わる
- **他のプロファイルの行は変わらない**
- ファイルが 1 つも動かない（`final_rel_path` が不変）
- 送信済みの資産のリモート日時は変わらない
- キャンセルすると途中で止まり、**処理済みの分は commit されている**
- `source: exif` のプロファイルで、公開済みファイルの EXIF から再計算される

**変異試験:**
- プロファイルの絞り込みを外す → 「他のプロファイルの行は変わらない」が落ちること
- バッチの合間のキャンセル確認を外す → キャンセルの試験が落ちること
- リモートも書き換える → 送信済み資産の試験が落ちること

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

**受け入れ（vitest）:** ビルトインで編集ボタンが出ない。保存でリビジョンが上がる。
検証エラーが日本語で出る。`test` ボタンが判定結果を出す。

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

---

## Phase 5 の完了条件（§20）

- [ ] **EOS 70D の SD カードを取り込める** —— 合成カードと fake ブローカーで通す。
      実カードは `phase1-manual-checklist.md` へ（**実機が無いので、この環境で確かめられる
      のはここまで**）
- [ ] `generic-dcim` にフォールバックし、`DCIM` を持たないボリュームは対象外になる
- [ ] プロファイルを画面から複製・編集でき、新リビジョンができる。ビルトインは守られる
- [ ] 信頼登録済みのカードを挿すと、画面を操作せずに取り込みが始まる
- [ ] 2 枚のカードを同時に挿して独立に扱える
- [ ] mountd を再起動してもアプリが復帰する
- [ ] `uv run pytest` / `-m needs_system` / ruff / npm の lint・typecheck・build・test:e2e が通る

---

## Phase 5 でやらないこと（意図的な除外）

| 項目 | 理由 |
| --- | --- |
| `subscribe`（netlink uevent） | 上記「検出の起動源を `subscribe` にしなかった理由」 |
| ffprobe の `creation_time` を第 4 の timestamp source にする | **Canon の MOV が壁時計を書くか UTC を書くかを実データ無しに確かめられない。** 推測で source を足すと、間違っていたときに `captured_at` を一斉に作り直すことになる。チェックリストで測ってから決める |
| `canon-eos` の結合 | 4GB 分割の判別根拠が実データ無しに得られない。誤結合は公開済みの `media_file` を取り残す |
| `UPLOAD_CONCURRENCY` の多重化 | Phase 6。独立した設計課題（上記） |
| PTP 接続 | スコープ外と決定済み（§2） |
| 自動アップロード | §12.1「アップロードは信頼登録の有無にかかわらず常に手動」 |
| プロファイルの import / export | YAGNI。テキストエリアからコピーできる |
| 継ぎ目サムネイル / 動画のプレビュー / `SECRET_KEY` のローテート | Phase 4 が送ったもの。Phase 5 の完了条件に関係しない |

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

（計画レビューはここに書く。実装差分のレビューはその下に足す。**毎巡 `--fresh`。**）
