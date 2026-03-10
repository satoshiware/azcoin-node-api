from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from typing import Any

from node_api.services.event_store import EventStore, ZmqEvent

logger = logging.getLogger(__name__)

_DEFAULT_TX_ZMQ_URL = "tcp://127.0.0.1:28332"
_DEFAULT_RAWTX_ZMQ_URL = "tcp://azcoind:28334"
_DEFAULT_RAWBLOCK_ZMQ_URL = "tcp://azcoind:28333"
_DEFAULT_CHAIN = "main"
_DEFAULT_MAX_EVENTS = 2000
_DEFAULT_SUBSCRIBER_QUEUE_SIZE = 256
_DEFAULT_TOPICS = ("hashtx", "rawblock", "rawtx")
_SUPPORTED_TOPICS = {"hashtx", "rawtx", "rawblock", "hashblock"}
Subscriber = tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]


class EventsBus:
    """
    Lightweight in-memory event bus fed by AZCoin ZMQ topics.

    Events are retained in a bounded ring buffer and exposed newest-first.
    """

    def __init__(
        self,
        *,
        tx_zmq_url: str,
        rawtx_zmq_url: str,
        rawblock_zmq_url: str,
        hashblock_zmq_url: str = "",
        chain: str = _DEFAULT_CHAIN,
        topics: tuple[str, ...] = _DEFAULT_TOPICS,
        event_store: EventStore | None = None,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        self._tx_zmq_url = tx_zmq_url
        self._rawtx_zmq_url = rawtx_zmq_url
        self._rawblock_zmq_url = rawblock_zmq_url
        self._hashblock_zmq_url = hashblock_zmq_url
        self._chain = chain
        self._topics = tuple(topic for topic in topics if topic in _SUPPORTED_TOPICS)
        self._event_store = event_store
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._subscribers: list[Subscriber] = []
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run_subscriber,
                name="az-events-zmq",
                daemon=True,
            )
            self._thread.start()

    def list_recent(self, *, limit: int, event_type: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = list(self._events)

        if event_type is not None:
            snapshot = [event for event in snapshot if event.get("type") == event_type]

        snapshot.reverse()
        return snapshot[:limit]

    def subscribe(
        self, *, max_queue_size: int = _DEFAULT_SUBSCRIBER_QUEUE_SIZE
    ) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.append((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queue_id = id(queue)
        with self._lock:
            self._subscribers = [
                (loop, subscriber_queue)
                for loop, subscriber_queue in self._subscribers
                if id(subscriber_queue) != queue_id
            ]

    def bind_event_store(self, store: EventStore | None) -> None:
        with self._lock:
            self._event_store = store

    def _append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
        self._push_to_event_store(event)
        self._broadcast(event)

    def _push_to_event_store(self, event: dict[str, Any]) -> None:
        with self._lock:
            store = self._event_store

        if store is None:
            return

        ev_type = event.get("type")
        if not isinstance(ev_type, str) or not ev_type:
            return

        chain = event.get("chain")
        if not isinstance(chain, str) or not chain:
            chain = self._chain

        timestamp = event.get("time")
        if not isinstance(timestamp, int):
            timestamp = int(time.time())

        seq = event.get("seq")
        if not isinstance(seq, int):
            seq = None

        payload_hex = event.get("payload_hex")
        if not isinstance(payload_hex, str):
            payload_hex = ""

        # Backward-compat for legacy hashtx/hashblock event shape.
        if not payload_hex:
            event_hash = event.get("hash")
            if isinstance(event_hash, str):
                payload_hex = event_hash

        store.push(
            ZmqEvent(
                type=ev_type,
                chain=chain,
                time=timestamp,
                seq=seq,
                payload_hex=payload_hex,
            )
        )

    def _broadcast(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)

        stale_queue_ids: set[int] = set()
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._queue_event, queue, dict(event))
            except RuntimeError:
                stale_queue_ids.add(id(queue))

        if stale_queue_ids:
            with self._lock:
                self._subscribers = [
                    (loop, queue)
                    for loop, queue in self._subscribers
                    if id(queue) not in stale_queue_ids
                ]

    @staticmethod
    def _queue_event(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest queued item for this subscriber and keep the stream live.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                return

    def _run_subscriber(self) -> None:
        try:
            import zmq
        except ModuleNotFoundError:
            logger.warning("pyzmq is not installed; events subscriber is disabled")
            return

        context = zmq.Context()
        sockets: list[Any] = []
        topic_endpoints = {
            "hashtx": self._tx_zmq_url,
            "rawtx": self._rawtx_zmq_url,
            "rawblock": self._rawblock_zmq_url,
            "hashblock": self._hashblock_zmq_url,
        }
        poller = zmq.Poller()

        for topic in self._topics:
            endpoint = topic_endpoints.get(topic, "")
            if not endpoint:
                logger.warning("Skipping ZMQ topic %s because endpoint is empty", topic)
                continue

            socket = context.socket(zmq.SUB)
            socket.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
            socket.connect(endpoint)
            poller.register(socket, zmq.POLLIN)
            sockets.append(socket)
            logger.info("Subscribed AZCoin events topic %s on %s", topic, endpoint)
            if topic == "rawtx":
                logger.info("AZCoin rawtx subscription is active on %s", endpoint)

        if not sockets:
            logger.warning("No AZCoin events topics configured; subscriber is idle")
            context.term()
            return

        logger.info(
            "Starting AZCoin events subscriber on hashtx=%s rawtx=%s rawblock=%s",
            self._tx_zmq_url,
            self._rawtx_zmq_url,
            self._rawblock_zmq_url,
        )
        try:
            while True:
                ready_sockets = dict(poller.poll(1000))
                if not ready_sockets:
                    continue

                for socket in sockets:
                    if socket not in ready_sockets:
                        continue
                    event = self._normalize_event(socket.recv_multipart())
                    if event is not None:
                        self._append(event)
        except Exception:
            logger.exception("AZCoin events subscriber stopped unexpectedly")
        finally:
            for socket in sockets:
                poller.unregister(socket)
                socket.close(0)
            context.term()

    def _normalize_event(self, parts: list[bytes]) -> dict[str, Any] | None:
        if len(parts) < 2:
            return None

        topic = parts[0].decode("utf-8", errors="ignore")
        payload = parts[1]
        if not isinstance(payload, (bytes, bytearray)):
            return None

        seq: int | None = None
        if len(parts) >= 3 and isinstance(parts[-1], (bytes, bytearray)) and len(parts[-1]) == 4:
            seq = int.from_bytes(parts[-1], byteorder="little", signed=False)

        timestamp = int(time.time())
        if topic == "hashtx":
            tx_hash = payload.hex()
            if not tx_hash:
                return None
            event = {
                "type": "hashtx",
                "hash": tx_hash,
                "chain": self._chain,
                "time": timestamp,
            }
            if seq is not None:
                event["seq"] = seq
            return event

        if topic == "rawtx":
            payload_hex = payload.hex()
            if not payload_hex:
                return None
            event = {
                "type": "rawtx",
                "chain": self._chain,
                "time": timestamp,
                "payload_hex": payload_hex,
            }
            if seq is not None:
                event["seq"] = seq
            return event

        if topic == "rawblock":
            # Emit lightweight metadata only; raw block bytes are not exposed.
            event = {
                "type": "rawblock",
                "chain": self._chain,
                "time": timestamp,
                "raw_len": len(payload),
            }
            if seq is not None:
                event["seq"] = seq
            return event

        if topic == "hashblock":
            block_hash = payload.hex()
            if not block_hash:
                return None
            event = {
                "type": "hashblock",
                "hash": block_hash,
                "chain": self._chain,
                "time": timestamp,
            }
            if seq is not None:
                event["seq"] = seq
            return event

        return None


def _env_first_nonempty(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _parse_topics(raw_topics: str) -> tuple[str, ...]:
    topics = [topic.strip() for topic in raw_topics.split(",") if topic.strip()]
    if not topics:
        return _DEFAULT_TOPICS
    return tuple(dict.fromkeys(topics))


events_bus = EventsBus(
    tx_zmq_url=_env_first_nonempty("AZ_ZMQ_HASHTX", "AZ_ZMQ_URL", default=_DEFAULT_TX_ZMQ_URL),
    rawtx_zmq_url=_env_first_nonempty("AZ_ZMQ_RAWTX", default=_DEFAULT_RAWTX_ZMQ_URL),
    rawblock_zmq_url=_env_first_nonempty(
        "AZ_ZMQ_RAWBLOCK",
        "AZ_ZMQ_RAWBLOCK_URL",
        default=_DEFAULT_RAWBLOCK_ZMQ_URL,
    ),
    hashblock_zmq_url=_env_first_nonempty("AZ_ZMQ_HASHBLOCK", default=""),
    chain=os.getenv("AZ_CHAIN", _DEFAULT_CHAIN),
    topics=_parse_topics(os.getenv("AZ_ZMQ_TOPICS", ",".join(_DEFAULT_TOPICS))),
)
