-- 自動取り込み（AUTO_IMPORT=trusted）を効かせるための 2 列。§12.1。

-- 自動取り込みを積んだ接続に印を付ける。列は volume_instance ではなく
-- volume_presence に置く。「このカードを取り込んだか」ではなく「この接続に
-- ついて積んだか」を憶えるためで、抜き挿しすればまた積まれる。
--
-- 積むことと印を付けることは 1 つの BEGIN IMMEDIATE に入れる。SQLite に行ロック
-- は無いので、条件付き UPDATE（CAS）でこの印を取れた側だけが実行者になる。
ALTER TABLE volume_presence ADD COLUMN auto_import_at TEXT;

-- 判定が「暫定マッチ」だったか（§6 の「対象だが中身が無い」）。
--
-- watcher は「積んでよいか」を毎 tick DB の現在値から組み直す。信頼登録は
-- trusted_at を UPDATE するだけで mountd の観測は動かないので、直前の
-- VolumeView を見ていると、カードを挿したまま承認しても再評価されない。
-- identity_confidence は既に volume_instance にあるが provisional は
-- VolumeView にしか無く、1 つでも欠けると組み直しが成立しない。
ALTER TABLE volume_instance ADD COLUMN provisional INTEGER NOT NULL DEFAULT 0
    CHECK (provisional IN (0, 1));
