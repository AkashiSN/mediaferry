-- 「つないだ動画」だけの一覧のための索引（§11 / §13）。
--
-- `GET /media?role=derived` は `0014`（`profile_id, captured_at DESC, id DESC`）の
-- 経路から外れる —— `role` はその索引に含まれないので、SQLite は並びの索引
-- （`media_file_captured_at`）で `captured_at DESC` を辿りながら 1 行ずつ `role` を
-- 確かめる。`derived` は行数が少ないので、`LIMIT` を満たすまでの走査量が読めない。
--
-- 実測（original 60,000 行 / derived 200 行、captured_at をばらつかせた状態）:
-- 索引が無いと 55〜66 ms（`captured_at` 側の並び索引をほぼ全走査）、
-- `(role, captured_at DESC, id DESC)` を置くと 0.1 ms 未満に落ちる。
-- 並びと同じ向きで持ち、tie-break の `id DESC` まで索引に入れておくと、
-- 一時 B-tree が要らなくなる（`0014` と同じ形）。
CREATE INDEX media_file_by_role
    ON media_file (role, captured_at DESC, id DESC);
