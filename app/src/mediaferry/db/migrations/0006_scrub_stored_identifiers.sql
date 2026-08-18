-- 検査より前の版が保存した `remote_asset_id` を落とす（§12.3 / §14）。
--
-- 受け取る側の検査は、新しく受け取る値にしか効かない。侵害された Immich が
-- API キーを assetId として返していた DB を新しい版で開くと、その平文は
-- upload_record.remote_asset_id に残り、一覧の API 応答に出続ける。承認や
-- 再開の経路では、その値がそのまま次の要求の URL に入る。
--
-- **値を見て選り分けない。** SQL からは鍵を復号できないので「形が識別子か」しか
-- 見られないが、それでは `test-api-key` のように unreserved だけでできた鍵が
-- 残る。SQLite の length と GLOB は埋め込み NUL で打ち切られるので、NUL を挟んだ
-- 値も「1 文字・禁止文字なし」と評価されてすり抜ける。**版そのものが「検査を
-- 入れる前に書かれた行」という cohort を指せる**ので、値に関係なく一度外す。
-- 正しい識別子は再確認（チェックサムでの照合）で戻る。
--
-- **complete は remote_checked_at も外す。** 外さないと「リモートに存在しないと
-- 確認済み」（remote_asset_id IS NULL かつ remote_checked_at IS NOT NULL）と同じ
-- 形になり、実際には在る資産を requeue で送り直せてしまう。
--
-- **awaiting_datetime_approval は無効化する。** 承認は人の操作で、押した先で
-- 書き換える資産を指せない。自力で直らないので、理由を付けて止める。
-- mediaferry_now は migrate.py が接続へ登録する。

UPDATE upload_record
   SET remote_asset_id = NULL,
       remote_checked_at = NULL,
       last_error = '検査より前に保存された識別子なので外した。再確認で戻る（0006）',
       updated_at = mediaferry_now()
 WHERE remote_asset_id IS NOT NULL;

UPDATE upload_record
   SET invalidated_at = COALESCE(invalidated_at, mediaferry_now()),
       invalidated_reason = COALESCE(invalidated_reason,
           '検査より前に保存された識別子を外した。承認する資産を指せない（0006）')
 WHERE state = 'awaiting_datetime_approval'
   AND remote_asset_id IS NULL
   AND last_error = '検査より前に保存された識別子なので外した。再確認で戻る（0006）';
