-- 向き先の観測値を指紋（SHA-256）へ変換する。
--
-- 相手は「こちらが読む値」を選べる。侵害された Immich は、受け取った
-- x-api-key を GET /api/users/me の id として返せる。生値を保存すると、
-- 暗号化したはずの鍵の平文の複製が、この列と API 応答に現れる。
-- 用途は「同じ向き先を指し続けているか」の等値比較だけなので、指紋で足りる。
--
-- destination_revision は不変（UPDATE を trigger が拒む）。移行はスキーマを
-- 変える窓なので、trigger を外して変換し、同じ本体で作り直す。
-- mediaferry_fingerprint は migrate.py が接続へ登録する（SQLite に SHA-256 は無い）。

DROP TRIGGER destination_revision_no_update;

UPDATE destination_revision
   SET remote_user_id = mediaferry_fingerprint(remote_user_id)
 WHERE remote_user_id IS NOT NULL;

UPDATE destination_revision
   SET server_instance_id = mediaferry_fingerprint(server_instance_id)
 WHERE server_instance_id IS NOT NULL;

CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;
