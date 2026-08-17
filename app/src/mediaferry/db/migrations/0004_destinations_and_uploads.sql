-- 転送先プロファイルとアップロード履歴。
-- 宛先の取り違えはアプリの検証だけに頼らず、複合外部キーで DB が防ぐ。

CREATE TABLE upload_destination (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    kind                TEXT NOT NULL CHECK (kind IN ('immich')),
    enabled             INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    -- 物理削除しない。履歴と監査情報を残す。
    archived_at         TEXT,
    current_revision_id TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (id, current_revision_id)
        REFERENCES destination_revision(destination_id, id) ON DELETE RESTRICT
);

CREATE TABLE destination_credential (
    id               TEXT PRIMARY KEY,
    destination_id   TEXT NOT NULL REFERENCES upload_destination(id) ON DELETE RESTRICT,
    revision         INTEGER NOT NULL,
    -- core/crypto.py の自己記述フォーマット。参照が絶えたら消して purged_at を立てる。
    secret_encrypted BLOB,
    key_fingerprint  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    purged_at        TEXT,
    UNIQUE (destination_id, revision),
    UNIQUE (destination_id, id),
    CHECK ((secret_encrypted IS NOT NULL AND purged_at IS NULL)
        OR (secret_encrypted IS NULL AND purged_at IS NOT NULL))
);

-- ある時点の接続設定一式のスナップショット。編集のたびに行が増える。
CREATE TABLE destination_revision (
    id                 TEXT PRIMARY KEY,
    destination_id     TEXT NOT NULL REFERENCES upload_destination(id) ON DELETE RESTRICT,
    revision           INTEGER NOT NULL,
    -- 向き先が変わったときだけ進む。履歴を引き継いでよいかの境界。
    target_epoch       INTEGER NOT NULL,
    -- API を叩きに行くエンドポイント。CDN やリバースプロキシを経由しない。
    base_url           TEXT NOT NULL,
    -- 画面のリンク生成にだけ使う。通信には使わない。
    public_url         TEXT,
    credential_id      TEXT NOT NULL,
    -- 同一性ではなく、向き先が変わったことを検知する guard。
    remote_user_id     TEXT,
    server_instance_id TEXT,
    verified_at        TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE (destination_id, revision),
    UNIQUE (destination_id, id),
    UNIQUE (destination_id, target_epoch, id),
    FOREIGN KEY (destination_id, credential_id)
        REFERENCES destination_credential(destination_id, id) ON DELETE RESTRICT
);

CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;

CREATE TRIGGER destination_revision_no_delete BEFORE DELETE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;

CREATE TABLE upload_record (
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
    UNIQUE (destination_id, target_epoch, media_file_id),
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

CREATE INDEX upload_record_by_media ON upload_record (media_file_id);
CREATE INDEX upload_record_claimable
    ON upload_record (destination_id, state) WHERE invalidated_at IS NULL;

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
