from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class BitcoinRpcError(Exception):
    """Base exception for Bitcoin JSON-RPC failures."""


@dataclass(frozen=True)
class BitcoinRpcTransportError(BitcoinRpcError):
    message: str


@dataclass(frozen=True)
class BitcoinRpcHttpError(BitcoinRpcError):
    status_code: int
    message: str


@dataclass(frozen=True)
class BitcoinRpcResponseError(BitcoinRpcError):
    code: int | None
    message: str


class BitcoinRpcClient:
    def __init__(
        self,
        *,
        url: str,
        user: str,
        password: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._auth = (user, password)
        self._timeout = httpx.Timeout(timeout_seconds)

    def call(self, method: str, params: list | None = None) -> dict[str, Any]:
        payload = {"jsonrpc": "1.0", "id": "azcoin-api", "method": method, "params": params or []}

        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(self._url, json=payload, auth=self._auth)
        except httpx.TimeoutException as e:
            raise BitcoinRpcTransportError("Bitcoin RPC timeout") from e
        except httpx.RequestError as e:
            raise BitcoinRpcTransportError("Bitcoin RPC network error") from e

        if r.status_code != 200:
            raise BitcoinRpcHttpError(
                status_code=r.status_code, message="Bitcoin RPC non-200 response"
            )

        try:
            data = r.json()
        except ValueError as e:
            raise BitcoinRpcResponseError(
                code=None, message="Bitcoin RPC returned invalid JSON"
            ) from e

        if isinstance(data, dict) and data.get("error"):
            err = data["error"] or {}
            code = err.get("code")
            message = err.get("message") or "Bitcoin JSON-RPC error"
            raise BitcoinRpcResponseError(code=code, message=message)

        if not isinstance(data, dict) or "result" not in data:
            raise BitcoinRpcResponseError(
                code=None, message="Bitcoin RPC returned unexpected payload"
            )

        result = data["result"]
        if not isinstance(result, dict):
            raise BitcoinRpcResponseError(
                code=None, message="Bitcoin RPC returned non-object result"
            )
        return result
