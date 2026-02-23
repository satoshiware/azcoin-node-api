from __future__ import annotations

from node_api.services.event_store import EventStore
from node_api.services.events_bus import EventsBus


def _build_bus() -> EventsBus:
    return EventsBus(
        tx_zmq_url="tcp://127.0.0.1:28332",
        rawtx_zmq_url="tcp://127.0.0.1:28334",
        rawblock_zmq_url="tcp://127.0.0.1:28333",
        chain="micro",
    )


def test_normalize_rawtx_three_frames_includes_seq_and_payload_hex() -> None:
    bus = _build_bus()
    frames = [b"rawtx", b"\x01\x02\x03", (42).to_bytes(4, byteorder="little", signed=False)]

    event = bus._normalize_event(frames)

    assert event is not None
    assert event["type"] == "rawtx"
    assert event["chain"] == "micro"
    assert event["seq"] == 42
    assert event["payload_hex"] == "010203"


def test_append_mirrors_events_into_bound_event_store() -> None:
    store = EventStore(maxlen=10)
    bus = _build_bus()
    bus.bind_event_store(store)

    bus._append(
        {
            "type": "rawtx",
            "chain": "micro",
            "time": 1700000000,
            "seq": 7,
            "payload_hex": "deadbeef",
        }
    )
    bus._append(
        {
            "type": "hashtx",
            "chain": "micro",
            "time": 1700000001,
            "hash": "aabbccdd",
        }
    )

    rawtx_events = store.recent(ev_type="rawtx", limit=1)
    hashtx_events = store.recent(ev_type="hashtx", limit=1)

    assert rawtx_events[0]["payload_hex"] == "deadbeef"
    assert rawtx_events[0]["seq"] == 7
    assert hashtx_events[0]["payload_hex"] == "aabbccdd"
