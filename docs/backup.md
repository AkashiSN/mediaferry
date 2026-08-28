# バックアップとリストア

**手順は実機で一通り実測してあります**（[`history/hardware-verification.md`](history/hardware-verification.md)）。

## DB が唯一の状態保持先

`failed_merges/` と `upload/` のような**ファイルシステム上の状態ディレクトリを
持たない**設計にしたので、ライブラリのファイル以外のすべては
`var/mediaferry.sqlite3` にしかない。

ファイルとの齟齬は起動時の reconciliation が回収するが、**それは DB がある
前提**での話であって、DB を失うと回収する側の記録が消える。

## ライブラリから再構築できるもの / できないもの

| 失うもの | 再構築 |
| --- | --- |
| `media_file`（実体・ハッシュ・撮影日時） | ライブラリを再スキャンすれば作り直せる（ffprobe とファイル名から） |
| `source_entry`（カード側の取込済判定） | カードを再スキャンすれば作り直せる。ただし取込済の判定は一度失われ、**全件が新規に見える** |
| `merge_group`（グループの構成と採用の判断） | 自動検出はやり直せるが、**手動編集と採用の記録は失われる** |
| `upload_record`（宛先ごとの送信済み状態） | **再構築できない。** 再送すると Immich 側の重複判定で弾かれるが、`origin` が `unknown` に落ちるので日時補正が承認待ちになる |
| `destination_credential`（API キー） | **再構築できない。** 再登録が要る |

つまり「写真と動画そのものは失われない」が、「どこまで送ったか」と「なぜその
派生物を採用したか」は失われる。バックアップの価値はそこにある。

## 取り方

対象は `var/mediaferry.sqlite3` と、その `-wal` / `-shm`。

**稼働中にファイルをコピーしない。** WAL モードなので、3 つのファイルを個別に
コピーすると互いに整合しない瞬間を掴みうる。SQLite に整合した 1 ファイルを
作らせる。

**素朴なコピーは「壊れる」のではなく「黙って別の過去に戻る」。** 実機で測った
（2026-08-21。WAL が 535 KB、本体が 421 KB という状態）。

| | `.backup` | 本体だけを `cp` | 生きている DB |
| --- | --- | --- | --- |
| `media_file` | 32 | **31** | 32 |
| `merge_group` | 3 | **4** | 3 |
| `job` | 11 | **10** | 11 |
| `PRAGMA integrity_check` | ok | **ok** | —— |
| ファイルサイズ | 421,888 | **421,888** | —— |

**検査もサイズも同じ**なので、取り違えても気づけない。中身は「派生物が 1 つ
足りず、**消したはずのグループが 1 つ生き返り**、ジョブが 1 つ足りない」状態で、
これで戻すと消したはずの候補が復活する。

```bash
sqlite3 /path/to/data/var/mediaferry.sqlite3 ".backup /path/to/backup/mediaferry.sqlite3"
```

`VACUUM INTO` でもよい（こちらは同時に断片化も解消する）。

```bash
sqlite3 /path/to/data/var/mediaferry.sqlite3 "VACUUM INTO '/path/to/backup/mediaferry.sqlite3'"
```

出力は **0600 で保存する**。転送先 API キーの暗号文を含む。

```bash
chmod 600 /path/to/backup/mediaferry.sqlite3
```

**`DATA_ROOT` の中（`var/` など）に置かない。** アプリが `/data` として読み書き
できる領域なので、アプリが侵害されたときにバックアップごと触れる。

## マスター鍵の置き場所

`MEDIAFERRY_SECRET_KEY` を **DB のバックアップと同じ場所に置かない**。同じ場所に
置くと、暗号文と復号鍵が 1 つのバックアップに揃い、§12.3 の境界（「`DATA_ROOT`
のバックアップ単体の流出には効く」）が消える。

鍵は TrueNAS のアプリ設定（環境変数）にある。**TrueNAS のシステム設定
バックアップには含まれうる**ので、そちらと DB のバックアップを同じ搬出先へ
まとめない。

## スナップショットとの関係

TrueNAS のスナップショットは、DB ファイルの整合を保証しない（スナップショット
時点の WAL とページの組み合わせが復元可能とは限らない）。

したがって **`.backup` を定期実行し、その出力をスナップショット対象の
データセットへ置く**。スナップショットが守るのは「整合の取れた 1 ファイル」に
なる。

## リストア

1. アプリを停止する
2. `var/mediaferry.sqlite3` を戻す
3. **`-wal` と `-shm` を消す。** 戻さないだけでは足りない —— 残すと「古い DB に
   新しい WAL」の組み合わせになる
4. **所有者と権限を戻す。** root で `cp` すると `root:root` になり、非 root の
   アプリが書けなくなる（`chown <app-uid>:<app-gid>` と `chmod 600`）
5. アプリを起動する

```bash
docker stop <app のコンテナ>
cp /path/to/backup/mediaferry.sqlite3 <dataset>/var/mediaferry.sqlite3
rm -f <dataset>/var/mediaferry.sqlite3-wal <dataset>/var/mediaferry.sqlite3-shm
chown <app-uid>:<app-gid> <dataset>/var/mediaferry.sqlite3
chmod 600 <dataset>/var/mediaferry.sqlite3
docker start <app のコンテナ>
```

起動時の reconciliation が、ファイルと DB の齟齬を回収する。

- `writing` の staging は破棄される
- `staged` の staging は永続化済みの情報だけで公開が完遂する
- ライブラリにあって DB に無いファイルは**孤立として報告される。削除はしない**
- DB にあって実体が無いファイルは `missing_at` が立つ。実体が戻れば次回の
  起動で解除される

バックアップ時点より後に取り込んだファイルは、この孤立の一覧に出る。取り込み
直すか、そのまま置いておくかはユーザが決める。

**回収の結果は起動ログに 1 行出る。** 実機で確かめたときの出力（2026-08-21）。

```
起動時の回収: {'missing': 1, 'cleaned_dirs': 2}
孤立 1 件、自動で回収できない staging 0 件。画面で判断が要る
```

退避したファイルを戻して起動し直すと解除される。

```
起動時の回収: {'restored': 1}
```

**孤立は 1 件のまま残る**（実体を消さないので）。

## 関連

- [`design.md`](design.md) §12.3（マスター鍵の境界）
- [`design.md`](design.md) §9.6（起動時の齟齬回収）
- [`history/hardware-checklist.md`](history/hardware-checklist.md)（実機での確認の記録）
