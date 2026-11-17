import pytest

from app.config import load_settings


def test_defaults_to_offline_without_credentials():
    settings = load_settings({})
    assert settings.mode == "offline"
    assert settings.api_configured is False
    assert settings.credentials is None
    assert settings.app_host == "127.0.0.1"


def test_rejects_partial_credentials():
    with pytest.raises(ValueError, match="incomplete"):
        load_settings({"OKX_API_KEY": "key"})


def test_requires_explicit_region_endpoints_when_credentials_present():
    env = {
        "OKX_API_KEY": "key",
        "OKX_API_SECRET": "secret",
        "OKX_API_PASSPHRASE": "passphrase",
    }
    with pytest.raises(ValueError, match="OKX_BASE_URL"):
        load_settings(env)


def test_accepts_complete_read_only_configuration():
    env = {
        "OKX_API_KEY": "key",
        "OKX_API_SECRET": "secret",
        "OKX_API_PASSPHRASE": "passphrase",
        "OKX_BASE_URL": "https://openapi.okx.com",
        "OKX_PRIVATE_WS_URL": "wss://ws.okx.com/ws/v5/private",
    }
    settings = load_settings(env)
    assert settings.mode == "configured"
    assert settings.api_configured is True
    assert settings.credentials.api_key == "key"
    assert settings.okx_base_url == "https://openapi.okx.com"


def test_rejects_non_loopback_dashboard_binding():
    with pytest.raises(ValueError, match="loopback"):
        load_settings({"APP_HOST": "0.0.0.0"})


def test_credentials_are_not_in_repr():
    env = {
        "OKX_API_KEY": "private-key-value",
        "OKX_API_SECRET": "private-secret-value",
        "OKX_API_PASSPHRASE": "private-passphrase-value",
        "OKX_BASE_URL": "https://openapi.okx.com",
        "OKX_PRIVATE_WS_URL": "wss://ws.okx.com/ws/v5/private",
    }
    settings = load_settings(env)
    assert "private-key-value" not in repr(settings)
    assert "private-secret-value" not in repr(settings)
    assert "private-passphrase-value" not in repr(settings)
