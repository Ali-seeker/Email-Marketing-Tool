"""FastAPI application entrypoint for ColdReach.

Provides versioned REST API endpoints for campaigns, leads, approval queue,
enrollment, planning, emails, replies, templates, suppression, stats, and safety.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_v1_router
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
        title=f"{settings.APP_NAME} Outreach Automation API",
        version="1.0.0",
        description=(
            "REST API for cold email outreach automation, domain safety monitoring, "
            "AI-grounded lead personalization, and human-in-the-loop review queues."
        ),
        lifespan=lifespan,
    )

    # CORS: Allow localhost and 127.0.0.1 dev server origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Consistent error response formatting
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": True,
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": True,
                "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
                "detail": str(exc),
            },
        )

    # Mount master v1 router
    app.include_router(api_v1_router)

    # Public unauthenticated health check endpoint
    @app.get("/api/health", tags=["Health"])
    def health_check() -> dict[str, object]:
        """Health check endpoint exposing runtime safety status."""
        curr_settings = get_settings()
        return {
            "status": "ok",
            "app_name": curr_settings.APP_NAME,
            "live_mode": curr_settings.LIVE_MODE,
            "environment": curr_settings.ENVIRONMENT,
        }

    return app


def validate_api_host(host: str, allow_external: bool = False) -> None:
    """Validate that API server binds only to loopback unless explicitly permitted."""
    local_interfaces = {"127.0.0.1", "localhost", "::1"}
    if host not in local_interfaces and not allow_external:
        raise ValueError(
            f"Security policy violation: API refuses to start on non-local interface '{host}' "
            "without explicit --allow-external flag or ALLOW_EXTERNAL_API=true setting."
        )


app = create_app()

if __name__ == "__main__":
    import argparse
    import sys

    import uvicorn

    parser = argparse.ArgumentParser(description="ColdReach REST API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="Explicitly permit non-local interface",
    )
    parser.add_argument("--reload", action="store_true", default=True, help="Enable reload")
    args = parser.parse_args()

    try:
        validate_api_host(args.host, allow_external=args.allow_external)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
