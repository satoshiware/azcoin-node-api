from __future__ import annotations

import binascii
import struct
import threading
import time
from typing import Dict, Iterable, Optional

import zmq

from .event_store import EventStore, ZmqEvent


def _parse_seq(frames: list[bytes]) -> Optional[int]:
    # Your node sends 3 frames for rawtx: [topic, payload, seq(4 bytes LE)]
    if len(frames) >= 3 and len(frames[-1]) == 4:
        return struct.unpack("<I", frames[-1])[0]
    return None


def _payload_hex(frames: list[bytes]) -> str:
    # For 3-frame payload is frames[1]; for 2-frame it's frames[1] as well.
    if len(frames) >= 2:
        return binascii.hexlify(frames[1]).decode()
    return ""


class ZmqSubscriber:
    def __init__(
        self,
        *,
        store: EventStore,
        chain: str,
        endpoints: Dict[str, str],  # topic -> tcp://...
        topics: Iterable[str],
    ) -> None:
        self.store = store
        self.chain = chain
        self.endpoints = endpoints
        self.topics = list(topics)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="zmq-subscriber", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        ctx = zmq.Context.instance()
        sub = ctx.socket(zmq.SUB)

        # connect each topic to its endpoint (can reuse same socket with multiple connects)
        for t in self.topics:
            ep = self.endpoints.get(t)
            if not ep:
                continue
            sub.connect(ep)
            sub.setsockopt(zmq.SUBSCRIBE, t.encode())

        # basic reconnect/backoff loop
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        while not self._stop.is_set():
            try:
                socks = dict(poller.poll(250))
                if sub not in socks:
                    continue

                frames = sub.recv_multipart()
                if not frames:
                    continue

                topic = frames[0].decode(errors="ignore")
                seq = _parse_seq(frames)
                payload_hex = _payload_hex(frames)

                ev = ZmqEvent(
                    type=topic,
                    chain=self.chain,
                    time=int(time.time()),
                    seq=seq,
                    payload_hex=payload_hex,
                )
                self.store.push(ev)

            except Exception:
                # don’t crash the app; sleep a bit then keep going
                time.sleep(0.25)
                continue
