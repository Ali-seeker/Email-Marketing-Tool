"""FastAPI application entrypoint for ColdReach.

Provides REST API endpoints for campaign management, leads, settings, and health check.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import init_db
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown hooks."""
    setup_logging()
    settings = get_settings()
    init_db()
    logger.info(
        "ColdReach starting up. Database initialized. SAFE_BY_DEFAULT active: LIVE_MODE=%s",
        settings.LIVE_MODE,
    )
    yield
    logger.info("ColdReach shutting down.")


def create_app() -> FastAPI:
    """Factory creating and configuring the FastAPI instance."""
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health_check() -> dict[str, object]:
        """Health check endpoint exposing runtime safety status."""
        return {
            "status": "ok",
            "app_name": settings.APP_NAME,
            "live_mode": settings.LIVE_MODE,
            "environment": settings.ENVIRONMENT,
        }

    return app


app = create_app()
