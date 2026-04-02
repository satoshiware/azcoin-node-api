from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from node_api.settings import get_settings


def _make_client(monkeypatch, db_path: Path, token: str = "testtoken-123") -> TestClient:
    import node_api.services.share_ledger as _sl

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("AZ_SHARE_DB_PATH", str(db_path))
    monkeypatch.setenv("AZ_MINING_DB_PATH", str(db_path))
    monkeypatch.setenv("AZ_NODE_API_TOKEN", token)
    get_settings.cache_clear()

    # Reset share ledger so init_db re-initializes with the temp DB path.
    # on_event("startup") may not fire under TestClient in newer Starlette.
    monkeypatch.setattr(_sl, "_CONN", None)
    monkeypatch.setattr(_sl, "_DB_PATH", None)
    _sl.init_db()

    from node_api import main as main_module

    app = main_module.create_app()
    return TestClient(app)


def _payload(
    worker: str = "BenC",
    *,
    ts: int = 1700000000,
    accepted: bool = True,
    duplicate: bool = False,
    share_diff: float = 1.0,
) -> dict[str, object]:
    return {
        "ts": ts,
        "worker": worker,
        "job_id": "job-42",
        "accepted": accepted,
        "duplicate": duplicate,
        "share_diff": share_diff,
        "reason": "",
        "extranonce2": "0a0b0c0d",
        "ntime": "65a1bc2f",
        "nonce": "deadbeef",
    }


def test_post_share_ok_with_token(monkeypatch, tmp_path: Path) -> None:
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    response = client.post(
        "/v1/mining/share",
        json=_payload(),
        headers={"Authorization": "Bearer testtoken-123"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_post_share_unauthorized_without_token_header(monkeypatch, tmp_path: Path) -> None:
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    response = client.post("/v1/mining/share", json=_payload())
    assert response.status_code == 401


def test_workers_endpoints_after_post(monkeypatch, tmp_path: Path) -> None:
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    post_resp = client.post(
        "/v1/mining/share",
        json=_payload(),
        headers={"Authorization": "Bearer testtoken-123"},
    )
    assert post_resp.status_code == 200

    workers_resp = client.get(
        "/v1/mining/workers",
        headers={"Authorization": "Bearer testtoken-123"},
    )
    assert workers_resp.status_code == 200
    workers = workers_resp.json()
    assert any(w["name"] == "BenC" and w["accepted"] == 1 for w in workers)

    worker_resp = client.get(
        "/v1/mining/workers/BenC",
        headers={"Authorization": "Bearer testtoken-123"},
    )
    assert worker_resp.status_code == 200
    worker = worker_resp.json()
    assert worker["name"] == "BenC"
    assert worker["accepted"] == 1
    assert worker["last_seen"] > 0


def test_worker_telemetry_rates(monkeypatch, tmp_path: Path) -> None:
    """Derived rate fields are computed correctly from accepted/rejected/dup counts."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    # 2 accepted (non-dup) + 1 accepted duplicate + 2 rejected
    # Expected: accepted=3, rejected=2, dup=1, total=5
    for i in range(2):
        assert client.post("/v1/mining/share", json=_payload("w1", ts=now - i), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("w1", ts=now - 2, duplicate=True), headers=headers).status_code == 200
    for i in range(2):
        assert client.post("/v1/mining/share", json=_payload("w1", ts=now - 3 - i, accepted=False), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    w1 = next(w for w in resp.json() if w["name"] == "w1")

    assert w1["accepted"] == 3
    assert w1["rejected"] == 2
    assert w1["dup"] == 1
    assert w1["total_shares"] == 5
    assert w1["acceptance_rate"] == pytest.approx(3 / 5)
    assert w1["rejection_rate"] == pytest.approx(2 / 5)
    assert w1["duplicate_rate"] == pytest.approx(1 / 5)
    assert w1["last_share_ts"] == w1["last_seen"]
    assert w1["seconds_since_last_share"] >= 0
    assert w1["seconds_since_last_share"] < 10
    assert isinstance(w1["recent_share_counts"], dict)
    assert set(w1["recent_share_counts"].keys()) == {"5m", "15m", "1h"}


def test_worker_recent_share_count_windows(monkeypatch, tmp_path: Path) -> None:
    """recent_share_counts windows count only shares within their time boundary."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    # 1 share at now → within 5m, 15m, 1h
    assert client.post("/v1/mining/share", json=_payload("w2", ts=now), headers=headers).status_code == 200
    # 1 share at now-600 → outside 5m, within 15m and 1h
    assert client.post("/v1/mining/share", json=_payload("w2", ts=now - 600), headers=headers).status_code == 200
    # 1 share at now-2000 → outside 5m and 15m, within 1h
    assert client.post("/v1/mining/share", json=_payload("w2", ts=now - 2000), headers=headers).status_code == 200
    # 1 share at now-7200 → outside all windows
    assert client.post("/v1/mining/share", json=_payload("w2", ts=now - 7200), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    w2 = next(w for w in resp.json() if w["name"] == "w2")

    assert w2["recent_share_counts"]["5m"] == 1
    assert w2["recent_share_counts"]["15m"] == 2
    assert w2["recent_share_counts"]["1h"] == 3


def test_worker_detail_includes_telemetry_and_recent_shares(monkeypatch, tmp_path: Path) -> None:
    """get_worker detail returns both recent_shares and new derived telemetry fields."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("w3", ts=now), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers/w3", headers=headers)
    assert resp.status_code == 200
    w3 = resp.json()

    # Original fields preserved
    assert w3["name"] == "w3"
    assert w3["accepted"] == 1
    assert "recent_shares" in w3
    assert len(w3["recent_shares"]) == 1

    # New derived fields present and correct
    assert w3["total_shares"] == 1
    assert w3["acceptance_rate"] == pytest.approx(1.0)
    assert w3["rejection_rate"] == pytest.approx(0.0)
    assert w3["duplicate_rate"] == pytest.approx(0.0)
    assert w3["last_share_ts"] == w3["last_seen"]
    assert w3["seconds_since_last_share"] >= 0
    assert w3["recent_share_counts"]["5m"] == 1
    assert w3["recent_share_counts"]["15m"] == 1
    assert w3["recent_share_counts"]["1h"] == 1


def test_worker_identity_with_dot(monkeypatch, tmp_path: Path) -> None:
    """Worker 'alice.rig1' is parsed into username='alice', miner_name='rig1'."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    w = next(w for w in resp.json() if w["name"] == "alice.rig1")
    assert w["raw_worker"] == "alice.rig1"
    assert w["username"] == "alice"
    assert w["miner_name"] == "rig1"


def test_worker_identity_without_dot(monkeypatch, tmp_path: Path) -> None:
    """Worker without dot uses full name as username, miner_name=''."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}

    assert client.post("/v1/mining/share", json=_payload("BenC"), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    w = next(w for w in resp.json() if w["name"] == "BenC")
    assert w["raw_worker"] == "BenC"
    assert w["username"] == "BenC"
    assert w["miner_name"] == ""


def test_hashrate_miner_positive_for_recent_accepted(monkeypatch, tmp_path: Path) -> None:
    """hashrate_miner > 0 when accepted shares exist within the 15m window."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now, share_diff=100.0), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    w = next(w for w in resp.json() if w["name"] == "alice.rig1")
    expected_hr = (100.0 * 4294967296.0) / 900.0
    assert w["hashrate_miner"] == pytest.approx(expected_hr)
    assert w["hashrate_miner"] > 0


def test_hashrate_miner_zero_for_old_shares(monkeypatch, tmp_path: Path) -> None:
    """hashrate_miner is 0.0 when accepted shares are outside the 15m window."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("old_miner", ts=now - 2000, share_diff=100.0), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    w = next(w for w in resp.json() if w["name"] == "old_miner")
    assert w["hashrate_miner"] == 0.0


def test_user_summary_aggregates_across_miners(monkeypatch, tmp_path: Path) -> None:
    """user_summary on worker detail aggregates all miners with the same username."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    # alice.rig1: 1 accepted (diff=50), 1 rejected
    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now, share_diff=50.0), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now - 1, accepted=False), headers=headers).status_code == 200
    # alice.rig2: 1 accepted (diff=75), 1 duplicate accepted (diff=25)
    assert client.post("/v1/mining/share", json=_payload("alice.rig2", ts=now - 2, share_diff=75.0), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("alice.rig2", ts=now - 3, share_diff=25.0, duplicate=True), headers=headers).status_code == 200

    # Detail for alice.rig1
    resp = client.get("/v1/mining/workers/alice.rig1", headers=headers)
    assert resp.status_code == 200
    w = resp.json()

    # Existing fields still present
    assert "recent_shares" in w
    assert w["total_shares"] == 2
    assert w["acceptance_rate"] == pytest.approx(0.5)

    # user_summary
    us = w["user_summary"]
    assert us["username"] == "alice"
    assert us["miner_count"] == 2
    # total_accepted: rig1=1, rig2=2 → 3
    assert us["total_accepted"] == 3
    # total_rejected: rig1=1, rig2=0 → 1
    assert us["total_rejected"] == 1
    assert us["total_dup"] == 1
    assert us["total_shares"] == 4
    # hashrate_user >= hashrate_miner (user has more accepted shares)
    assert us["hashrate_user"] >= w["hashrate_miner"]
    expected_user_hr = ((50.0 + 75.0 + 25.0) * 4294967296.0) / 900.0
    assert us["hashrate_user"] == pytest.approx(expected_user_hr)


def test_list_users_returns_aggregated_users(monkeypatch, tmp_path: Path) -> None:
    """GET /v1/mining/users returns aggregated summaries for multiple users."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    # Two users: alice (2 miners), bob (1 miner)
    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now, share_diff=50.0), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("alice.rig2", ts=now - 1, share_diff=30.0), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("bob", ts=now - 2, share_diff=10.0), headers=headers).status_code == 200

    resp = client.get("/v1/mining/users", headers=headers)
    assert resp.status_code == 200
    users_list = resp.json()
    assert len(users_list) == 2

    alice = next(u for u in users_list if u["username"] == "alice")
    assert alice["miner_count"] == 2
    assert alice["total_accepted"] == 2
    assert alice["total_rejected"] == 0
    assert alice["total_shares"] == 2
    assert alice["hashrate_user"] > 0
    assert alice["first_seen"] > 0
    assert alice["last_seen"] > 0
    assert alice["seconds_since_last_share"] >= 0

    bob = next(u for u in users_list if u["username"] == "bob")
    assert bob["miner_count"] == 1
    assert bob["total_accepted"] == 1
    assert bob["total_shares"] == 1


def test_user_detail_with_two_miners(monkeypatch, tmp_path: Path) -> None:
    """GET /v1/mining/users/{username} returns user summary with enriched miners list."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    # alice.rig1: 1 accepted (diff=50), 1 rejected
    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now, share_diff=50.0), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now - 1, accepted=False), headers=headers).status_code == 200
    # alice.rig2: 1 accepted (diff=75)
    assert client.post("/v1/mining/share", json=_payload("alice.rig2", ts=now - 2, share_diff=75.0), headers=headers).status_code == 200

    resp = client.get("/v1/mining/users/alice", headers=headers)
    assert resp.status_code == 200
    u = resp.json()

    assert u["username"] == "alice"
    assert u["miner_count"] == 2
    assert u["total_accepted"] == 2
    assert u["total_rejected"] == 1
    assert u["total_dup"] == 0
    assert u["total_shares"] == 3
    expected_hr = ((50.0 + 75.0) * 4294967296.0) / 900.0
    assert u["hashrate_user"] == pytest.approx(expected_hr)
    assert u["first_seen"] > 0
    assert u["last_seen"] > 0
    assert u["seconds_since_last_share"] >= 0

    # miners array contains both enriched workers
    assert "miners" in u
    assert len(u["miners"]) == 2
    miner_names = {m["name"] for m in u["miners"]}
    assert miner_names == {"alice.rig1", "alice.rig2"}
    for m in u["miners"]:
        assert "total_shares" in m
        assert "hashrate_miner" in m
        assert "username" in m
        assert "recent_share_counts" in m


def test_user_detail_unknown_returns_404(monkeypatch, tmp_path: Path) -> None:
    """GET /v1/mining/users/{username} returns 404 for unknown username."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}

    resp = client.get("/v1/mining/users/nonexistent", headers=headers)
    assert resp.status_code == 404


# ── Block / Reward tests ─────────────────────────────────────────────


def _block_payload(
    *,
    height: int = 1000,
    block_hash: str = "00000000abcdef1234567890abcdef1234567890abcdef1234567890abcdef12",
    reward: float = 50.0,
    worker: str = "alice.rig1",
    ts: int = 1700000000,
    confirmed: bool = False,
) -> dict[str, object]:
    return {
        "height": height,
        "block_hash": block_hash,
        "reward": reward,
        "worker": worker,
        "ts": ts,
        "confirmed": confirmed,
    }


def test_post_block_ok(monkeypatch, tmp_path: Path) -> None:
    """POST /v1/mining/block ingests a block find."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}

    resp = client.post("/v1/mining/block", json=_block_payload(), headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_post_block_unauthorized(monkeypatch, tmp_path: Path) -> None:
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    resp = client.post("/v1/mining/block", json=_block_payload())
    assert resp.status_code == 401


def test_post_block_upsert_on_duplicate_height(monkeypatch, tmp_path: Path) -> None:
    """Re-posting the same height updates the existing row (upsert)."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}

    assert client.post("/v1/mining/block", json=_block_payload(reward=50.0), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(reward=51.0), headers=headers).status_code == 200

    resp = client.get("/v1/mining/blocks", headers=headers)
    blocks = resp.json()
    assert len(blocks) == 1
    assert blocks[0]["reward"] == 51.0


def test_list_blocks_returns_found_blocks(monkeypatch, tmp_path: Path) -> None:
    """GET /v1/mining/blocks returns ingested blocks in height-desc order."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}

    assert client.post("/v1/mining/block", json=_block_payload(height=100, worker="alice.rig1", reward=50.0), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(height=200, worker="bob", reward=25.0), headers=headers).status_code == 200

    resp = client.get("/v1/mining/blocks", headers=headers)
    assert resp.status_code == 200
    blocks = resp.json()
    assert len(blocks) == 2
    assert blocks[0]["height"] == 200
    assert blocks[1]["height"] == 100

    assert blocks[0]["username"] == "bob"
    assert blocks[0]["miner_name"] == ""
    assert blocks[1]["username"] == "alice"
    assert blocks[1]["miner_name"] == "rig1"


def test_worker_rewards_total(monkeypatch, tmp_path: Path) -> None:
    """Workers show blocks_found and rewards_total from ingested block finds."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("alice.rig2", ts=now), headers=headers).status_code == 200

    assert client.post("/v1/mining/block", json=_block_payload(height=100, worker="alice.rig1", reward=50.0, ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(height=200, worker="alice.rig1", reward=25.0, ts=now), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    rig1 = next(w for w in resp.json() if w["name"] == "alice.rig1")
    rig2 = next(w for w in resp.json() if w["name"] == "alice.rig2")

    assert rig1["blocks_found"] == 2
    assert rig1["rewards_total"] == pytest.approx(75.0)
    assert rig2["blocks_found"] == 0
    assert rig2["rewards_total"] == pytest.approx(0.0)


def test_worker_detail_user_summary_has_rewards(monkeypatch, tmp_path: Path) -> None:
    """Worker detail user_summary includes aggregated blocks_found and rewards_total."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("alice.rig2", ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(height=100, worker="alice.rig1", reward=50.0, ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(height=200, worker="alice.rig2", reward=30.0, ts=now), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers/alice.rig1", headers=headers)
    assert resp.status_code == 200
    us = resp.json()["user_summary"]
    assert us["blocks_found"] == 2
    assert us["rewards_total"] == pytest.approx(80.0)


def test_user_list_includes_rewards(monkeypatch, tmp_path: Path) -> None:
    """GET /v1/mining/users returns blocks_found and rewards_total per user."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("bob", ts=now), headers=headers).status_code == 200

    assert client.post("/v1/mining/block", json=_block_payload(height=100, worker="alice.rig1", reward=50.0, ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(height=200, worker="bob", reward=25.0, ts=now), headers=headers).status_code == 200

    resp = client.get("/v1/mining/users", headers=headers)
    assert resp.status_code == 200
    users_list = resp.json()

    alice = next(u for u in users_list if u["username"] == "alice")
    assert alice["blocks_found"] == 1
    assert alice["rewards_total"] == pytest.approx(50.0)

    bob = next(u for u in users_list if u["username"] == "bob")
    assert bob["blocks_found"] == 1
    assert bob["rewards_total"] == pytest.approx(25.0)


def test_user_detail_includes_rewards(monkeypatch, tmp_path: Path) -> None:
    """GET /v1/mining/users/{username} returns blocks_found, rewards_total, and per-miner rewards."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("alice.rig1", ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/share", json=_payload("alice.rig2", ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(height=100, worker="alice.rig1", reward=50.0, ts=now), headers=headers).status_code == 200
    assert client.post("/v1/mining/block", json=_block_payload(height=200, worker="alice.rig2", reward=30.0, ts=now), headers=headers).status_code == 200

    resp = client.get("/v1/mining/users/alice", headers=headers)
    assert resp.status_code == 200
    u = resp.json()

    assert u["blocks_found"] == 2
    assert u["rewards_total"] == pytest.approx(80.0)

    rig1 = next(m for m in u["miners"] if m["name"] == "alice.rig1")
    rig2 = next(m for m in u["miners"] if m["name"] == "alice.rig2")
    assert rig1["blocks_found"] == 1
    assert rig1["rewards_total"] == pytest.approx(50.0)
    assert rig2["blocks_found"] == 1
    assert rig2["rewards_total"] == pytest.approx(30.0)


def test_worker_no_blocks_has_zero_rewards(monkeypatch, tmp_path: Path) -> None:
    """Workers with no block finds show blocks_found=0 and rewards_total=0.0."""
    client = _make_client(monkeypatch, tmp_path / "shares.db")
    headers = {"Authorization": "Bearer testtoken-123"}
    now = int(time.time())

    assert client.post("/v1/mining/share", json=_payload("BenC", ts=now), headers=headers).status_code == 200

    resp = client.get("/v1/mining/workers", headers=headers)
    assert resp.status_code == 200
    w = next(w for w in resp.json() if w["name"] == "BenC")
    assert w["blocks_found"] == 0
    assert w["rewards_total"] == 0.0
