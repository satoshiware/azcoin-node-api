from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ZMQ_URL = "tcp://127.0.0.1:28332"
_DEFAULT_CHAIN = "micro"
_DEFAULT_MAX_EVENTS = 2000
_SUPPORTED_TOPICS = {"hashtx", "hashblock"}


class EventsBus:
    """
    Lightweight in-memory event bus fed by AZCoin ZMQ topics.

    Events are retained in a bounded ring buffer and exposed newest-first.
    """

    def __init__(self, *, zmq_url: str, max_events: int = _DEFAULT_MAX_EVENTS) -> None:
        self._zmq_url = zmq_url
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
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

    def _append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)

    def _run_subscriber(self) -> None:
        try:
            import zmq
        except ModuleNotFoundError:
            logger.warning("pyzmq is not installed; events subscriber is disabled")
            return

        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        socket.setsockopt(zmq.SUBSCRIBE, b"")
        socket.connect(self._zmq_url)

        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)

        logger.info("Starting AZCoin events subscriber on %s", self._zmq_url)
        try:
            while True:
                if socket not in dict(poller.poll(1000)):
                    continue

                parts = socket.recv_multipart()
                if len(parts) < 2:
                    continue

                topic = parts[0].decode("utf-8", errors="ignore")
                if topic not in _SUPPORTED_TOPICS:
                    continue

                payload = parts[1]
                tx_or_block_hash = payload.hex() if isinstance(payload, (bytes, bytearray)) else ""
                if not tx_or_block_hash:
                    continue

                self._append(
                    {
                        "type": topic,
                        "hash": tx_or_block_hash,
                        "chain": _DEFAULT_CHAIN,
                        "time": int(time.time()),
                    }
                )
        except Exception:
            logger.exception("AZCoin events subscriber stopped unexpectedly")
        finally:
            poller.unregister(socket)
            socket.close(0)
            context.term()


events_bus = EventsBus(zmq_url=os.getenv("AZ_ZMQ_URL", _DEFAULT_ZMQ_URL))
