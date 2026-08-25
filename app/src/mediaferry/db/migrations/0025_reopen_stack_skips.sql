-- RAW+JPEG の組の判定から撮影時刻を外したので、既存の見送りを未評価へ戻す
-- （`docs/decisions.md` の「撮影時刻を組の条件にしない」）。
--
-- **リビジョンの公開では戻らない。** `db/profiles.py` の `_publish_revision` は
-- 前後の定義を `StackRule` へパースしてから比べる。`tolerance_seconds` は
-- dataclass に無く、パーサも鍵を許すだけで読まないので、この鍵を持つ旧 JSON と
-- 持たない新 JSON は**同じ値にパースされて差が出ない**。変わったのは定義ではなく
-- 規則の読み方（コード）なので、定義の比較には現れない。
--
-- **他の戻し口も届かない。** `retry` は `failed` だけ、`requeue` はリモートから
-- 消えた `complete` だけ、`jobs/recompute.py` の `_reopen_stack` はその実行で
-- `captured_at` が動いた行だけを開き直す。**両方が見送りの組**はどれにも当たらない。
--
-- **既に組んだものには触らない。** `stacked` は「その `remote_asset_id` を送った
-- 結果」（`0016`）なので、未評価へ戻すと同じ組をもう一度作りに行く。
-- `remote_stack_id IS NULL` は保険 —— `0015` の trigger が
-- 「`skipped` なら `remote_stack_id` は NULL」を強制しているので、この条件が
-- 落とす行は現れない。**未評価の行にも触らない**（同じ値でも `updated_at` が動く）。
--
-- mediaferry_now は migrate.py が接続へ登録する。
UPDATE upload_record
   SET stack_state = NULL,
       stack_reason = NULL,
       updated_at = mediaferry_now()
 WHERE stack_state = 'skipped'
   AND remote_stack_id IS NULL;
