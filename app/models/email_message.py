"""EmailMessage ORM model."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.lead import Lead


class EmailMessageStatus(StrEnum):
    """Lifecycle status for individual outbound sequence messages."""

    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class EmailMessage(Base):
    """Outbound sequence email message generated for a lead."""

    __tablename__ = "email_messages"
    __table_args__ = (Index("ix_email_messages_status_scheduled_at", "status", "scheduled_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        default=EmailMessageStatus.DRAFT.value,
        nullable=False,
        index=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_variant_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_variant_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    lead: Mapped["Lead"] = relationship("Lead", back_populates="emails")
