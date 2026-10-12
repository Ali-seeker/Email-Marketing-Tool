"""Sequence step ORM model for campaign outreach sequences."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import system_clock
from app.core.db import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign


class SequenceStep(Base):
    """Step in a campaign outreach sequence containing subject and body variants."""

    __tablename__ = "sequence_steps"
    __table_args__ = (UniqueConstraint("campaign_id", "step", name="uq_campaign_sequence_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    send_as_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subjects: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    bodies: Mapped[list[str]] = mapped_column(JSON, nullable=False)
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
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="sequence_steps")
