"""Safety monitor, health metrics, and emergency kill-switch subsystem.

Implements strict health thresholds, multi-window deliverability tracking,
and automated kill-switch engagement to protect sender domain reputation:
- Minimum sample size enforcement (>= 20 sends) for all rate-based rules.
- Instant absolute rules (complaints, auth/block errors, 3 bounces in last 10).
- Explicit human typed acknowledgement with mandatory note for resumption.
- Post-resume daily capacity halving for 3 calendar days.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.logging import get_logger
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.reply import Reply
from app.models.setting import Setting
from app.services.notifier import (
    LogNotifier,
    Notifier,
    is_alert_rate_limited,
    record_alert_dispatched,
)

logger = get_logger(__name__)

# Keywords signaling abuse, spam reporting, or legal threats in incoming emails
COMPLAINT_KEYWORDS = (
    "spam",
    "report",
    "abuse",
    "harassment",
    "legal action",
)

# Authentication and account restriction error keywords
AUTH_OR_BLOCK_KEYWORDS = (
    "authentication failed",
    "auth failed",
    "invalid credentials",
    "login denied",
    "account suspended",
    "account blocked",
    "unusual activity",
    "temporary block",
    "535",
    "534",
)


@dataclass
class HealthMetrics:
    """Consolidated outreach health metrics calculated across sliding windows."""

    bounce_rate_100: float
    bounce_rate_7d: float
    bounce_rate: float
    total_sent_100: int
    total_sent_7d: int
    failure_rate: float
    total_attempts_50: int
    failed_attempts_50: int
    complaint_signals_7d: int
    unsubscribe_rate_100: float
    total_unsubscribes_100: int
    auth_or_block_errors: int
    hard_bounces_last_10: int
    total_sent_last_10: int
    sample_size_ok: bool  # True if send volume in rate window >= 20

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return asdict(self)


def calculate_health_metrics(db: Session, now: datetime) -> HealthMetrics:
    """Recalculate complete system health and deliverability metrics.

    Args:
        db: SQLAlchemy session.
        now: Current timestamp (timezone-aware).

    Returns:
        Populated HealthMetrics instance.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    # -------------------------------------------------------------------------
    # 1. 7-Day Window: Total Sent & Hard Bounces
    # -------------------------------------------------------------------------
    start_7d = now - timedelta(days=7)
    sent_7d = (
        db.scalar(
            select(func.count(EmailMessage.id)).where(
                EmailMessage.sent_at >= start_7d,
                EmailMessage.status.in_(
                    (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
                ),
            )
        )
        or 0
    )
    bounces_7d_raw = (
        db.scalar(
            select(func.count(func.distinct(Event.lead_id))).where(
                Event.created_at >= start_7d,
                Event.type == EventType.BOUNCED.value,
            )
        )
        or 0
    )
    bounces_7d = min(sent_7d, bounces_7d_raw)
    bounce_rate_7d = (bounces_7d / sent_7d * 100.0) if sent_7d > 0 else 0.0

    # -------------------------------------------------------------------------
    # 2. Last 100 Sends Window: Bounce Rate & Unsubscribe Rate
    # -------------------------------------------------------------------------
    recent_sent_records = db.execute(
        select(EmailMessage.id, EmailMessage.lead_id)
        .where(
            EmailMessage.status.in_(
                (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
            )
        )
        .order_by(EmailMessage.sent_at.desc())
        .limit(100)
    ).all()

    total_sent_100 = len(recent_sent_records)
    recent_lead_ids = [row[1] for row in recent_sent_records if row[1] is not None]

    if total_sent_100 > 0 and recent_lead_ids:
        bounces_100 = (
            db.scalar(
                select(func.count(Lead.id)).where(
                    Lead.id.in_(recent_lead_ids),
                    Lead.status == LeadStatus.BOUNCED.value,
                )
            )
            or 0
        )
        bounce_rate_100 = bounces_100 / total_sent_100 * 100.0

        unsubs_100 = (
            db.scalar(
                select(func.count(Lead.id)).where(
                    Lead.id.in_(recent_lead_ids),
                    Lead.status == LeadStatus.UNSUBSCRIBED.value,
                )
            )
            or 0
        )
        unsub_rate_100 = unsubs_100 / total_sent_100 * 100.0
    else:
        bounces_100 = 0
        bounce_rate_100 = 0.0
        unsubs_100 = 0
        unsub_rate_100 = 0.0

    overall_bounce_rate = max(bounce_rate_100, bounce_rate_7d)

    # -------------------------------------------------------------------------
    # 3. Last 50 Attempts Window: Failure Rate
    # -------------------------------------------------------------------------
    recent_attempts = db.scalars(
        select(EmailMessage)
        .where(
            EmailMessage.status.in_(
                (
                    EmailMessageStatus.SENT.value,
                    EmailMessageStatus.SENT_DRY_RUN.value,
                    EmailMessageStatus.FAILED.value,
                )
            )
        )
        .order_by(EmailMessage.id.desc())
        .limit(50)
    ).all()

    total_attempts_50 = len(recent_attempts)
    failed_attempts_50 = sum(
        1 for m in recent_attempts if m.status == EmailMessageStatus.FAILED.value
    )
    failure_rate = (
        (failed_attempts_50 / total_attempts_50 * 100.0) if total_attempts_50 > 0 else 0.0
    )

    # -------------------------------------------------------------------------
    # 4. 7-Day Window: Complaint Signals (Spam, Abuse, Legal threats)
    # -------------------------------------------------------------------------
    complaint_signals_7d = 0

    # 4a. Check in Reply table
    replies_7d = db.scalars(select(Reply).where(Reply.created_at >= start_7d)).all()
    for rep in replies_7d:
        content = f"{rep.subject or ''} {rep.snippet or ''} {rep.body or ''}".lower()
        if any(kw in content for kw in COMPLAINT_KEYWORDS):
            complaint_signals_7d += 1

    # 4b. Check in unsubscribe / suppression events
    unsub_events = db.scalars(
        select(Event).where(
            Event.created_at >= start_7d,
            Event.type == EventType.UNSUBSCRIBED.value,
        )
    ).all()
    for ev in unsub_events:
        payload_str = str(ev.payload or "").lower()
        if any(kw in payload_str for kw in COMPLAINT_KEYWORDS):
            complaint_signals_7d += 1

    # -------------------------------------------------------------------------
    # 5. Auth or Block / Suspension Errors
    # -------------------------------------------------------------------------
    # Count errors logged in Event or Setting table
    auth_errors = 0
    auth_setting_val = db.scalar(select(Setting.value).where(Setting.key == "AUTH_OR_BLOCK_ERRORS"))
    if auth_setting_val and auth_setting_val.isdigit():
        auth_errors += int(auth_setting_val)

    error_events = db.scalars(
        select(Event).where(
            Event.created_at >= start_7d,
            Event.type.in_((EventType.ERROR.value, EventType.KILLSWITCH.value)),
        )
    ).all()
    for ev in error_events:
        txt = str(ev.payload or "").lower()
        if any(kw in txt for kw in AUTH_OR_BLOCK_KEYWORDS):
            auth_errors += 1

    # -------------------------------------------------------------------------
    # 6. Last 10 Sends: 3 Hard Bounces Check (Absolute Rule)
    # -------------------------------------------------------------------------
    last_10_records = db.execute(
        select(EmailMessage.id, EmailMessage.lead_id)
        .where(
            EmailMessage.status.in_(
                (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
            )
        )
        .order_by(EmailMessage.sent_at.desc())
        .limit(10)
    ).all()

    total_sent_last_10 = len(last_10_records)
    lead_ids_last_10 = [row[1] for row in last_10_records if row[1] is not None]

    if lead_ids_last_10:
        bounces_last_10 = (
            db.scalar(
                select(func.count(Lead.id)).where(
                    Lead.id.in_(lead_ids_last_10),
                    Lead.status == LeadStatus.BOUNCED.value,
                )
            )
            or 0
        )
    else:
        bounces_last_10 = 0

    # Minimum sample size: at least 20 sends required before rate-based rules can fire
    sample_size_ok = total_sent_100 >= 20 or sent_7d >= 20

    return HealthMetrics(
        bounce_rate_100=bounce_rate_100,
        bounce_rate_7d=bounce_rate_7d,
        bounce_rate=overall_bounce_rate,
        total_sent_100=total_sent_100,
        total_sent_7d=sent_7d,
        failure_rate=failure_rate,
        total_attempts_50=total_attempts_50,
        failed_attempts_50=failed_attempts_50,
        complaint_signals_7d=complaint_signals_7d,
        unsubscribe_rate_100=unsub_rate_100,
        total_unsubscribes_100=unsubs_100,
        auth_or_block_errors=auth_errors,
        hard_bounces_last_10=bounces_last_10,
        total_sent_last_10=total_sent_last_10,
        sample_size_ok=sample_size_ok,
    )


def evaluate_safety_rules(metrics: HealthMetrics) -> tuple[str, list[str]]:
    """Evaluate health metrics against WARNING and CRITICAL thresholds.

    Rules:
    - CRITICAL (auto kill-switch ON, stop all sending):
      1. bounce_rate > 2.0% (if sample size >= 20)
      2. failure_rate >= 15.0% (if sample size >= 20 attempts)
      3. complaint_signals_7d >= 2 (ABSOLUTE)
      4. auth_or_block_errors >= 1 (ABSOLUTE)
      5. hard_bounces_last_10 >= 3 (ABSOLUTE)
    - WARNING (alert only, no killswitch):
      1. bounce_rate >= 1.5% (if sample size >= 20)
      2. unsubscribe_rate >= 3.0% (if sample size >= 20)
      3. failure_rate >= 5.0% (if sample size >= 20 attempts)

    Returns:
        Tuple of (severity_level, list_of_triggering_reasons).
    """
    critical_reasons: list[str] = []
    warning_reasons: list[str] = []

    # 1. Absolute Critical Rules (No minimum sample size required)
    if metrics.hard_bounces_last_10 >= 3:
        critical_reasons.append(
            f"Cluster of 3 hard bounces within last 10 sends ({metrics.hard_bounces_last_10}/10)"
        )

    if metrics.complaint_signals_7d >= 2:
        critical_reasons.append(
            f"{metrics.complaint_signals_7d} abuse/spam complaint signals detected in last 7 days"
        )

    if metrics.auth_or_block_errors >= 1:
        critical_reasons.append(
            f"{metrics.auth_or_block_errors} SMTP/IMAP authentication or provider block error(s)"
        )

    # 2. Rate-based Rules (Enforces minimum sample size >= 20)
    if metrics.sample_size_ok:
        if metrics.bounce_rate > 2.0:
            critical_reasons.append(
                f"Bounce rate {metrics.bounce_rate:.2f}% exceeded critical 2.0% threshold"
            )
        elif metrics.bounce_rate >= 1.5:
            warning_reasons.append(
                f"Bounce rate {metrics.bounce_rate:.2f}% reached warning threshold (>= 1.5%)"
            )

        if metrics.unsubscribe_rate_100 >= 3.0:
            warning_reasons.append(
                f"Unsubscribe rate {metrics.unsubscribe_rate_100:.2f}% "
                f"reached warning threshold (>= 3.0%)"
            )

    # Attempt-based failure rate (requires at least 20 attempts)
    if metrics.total_attempts_50 >= 20:
        if metrics.failure_rate >= 15.0:
            critical_reasons.append(
                f"Send failure rate {metrics.failure_rate:.2f}% exceeded critical 15.0% threshold"
            )
        elif metrics.failure_rate >= 5.0:
            warning_reasons.append(
                f"Send failure rate {metrics.failure_rate:.2f}% reached warning threshold (>= 5.0%)"
            )

    if critical_reasons:
        return "CRITICAL", critical_reasons
    if warning_reasons:
        return "WARNING", warning_reasons
    return "OK", []


def trigger_killswitch(
    db: Session,
    reason: str,
    metrics: HealthMetrics | dict[str, Any] | None = None,
    clock: Clock | None = None,
    notifier: Notifier | None = None,
) -> None:
    """Engage global emergency kill-switch, halt campaigns, and emit alerts.

    Args:
        db: SQLAlchemy session.
        reason: Summary of triggered safety violations.
        metrics: Health metrics snapshot.
        clock: Optional Clock implementation.
        notifier: Optional Notifier implementation.
    """
    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    # Update or insert settings
    db.merge(Setting(key="KILLSWITCH", value="on"))
    db.merge(Setting(key="KILLSWITCH_REASON", value=reason))
    db.merge(Setting(key="KILLSWITCH_TRIGGERED_AT", value=now_dt.isoformat()))

    metrics_dict = metrics.to_dict() if isinstance(metrics, HealthMetrics) else (metrics or {})
    db.merge(Setting(key="KILLSWITCH_METRICS", value=json.dumps(metrics_dict)))

    # Pause all active campaigns
    db.execute(
        update(Campaign)
        .where(Campaign.status == CampaignStatus.ACTIVE.value)
        .values(status=CampaignStatus.PAUSED.value)
    )

    # Emit audit Event
    db.add(
        Event(
            lead_id=None,
            type=EventType.KILLSWITCH.value,
            payload={
                "state": "triggered",
                "reason": reason,
                "metrics": metrics_dict,
                "triggered_at": now_dt.isoformat(),
            },
            created_at=now_dt,
        )
    )
    db.commit()

    logger.critical("CRITICAL: EMERGENCY KILL-SWITCH ENGAGED! Reason: %s", reason)

    # Dispatch alerts
    target_notifier = notifier or LogNotifier()
    target_notifier.notify(
        level="CRITICAL",
        title="EMERGENCY KILL-SWITCH ENGAGED",
        message=f"Sending halted immediately. Reason: {reason}",
        payload=metrics_dict,
    )
    record_alert_dispatched(
        db,
        level="CRITICAL",
        title="EMERGENCY KILL-SWITCH ENGAGED",
        message=reason,
        clock=clk,
        payload=metrics_dict,
    )


def resume_killswitch(
    acknowledgement: str,
    note: str,
    db: Session,
    clock: Clock | None = None,
    notifier: Notifier | None = None,
) -> bool:
    """Manually resume outreach after an emergency kill-switch trip.

    Requires:
    1. A typed acknowledgement statement ('I ACKNOWLEDGE' or 'I ACKNOWLEDGE AND RESUME').
    2. A mandatory explanatory human note.
    Effect:
    - Sets KILLSWITCH=off.
    - Halves the daily sending cap for the next 3 days.

    Args:
        acknowledgement: User-typed confirmation phrase.
        note: Explanatory rationale and remediation note.
        db: SQLAlchemy session.
        clock: Optional Clock implementation.
        notifier: Optional Notifier implementation.

    Returns:
        True upon successful resumption.

    Raises:
        ValueError: If acknowledgement or note is missing/invalid.
    """
    clean_ack = (acknowledgement or "").strip().upper()
    valid_acks = (
        "I ACKNOWLEDGE",
        "I ACKNOWLEDGE AND RESUME",
        "I UNDERSTAND AND ACCEPT RISK",
    )
    if clean_ack not in valid_acks:
        raise ValueError(
            f"Invalid acknowledgement '{acknowledgement}'. "
            f"Please type one of: {', '.join(valid_acks)}"
        )

    clean_note = (note or "").strip()
    if not clean_note or len(clean_note) < 5:
        raise ValueError("A descriptive human note (>= 5 chars) is required to resume.")

    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    db.merge(Setting(key="KILLSWITCH", value="off"))
    db.merge(Setting(key="KILLSWITCH_RESUMED_AT", value=now_dt.isoformat()))
    db.merge(Setting(key="KILLSWITCH_RESUME_NOTE", value=clean_note))
    db.merge(Setting(key="KILLSWITCH_RESUME_ACK", value=clean_ack))

    db.add(
        Event(
            lead_id=None,
            type=EventType.KILLSWITCH.value,
            payload={
                "state": "resumed",
                "note": clean_note,
                "ack": clean_ack,
                "resumed_at": now_dt.isoformat(),
                "cap_halved_days": 3,
            },
            created_at=now_dt,
        )
    )
    db.commit()

    logger.info("Outreach resumed from kill-switch. Note: %s. Cap halved for 3 days.", clean_note)

    target_notifier = notifier or LogNotifier()
    target_notifier.notify(
        level="INFO",
        title="Kill-Switch Resumed",
        message=f"Outreach resumed by operator. Note: {clean_note}. Daily cap halved for 3 days.",
        payload={"note": clean_note, "resumed_at": now_dt.isoformat()},
    )
    return True


@dataclass
class ResumeResult:
    """Outcome of resuming outreach after killswitch."""

    success: bool
    reduced_cap: int
    message: str


def resume_sending(
    db: Session,
    acknowledged_by: str = "operator",
    root_cause: str = "Issue resolved",
    clock: Clock | None = None,
    notifier: Notifier | None = None,
) -> ResumeResult:
    """Convenience resumption helper returning structured result and reduced capacity."""
    resume_killswitch(
        acknowledgement="I ACKNOWLEDGE AND RESUME",
        note=f"{acknowledged_by}: {root_cause}",
        db=db,
        clock=clock,
        notifier=notifier,
    )
    from app.services.planner import HARD_MAX_LIMIT

    reduced = max(1, HARD_MAX_LIMIT // 2)
    return ResumeResult(
        success=True,
        reduced_cap=reduced,
        message="Outreach resumed successfully. Daily cap halved for 3 days.",
    )


def is_killswitch_active(db: Session) -> bool:
    """Check if emergency global killswitch is currently engaged."""
    val = db.scalar(select(Setting.value).where(Setting.key == "KILLSWITCH"))
    return bool(val is not None and val.strip().lower() in ("on", "true", "1", "yes"))


def is_cap_halved_from_resume(
    db: Session,
    target_date: date | None = None,
    clock: Clock | None = None,
) -> bool:
    """Check if sending cap should be halved due to recent resume (within 3 days)."""
    resumed_at_str = db.scalar(select(Setting.value).where(Setting.key == "KILLSWITCH_RESUMED_AT"))
    if not resumed_at_str:
        return False

    try:
        resumed_dt = datetime.fromisoformat(resumed_at_str)
        resumed_date = resumed_dt.date()

        clk = clock or system_clock
        check_date = target_date or clk.now().date()

        diff_days = (check_date - resumed_date).days
        return 0 <= diff_days < 3
    except Exception:
        return False


def evaluate_and_enforce_safety(
    db: Session,
    notifier: Notifier | None = None,
    clock: Clock | None = None,
) -> tuple[str, list[str], HealthMetrics]:
    """Execute complete health audit, enforce safety rules, and dispatch alerts.

    Called after every outbound email dispatch and every inbound inbox poll cycle.

    Args:
        db: SQLAlchemy session.
        notifier: Target notification channel.
        clock: Optional Clock implementation.

    Returns:
        Tuple of (severity_level, reasons_list, health_metrics).
    """
    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    metrics = calculate_health_metrics(db, now_dt)
    level, reasons = evaluate_safety_rules(metrics)

    target_notifier = notifier or LogNotifier()

    if level == "CRITICAL":
        already_active = is_killswitch_active(db)
        reason_text = "; ".join(reasons)
        if not already_active:
            trigger_killswitch(
                db,
                reason=reason_text,
                metrics=metrics,
                clock=clk,
                notifier=target_notifier,
            )
        else:
            # If already active, check alert rate limit
            title = "EMERGENCY KILL-SWITCH ENGAGED"
            if not is_alert_rate_limited(db, title, clk):
                target_notifier.notify(
                    level="CRITICAL",
                    title=title,
                    message=f"Kill-switch active. Violations: {reason_text}",
                    payload=metrics.to_dict(),
                )
                record_alert_dispatched(
                    db,
                    level="CRITICAL",
                    title=title,
                    message=reason_text,
                    clock=clk,
                    payload=metrics.to_dict(),
                )

    elif level == "WARNING":
        title = "Outreach Health Warning"
        warning_text = "; ".join(reasons)
        if not is_alert_rate_limited(db, title, clk):
            target_notifier.notify(
                level="WARNING",
                title=title,
                message=f"Health metrics warning: {warning_text}",
                payload=metrics.to_dict(),
            )
            record_alert_dispatched(
                db,
                level="WARNING",
                title=title,
                message=warning_text,
                clock=clk,
                payload=metrics.to_dict(),
            )

    return level, reasons, metrics
