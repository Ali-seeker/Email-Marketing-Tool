"""External email verification interface and implementations.

Supports NullVerifier, HunterVerifier, and FakeEmailVerifier.
Adheres to RULES.md: all external API systems are behind an abstract interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.lead import RiskLevel
from app.models.setting import Setting

logger = get_logger(__name__)


@dataclass
class VerifierResult:
    """Outcome of an external email verification check."""

    status: str  # e.g., 'valid', 'accept_all', 'unknown', 'invalid', 'quota_exceeded'
    risk_level: str  # RiskLevel.OK, RiskLevel.RISKY, RiskLevel.BAD
    raw: dict[str, Any] | None = None


class EmailVerifier(ABC):
    """Abstract interface for external email verification providers."""

    @abstractmethod
    def verify(self, email: str, db: Session, clock: Clock) -> VerifierResult | None:
        """Verify email against external verification service.

        Args:
            email: Target email address.
            db: SQLAlchemy session for quota and credit tracking.
            clock: Application clock.

        Returns:
            VerifierResult or None if verifier is inactive or skipped.
        """
        pass


class NullVerifier(EmailVerifier):
    """Default no-op verifier when external verification is disabled."""

    def verify(self, email: str, db: Session, clock: Clock) -> VerifierResult | None:
        """Return None without performing any external call."""
        return None


class HunterVerifier(EmailVerifier):
    """Hunter.io email verifier with monthly credit quota enforcement."""

    def __init__(self, api_key: str | None = None, monthly_quota: int = 25) -> None:
        """Initialize Hunter.io verifier."""
        settings = get_settings()
        self.api_key = api_key or (
            settings.HUNTER_API_KEY.get_secret_value() if settings.HUNTER_API_KEY else ""
        )
        self.monthly_quota = monthly_quota or settings.HUNTER_MONTHLY_FREE_QUOTA

    def _get_monthly_key(self, clock: Clock) -> str:
        """Format monthly key for tracking Hunter credits, e.g. hunter_credits_2026_10."""
        return f"hunter_credits_{clock.now().strftime('%Y_%m')}"

    def get_used_credits(self, db: Session, clock: Clock) -> int:
        """Fetch count of Hunter credits consumed in the current calendar month."""
        key = self._get_monthly_key(clock)
        setting = db.scalar(select(Setting).where(Setting.key == key))
        if not setting or not setting.value:
            return 0
        try:
            return int(setting.value)
        except ValueError:
            return 0

    def _increment_credits(self, db: Session, clock: Clock) -> int:
        """Increment and persist credit usage count."""
        key = self._get_monthly_key(clock)
        setting = db.scalar(select(Setting).where(Setting.key == key))
        if not setting:
            setting = Setting(key=key, value="1", updated_at=clock.now())
            db.add(setting)
            new_val = 1
        else:
            try:
                current_val = int(setting.value)
            except ValueError:
                current_val = 0
            new_val = current_val + 1
            setting.value = str(new_val)
            setting.updated_at = clock.now()
        db.commit()
        return new_val

    def _set_quota_exceeded(self, db: Session, clock: Clock, used: int) -> None:
        """Record quota exceeded flag and alert details in Setting table."""
        import json

        now_dt = clock.now()
        flag = db.scalar(select(Setting).where(Setting.key == "HUNTER_QUOTA_EXCEEDED"))
        if not flag:
            db.add(Setting(key="HUNTER_QUOTA_EXCEEDED", value="true", updated_at=now_dt))
        else:
            flag.value = "true"
            flag.updated_at = now_dt

        dismissed = db.scalar(select(Setting).where(Setting.key == "HUNTER_QUOTA_ALERT_DISMISSED"))
        if not dismissed:
            db.add(Setting(key="HUNTER_QUOTA_ALERT_DISMISSED", value="false", updated_at=now_dt))
        else:
            dismissed.value = "false"
            dismissed.updated_at = now_dt

        details = db.scalar(select(Setting).where(Setting.key == "HUNTER_QUOTA_INFO"))
        payload_str = json.dumps(
            {
                "used": used,
                "quota": self.monthly_quota,
                "fallback_active": True,
                "exceeded_at": now_dt.isoformat(),
            }
        )
        if not details:
            db.add(Setting(key="HUNTER_QUOTA_INFO", value=payload_str, updated_at=now_dt))
        else:
            details.value = payload_str
            details.updated_at = now_dt
        db.commit()

    def verify(self, email: str, db: Session, clock: Clock) -> VerifierResult | None:
        """Query Hunter.io email verification API if quota allows."""
        if not self.api_key:
            return None

        used = self.get_used_credits(db, clock)
        if used >= self.monthly_quota:
            logger.warning(
                "Hunter.io quota reached (%d/%d used). "
                "Skipping external check for %s; using local validation fallback.",
                used,
                self.monthly_quota,
                email,
            )
            self._set_quota_exceeded(db, clock, used)
            return None

        url = "https://api.hunter.io/v2/email-verifier"
        params = {"email": email, "api_key": self.api_key}

        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url, params=params)
                if resp.status_code == 200:
                    self._increment_credits(db, clock)
                    data = resp.json().get("data", {})
                    status = data.get("status", "unknown")

                    if status == "valid":
                        risk = RiskLevel.OK.value
                    elif status in ("accept_all", "unknown", "webmail"):
                        risk = RiskLevel.RISKY.value
                    else:
                        risk = RiskLevel.BAD.value

                    return VerifierResult(status=status, risk_level=risk, raw=data)
                elif resp.status_code == 429:
                    logger.error(
                        "Hunter API quota exhausted (HTTP 429). Falling back to local validation."
                    )
                    self._set_quota_exceeded(db, clock, self.monthly_quota)
                    return None
                elif resp.status_code == 401:
                    logger.error(
                        "Hunter API authentication failed (HTTP 401). Check HUNTER_API_KEY."
                    )
                    return None
        except Exception as err:
            logger.error("Hunter API call failed: %s; falling back to local validation", err)

        return None


def get_default_verifier(db: Session | None = None) -> EmailVerifier:
    """Resolve active EmailVerifier based on configuration or database setting."""
    settings = get_settings()
    api_key: str = ""
    if settings.HUNTER_API_KEY:
        api_key = settings.HUNTER_API_KEY.get_secret_value()

    if not api_key and db:
        db_key = db.scalar(select(Setting.value).where(Setting.key == "HUNTER_API_KEY"))
        if db_key and str(db_key).strip():
            api_key = str(db_key).strip()

    if api_key:
        quota = settings.HUNTER_MONTHLY_FREE_QUOTA
        if db:
            db_quota = db.scalar(
                select(Setting.value).where(Setting.key == "HUNTER_MONTHLY_FREE_QUOTA")
            )
            if db_quota and str(db_quota).isdigit():
                quota = int(db_quota)
        return HunterVerifier(api_key=api_key, monthly_quota=quota)

    return NullVerifier()


class FakeEmailVerifier(EmailVerifier):
    """Deterministic mock verifier for automated test suites."""

    def __init__(self, monthly_quota: int = 25) -> None:
        """Initialize mock verifier with preconfigured responses and quota."""
        self._responses: dict[str, VerifierResult] = {}
        self.call_count = 0
        self.monthly_quota = monthly_quota

    def set_result(self, email: str, status: str, risk_level: str) -> None:
        """Set expected verification response for an email."""
        self._responses[email.strip().lower()] = VerifierResult(
            status=status,
            risk_level=risk_level,
        )

    def verify(self, email: str, db: Session, clock: Clock) -> VerifierResult | None:
        """Simulate verification check with quota enforcement."""
        key = f"hunter_credits_{clock.now().strftime('%Y_%m')}"
        setting = db.scalar(select(Setting).where(Setting.key == key))
        used = int(setting.value) if setting and setting.value.isdigit() else 0

        if used >= self.monthly_quota:
            return None

        self.call_count += 1
        # Track credits in database
        if not setting:
            setting = Setting(key=key, value="1", updated_at=clock.now())
            db.add(setting)
        else:
            setting.value = str(used + 1)
            setting.updated_at = clock.now()
        db.commit()

        norm_email = email.strip().lower()
        if norm_email in self._responses:
            return self._responses[norm_email]

        return VerifierResult(status="valid", risk_level=RiskLevel.OK.value)
