"""FastAPI dependency providers for authentication, database session, and clock."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import get_settings
from app.core.db import SessionLocal

# OpenAPI security scheme definition for Swagger UI
api_key_header = APIKeyHeader(name="X-API-Token", auto_error=False)


def get_db() -> Generator[Session, None, None]:
    """Provide scoped SQLAlchemy database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_clock() -> Clock:
    """Provide injectable Clock instance (defaults to system_clock)."""
    return system_clock


def verify_api_token(
    request: Request,
    api_key_header_val: Annotated[str | None, Depends(api_key_header)] = None,
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
) -> str:
    """Validate X-API-Token header against configured API_TOKEN.

    Raises:
        HTTPException(401): If token is missing, empty, or mismatched.
    """
    settings = get_settings()
    expected_token = settings.API_TOKEN.get_secret_value()

    # Check header from either alias or OpenAPI dependency
    provided_token = x_api_token or api_key_header_val

    # Also allow query parameter 'token' for EventSource / SSE clients
    if not provided_token and request.query_params.get("token"):
        provided_token = request.query_params.get("token")

    if not provided_token or provided_token.strip() != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token. Provide header 'X-API-Token'.",
        )
    return provided_token


class Pagination:
    """Standardized pagination parameter dependency."""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="Page number (1-based)")] = 1,
        page_size: Annotated[int, Query(ge=1, le=200, description="Items per page")] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size
        self.offset = (page - 1) * page_size
