import pytest

from app.endpoints import validate_private_ws_url, validate_rest_base_url


@pytest.mark.parametrize("url", [
    "https://openapi.okx.com",
    "https://us.okx.com",
    "https://eea.okx.com",
])
def test_accepts_official_rest_hosts(url):
    assert validate_rest_base_url(url) == url


@pytest.mark.parametrize("url", [
    "wss://ws.okx.com/ws/v5/private",
    "wss://wsus.okx.com/ws/v5/private",
    "wss://wseea.okx.com/ws/v5/private",
])
def test_accepts_official_private_ws_hosts(url):
    assert validate_private_ws_url(url) == url


@pytest.mark.parametrize("url", [
    "http://openapi.okx.com",
    "https://evil.example",
    "https://openapi.okx.com.attacker.example",
    "https://user:pass@openapi.okx.com",
    "https://openapi.okx.com:8443",
    "https://openapi.okx.com/api/v5",
    "https://openapi.okx.com?redirect=evil",
])
def test_rejects_untrusted_rest_urls(url):
    with pytest.raises(ValueError):
        validate_rest_base_url(url)


@pytest.mark.parametrize("url", [
    "ws://ws.okx.com/ws/v5/private",
    "wss://evil.example/ws/v5/private",
    "wss://ws.okx.com/ws/v5/public",
    "wss://ws.okx.com:8443/ws/v5/private",
])
def test_rejects_untrusted_private_ws_urls(url):
    with pytest.raises(ValueError):
        validate_private_ws_url(url)
