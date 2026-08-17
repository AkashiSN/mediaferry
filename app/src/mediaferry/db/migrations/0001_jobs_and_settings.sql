-- ジョブと設定。artifact_staging と upload_record が job.id を参照するので
-- 最初の版に置く。

CREATE TABLE job (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL CHECK (type IN (
                         'scan', 'import', 'detect_groups', 'merge',
                         'upload', 'recompute_timestamps', 'deep_verify')),
    status           TEXT NOT NULL CHECK (status IN (
                         'queued', 'running', 'cancelling', 'cancelled',
                         'interrupted', 'succeeded', 'failed')),
    params_json      TEXT NOT NULL,
    progress_json    TEXT,
    lease_token      TEXT,
    lease_expires_at TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    error            TEXT,
    -- 片方だけ残ると「期限なし」と区別できなくなる。
    CHECK ((lease_token IS NULL AND lease_expires_at IS NULL)
        OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX job_queued ON job (created_at) WHERE status = 'queued';
CREATE INDEX job_running ON job (lease_expires_at) WHERE status IN ('running', 'cancelling');

-- id は SSE の Last-Event-ID に使うので、ジョブをまたいで単調増加させる。
CREATE TABLE job_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    level     TEXT NOT NULL CHECK (level IN ('debug', 'info', 'warning', 'error')),
    message   TEXT NOT NULL,
    data_json TEXT,
    at        TEXT NOT NULL,
    UNIQUE (job_id, seq)
);

CREATE TABLE app_setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
