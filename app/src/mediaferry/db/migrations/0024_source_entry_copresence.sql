-- 0024_source_entry_copresence.sql
--
-- 組の身元に「同席の証拠」を足す（docs/history/phase10-design.md）。
--
-- **印は <scan の job_id>:<stem prefix>。** スキャンの id だけにすると、1 回の
-- スキャンが書いた別々の組が同じ印になり、一覧が無関係な写真を 1 タイルに畳む。
-- prefix を含めることで、印の等しさが「同じスキャンで、同じ stem の下で、同時に
-- 見えた」をそのまま表す。SQL 側で rel_path から stem を切り出さずに済む。
--
-- **既存の行は NULL のまま。** 過去に同席したかどうかは記録に無いので、埋められ
-- ない。既存のライブラリは次にそのカードをスキャンしたときに印が付く。
ALTER TABLE source_entry ADD COLUMN copresent_key TEXT;

-- 「自分より順位が上の相方が居るか」を SQL で見るのに要る。rel_path から SQL で
-- 拡張子を切り出すと読めない式になる。**書くのはスキャンで、既存の行は NULL。**
ALTER TABLE source_entry ADD COLUMN extension TEXT;

-- 一覧の従外しが引く経路（同じカードの、同じ印の、別のメディア）。
CREATE INDEX source_entry_copresent
    ON source_entry (volume_instance_id, copresent_key)
    WHERE copresent_key IS NOT NULL;
