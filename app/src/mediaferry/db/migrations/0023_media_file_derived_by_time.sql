-- `0022` の全体索引を、role='derived' だけの部分索引に差し替える。
--
-- `0022` の `(role, captured_at DESC, id DESC)` は role='original' 側にも使える
-- 形だったため、`db/selection.py` の `SENDABLE_CLAUSE`
-- （`(role = 'original' AND ...) OR (role = 'derived' AND ...)`）の**両方の枝**で
-- 索引が使えるようになった。SQLite はこれを見つけると MULTI-INDEX OR を選ぶが、
-- OR の結果は `captured_at` の並び順に出ないので最後に全件ソートが要る。
-- role='original' が全体の大半を占めるため、`GET /media?status=unsent&…`
-- （実測: original 60,000 行 / derived 200 行、`0022` 追加前は中央値 0.58 ms）が
-- 中央値 74 ms まで悪化した。
--
-- role='derived' だけを持つ部分索引にすると、role='original' の枝には索引が
-- 無くなるので MULTI-INDEX OR の対象から外れ、既存の経路（`media_file_captured_at`
-- を辿って絞り込む）に戻る。`GET /media?role=derived` は変わらず高速に保てる
-- （実測: 索引無しでは中央値 56.7 ms、この部分索引では中央値 0.30 ms）。
--
-- **`0022` は適用済みの版なので書き換えない。** ここで DROP してから作り直す。
DROP INDEX media_file_by_role;

CREATE INDEX media_file_derived_listing
    ON media_file (captured_at DESC, id DESC) WHERE role = 'derived';
