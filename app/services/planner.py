"""Send planner and scheduler service.

Implements safety-first outbound email planning:
1. Warm-up and Ramp-Up daily caps based on OUTREACH_START_DATE.
2. Hard daily capacity limit (HARD_MAX_PER_DAY = 30) that no setting may exceed.
3. Allowed send days (Tue, Wed, Thu) and window (09:00-11:30) in lead's local timezone.
4. Global mailbox throttling with MIN_GAP_SECONDS (120s) plus random jitter (0-180s).
5. Follow-up priority and exact sent-time interval tracking.
6. Automatic lead suppression and pause verification.
7. DST resilience and missing timezone fallback with operator warnings.
8. Complete idempotency across multiple planning invocations.
"""

import json
import random
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus
from app.models.sequence import SequenceStep
from app.models.setting import Setting
from app.services.suppression import is_suppressed

logger = get_logger(__name__)

HARD_MAX_LIMIT = 30
WEEKDAY_NAME_TO_INT: dict[str, int] = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    """Retrieve setting value from Setting table with fallback to app config."""
    db_setting = db.scalar(select(Setting).where(Setting.key == key))
    if db_setting is not None and db_setting.value is not None:
        return db_setting.value
    cfg = get_settings()
    return getattr(cfg, key, default)


def get_outreach_start_date(db: Session) -> date:
    """Retrieve and parse OUTREACH_START_DATE."""
    raw_val = get_setting(db, "OUTREACH_START_DATE", default="2026-11-01")
    if isinstance(raw_val, date):
        return raw_val
    if isinstance(raw_val, datetime):
        return raw_val.date()
    try:
        return date.fromisoformat(str(raw_val).strip())
    except Exception:
        return date(2026, 11, 1)


def get_mailbox_tz(db: Session) -> ZoneInfo:
    """Retrieve operator's mailbox timezone for counting the day."""
    tz_str = str(get_setting(db, "MAILBOX_TZ", default="America/New_York")).strip()
    try:
        return ZoneInfo(tz_str)
    except Exception:
        logger.warning("Invalid MAILBOX_TZ '%s', falling back to America/New_York", tz_str)
        return ZoneInfo("America/New_York")


def get_allowed_weekdays(db: Session) -> list[int]:
    """Retrieve allowed outreach weekdays as integer indices (0=Mon, 1=Tue, ...)."""
    raw_val = get_setting(db, "SEND_DAYS", default=["Tue", "Wed", "Thu"])
    days_list: list[str] = []
    if isinstance(raw_val, list):
        days_list = [str(d) for d in raw_val]
    elif isinstance(raw_val, str):
        raw_val = raw_val.strip()
        if raw_val.startswith("[") and raw_val.endswith("]"):
            try:
                days_list = [str(d) for d in json.loads(raw_val)]
            except Exception:
                days_list = [part.strip() for part in raw_val.strip("[]").split(",")]
        else:
            days_list = [part.strip() for part in raw_val.split(",")]

    int_days: list[int] = []
    for d in days_list:
        clean = d.strip().lower()
        if clean in WEEKDAY_NAME_TO_INT:
            int_days.append(WEEKDAY_NAME_TO_INT[clean])
        elif clean.isdigit() and 0 <= int(clean) <= 6:
            int_days.append(int(clean))

    return int_days if int_days else [1, 2, 3]


def get_send_window(db: Session) -> tuple[time, time]:
    """Retrieve daily send window times (start, end) in lead local time."""
    raw_start = str(get_setting(db, "SEND_WINDOW_START", default="09:00")).strip()
    raw_end = str(get_setting(db, "SEND_WINDOW_END", default="11:30")).strip()

    def _parse_time(t_str: str, fallback: time) -> time:
        try:
            parts = [int(p) for p in t_str.split(":")]
            return time(parts[0], parts[1])
        except Exception:
            return fallback

    return _parse_time(raw_start, time(9, 0)), _parse_time(raw_end, time(11, 30))


def get_ramp_up_cap(target_date: date, start_date: date) -> int:
    """Compute base ramp-up daily dispatch quota based on outreach start date.

    RAMP_UP schedule:
    - days 0-2 after start = 10/day
    - days 3-6 = 15/day
    - days 7-13 = 20/day
    - day 14+ = 25/day
    """
    if target_date < start_date:
        return 0
    day_diff = (target_date - start_date).days
    if day_diff <= 2:
        return 10
    elif day_diff <= 6:
        return 15
    elif day_diff <= 13:
        return 20
    else:
        return 25


def get_sequence_step_delay(db: Session, campaign_id: int, step: int) -> int:
    """Determine N days delay after the previous step was sent for follow-up message."""
    step_def = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == campaign_id,
            SequenceStep.step == step,
        )
    )
    if step_def:
        prev_step_def = db.scalar(
            select(SequenceStep).where(
                SequenceStep.campaign_id == campaign_id,
                SequenceStep.step == step - 1,
            )
        )
        if prev_step_def and step_def.delay_days > prev_step_def.delay_days:
            return step_def.delay_days - prev_step_def.delay_days
        elif step_def.delay_days > 0:
            return step_def.delay_days

    # Standard default sequence: step 2 -> 4 days, step 3 -> 4 days, step 4 -> 6 days
    default_delays = {2: 4, 3: 4, 4: 6}
    return default_delays.get(step, 4)


def due_emails(
    now_or_db: datetime | Session | None = None,
    db_or_now: Session | datetime | None = None,
    now: datetime | None = None,
    db: Session | None = None,
) -> list[EmailMessage]:
    """Return approved EmailMessages that are due for dispatch.

    Rules:
    - First emails (step 1) are due immediately.
    - Follow-ups (steps 2-4) are due N days after the previous step was SENT (never before).
    - Follow-ups have priority over new first emails.
    - Skip leads that are replied, bounced, unsubscribed, suppressed, or whose campaign is paused.
    - Re-check suppression list at planning time.
    """
    actual_now: datetime | None = now
    actual_db: Session | None = db

    if isinstance(now_or_db, datetime):
        actual_now = now_or_db
    elif isinstance(now_or_db, Session):
        actual_db = now_or_db

    if isinstance(db_or_now, datetime):
        actual_now = db_or_now
    elif isinstance(db_or_now, Session):
        actual_db = db_or_now

    if actual_now is None:
        actual_now = system_clock.now()
    if actual_now.tzinfo is None:
        actual_now = actual_now.replace(tzinfo=UTC)

    close_session_on_exit = False
    if actual_db is None:
        actual_db = SessionLocal()
        close_session_on_exit = True

    try:
        # Query approved emails
        stmt = (
            select(EmailMessage)
            .where(EmailMessage.status == EmailMessageStatus.APPROVED.value)
            .order_by(EmailMessage.step.desc(), EmailMessage.id.asc())
        )
        approved_emails = actual_db.scalars(stmt).all()

        eligible: list[EmailMessage] = []
        for msg in approved_emails:
            lead = msg.lead or actual_db.get(Lead, msg.lead_id)
            if not lead:
                continue

            # 1. Skip if lead is in a finalized / inactive state
            terminal_statuses = (
                LeadStatus.REPLIED.value,
                LeadStatus.BOUNCED.value,
                LeadStatus.UNSUBSCRIBED.value,
                LeadStatus.INVALID.value,
            )
            if lead.status in terminal_statuses:
                msg.status = EmailMessageStatus.SKIPPED.value
                msg.error = f"Lead in {lead.status} status"
                continue

            # 2. Re-check global suppression
            if is_suppressed(actual_db, lead.email):
                msg.status = EmailMessageStatus.SKIPPED.value
                msg.error = "Suppressed target"
                lead.status = LeadStatus.UNSUBSCRIBED.value
                continue

            # 3. Skip if parent campaign is paused or inactive
            campaign = lead.campaign or actual_db.get(Campaign, lead.campaign_id)
            if not campaign or campaign.status == CampaignStatus.PAUSED.value:
                continue
            if campaign.status != CampaignStatus.ACTIVE.value:
                continue

            # 4. Check step dispatch timing
            if msg.step == 1:
                eligible.append(msg)
            else:
                # Follow-up: must have previous step SENT with sent_at set
                prev_msg = actual_db.scalar(
                    select(EmailMessage).where(
                        EmailMessage.lead_id == lead.id,
                        EmailMessage.step == msg.step - 1,
                    )
                )
                if not prev_msg or prev_msg.status != EmailMessageStatus.SENT.value:
                    continue
                if prev_msg.sent_at is None:
                    continue

                prev_sent = prev_msg.sent_at
                if prev_sent.tzinfo is None:
                    prev_sent = prev_sent.replace(tzinfo=UTC)

                delay_days = get_sequence_step_delay(actual_db, campaign.id, msg.step)
                due_at = prev_sent + timedelta(days=delay_days)

                if actual_now < due_at:
                    # Not due yet
                    continue

                eligible.append(msg)

        # Sort with strict priority: follow-ups (step 2-4) before new leads (step 1)
        eligible.sort(key=lambda m: (0 if m.step > 1 else 1, m.step, m.id))
        return eligible
    finally:
        if close_session_on_exit:
            actual_db.close()


def daily_capacity(
    target_date: date | str,
    db: Session | None = None,
) -> int:
    """Calculate remaining sending capacity for target date in mailbox timezone.

    Remaining capacity = ramp-up cap for that date minus emails already sent or scheduled that day,
    never exceeding HARD_MAX_PER_DAY (30).
    """
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date.strip())

    close_session_on_exit = False
    if db is None:
        db = SessionLocal()
        close_session_on_exit = True

    try:
        start_date = get_outreach_start_date(db)
        if target_date < start_date:
            return 0

        base_cap = get_ramp_up_cap(target_date, start_date)

        # Check configurable hard max override, clamped unconditionally at 30
        configured_max = get_setting(db, "HARD_MAX_PER_DAY", default=HARD_MAX_LIMIT)
        try:
            configured_max_int = int(configured_max)
        except Exception:
            configured_max_int = HARD_MAX_LIMIT

        effective_cap = min(base_cap, configured_max_int, HARD_MAX_LIMIT)

        # Count emails already sent or scheduled on target_date in operator's mailbox timezone
        mailbox_tz = get_mailbox_tz(db)
        stmt = select(EmailMessage).where(
            EmailMessage.status.in_(
                (
                    EmailMessageStatus.SCHEDULED.value,
                    EmailMessageStatus.SENDING.value,
                    EmailMessageStatus.SENT.value,
                    EmailMessageStatus.SENT_DRY_RUN.value,
                )
            )
        )
        existing_msgs = db.scalars(stmt).all()

        used_count = 0
        for msg in existing_msgs:
            if msg.status in (
                EmailMessageStatus.SCHEDULED.value,
                EmailMessageStatus.SENDING.value,
            ):
                ref_dt = msg.scheduled_at
            else:
                ref_dt = msg.sent_at or msg.scheduled_at
            if ref_dt is None:
                ref_dt = msg.scheduled_at or msg.sent_at
            if ref_dt is None:
                continue

            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=UTC)

            local_date = ref_dt.astimezone(mailbox_tz).date()
            if local_date == target_date:
                used_count += 1

        remaining = max(0, effective_cap - used_count)
        return min(remaining, HARD_MAX_LIMIT)
    finally:
        if close_session_on_exit:
            db.close()


def plan_day(
    target_date: date | str,
    db: Session | None = None,
    clock: Clock | None = None,
    jitter_fn: Callable[[], int] | None = None,
) -> list[EmailMessage]:
    """Assign scheduled_at to due emails within lead windows and mailbox limits.

    Respects:
    - OUTREACH_START_DATE (nothing planned before it).
    - Daily capacity and ramp-up tiers (clamped to HARD_MAX_PER_DAY).
    - Lead send window (09:00-11:30) and allowed weekdays (Tue, Wed, Thu) in lead's local time.
    - Global mailbox throttling (MIN_GAP_SECONDS + random jitter).
    - Idempotency: multiple runs will not duplicate sends or breach limits.
    - Missing lead timezone fallback to mailbox timezone with warning.
    - Window expiration: pushed forward to next allowed day.
    """
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date.strip())

    if clock is None:
        clock = system_clock
    now_utc = clock.now()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    close_session_on_exit = False
    if db is None:
        db = SessionLocal()
        close_session_on_exit = True

    try:
        start_date = get_outreach_start_date(db)
        if target_date < start_date:
            logger.info(
                "Target date %s is before OUTREACH_START_DATE %s; planning 0 emails",
                target_date,
                start_date,
            )
            return []

        remaining_cap = daily_capacity(target_date, db)
        if remaining_cap <= 0:
            logger.info("No remaining capacity for date %s", target_date)
            return []

        candidates = due_emails(now=now_utc, db=db)
        if not candidates:
            logger.info("No approved and due emails to schedule for %s", target_date)
            return []

        # Pick up to remaining daily capacity (follow-ups prioritized)
        picked = candidates[:remaining_cap]

        # Planner configuration
        mailbox_tz = get_mailbox_tz(db)
        mailbox_tz_str = str(mailbox_tz)
        allowed_weekdays = get_allowed_weekdays(db)
        window_start_time, window_end_time = get_send_window(db)

        min_gap_sec = int(get_setting(db, "MIN_GAP_SECONDS", default=120))
        max_jitter_sec = int(get_setting(db, "MAX_JITTER_SECONDS", default=180))

        if jitter_fn is None:
            jitter_fn = lambda: random.randint(0, max_jitter_sec)  # noqa: E731

        # Retrieve latest scheduled dispatch across the mailbox to ensure global gap
        latest_stmt = (
            select(EmailMessage.scheduled_at)
            .where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
            .order_by(EmailMessage.scheduled_at.desc())
        )
        latest_scheduled_dt = db.scalar(latest_stmt)
        if latest_scheduled_dt is not None:
            if latest_scheduled_dt.tzinfo is None:
                latest_scheduled_dt = latest_scheduled_dt.replace(tzinfo=UTC)
            last_scheduled_utc: datetime | None = latest_scheduled_dt.astimezone(UTC)
        else:
            last_scheduled_utc = None

        # Resolve lead timezones and earliest local window start in UTC
        prepared: list[tuple[EmailMessage, ZoneInfo, datetime]] = []
        for email_msg in picked:
            lead = email_msg.lead or db.get(Lead, email_msg.lead_id)
            if not lead:
                continue

            lead_tz: ZoneInfo
            if not lead.timezone or not lead.timezone.strip():
                logger.warning(
                    "Lead %d (%s) has no timezone; falling back to mailbox timezone '%s'",
                    lead.id,
                    lead.email,
                    mailbox_tz_str,
                )
                lead_tz = mailbox_tz
            else:
                try:
                    lead_tz = ZoneInfo(lead.timezone.strip())
                except Exception:
                    logger.warning(
                        "Lead %d (%s) has invalid timezone '%s'; using fallback '%s'",
                        lead.id,
                        lead.email,
                        lead.timezone,
                        mailbox_tz_str,
                    )
                    lead_tz = mailbox_tz

            earliest_local_dt = datetime.combine(target_date, window_start_time, tzinfo=lead_tz)
            earliest_utc_dt = earliest_local_dt.astimezone(UTC)
            prepared.append((email_msg, lead_tz, earliest_utc_dt))

        # Sort within picked batch: chronological window start, then follow-ups before new
        prepared.sort(
            key=lambda item: (
                item[2],
                0 if item[0].step > 1 else 1,
                item[0].step,
                item[0].id,
            )
        )

        scheduled_messages: list[EmailMessage] = []
        for email_msg, lead_tz, _ in prepared:
            jitter = jitter_fn()
            if last_scheduled_utc is None:
                earliest_possible_utc = now_utc
            else:
                earliest_possible_utc = max(
                    now_utc,
                    last_scheduled_utc + timedelta(seconds=min_gap_sec + jitter),
                )

            # Search forward from target_date for the next valid day & window in lead's tz
            day_cursor = target_date
            chosen_slot_utc: datetime | None = None

            while chosen_slot_utc is None:
                # 1. Day must be an allowed weekday
                if day_cursor.weekday() not in allowed_weekdays:
                    day_cursor += timedelta(days=1)
                    continue

                # 2. Compute window for day_cursor in lead_tz
                win_start_local = datetime.combine(day_cursor, window_start_time, tzinfo=lead_tz)
                win_end_local = datetime.combine(day_cursor, window_end_time, tzinfo=lead_tz)
                win_start_utc = win_start_local.astimezone(UTC)
                win_end_utc = win_end_local.astimezone(UTC)

                # 3. Check if window has already expired on this day
                if earliest_possible_utc > win_end_utc:
                    day_cursor += timedelta(days=1)
                    continue

                # 4. Valid slot found inside lead's window
                slot_candidate_utc = max(win_start_utc, earliest_possible_utc)
                if slot_candidate_utc <= win_end_utc:
                    chosen_slot_utc = slot_candidate_utc
                else:
                    day_cursor += timedelta(days=1)

            # Assign and transition status
            email_msg.scheduled_at = chosen_slot_utc
            email_msg.status = EmailMessageStatus.SCHEDULED.value
            last_scheduled_utc = chosen_slot_utc

            lead = email_msg.lead or db.get(Lead, email_msg.lead_id)
            if lead and lead.status == LeadStatus.APPROVED.value:
                lead.status = LeadStatus.IN_SEQUENCE.value

            scheduled_messages.append(email_msg)

        db.commit()
        return scheduled_messages
    finally:
        if close_session_on_exit:
            db.close()
