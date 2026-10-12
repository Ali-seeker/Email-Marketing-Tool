"""Lead ORM model."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import system_clock
from app.core.db import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.email_message import EmailMessage
    from app.models.event import Event


class LeadStatus(StrEnum):
    """Enumeration of lead states across the outreach lifecycle."""

    NEW = "new"
    VALID = "valid"
    INVALID = "invalid"
    PERSONALIZED = "personalized"
    APPROVED = "approved"
    NEEDS_MANUAL_NOTE = "needs_manual_note"
    IN_SEQUENCE = "in_sequence"
    REPLIED = "replied"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"
    COMPLETED = "completed"
    PAUSED = "paused"
    WON = "won"


class RiskLevel(StrEnum):
    """Categorical risk assessment for email deliverability."""

    OK = "ok"
    RISKY = "risky"
    BAD = "bad"


class Lead(Base):
    """Lead target profile imported for cold email campaigns."""

    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("campaign_id", "email", name="uq_campaign_lead_email"),
        Index("ix_leads_campaign_status", "campaign_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    first_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    personal_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        default=LeadStatus.NEW.value,
        nullable=False,
        index=True,
    )
    risk_level: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    validation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
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

    # Relationships
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="leads")
    emails: Mapped[list["EmailMessage"]] = relationship(
        "EmailMessage",
        back_populates="lead",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="lead",
        cascade="all, delete-orphan",
    )
