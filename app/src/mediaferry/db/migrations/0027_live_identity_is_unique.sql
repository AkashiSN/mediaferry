-- mediaferry:foreign-keys-off
-- 同一性の UNIQUE を、有効な行だけを見る部分索引へ付け替える（§2.3）。
--
-- 守りたい不変条件は「**有効な**送信記録は (宛先, epoch, メディア) 1 組につき
-- 高々 1 つ」であって、「行が 1 つ」ではない。消滅した記録を無効化して通常の
-- 「まだ送っていない」へ戻す形にすると、同じ組に無効化された行と新しい行が並ぶ。
-- `0004` の**表制約**の UNIQUE はそれを許さないので、送り直しが IntegrityError になる。
--
-- **作り直しが要るのは表制約を後から落とせないため。** upload_record は
-- upload_destination / media_file / merge_group / job / destination_revision を
-- 参照しているので、外部キーを外さないと入れ替えられない。runner が先頭行の
-- 目印を見て切り替え、COMMIT より前に PRAGMA foreign_key_check を確かめる。

CREATE TABLE upload_record_new (
    id                      TEXT PRIMARY KEY,
    destination_id          TEXT NOT NULL REFERENCES upload_destination(id) ON DELETE RESTRICT,
    target_epoch            INTEGER NOT NULL,
    media_file_id           TEXT NOT NULL REFERENCES media_file(id) ON DELETE RESTRICT,
    state                   TEXT NOT NULL CHECK (state IN (
                                'pending', 'checking', 'uploading', 'asset_known', 'tagging',
                                'fixing_datetime', 'awaiting_datetime_approval',
                                'complete', 'failed', 'needs_recheck')),
    -- 送信を許可した根拠。claim 時にどの条件で再評価するかを決める。
    selection_rule          TEXT NOT NULL CHECK (selection_rule IN (
                                'default', 'failed_group_member', 'adopted_derived')),
    origin                  TEXT NOT NULL CHECK (origin IN (
                                'created_by_us', 'pre_existing', 'unknown')),
    -- 初回 checking が reject なら「以前から存在した」ことを証明できる。
    -- accept だったことは自作の証明にならない。
    first_check_result      TEXT CHECK (first_check_result IN ('accept', 'reject')),
    remote_asset_id         TEXT,
    remote_is_trashed       INTEGER CHECK (remote_is_trashed IN (0, 1)),
    remote_checked_at       TEXT,
    checksum                TEXT,
    attempts                INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    eligibility_reason      TEXT,
    merge_group_id          TEXT REFERENCES merge_group(id) ON DELETE RESTRICT,
    claim_job_id            TEXT REFERENCES job(id) ON DELETE RESTRICT,
    claim_token             TEXT,
    claim_expires_at        TEXT,
    destination_revision_id TEXT,
    -- 状態機械とは直交するフラグ。state の列挙には混ぜない。
    invalidated_at          TEXT,
    invalidated_reason      TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    -- いつ時点の観測かは `remote_checked_at` が持つ（`0009`）。
    remote_datetime_original TEXT,
    -- スタックは「その宛先へその資産を送った結果」（`0015`）。
    stack_state             TEXT CHECK (stack_state IN ('stacked', 'skipped')),
    remote_stack_id         TEXT,
    stack_reason            TEXT,
    CHECK ((claim_job_id IS NULL AND claim_token IS NULL AND claim_expires_at IS NULL)
        OR (claim_job_id IS NOT NULL AND claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),
    -- 進行中なら所有者と、どの設定で送っているかが必ず分かる。
    CHECK (state NOT IN ('checking', 'uploading', 'asset_known', 'tagging', 'fixing_datetime')
        OR (claim_job_id IS NOT NULL AND destination_revision_id IS NOT NULL)),
    -- 終端と待機状態に claim が残っていると、明示操作しても期限まで claim できない。
    CHECK (state NOT IN ('pending', 'needs_recheck', 'complete', 'failed',
                         'awaiting_datetime_approval')
        OR claim_job_id IS NULL),
    -- 送信済みなのにどの設定へ送ったか分からない、を作らない。
    CHECK (state <> 'complete' OR destination_revision_id IS NOT NULL),
    FOREIGN KEY (destination_id, target_epoch, destination_revision_id)
        REFERENCES destination_revision(destination_id, target_epoch, id) ON DELETE RESTRICT
);

INSERT INTO upload_record_new (
    id, destination_id, target_epoch, media_file_id, state, selection_rule, origin,
    first_check_result, remote_asset_id, remote_is_trashed, remote_checked_at, checksum,
    attempts, last_error, eligibility_reason, merge_group_id, claim_job_id, claim_token,
    claim_expires_at, destination_revision_id, invalidated_at, invalidated_reason,
    created_at, updated_at, remote_datetime_original, stack_state, remote_stack_id, stack_reason)
SELECT
    id, destination_id, target_epoch, media_file_id, state, selection_rule, origin,
    first_check_result, remote_asset_id, remote_is_trashed, remote_checked_at, checksum,
    attempts, last_error, eligibility_reason, merge_group_id, claim_job_id, claim_token,
    claim_expires_at, destination_revision_id, invalidated_at, invalidated_reason,
    created_at, updated_at, remote_datetime_original, stack_state, remote_stack_id, stack_reason
FROM upload_record;

DROP TABLE upload_record;
ALTER TABLE upload_record_new RENAME TO upload_record;

-- 索引と trigger は DROP TABLE で一緒に消えるので、全部作り直す。
-- **部分索引は述語が問い合わせ側と一字一句そろっていないと使われない。**

CREATE INDEX upload_record_by_media ON upload_record (media_file_id);
CREATE INDEX upload_record_claimable
    ON upload_record (destination_id, state) WHERE invalidated_at IS NULL;

-- 第 2 パスの抽出の駆動索引（`0015`）。旧 epoch の complete が監査履歴として
-- 残るので、`target_epoch` を鍵に入れて別ライブラリの資産 ID を掴まない。
CREATE INDEX upload_record_unstacked ON upload_record (destination_id, target_epoch, id)
    WHERE stack_state IS NULL AND state = 'complete' AND invalidated_at IS NULL;

-- 「まだ送っていない」を数えるための索引（`0019`）。2 列とも等値で当たるので、
-- 統計の有無によらずこちらが選ばれる。
CREATE INDEX upload_record_live_pair
    ON upload_record (media_file_id, destination_id) WHERE invalidated_at IS NULL;

-- 同一性の一意性。**有効な行だけを見る。** 無効化された行は監査履歴なので、
-- 同じ組に何行あってもよい。
--
-- **列順は `(media_file_id, target_epoch, destination_id)`。** 一意性は列の集合で
-- 決まるので順序は守るものを変えないが、**述語が同じ部分索引どうしは、先頭の
-- 列が重なると計画を奪い合う**。この索引は既存の 2 本と同じ
-- `WHERE invalidated_at IS NULL` を持つので、両方を避ける並びを選ぶ。
--
-- `destination_id` を先頭に置くと `upload_record_claimable`
-- （`(destination_id, state)`）から奪う。そうなると `claim_next` は
-- 「pending の行だけを辿る」から「その宛先・epoch の有効な全行（complete を
-- 含む）を辿って state で捨てる」へ落ちる。claim はファイル 1 本ごとに走るので、
-- 同期 1 回が O(N^2) になる。
--
-- `(media_file_id, destination_id, ...)` の並びは `upload_record_live_pair`
-- （`(media_file_id, destination_id)`）から奪う。探索鍵は同じ 2 列なので速さは
-- 変わらないが、駆動する索引を固定したテスト（`0019`）が指すものと食い違う。
--
-- **統計（`ANALYZE`）はどこでも取っていない**ので、選ばれた計画がそのまま
-- 実機に出る。`target_epoch` を 2 番目に挟めば、`destination_id` 単独にも
-- `(media_file_id, destination_id)` にも当たらない。3 列とも等値で
-- `invalidated_at IS NULL` を持つ引き（送信対の解決）は、3 列とも鍵に入る。
--
-- **この並びでも `media_file_id` 単独の等値は移る。** `deletion_blocker` は
-- `upload_record_live_pair` からこの索引へ移るが、**先頭列も述語も同じなので
-- コストは等価**（実測）。動かさないと言えるのは `claim_next` と
-- 「まだ送っていない」の集計の計画で、`media_file_id` 単独の引きではない。
CREATE UNIQUE INDEX upload_record_live_identity
    ON upload_record (media_file_id, target_epoch, destination_id)
    WHERE invalidated_at IS NULL;

-- 複合外部キーは destination_revision_id が NULL だと効かない。pending の行が
-- 存在しない epoch を名乗れると、後から同じ epoch の revision が別の意味で
-- 作られたときに、どの設定へ送ったかを復元できなくなる。
CREATE TRIGGER upload_record_epoch_must_exist
BEFORE INSERT ON upload_record
WHEN NOT EXISTS (
    SELECT 1 FROM destination_revision
     WHERE destination_id = NEW.destination_id AND target_epoch = NEW.target_epoch
)
BEGIN
    SELECT RAISE(ABORT, 'no revision exists for this destination and epoch');
END;

-- 同一性の 3 欄は不変。書き換えられると、INSERT 時の guard も複合 FK も
-- 迂回して「存在しない epoch の pending 行」を作れる。
CREATE TRIGGER upload_record_identity_is_immutable
BEFORE UPDATE OF destination_id, target_epoch, media_file_id ON upload_record
WHEN NEW.destination_id IS NOT OLD.destination_id
  OR NEW.target_epoch IS NOT OLD.target_epoch
  OR NEW.media_file_id IS NOT OLD.media_file_id
BEGIN
    SELECT RAISE(ABORT, 'the identity of an upload record is immutable');
END;

CREATE TRIGGER upload_record_selection_rule_immutable
BEFORE UPDATE OF selection_rule ON upload_record
WHEN NEW.selection_rule <> OLD.selection_rule
BEGIN
    SELECT RAISE(ABORT, 'selection_rule is immutable');
END;

CREATE TRIGGER upload_record_first_check_immutable
BEFORE UPDATE OF first_check_result ON upload_record
WHEN OLD.first_check_result IS NOT NULL AND NEW.first_check_result IS NOT OLD.first_check_result
BEGIN
    SELECT RAISE(ABORT, 'first_check_result is immutable');
END;

-- 3 列の組み合わせを守る（`0015`）。INSERT と UPDATE の両方に置く。
-- **比較は `IS` で書く（`=` ではない）。** NULL との `=` は NULL を返し、
-- WHEN が成立せず trigger が黙って素通りする。
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

-- スタックは「その `remote_asset_id` を送った結果」（`0016`）。資産 ID が消えたり
-- 別の値に変わったら、その結果はもう現在の姿を表さない。**NULL だけでなく
-- 「別 ID への差し替え」も塞ぐ。**
CREATE TRIGGER upload_record_stacked_needs_its_asset
AFTER UPDATE OF stack_state, remote_stack_id, remote_asset_id ON upload_record
WHEN NEW.stack_state IS 'stacked'
    AND (NEW.remote_asset_id IS NULL OR OLD.remote_asset_id IS NOT NEW.remote_asset_id)
BEGIN
    SELECT RAISE(ABORT, 'stacked のまま remote_asset_id を変えられない');
END;

CREATE TRIGGER upload_record_stacked_needs_its_asset_insert
AFTER INSERT ON upload_record
WHEN NEW.stack_state IS 'stacked' AND NEW.remote_asset_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'stacked のまま remote_asset_id を捨てられない');
END;
