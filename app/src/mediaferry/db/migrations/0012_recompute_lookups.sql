-- 再計算の対象抽出のための索引（§6 の `recompute_timestamps`）。
--
-- 抽出は `media_file` 1 行ごとに相関副問い合わせを回す。索引が無いと
-- `source_entry` を毎行 SCAN し、並べ替えに一時 B-tree を作るので、数万件の
-- ライブラリでは**最初の `assert_lease` に届く前に 60 秒を超え**、正常なジョブが
-- リース切れで落ちる（キャンセルもその間は観測されない）。

-- 列の順が意味を持つ。`media_file_id` で引いてから `observed_at, id` の順で
-- 読めるので、一時 B-tree が要らなくなる。**`state` を間に挟まない**
-- （挟むと並べ替えに使えなくなる）。
CREATE INDEX source_entry_by_media
    ON source_entry (media_file_id, observed_at, id);

-- 外部キーには索引が自動では付かない。派生物の抽出が
-- `merge_group.output_media_file_id = m.id` で引く。
CREATE INDEX merge_group_by_output
    ON merge_group (output_media_file_id);
