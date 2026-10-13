"""Outreach planning and capacity preview API endpoints."""

from datetime import UTC, date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_clock, get_db
from app.api.schemas import CapacityResponse, PlannedEmailItem, PlanningPreviewResponse
from app.core.clock import Clock
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead
from app.services.planner import daily_capacity, due_emails, get_mailbox_tz
from app.services.safety import is_cap_halved_from_resume

router = APIRouter(prefix="/planning", tags=["Planning"])


@router.get("/preview", response_model=PlanningPreviewResponse)
def preview_plan(
    target_date: Annotated[
        str | None, Query(description="Date in YYYY-MM-DD format (defaults to tomorrow)")
    ] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> PlanningPreviewResponse:
    """Preview outbound emails scheduled or due to be sent on the specified target date."""
    clk_now = clock.now()
    if clk_now.tzinfo is None:
        clk_now = clk_now.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    local_now = clk_now.astimezone(mailbox_tz)

    if target_date:
        parsed_date = date.fromisoformat(target_date.strip())
    else:
        parsed_date = local_now.date() + timedelta(days=1)

    rem_cap = daily_capacity(parsed_date, db=db)

    # 1. Fetch currently scheduled emails (already slotted in delivery windows)
    scheduled_msgs = db.scalars(
        select(EmailMessage)
        .where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
        .order_by(EmailMessage.scheduled_at.asc(), EmailMessage.id.asc())
    ).all()

    planned_items: list[PlannedEmailItem] = []
    seen_ids: set[int] = set()

    for msg in scheduled_msgs:
        lead = msg.lead or db.get(Lead, msg.lead_id)
        if lead:
            seen_ids.add(msg.id)
            planned_items.append(
                PlannedEmailItem(
                    email_id=msg.id,
                    lead_id=lead.id,
                    email=lead.email,
                    company=lead.company,
                    step=msg.step,
                    subject=msg.subject,
                    scheduled_at=msg.scheduled_at,
                )
            )

    # 2. Add candidates that are approved and due
    candidates = due_emails(now=clk_now, db=db)
    for msg in candidates[:rem_cap]:
        if msg.id not in seen_ids:
            lead = msg.lead or db.get(Lead, msg.lead_id)
            if lead:
                planned_items.append(
                    PlannedEmailItem(
                        email_id=msg.id,
                        lead_id=lead.id,
                        email=lead.email,
                        company=lead.company,
                        step=msg.step,
                        subject=msg.subject,
                        scheduled_at=msg.scheduled_at,
                    )
                )

    return PlanningPreviewResponse(
        target_date=parsed_date.isoformat(),
        remaining_capacity=rem_cap,
        planned_emails=planned_items,
    )


@router.get("/capacity", response_model=CapacityResponse)
def get_capacity(
    check_date: Annotated[str | None, Query(description="Date in YYYY-MM-DD format")] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> CapacityResponse:
    """Get total and remaining outbound dispatch capacity for a given date."""
    clk_now = clock.now()
    if clk_now.tzinfo is None:
        clk_now = clk_now.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    local_now = clk_now.astimezone(mailbox_tz)

    if check_date:
        parsed_date = date.fromisoformat(check_date.strip())
    else:
        parsed_date = local_now.date()

    rem_cap = daily_capacity(parsed_date, db=db)

    # Compute total cap for the day
    from app.services.planner import get_outreach_start_date, get_ramp_up_cap, get_setting

    start_date = get_outreach_start_date(db)
    base_cap = get_ramp_up_cap(parsed_date, start_date)
    configured_max = get_setting(db, "HARD_MAX_PER_DAY", default=30)
    try:
        configured_max_int = int(configured_max)
    except Exception:
        configured_max_int = 30
    total_cap = min(base_cap, configured_max_int, 30)
    halved = is_cap_halved_from_resume(db, target_date=parsed_date, clock=clock)
    if halved:
        total_cap = max(1, total_cap // 2)

    sent_or_planned = max(0, total_cap - rem_cap)

    return CapacityResponse(
        date=parsed_date.isoformat(),
        daily_capacity=total_cap,
        sent_today=sent_or_planned,
        remaining_capacity=rem_cap,
        is_cap_halved=halved,
    )


@router.post("/plan", response_model=PlanningPreviewResponse)
def execute_plan(
    target_date: Annotated[str | None, Query(description="Date in YYYY-MM-DD format")] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> PlanningPreviewResponse:
    """Execute the outbound dispatch scheduler for target date."""
    clk_now = clock.now()
    if clk_now.tzinfo is None:
        clk_now = clk_now.replace(tzinfo=UTC)

    mailbox_tz = get_mailbox_tz(db)
    local_now = clk_now.astimezone(mailbox_tz)

    if target_date:
        parsed_date = date.fromisoformat(target_date.strip())
    else:
        parsed_date = local_now.date()

    from app.services.planner import plan_day

    scheduled_msgs = plan_day(target_date=parsed_date, db=db, clock=clock)
    rem_cap = daily_capacity(parsed_date, db=db)

    planned_items: list[PlannedEmailItem] = []
    for msg in scheduled_msgs:
        lead = msg.lead or db.get(Lead, msg.lead_id)
        if lead:
            item = PlannedEmailItem(
                email_id=msg.id,
                lead_id=lead.id,
                email=lead.email,
                company=lead.company,
                step=msg.step,
                subject=msg.subject,
                scheduled_at=msg.scheduled_at,
            )
            planned_items.append(item)

    return PlanningPreviewResponse(
        target_date=parsed_date.isoformat(),
        remaining_capacity=rem_cap,
        planned_emails=planned_items,
    )

