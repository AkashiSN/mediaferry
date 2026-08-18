-- 検査の無かった版が保存した `remote_asset_id` を落とす（§12.3 / §14）。
--
-- 受け取る側の検査は、新しく受け取る値にしか効かない。侵害された Immich が
-- API キーを assetId として返していた DB を新しい版で開くと、その平文は
-- upload_record.remote_asset_id に残り、一覧の API 応答に出続ける。承認や
-- 再開の経路では、その値がそのまま次の要求の URL に入る。
--
-- 許す形は adapter と同じ（RFC 3986 の unreserved と長さの上限）。SQL 側では
-- 鍵との一致を見られない（暗号化されていて、ここでは復号できない）ので、
-- 符号化した鍵のように**形で分かるもの**をここで落とし、鍵そのものと同じ形の
-- 値は送る直前に adapter が弾く。
--
-- **complete は無効化しない。** 識別子を外した行は「リモートに存在しないと
-- 分かった complete」と同じ形になり、利用者が requeue して送り直せる。照合は
-- チェックサムで行うので、再確認だけでも正しい識別子に戻る。経緯は last_error
-- に残す。
--
-- **awaiting_datetime_approval は無効化する。** 承認は人の操作で、押した先で
-- 書き換える資産を指せない。自力で直らないので、理由を付けて止める。
-- mediaferry_now は migrate.py が接続へ登録する。

UPDATE upload_record
   SET remote_asset_id = NULL,
       last_error = '保存されていた識別子が検査を通らないので外した（0006）',
       updated_at = mediaferry_now()
 WHERE remote_asset_id IS NOT NULL
   AND (remote_asset_id GLOB '*[^A-Za-z0-9._~-]*' OR length(remote_asset_id) > 128);

UPDATE upload_record
   SET invalidated_at = COALESCE(invalidated_at, mediaferry_now()),
       invalidated_reason = COALESCE(invalidated_reason,
           '保存されていた識別子が検査を通らない。承認する資産を指せない（0006）')
 WHERE state = 'awaiting_datetime_approval'
   AND remote_asset_id IS NULL
   AND last_error = '保存されていた識別子が検査を通らないので外した（0006）';
