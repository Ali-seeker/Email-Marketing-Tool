"""Suppression list ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import system_clock
from app.core.db import Base


class Suppression(Base):
    """Global do-not-contact suppression list."""

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="unsubscribed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: system_clock.now(),
        nullable=False,
    )
