from __future__ import annotations

from fastapi import FastAPI

from node_api.auth.middleware import AuthConfig, JWTAuthMiddleware
from node_api.auth.validator import RejectAllValidator, StaticTokenValidator
from node_api.logging import configure_logging
from node_api.routes.v1.az_node import router as az_node_router
from node_api.routes.v1.health import router as health_router
from node_api.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level)

    openapi_tags = [
        {"name": "health", "description": "Service liveness/readiness endpoints."},
        {"name": "az-node", "description": "AZCoin node endpoints (protected)."},
    ]

    app = FastAPI(
        title="AZCoin Node API",
        version="0.1.0",
        openapi_tags=openapi_tags,
    )

    app.add_middleware(
        JWTAuthMiddleware,
        config=AuthConfig(
            protected_path_prefixes=(f"{settings.api_v1_prefix}/az",),
            exempt_paths=(
                f"{settings.api_v1_prefix}/health",
                "/openapi.json",
                "/docs",
                "/redoc",
            ),
        ),
        validator=(
            StaticTokenValidator(expected_token=settings.az_api_dev_token or "")
            if settings.auth_mode == "dev_token"
            else RejectAllValidator()
        ),
    )

    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(az_node_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
