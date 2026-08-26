-- mediaferry:foreign-keys-off
-- 器が申告した撮影時刻（`container_wall`）を持てるようにし、`captured_at_source` に
-- `container` を足す。あわせて一覧の索引を `rel_path` で tie-break する形に直す。
--
-- **作り直しが要るのは CHECK 制約を後から変えられないため。** media_file は
-- merge_group / merge_member / source_entry / upload_record から参照されているので、
-- 外部キーを外さないと入れ替えられない。runner が先頭行の目印を見て切り替え、
-- 適用後に PRAGMA foreign_key_check を確かめる。

CREATE TABLE media_file_new (
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
    captured_at_source  TEXT NOT NULL
        CHECK (captured_at_source IN ('filename', 'exif', 'container', 'mtime')),
    captured_at_tz      TEXT,
    captured_at_note    TEXT,
    -- ffprobe が返した creation_time をそのまま入れる。**解釈しない** ——
    -- 意味は core/timestamps.py が決めるので、再計算で読み直せる。
    container_wall      TEXT,
    duration_seconds    REAL,
    -- ffprobe を実行していない状態 (not_run) は公開済みレコードには無い。
    probe_state         TEXT NOT NULL CHECK (probe_state IN ('ok', 'failed', 'not_applicable')),
    missing_at          TEXT,
    captured_at_revision_id TEXT REFERENCES profile_revision(id) ON DELETE RESTRICT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT,
    -- probe に成功した動画は必ず duration を持つ（§9.7 の境界判定が依存する）。
    CHECK (kind <> 'video' OR probe_state <> 'ok' OR duration_seconds IS NOT NULL)
);

INSERT INTO media_file_new (
    id, role, profile_id, profile_revision_id, rel_path, size_bytes, mtime_ns, sha1, kind,
    captured_at, captured_at_source, captured_at_tz, captured_at_note, duration_seconds,
    probe_state, missing_at, captured_at_revision_id, created_at)
SELECT
    id, role, profile_id, profile_revision_id, rel_path, size_bytes, mtime_ns, sha1, kind,
    captured_at, captured_at_source, captured_at_tz, captured_at_note, duration_seconds,
    probe_state, missing_at, captured_at_revision_id, created_at
FROM media_file;

DROP TABLE media_file;
ALTER TABLE media_file_new RENAME TO media_file;

-- 索引と trigger は DROP TABLE で一緒に消えるので、全部作り直す。
CREATE INDEX media_file_sha1 ON media_file (sha1);
CREATE INDEX media_file_captured_at ON media_file (captured_at);
CREATE INDEX media_file_by_profile ON media_file (profile_id, role, rel_path);

-- **tie-break は rel_path。** id は乱数なので、同じ撮影日時の並びに意味が無い。
-- rel_path は UNIQUE なので単独で足りる。
CREATE INDEX media_file_listing
    ON media_file (profile_id, captured_at DESC, rel_path DESC);
CREATE INDEX media_file_derived_listing
    ON media_file (captured_at DESC, rel_path DESC) WHERE role = 'derived';

-- `0011` の trigger をそのまま作り直す。単一の FK では「同じプロファイルの版で
-- あること」を守れないので、trigger で同じ強さを作っている。
CREATE TRIGGER media_file_captured_revision_insert
BEFORE INSERT ON media_file
WHEN NEW.captured_at_revision_id IS NULL
  OR NOT EXISTS (SELECT 1 FROM profile_revision
                  WHERE id = NEW.captured_at_revision_id
                    AND profile_id = NEW.profile_id)
BEGIN
    SELECT RAISE(ABORT, 'captured_at_revision_id must be a revision of the same profile');
END;

CREATE TRIGGER media_file_captured_revision_update
BEFORE UPDATE OF captured_at_revision_id, profile_id ON media_file
WHEN NEW.captured_at_revision_id IS NULL
  OR NOT EXISTS (SELECT 1 FROM profile_revision
                  WHERE id = NEW.captured_at_revision_id
                    AND profile_id = NEW.profile_id)
BEGIN
    SELECT RAISE(ABORT, 'captured_at_revision_id must be a revision of the same profile');
END;
