-- **破棄したグループは構成ファイルを手放す。**
--
-- `merge_member.active` は `0003` で `merge_group.superseded_by_id` の写しとして
-- 定義した。そのため `discard`（`status = 'skipped'`）では member が active の
-- まま残り、次の 3 つがすべて塞がっていた。
--
--   * 再検出 —— active な member は境界扱いなので、別の構成を組めない
--   * `supersede`（§13 の組み直し）—— 相手側の member が active なので必ず衝突する
--   * `create_manual` —— 同じ検査で断られる
--
-- **2 つのグループを 1 つにまとめる経路が実質存在しなかった。** 実機で、16 GiB
-- 分割の 5 パートが継ぎ目の丸め誤差で 2 つに割れて検出され、それを組み直せずに
-- 詰まって分かった。
--
-- 写し元を「生きているグループ」= `superseded_by_id IS NULL AND status <> 'skipped'`
-- へ変える。**解放しても、捨てたのと同じ構成が作り直されることはない** ——
-- `save_detected` は同じ `input_digest` で `superseded_by_id IS NULL` の行があれば
-- 作らないので、破棄したグループ自身が番人になる。
--
-- **`0003` は書き換えない。** 適用済みの版を書き換えると、前の版で作った DB が
-- 開けなくなる（§7 に「以後この手は使わない」と記録がある）。

DROP TRIGGER merge_member_insert_matches_parent;
DROP TRIGGER merge_member_update_matches_parent;

-- 既存の DB を埋め戻す。**trigger を作る前に行う** —— 新しい trigger は
-- 「active は生きているグループの写し」を強制するので、順序を逆にしても通るが、
-- 古い行を先に片付けておく方が意図が読める。
UPDATE merge_member SET active = 0
 WHERE merge_group_id IN (SELECT id FROM merge_group WHERE status = 'skipped');

CREATE TRIGGER merge_member_insert_matches_parent
BEFORE INSERT ON merge_member
WHEN NEW.active <> (
    SELECT superseded_by_id IS NULL AND status <> 'skipped'
    FROM merge_group WHERE id = NEW.merge_group_id
)
BEGIN
    SELECT RAISE(ABORT, 'member active flag must match the group live state');
END;

CREATE TRIGGER merge_member_update_matches_parent
BEFORE UPDATE OF merge_group_id, active ON merge_member
WHEN NEW.active <> (
    SELECT superseded_by_id IS NULL AND status <> 'skipped'
    FROM merge_group WHERE id = NEW.merge_group_id
)
BEGIN
    SELECT RAISE(ABORT, 'member active flag must match the group live state');
END;

CREATE TRIGGER merge_group_discard_deactivates_members
AFTER UPDATE OF status ON merge_group
WHEN NEW.status = 'skipped' AND OLD.status <> 'skipped'
BEGIN
    UPDATE merge_member SET active = 0 WHERE merge_group_id = NEW.id;
END;

-- 破棄は不可逆。戻せると active と親の状態が乖離し、旧 member が復活して
-- 現グループの候補判定を壊す（`merge_group_supersede_is_final` と同じ理由）。
CREATE TRIGGER merge_group_discard_is_final
BEFORE UPDATE OF status ON merge_group
WHEN OLD.status = 'skipped' AND NEW.status <> 'skipped'
BEGIN
    SELECT RAISE(ABORT, 'discard is irreversible');
END;
