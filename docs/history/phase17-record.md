# Phase 17 の記録 —— 撮影地のタイムゾーンの上書き

設計は [`phase17-design.md`](phase17-design.md)、計画は [`phase17-plan.md`](phase17-plan.md)。
このセッションで 6 タスクを順に実装し（Native 実行）、最後にブランチ全体を 1 回だけ
別の目でレビューした。

## 計画から外れた判断

| Task | 判断 | 理由 |
| --- | --- | --- |
| 3 | 既存テスト `test_the_requeue_shares_the_transaction_with_the_update` の差し替え先を `Recomputer._requeue` から `mediaferry.jobs.recompute.requeue_sent` へ | 差し戻しを関数へ移したため。確かめていること（差し戻しが落ちたら値も戻る）は変わらない |
| 4 | **上書きを記録しただけで値が動かない行は、`changed` に数えるが差し戻さない** | 相手に書いた日時との差が生じないので、送り直しは何も直さない |
| 4 | 結合出力の探し方を「先頭の active member が動いたもの」から「どれかの active member が動いたもの」へ | 先頭に絞る条件は、`_inherit` が先頭から読み直すので結果を変えず、変異試験で検出できない重複だった。条件ごと外して単純にした |
| 4 | 計画に無いテストを足した（既定ゾーンへの戻り、記録のみの行、後続 member、結合出力の差し戻しと非差し戻し、見送りの戻し、空の `ids`、一覧の列、不正な本文 4 種） | 変異試験で抜けが見えたため |
| 5 | ダイアログの名前を「撮影地のタイムゾーンを付け替える」に | 入力欄のラベル「撮影地のタイムゾーン」と同じ名前だと、読み上げでもテストでも区別できない |
| 5 | 失敗したらダイアログを閉じて写真の画面のエラー帯に出し、選択は残す | 計画は失敗の出し先を決めていなかった。選び直させないことを優先した |

## 最後のレビューで直したもの

ブランチ全体を別の目（Opus）で 1 回レビューした。Critical 0、Important 3、Minor 8。
Important はどれも設計が扱っていなかった経路で、3 つとも直した。

| 指摘 | 起きること | 直し方 | テスト |
| --- | --- | --- | --- |
| I1 再計算が `none` の値に上書きを載せる | カメラの種類を `timezone_policy: none` に変えて再計算すると、UTC の札を貼った壁時計を撮影地へ直し、**日付ごと 7 時間ずれる**。`captured_at_revision_id` が `none` の版を指すので、API からも外せない | `none` の版で解き直したときは上書きを付けず、列も消す | `test_recompute_drops_the_zone_override_when_the_profile_stops_resolving_zones` |
| I2 送信中の記録を付け替える | 送信ジョブは読んだ日時を Immich へ書いて `complete` で終える。差し戻しは `complete` にしか当たらないので、**古い日時のまま誰も送り直さない** | 掴まれている記録（`checking`〜`fixing_datetime`）があれば 409 | `test_a_record_held_by_an_upload_refuses_the_request` ほか |
| I3 結合中の member を付け替える | 出力は読み終えた先頭の日時で公開され、まだ `output_media_file_id` が無いので継ぎ直しからも見えない | `merging` のグループの active member があれば 409 | `test_a_member_being_merged_refuses_the_request` |

**409 の判定は、先頭以外の member だけが動いた出力も含める。** その出力の値は動かない
ので厳密には止めなくてよいが、送信中の出力に当たるのは稀で、判定を細かくするより
単純に保つ方を採った。

**Minor のうち 2 件は影響で見て格上げし、直した。** 入力欄が 44px に満たず iOS で
画面が拡大される件（M3）と、360px でダイアログのボタンが 1 行に詰まって字が切れる件
（M4）。スマホから開けば必ず当たる。`.field` を付け、ダイアログの入力欄だけ 16px にし、
`.dialog-actions` を折り返すようにした。E2E に「いちばん狭い画面でも欠けずに押せる」を
足し、3 つの直しをそれぞれ戻すと落ちることを確かめた。

見送った Minor（利用者の判断を待つ）: 選択に古い結合出力が混ざると全体が 400 になる、
版ごとの定義の読み直しの重複、くわしくに元のゾーンを書いていない、`datalist` の候補が
約 600 件で iOS で扱いにくい、`Etc/GMT+7` などの紛らわしい名前も候補に出る、DST の
注記が付け替え後も残る。

## 変異試験

`PYTHONDONTWRITEBYTECODE=1` を付け、控えを取ってから 1 つずつ壊した。

| Task | 当てた数 | 初回に生き残った数 | 対処 |
| --- | --- | --- | --- |
| 2 `with_zone_override` | 4 | 0 | —— |
| 3 再計算と差し戻し | 3 | 0 | —— |
| 4 `apply_zone_override` | 14 | 3 | 下 |
| 5 画面 | 7 | 0 | —— |
| レビューの修正 | 7 | 1 | 変異の当て先が別の 409（`delete_media`）だった。当て直して検出 |
| 画面の直し（E2E） | 3 | 0 | —— |

Task 4 で生き残った 3 件:

- **上書きを外すときにプロファイルの `timezone` を見ない変異** —— テストのプロファイルの
  ゾーンと既定のゾーンがどちらも `Asia/Tokyo` で、区別が付かなかった。既定を `UTC` に
  してテストを直した
- **値が動かなくても差し戻す変異** —— それを見るテストが無かった。
  `test_recording_without_moving_does_not_requeue` を足した
- **結合出力を先頭の member に絞る条件を外す変異** —— 構造的に検出できない（上の表）。
  条件そのものを外した

## 受け入れ

- `uv run pytest`: 全件緑（Task 4 の時点で 1842 件）
- `uv run ruff check .` / `uv run ruff format --check .`: 緑
- `npm --prefix web test`: 696 件緑。lint・typecheck・build も緑
- `npm --prefix web run test:e2e`: 22 件緑（単独で流した）

## 実機で確かめること

- 2026-09-19〜22 の DJI の動画を写真の画面で選び、`Asia/Ho_Chi_Minh` に付け替える
- `GET /api/media` で該当行が `+07:00` になり、瞬間が変わっていないこと
- Immich へ送ると現地の時刻で表示されること
