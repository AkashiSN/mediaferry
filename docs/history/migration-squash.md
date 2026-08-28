# 移行を 1 本へ畳んだ記録（2026-08-29）

**配布前に、出荷済みの移行 27 本を `0001_initial.sql` 1 本へ畳んだ。**
判断そのものは [`../decisions.md`](../decisions.md) の「データモデルとマイグレーション」に
ある。ここには、**畳む前の 27 本が何だったか**と、**同じことをもう一度やるなら
どう確かめるか**を残す。

## なぜ畳めたか

27 本の内訳を数えると、**新規 DB に効くのは最終形の DDL だけ**だった。

| 種類 | 版 | 新規 DB への効き |
| --- | --- | --- |
| 表・索引・trigger を作る | `0001`〜`0004`, `0008`〜`0010`, `0012`〜`0016`, `0019`〜`0024` | **最終形に残る** |
| 既存 DB の行を直す一度きりのデータ移行 | `0005`（指紋化）, `0006`（識別子の洗い）, `0007`（相手由来の観測の破棄）, `0011` の埋め戻し, `0017`, `0018`, `0021` の埋め戻し, `0025` | 新規 DB では **0 行に当たる** |
| 表を作り直して前の形を置き換える | `0026`（`media_file`）, `0027`（`upload_record`） | **後の形だけが残る** |

**まだ誰にも配っていない**ので、中間形を運ぶ相手が居ない。実機の DB は作り直した
（利用者の判断）。

## 何を確かめたか

畳んだ形が元と同じであることを、**削除の前に 1 回だけ**、使い捨てのスクリプトで
確かめた（`27 本を順に当てた DB` と `0001_initial.sql だけを当てた DB` を作って比較）。

1. `sqlite_master` の全オブジェクト（表・索引・trigger）を**正規化して**突き合わせる。
   正規化は「`--` コメントを落とす／空白を潰す／`"` を外す」の 3 つだけ。
   **これで整形とコメントの書き直しは許しつつ、意味の変化は捕まえられる** ——
   `ALTER TABLE RENAME` で作り直した表は `sqlite_master` に `"media_file"` のように
   引用符付きで残るので、外さないと差分になる。→ **62 件すべて一致**
2. 表ごとの `PRAGMA table_info` / `foreign_key_list` / `index_list`。
   **列順まで見る** —— `SELECT *` の意味が変わるので、読みやすさのために列を
   並べ替えてはいけない。→ **60 件すべて一致**
3. 両方で `PRAGMA integrity_check` と `PRAGMA foreign_key_check`。→ どちらも空

**列を並べ替えたくなる。** `ALTER TABLE ADD COLUMN` で足した列は表の末尾に付くので、
畳んだ形では `volume_instance.provisional` / `scanned_at` のように意味の近い列から
離れて並ぶ。**並べ替えると 2 の検査が落ちる。** 揃えたのは書式とコメントだけで、
順序は元のまま残した。

## 何を失ったか

**データ移行そのものを見ていたテスト 12 本を消した。** 対象の移行が無くなったので、
テストが立てる前提（「前の版で作った DB」）を作れない。

| 消したテスト | 見ていたもの |
| --- | --- |
| `test_an_existing_raw_remote_id_is_converted_to_a_fingerprint` / `test_a_value_that_is_already_a_fingerprint_is_not_hashed_again` | `0005` の指紋化 |
| `test_untrusted_remote_state_is_dropped_whatever_its_shape` | `0007` の cohort ごとの破棄 |
| `test_existing_rows_get_the_revision_they_were_imported_with` | `0011` の埋め戻し |
| `test_a_group_discarded_before_the_change_gives_its_files_back` | `0017` |
| `test_a_card_that_was_already_counted_stays_counted` | `0021` の埋め戻し |
| `test_progress_left_on_a_finished_job_is_cleared` | `0018` |
| `test_existing_skips_go_back_to_unevaluated` | `0025` |
| `test_rows_survive_the_media_file_rebuild` / `test_rows_survive_the_upload_record_rebuild` | `0026` / `0027` の作り直しが行を運ぶこと |
| `test_a_database_from_the_previous_release_still_opens` | 版の checksum の凍結（**名前を `test_a_shipped_migration_is_never_edited` に変えて残した**） |
| `test_the_dead_concurrency_setting_is_removed` | `0015` が消す設定行 |

**生きている不変条件は失っていない。** 指紋化の「生値を保存しない」は
`test_fingerprint.py` と `test_destination_repository.py` が書き込み経路で見ており、
移行のテストが見ていたのは**過去に書かれた行を直すこと**だった。

**runner の仕掛けは残した。** 出荷している 1 本は FK オフの目印を使わないが、
`-- mediaferry:foreign-keys-off` の経路とその 5 本のテストはそのまま置いてある ——
`PRAGMA foreign_keys` がトランザクション内で黙って無視される穴は、
**スカッシュしても消えない**（[`../decisions.md`](../decisions.md)）。

**runner から落としたもの:** `mediaferry_fingerprint` と `mediaferry_now` の
`create_function`。使っていたのは消えたデータ移行 4 本だけで、**最終形のスキーマは
どちらも参照していない**（`sqlite_master` を検索して確認）。データを作り替える版を
また足すなら、そのとき戻す。

## 版番号の名指しを 113 か所直した

コードとテストのコメントが `0012` のように**もう無いファイル**を名指ししていたので、
**スキーマオブジェクトの名前**へ書き換えた（`0012` → `source_entry_by_media`、
`0004` の CHECK → `upload_record` の CHECK、など）。`docs/design.md`・
`docs/decisions.md`・`docs/development.md` も同じ方針で直した。

**`docs/history/` は直していない。** どちらも
**日付の付いた記録**で、当時そのファイルが在ったことまで含めて事実である。

## 決着済みだが、対象が消えた判断

`decisions.md` から移した 3 行。**判断の一般形は
「出所は値自身の中身で決めない。信用できるのは版（cohort）だけ」として
`decisions.md` に残っている**ので、ここには具体の移行についての記録だけを置く。

| 判断 | 理由 |
| --- | --- |
| **既存 DB の観測値も移行で指紋へ変換する**（`0005`） | 変換しないと旧平文が残り、preflight が全宛先を「向き先が変わった」と誤判定する。`destination_revision` は不変なので、trigger を外して変換し作り直す。**SQLite に SHA-256 が無い**ので、runner が `mediaferry_fingerprint` を接続へ登録する |
| **変換済みかどうかは、形の推定ではなく値の接頭辞で見分ける**（`0005` / `sha256:`） | 指紋化を入れたアプリは新しい行を指紋で保存するが、移行が無ければ `schema_migration` は前の版のまま。**生値と指紋が混ざった DB が正規に作れる**ので、二重ハッシュを避ける必要がある。ただし「64 文字の 16 進なら指紋」と推定すると、**同じ形の API キーを相手が `users/me` の `id` に返していた DB で「もう指紋だ」と誤認し、鍵の平文がこの列と API 応答に残る**（この移行が塞ごうとしている脅威そのもの）。指紋であることを値自身に持たせ、接頭辞の無い値は中身が何であれ観測値として変換する |
| **保存済みの相手由来の識別子・観測は、値を見て選り分けずに cohort ごと落とす**（`0006` / `0007`） | 受け取る側の検査は新しく受け取る値にしか効かない。**形で選り分けると `test-api-key` のように unreserved だけでできた鍵が残る**し、SQLite の `length` と `GLOB` は埋め込み NUL で打ち切られるので NUL を挟んだ値もすり抜ける。SQL からは鍵を復号できないが、**版そのものが「検査を入れる前に書かれた行」という cohort を指せる**ので、値に関係なく一度外して再確認で戻す。`remote_asset_id` / `remote_checked_at` / `remote_is_trashed` は**まとめて**捨てる（片方だけ残すと「どの資産の、いつの観測か分からないゴミ箱状態」が一覧に出る。`complete` に `remote_checked_at` を残すと「リモートに存在しない」と同じ形になり、実際には在る資産を画面が「Immich にはもうありません」と言う）。`complete` は再確認で戻り、`awaiting_datetime_approval` は指す資産が無いので無効化する |
