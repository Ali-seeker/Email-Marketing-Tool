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
    API_TOKEN: SecretStr = Field(
        default=SecretStr("coldreach-test-secret-token-12345"),
        description="Secret token required in X-API-Token header for all REST endpoints.",
    )

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

    # Outreach Schedule & Planner Settings
    OUTREACH_START_DATE: str = Field(
        default="2026-11-01",
        description="Date operator warm-up completed; no cold outreach sent before this date.",
    )
    HARD_MAX_PER_DAY: int = Field(
        default=30,
        description="Hard maximum dispatch limit per day that cannot be exceeded by any setting.",
    )
    SEND_DAYS: list[str] = Field(
        default=["Tue", "Wed", "Thu"],
        description="Allowed outreach days in lead-local timezone.",
    )
    SEND_WINDOW_START: str = Field(
        default="09:00",
        description="Start time of daily outreach window in lead-local time (HH:MM).",
    )
    SEND_WINDOW_END: str = Field(
        default="11:30",
        description="End time of daily outreach window in lead-local time (HH:MM).",
    )
    MIN_GAP_SECONDS: int = Field(
        default=120,
        description="Minimum seconds gap between consecutive sends from the same mailbox.",
    )
    MAX_JITTER_SECONDS: int = Field(
        default=180,
        description="Maximum random jitter in seconds added to MIN_GAP_SECONDS.",
    )
    MAILBOX_TZ: str = Field(
        default="America/New_York",
        description="Operator mailbox timezone used for counting the day.",
    )

    # Email Validation Settings
    ALLOW_RISKY: bool = Field(
        default=False,
        description="Allow risky emails to be marked as valid. If False, they stay invalid.",
    )
    HUNTER_API_KEY: SecretStr | None = Field(
        default=None,
        description="Optional Hunter.io API key for secondary verification.",
    )
    HUNTER_MONTHLY_FREE_QUOTA: int = Field(
        default=25,
        description="Monthly free API verification quota for Hunter.io.",
    )

    # Template Personalization & Sending Identity
    REQUIRE_PERSONAL_NOTE: bool = Field(
        default=True,
        description="If True, leads without a personal_note stay in draft.",
    )
    SENDER_NAME: str = "Alex Mercer"
    SENDER_COMPANY: str = "Agentic Software"
    SENDER_WEBSITE: str = "https://agenticsoftware.com"
    SENDER_ADDRESS: str = "100 Pine St, Suite 1250, San Francisco, CA"
    PORTFOLIO_URL: str = "https://agenticsoftware.com/work"
    DEFAULT_SERVICE: str = "bespoke AI automation pipelines"
    DEFAULT_EXAMPLE_RESULT: str = "reclaiming 15+ engineering hours weekly"

    # SMTP Configuration (live outreach)
    SMTP_HOST: str = "smtp.example.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = "user@example.com"
    SMTP_PASSWORD: SecretStr = SecretStr("")
    SMTP_USE_TLS: bool = True
    SMTP_FROM_EMAIL: str = "user@example.com"
    SMTP_FROM_NAME: str = "ColdReach Operator"

    # IMAP Configuration (inbound reply & bounce monitoring)
    IMAP_HOST: str = "imap.gmail.com"
    IMAP_PORT: int = 993
    IMAP_USER: str = "user@example.com"
    IMAP_APP_PASSWORD: SecretStr = SecretStr("")
    IMAP_USE_SSL: bool = True
    INBOX_POLL_INTERVAL_SECONDS: int = 15

    # Safety & Alerting Configuration
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    ALERT_EMAIL: str | None = None
    ALERT_SMTP_HOST: str | None = None
    ALERT_SMTP_PORT: int = 587
    ALERT_SMTP_USERNAME: str | None = None
    ALERT_SMTP_PASSWORD: SecretStr = SecretStr("")
    DAILY_SUMMARY_HOUR: int = 9

    # LLM & Personalization Settings
    LLM_PROVIDER: str = Field(
        default="gemini",
        description="LLM provider: gemini, ollama, claude, or fake",
    )
    GEMINI_API_KEY: SecretStr | None = None
    GEMINI_MODEL: str = "gemini-1.5-flash"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
    ANTHROPIC_API_KEY: SecretStr | None = None
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    LLM_DAILY_CALL_CAP: int = 200
    AUTO_APPROVE: bool = Field(
        default=False,
        description="Automatically approve valid AI-generated personal notes.",
    )

    # Web Fetcher & Scraping Settings
    WEB_FETCHER_TIMEOUT_SECONDS: float = 10.0
    WEB_FETCHER_MAX_BYTES: int = 500 * 1024  # 500 KB limit
    WEB_FETCHER_MAX_REDIRECTS: int = 3
    WEB_CACHE_TTL_DAYS: int = 30

    @property
    def imap_username(self) -> str:
        """Resolve IMAP username."""
        return self.IMAP_USER

    @property
    def imap_password(self) -> str:
        """Resolve IMAP plain password."""
        return self.IMAP_APP_PASSWORD.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
