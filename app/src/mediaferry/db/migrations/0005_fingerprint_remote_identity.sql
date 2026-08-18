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
--
-- **生値と指紋が混ざった DB がある。** 指紋化を入れたアプリは新しいリビジョンを
-- 指紋で保存する一方、この版が無ければ schema_migration は 4 のままなので、
-- 「古い行は生値・新しい行は指紋」という DB が正規に作れる。指紋をもう一度
-- ハッシュすると、観測値の一重指紋と永久に一致せず、その宛先が恒久的に拒否
-- される。
--
-- **見分けは形の推定ではなく接頭辞で行う**（`sha256:`）。64 文字の 16 進という
-- 形で判定すると、同じ形の API キーを相手が `users/me` の `id` に返していた
-- DB で「もう指紋だ」と誤認し、**鍵の平文がこの列と API 応答に残る**
-- —— この版が塞ごうとしている脅威そのものになる。接頭辞の無い値は、
-- 中身が何であれ観測値として扱って変換する。

DROP TRIGGER destination_revision_no_update;

UPDATE destination_revision
   SET remote_user_id = mediaferry_fingerprint(remote_user_id)
 WHERE remote_user_id IS NOT NULL
   AND remote_user_id NOT LIKE 'sha256:%';

UPDATE destination_revision
   SET server_instance_id = mediaferry_fingerprint(server_instance_id)
 WHERE server_instance_id IS NOT NULL
   AND server_instance_id NOT LIKE 'sha256:%';

CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;
