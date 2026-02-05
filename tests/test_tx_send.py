import os

# Set before importing app/settings so middleware uses this token.
os.environ["AZ_API_DEV_TOKEN"] = "testtoken"
os.environ["AUTH_MODE"] = "dev_token"

from fastapi.testclient import TestClient  # noqa: E402

from node_api.main import app  # noqa: E402


def test_tx_send_calls_sendrawtransaction():
    class FakeRPC:
        def call(self, method, params):  # noqa: ANN001
            assert method == "sendrawtransaction"
            assert params == ["deadbeef"]
            return "00" * 32

    # Override the FastAPI dependency to avoid needing BTC_RPC_* env vars.
    from node_api.routes.v1.tx import send as tx_send  # noqa: E402

    app.dependency_overrides[tx_send.BitcoinRPC.from_settings] = lambda: FakeRPC()

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
