"""Event audit trail ORM model."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import system_clock
from app.core.db import Base

if TYPE_CHECKING:
    from app.models.lead import Lead


class EventType(StrEnum):
    """Categorical types of outreach and system events."""

    IMPORTED = "imported"
    VALIDATED = "validated"
    SENT = "sent"
    BOUNCED = "bounced"
    SOFT_BOUNCE = "soft_bounce"
    REPLIED = "replied"
    AUTO_REPLY = "auto_reply"
    UNSUBSCRIBED = "unsubscribed"
    KILLSWITCH = "killswitch"
    ERROR = "error"


class Event(Base):
    """Immutable event log capturing all actions, transitions, and external signals."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_type_created_at", "type", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: system_clock.now(),
        nullable=False,
    )

    # Relationships
    lead: Mapped["Lead | None"] = relationship("Lead", back_populates="events")
