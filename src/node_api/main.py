# ruff: noqa: I001
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from node_api.auth.middleware import AuthConfig, JWTAuthMiddleware
from node_api.auth.validator import RejectAllValidator, StaticTokenValidator
from node_api.logging import configure_logging
from node_api.routes.v1.alerts import router as alerts_router
from node_api.routes.v1.az_blocks import router as az_blocks_router
from node_api.routes.v1.az_mempool import router as az_mempool_router
from node_api.routes.v1.az_mining import router as az_mining_router
from node_api.routes.v1.az_node import router as az_node_router
from node_api.routes.v1.az_wallet import router as az_wallet_router
from node_api.routes.v1.btc_node import router as btc_node_router
from node_api.routes.v1.btc_wallet import router as btc_wallet_router
from node_api.routes.v1.dashboard import router as dashboard_router
from node_api.routes.v1.events import router as events_router
from node_api.routes.v1.health import (
    public_router as health_public_router,
    router as health_router,
)
from node_api.routes.v1.metrics import router as metrics_router
from node_api.routes.v1.miners import router as miners_router
from node_api.routes.v1.node import router as node_router
from node_api.routes.v1.services import router as services_router
from node_api.routes.v1.translator import router as translator_router
from node_api.routes.v1.tx import send as tx_send
from node_api.services.event_store import EventStore
from node_api.services.events_bus import events_bus
from node_api.settings import get_settings
from node_api.version import get_version

logger = logging.getLogger(__name__)
store = EventStore(maxlen=int(os.getenv("AZ_ZMQ_RING_SIZE", "500")))


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    from node_api.routers.events_recent import router as events_recent_router

    openapi_tags = [
        {"name": "health", "description": "Service liveness/readiness endpoints."},
        {"name": "events", "description": "Recent event stream endpoints."},
        {"name": "alerts", "description": "Operator alert endpoints (protected)."},
        {"name": "dashboard", "description": "Dashboard summary endpoint (protected)."},
        {"name": "metrics", "description": "Metrics chart endpoints (protected)."},
        {"name": "miners", "description": "Translator miner/session table endpoint (protected)."},
        {"name": "az-node", "description": "AZCoin node endpoints (protected)."},
        {
            "name": "az-mining",
            "description": "AZCoin mining template and status endpoints (protected).",
        },
        {"name": "az-mempool", "description": "AZCoin mempool endpoints (protected)."},
        {"name": "az-blocks", "description": "AZCoin block reward endpoints (protected)."},
        {"name": "az-wallet", "description": "AZCoin wallet endpoints (protected)."},
        {"name": "btc-node", "description": "Bitcoin node endpoints (protected)."},
        {"name": "btc-wallet", "description": "Bitcoin wallet endpoints (protected)."},
        {"name": "node", "description": "Multi-node summary endpoints (protected)."},
        {"name": "services", "description": "Local service status endpoints (protected)."},
        {"name": "tx", "description": "Transaction endpoints (protected)."},
        {"name": "translator", "description": "Translator log observability (protected)."},
    ]

    app = FastAPI(
        title="AZCoin Node API",
        version=get_version(),
        openapi_tags=openapi_tags,
    )

    app.add_middleware(
        JWTAuthMiddleware,
        config=AuthConfig(
            protected_path_prefixes=(
                f"{settings.api_v1_prefix}/alerts",
                f"{settings.api_v1_prefix}/az",
                f"{settings.api_v1_prefix}/btc",
                f"{settings.api_v1_prefix}/dashboard",
                f"{settings.api_v1_prefix}/metrics",
                f"{settings.api_v1_prefix}/miners",
                f"{settings.api_v1_prefix}/node",
                f"{settings.api_v1_prefix}/services",
                f"{settings.api_v1_prefix}/tx",
                f"{settings.api_v1_prefix}/translator",
            ),
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

    @app.on_event("startup")
    async def expand_thread_pool() -> None:
        from anyio import CapacityLimiter
        from anyio.lowlevel import RunVar
        limiter = CapacityLimiter(200)
        RunVar("_default_thread_limiter").set(limiter)
        logger.info("Thread pool expanded to 200")

    @app.on_event("startup")
    def start_events_subscriber() -> None:
        events_bus.bind_event_store(store)
        events_bus.start()

    # NOTE: /v1/events/recent is EventStore-backed (ZMQ ingest). Keep legacy routes from
    # defining the same path.
    app.include_router(events_recent_router)
    app.include_router(health_public_router)
    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(events_router, prefix=settings.api_v1_prefix)
    app.include_router(alerts_router, prefix=settings.api_v1_prefix)
    app.include_router(dashboard_router, prefix=settings.api_v1_prefix)
    app.include_router(metrics_router, prefix=settings.api_v1_prefix)
    app.include_router(miners_router, prefix=settings.api_v1_prefix)
    app.include_router(az_node_router, prefix=settings.api_v1_prefix)
    app.include_router(az_mining_router, prefix=settings.api_v1_prefix)
    app.include_router(az_mempool_router, prefix=settings.api_v1_prefix)
    app.include_router(az_blocks_router, prefix=settings.api_v1_prefix)
    app.include_router(az_wallet_router, prefix=settings.api_v1_prefix)
    app.include_router(btc_node_router, prefix=settings.api_v1_prefix)
    app.include_router(btc_wallet_router, prefix=settings.api_v1_prefix)
    app.include_router(node_router, prefix=settings.api_v1_prefix)
    app.include_router(services_router, prefix=settings.api_v1_prefix)
    app.include_router(translator_router, prefix=settings.api_v1_prefix)

    # Keep versioning centralized so changing API_V1_PREFIX updates all routes.
    app.include_router(tx_send.router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
