"""Just-In-Time (JIT) Lead Batch Processing & Dynamic Capacity Dispatch Service.

Architecture:
1. Pool-Based Storage: Allows importing tens of thousands of leads safely into the database
   without overwhelming the system or calling the LLM upfront.
2. Dynamic Quota Calculation:
   - Sums active sender mailbox capacities (e.g. 10 accounts x 30 = 300/day).
   - Priority 1: Follow-up emails (Steps 2, 3, 4) take absolute precedence.
   - Priority 2: Leftover personalized/approved leads from previous days are utilized first.
   - Calculates exact daily deficit needed for new initial outreach.
3. On-Demand Personalization:
   - Personalizes ONLY the exact deficit needed for today from the un-personalized pool.
   - Prevents stale observations, avoids LLM rate limits, and zero browser timeouts.
4. Auto-Roll & Resilience:
   - Any unsent personalized leads automatically roll over to the next day's send window.
   - Next day's quota dynamically reduces to accommodate the leftover ready leads.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus
from app.models.setting import Setting
from app.services.fetcher import RealFetcher, WebFetcher
from app.services.llm import LlmClient, get_llm_client
from app.services.personalizer import personalize_lead
from app.services.planner import (
    daily_capacity,
    due_emails,
    get_allowed_weekdays,
    get_effective_daily_cap,
    get_mailbox_tz,
    get_send_window,
    get_setting,
    plan_day,
)
from app.services.sequencer import enroll

logger = get_logger(__name__)


@dataclass
class JITStatus:
    """Current state of the JIT lead pool and daily sending quota."""

    target_date: str
    total_daily_cap: int
    used_or_scheduled_today: int
    remaining_daily_capacity: int
    due_followups_count: int
    available_new_slots: int
    ready_leads_in_buffer: int
    leads_needed_today: int
    unprocessed_pool_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JITResult:
    """Outcome of a JIT batch processing cycle."""

    status: JITStatus
    personalized_count: int
    enrolled_count: int
    scheduled_count: int
    skipped_count: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_jit_status(
    db: Session,
    target_date: date | None = None,
    clock: Clock | None = None,
) -> JITStatus:
    """Calculate the precise capacity, follow-up requirements, and new lead deficit for today.

    Args:
        db: SQLAlchemy session.
        target_date: Optional specific date (defaults to today in mailbox timezone).
        clock: Optional Clock implementation.

    Returns:
        JITStatus with complete quota metrics.
    """
    clk = clock or system_clock
    now_utc = clk.now()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    tgt_date = target_date or now_utc.astimezone(mailbox_tz).date()

    # 1. Total daily capacity across all active sender mailboxes (e.g. 10 * 30 = 300)
    total_daily_cap = get_effective_daily_cap(db, tgt_date)

    # 2. Remaining capacity after taking into account emails already sent/scheduled for today
    rem_cap = daily_capacity(tgt_date, db)
    used_or_scheduled_today = max(0, total_daily_cap - rem_cap)

    # 3. Follow-up priority: Count due follow-ups (Step > 1)
    all_due = due_emails(now=now_utc, db=db)
    due_followups = [m for m in all_due if m.step > 1]
    due_followups_count = len(due_followups)

    # 4. Slots available for new Step 1 outreach today
    available_new_slots = max(0, rem_cap - min(due_followups_count, rem_cap))

    # 5. Ready leads already in the buffer
    # (Step 1 already due/approved OR leads approved/personalized)
    ready_step1_emails = [m for m in all_due if m.step == 1]
    ready_unrolled_leads = (
        db.scalar(
            select(func.count(Lead.id))
            .join(Campaign)
            .where(
                Campaign.status == CampaignStatus.ACTIVE.value,
                Lead.status.in_([LeadStatus.APPROVED.value, LeadStatus.PERSONALIZED.value]),
            )
        )
        or 0
    )
    ready_leads_in_buffer = len(ready_step1_emails) + ready_unrolled_leads

    # 6. Exact deficit: How many fresh leads need Gemini personalization today?
    leads_needed_today = max(0, available_new_slots - ready_leads_in_buffer)

    # 7. Unprocessed pool leads waiting in DB
    unprocessed_pool_count = (
        db.scalar(
            select(func.count(Lead.id))
            .join(Campaign)
            .where(
                Campaign.status == CampaignStatus.ACTIVE.value,
                Lead.status.in_([LeadStatus.VALID.value, LeadStatus.NEW.value]),
            )
        )
        or 0
    )

    return JITStatus(
        target_date=tgt_date.isoformat(),
        total_daily_cap=total_daily_cap,
        used_or_scheduled_today=used_or_scheduled_today,
        remaining_daily_capacity=rem_cap,
        due_followups_count=due_followups_count,
        available_new_slots=available_new_slots,
        ready_leads_in_buffer=ready_leads_in_buffer,
        leads_needed_today=leads_needed_today,
        unprocessed_pool_count=unprocessed_pool_count,
    )


def run_jit_batch(
    db: Session,
    target_date: date | None = None,
    clock: Clock | None = None,
    fetcher: WebFetcher | None = None,
    llm: LlmClient | None = None,
    max_batch: int | None = None,
    auto_enroll: bool = True,
    auto_plan: bool = True,
) -> JITResult:
    """Execute a Just-In-Time lead batch personalization and scheduling cycle.

    1. Determines exact lead deficit needed for today's quota.
    2. Pulls up to `deficit` leads from the unprocessed pool.
    3. Runs personalization (Gemini / Smart Engine).
    4. Enrolls approved leads into the sequence and schedules dispatches.

    Args:
        db: SQLAlchemy session.
        target_date: Target dispatch date (defaults to today).
        clock: Optional Clock implementation.
        fetcher: WebFetcher implementation.
        llm: LlmClient implementation.
        max_batch: Optional cap on how many to personalize in this single run.
        auto_enroll: Automatically enroll approved leads into sequence.
        auto_plan: Automatically run plan_day to schedule emails.

    Returns:
        JITResult containing processing counts and refreshed status.
    """
    settings = get_settings()
    clk = clock or system_clock
    now_utc = clk.now()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    tgt_date = target_date or now_utc.astimezone(mailbox_tz).date()

    # Step 1: Check JIT requirements
    initial_status = get_jit_status(db, target_date=tgt_date, clock=clk)

    leads_to_process = initial_status.leads_needed_today
    if max_batch is not None and max_batch > 0:
        leads_to_process = min(leads_to_process, max_batch)

    personalized_count = 0
    skipped_count = 0
    enrolled_count = 0
    scheduled_count = 0

    # Step 2: On-demand personalization for deficit leads
    if leads_to_process > 0 and initial_status.unprocessed_pool_count > 0:
        active_fetcher = fetcher or RealFetcher(settings=settings)
        active_llm = llm or get_llm_client(settings=settings)

        # Pull leads in FIFO order from active campaigns
        candidate_leads = db.scalars(
            select(Lead)
            .join(Campaign)
            .where(
                Campaign.status == CampaignStatus.ACTIVE.value,
                Lead.status.in_([LeadStatus.VALID.value, LeadStatus.NEW.value]),
            )
            .order_by(Lead.id.asc())
            .limit(leads_to_process)
        ).all()

        logger.info(
            "JIT Personalizer: Processing %d lead(s) for date %s (deficit was %d)",
            len(candidate_leads),
            tgt_date,
            leads_to_process,
        )

        for lead in candidate_leads:
            success, msg = personalize_lead(
                lead=lead,
                db=db,
                fetcher=active_fetcher,
                llm=active_llm,
                clock=clk,
            )
            if success:
                personalized_count += 1
            else:
                skipped_count += 1

    # Step 3: Auto-Enroll approved leads across active campaigns
    if auto_enroll:
        active_campaigns = db.scalars(
            select(Campaign).where(Campaign.status == CampaignStatus.ACTIVE.value)
        ).all()
        for camp in active_campaigns:
            enrolled = enroll(camp.id, db=db, clock=clk)
            enrolled_count += len(enrolled)

    # Step 4: Auto-Plan: Schedule emails into the daily window across mailboxes
    if auto_plan and initial_status.remaining_daily_capacity > 0:
        scheduled_msgs = plan_day(target_date=tgt_date, db=db, clock=clk)
        scheduled_count = len(scheduled_msgs)

    # Step 5: Refresh status
    final_status = get_jit_status(db, target_date=tgt_date, clock=clk)

    message = (
        f"JIT Cycle completed for {tgt_date}: "
        f"{personalized_count} leads personalized, "
        f"{enrolled_count} enrolled, "
        f"{scheduled_count} emails scheduled into send window. "
        f"Remaining pool: {final_status.unprocessed_pool_count} leads."
    )
    logger.info(message)

    return JITResult(
        status=final_status,
        personalized_count=personalized_count,
        enrolled_count=enrolled_count,
        scheduled_count=scheduled_count,
        skipped_count=skipped_count,
        message=message,
    )


def reconcile_overdue_scheduled_emails(
    db: Session,
    target_date: date,
    mailbox_tz: ZoneInfo,
    clock: Clock | None = None,
) -> int:
    """Find any scheduled emails from previous days that were not sent,

    and reset them to APPROVED so they are cleanly re-slotted into today's window.
    """
    stmt = select(EmailMessage).where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
    scheduled_msgs = db.scalars(stmt).all()

    reset_count = 0
    for msg in scheduled_msgs:
        if msg.scheduled_at is None:
            continue
        msg_dt = msg.scheduled_at
        if msg_dt.tzinfo is None:
            msg_dt = msg_dt.replace(tzinfo=UTC)

        msg_local_date = msg_dt.astimezone(mailbox_tz).date()
        # If the email was scheduled for a date in the past, reset to APPROVED
        if msg_local_date < target_date:
            logger.info(
                "Carrying over leftover email #%d (lead #%d, step %d) from %s to today's schedule",
                msg.id,
                msg.lead_id,
                msg.step,
                msg_local_date,
            )
            msg.status = EmailMessageStatus.APPROVED.value
            msg.scheduled_at = None
            reset_count += 1

    if reset_count > 0:
        db.commit()
    return reset_count


def reconcile_and_refresh_daily_schedule(
    db: Session,
    target_date: date | None = None,
    clock: Clock | None = None,
    fetcher: WebFetcher | None = None,
    llm: LlmClient | None = None,
) -> JITResult:
    """Complete pre-window reconciliation routine.

    1. Reconciles past leftover scheduled messages to APPROVED.
    2. Runs JIT deficit calculation and personalization.
    3. Auto-enrolls approved leads.
    4. Auto-plans and schedules all due dispatches into today's window.
    """
    clk = clock or system_clock
    now_utc = clk.now()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    tgt_date = target_date or now_utc.astimezone(mailbox_tz).date()

    # Step 1: Reconcile overdue / leftover emails from previous days
    reconciled_count = reconcile_overdue_scheduled_emails(
        db=db,
        target_date=tgt_date,
        mailbox_tz=mailbox_tz,
        clock=clk,
    )

    # Step 2: Run JIT batch processing for today
    result = run_jit_batch(
        db=db,
        target_date=tgt_date,
        clock=clk,
        fetcher=fetcher,
        llm=llm,
        auto_enroll=True,
        auto_plan=True,
    )

    # Step 3: Record today's sync completion in settings
    db.merge(Setting(key="LAST_DAILY_JIT_DATE", value=tgt_date.isoformat()))
    db.commit()

    if reconciled_count > 0:
        result.message = (
            f"Reconciled {reconciled_count} leftover email(s) from yesterday. " + result.message
        )

    return result


def is_daily_refresh_due(
    db: Session,
    clock: Clock | None = None,
    pre_window_minutes: int | None = None,
    force: bool = False,
) -> bool:
    """Determine whether the daily schedule reconciliation and JIT batch is due to run.

    Timing rules:
    1. If `force` is True, returns True immediately.
    2. Only runs on configured outreach days (e.g. Tue, Wed, Thu or Mon-Fri).
    3. Triggers `pre_window_minutes` (configurable via JIT_PRE_WINDOW_MINUTES, default: 10 mins)
       before SEND_WINDOW_START (e.g. at 08:50 AM for a 09:00 AM dispatch window), or anytime
       within the active sending window.
    4. Automatically runs once per calendar day.
    """
    if force:
        return True

    effective_pre_window = (
        pre_window_minutes
        if pre_window_minutes is not None
        else int(get_setting(db, "JIT_PRE_WINDOW_MINUTES", default=10))
    )

    clk = clock or system_clock
    now_utc = clk.now()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    local_now = now_utc.astimezone(mailbox_tz)
    today_date = local_now.date()

    # Rule 1: Allowed outreach weekdays check
    allowed_weekdays = get_allowed_weekdays(db)
    if today_date.weekday() not in allowed_weekdays:
        return False

    # Rule 2: Pre-window timing check (e.g. 10 minutes before 09:00 -> 08:50 AM)
    win_start, win_end = get_send_window(db)
    pre_window_dt = datetime.combine(today_date, win_start, tzinfo=mailbox_tz) - timedelta(
        minutes=effective_pre_window
    )

    # If it is before the pre-window buffer, wait until the buffer time
    if local_now < pre_window_dt:
        return False

    # Rule 3: Once per day check
    last_refresh_date = get_setting(db, "LAST_DAILY_JIT_DATE", default=None)
    if last_refresh_date == today_date.isoformat():
        return False

    return True
