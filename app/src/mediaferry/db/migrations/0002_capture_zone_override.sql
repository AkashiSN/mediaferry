-- 撮影地のタイムゾーンの上書き。
-- NULL は上書きなし。値は IANA のゾーン名で、正しさは API が検める
-- （SQL ではゾーン名を判定できない）。`derived` の行では常に NULL
-- （結合した動画は先頭の active member から継ぐ）。
ALTER TABLE media_file ADD COLUMN captured_at_zone_override TEXT;
