from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rideassist_common.config import get_settings
from rideassist_common.db import get_session
from rideassist_common.observability import RequestContextMiddleware, configure_logging
from rideassist_common.redis import get_redis


def create_app(
    service: str,
    routers: Sequence[APIRouter],
    lifespan: Any = None,
    description: str = "",
) -> FastAPI:
    """Uniform service skeleton: logging, request IDs, CORS and liveness/readiness probes."""
    configure_logging(service)
    settings = get_settings()
    app = FastAPI(
        title=f"RideAssist · {service}",
        description=description,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.get("/livez", include_in_schema=False)
    async def livez() -> dict:
        return {"status": "ok", "service": service}

    @app.get("/health", include_in_schema=False)
    async def health(
        session: AsyncSession = Depends(get_session), redis=Depends(get_redis)
    ) -> JSONResponse:
        checks = {}
        try:
            await session.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception as exc:  # noqa: BLE001 - surfaced in the health payload
            checks["postgres"] = f"error: {exc.__class__.__name__}"
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {exc.__class__.__name__}"
        healthy = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if healthy else "degraded", "service": service, "checks": checks},
            status_code=200 if healthy else 503,
        )

    for router in routers:
        app.include_router(router)
    return app
