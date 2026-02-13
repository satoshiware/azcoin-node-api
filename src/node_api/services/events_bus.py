from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TX_ZMQ_URL = "tcp://127.0.0.1:28332"
_DEFAULT_RAWBLOCK_ZMQ_URL = "tcp://azcoind:28333"
_DEFAULT_CHAIN = "micro"
_DEFAULT_MAX_EVENTS = 2000
_DEFAULT_SUBSCRIBER_QUEUE_SIZE = 256
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
        rawblock_zmq_url: str,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        self._tx_zmq_url = tx_zmq_url
        self._rawblock_zmq_url = rawblock_zmq_url
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

    def _append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
        self._broadcast(event)

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
        tx_socket = context.socket(zmq.SUB)
        tx_socket.setsockopt(zmq.SUBSCRIBE, b"hashtx")
        tx_socket.connect(self._tx_zmq_url)

        rawblock_socket = context.socket(zmq.SUB)
        rawblock_socket.setsockopt(zmq.SUBSCRIBE, b"rawblock")
        rawblock_socket.connect(self._rawblock_zmq_url)

        poller = zmq.Poller()
        poller.register(tx_socket, zmq.POLLIN)
        poller.register(rawblock_socket, zmq.POLLIN)

        logger.info(
            "Starting AZCoin events subscriber on tx=%s rawblock=%s",
            self._tx_zmq_url,
            self._rawblock_zmq_url,
        )
        try:
            while True:
                ready_sockets = dict(poller.poll(1000))
                if not ready_sockets:
                    continue

                if tx_socket in ready_sockets:
                    tx_event = self._normalize_event(tx_socket.recv_multipart())
                    if tx_event is not None:
                        self._append(tx_event)

                if rawblock_socket in ready_sockets:
                    rawblock_event = self._normalize_event(rawblock_socket.recv_multipart())
                    if rawblock_event is not None:
                        self._append(rawblock_event)
        except Exception:
            logger.exception("AZCoin events subscriber stopped unexpectedly")
        finally:
            poller.unregister(tx_socket)
            poller.unregister(rawblock_socket)
            tx_socket.close(0)
            rawblock_socket.close(0)
            context.term()

    @staticmethod
    def _normalize_event(parts: list[bytes]) -> dict[str, Any] | None:
        if len(parts) < 2:
            return None

        topic = parts[0].decode("utf-8", errors="ignore")
        payload = parts[1]
        if not isinstance(payload, (bytes, bytearray)):
            return None

        if topic == "hashtx":
            tx_hash = payload.hex()
            if not tx_hash:
                return None
            return {
                "type": "hashtx",
                "hash": tx_hash,
                "chain": _DEFAULT_CHAIN,
                "time": int(time.time()),
            }

        if topic == "rawblock":
            # Emit lightweight metadata only; raw block bytes are not exposed.
            return {
                "type": "rawblock",
                "chain": _DEFAULT_CHAIN,
                "time": int(time.time()),
                "raw_len": len(payload),
            }

        return None


events_bus = EventsBus(
    tx_zmq_url=os.getenv("AZ_ZMQ_URL", _DEFAULT_TX_ZMQ_URL),
    rawblock_zmq_url=os.getenv("AZ_ZMQ_RAWBLOCK_URL", _DEFAULT_RAWBLOCK_ZMQ_URL),
)
