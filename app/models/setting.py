"""System settings key-value ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import system_clock
from app.core.db import Base


class Setting(Base):
    """Dynamic key-value application setting persisted in database."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: system_clock.now(),
        onupdate=lambda: system_clock.now(),
        nullable=False,
    )
