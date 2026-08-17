"""現在時刻の単一の出所.

DB に入る時刻はすべてここを通す。テストは `freeze` で固定した値を使い、
「1 秒ずれたから落ちる」テストを書かなくて済むようにする。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """UTC の ISO-8601 文字列にする. DB の時刻表現はこれだけ."""
    return dt.astimezone(UTC).isoformat()


def now_iso() -> str:
    return iso(utcnow())
