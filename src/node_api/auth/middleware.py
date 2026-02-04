from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from node_api.auth.validator import TokenValidator


def _extract_bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts[0].strip(), parts[1].strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


@dataclass(frozen=True)
class AuthConfig:
    protected_path_prefixes: tuple[str, ...]
    exempt_paths: tuple[str, ...]


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    JWT auth middleware (stub).

    Enforces a Bearer token on configured protected path prefixes only.
    """

    def __init__(
        self,
        app,
        *,
        config: AuthConfig,
        validator: TokenValidator,
    ) -> None:
        super().__init__(app)
        self._config = config
        self._validator = validator

    def _is_exempt(self, path: str) -> bool:
        return any(
            path == p or path.startswith(p.rstrip("/") + "/") for p in self._config.exempt_paths
        )

    def _is_protected(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._config.protected_path_prefixes)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if self._is_exempt(path) or not self._is_protected(path):
            return await call_next(request)

        token = _extract_bearer_token(request.headers.get("Authorization"))
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing bearer token"})

        if not self._validator.validate(token):
            return JSONResponse(status_code=403, content={"detail": "Invalid token"})

        return await call_next(request)
