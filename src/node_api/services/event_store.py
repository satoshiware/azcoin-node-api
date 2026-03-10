from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any, Deque, Dict, List, Optional


@dataclass(frozen=True)
class ZmqEvent:
    type: str          # rawtx, rawblock, hashtx, hashblock
    chain: str         # main, regtest, etc.
    time: int          # unix seconds
    seq: Optional[int] # optional sequence
    payload_hex: str   # raw bytes as hex (or hash bytes as hex)


class EventStore:
    def __init__(self, maxlen: int = 500) -> None:
        self._buf: Deque[ZmqEvent] = deque(maxlen=maxlen)
        self._lock = Lock()

    def push(self, ev: ZmqEvent) -> None:
        with self._lock:
            self._buf.append(ev)

    def recent(self, *, ev_type: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            items = list(self._buf)

        if ev_type:
            items = [e for e in items if e.type == ev_type]

        # newest first
        items = items[-limit:][::-1]
        return [asdict(e) for e in items]
