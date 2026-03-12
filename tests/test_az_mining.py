"""Tests for AZCoin mining template and status endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from node_api.services.azcoin_rpc import AzcoinRpcResponseError, AzcoinRpcTransportError
from node_api.settings import get_settings

AUTH_HEADER = {"Authorization": "Bearer testtoken"}


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_RPC_URL", "http://127.0.0.1:19332")
    monkeypatch.setenv("AZ_RPC_USER", "user")
    monkeypatch.setenv("AZ_RPC_PASSWORD", "pass")
    monkeypatch.setenv("AZ_EXPECTED_CHAIN", "main")
    get_settings.cache_clear()

    from node_api import main as main_module

    return TestClient(main_module.create_app())


# --- template/current ---


@pytest.mark.parametrize("path", ["/v1/az/mining/template/current", "/v1/az/mining/status"])
def test_az_mining_endpoints_require_auth(monkeypatch, path: str):
    client = _make_client(monkeypatch)
    r = client.get(path)
    assert r.status_code == 401


def test_template_current_success(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    curtime = 1700000000

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        assert method == "getblocktemplate"
        assert params == [{}]
        return {
            "previousblockhash": "0000000000000000000123456789abcdef0123456789abcdef0123456789abcdef",
            "version": 536870912,
            "bits": "1d00ffff",
            "curtime": curtime,
            "height": 12345,
        }

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mining/template/current", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"job_id", "prev_hash", "version", "nbits", "ntime", "clean_jobs", "height"}
    assert body["prev_hash"] == "0000000000000000000123456789abcdef0123456789abcdef0123456789abcdef"
    assert body["version"] == 536870912
    assert body["nbits"] == "1d00ffff"
    assert body["ntime"] == hex(curtime)[2:]
    assert body["clean_jobs"] is True
    assert body["height"] == 12345
    assert len(body["job_id"]) == 16
    assert all(c in "0123456789abcdef" for c in body["job_id"])


def test_template_current_job_id_deterministic(monkeypatch):
    """Same template fields produce same job_id."""
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        return {
            "previousblockhash": "aa" + "00" * 31,
            "version": 2,
            "bits": "1d00ffff",
            "curtime": 1000,
            "height": 1,
        }

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r1 = client.get("/v1/az/mining/template/current", headers=AUTH_HEADER)
    r2 = client.get("/v1/az/mining/template/current", headers=AUTH_HEADER)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]


def test_template_current_returns_502_on_rpc_failure(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcTransportError("network down")

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get("/v1/az/mining/template/current", headers=AUTH_HEADER)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_UNAVAILABLE"


def test_template_current_returns_502_on_invalid_payload(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        return {"previousblockhash": "abc", "version": 1}
        # missing bits, curtime, height

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mining/template/current", headers=AUTH_HEADER)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_INVALID_PAYLOAD"


def test_template_current_returns_502_on_non_dict_result(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        return None

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mining/template/current", headers=AUTH_HEADER)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "AZ_RPC_UNAVAILABLE"


def test_template_current_returns_503_on_wrong_chain(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module
    from node_api.services.azcoin_rpc import AzcoinRpcWrongChainError

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcWrongChainError(expected_chain="main", actual_chain="regtest")

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mining/template/current", headers=AUTH_HEADER)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "AZ_WRONG_CHAIN"


# --- status ---


def test_status_healthy(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {"chain": "main", "blocks": 1000, "headers": 1000}
        if method == "getblocktemplate":
            return {
                "previousblockhash": "00" * 32,
                "version": 2,
                "bits": "1d00ffff",
                "curtime": 1700000000,
                "height": 1001,
            }
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mining/status", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert body["rpc_healthy"] is True
    assert body["template_ok"] is True
    assert body["chain"] == "main"
    assert body["blocks"] == 1000
    assert body["headers"] == 1000
    assert "error" not in body


def test_status_unhealthy_rpc_not_configured(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_RPC_URL", "")
    monkeypatch.setenv("AZ_RPC_USER", "")
    monkeypatch.setenv("AZ_RPC_PASSWORD", "")
    get_settings.cache_clear()

    from node_api import main as main_module

    client = TestClient(main_module.create_app())

    r = client.get("/v1/az/mining/status", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert body["rpc_healthy"] is False
    assert body["template_ok"] is False
    assert body["chain"] is None
    assert body["blocks"] is None
    assert body["headers"] is None
    assert body["error"] == "AZ_RPC_NOT_CONFIGURED"


def test_status_unhealthy_rpc_unavailable(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    def boom(self, method: str, params=None):  # noqa: ANN001
        raise AzcoinRpcTransportError("connection refused")

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", boom, raising=True)

    r = client.get("/v1/az/mining/status", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert body["rpc_healthy"] is False
    assert body["template_ok"] is False
    assert body["chain"] is None
    assert body["blocks"] is None
    assert body["headers"] is None
    assert body["error"] == "AZ_RPC_UNAVAILABLE"


def test_status_unhealthy_wrong_chain(monkeypatch):
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module
    from node_api.services.azcoin_rpc import AzcoinRpcWrongChainError

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            raise AzcoinRpcWrongChainError(expected_chain="main", actual_chain="regtest")
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mining/status", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert body["rpc_healthy"] is True
    assert body["template_ok"] is False
    assert body["chain"] is None
    assert body["blocks"] is None
    assert body["headers"] is None
    assert body["error"] == "AZ_WRONG_CHAIN"


def test_status_template_ok_false_when_getblocktemplate_fails(monkeypatch):
    """template_ok is False when getblockchaininfo succeeds but getblocktemplate fails."""
    client = _make_client(monkeypatch)

    from node_api.routes.v1 import az_mining as az_mining_module

    def fake_call(self, method: str, params=None):  # noqa: ANN001
        if method == "getblockchaininfo":
            return {"chain": "main", "blocks": 500, "headers": 500}
        if method == "getblocktemplate":
            raise AzcoinRpcResponseError(code=-32603, message="Node is not synced")
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(az_mining_module.AzcoinRpcClient, "call", fake_call, raising=True)

    r = client.get("/v1/az/mining/status", headers=AUTH_HEADER)
    assert r.status_code == 200
    body = r.json()
    assert body["rpc_healthy"] is True
    assert body["template_ok"] is False
    assert body["chain"] == "main"
    assert body["blocks"] == 500
    assert body["headers"] == 500
