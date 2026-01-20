from enum import Enum
from typing import Dict

from fastapi import Header, HTTPException, status

from app.config import Settings


class Role(str, Enum):
    owner = "owner"
    reader = "reader"


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        self._api_key_map = self._parse_api_keys(settings.api_keys)

    @staticmethod
    def _parse_api_keys(raw_keys: str) -> Dict[str, Role]:
        api_key_map: Dict[str, Role] = {}
        if not raw_keys:
            return api_key_map
        entries = [entry.strip() for entry in raw_keys.split(",") if entry.strip()]
        for entry in entries:
            if ":" not in entry:
                continue
            role_raw, key = entry.split(":", 1)
            role_raw = role_raw.strip().lower()
            key = key.strip()
            if not key:
                continue
            try:
                role = Role(role_raw)
            except ValueError:
                continue
            api_key_map[key] = role
        return api_key_map

    def require_role(self, required: Role):
        def _check(x_api_key: str | None = Header(default=None)) -> Role:
            if not x_api_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing X-API-Key header",
                )
            role = self._api_key_map.get(x_api_key)
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid API key",
                )
            if required == Role.owner and role != Role.owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Owner role required",
                )
            return role

        return _check
