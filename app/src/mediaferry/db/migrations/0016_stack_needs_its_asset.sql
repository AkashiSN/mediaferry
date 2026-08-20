-- スタックは「その `remote_asset_id` を送った結果」（§9.11）。
-- 資産 ID が消えたり別の値に変わったら、その結果はもう現在の姿を表さない。
--
-- 書き手（`stamp_many` / `stamp_remote` / retry / requeue）は 3 列を一緒に
-- 未評価へ戻すが、**将来の消し忘れを fail-closed にする**ために trigger でも守る。
--
-- **`0015` は書き換えない。** 適用済みの版を書き換えると、前の版で作った DB が
-- 開けなくなる（§7 に「以後この手は使わない」と記録がある）。
--
-- 見送り（`skipped`）は「送らなかった」記録なので、資産 ID とは独立に残ってよい。
CREATE TRIGGER upload_record_stacked_needs_its_asset
AFTER UPDATE OF stack_state, remote_stack_id, remote_asset_id ON upload_record
WHEN NEW.stack_state IS 'stacked' AND NEW.remote_asset_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'stacked のまま remote_asset_id を捨てられない');
END;

CREATE TRIGGER upload_record_stacked_needs_its_asset_insert
AFTER INSERT ON upload_record
WHEN NEW.stack_state IS 'stacked' AND NEW.remote_asset_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'stacked のまま remote_asset_id を捨てられない');
END;
