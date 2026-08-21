# 開発する

このリポジトリで作業するときの入口です。**現在の仕様は
[`design.md`](design.md)**、**そう決めた理由は [`decisions.md`](decisions.md)**、
**どう作ったかの記録は [`history/`](history/README.md)** にあります。

利用者向けの文書は [`setup.md`](setup.md) と [`user-guide.md`](user-guide.md) です。

## この案件の性格

**判断の理由がコミット本文と `docs/` にしか無い**プロジェクトです。コードの
コメントは「いま何をしているか」だけを書き、経緯は `docs/` に置きます
（[`../CLAUDE.md`](../CLAUDE.md)）。

**理屈で結論を出さず、測ってから決めてきました。** dirfd が `..` で親へ抜ける
ことも、`-c copy` の結合が 11.4% 縮むことも、実際に動かして初めて分かりました。
同じ形の判断をするときは、まず測ってください（[`decisions.md`](decisions.md) の
「実測で覆った判断」）。

### レビューで分かったこと（この案件の中核）

**codex のレビューは 28 巡回した**（Phase 3 に 7 巡、Phase 4 に 2 巡、Phase 5 に
計画 2 巡 + 実装 9 巡、Phase 6 に計画 4 巡 + 実装 4 巡）。
巡ごとの詳細は [`history/`](history/README.md) の各 `phaseN-plan.md` の
「レビュー記録」にある。**傾向がはっきりしている。**

| 何を見せたか | 出てくるもの |
| --- | --- |
| **計画に埋め込んだコード**（Phase 3 の 1・2 巡目） | 機械的な欠陥ばかり（blocker 8 件ずつ）。**文書のコードは型検査も実行もできない**ので、毎巡同じ層が残る |
| **計画の範囲と契約**（Phase 4 の計画レビュー） | **足りないもの**（backend の状態が実在しない、Task が無い、E2E の土台が無い） |
| **実装差分**（Phase 3 の 3〜7 巡目、Phase 4 の実装レビュー） | **本当の層**（並行性、相手が値を選べる応答、実装した順序）と、**つながっていないもの**（API はあるが画面から呼べない） |

**直した箇所の周りをもう一度見せると、また出る。** Phase 3 は 5・6・7 巡目とも
blocker が 2 件ずつ出た。**巡を重ねても止まらない。** Phase 5 の計画も同じで、
1 巡目の blocker 4 件を直した 2 巡目に**新しい blocker が 3 件出た**（いずれも
1 巡目で作り直した箇所の周り）。**ただし実装に入ると、その 3 件はどれも
「実装を読めば分かる」層だった** —— 計画に対するレビューはそこで打ち切り、
実装差分を見せる側へ移して正解だった。

**レビュー役は毎回 `--fresh` で回す**（下の「codex への経路（agmsg）」）。継ぎ足すと、**自分が提案した
対処の周りが盲点になる** —— 7 巡目で出た blocker 2 件は、どちらも 5・6 巡目のセッション
自身の提案だった（版の書き換え、`sha256:` 接頭辞）。

**Phase 6 でも同じ形が出た**（実装差分レビュー 4 巡、major 5 → 3 → 1 → 0）。
**毎巡「前の巡の対処が作った境界」から出ている。**

| 巡 | 出どころ |
| --- | --- |
| 1 | **実装して初めて現れた接続部** —— 無効化した宛先（claim を使わない経路が安全条件から外れる）、再確認との相互作用、回収の GET、`PUT` 後の実像 |
| 2 | 1 巡目の対処が作った境界 —— 組の相方が取り残される、trigger が「別 ID への差し替え」を塞いでいない、資産 ID の重複 |
| 3 | 2 巡目の対処が作った境界 —— **reopen を CAS より先に呼んで「古い観測は何も書かない」を破った**、理由の取り違え |
| 4 | docs の同期のみ（実装の指摘なし） |

**計画レビューでは出なかった層が、実装では 5 件出た。** 文書に対するレビューを
何巡重ねても、この層には届かない。

**繰り返し出た誤りの型**（次も同じ形で出る）:

- **形から素性を推定する。** 「64 hex なら指紋」「unreserved なら安全」は、同じ形の秘密を
  素通りさせる。信用できるのは cohort（版）だけ。値自身に持たせた印も、**相手が値を
  選べる場所では出所にならない**
- **直した検査が新しい境界を作る。** 拒否の列挙は fail-open、移行は新しい状態（混在 DB）を
  作り、guard は片側だけ強くなる
- **順序**。trigger が先に走る、`await` の後で参照が消える、mount の順で API が飲まれる
- **受け入れの経路に入っていない機能は、無いのと同じ**（API はあるが画面から呼べない）

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

### 開発コンテナ（この LXC）

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
  上限を置き、別スレッドで走らせて `join(timeout=…)` で見る。** Phase 5 では
  これで 2 度詰まった（`SilentBroker` と ReDoS の試験）

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

**`.orig` からの復元は飾りではない。Phase 5 で 2 回それに救われた。**
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

**変異は「成立する形」で当てる。** レビュー 4 巡目で、`range` の刻みだけを変える
変異が素通りした。原因はテストの弱さではなく**変異の当て方**で、スライスの側が
元の定数を見たままなので分割が消えていなかった。**素通りを見たら、まず「その変異は
本当に狙いの判断を壊しているか」を疑う。**

**Phase 5 で見つかった「素通り」の型**（58 件中 47 件を検出し、素通り 11 件を精査した）:

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

### 作業の作法

1. 各タスクは「失敗するテストを書く → 失敗を確認 → 最小実装 → 通ることを確認 →
   変異試験 → コミット」で完結させる
2. **変異試験のステップを省かない。** Phase 1 では、計画のテストが「通っては
   いるが実装の判断を検証していない」箇所が **30 件以上**見つかった。特に多い
   パターンは次の 3 つ:
   - 別の分岐で先に落ちていて、狙いの分岐を一度も通っていない
   - 結果が同じになる筋書きしか試していない（差が出るのは「読んだバイト数」
     「試した名前の数」のような量だけ）
   - 順序規則を、たまたま同じ順になるデータで試している
3. **検出できない変異は、検出できないことを計画に書く。** 構造的にテスト不能な
   保険は実在する（`claim_next` の CAS 条件、`_materialise_link` の `os.fsync`、
   `sort_keys`、スキーマの trigger が保証する冗長条件など）。ただし
   **「検出できない」と書いてある変異の多くは、テストを 1 つ足せば検出できた**
   —— Phase 2 では計画が検出不能としていた 5 件のうち 4 件を、実装時に固定できた。
   まず落とせないか試し、それから記録する
4. **変異は「成立する形」で当てる。** 例外で全件落ちる書き換え（dict を
   `sorted` に渡す等）は、狙いの判断を検証したことにならない。
   **`start_new_session=True` を外す変異は当ててはいけない**（子がテストランナーと
   同じプロセスグループに入り、キャンセル試験の `killpg` が pytest ごと撃つ）
5. 計画から外れる判断をしたら、その場で計画側にも書き戻す
6. 詰まったら codex に相談する

### レビューの依頼

**先にコミットしてから、hash とファイル名を渡して読ませる。** 差分の全文を本文へ
貼ると、反映前の版をレビューされることがある（実際に 1 度起きた）。

**何を見せるかで、出るものが変わる。** 文書に埋め込んだコードは型検査も実行も
できないので、機械的な欠陥が毎巡残る（上の表）。**実装差分を見せると、
文書では出なかった層**（SQLite の並行性、相手が値を選べる応答、実装した順序）
**が出る。** 直した箇所の周辺をもう一度見せると、また出る。

### codex への経路（agmsg）

codex とのやり取りは agmsg 経由。チーム `mediaferry`、こちらが `mediaferry-dev`
（claude-code）、レビュー役が `mediaferry-reviewer`（codex）。スクリプトは
`~/.agents/skills/agmsg/scripts/` にある。**依頼のたびに起動し、終わったら必ず
片付ける。**

```bash
S=~/.agents/skills/agmsg/scripts
TEAM=mediaferry FROM=mediaferry-dev REVIEWER=mediaferry-reviewer

# この役割の bridge の pid だけを取る。行を team/role で固定するのは、別の codex
# 役割が増えたときに pid が複数行になって後段の ps が壊れないようにするため
bridge_pid() {
  "$S/delivery.sh" status codex "$(pwd)" \
    | sed -n "s|^Codex bridge: $TEAM/$REVIEWER alive (pid \([0-9]*\)).*|\1|p"
}
# ラベル完全一致でペイン id を取る（grep の部分一致だと別役割を拾う）
pane_id() {
  herdr pane list | python3 -c "import sys,json;print(next((p['pane_id'] \
    for p in json.load(sys.stdin)['result']['panes'] \
    if p.get('label')=='$REVIEWER'),''))"
}

# 1. 起動（この環境は tmux ではなく herdr のペインになる）
"$S/spawn.sh" codex "$REVIEWER" --project "$(pwd)"
PANE=$(pane_id)

# 2. bridge が起きるまで待つ。spawn は codex の readiness を待たずに返るので、
#    1 度見て not running でも失敗ではない
BPID=""
for _ in $(seq 30); do BPID=$(bridge_pid); [ -n "$BPID" ] && break; sleep 1; done

# 3. 依頼。alive を確かめられたときだけ送る（待ちが空振りしたら送らない）
if [ -n "$BPID" ]; then
  "$S/send.sh" "$TEAM" "$FROM" "$REVIEWER" "<依頼>"   # hash とファイル名を渡す
else
  echo "bridge が上がらない。送らずに調べること"
fi

# 4. 片付け。--force を最初から付ける
"$S/despawn.sh" "$TEAM" "$FROM" "$REVIEWER" --force   # → status=forced

# 5. 確認。status=forced は証明にならないので 3 つを別々に見る
"$S/identities.sh" "$(pwd)" codex                     # → 空なら登録は消えた
[ -n "$PANE" ] && pane_id | grep -q . && echo "ペインが残っている: $PANE"
#    bridge は即死しない。launcher が登録の消失を 2 tick 連続で見てから落とす
#    設計で、poll は 0.3→2 秒に伸びる（実測で終了まで 3 秒。即時の ps 1 回だと
#    正常系でも「生存」と誤読する）。消えるまで期限付きで待つ。args も見るのは
#    pid が再利用されていた場合に別プロセスを「生存」と誤読しないため
for _ in $(seq 15); do
  ps -p "$BPID" -o args= 2>/dev/null | grep -q codex-bridge.js || break
  sleep 1
done
ps -p "$BPID" -o args= 2>/dev/null | grep -q codex-bridge.js \
  && echo "bridge が残っている: $BPID"
```

**`--force` を最初から付けること。素の graceful を先に打ってはいけない。**
graceful な despawn は actas ロックだけを土台にしている（前提チェック、teardown の
実行主体であるメンバー側 `watch.sh`、完了判定のポーリング、3 つとも）。codex は
`actas-claim` を一度も走らせないためロックが常に `free` で、graceful は
`status=ok note=no-live-lock` を返して**何も片付けない**。しかもその離脱の直前に
`rm -f "$SPAWN_REC"` が走り、`--force` が読むはずの placement レコード
（`spawn.sh` が `herdr:<pane_id><TAB><project><TAB>codex` を書いている）を消す。
結果、続けて `--force` を打っても `no placement record` で exit 1 になる。
**その起動分は `despawn.sh --force` で後追いできない**（spawn し直せば placement は
また作られるし、手での teardown は残っている）。順序は一方通行。

`--force` はペインを閉じ、登録を落とし、placement レコードを消す。**bridge を
kill するコードは `despawn.sh` にも `reset.sh` にも無いが、それでも bridge は
止まる。** 止めているのは `codex-bridge-launcher.sh` の子で、毎 tick
`identities.sh` を見て自分の役割が 2 連続で空なら pidfile の bridge を kill して
`exit 0` する（同ファイルの `deregistered_ticks`。2 連続を要求するのは、
`identities.sh` が読み取り失敗でも空を返すため）。**手動の kill は要らない。**
この節を読んで `despawn.sh` だけを見ると「force は bridge を止めない」と読めるので、
根拠の在り処をここに書いておく。

**`delivery.sh status codex` は片付けの確認に使えない。** 登録を列挙する実装なので、
登録が消えた後は bridge が残っていても
`no identities registered for this project` と出る。停止の確認は上の手順 5 のように
pid を直接見る。同じ理由で `despawn.sh` の `status=forced` も証明にならない
（ペイン close も `reset.sh` も失敗を握り潰したうえで常に `forced` を返す）。

**プロジェクト共用の `codex app-server` は残る。** 次の spawn が使い回すので放置で
よい。明示的に落とすなら `delivery.sh set off codex "$(pwd)"`（`stop_codex_bridge`
が bridge と app-server の両方を落とす）だが、**delivery 設定まで `off` になる**ので
使ったら `set monitor` で戻すこと。

**起動しっぱなしにしない理由**: codex CLI が居なくなっても bridge プロセスだけが
生き残ることがあり、その状態で `mediaferry-reviewer` 宛に送るとメッセージを黙って
飲み込む。手で `herdr pane close <pane_id>` だけした場合がこれに当たる（登録が
残るので bridge も残る。ペインを閉じても SessionEnd フックは走らない）。

**この状態を bridge の `kill` で直そうとしない。効かない。** 登録と app-server が
残っている限り launcher の子は生きていて、pidfile が消えたのを見て bridge を
起動し直す（`codex-bridge-launcher.sh` の再利用判定。pidfile が無い / pid が死んで
いる枝は、そのまま起動へ落ちる）。**ペインを閉じただけなら placement レコードは
残っているので、`despawn.sh ... --force` がそのまま使える**（消すのは graceful の
`free` 分岐と `--force` 自身だけ）。それも通らないときは
`delivery.sh set off codex "$(pwd)"` で launcher の寿命元である app-server ごと
落とし、`set monitor` で戻す。

（2026-08-18 に確認。graceful → `--force` の順で打って詰まり、`--force` を最初から
打つ手順で spawn から片付けまで 1 サイクル通ることを実測した。）

**spawn は前回のセッションを resume する。レビューは `--fresh` で回す。**

`spawn.sh` は **role（team + agent 名）ごとに前回の CLI セッション id を憶えている**
（`~/.agents/skills/agmsg/run/role-session.<team>__<agent>`）。codex の `type.conf` は
`resume_arg=resume` なので、起動コマンドは `codex resume <uuid> …` になり、**前回の
文脈を引き継いで立ち上がる**。`despawn --force` はこの記録を消さないので、次の spawn も
同じセッションに戻る（rollout が消えていれば fresh に倒れる）。

継ぎ足しなので**コンテキストは巡ごとに積み上がる**。5・6 巡目を同じセッションで回した
時点の実測は、rollout 5.4 MB、`last_token_usage.input_tokens` 141,233 に対して
`model_context_window` 258,400（約 55%）。このまま行くと 7〜8 巡目で窓に当たり、
codex 側の自動圧縮が過去巡を要約に落とす。

**7 巡目からは、毎巡 `--fresh` を付ける。**

```bash
$S/spawn.sh codex "$REVIEWER" --project "$(pwd)" --fresh
```

理由は 2 つ。(1) レビューに要る文脈は**リポジトリ側に全部ある**（依頼文で対象コミットと
重点箇所と [`decisions.md`](decisions.md) を渡している）ので、継ぎ足しの利点が小さい。(2) 同じ
セッションのレビュー役は**自分の過去の判断に引きずられる**ので、独立した目としては弱く
なる。

**「1 度 `--fresh` にすれば以後も新しい」ではない。** `--fresh` で起動すると role の
記録は**その新しいセッション**に置き換わる（実測: 7 巡目の spawn 直後に
`session=` が新 uuid になった）。次に `--fresh` 無しで起動すると、今度はその
セッションを継ぎ足す。**毎回付ける。**

fresh のレビュー役は前巡を知らないので、依頼文は自己完結させる（対象コミット、
背景 1 段落、先に読ませる docs、重点箇所）。7 巡目の依頼文がその形。

**やり取りの実務。**

- **返信は待ち構えなくていい。** monitor モードなので、codex の返信は Monitor
  （`agmsg inbox stream`）に流れてくる。かつては `history.sh` を回して待って
  いたが、その必要は無い。待ち時間の目安は、4800 行の計画のレビューで
  返信まで約 6 分だった
- **長文メッセージは配送が遅れる。** 変更点の全文を貼らず commit hash と
  ファイル名を渡すのは、上の「レビューの依頼」とは別に agmsg 側の理由からも
  正しい
- **過去ログは `history.sh <team> <agent> <件数>`。** 既定は 20 件で、全件を
  吐く口は無い（3 番目の引数が件数。数字以外を渡すと黙って 20 に戻る）
- **名簿は `team.sh <team>`。** チーム名は名前空間なので、別チームに同名の
  メンバーが居ても衝突しない（一意であることが要るのはチームの中だけ）
- **codex の生死は `delivery.sh status codex` の `Codex bridge:` 行で見る。**
  `watch processes:` の数は claude-code 側の `watch.sh` を数えたもので、
  `delivery.sh status claude-code` に出る別物。codex 側の状態ではない

**2026-08-18 より前のレビュー履歴は残っていない。** この日 agmsg 自体がリセット
され、チーム定義も履歴も消えた。Phase 3 の 2 巡目までは `chezmoi` チームの codex
とやり取りしていたが、そのチームごと無い。`mediaferry` チームの旧名簿
（`deckhand` / `lookout`）も同様。探しても出てこないので、探さないこと。

---

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

---

## まだ確かめられていないこと

**2026-08-20〜21 に実機（TrueNAS の Custom App）で検証を始めた。** そのときの
記録は [`history/hardware-verification.md`](history/hardware-verification.md) に
ある（実測値、実機でしか出なかった不具合 9 件、ffmpeg の実挙動）。以下は
**まだ残っているもの**。

1. **Immich への送信（Phase 3・6）。いちばん大きい未検証。** 転送先の登録、
   `origin` の判別、タグ、日時の書き戻し、RAW/JPEG のスタッキングまで、
   **一度も動かしていない**。ライブラリに実データが揃っている状態でやる方が
   安い（取り込みに 30 分かかる）。
2. **チェックリストの残り。** 5 番（抜く。修正後の踏み直し）と 8 番（キャンセル）。
   **リセット後の最初の取り込みでやると楽**（全件が未取り込みになるので、120 GiB の
   途中で好きなところで止められる）。10・11・12 番は済んだ（11 番は不合格。
   **対処は入れた**ので、リセット後の再取り込みが確認の機会になる —— 写真を
   足して `captured_at` が現地の壁時計になることを見る）。
3. **Canon EOS 70D の実カード（13〜17 番）。** プロファイルは仕様と知識から
   書いており、**実データを一度も見ていない**。**17 番は Phase 6 で増えた項目**
   （RAW+JPEG の組が実カードで成立するか。`exifread` が実機の CR2 から
   `DateTimeOriginal` を読めるかが要）。
4. **自動取り込み（§12.1）。** 承認したら「いま挿してあるカードの中身が数秒後に
   取り込まれる」を実機で見ていない。

**バックアップとリストアは 2026-08-21 に実測して閉じた**（`backup.md` に反映済み）。

## 持ち越している判断

**決着していない、または実機待ちのもの。** 決着済みの判断は
[`decisions.md`](decisions.md) にある。

| 項目 | 状況 |
| --- | --- |
| 実 USB での確認 | `phase1-manual-checklist.md` に手順を用意済み。未実施 |
| **`0007` が既存 DB に再検証を要求する** | 相手由来の観測（`remote_user_id` / `server_instance_id` / `remote_asset_id`）を捨てるので、開いた直後はどの宛先も「向き先の記録が無い」で**閉じる**（送信は始まらない）。**宛先を保存し直す（PATCH）と新しいリビジョンに今の観測が入って直る。** 送信済みレコードの識別子は宛先ごとの再確認がチェックサム照合で戻す |
| **`0005` を版を足さずに書き換えた** | 古い `0005` を適用済みの DB は runner が `MigrationError` で開けない（配布前なので開発用 DB は作り直す前提）。**以後この手は使わない** —— 7 巡目の blocker になった |
| 認証の既定 | **off のまま**（利用者の判断）。`BIND_HOST` の既定も loopback。`TRUSTED_HOSTS` は IP と `localhost` を既定で通し、ホスト名だけ許可制 |
| フロントの依存 | `web/package-lock.json` を追跡している。Playwright のブラウザは `npx playwright install chromium` で入れる（CI で回すなら `--with-deps`） |
| **mtime の解釈** | **決着した（2026-08-21）。** DJI は exFAT の `OffsetFromUtc` を書いており、**mtime は真の瞬間**だった（11 番の実測）。壁時計は解決した TZ で描画する形へ 3 か所を同時に直した（`decisions.md` の「実測で覆った判断」）。**実機で確かめるのはこれから** —— 写真（`PANORAMA/PANO_*.JPG`）を含む再取り込みで見る |
| ~~取り込み側のリースの穴~~ | **塞いだ**（Phase 2 の Task 7）。`_with_lease_pulse` が共通の `_publish` に入ったので、16 GiB のコピー後の `os.fsync` と ffprobe も守られる。回帰テストは `test_publisher.py::test_a_slow_fsync_does_not_lose_the_lease` |
| `_publish` の外の `fsync_dir` | ジョブ用ディレクトリを作った直後の `fsync_dir` は `_with_lease_pulse` の外にある。ディレクトリの fsync はメタデータだけなので実運用では一瞬で終わるが、極端に遅い環境では守られていない |
| `disposition.attached_pic` | 結合の「最初の映像ストリームのみ」の判定が、埋め込みサムネイルをこれで見分ける。実機の DJI ファイルで立っているかは未確認（`keep_streams.video` が `primary` の間は影響しない）。チェックリスト 12 番で見る |
| TS フォールバックの実運用 | Phase 0 の実測では DJI は concat 経路で通っており、TS 経路は**まだ実データで走っていない**。テストは lavfi のクリップで両経路を通す（`tmcd` の脱落まで再現している） |
| サイズ検査の許容誤差と合成クリップ | 2% は 16 GiB 級の実ファイルが前提（オーバーヘッド 0.002%）。数百 KB の合成クリップでは 7〜8% ずれるので、e2e は「不合格でも公開され、採用すれば選択肢に出る」経路で通している |
| 5 パート連続録画（70 GiB 級）のアップロード | 28.36 GiB は完走した。同じ経路で扱える見込みだが未実測。タイムアウトは比例して伸びる |
| Canon EOS 70D のプロファイル | **書いたが実データを一度も見ていない**（Task 4）。E2E で通しているのは `require` から組み立てた合成カードまで。`merge` は無効、`hints.usb_ids` は空。実カードでの確認は `phase1-manual-checklist.md` の 13〜16 番 |
| **`0011`（`captured_at_revision_id`）** | **入れた**（Task 6）。既存 DB へは `profile_revision_id` の写しで埋め戻る。trigger 2 本が「必ず値を持つ」「同じプロファイルの版である」を守る |
| **`0012`（再計算の抽出用の索引）** | **入れた**（実装差分レビュー 4 巡目）。`source_entry (media_file_id, observed_at, id)` と `merge_group (output_media_file_id)`。無いと `media_file` 1 行ごとに `SCAN` が走り、**最初の `assert_lease` に届く前にリースが切れる** |
| **`0013`（ページ送りの駆動索引）** | **入れた**（実装差分レビュー 5 巡目）。`media_file (profile_id, role, rel_path)`。`LIMIT` は**返す件数しか縛らない** —— 無いと別プロファイルの全行を走査しうる。**`0012` を書き換えずに版を足した**（§7 の `0005` の教訓） |
| **`0014`（一覧の索引）と `IN` → `=`** | **入れた**（実装差分レビュー 6 巡目）。`0013` を足したら、プロファイルで絞った一覧が `media_file_captured_at` を辿る経路から外れ、**全行を拾ってから並べ替える**ようになった。`media_file (profile_id, captured_at DESC, id DESC)` を足し、`_filters` の `IN (SELECT ...)` を `= (SELECT ...)` へ変えた（`IN` だと複数の値を取りうると見なされ、索引があっても並べ替えを外せない）。**索引を足したら、他の問い合わせの EXPLAIN も見る** |
| **`js-yaml` を web の依存に足した** | プロファイル編集の YAML テキストエリア（Task 7）。**v5 は自前の型を持つ**ので `@types/js-yaml` は入れない（入れると衝突する） |
| ~~**`main` へのマージ**~~ | **済んだ**（2026-08-19）。`phase5-generalization` の 21 コミットを `--no-ff` でマージし、マージ結果でも全テストを流してからブランチを消した |
| **`0015`（スタックの 3 列）** | **入れた**（Task 1）。trigger 2 本が 3 列の組み合わせを守る。**比較は `IS`**（`=` だと NULL のとき WHEN が成立せず素通りする）。索引 `upload_record (destination_id, target_epoch, id)` は部分索引で、**述語を問い合わせ側と一字一句そろえる** |
| **スタックの規則は「現行リビジョン」で読む** | 取り込み時の版ではない（`immich.tags` と同じ層）。組ごとに版を固定し、guard と記録の CAS で「まだ現行か」を見る。版が進んだら見送りを未評価へ戻す（`_publish_revision`） |
| **「既存スタックに触らない」は保証ではない** | Immich に条件付きの作成が無く、読み直しと `POST` の間の競合は実装で閉じられない。残余として §9.11 に明記して受け入れた。吸収されても、要求と違う集合は protocol error にするので **id は記録しない** |
| 認証を既定 off のままにするか | ユーザの判断で off。`BIND_HOST` の既定を loopback にし、認証無効で非 loopback にバインドしていたら警告する緩和のみ |
| Phase 1〜3 を配布可能リリースにしない | 認証と CSRF が入る Phase 4 より前に LAN へ公開しない |
| SSE（`GET /events`） | Phase 4 の Web UI と一緒に入れる。Phase 1 は `GET /api/jobs/{id}/events?after_seq=` のポーリング |

## 作業の進め方

**Phase 5 で分かったこと**（フェーズを跨いで効く）:

- **受け入れ（E2E）は、単体では見えない抜けを捕まえる。** Task 8 の「判定の理由」は
  対象外のときだけ出しており、vitest では画面の一部しか見ていないので**無いことが
  仕様に見えていた**。E2E が一致したカードの理由を探して初めて落ちた
- **変異が素通りしたら、まず「相方がマスクしていないか」を見る。** Task 6 の
  「バッチの合間のキャンセル確認」は、続けて呼ぶ `assert_lease` が同じ場合を
  `LeaseLost` として拾うので単独では検出できない。**対で壊せば検出できる**ので、
  冗長さは意図であって削ってよい根拠にはならない
- **「そういう契約だ」と書いた変異が、当てる形を間違えて素通りすることがある。**
  Task 6 の「衝突接尾辞の付いた名前に当てる」は、先頭に錨を打った pattern では
  原名でも別名でも同じ ts になるので差が出ない。**差が出る形（錨の無い pattern）を
  持つプロファイルを試験に用意して初めて成立する**

- **自分が書いたテストも素通りする。** Phase 5 の実装中、**書いた直後のテストが
  既定値と一致するだけで通っていた**ことが 1 度あった（`provisional` の永続化。
  fixture が `False` で DB の既定値も 0）。**両方の値を通す筋書きを作ってから
  実装する**
- **変異試験の素通りは、まずテストの穴を疑う。** Phase 5 では素通り 11 件のうち
  **7 件がテストの穴**で、構造的に検出できないのは 3 件だけだった（残り 1 件は
  変異の当て方が悪かった）。下の「変異試験のやり方」に型をまとめてある
- **既存のテストが落ちたら、まず「挙動が正しく変わったのか」を見る。** Task 4 で
  4 件落ちたうち 1 件は、差し替えカードの中身がまさに Canon の構成だったため
  `canon-eos` として確定するようになったもの。**テストを通すために直すのではなく、
  新しい挙動をより直接的に書く形へ変えた**（`profile_slug` が変わることを見る）
- **試験の土台の弱さが、契約の検証を素通りさせる。** `broker_factory` が毎回同じ
  接続を返していたので「watcher は専用の接続を持つ」を検証できなかった。
  **土台を直してから契約を試す**
- **計画に書いた対処が、実装を読むと成立しないことがある。** Task 6 の
  `awaiting_datetime_approval` への差し戻しは、実装を読んで `needs_recheck` へ
  変えた（その状態は `tagging` からしか入れず、現在値の観測に Immich の
  クライアントが要る）。**レビューが来る前に自分で見つけられた**

**Phase 4 で分かったこと**（フェーズを跨いで効く）:

- **試験の土台を先に作る。** E2E の土台（実プロセス + fake broker + fake Immich 2 台）を
  画面より前に作ったので、SSE が `TestClient` で詰まったときに「実プロセスでは動く」と
  切り分けられた。UI の型が API とずれていたことも、**React の例外**として教えてくれた
  （`pageerror` を拾う）
- **試験できない層は、試験できる層へ寄せる。** SSE は `TestClient` で試せないので、
  位置の決め方と資源の返し方は生成器を直接呼ぶ単体試験に、線の上の挙動は実プロセスに
  分けた。**片方だけだと、変異試験に載らないか、本物を通らない**
- **「実装した」と「つながっている」は別。** API はあるのに画面から呼べない機能が 4 つ
  あり、E2E が緑のまま素通りしていた。**受け入れの経路に入っていない機能は、無いのと同じ**
- **素通りした変異は、まずテストの当て方を疑う。** Phase 4 で素通りした 9 件のうち 7 件は
  テストの穴（1 回の poll で届く範囲しか見ていない、途中まで書けてから失敗する経路を
  通っていない、テストが互いをマスクしている）だった。**構造的に検出できないのは 2 件だけ**

- **理屈で結論を出さず、測る。** dirfd の `..` 脱出も、結合の 11.4% 欠損も、
  `os.listdir(-1)` がカレントディレクトリを返すことも、実際に動かして初めて
  分かった。どれも実装後に気づいたら手戻りが大きい
- **判定は終了ステータスで行う。** 出力を読んで「動いていそう」で通さない
- **テストが素通りしていないか変異試験で確かめる。** Phase 1 では毎タスクで
  実施し、30 件以上の穴が見つかった。**「テストが通った」は「実装が正しい」の
  証拠にならない**
- **計画のテストも疑う。** Phase 1 では計画側のテストに 5 件のバグがあった
  （狙いと違う制約で落ちていた、前提を満たさない値を使っていた、不変 trigger を
  発火させる無意味な行があった、fixture がクライアント側だけを差し替えて
  サーバと不整合になっていた、ビルトインが 1 つしか無い前提を見落としていた）
- **codex のレビューは鵜呑みにしない。** 設計で反論したのは 2 点だけだったが、
  その 2 点（SHA-256 の追加、認証の必須化）は退けて正解だった。逆に
  「暗号化は無意味」という自分の理屈は誤りで、指摘を受けて撤回した。
  Phase 2 の計画では 12 件を反映し、1 件（検証器の版を `input_digest` に入れる）
  を**設計の定義を根拠に退けて、先方も受け入れた**
- **レビューは 1 巡で終わらせない。** Phase 2 の計画では、1 巡目の blocker 4 件を
  直した後の 2 巡目で、**さらに blocker が 2 件出た**。しかもどちらも
  「直した箇所の周辺」だった（pulse を read loop に入れたが、その後の `fsync` と
  ffprobe が守られていない／キャンセル例外を上げると `JobRunner` が `failed` に
  する）。**修正が新しい境界を作るので、そこをもう一度見せる**
- **既存コードとの接続部を疑う。** Phase 2 で見つかった blocker のうち 2 件は、
  新しいコードではなく**既存の `JobRunner` と `ArtifactPublisher` との境界**に
  あった。片方は取り込み側にも同じ形で存在していた
- **計画にコードを全部書くと、レビューで実際の穴が出る。** 「ここで heartbeat を
  打つ」と散文で書いていたら、`fsync` と ffprobe が守られていないことは
  見つからなかった。手順の順序と例外の流れまで書いてあったから指摘できた
- **計画の「検出できない変異」を鵜呑みにしない。** Phase 2 では計画が検出不能と
  していた 5 件のうち 4 件を、テストを 1 つ足すだけで固定できた（`LeaseLost` の
  待ち、`fsync` の囲み、`truncated`、片側のフレーム判定）。「観測には競合の
  再現が要る」と書いてあっても、たいていは決定的に組める
- **計画が「既存のテストが落ちる」と書いていても、そのテストの実在を確かめる。**
  Phase 2 では `test_a_short_write_is_aborted` を前提にした変異があったが、
  その名前のテストは Phase 1 に存在しなかった
- **合成データは実データの比率を持たない。** lavfi の小さいクリップは MP4 の
  オーバーヘッドが 7〜8% を占め、実機（0.002%）を前提にした 2% のサイズ検査に
  必ず落ちる。閾値の妥当性を合成データで測らない
- **実装で初めて分かることが残る。** Phase 2 では、計画に無かった処理が 1 つ要った
  （TS 片の並びを揃える `_ts_layout`）。`concat:` が生バイトを継ぐことは、
  実際に 2 本つないでみるまで表に出なかった
