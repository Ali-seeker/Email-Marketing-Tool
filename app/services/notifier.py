"""Alerting and notification subsystem.

Provides multi-channel alerts for health warnings, emergency kill-switch trips,
and once-a-day operational summaries with rate-limiting and dashboard banner feeds.
"""

import email.message
import hashlib
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.event import Event, EventType
from app.models.setting import Setting

logger = get_logger(__name__)


@dataclass
class AlertRecord:
    """Structured representation of a dispatched alert."""

    level: str
    title: str
    message: str
    payload: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Notifier(ABC):
    """Abstract interface for dispatching system notifications."""

    @abstractmethod
    def notify(
        self,
        level: str,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Dispatch notification across configured channel.

        Args:
            level: Alert severity ('INFO', 'WARNING', 'CRITICAL').
            title: Short descriptive headline.
            message: Full diagnostic narrative.
            payload: Optional structured metadata.

        Returns:
            True if delivered successfully, False if skipped or failed.
        """
        pass


class LogNotifier(Notifier):
    """Fallback notifier that writes alerts to standard application logs."""

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        log_level = level.upper()
        log_text = f"[{log_level}] {title} - {message}"
        if log_level == "CRITICAL":
            logger.critical(log_text)
        elif log_level == "WARNING":
            logger.warning(log_text)
        else:
            logger.info(log_text)
        return True


class TelegramNotifier(Notifier):
    """Dispatches notifications via Telegram Bot API using httpx."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        token = self.settings.TELEGRAM_BOT_TOKEN
        chat_id = self.settings.TELEGRAM_CHAT_ID

        if not token or not chat_id:
            logger.debug("Telegram notification skipped: credentials not configured.")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        text = f"🚨 [{level.upper()}] *{title}*\n\n{message}"
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                )
                return resp.status_code == 200
        except Exception as err:
            logger.error("Failed to deliver Telegram alert: %s", err)
            return False


class EmailNotifier(Notifier):
    """Dispatches notifications via dedicated alert SMTP channel."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        alert_email = self.settings.ALERT_EMAIL
        if not alert_email or not alert_email.strip():
            logger.debug("Email notification skipped: ALERT_EMAIL not set.")
            return False

        smtp_host = self.settings.ALERT_SMTP_HOST or self.settings.SMTP_HOST
        smtp_port = self.settings.ALERT_SMTP_PORT or self.settings.SMTP_PORT
        smtp_user = self.settings.ALERT_SMTP_USERNAME or self.settings.SMTP_USERNAME

        if not self.settings.LIVE_MODE:
            logger.info(
                "[DRY-RUN ALERT EMAIL] To: %s | Subject: [%s] %s | Body: %s",
                alert_email,
                level.upper(),
                title,
                message,
            )
            return True

        msg = email.message.EmailMessage()
        msg["From"] = smtp_user
        msg["To"] = alert_email
        msg["Subject"] = f"[{level.upper()}] ColdReach Alert: {title}"
        msg.set_content(message)

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10.0) as server:
                server.starttls()
                pwd = (
                    self.settings.ALERT_SMTP_PASSWORD.get_secret_value()
                    if self.settings.ALERT_SMTP_PASSWORD
                    else self.settings.SMTP_PASSWORD.get_secret_value()
                )
                if smtp_user and pwd:
                    server.login(smtp_user, pwd)
                server.send_message(msg)
            return True
        except Exception as err:
            logger.error("Failed to send alert email: %s", err)
            return False


class FakeNotifier(Notifier):
    """In-memory mock notifier recording all alerts for deterministic test assertion."""

    def __init__(self) -> None:
        self.alerts: list[AlertRecord] = []

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        record = AlertRecord(
            level=level.upper(),
            title=title,
            message=message,
            payload=payload,
        )
        self.alerts.append(record)
        return True

    def clear(self) -> None:
        """Clear recorded alerts."""
        self.alerts.clear()


class CompositeNotifier(Notifier):
    """Broadcasts notifications across all attached notifiers."""

    def __init__(self, notifiers: list[Notifier] | None = None) -> None:
        self.notifiers = notifiers or [LogNotifier()]

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        any_success = False
        for notifier in self.notifiers:
            try:
                res = notifier.notify(level, title, message, payload)
                if res:
                    any_success = True
            except Exception as err:
                logger.error("Notifier %s failed: %s", notifier, err)
        return any_success


# =========================================================================
# Rate-Limiting & Dashboard Banner Support
# =========================================================================


def _normalize_alert_key(title: str) -> str:
    """Create a sanitized deterministic key for rate-limiting identical alerts."""
    clean = title.strip().lower()
    return hashlib.md5(clean.encode("utf-8")).hexdigest()[:16]


def is_alert_rate_limited(db: Session, title: str, clock: Clock) -> bool:
    """Check if the same alert headline was already dispatched within the last hour.

    Args:
        db: SQLAlchemy session.
        title: Alert headline or key identifier.
        clock: Injectable clock.

    Returns:
        True if the alert is rate-limited (sent < 1 hour ago), False otherwise.
    """
    key = f"ALERT_RL_{_normalize_alert_key(title)}"
    record = db.scalar(select(Setting.value).where(Setting.key == key))
    if not record:
        return False

    try:
        last_dt = datetime.fromisoformat(record)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        now_dt = clock.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)

        return (now_dt - last_dt) < timedelta(hours=1)
    except Exception:
        return False


def record_alert_dispatched(
    db: Session,
    level: str,
    title: str,
    message: str,
    clock: Clock,
    payload: dict[str, Any] | None = None,
) -> None:
    """Store alert state, emit audit event, and update the 1-hour rate-limiting checkpoint."""
    now_dt = clock.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    # 1. Update rate limit setting
    key = f"ALERT_RL_{_normalize_alert_key(title)}"
    setting = db.scalar(select(Setting).where(Setting.key == key))
    if setting:
        setting.value = now_dt.isoformat()
    else:
        db.add(Setting(key=key, value=now_dt.isoformat()))

    # 2. Persist audit Event for history and dashboard banner feeds
    db.add(
        Event(
            lead_id=None,
            type="alert",
            payload={
                "level": level.upper(),
                "title": title,
                "message": message,
                "data": payload or {},
            },
            created_at=now_dt,
        )
    )
    db.commit()


def get_latest_alerts(db: Session, limit: int = 5) -> list[dict[str, Any]]:
    """Retrieve the most recent system alerts for dashboard banner rendering.

    Returns:
        List of dicts containing level, title, message, and timestamp.
    """
    stmt = (
        select(Event)
        .where(Event.type.in_(("alert", EventType.KILLSWITCH.value)))
        .order_by(Event.created_at.desc())
        .limit(limit)
    )
    events = db.scalars(stmt).all()
    results: list[dict[str, Any]] = []

    for ev in events:
        payload = ev.payload or {}
        results.append(
            {
                "id": ev.id,
                "type": ev.type,
                "level": payload.get("level", "CRITICAL" if ev.type == "killswitch" else "INFO"),
                "title": payload.get("title", f"System {ev.type.upper()}"),
                "message": payload.get("message", payload.get("reason", "")),
                "created_at": ev.created_at.isoformat() if ev.created_at else "",
            }
        )
    return results


def send_daily_summary(
    db: Session,
    notifier: Notifier,
    clock: Clock | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Send once-a-day operational health summary at configurable hour (default 09:00).

    Args:
        db: SQLAlchemy session.
        notifier: Target notification channel.
        clock: Optional Clock implementation.
        force: If True, bypass hour and once-per-day checks.

    Returns:
        Summary metrics dictionary if dispatched, None if skipped.
    """

    from app.models.email_message import EmailMessage, EmailMessageStatus
    from app.services.planner import get_mailbox_tz

    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    local_now = now.astimezone(mailbox_tz)
    today_str = local_now.date().isoformat()

    settings = get_settings()
    summary_hour = settings.DAILY_SUMMARY_HOUR

    last_date_setting = db.scalar(
        select(Setting.value).where(Setting.key == "LAST_DAILY_SUMMARY_DATE")
    )
    if not force:
        # Check hour of day
        if local_now.hour < summary_hour:
            return None
        # Check already sent today
        if last_date_setting and last_date_setting == today_str:
            return None

    # Calculate 24h operational window metrics
    since_24h = now - timedelta(hours=24)

    sent_count = (
        db.scalar(
            select(func.count(EmailMessage.id)).where(
                EmailMessage.sent_at >= since_24h,
                EmailMessage.status.in_(
                    (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
                ),
            )
        )
        or 0
    )

    replies_count = (
        db.scalar(
            select(func.count(Event.id)).where(
                Event.created_at >= since_24h,
                Event.type == EventType.REPLIED.value,
            )
        )
        or 0
    )

    bounces_count = (
        db.scalar(
            select(func.count(Event.id)).where(
                Event.created_at >= since_24h,
                Event.type == EventType.BOUNCED.value,
            )
        )
        or 0
    )

    ks_status = db.scalar(select(Setting.value).where(Setting.key == "KILLSWITCH")) or "off"

    summary_data = {
        "date": today_str,
        "sent_24h": sent_count,
        "replies_24h": replies_count,
        "bounces_24h": bounces_count,
        "killswitch": ks_status.upper(),
    }

    body = (
        f"[DAILY SUMMARY] ColdReach Health Summary ({today_str})\n"
        f"- Emails Dispatched (24h): {sent_count}\n"
        f"- Replies Received (24h): {replies_count}\n"
        f"- Hard Bounces (24h): {bounces_count}\n"
        f"- Emergency Kill-Switch: {ks_status.upper()}"
    )

    notifier.notify(
        level="INFO",
        title=f"Daily Summary: {today_str}",
        message=body,
        payload=summary_data,
    )

    # Record checkpoint
    setting = db.scalar(select(Setting).where(Setting.key == "LAST_DAILY_SUMMARY_DATE"))
    if setting:
        setting.value = today_str
    else:
        db.add(Setting(key="LAST_DAILY_SUMMARY_DATE", value=today_str))
    db.commit()

    return summary_data
