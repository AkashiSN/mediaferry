-- カードを見つけたら数える（§12.1）。`auto_import_at` と同じ形の印で、
-- presence ごとに 1 回だけ `scan` を積むためのもの。
--
-- **信頼の有無にも AUTO_IMPORT にもよらない。** §12.1 の「スキャン結果を
-- 画面に出すところで止まり、ユーザの承認を待つ」は、数え終わっていることを
-- 前提にしている。
ALTER TABLE volume_presence ADD COLUMN auto_scan_at TEXT;
