from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from node_api.settings import get_settings


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


class BitcoinRPC:
    """
    Single Bitcoin JSON-RPC client with shared transport.
    Use call() for any result type; use call_dict() when result must be a dict.
    """

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

    @classmethod
    def from_settings(cls) -> "BitcoinRPC":
        settings = get_settings()
        if not settings.btc_rpc_url or not settings.btc_rpc_user or not settings.btc_rpc_password:
            raise BitcoinRpcResponseError(code=None, message="Bitcoin RPC is not configured")
        return cls(
            url=settings.btc_rpc_url,
            user=settings.btc_rpc_user,
            password=settings.btc_rpc_password.get_secret_value(),
            timeout_seconds=settings.btc_rpc_timeout_seconds,
        )

    def _request(self, method: str, params: list | None = None) -> Any:
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

        return data["result"]

    def call(self, method: str, params: list | None = None) -> Any:
        """Execute RPC and return raw result."""
        return self._request(method, params)

    def call_dict(self, method: str, params: list | None = None) -> dict[str, Any]:
        """Execute RPC and return result as dict; raise if result is not a dict."""
        result = self._request(method, params)
        if not isinstance(result, dict):
            raise BitcoinRpcResponseError(
                code=None, message="Bitcoin RPC returned non-object result"
            )
        return result
