-- 撮影日時を算出した版を、取り込みに使った版から分ける（§6 の再計算）。

-- captured_at を算出したときに使ったプロファイルリビジョン。
-- profile_revision_id（そのレコードが取り込みに使用した不変の版）とは別の問い。
-- 分けないと、recompute_timestamps が「旧版を指しながら値は新版由来」の行を作るか、
-- 版ごと進めて timestamp 以外の新定義まで適用したと偽ることになる。
ALTER TABLE media_file ADD COLUMN captured_at_revision_id TEXT
    REFERENCES profile_revision(id) ON DELETE RESTRICT;

-- 既存行は取り込み時の版で算出されている。
UPDATE media_file SET captured_at_revision_id = profile_revision_id;

-- **単一の FK では「同じプロファイルの版であること」を守れない**（別プロファイルの
-- 版も指せる）。SQLite の ALTER TABLE では複合 FK も NOT NULL も後から足せないので、
-- trigger で同じ強さを作る。profile_revision には UNIQUE (profile_id, id) があるので
-- 突き合わせられる（volume_instance と media_file が使っている複合 FK と同じ根拠）。
--
-- media_file を作り直さないのは、upload_record / artifact_staging / merge_member から
-- 参照されているため。**移行の失敗が最も高くつく表**で rebuild する利は無い。
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
