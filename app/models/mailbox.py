"""Mailbox ORM model for multi-account sending and inbound reply monitoring."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import system_clock
from app.core.db import Base

if TYPE_CHECKING:
    from app.models.email_message import EmailMessage
    from app.models.lead import Lead


class Mailbox(Base):
    """Sending and receiving mailbox account (e.g. Gmail / Google Workspace / Outlook)."""

    __tablename__ = "mailboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(150), default="ColdReach Operator", nullable=False)

    # SMTP Configuration (Outbound dispatch)
    smtp_host: Mapped[str] = mapped_column(String(255), default="smtp.gmail.com", nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, default=465, nullable=False)
    smtp_username: Mapped[str] = mapped_column(String(320), nullable=False)
    smtp_password: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # IMAP Configuration (Inbound reply & bounce monitoring)
    imap_host: Mapped[str] = mapped_column(String(255), default="imap.gmail.com", nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, default=993, nullable=False)
    imap_username: Mapped[str] = mapped_column(String(320), nullable=False)
    imap_password: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Operational safety limits
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

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
    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="mailbox")
    emails: Mapped[list["EmailMessage"]] = relationship("EmailMessage", back_populates="mailbox")
