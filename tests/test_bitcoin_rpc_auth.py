"""Tests for Bitcoin RPC auth: cookie file vs env user/password."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from node_api.services.bitcoin_rpc import (
    BitcoinRPC,
    BitcoinRpcResponseError,
    BitcoinRpcTransportError,
)


def test_from_settings_uses_cookie_file_when_set(monkeypatch):
    """Cookie auth path chosen when BTC_RPC_COOKIE_FILE is set."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cookie", delete=False) as f:
        f.write("__cookie__:secret-rpc-pass\n")
        cookie_path = f.name
    try:
        monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
        monkeypatch.setenv("BTC_RPC_COOKIE_FILE", cookie_path)
        monkeypatch.delenv("BTC_RPC_USER", raising=False)
        monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)

        from node_api.settings import get_settings

        get_settings.cache_clear()
        client = BitcoinRPC.from_settings()
        assert client._auth == ("__cookie__", "secret-rpc-pass")
        assert client._url == "http://127.0.0.1:8332"
    finally:
        Path(cookie_path).unlink(missing_ok=True)


def test_from_settings_falls_back_to_env_user_password_when_no_cookie(monkeypatch):
    """Fallback to env user/password when cookie file is absent."""
    monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
    monkeypatch.setenv("BTC_RPC_USER", "rpcuser")
    monkeypatch.setenv("BTC_RPC_PASSWORD", "rpcpass")
    monkeypatch.delenv("BTC_RPC_COOKIE_FILE", raising=False)

    from node_api.settings import get_settings

    get_settings.cache_clear()
    client = BitcoinRPC.from_settings()
    assert client._auth == ("rpcuser", "rpcpass")


def test_from_settings_raises_when_not_configured(monkeypatch):
    """Raises when no URL or credentials."""
    monkeypatch.delenv("BTC_RPC_URL", raising=False)
    monkeypatch.delenv("BTC_RPC_COOKIE_FILE", raising=False)
    monkeypatch.delenv("BTC_RPC_USER", raising=False)
    monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)

    from node_api.settings import get_settings

    get_settings.cache_clear()
    with pytest.raises(BitcoinRpcResponseError) as exc_info:
        BitcoinRPC.from_settings()
    assert "not configured" in exc_info.value.message.lower()


def test_from_settings_cookie_file_missing_raises_transport_error(monkeypatch):
    """Missing cookie file raises BitcoinRpcTransportError (maps to BTC_RPC_UNAVAILABLE)."""
    monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
    monkeypatch.setenv("BTC_RPC_COOKIE_FILE", "/nonexistent/path/.cookie")
    monkeypatch.delenv("BTC_RPC_USER", raising=False)
    monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)

    from node_api.settings import get_settings

    get_settings.cache_clear()
    with pytest.raises(BitcoinRpcTransportError) as exc_info:
        BitcoinRPC.from_settings()
    assert "not found" in exc_info.value.message.lower()


def test_from_settings_cookie_file_malformed_raises_transport_error(monkeypatch):
    """Malformed cookie file raises BitcoinRpcTransportError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cookie", delete=False) as f:
        f.write("no-colon-here\n")
        cookie_path = f.name
    try:
        monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
        monkeypatch.setenv("BTC_RPC_COOKIE_FILE", cookie_path)
        monkeypatch.delenv("BTC_RPC_USER", raising=False)
        monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)

        from node_api.settings import get_settings

        get_settings.cache_clear()
        with pytest.raises(BitcoinRpcTransportError) as exc_info:
            BitcoinRPC.from_settings()
        assert "malformed" in exc_info.value.message.lower()
    finally:
        Path(cookie_path).unlink(missing_ok=True)


def test_from_settings_cookie_file_empty_raises_transport_error(monkeypatch):
    """Empty cookie file raises BitcoinRpcTransportError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cookie", delete=False) as f:
        f.write("")
        cookie_path = f.name
    try:
        monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
        monkeypatch.setenv("BTC_RPC_COOKIE_FILE", cookie_path)
        monkeypatch.delenv("BTC_RPC_USER", raising=False)
        monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)

        from node_api.settings import get_settings

        get_settings.cache_clear()
        with pytest.raises(BitcoinRpcTransportError) as exc_info:
            BitcoinRPC.from_settings()
        assert "empty" in exc_info.value.message.lower()
    finally:
        Path(cookie_path).unlink(missing_ok=True)


def test_get_btc_rpc_cookie_unavailable_returns_502(monkeypatch):
    """Cookie file error from get_btc_rpc returns 502 BTC_RPC_UNAVAILABLE."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("BTC_RPC_URL", "http://127.0.0.1:8332")
    monkeypatch.setenv("BTC_RPC_COOKIE_FILE", "/nonexistent/.cookie")
    monkeypatch.delenv("BTC_RPC_USER", raising=False)
    monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)

    from node_api.settings import get_settings

    get_settings.cache_clear()

    from node_api import main as main_module

    client = TestClient(main_module.create_app())
    r = client.get("/v1/btc/node/info", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "BTC_RPC_UNAVAILABLE"
