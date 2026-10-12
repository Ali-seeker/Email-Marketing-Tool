"""Reply ORM model for storing lead inbound responses."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import system_clock
from app.core.db import Base, UTCDateTime

if TYPE_CHECKING:
    from app.models.email_message import EmailMessage
    from app.models.lead import Lead


class Reply(Base):
    """Captured inbound lead reply record."""

    __tablename__ = "replies"
    __table_args__ = (
        Index("ix_replies_lead_id", "lead_id"),
        Index("ix_replies_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
    )
    email_message_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("email_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    to_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str] = mapped_column(String(500), nullable=False)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    is_positive_hint: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: system_clock.now(),
        nullable=False,
    )

    lead: Mapped["Lead | None"] = relationship("Lead", backref="replies")
    email_message: Mapped["EmailMessage | None"] = relationship("EmailMessage", backref="replies")
