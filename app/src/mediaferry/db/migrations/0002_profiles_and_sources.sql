-- プロファイルとソース側（デバイス・ボリューム・スキャン結果）。

CREATE TABLE device_profile (
    id                  TEXT PRIMARY KEY,
    -- ライブラリのパスに使うので作成後は変更しない。
    slug                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    builtin             INTEGER NOT NULL CHECK (builtin IN (0, 1)),
    archived_at         TEXT,
    current_revision_id TEXT,
    created_at          TEXT NOT NULL,
    -- 他プロファイルの版を現行にできないよう複合外部キーで縛る。
    FOREIGN KEY (id, current_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT
);

CREATE TABLE profile_revision (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES device_profile(id) ON DELETE RESTRICT,
    revision        INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (profile_id, revision),
    UNIQUE (profile_id, id)
);

-- 過去データの解釈が後から変わらないよう、版は不変にする。
CREATE TRIGGER profile_revision_no_update BEFORE UPDATE ON profile_revision
BEGIN
    SELECT RAISE(ABORT, 'profile_revision is immutable');
END;

CREATE TRIGGER profile_revision_no_delete BEFORE DELETE ON profile_revision
BEGIN
    SELECT RAISE(ABORT, 'profile_revision is immutable');
END;

-- serial は機種の既定値でありうるので、識別は 4 つ組で行う。
-- SQLite の UNIQUE は NULL 同士を区別するため、欠損は '' で表す。
CREATE TABLE source_device (
    id             TEXT PRIMARY KEY,
    usb_vendor_id  TEXT NOT NULL,
    usb_product_id TEXT NOT NULL,
    usb_product    TEXT NOT NULL DEFAULT '',
    serial         TEXT NOT NULL DEFAULT '',
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    UNIQUE (usb_vendor_id, usb_product_id, usb_product, serial)
);

-- カードはリーダーの間を移動するので、デバイスとは独立に記憶する。
CREATE TABLE volume_instance (
    id                      TEXT PRIMARY KEY,
    fs_uuid                 TEXT NOT NULL DEFAULT '',
    fs_type                 TEXT NOT NULL,
    fs_label                TEXT NOT NULL DEFAULT '',
    size_bytes              INTEGER NOT NULL,
    identity_confidence     TEXT NOT NULL CHECK (identity_confidence IN ('high', 'low')),
    content_manifest_digest TEXT,
    last_source_device_id   TEXT REFERENCES source_device(id) ON DELETE SET NULL,
    profile_id              TEXT REFERENCES device_profile(id) ON DELETE RESTRICT,
    profile_revision_id     TEXT,
    trusted_at              TEXT,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT
);

-- UUID の無いカードは同定できない。推測でしかない同定に UNIQUE を掛けない。
CREATE UNIQUE INDEX volume_instance_identity
    ON volume_instance (fs_uuid, fs_type, size_bytes) WHERE fs_uuid <> '';

-- 同じ identity のカードが同時に 2 枚挿さりうるので、接続ごとに行を持つ。
-- 行は「接続」1 つに対応する。列挙のたびに増やさない。増やすと、キューに
-- 積んだときの presence と実行時の presence が別物になって必ず stale になり、
-- 抜けたポートの古い行が live のまま残って同定の確度を永久に下げる。
CREATE TABLE volume_presence (
    id                 TEXT PRIMARY KEY,
    volume_instance_id TEXT NOT NULL REFERENCES volume_instance(id) ON DELETE CASCADE,
    broker_epoch       TEXT NOT NULL,
    generation         INTEGER NOT NULL,
    device_node        TEXT NOT NULL,
    major              INTEGER NOT NULL,
    minor              INTEGER NOT NULL,
    sysfs_path         TEXT NOT NULL,
    attached_at        TEXT NOT NULL,
    detached_at        TEXT,
    UNIQUE (volume_instance_id, broker_epoch, generation, major, minor)
);

CREATE INDEX volume_presence_live
    ON volume_presence (volume_instance_id) WHERE detached_at IS NULL;

CREATE TABLE source_entry (
    id                  TEXT PRIMARY KEY,
    volume_instance_id  TEXT NOT NULL REFERENCES volume_instance(id) ON DELETE CASCADE,
    -- カード上の原名。保存先の名前 (media_file.rel_path) とは衝突時に食い違う。
    rel_path            TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    mtime_ns            INTEGER NOT NULL,
    quick_fingerprint   TEXT NOT NULL,
    fingerprint_version INTEGER NOT NULL,
    media_file_id       TEXT,
    state               TEXT NOT NULL CHECK (state IN ('seen', 'importing', 'published', 'failed')),
    observed_at         TEXT NOT NULL,
    UNIQUE (volume_instance_id, rel_path)
);
