"""Campaign ORM model."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import system_clock
from app.core.db import Base

if TYPE_CHECKING:
    from app.models.lead import Lead
    from app.models.sequence import SequenceStep


class CampaignStatus(StrEnum):
    """Enumeration of possible campaign states."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class Campaign(Base):
    """Campaign grouping outbound leads, email sequences, and outreach settings."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    niche: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        default=CampaignStatus.DRAFT.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: system_clock.now(),
        nullable=False,
    )

    # Relationships
    leads: Mapped[list["Lead"]] = relationship(
        "Lead",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    sequence_steps: Mapped[list["SequenceStep"]] = relationship(
        "SequenceStep",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="SequenceStep.step",
    )
