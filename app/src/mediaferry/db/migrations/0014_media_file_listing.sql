-- プロファイルで絞ったライブラリ一覧のための索引（§11 / §13）。
--
-- `0013` は再計算のページ送りには正しく効くが、一覧
-- （`m.profile_id IN (...)` + `ORDER BY m.captured_at DESC, m.id DESC` + `LIMIT 50`）
-- にも選ばれてしまい、**そのプロファイルの全行を拾ってから並べ替える**。
-- `media_file_captured_at` を辿れば先頭ページで止まれた経路なので、
-- プロファイルが大半を占める通常の構成ほど悪化する。
--
-- 並びと同じ向きで持つ。tie-break の `id DESC` まで索引に入れておくと、
-- 一時 B-tree が要らなくなる。
CREATE INDEX media_file_listing
    ON media_file (profile_id, captured_at DESC, id DESC);
