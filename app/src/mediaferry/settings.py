"""インフラ設定の解決.

優先順位は 環境変数 > DB（Web 画面） > 既定値。env で指定された項目は画面で
ロックされる。TrueNAS のアプリ設定画面に書いた値が、常にアプリの実際の挙動と
一致する状態を保つための順序である。

転送先プロファイルはここに含まれない。ユーザのデータであって基盤の設定では
ないので、DB だけで管理する（§12）。
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .clock import now_iso

ENV_PREFIX = "MEDIAFERRY_"
MASK = "********"


class Tier(Enum):
    """値がどこに置けて、いつ効くか."""

    BOOTSTRAP = "bootstrap"  # env のみ。DB に保存できない
    RESTART = "restart"  # DB に保存でき、次回起動から効く
    RUNTIME = "runtime"  # DB に保存でき、次のジョブ／リクエストから効く


class SettingLocked(RuntimeError):
    """env で固定されているか、DB へ保存してはいけない項目を書こうとした."""


class SettingInvalid(ValueError):
    """未知のキー、または値が仕様を満たさない."""


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: str | None
    parse: Callable[[str], Any]
    tier: Tier
    secret: bool = False


@dataclass(frozen=True)
class SettingValue:
    key: str
    value: str | None
    source: str  # env / db / default
    locked: bool
    tier: Tier
    writable: bool


@dataclass(frozen=True)
class Settings:
    data_root: Path
    broker_socket: Path
    bind_host: str
    http_port: int
    auth_password: str | None
    secret_key: bytes | None
    upload_concurrency: int
    upload_timeout_seconds: int
    upload_max_attempts: int
    auto_import: str
    default_timezone: str | None
    log_level: str
    trusted_hosts: str = ""

    def trusted_host_names(self) -> frozenset[str]:
        """`Host` として名乗ってよい名前（IP と localhost は別に既定で通す）."""
        return frozenset(name.strip() for name in self.trusted_hosts.split(",") if name.strip())


@dataclass(frozen=True)
class Warning:
    """危険な組み合わせの報せ. **画面が `code` を見て文言を決める**（§13）."""

    code: str
    message: str


def _port(raw: str) -> int:
    if not raw.isdigit() or not (1 <= int(raw) <= 65535):
        raise SettingInvalid(f"ポート番号として解釈できない: {raw}")
    return int(raw)


def _positive_int(raw: str) -> int:
    if not raw.isdigit() or int(raw) < 1:
        raise SettingInvalid(f"1 以上の整数である必要がある: {raw}")
    return int(raw)


def _choice(*allowed: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if raw not in allowed:
            raise SettingInvalid(f"{raw} は {allowed} のいずれかでなければならない")
        return raw

    return parse


def _timezone(raw: str) -> str:
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SettingInvalid(f"IANA タイムゾーンとして解釈できない: {raw}") from exc
    return raw


def _secret_key(raw: str) -> bytes:
    """パスワードではなく 256bit のランダム鍵を base64 で受け取る."""
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SettingInvalid("SECRET_KEY は base64 で与える") from exc
    if len(key) != 32:
        raise SettingInvalid(f"SECRET_KEY は 32 バイトである必要がある（{len(key)} バイト）")
    return key


SETTING_SPECS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        # DB 自身の置き場所を決めるので、DB に保存できない。
        SettingSpec("DATA_ROOT", "/data", Path, Tier.BOOTSTRAP),
        # compose.yaml が既にこのキーを app に渡している。
        SettingSpec("BROKER_SOCKET", "/run/mediaferry/broker.sock", Path, Tier.BOOTSTRAP),
        # マスター鍵を DB へ置けると、暗号文と復号鍵が同じバックアップに入る。
        SettingSpec("SECRET_KEY", None, _secret_key, Tier.BOOTSTRAP, secret=True),
        # Phase 4 で認証を入れるときに Argon2 ハッシュの保存先を別に作る。
        # 平文を app_setting へ置く経路は作らない。
        SettingSpec("AUTH_PASSWORD", None, str, Tier.BOOTSTRAP, secret=True),
        SettingSpec("BIND_HOST", "127.0.0.1", str, Tier.RESTART),
        # 名乗ってよいホスト名。**IP と localhost は既定で通す**（利用者が直に
        # 打つのは正当な使い方）。ホスト名だけを明示の許可制にして、DNS
        # rebinding の経路を閉じる（§14）。
        SettingSpec("TRUSTED_HOSTS", "", str, Tier.RESTART),
        SettingSpec("HTTP_PORT", "8080", _port, Tier.RESTART),
        SettingSpec("UPLOAD_CONCURRENCY", "2", _positive_int, Tier.RUNTIME),
        SettingSpec("UPLOAD_TIMEOUT_SECONDS", "86400", _positive_int, Tier.RUNTIME),
        SettingSpec("UPLOAD_MAX_ATTEMPTS", "3", _positive_int, Tier.RUNTIME),
        SettingSpec("AUTO_IMPORT", "trusted", _choice("trusted", "off"), Tier.RUNTIME),
        # 既定値を置かない。UTC を既定にすると force_offset が補正にならないまま
        # 誤った時刻で確定する（§12.2）。
        SettingSpec("DEFAULT_TIMEZONE", None, _timezone, Tier.RUNTIME),
        SettingSpec(
            "LOG_LEVEL", "info", _choice("debug", "info", "warning", "error"), Tier.RUNTIME
        ),
    )
}


def bootstrap_data_root(env: Mapping[str, str]) -> Path:
    """DB を開く前に要る値. env と既定値だけで決まる."""
    return Path(env.get(ENV_PREFIX + "DATA_ROOT", SETTING_SPECS["DATA_ROOT"].default))


class SettingsService:
    def __init__(self, conn: sqlite3.Connection, env: Mapping[str, str]) -> None:
        self._conn = conn
        self._env = env

    def _raw(self, key: str) -> tuple[str | None, str]:
        spec = SETTING_SPECS[key]
        from_env = self._env.get(ENV_PREFIX + key)
        if from_env is not None:
            return from_env, "env"
        # BOOTSTRAP は DB を見ない。書けないだけでなく、読みもしない
        # （書けてしまった行が後から効くことを防ぐ）。
        if spec.tier is not Tier.BOOTSTRAP:
            row = self._conn.execute(
                "SELECT value FROM app_setting WHERE key = ?", (key,)
            ).fetchone()
            if row is not None:
                return row["value"], "db"
        return spec.default, "default"

    def describe_all(self) -> list[SettingValue]:
        out = []
        for key, spec in SETTING_SPECS.items():
            raw, source = self._raw(key)
            value = MASK if (spec.secret and raw is not None) else raw
            locked = source == "env"
            out.append(
                SettingValue(
                    key=key,
                    value=value,
                    source=source,
                    locked=locked,
                    tier=spec.tier,
                    writable=not locked and spec.tier is not Tier.BOOTSTRAP,
                )
            )
        return out

    def set(self, key: str, value: str) -> Tier:
        """保存して、その値がいつ効くかを返す."""
        spec = SETTING_SPECS.get(key)
        if spec is None:
            raise SettingInvalid(f"未知の設定キー: {key}")
        if spec.tier is Tier.BOOTSTRAP:
            raise SettingLocked(f"{key} は env でのみ設定できる（DB には保存しない）")
        if ENV_PREFIX + key in self._env:
            raise SettingLocked(f"{key} は環境変数で固定されている")
        spec.parse(value)
        self._conn.execute(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, now_iso()),
        )
        return spec.tier

    def snapshot(self) -> Settings:
        parsed: dict[str, Any] = {}
        for key, spec in SETTING_SPECS.items():
            raw, _ = self._raw(key)
            parsed[key] = None if raw is None else spec.parse(raw)
        return Settings(
            data_root=parsed["DATA_ROOT"],
            broker_socket=parsed["BROKER_SOCKET"],
            bind_host=parsed["BIND_HOST"],
            http_port=parsed["HTTP_PORT"],
            auth_password=parsed["AUTH_PASSWORD"],
            secret_key=parsed["SECRET_KEY"],
            upload_concurrency=parsed["UPLOAD_CONCURRENCY"],
            upload_timeout_seconds=parsed["UPLOAD_TIMEOUT_SECONDS"],
            upload_max_attempts=parsed["UPLOAD_MAX_ATTEMPTS"],
            auto_import=parsed["AUTO_IMPORT"],
            default_timezone=parsed["DEFAULT_TIMEZONE"],
            log_level=parsed["LOG_LEVEL"],
            trusted_hosts=parsed["TRUSTED_HOSTS"],
        )


def startup_warnings(settings: Settings) -> list[Warning]:
    """危険な組み合わせを起動ログと UI バナーに出す.

    認証は必須にしない（LAN 内で無設定で使えることを優先する）が、意図せず
    公開している状態には気づけるようにする。**画面が文言を決められるよう
    `code` を添える**（§13）。
    """
    warnings: list[Warning] = []
    if settings.auth_password is None and not _is_loopback(settings.bind_host):
        warnings.append(
            Warning(
                code="unauthenticated_exposure",
                message=(
                    f"認証が無効なまま {settings.bind_host} で待ち受けている。"
                    "LAN の他の端末から操作できる状態になっている。"
                ),
            )
        )
    return warnings


def _is_loopback(host: str) -> bool:
    if host in {"localhost"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
