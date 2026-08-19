-- Phase 6: RAW/JPEG のスタッキング（§9.11）。
-- **状態機械には状態を足さない。** スタックは「その宛先へその資産を送った結果」
-- なので、`remote_asset_id` と同じ層に置く。

ALTER TABLE upload_record ADD COLUMN stack_state TEXT
    CHECK (stack_state IN ('stacked', 'skipped'));
ALTER TABLE upload_record ADD COLUMN remote_stack_id TEXT;
ALTER TABLE upload_record ADD COLUMN stack_reason TEXT;

-- `ALTER TABLE` では表制約を足せないので、3 列の組み合わせは trigger で守る
-- （`0011` の `captured_at_revision_id` と同じ形）。INSERT と UPDATE の両方に
-- 置く。**片側だけだと抜け道になる。**
--
-- **比較は `IS` で書く（`=` ではない）。** `stack_state` が NULL のとき
-- `NEW.stack_state = 'stacked'` は NULL を返し、`偽 OR NULL` は NULL、
-- `NOT NULL` も NULL になるので **WHEN が成立せず trigger が黙って素通りする。**
-- 「未評価へ戻すのに理由が残っている」がそのまま通ってしまう。
--
-- **`state = 'complete'` は条件に入れない。** 再計算の差し戻し（`_requeue`）が
-- `complete` → `needs_recheck` を動かすので、入れると正当な差し戻しが ABORT する。
-- スタック済みという事実は、レコードが再確認へ戻っても真のままである。
CREATE TRIGGER upload_record_stack_shape_insert
AFTER INSERT ON upload_record
WHEN NOT (
       (NEW.stack_state IS NULL AND NEW.remote_stack_id IS NULL AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state IS 'stacked' AND NEW.remote_stack_id IS NOT NULL
        AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state IS 'skipped' AND NEW.stack_reason IS NOT NULL
        AND NEW.remote_stack_id IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'stack_state と remote_stack_id / stack_reason の組が不正');
END;

CREATE TRIGGER upload_record_stack_shape_update
AFTER UPDATE OF stack_state, remote_stack_id, stack_reason ON upload_record
WHEN NOT (
       (NEW.stack_state IS NULL AND NEW.remote_stack_id IS NULL AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state IS 'stacked' AND NEW.remote_stack_id IS NOT NULL
        AND NEW.stack_reason IS NULL)
    OR (NEW.stack_state IS 'skipped' AND NEW.stack_reason IS NOT NULL
        AND NEW.remote_stack_id IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'stack_state と remote_stack_id / stack_reason の組が不正');
END;

-- 第 2 パスの抽出の駆動索引。**述語は問い合わせ側と一字一句そろえる**
-- （部分索引は述語が一致しないと使われない）。
--
-- **`target_epoch` を鍵に入れる。** 向き先を変えた宛先では旧 epoch の `complete` が
-- 監査履歴として残る（`_invalidate_old_epoch_locked` は `state <> 'complete'` だけを
-- 無効化する）ので、epoch で絞らないと**別ライブラリへ送った資産 ID を現行の
-- 資格情報で送る**ことになる。
CREATE INDEX upload_record_unstacked ON upload_record (destination_id, target_epoch, id)
    WHERE stack_state IS NULL AND state = 'complete' AND invalidated_at IS NULL;

-- 効かないまま 3 フェーズ残っていた設定行を消す（§21）。env に残っていても未知の
-- キーは読まれないので、起動は壊れない。
DELETE FROM app_setting WHERE key = 'UPLOAD_CONCURRENCY';
