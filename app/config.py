import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    rpc_url: str
    rpc_user: str
    rpc_password: str
    api_keys: str
    request_timeout: float


def load_settings() -> Settings:
    return Settings(
        rpc_url=os.getenv("AZCOIN_RPC_URL", "http://localhost:8332"),
        rpc_user=os.getenv("AZCOIN_RPC_USER", ""),
        rpc_password=os.getenv("AZCOIN_RPC_PASSWORD", ""),
        api_keys=os.getenv("API_KEYS", ""),
        request_timeout=float(os.getenv("RPC_TIMEOUT", "10")),
    )
