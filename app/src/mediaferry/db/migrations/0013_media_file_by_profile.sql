-- 再計算のページ送りを、プロファイルの中だけで進めるための索引。
--
-- `0012` は相関副問い合わせ（`source_entry` / `merge_group`）の側だけを直した。
-- 外側の keyset は `rel_path` の UNIQUE 索引で駆動されるので、`LIMIT` は
-- **返す件数しか縛らない** —— 別プロファイルの大きなライブラリがあると、
-- 1 ページ読むだけでその全行を走査する（`original` は `rel_path` の並び上、
-- `derived/` も先に通る）。**`fetch` の最中は heartbeat もキャンセル観測も
-- 無い**ので、そこでリース窓を超える。
--
-- 列の順が意味を持つ。`profile_id, role` で絞ってから `rel_path` の順に読めるので、
-- keyset の `> ?` がそのまま索引の探索になり、並べ替えも要らなくなる。
CREATE INDEX media_file_by_profile
    ON media_file (profile_id, role, rel_path);
