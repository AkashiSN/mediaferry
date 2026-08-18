import pytest

from mediaferry.core.destinations.urls import EndpointRejected, normalize_endpoint


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://immich.invalid:2283", "http://immich.invalid:2283"),
        ("http://immich.invalid:2283/", "http://immich.invalid:2283"),
        ("http://immich.invalid:2283/api/", "http://immich.invalid:2283/api"),
        ("HTTP://Immich.Invalid:2283", "http://immich.invalid:2283"),
        ("https://immich.invalid", "https://immich.invalid"),
    ],
)
def test_accepted_endpoints_are_normalised(raw, expected):
    assert normalize_endpoint(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://immich.invalid",
        "//immich.invalid",
        "immich.invalid:2283",
        "",
        "   ",
    ],
)
def test_only_http_and_https_are_accepted(raw):
    with pytest.raises(EndpointRejected):
        normalize_endpoint(raw)


def test_userinfo_is_refused():
    # 資格情報を URL に埋めると、ログと画面の両方に出る経路ができる。
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http://user:pass@immich.invalid:2283")


def test_a_fragment_is_refused():
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http://immich.invalid:2283/#/photos")


def test_a_query_is_refused():
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http://immich.invalid:2283/?token=x")


def test_a_missing_host_is_refused():
    with pytest.raises(EndpointRejected):
        normalize_endpoint("http:///api")


def test_the_default_port_is_not_written_back():
    # 既定ポートを明示すると、同じ宛先が別の文字列で 2 通り保存される。
    assert normalize_endpoint("http://immich.invalid:80") == "http://immich.invalid"
    assert normalize_endpoint("https://immich.invalid:443") == "https://immich.invalid"


def test_a_non_default_port_is_kept():
    assert normalize_endpoint("http://immich.invalid:2283") == "http://immich.invalid:2283"


def test_an_ipv6_host_keeps_its_brackets():
    assert normalize_endpoint("http://[::1]:2283") == "http://[::1]:2283"
    assert normalize_endpoint("http://[::1]") == "http://[::1]"


@pytest.mark.parametrize("raw", ["http://immich.invalid:99999", "http://immich.invalid:abc"])
def test_an_unusable_port_is_refused(raw):
    # urlsplit の ValueError をそのまま外へ出さない（400 に正規化できない）。
    with pytest.raises(EndpointRejected):
        normalize_endpoint(raw)
