"""Application configuration and safety limits.

Enforces SAFE BY DEFAULT: LIVE_MODE is strictly False unless explicitly enabled.
Secrets and connection parameters are loaded from environment variables or .env.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Central configuration for ColdReach application."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SAFE BY DEFAULT: While False, NO real email can ever leave this machine.
    LIVE_MODE: bool = Field(
        default=False,
        description="Dry-run safety switch. Real emails only send if True.",
    )

    APP_NAME: str = "ColdReach"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = Field(
        default="sqlite:///data/coldreach.db",
        description="SQLAlchemy database connection URL (SQLite WAL mode).",
    )

    # Hard Safety Limits
    MAX_EMAILS_PER_DAY_PER_MAILBOX: int = Field(
        default=30,
        description="Hard daily dispatch cap per mailbox.",
    )
    MIN_SECONDS_BETWEEN_EMAILS: int = Field(
        default=120,
        description="Minimum seconds of pause between consecutive outbound emails.",
    )
    BOUNCE_RATE_STOP_THRESHOLD_PERCENT: float = Field(
        default=2.0,
        description="Bounce percentage that triggers an immediate campaign halt.",
    )

    # SMTP Configuration (live outreach)
    SMTP_HOST: str = "smtp.example.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = "user@example.com"
    SMTP_PASSWORD: SecretStr = SecretStr("")
    SMTP_USE_TLS: bool = True
    SMTP_FROM_EMAIL: str = "user@example.com"
    SMTP_FROM_NAME: str = "ColdReach Operator"

    # IMAP Configuration (inbound reply & bounce monitoring)
    IMAP_HOST: str = "imap.example.com"
    IMAP_PORT: int = 993
    IMAP_USERNAME: str = "user@example.com"
    IMAP_PASSWORD: SecretStr = SecretStr("")
    IMAP_USE_SSL: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
