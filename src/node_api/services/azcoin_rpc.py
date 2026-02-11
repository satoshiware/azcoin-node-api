from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import logging
log = logging.getLogger("node_api.azcoin_rpc")

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


class AzcoinRpcClient:
    def __init__(
        self,
        *,
        url: str,
        user: str,
        password: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        # Normalize once; keep the value we actually call.
        self._url = url.rstrip("/")
        self._auth = (user, password)
        self._timeout = httpx.Timeout(timeout_seconds)

    def call(self, method: str, params: list | None = None) -> dict[str, Any]:
        payload = {
            "jsonrpc": "1.0",
            "id": "azcoin-api",
            "method": method,
            "params": params or [],
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                # TEMP DEBUG: prove the exact URL/method used during failing requests.
                # This will show up in `docker logs ...`.
                log.warning("AZ_RPC_CALL_URL=%r METHOD=%s", self._url, method)

                r = client.post(self._url, json=payload, auth=self._auth)

        except httpx.TimeoutException as e:
            raise AzcoinRpcTransportError("AZCoin RPC timeout") from e

        except httpx.RequestError as e:
            # include the underlying error (DNS, connect refused, reset, etc.)
            raise AzcoinRpcTransportError(
                f"AZCoin RPC network error calling {self._url!r} for method {method!r}: {type(e).__name__}: {e}"
            ) from e

        if r.status_code != 200:
            raise AzcoinRpcHttpError(status_code=r.status_code, message="AZCoin RPC non-200 response")

        try:
            data = r.json()
        except ValueError as e:
            raise AzcoinRpcResponseError(code=None, message="AZCoin RPC returned invalid JSON") from e

        if isinstance(data, dict) and data.get("error"):
            err = data["error"] or {}
            code = err.get("code")
            message = err.get("message") or "AZCoin JSON-RPC error"
            raise AzcoinRpcResponseError(code=code, message=message)

        if not isinstance(data, dict) or "result" not in data:
            raise AzcoinRpcResponseError(code=None, message="AZCoin RPC returned unexpected payload")

        result = data["result"]
        if not isinstance(result, dict):
            raise AzcoinRpcResponseError(code=None, message="AZCoin RPC returned non-object result")

        return result
