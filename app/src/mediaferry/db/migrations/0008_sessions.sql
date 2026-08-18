-- ログインセッションと、パスワードの世代（§12 / §14）。
--
-- **Cookie の値は生のまま保存しない。** 保存するのは SHA-256 の指紋で、受け取った
-- Cookie を毎回ハッシュして突き合わせる。DB のバックアップが漏れても、そこから
-- 有効な Cookie を組み立てられない（転送先の remote_user_id と同じ理屈）。
--
-- **パスワードの世代印は Argon2 のハッシュそのもの。** 起動のたびに平文をハッシュ
-- し直して比べる形にはできない —— salt が毎回変わるので、同じ平文でも一致せず、
-- 再起動のたびに全セッションが失効する。保存したハッシュに env の平文を verify し、
-- 通れば同じ世代、通らなければ全失効してハッシュを差し替える。

CREATE TABLE auth_session (
    fingerprint  TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX idx_auth_session_expiry ON auth_session (expires_at);

-- 1 行だけ持つ。認証が無効なら行が無い。
CREATE TABLE auth_password (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    hash       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
