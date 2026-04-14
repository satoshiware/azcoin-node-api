import os

from fastapi.testclient import TestClient

# Set before importing app/settings so middleware uses this token.
os.environ["AZ_API_DEV_TOKEN"] = "testtoken"
os.environ["AUTH_MODE"] = "dev_token"


def test_tx_send_calls_sendrawtransaction():
    from node_api.main import app

    class FakeRPC:
        def call(self, method, params):  # noqa: ANN001
            assert method == "sendrawtransaction"
            assert params == ["deadbeef"]
            return "00" * 32

    # Override the FastAPI dependency to avoid needing BTC_RPC_* env vars.
    from node_api.routes.v1.tx import send as tx_send  # noqa: E402

    app.dependency_overrides[tx_send.get_bitcoin_rpc] = lambda: FakeRPC()

    client = TestClient(app)
    r = client.post(
        "/v1/tx/send",
        json={"hex": "deadbeef"},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["txid"] == "00" * 32

    # Cleanup for other tests
    app.dependency_overrides.clear()


def test_tx_send_returns_400_for_rpc_json_error():
    from node_api.main import app
    from node_api.services.bitcoin_rpc import BitcoinRpcResponseError

    class FakeRPC:
        def call(self, method, params):  # noqa: ANN001
            assert method == "sendrawtransaction"
            assert params == ["deadbeef"]
            raise BitcoinRpcResponseError(code=-22, message="TX decode failed")

    from node_api.routes.v1.tx import send as tx_send  # noqa: E402

    app.dependency_overrides[tx_send.get_bitcoin_rpc] = lambda: FakeRPC()

    client = TestClient(app)
    r = client.post(
        "/v1/tx/send",
        json={"hex": "deadbeef"},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == {
        "code": "TX_REJECTED",
        "message": "TX decode failed",
        "rpc_code": -22,
    }

    app.dependency_overrides.clear()


def test_tx_send_returns_503_when_rpc_not_configured(monkeypatch):
    from node_api.main import app
    from node_api.settings import get_settings

    monkeypatch.delenv("BTC_RPC_URL", raising=False)
    monkeypatch.delenv("BTC_RPC_COOKIE_FILE", raising=False)
    monkeypatch.delenv("BTC_RPC_USER", raising=False)
    monkeypatch.delenv("BTC_RPC_PASSWORD", raising=False)
    get_settings.cache_clear()

    app.dependency_overrides.clear()
    client = TestClient(app)

    r = client.post(
        "/v1/tx/send",
        json={"hex": "deadbeef"},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "BTC_RPC_NOT_CONFIGURED"
    get_settings.cache_clear()


def test_tx_send_respects_api_v1_prefix(monkeypatch):
    from node_api.settings import get_settings

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("API_V1_PREFIX", "/api")
    get_settings.cache_clear()

    from node_api.main import create_app  # noqa: E402
    from node_api.routes.v1.tx import send as tx_send  # noqa: E402

    class FakeRPC:
        def call(self, method, params):  # noqa: ANN001
            assert method == "sendrawtransaction"
            assert params == ["deadbeef"]
            return "11" * 32

    local_app = create_app()
    local_app.dependency_overrides[tx_send.get_bitcoin_rpc] = lambda: FakeRPC()
    client = TestClient(local_app)

    ok = client.post(
        "/api/tx/send",
        json={"hex": "deadbeef"},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["txid"] == "11" * 32

    old_prefix = client.post(
        "/v1/tx/send",
        json={"hex": "deadbeef"},
        headers={"Authorization": "Bearer testtoken"},
    )
    assert old_prefix.status_code == 404
    get_settings.cache_clear()
