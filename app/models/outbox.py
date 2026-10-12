"""Outbox email ORM model for dry-run message logging."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import system_clock
from app.core.db import Base, UTCDateTime


class OutboxEmail(Base):
    """Persisted outbox email captured during dry-run operation."""

    __tablename__ = "outbox_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_eml: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="sent (dry-run)", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: system_clock.now(),
        nullable=False,
    )
