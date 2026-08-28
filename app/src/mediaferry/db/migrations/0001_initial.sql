-- 最初の版。表・索引・trigger をこの 1 本で作る。
--
-- **トランザクションと版の記録は runner が持つ**（`db/migrate.py`）。この
-- ファイルは DDL だけを書き、`BEGIN` / `COMMIT` も `schema_migration` への
-- INSERT も書かない。
--
-- 並びは **表 → 索引 → trigger**。trigger は対象の表が既に無いと作れない。
-- 表どうしの参照は前方参照でよい（SQLite は CREATE 時に相手の存在を見ない）。

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
    -- 判定が「暫定マッチ」だったか（§6 の「対象だが中身が無い」）。
    --
    -- watcher は「積んでよいか」を毎 tick DB の現在値から組み直す。信頼登録は
    -- trusted_at を UPDATE するだけで mountd の観測は動かないので、直前の
    -- VolumeView を見ていると、カードを挿したまま承認しても再評価されない。
    -- identity_confidence は volume_instance にあるが provisional は VolumeView に
    -- しか無く、1 つでも欠けると組み直しが成立しない。
    provisional             INTEGER NOT NULL DEFAULT 0 CHECK (provisional IN (0, 1)),
    -- 「そのカードを数え終えたか」を、数えた結果からではなく事実として持つ（§11）。
    --
    -- **一致するファイルが無いカードは `source_entry` を 1 行も作らない。** だから
    -- 「行の最大 observed_at」で代用すると、スキャンが完全に成功しても
    -- `scanned_at` が NULL のままになり、ホームは「中身を数えています。」から
    -- 永久に出られない（DJI は内蔵ストレージと SD カードを同時に見せるので、
    -- 撮影前の内蔵ストレージで実際に起きる）。
    scanned_at              TEXT,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT
);

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
    -- 自動取り込みを積んだ接続に印を付ける（§12.1）。列は volume_instance では
    -- なく volume_presence に置く。「このカードを取り込んだか」ではなく「この
    -- 接続について積んだか」を憶えるためで、抜き挿しすればまた積まれる。
    --
    -- 積むことと印を付けることは 1 つの BEGIN IMMEDIATE に入れる。SQLite に行ロック
    -- は無いので、条件付き UPDATE（CAS）でこの印を取れた側だけが実行者になる。
    auto_import_at     TEXT,
    -- カードを見つけたら数える（§12.1）。`auto_import_at` と同じ形の印で、
    -- presence ごとに 1 回だけ `scan` を積むためのもの。
    --
    -- **信頼の有無にも AUTO_IMPORT にもよらない。** §12.1 の「スキャン結果を
    -- 画面に出すところで止まり、ユーザの承認を待つ」は、数え終わっていることを
    -- 前提にしている。
    auto_scan_at       TEXT,
    UNIQUE (volume_instance_id, broker_epoch, generation, major, minor)
);

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

CREATE TABLE source_entry (
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
    -- 同席の印（§9.11）。**中身は `<scan の job_id>:<stem prefix>`。** スキャンの
    -- id だけにすると、1 回のスキャンが書いた別々の組が同じ印になり、一覧が
    -- 無関係な写真を 1 タイルに畳む。prefix を含めることで、印の等しさが「同じ
    -- スキャンで、同じ stem の下で、同時に見えた」をそのまま表す。SQL 側で
    -- rel_path から stem を切り出さずに済む。**書くのはスキャン。**
    copresent_key       TEXT,
    -- 「自分より順位が上の相方が居るか」を SQL で見るのに要る。rel_path から SQL で
    -- 拡張子を切り出すと読めない式になる。**書くのはスキャン。**
    extension           TEXT,
    UNIQUE (volume_instance_id, rel_path)
);

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

CREATE TABLE auth_session (
    fingerprint  TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

-- 1 行だけ持つ。認証が無効なら行が無い。
CREATE TABLE auth_password (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    hash       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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
    -- captured_at を算出したときに使ったプロファイルリビジョン。
    -- profile_revision_id（そのレコードが取り込みに使用した不変の版）とは別の問い。
    -- 分けないと、recompute_timestamps が「旧版を指しながら値は新版由来」の行を作るか、
    -- 版ごと進めて timestamp 以外の新定義まで適用したと偽ることになる。
    captured_at_revision_id TEXT REFERENCES profile_revision(id) ON DELETE RESTRICT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT,
    -- probe に成功した動画は必ず duration を持つ（§9.7 の境界判定が依存する）。
    CHECK (kind <> 'video' OR probe_state <> 'ok' OR duration_seconds IS NOT NULL)
);

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
    -- リモートの撮影日時の観測（§9.10 / §13）。承認の画面は「現在値と変更案を
    -- 並べて表示」する。現在値はどこかに保存されていなければ出せない ——
    -- 画面を開くたびに N 件ぶんの HTTP を出すわけにはいかない。
    -- **いつ時点の観測かは `remote_checked_at` が持つ**（列を増やさない）。日時と
    -- 観測時刻を別々に書くと、「日時は新しいが観測時刻は古い」行ができる。
    remote_datetime_original TEXT,
    -- スタックは「その宛先へその資産を送った結果」（§9.11）なので、
    -- `remote_asset_id` と同じ層に置く。**状態機械には状態を足さない。**
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

CREATE INDEX job_queued ON job (created_at) WHERE status = 'queued';

CREATE INDEX job_running ON job (lease_expires_at) WHERE status IN ('running', 'cancelling');

-- UUID の無いカードは同定できない。推測でしかない同定に UNIQUE を掛けない。
CREATE UNIQUE INDEX volume_instance_identity
    ON volume_instance (fs_uuid, fs_type, size_bytes) WHERE fs_uuid <> '';

CREATE INDEX volume_presence_live
    ON volume_presence (volume_instance_id) WHERE detached_at IS NULL;

CREATE UNIQUE INDEX merge_group_active_digest
    ON merge_group (input_digest) WHERE superseded_by_id IS NULL;

CREATE UNIQUE INDEX merge_member_one_active_group
    ON merge_member (media_file_id) WHERE active = 1;

CREATE INDEX artifact_staging_open ON artifact_staging (state) WHERE state <> 'published';

CREATE INDEX idx_auth_session_expiry ON auth_session (expires_at);

-- 再計算の対象抽出が引く経路。列の順が意味を持つ。`media_file_id` で引いてから
-- `observed_at, id` の順で読めるので、一時 B-tree が要らなくなる。**`state` を
-- 間に挟まない**（挟むと並べ替えに使えなくなる）。
CREATE INDEX source_entry_by_media
    ON source_entry (media_file_id, observed_at, id);

-- 外部キーには索引が自動では付かない。派生物の抽出が
-- `merge_group.output_media_file_id = m.id` で引く。
CREATE INDEX merge_group_by_output
    ON merge_group (output_media_file_id);

-- 一覧の従外しが引く経路（同じカードの、同じ印の、別のメディア）。
CREATE INDEX source_entry_copresent
    ON source_entry (volume_instance_id, copresent_key)
    WHERE copresent_key IS NOT NULL;

CREATE INDEX media_file_sha1 ON media_file (sha1);

CREATE INDEX media_file_captured_at ON media_file (captured_at);

-- 再計算のページ送りを、プロファイルの中だけで進めるための索引。
--
-- 外側の keyset を `rel_path` の UNIQUE 索引で駆動すると、`LIMIT` は**返す件数
-- しか縛らない** —— 別プロファイルの大きなライブラリがあると、1 ページ読むだけで
-- その全行を走査する（`original` は `rel_path` の並び上、`derived/` も先に通る）。
-- **`fetch` の最中は heartbeat もキャンセル観測も無い**ので、そこでリース窓を超える。
--
-- 列の順が意味を持つ。`profile_id, role` で絞ってから `rel_path` の順に読めるので、
-- keyset の `> ?` がそのまま索引の探索になり、並べ替えも要らなくなる。
CREATE INDEX media_file_by_profile ON media_file (profile_id, role, rel_path);

-- プロファイルで絞ったライブラリ一覧のための索引（§11 / §13）。
--
-- `media_file_by_profile` は再計算のページ送りには正しく効くが、一覧
-- （`m.profile_id IN (...)` + `ORDER BY m.captured_at DESC` + `LIMIT 50`）にも
-- 選ばれてしまい、**そのプロファイルの全行を拾ってから並べ替える**。
-- `media_file_captured_at` を辿れば先頭ページで止まれた経路なので、
-- プロファイルが大半を占める通常の構成ほど悪化する。
--
-- 並びと同じ向きで持つ。**tie-break は rel_path。** id は乱数なので、同じ撮影日時の
-- 並びに意味が無い。rel_path は UNIQUE なので単独で足り、索引に入れておくと
-- 一時 B-tree が要らなくなる。
CREATE INDEX media_file_listing
    ON media_file (profile_id, captured_at DESC, rel_path DESC);

-- 「つないだ動画」だけの一覧のための索引（§11 / §13）。
--
-- `GET /media?role=derived` は `media_file_listing` の経路から外れる —— `role` は
-- その索引に含まれないので、SQLite は並びの索引（`media_file_captured_at`）で
-- `captured_at DESC` を辿りながら 1 行ずつ `role` を確かめる。`derived` は行数が
-- 少ないので、`LIMIT` を満たすまでの走査量が読めない。
--
-- 実測（original 60,000 行 / derived 200 行、captured_at をばらつかせた状態）:
-- 索引が無いと 55〜66 ms（`captured_at` 側の並び索引をほぼ全走査）、
-- 部分索引を置くと 0.1 ms 未満に落ちる。
CREATE INDEX media_file_derived_listing
    ON media_file (captured_at DESC, rel_path DESC) WHERE role = 'derived';

CREATE INDEX upload_record_by_media ON upload_record (media_file_id);

CREATE INDEX upload_record_claimable
    ON upload_record (destination_id, state) WHERE invalidated_at IS NULL;

-- 第 2 パス（スタックの評価）の抽出の駆動索引。**述語は問い合わせ側と一字一句
-- そろえる**（部分索引は述語が一致しないと使われない）。
--
-- **`target_epoch` を鍵に入れる。** 向き先を変えた宛先では旧 epoch の `complete` が
-- 監査履歴として残る（`_invalidate_old_epoch_locked` は `state <> 'complete'` だけを
-- 無効化する）ので、epoch で絞らないと**別ライブラリへ送った資産 ID を現行の
-- 資格情報で送る**ことになる。
CREATE INDEX upload_record_unstacked ON upload_record (destination_id, target_epoch, id)
    WHERE stack_state IS NULL AND state = 'complete' AND invalidated_at IS NULL;

-- **「まだ送っていない」を数えるための索引。**
--
-- ダッシュボードの `unsent_total` と宛先ごとの `unsent` は、media 1 件ごとに
-- 「この宛先の有効な記録があるか」を問い合わせる（`api/routes_system.py`）。
-- この問い合わせは `upload_record_by_media (media_file_id)` と
-- `upload_record_claimable (destination_id, state)` の**両方に等値の条件を持つ**。
-- 統計（`sqlite_stat1`）が無い間、SQLite は `destination_id` の方を選ぶことがあり、
-- そうなると media 1 件ごとに**その宛先の全レコードを走査する**（実測: media
-- 4,000 件で 1.3 秒、8,000 件で 5.6 秒。行数が倍になるたびに 4 倍）。
--
-- 2 列とも等値で当たる索引を置くと、統計の有無によらずこちらが選ばれる
-- （実測: 同じ DB で 2.5 ms）。`invalidated_at IS NULL` は問い合わせ側の条件と
-- 同じなので、部分索引にして小さく保つ。
--
-- **統計を取って解決しない。** `ANALYZE` でも同じ索引が選ばれるようになるが、
-- 取り直すのは起動時しかなく、空の DB で取った統計（「どの表も 0 行」）が残ると
-- 取り込みで行が増えたあとの選択が**統計が無いときより悪くなる**（結合の
-- 構成ファイルを引く問い合わせが走査に落ちるのを確認した）。
CREATE INDEX upload_record_live_pair
    ON upload_record (media_file_id, destination_id) WHERE invalidated_at IS NULL;

-- 同一性の一意性。**有効な行だけを見る。** 無効化された行は監査履歴なので、
-- 同じ組に何行あってもよい。
--
-- **列順は `(media_file_id, target_epoch, destination_id)`。** 一意性は列の集合で
-- 決まるので順序は守るものを変えないが、**述語が同じ部分索引どうしは、先頭の
-- 列が重なると計画を奪い合う**。この索引は他の 2 本と同じ
-- `WHERE invalidated_at IS NULL` を持つので、両方を避ける並びを選ぶ。
--
-- `destination_id` を先頭に置くと `upload_record_claimable`
-- （`(destination_id, state)`）から奪う。そうなると `claim_next` は
-- 「pending の行だけを辿る」から「その宛先・epoch の有効な全行（complete を
-- 含む）を辿って state で捨てる」へ落ちる。claim はファイル 1 本ごとに走るので、
-- 同期 1 回が O(N^2) になる。
--
-- `(media_file_id, destination_id, ...)` の並びは `upload_record_live_pair` から
-- 奪う。探索鍵は同じ 2 列なので速さは変わらないが、駆動する索引を固定した
-- テストが指すものと食い違う。
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

-- 過去データの解釈が後から変わらないよう、版は不変にする。
CREATE TRIGGER profile_revision_no_update BEFORE UPDATE ON profile_revision
BEGIN
    SELECT RAISE(ABORT, 'profile_revision is immutable');
END;

CREATE TRIGGER profile_revision_no_delete BEFORE DELETE ON profile_revision
BEGIN
    SELECT RAISE(ABORT, 'profile_revision is immutable');
END;

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

CREATE TRIGGER destination_revision_no_delete BEFORE DELETE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;

CREATE TRIGGER destination_revision_no_update BEFORE UPDATE ON destination_revision
BEGIN
    SELECT RAISE(ABORT, 'destination_revision is immutable');
END;

-- active は親の superseded / skipped 状態の写しなので、両方向で一致を強制する。
-- 片方向だけだと、既に superseded のグループへ後から active な member を足すか、
-- active な member の merge_group_id を superseded なグループへ付け替えて迂回できる。
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

-- **単一の FK では「同じプロファイルの版であること」を守れない**（別プロファイルの
-- 版も指せる）ので、trigger で同じ強さを作る。profile_revision には
-- UNIQUE (profile_id, id) があるので突き合わせられる（volume_instance と
-- media_file が使っている複合 FK と同じ根拠）。INSERT と UPDATE の両方に置く。
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

-- スタックの 3 列の組み合わせを守る。INSERT と UPDATE の両方に置く。
-- **片側だけだと抜け道になる。**
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

-- スタックは「その `remote_asset_id` を送った結果」（§9.11）。資産 ID が消えたり
-- 別の値に変わったら、その結果はもう現在の姿を表さない。書き手は 3 列を一緒に
-- 未評価へ戻すが、**将来の消し忘れを fail-closed にする**ために trigger でも守る。
--
-- **NULL だけでなく「別 ID への差し替え」も塞ぐ。** NULL だけを見ると、
-- `advance_owned` のような汎用の書き手（`_locked_cas`）が、古い
-- `remote_stack_id` を新しい資産の結果として残せる。
--
-- 見送り（`skipped`）は「送らなかった」記録なので、資産 ID とは独立に残ってよい。
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
