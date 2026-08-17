import base64

import pytest

from mediaferry.clock import now_iso
from mediaferry.settings import (
    SettingInvalid,
    SettingLocked,
    SettingsService,
    Tier,
    startup_warnings,
)


def service(db, **env):
    return SettingsService(db, env={f"MEDIAFERRY_{k}": v for k, v in env.items()})


def test_defaults_are_used_when_nothing_is_set(db):
    snapshot = service(db).snapshot()
    assert snapshot.bind_host == "127.0.0.1"
    assert snapshot.http_port == 8080
    assert str(snapshot.broker_socket) == "/run/mediaferry/broker.sock"
    assert snapshot.auto_import == "trusted"
    assert snapshot.upload_concurrency == 2
    assert snapshot.default_timezone is None


def test_db_overrides_the_default(db):
    db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'debug', ?)", (now_iso(),))
    assert service(db).snapshot().log_level == "debug"


def test_env_overrides_the_db(db):
    """TrueNAS のアプリ設定画面が常に事実と一致するようにするため、env が勝つ."""
    db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'debug', ?)", (now_iso(),))
    assert service(db, LOG_LEVEL="warning").snapshot().log_level == "warning"


def test_env_backed_settings_are_locked(db):
    described = {s.key: s for s in service(db, HTTP_PORT="9000").describe_all()}
    assert described["HTTP_PORT"].source == "env"
    assert described["HTTP_PORT"].locked is True
    assert described["LOG_LEVEL"].locked is False


def test_writing_a_locked_setting_is_refused(db):
    with pytest.raises(SettingLocked):
        service(db, HTTP_PORT="9000").set("HTTP_PORT", "9001")


def test_bootstrap_secrets_cannot_be_stored_in_the_db(db):
    """暗号文と復号鍵が同じバックアップに入ると、暗号化が何も守らなくなる."""
    svc = service(db)
    for key in ("SECRET_KEY", "AUTH_PASSWORD", "DATA_ROOT", "BROKER_SOCKET"):
        with pytest.raises(SettingLocked, match="env"):
            svc.set(key, "x")
    assert db.execute("SELECT count(*) FROM app_setting").fetchone()[0] == 0


def test_bootstrap_rows_in_the_db_are_ignored(db):
    """書けないだけでなく読みもしない.

    set() は BOOTSTRAP を弾くが、旧版・手動編集・将来の不具合で行が紛れ込むと、
    読む側が拾った瞬間に「鍵は DB の外」という境界が崩れる。
    """
    for key, value in (
        ("SECRET_KEY", base64.b64encode(bytes(32)).decode()),
        ("AUTH_PASSWORD", "s3cret"),
        ("DATA_ROOT", "/elsewhere"),
        ("BROKER_SOCKET", "/tmp/evil.sock"),  # noqa: S108
    ):
        db.execute("INSERT INTO app_setting VALUES (?, ?, ?)", (key, value, now_iso()))

    snapshot = service(db).snapshot()
    assert snapshot.secret_key is None
    assert snapshot.auth_password is None
    assert str(snapshot.data_root) == "/data"
    assert str(snapshot.broker_socket) == "/run/mediaferry/broker.sock"

    described = {s.key: s for s in service(db).describe_all()}
    assert described["SECRET_KEY"].source == "default"
    assert described["DATA_ROOT"].source == "default"


def test_set_reports_when_the_value_takes_effect(db):
    svc = service(db)
    assert svc.set("HTTP_PORT", "9001") is Tier.RESTART
    assert svc.set("LOG_LEVEL", "debug") is Tier.RUNTIME


def test_runtime_values_are_visible_to_the_next_snapshot(db):
    """UI で TZ を設定した直後の取り込みが古い値を見ないこと."""
    svc = service(db)
    assert svc.snapshot().default_timezone is None
    svc.set("DEFAULT_TIMEZONE", "Asia/Tokyo")
    assert svc.snapshot().default_timezone == "Asia/Tokyo"


def test_set_validates_before_storing(db):
    svc = service(db)
    with pytest.raises(SettingInvalid):
        svc.set("HTTP_PORT", "not-a-port")
    with pytest.raises(SettingInvalid):
        svc.set("AUTO_IMPORT", "always")
    with pytest.raises(SettingInvalid):
        svc.set("DEFAULT_TIMEZONE", "Mars/Olympus")
    assert db.execute("SELECT count(*) FROM app_setting").fetchone()[0] == 0


def test_unknown_keys_are_refused(db):
    with pytest.raises(SettingInvalid):
        service(db).set("SHELL", "/bin/sh")


def test_secret_key_must_be_32_random_bytes_in_base64(db):
    # base64 として読めない
    with pytest.raises(SettingInvalid, match="base64"):
        service(db, SECRET_KEY="hunter2").snapshot()
    # base64 としては読めるが 256bit ではない（パスワードを base64 にしただけ）
    short = base64.b64encode(b"hunter2").decode()
    with pytest.raises(SettingInvalid, match="32"):
        service(db, SECRET_KEY=short).snapshot()
    ok = base64.b64encode(bytes(32)).decode()
    assert service(db, SECRET_KEY=ok).snapshot().secret_key == bytes(32)


def test_secrets_are_masked_when_described(db):
    """API 応答にもログにも値そのものを出さない."""
    described = {s.key: s for s in service(db, AUTH_PASSWORD="s3cret").describe_all()}
    assert described["AUTH_PASSWORD"].value == "********"
    assert "s3cret" not in repr(described["AUTH_PASSWORD"])
    assert described["AUTH_PASSWORD"].writable is False


def test_non_loopback_without_auth_warns(db):
    warnings = startup_warnings(service(db, BIND_HOST="0.0.0.0").snapshot())  # noqa: S104
    assert any("認証" in w for w in warnings)
    assert startup_warnings(service(db).snapshot()) == []
