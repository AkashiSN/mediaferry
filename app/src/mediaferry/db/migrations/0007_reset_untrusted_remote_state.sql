-- 検査より前に保存された「相手由来の値」を、値を見ずに一掃する（§12.3 / §14）。
--
-- **形からは素性が分からない。** 0005 は「64 文字の 16 進なら指紋」、0006 は
-- 「識別子の形なら安全」と値の形で判定していたが、どちらも同じ形の秘密を
-- 素通りさせる（`test-api-key` は識別子の形をしているし、64 hex の API キーは
-- ありうる）。値自身の接頭辞も出所にはならない —— API キーを発行するのは相手で、
-- `sha256:` で始まる鍵を選べる（SQLite の LIKE は ASCII の大小文字を区別しない
-- ので `SHA256:` も同じ）。**攻撃者が選べる材料で信用を決めない。**
--
-- 信用できるのは版そのもの（この版より前に書かれた行、という cohort）だけなので、
-- 相手由来の列を値に関係なく捨てる。
--
-- destination_revision: remote_user_id と server_instance_id を NULL にする。
--   preflight は記録が無ければ「向き先の記録が無い。接続を検証し直す」で**閉じる**
--   （送信は始まらない）。宛先を保存し直せば、新しいリビジョンに今の観測が入る。
--   ここで値を作り直さないのは、0005 で既に指紋化された行と、指紋の形をした生値を
--   区別できないため（作り直すと前者が二重指紋になり、同じく閉じる側に倒れる。
--   どちらにせよ再検証が要るなら、理由が読める NULL にする）。
--   リビジョンは不変（UPDATE を trigger が拒む）ので、外して変換し作り直す。
--
-- upload_record: 相手由来の観測を 3 列まとめて捨てる。remote_is_trashed だけ
--   残すと、「どの資産の、いつの観測か分からないゴミ箱状態」が一覧に出る。
--   complete は再確認（チェックサム照合）で識別子も状態も戻る。remote_checked_at
--   を残さないのは、「リモートに存在しないと確認済み」と同じ形になって、実際には
--   在る資産を requeue で送り直せてしまうため。
--
-- awaiting_datetime_approval は自力で直らない（承認の押し先を指せない）ので、
-- 理由を付けて止める。mediaferry_now は migrate.py が接続へ登録する。

DROP TRIGGER destination_revision_no_update;

UPDATE destination_revision
   SET remote_user_id = NULL,
       server_instance_id = NULL
 WHERE remote_user_id IS NOT NULL
    OR server_instance_id IS NOT NULL;

CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;

UPDATE upload_record
   SET remote_asset_id = NULL,
       remote_checked_at = NULL,
       remote_is_trashed = NULL,
       last_error = '検査より前に保存された相手由来の値を捨てた。再確認で戻る（0007）',
       updated_at = mediaferry_now()
 WHERE remote_asset_id IS NOT NULL
    OR remote_checked_at IS NOT NULL
    OR remote_is_trashed IS NOT NULL;

UPDATE upload_record
   SET invalidated_at = COALESCE(invalidated_at, mediaferry_now()),
       invalidated_reason = COALESCE(invalidated_reason,
           '検査より前に保存された識別子を捨てた。承認する資産を指せない（0007）')
 WHERE state = 'awaiting_datetime_approval'
   AND remote_asset_id IS NULL
   AND last_error = '検査より前に保存された相手由来の値を捨てた。再確認で戻る（0007）';
