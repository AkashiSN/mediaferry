# 開発する

このリポジトリで作業するときの入口です。**現在の仕様は
[`design.md`](design.md)**、**そう決めた理由は [`decisions.md`](decisions.md)**、
**どう作ったかの記録は [`history/`](history/README.md)** にあります。

利用者向けの文書は [`setup.md`](setup.md) と [`user-guide.md`](user-guide.md) です。

## この案件の性格

**判断の理由がコミット本文と `docs/` にしか無い**プロジェクトです。コードの
コメントは「いま何をしているか」だけを書き、経緯は `docs/` に置きます
（[`../CLAUDE.md`](../CLAUDE.md)）。**同じ層をもう一度触るときは、
[`history/lessons.md`](history/lessons.md) に何が見落とされやすいかがあります。**

**理屈で結論を出さず、測ってから決めてきました。** dirfd が `..` で親へ抜ける
ことも、`-c copy` の結合が 11.4% 縮むことも、実際に動かして初めて分かりました。
同じ形の判断をするときは、まず測ってください（[`decisions.md`](decisions.md) の
「実測で覆った判断」）。

## 環境の癖と罠

### TrueNAS ホスト

- **共有データセット** `/mnt/ssd/develop-server/` が開発コンテナとホストの両方から
  同じパスで見える。ソースは `/mnt/ssd/develop-server/mediaferry/` に配置済み
  （`git archive HEAD | tar -x -C ...` で更新）
- **既定シェルは zsh**。以下は bash と違うので手順書に書かない
  - 行内コメント（`cmd  # 説明`）が**無効**。`#` 以降が引数として渡る
  - `tail -1` が `option used in invalid context` になる。`tail -n 1` を使う
  - `${PIPESTATUS[0]}` が展開されない。パイプを避けてリダイレクトで受ける
- Immich の内部エンドポイントは `http://172.16.100.21:80`（環境固有。リポジトリには書かない）

### 開発コンテナ

- **入れ子の非特権 LXC。AppArmor がマウントを阻む。** privileged コンテナ内でも
  `mount` は通らないので、マウント絡みの検証は TrueNAS ホストで行う
- `unshare -Urm` は使える。`needs_root` のテストはこれで通る
- **`/dev` を汚さないこと。** 過去に `mount -o loop` を privileged コンテナで走らせて
  `/dev/loop0`〜`loop1048575` を 104 万個作り、`docker run --privileged` が
  spec サイズ超過で動かなくなった。`/dev` は tmpfs なので再起動で戻る

### テストと lint

- **`ruff format` は Markdown 内のコードブロックも整形する。** `docs/` は
  `extend-exclude` で対象外にしてある。外すと仕様書と計画そのものが書き換わる
- **Python の版は `.python-version`（3.14）で決まる。** イメージの
  `python:3.14-slim-bookworm` と揃えてあるので、手元・CI・配るものが同じ版になる。
  外すと uv がその場に居る適合版を掴み、**配らない版でテストが通る**
- **`except` の括弧は ruff format が外す**（`except A, B:`）。`target-version` が
  `py314` なので PEP 758 の書き方に整形される。Python 2 の `except E, name:` とは
  別物で、**名前の束縛ではなく複数の型**を並べている
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付ける。** 変異の前後でバイト数が
  変わらない書き換え（`>` を `<` にする、`a or b` を `b or a` にする等）では、
  `.pyc` の無効化条件（mtime の秒＋サイズ）をすり抜けて古いバイトコードが使われ、
  変異が効いているかを読み違える
- `os.listdir(-1)` は **EBADF にならずカレントディレクトリを返す**。閉じた
  `VolumeHandle` の `dirfd` は -1 なので、「閉じた」ことを `pytest.raises(OSError)`
  で確かめられない。`handle.closed` を見る
- **`pytest-timeout` は入っていない。** `uv run pytest --timeout=90 --version` は
  通るが（`--version` が引数検証より先に返る）、実際に使うと
  `unrecognized arguments` になる。**「入っている」と誤読しないこと**
- **`test_api.py::test_shutdown_waits_for_the_running_handler` が一度ハングした**
  （2026-08-21。通常 3 分 45 秒の全件が 27 分止まった。単体で流すと 5 秒、直後の
  全件も 226 秒で通ったので再現しない）。`slow_scan` は `ctx.cancelled()` が立つまで
  回り、lifespan はその worker を**上限無しで待つ**ので、キャンセルが届かないと
  失敗ではなく無限待ちになる。**止まったときの当たり付け**: `/proc/<pid>/task/*/wchan`
  に `skb_wait_for_more_packets` が並んでいたらソケット待ち、
  `/tmp/pytest-of-ubuntu/pytest-current/` の最新ディレクトリがその時のテスト名
- **回帰でテストが「ハング」する形を書かない。** 応答しない相手を待つ試験や
  ReDoS の試験を素直に書くと、実装を壊したときに失敗ではなく無限待ちになる
  （`to_thread` のスレッドはインタプリタ終了時に join される）。**待ちには必ず
  上限を置き、別スレッドで走らせて `join(timeout=…)` で見る。** これで 2 度
  詰まったことがある（`SilentBroker` と ReDoS の試験）
- **締め切り（リース・満期）を試すテストは、2 つの向きを分けて数を決める。**
  「壊したら落ちる」側は**`sleep` の合計だけ**で満たす（`sleep` は機械の速さに
  関わらないので、速い機械でも遅い機械でも検出できる）。「壊していないのに
  落ちない」側は**締め切りから 1 周ぶんを引いた余地**で決まり、そこに入るのは
  DB 書き込みなどの**実処理＝機械の速さに比例して伸びるもの**。手元の実測に対して
  **10 倍の余地**を見ておく。手元で 2 倍遅い程度の CI は普通にある。
  実例は `test_uploader.py::test_many_quick_sends_do_not_lose_the_lease`
  （リース 1 秒では実処理に 0.2 秒しか余地が無く、CI で落ちた。3 秒にした）

### 画面を実際に描かせて確かめる

**jsdom は `styles.css` を解析しない。** web のテストは 1 件も CSS の色と寸法を
観測できないので、**見た目の壊れ方は実ブラウザでしか捕まらない**（E2E の幅の検査が
それを担う）。E2E に載っていない状態を見たいときは、**E2E と同じ実サーバを手で
立てる**（fake broker と fake Immich 2 台つきの本物）。

```bash
cd web && npm run build      # 実サーバは dist を配る。忘れると前の版を見ることになる
rm -rf /tmp/mf-ui && mkdir -p /tmp/mf-ui
PYTHONPATH=$PWD/app uv run python -m tests.system.serve /tmp/mf-ui "パスワード" &
# 標準出力の 1 行目が {"url": …, "immich": [...], "data_root": …}
```

- **状態ディレクトリは短いパスに置く。** ブローカーのソケットが `AF_UNIX path too
  long` で落ちる（`sun_path` は 108 バイト）。深いところに掘ると起動しない
- 合成カードの実体は `<状態>/card`（DJI）と `<状態>/canon`（Canon）。ここへ
  ファイルを足す・消してから画面の「スキャン」を押せば、**カード側の増減**を
  そのまま再現できる
- 合成カードの動画は 100 バイトで ffmpeg が読めない。結合まで通したいときは
  `web/e2e/journey.spec.ts` の `mergeTwoParts` と同じく、`<data_root>/library`
  配下の `.MP4` を小さな本物のクリップに差し替えてから「つなぐ」を押す
- **片付けは自分の分だけ。** 親（`tests.system.serve`）を殺しても子の
  `python3 -m mediaferry` は孤児として残る（下の「E2E がサーバを回収しない」）。
  同じ木で別の E2E が走っていることがあるので、`/proc/<pid>/environ` の
  `MEDIAFERRY_DATA_ROOT` で自分のものだと確かめてから落とす

### ブローカー（mountd）とテストの土台

- **`BrokerServer._observe` は lister が返す `broker_epoch` と `generation` を捨てて、
  自分の値で刻み直す。** 世代は観測した集合 `(volume_key, fs_uuid, fs_type, size_bytes)`
  の指紋から算出する。したがって**テストで「抜き挿し」を表すには、集合そのものを
  変えるしかない**（`generation` の欄を書き換えても客体には届かない）。逆に言えば、
  観測トークンは完全にサーバ側で決まるので app 側が値を作れない
- **epoch を試験から指定することはできない**（起動ごとの乱数）。トークンの比較規則は
  スタブの client で単体試験にし、線の上の挙動は実 `BrokerServer` で見る
- **`conftest` の `broker_factory` は呼ぶたびに新しい接続を作る。** handle は発行した
  接続に束縛される（`design.md` §11）ので、同じ client を使い回すと「`VolumeWatcher` は専用の
  ブローカー接続を持つ」を検証できない（watcher の停止が取り込みの相手を切る経路が
  素通りする）
- **`FakeMountManager.mounts` がマウント回数を数える。** 「判定のたびにマウントする」
  代償を測るのに使う

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

### 変異試験のやり方（ドライバはリポジトリに無い）

**ドライバはセッションの scratchpad に置いて使い捨てにしている。** 引き継いだら
下を書き直す（20 行ほど）。要点は「中断されても原文へ戻せること」と
「`PYTHONDONTWRITEBYTECODE=1` を付けて呼ぶこと」。

```python
# mutate.py <対象ソース> "<テストファイル…>" <変異定義 JSON>
# JSON は [{"name": ..., "old": ..., "new": ...}] の配列。
#   1 つの変異が複数箇所に及ぶときは {"name": ..., "pairs": [{"old":…, "new":…}, …]}。
#   **対で効く条件（SELECT と CAS の両方に置いた検査など）は、片方だけ壊しても
#   もう片方が塞ぐので単独では検出できない。pairs で同時に壊す。**
# 開始時に <対象>.orig を残し、次回起動時に残っていればそこから復元する。
# 各変異ごとに: 置換 → pytest -q --tb=no -rf →
#   落ちたテスト名を「[検出]」、1 つも落ちなければ「[素通り]」として出す。
# 最後に必ず原文へ戻す（finally）。
```

**`.orig` からの復元は飾りではない。実際に 2 回それに救われている。**
タイムアウトや `pkill` で SIGTERM を受けると **`finally` は走らない**ので、
変異が適用されたままソースに残る。次回起動時の復元が無ければ、壊れた実装のまま
先へ進むことになる。**変異試験はバックグラウンドへ逃がし、`<対象>.orig` の有無で
完了を待つ。** 走っている間に他のテストを流さないこと（変異中のモジュールを読む）。

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python mutate.py \
  app/src/mediaferry/jobs/uploader.py "app/tests/test_uploader.py" mut.json
```

**`.pyc` の話は本当に効く。** バイト数が変わらない書き換え（`>` を `<`、
`a or b` を `b or a`）では無効化条件をすり抜けて古いバイトコードが使われる。

**変異は「成立する形」で当てる。** `range` の刻みだけを変える変異が
素通りしたことがある。原因はテストの弱さではなく**変異の当て方**で、スライスの側が
元の定数を見たままなので分割が消えていなかった。**素通りを見たら、まず「その変異は
本当に狙いの判断を壊しているか」を疑う。**

**「素通り」の型**（ある回では 58 件中 47 件を検出し、素通り 11 件を精査した）:

| 型 | 例 | 見分け方 |
| --- | --- | --- |
| **狙いの分岐に届いていない** | `exif` の宣言を見る条件を壊したのに、ファイル名が当たる筋書きで試していたので `filename` の枝が先に返っていた | 変異した行に到達しているかを確かめる |
| **観測できる差が無い** | 解決の位置を手順 4 の前後で動かしても、実体は手順 2〜3 で書き終わっているので同じサイズが見える | 出力ではなく**手順の記録**と突き合わせる（`_checkpoint` を記録する） |
| **結果が同じになる筋書きしか試していない** | 動画で EXIF を読んでも `None` が返って fallback に落ちるので `captured_at_source` は変わらない | **呼んだかどうか**をスパイで見る |
| **条件同士が互いをマスクしている** | `detached_at` を `SELECT` と CAS の両方に置いてあるので、片方だけ壊しても塞がる | `pairs` で対を同時に壊す。**対で検出できれば、冗長さは意図であって削ってよい根拠にはならない** |
| **変異が判断を壊していない** | `EMPTY` の定数の値そのものは効いておらず、効くのは `_seen` の初期値 | 壊れる形へ変異を書き直す |
| **構造的に検出できない** | 旧 socket の `close()`（CPython の参照カウントが閉じる）、`details=False`（出力に差が出ない） | **記録に残す** |

**ReDoS の試験を書くときは、その式が本当に破綻するかを測ってから使う。**
`(?P<ts>\d{4})(a|a)+$` は「悪性のつもり」で書いたが、`\d{4}` が先頭で即座に
失敗するので backtracking に入らず、変異試験は素通りしたままだった。

**差し替える偽物は、本物と同じ形にする。** `JobContext.heartbeat` は
`progress` を任意引数に取り、走査の途中は引数なし、`with_lease_pulse` からは
`progress` 付きで呼ばれる。`test_the_hash_scan_pulses_the_lease` の偽物は引数を
受けられない形で書かれていたため、**fsync が心拍の間隔より長い環境でだけ**
`TypeError` で落ちた（開発機では通り、CI #41 で落ちた）。速い機械では届かない
呼び出し口が偽物にはある。**形を合わせておけば、機械の速さでテストの結果が
変わらない。**

### 作業の作法

1. 各タスクは「失敗するテストを書く → 失敗を確認 → 最小実装 → 通ることを確認 →
   変異試験 → コミット」で完結させる
2. **変異試験のステップを省かない。** 「通ってはいるが実装の判断を検証して
   いない」テストは実際に **30 件以上**見つかっている。特に多いパターンは次の 3 つ:
   - 別の分岐で先に落ちていて、狙いの分岐を一度も通っていない
   - 結果が同じになる筋書きしか試していない（差が出るのは「読んだバイト数」
     「試した名前の数」のような量だけ）
   - 順序規則を、たまたま同じ順になるデータで試している
3. **単独で検出できない変異は、対で壊してから記録する。** `list_stale_derived` の
   `JOIN` は `LEFT JOIN` にしても差が出ない —— `WHERE` の側が出所の無い行を落とす
   ため（**条件同士が互いをマスクする**）。`pairs` で同時に壊せば検出できるので、
   冗長さは意図であって削ってよい根拠にはならない
   （`history/hardware-verification.md`）
4. **検出できない変異は、検出できないことを計画に書く。** 構造的にテスト不能な
   保険は実在する（`claim_next` の CAS 条件、`_materialise_link` の `os.fsync`、
   `sort_keys`、スキーマの trigger が保証する冗長条件など）。ただし
   **「検出できない」と書いたものの多くは、テストを 1 つ足せば検出できる**
   —— 検出不能としていた 5 件のうち 4 件を後から固定できたことがある。
   まず落とせないか試し、それから記録する
5. **変異は「成立する形」で当てる。** 例外で全件落ちる書き換え（dict を
   `sorted` に渡す等）は、狙いの判断を検証したことにならない。
   **`start_new_session=True` を外す変異は当ててはいけない**（子がテストランナーと
   同じプロセスグループに入り、キャンセル試験の `killpg` が pytest ごと撃つ）

## 開発コマンド

```bash
uv sync --all-packages        # --all-packages が必須。素の sync ではメンバーが入らない
uv run pytest
uv run pytest -m needs_root     # ユーザ名前空間が使える環境でのみ通る
uv run pytest -m needs_system   # 実プロセスを起動する（E2E の土台）
uv run pytest -m needs_immich   # 実 Immich が要る（下記の env）
uv run ruff check . && uv run ruff format --check .

npm --prefix web ci
npm --prefix web test           # vitest
npm --prefix web run lint / typecheck / build
npm --prefix web run test:e2e   # Playwright（実プロセス + fake broker + fake Immich 2 台）
npm --prefix web run typegen    # OpenAPI から web/src/api/types.ts を作り直す
```

**API を変えたら型を作り直す。** 忘れても `test_api_types_are_current.py` が
落とすので気づける（npm が無い環境でも回る）。

**実 Immich のテストは接続情報を env で渡す。** 会話や履歴に鍵を出さないよう、
リポジトリの外のファイルに置いて読み込む運用にしてある。

```bash
set -a; . ~/.config/mediaferry/test-immich.env; set +a   # URL と API キー
uv run pytest -m needs_immich
```

アプリの起動は `python -m mediaferry`（`BIND_HOST` の既定は `127.0.0.1`）。
画面はビルド済み資産を `MEDIAFERRY_WEB_ROOT`（既定 `/srv/web`）から配る。
開発中は `npm --prefix web run dev`（`/api` をローカルへ proxy する）。
スパイク（いまは [`../tools/`](../tools/README.md) の診断ツール）の再実行は
TrueNAS ホストで。手順は [`history/phase0-findings.md`](history/phase0-findings.md) と
[`../tools/README.md`](../tools/README.md) にある。
