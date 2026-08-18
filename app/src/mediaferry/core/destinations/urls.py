"""転送先の接続エンドポイントの検証（§12.4）.

`base_url` は mediaferry が実際に接続する先で、`public_url` は画面のリンクに
描画するだけの値。**両方に同じ検証を掛ける。** 片方だけ緩めると、
`javascript:` を保存できる欄が残る。

正規化して保存するのは、同じ宛先が違う文字列で 2 通り保存されるのを防ぐため。
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}


class EndpointRejected(ValueError):
    """スキーム・userinfo・fragment・ホストのいずれかが要件を満たさない."""


def normalize_endpoint(raw: str) -> str:
    """受理した URL を正規形で返す. 受理できなければ送出する."""
    text = raw.strip()
    if not text:
        raise EndpointRejected("URL が空")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise EndpointRejected(f"http か https でなければならない: {scheme or '(スキーム無し)'}")
    if parts.username is not None or parts.password is not None:
        # URL に埋めた資格情報は、ログにも画面にも出る経路になる。
        raise EndpointRejected("URL に userinfo を含めない")
    if parts.fragment:
        raise EndpointRejected("URL に fragment を含めない")
    if parts.query:
        raise EndpointRejected("URL に query を含めない")
    if not parts.hostname:
        raise EndpointRejected("ホスト名が無い")
    try:
        port = parts.port
    except ValueError as exc:
        # 範囲外・数値でないポートは urlsplit が読むときに落ちる。
        raise EndpointRejected(f"ポート番号として解釈できない: {parts.netloc}") from exc

    # `hostname` は urlsplit が小文字にして返す（大文字のホスト名はここで揃う）。
    host = parts.hostname
    if ":" in host:
        # IPv6 は括弧で囲み直す。素で組むと `http://::1:2283` になって壊れる。
        host = f"[{host}]"
    if port is not None and port != DEFAULT_PORTS[scheme]:
        host = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, host, path, "", ""))
