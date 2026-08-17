-- 公開されたメディアと、公開途中の状態、結合グループ。

CREATE TABLE media_file (
    id                  TEXT PRIMARY KEY,
    role                TEXT NOT NULL CHECK (role IN ('original', 'derived')),
    profile_id          TEXT NOT NULL REFERENCES device_profile(id) ON DELETE RESTRICT,
    profile_revision_id TEXT NOT NULL,
    -- DATA_ROOT からの相対パス。保存先の名前であり、カード上の原名ではない。
    rel_path            TEXT NOT NULL UNIQUE,
    size_bytes          INTEGER NOT NULL,
    mtime_ns            INTEGER NOT NULL,
    sha1                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('photo', 'video')),
    captured_at         TEXT NOT NULL,
    captured_at_source  TEXT NOT NULL CHECK (captured_at_source IN ('filename', 'exif', 'mtime')),
    captured_at_tz      TEXT,
    captured_at_note    TEXT,
    duration_seconds    REAL,
    -- ffprobe を実行していない状態 (not_run) は公開済みレコードには無い。
    probe_state         TEXT NOT NULL CHECK (probe_state IN ('ok', 'failed', 'not_applicable')),
    missing_at          TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT,
    -- probe に成功した動画は必ず duration を持つ（§9.7 の境界判定が依存する）。
    CHECK (kind <> 'video' OR probe_state <> 'ok' OR duration_seconds IS NOT NULL)
);

CREATE INDEX media_file_sha1 ON media_file (sha1);
CREATE INDEX media_file_captured_at ON media_file (captured_at);

CREATE TABLE merge_group (
    id                   TEXT PRIMARY KEY,
    profile_id           TEXT NOT NULL REFERENCES device_profile(id) ON DELETE RESTRICT,
    profile_revision_id  TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN (
                             'detected', 'merging', 'merged', 'failed', 'skipped')),
    input_digest         TEXT NOT NULL,
    output_media_file_id TEXT REFERENCES media_file(id) ON DELETE RESTRICT,
    detected_by          TEXT NOT NULL CHECK (detected_by IN ('auto', 'manual')),
    superseded_by_id     TEXT REFERENCES merge_group(id) ON DELETE RESTRICT,
    tool_version         TEXT,
    verification_json    TEXT,
    adopted_at           TEXT,
    error                TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX merge_group_active_digest
    ON merge_group (input_digest) WHERE superseded_by_id IS NULL;

-- active は merge_group.superseded_by_id の写し。SQLite の部分索引は
-- 他テーブルの列を見られないので、trigger で同期する。
CREATE TABLE merge_member (
    merge_group_id TEXT NOT NULL REFERENCES merge_group(id) ON DELETE CASCADE,
    media_file_id  TEXT NOT NULL REFERENCES media_file(id) ON DELETE RESTRICT,
    position       INTEGER NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    PRIMARY KEY (merge_group_id, media_file_id),
    UNIQUE (merge_group_id, position)
);

CREATE UNIQUE INDEX merge_member_one_active_group
    ON merge_member (media_file_id) WHERE active = 1;

CREATE TRIGGER merge_group_supersede_deactivates_members
AFTER UPDATE OF superseded_by_id ON merge_group
WHEN NEW.superseded_by_id IS NOT NULL AND OLD.superseded_by_id IS NULL
BEGIN
    UPDATE merge_member SET active = 0 WHERE merge_group_id = NEW.id;
END;

-- supersede は不可逆。戻せると active と親の状態が乖離し、旧グループの
-- member が復活して現グループの候補判定を壊す。
CREATE TRIGGER merge_group_supersede_is_final
BEFORE UPDATE OF superseded_by_id ON merge_group
WHEN OLD.superseded_by_id IS NOT NULL AND NEW.superseded_by_id IS NOT OLD.superseded_by_id
BEGIN
    SELECT RAISE(ABORT, 'supersede is irreversible');
END;

CREATE TRIGGER merge_group_no_self_supersede
BEFORE UPDATE OF superseded_by_id ON merge_group
WHEN NEW.superseded_by_id = NEW.id
BEGIN
    SELECT RAISE(ABORT, 'a group cannot supersede itself');
END;

-- active の denormalize は片方向の trigger だけだと、既に superseded の
-- グループへ後から active な member を足して壊せる。
-- active は親の superseded 状態の写しなので、両方向で一致を強制する。
-- 片方向だけだと、active な member の merge_group_id を superseded な
-- グループへ付け替えて迂回できる。
CREATE TRIGGER merge_member_insert_matches_parent
BEFORE INSERT ON merge_member
WHEN NEW.active <> (
    SELECT superseded_by_id IS NULL FROM merge_group WHERE id = NEW.merge_group_id
)
BEGIN
    SELECT RAISE(ABORT, 'member active flag must match the group supersede state');
END;

CREATE TRIGGER merge_member_update_matches_parent
BEFORE UPDATE OF merge_group_id, active ON merge_member
WHEN NEW.active <> (
    SELECT superseded_by_id IS NULL FROM merge_group WHERE id = NEW.merge_group_id
)
BEGIN
    SELECT RAISE(ABORT, 'member active flag must match the group supersede state');
END;

-- 取り込みと結合が同じ公開プロトコルを通る。片方だけ回収不能にしない。
CREATE TABLE artifact_staging (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('import', 'merge')),
    job_id           TEXT NOT NULL REFERENCES job(id) ON DELETE RESTRICT,
    lease_token      TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('writing', 'staged', 'published')),
    staging_rel_path TEXT NOT NULL,
    final_rel_path   TEXT,
    expected_size    INTEGER,
    content_sha1     TEXT,
    metadata_json    TEXT,
    source_entry_id  TEXT REFERENCES source_entry(id) ON DELETE RESTRICT,
    merge_group_id   TEXT REFERENCES merge_group(id) ON DELETE RESTRICT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    -- staged 以降は永続情報だけで公開を再開できる。
    CHECK (state = 'writing' OR (final_rel_path IS NOT NULL AND expected_size IS NOT NULL
           AND content_sha1 IS NOT NULL AND metadata_json IS NOT NULL)),
    CHECK ((kind = 'import' AND source_entry_id IS NOT NULL AND merge_group_id IS NULL)
        OR (kind = 'merge' AND merge_group_id IS NOT NULL AND source_entry_id IS NULL))
);

CREATE INDEX artifact_staging_open ON artifact_staging (state) WHERE state <> 'published';

-- media_file が 0002 の時点では無かったので、外部キーをここで足す。
-- SQLite に ADD CONSTRAINT が無いため、作り直して移し替える。
CREATE TABLE source_entry_new (
    id                  TEXT PRIMARY KEY,
    volume_instance_id  TEXT NOT NULL REFERENCES volume_instance(id) ON DELETE CASCADE,
    rel_path            TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    mtime_ns            INTEGER NOT NULL,
    quick_fingerprint   TEXT NOT NULL,
    fingerprint_version INTEGER NOT NULL,
    media_file_id       TEXT REFERENCES media_file(id) ON DELETE SET NULL,
    state               TEXT NOT NULL CHECK (state IN ('seen', 'importing', 'published', 'failed')),
    observed_at         TEXT NOT NULL,
    UNIQUE (volume_instance_id, rel_path)
);

INSERT INTO source_entry_new SELECT * FROM source_entry;
DROP TABLE source_entry;
ALTER TABLE source_entry_new RENAME TO source_entry;
