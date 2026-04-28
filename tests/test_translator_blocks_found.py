from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from node_api.services.translator_blocks_found import poll_blocks_found_once
from node_api.services.translator_blocks_found_store import TranslatorBlocksFoundStore
from node_api.settings import Settings, get_settings


def _settings(monkeypatch, tmp_path: Path) -> Settings:
    db_path = tmp_path / "translator_blocks_found.sqlite3"
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "dev_token")
    monkeypatch.setenv("AZ_API_DEV_TOKEN", "testtoken")
    monkeypatch.setenv("TRANSLATOR_BLOCKS_FOUND_DB_PATH", str(db_path))
    monkeypatch.setenv("TRANSLATOR_LOG_PATH", "")
    get_settings.cache_clear()
    return get_settings()


def _client(monkeypatch, tmp_path: Path) -> tuple[TestClient, TranslatorBlocksFoundStore]:
    settings = _settings(monkeypatch, tmp_path)
    from node_api import main as main_module

    client = TestClient(main_module.create_app())
    store = TranslatorBlocksFoundStore.from_settings(settings)
    return client, store


def _joined_row(
    *,
    channel_id: int = 2,
    worker_identity: str = "alice.rig01",
    authorized_worker_name: str | None = "alice.rig01",
    downstream_user_identity: str | None = "alice.rig01",
    upstream_user_identity: str | None = "pool.alice",
    blocks_found: int = 0,
    share_work_sum: str = "1000",
    shares_acknowledged: int = 10,
    shares_submitted: int = 10,
    shares_rejected: int = 0,
) -> dict:
    return {
        "channel_id": channel_id,
        "worker_identity": worker_identity,
        "authorized_worker_name": authorized_worker_name,
        "downstream_user_identity": downstream_user_identity,
        "upstream_user_identity": upstream_user_identity,
        "blocks_found": blocks_found,
        "share_work_sum": share_work_sum,
        "shares_acknowledged": shares_acknowledged,
        "shares_submitted": shares_submitted,
        "shares_rejected": shares_rejected,
        "join_status": "joined",
    }


def _snapshot(snapshot_time: int, *rows: dict) -> dict:
    return {
        "status": "ok",
        "configured": True,
        "snapshot_time": snapshot_time,
        "source": "translator",
        "data": {"total": len(rows), "items": list(rows)},
        "detail": None,
    }


def _insert_event(
    store: TranslatorBlocksFoundStore,
    *,
    detected_time: int,
    channel_id: int,
    worker_identity: str,
    blocks_found_before: int,
    blocks_found_after: int,
    blockhash_status: str = "unresolved",
) -> None:
    created = store.insert_event(
        {
            "identity_key": worker_identity,
            "detected_time": detected_time,
            "channel_id": channel_id,
            "worker_identity": worker_identity,
            "authorized_worker_name": worker_identity,
            "downstream_user_identity": worker_identity,
            "upstream_user_identity": f"upstream.{worker_identity}",
            "blocks_found_before": blocks_found_before,
            "blocks_found_after": blocks_found_after,
            "blocks_found_delta": blocks_found_after - blocks_found_before,
            "share_work_sum_at_detection": "1000",
            "shares_acknowledged_at_detection": 10,
            "shares_submitted_at_detection": 10,
            "shares_rejected_at_detection": 0,
            "blockhash": None,
            "blockhash_status": blockhash_status,
            "correlation_status": "counter_delta_only",
            "raw_snapshot_json": None,
        }
    )
    assert created is True


def test_poller_first_observation_creates_only_state(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(monkeypatch, tmp_path)
    store = TranslatorBlocksFoundStore.from_settings(settings)

    stats = poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1000, _joined_row(blocks_found=3)),
    )

    assert stats["events_created"] == 0
    assert store.event_count() == 0
    state = store.get_poller_state("alice.rig01")
    assert state is not None
    assert state["last_blocks_found"] == 3
    assert state["last_channel_id"] == 2
    assert state["last_seen_time"] == 1000


def test_poller_second_same_observation_creates_no_event(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(monkeypatch, tmp_path)
    store = TranslatorBlocksFoundStore.from_settings(settings)

    poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1000, _joined_row(blocks_found=3)),
    )
    stats = poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1015, _joined_row(blocks_found=3)),
    )

    assert stats["events_created"] == 0
    assert store.event_count() == 0
    state = store.get_poller_state("alice.rig01")
    assert state is not None
    assert state["last_blocks_found"] == 3
    assert state["last_seen_time"] == 1015


def test_poller_detects_plus_one_blocks_found(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(monkeypatch, tmp_path)
    store = TranslatorBlocksFoundStore.from_settings(settings)

    poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1000, _joined_row(blocks_found=3)),
    )
    stats = poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1015, _joined_row(blocks_found=4, share_work_sum="2000")),
    )

    assert stats["events_created"] == 1
    total, items = store.list_events(
        start_time=None,
        end_time=None,
        limit=100,
        worker_identity=None,
        channel_id=None,
        blockhash_status=None,
    )
    assert total == 1
    item = items[0]
    assert item["blocks_found_before"] == 3
    assert item["blocks_found_after"] == 4
    assert item["blocks_found_delta"] == 1
    assert item["share_work_sum_at_detection"] == "2000"


def test_poller_detects_plus_two_blocks_found(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(monkeypatch, tmp_path)
    store = TranslatorBlocksFoundStore.from_settings(settings)

    poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1000, _joined_row(blocks_found=3)),
    )
    stats = poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1015, _joined_row(blocks_found=5)),
    )

    assert stats["events_created"] == 1
    _, items = store.list_events(
        start_time=None,
        end_time=None,
        limit=100,
        worker_identity=None,
        channel_id=None,
        blockhash_status=None,
    )
    assert items[0]["blocks_found_delta"] == 2


def test_poller_counter_reset_updates_state_without_negative_event(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    store = TranslatorBlocksFoundStore.from_settings(settings)

    poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1000, _joined_row(blocks_found=5)),
    )
    stats = poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1015, _joined_row(blocks_found=2)),
    )

    assert stats["counter_resets"] == 1
    assert store.event_count() == 0
    state = store.get_poller_state("alice.rig01")
    assert state is not None
    assert state["last_blocks_found"] == 2
    assert state["last_seen_time"] == 1015


def test_poller_channel_id_change_does_not_create_false_identity(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    store = TranslatorBlocksFoundStore.from_settings(settings)

    poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1000, _joined_row(channel_id=2, blocks_found=1)),
    )
    stats = poll_blocks_found_once(
        settings,
        store,
        snapshot=_snapshot(1015, _joined_row(channel_id=99, blocks_found=1)),
    )

    assert stats["events_created"] == 0
    assert store.event_count() == 0
    state = store.get_poller_state("alice.rig01")
    assert state is not None
    assert state["last_channel_id"] == 99
    assert state["last_blocks_found"] == 1


def test_blocks_found_endpoint_returns_newest_first(monkeypatch, tmp_path: Path) -> None:
    client, store = _client(monkeypatch, tmp_path)
    _insert_event(
        store,
        detected_time=1000,
        channel_id=2,
        worker_identity="worker-a",
        blocks_found_before=0,
        blocks_found_after=1,
    )
    _insert_event(
        store,
        detected_time=1020,
        channel_id=3,
        worker_identity="worker-b",
        blocks_found_before=1,
        blocks_found_after=2,
    )

    response = client.get(
        "/v1/translator/blocks-found",
        headers={"Authorization": "Bearer testtoken"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "translator_blocks_found_events"
    assert body["total"] == 2
    assert [item["detected_time"] for item in body["items"]] == [1020, 1000]


def test_blocks_found_endpoint_start_time_includes_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    client, store = _client(monkeypatch, tmp_path)
    _insert_event(
        store,
        detected_time=1000,
        channel_id=2,
        worker_identity="worker-a",
        blocks_found_before=0,
        blocks_found_after=1,
    )
    _insert_event(
        store,
        detected_time=1001,
        channel_id=3,
        worker_identity="worker-b",
        blocks_found_before=1,
        blocks_found_after=2,
    )

    response = client.get(
        "/v1/translator/blocks-found",
        params={"start_time": 1000},
        headers={"Authorization": "Bearer testtoken"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_blocks_found_endpoint_end_time_excludes_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    client, store = _client(monkeypatch, tmp_path)
    _insert_event(
        store,
        detected_time=1000,
        channel_id=2,
        worker_identity="worker-a",
        blocks_found_before=0,
        blocks_found_after=1,
    )
    _insert_event(
        store,
        detected_time=1001,
        channel_id=3,
        worker_identity="worker-b",
        blocks_found_before=1,
        blocks_found_after=2,
    )

    response = client.get(
        "/v1/translator/blocks-found",
        params={"end_time": 1001},
        headers={"Authorization": "Bearer testtoken"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["detected_time"] == 1000


def test_blocks_found_endpoint_worker_identity_filter(monkeypatch, tmp_path: Path) -> None:
    client, store = _client(monkeypatch, tmp_path)
    _insert_event(
        store,
        detected_time=1000,
        channel_id=2,
        worker_identity="worker-a",
        blocks_found_before=0,
        blocks_found_after=1,
    )
    _insert_event(
        store,
        detected_time=1001,
        channel_id=3,
        worker_identity="worker-b",
        blocks_found_before=1,
        blocks_found_after=2,
    )

    response = client.get(
        "/v1/translator/blocks-found",
        params={"worker_identity": "worker-b"},
        headers={"Authorization": "Bearer testtoken"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["worker_identity"] == "worker-b"


def test_blocks_found_endpoint_channel_id_filter(monkeypatch, tmp_path: Path) -> None:
    client, store = _client(monkeypatch, tmp_path)
    _insert_event(
        store,
        detected_time=1000,
        channel_id=2,
        worker_identity="worker-a",
        blocks_found_before=0,
        blocks_found_after=1,
    )
    _insert_event(
        store,
        detected_time=1001,
        channel_id=3,
        worker_identity="worker-b",
        blocks_found_before=1,
        blocks_found_after=2,
    )

    response = client.get(
        "/v1/translator/blocks-found",
        params={"channel_id": 2},
        headers={"Authorization": "Bearer testtoken"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["channel_id"] == 2


def test_blocks_found_endpoint_invalid_time_range_returns_422(
    monkeypatch, tmp_path: Path
) -> None:
    client, _store = _client(monkeypatch, tmp_path)

    response = client.get(
        "/v1/translator/blocks-found",
        params={"start_time": 1000, "end_time": 1000},
        headers={"Authorization": "Bearer testtoken"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TRANSLATOR_BLOCKS_FOUND_TIME_RANGE_INVALID"


def test_blocks_found_endpoint_exposes_only_evidence_fields(
    monkeypatch, tmp_path: Path
) -> None:
    client, store = _client(monkeypatch, tmp_path)
    _insert_event(
        store,
        detected_time=1000,
        channel_id=2,
        worker_identity="worker-a",
        blocks_found_before=0,
        blocks_found_after=1,
    )

    response = client.get(
        "/v1/translator/blocks-found",
        headers={"Authorization": "Bearer testtoken"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "blockhash_status" in item
    assert "correlation_status" in item
    assert "payout_status" not in item
    assert "wallet_txid" not in item
    assert "payment_address" not in item
