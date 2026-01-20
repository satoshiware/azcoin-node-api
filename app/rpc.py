import itertools
from typing import Any

import requests

from app.config import Settings


class RPCError(RuntimeError):
    pass


class RPCClient:
    def __init__(self, settings: Settings) -> None:
        self._url = settings.rpc_url
        self._auth = (settings.rpc_user, settings.rpc_password)
        self._timeout = settings.request_timeout
        self._counter = itertools.count(1)

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "1.0",
            "id": next(self._counter),
            "method": method,
            "params": params or [],
        }
        try:
            response = requests.post(
                self._url,
                json=payload,
                auth=self._auth,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise RPCError(f"RPC connection failed: {exc}") from exc
        if response.status_code != 200:
            raise RPCError(
                f"RPC request failed with status {response.status_code}: {response.text}"
            )
        data = response.json()
        if data.get("error"):
            raise RPCError(str(data["error"]))
        return data.get("result")
