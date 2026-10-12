"""DNS lookup cache model."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import system_clock
from app.core.db import Base


class DomainDnsCache(Base):
    """Database cache of DNS MX and A record lookups per domain."""

    __tablename__ = "domain_dns_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    has_mx: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_a: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    records: Mapped[list[str] | dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: system_clock.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: system_clock.now(),
        onupdate=lambda: system_clock.now(),
        nullable=False,
    )
