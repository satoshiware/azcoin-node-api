from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class AzcoinRpcError(Exception):
    """Base exception for AZCoin JSON-RPC failures."""


@dataclass(frozen=True)
class AzcoinRpcTransportError(AzcoinRpcError):
    message: str


@dataclass(frozen=True)
class AzcoinRpcHttpError(AzcoinRpcError):
    status_code: int
    message: str


@dataclass(frozen=True)
class AzcoinRpcResponseError(AzcoinRpcError):
    code: int | None
    message: str


@dataclass(frozen=True)
class AzcoinRpcWrongChainError(AzcoinRpcError):
    expected_chain: str
    actual_chain: str | None


class AzcoinRpcClient:
    def __init__(
        self,
        *,
        url: str,
        user: str,
        password: str,
        timeout_seconds: float = 5.0,
        expected_chain: str = "main",
    ) -> None:
        self._url = url.rstrip("/")
        self._auth = (user, password)
        self._timeout = httpx.Timeout(timeout_seconds)
        self._expected_chain = expected_chain
        self._chain_checked = False

    def call(self, method: str, params: list | None = None) -> Any:
        if method == "getblockchaininfo":
            result = self._call_raw(method, params)
            self._validate_chain_info(result)
            return result

        self._ensure_expected_chain()
        return self._call_raw(method, params)

    def _ensure_expected_chain(self) -> None:
        if self._chain_checked:
            return

        result = self._call_raw("getblockchaininfo")
        self._validate_chain_info(result)

    def _validate_chain_info(self, result: Any) -> None:
        if not isinstance(result, dict):
            raise AzcoinRpcResponseError(code=None, message="AZCoin RPC returned non-object result")

        chain = result.get("chain")
        if not isinstance(chain, str):
            raise AzcoinRpcResponseError(
                code=None, message="AZCoin RPC returned unexpected blockchain info"
            )

        if chain != self._expected_chain:
            raise AzcoinRpcWrongChainError(expected_chain=self._expected_chain, actual_chain=chain)

        self._chain_checked = True

    def _call_raw(self, method: str, params: list | None = None) -> Any:
        payload = {"jsonrpc": "1.0", "id": "azcoin-api", "method": method, "params": params or []}

        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(self._url, json=payload, auth=self._auth)
        except httpx.TimeoutException as e:
            raise AzcoinRpcTransportError("AZCoin RPC timeout") from e
        except httpx.RequestError as e:
            raise AzcoinRpcTransportError("AZCoin RPC network error") from e

        if r.status_code != 200:
            raise AzcoinRpcHttpError(
                status_code=r.status_code, message="AZCoin RPC non-200 response"
            )

        try:
            data = r.json()
        except ValueError as e:
            raise AzcoinRpcResponseError(
                code=None, message="AZCoin RPC returned invalid JSON"
            ) from e

        if isinstance(data, dict) and data.get("error"):
            err = data["error"] or {}
            code = err.get("code")
            message = err.get("message") or "AZCoin JSON-RPC error"
            raise AzcoinRpcResponseError(code=code, message=message)

        if not isinstance(data, dict) or "result" not in data:
            raise AzcoinRpcResponseError(
                code=None, message="AZCoin RPC returned unexpected payload"
            )

        return data["result"]
