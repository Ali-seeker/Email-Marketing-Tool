"""Web page scraping cache model."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import system_clock
from app.core.db import Base


class WebPageCache(Base):
    """Database cache of scraped website content (HTML metadata & visible text)."""

    __tablename__ = "web_page_cache"
    __table_args__ = (
        Index("ix_web_cache_domain", "domain"),
        Index("ix_web_cache_url", "url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    headings: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    visible_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
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

    def is_fresh(self, now: datetime, ttl_days: int = 30) -> bool:
        """Check whether cached page content is within the TTL window (default 30 days)."""
        ref_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        ref_updated = (
            self.updated_at
            if self.updated_at.tzinfo is not None
            else self.updated_at.replace(tzinfo=UTC)
        )
        return (ref_now - ref_updated) < timedelta(days=ttl_days)
