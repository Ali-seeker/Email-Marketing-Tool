"""Analytics, timeseries, funnel metrics, and deliverability stats API endpoints."""

from datetime import UTC, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_clock, get_db
from app.api.schemas import (
    DailyStatsItem,
    FunnelStatsResponse,
    VariantStatsItem,
)
from app.core.clock import Clock
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.reply import Reply
from app.services.planner import daily_capacity
from app.services.safety import calculate_health_metrics, is_killswitch_active

router = APIRouter(prefix="/stats", tags=["Stats"])


@router.get("/timeseries", response_model=list[DailyStatsItem])
def get_daily_timeseries(
    days: int = 30,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> list[DailyStatsItem]:
    """Retrieve daily sent, reply, and bounce counts over the last N days."""
    clk_now = clock.now()
    if clk_now.tzinfo is None:
        clk_now = clk_now.replace(tzinfo=UTC)

    start_date = (clk_now - timedelta(days=days)).date()

    # Pre-populate map
    date_map: dict[str, DailyStatsItem] = {}
    for i in range(days + 1):
        d_str = (start_date + timedelta(days=i)).isoformat()
        date_map[d_str] = DailyStatsItem(date=d_str, sent=0, replies=0, bounces=0)

    # 1. Sent emails
    sent_stmt = (
        select(func.date(EmailMessage.sent_at).label("d"), func.count(EmailMessage.id))
        .where(
            EmailMessage.status.in_(
                [EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value]
            ),
            EmailMessage.sent_at.is_not(None),
        )
        .group_by("d")
    )
    for row in db.execute(sent_stmt).all():
        if row[0] and str(row[0]) in date_map:
            date_map[str(row[0])].sent = row[1]

    # 2. Inbound replies
    reply_stmt = select(func.date(Reply.created_at).label("d"), func.count(Reply.id)).group_by("d")
    for row in db.execute(reply_stmt).all():
        if row[0] and str(row[0]) in date_map:
            date_map[str(row[0])].replies = row[1]

    # 3. Hard & soft bounces
    bounce_stmt = (
        select(func.date(Event.created_at).label("d"), func.count(Event.id))
        .where(Event.type.in_([EventType.BOUNCED.value, EventType.SOFT_BOUNCE.value]))
        .group_by("d")
    )
    for row in db.execute(bounce_stmt).all():
        if row[0] and str(row[0]) in date_map:
            date_map[str(row[0])].bounces = row[1]

    return sorted(date_map.values(), key=lambda x: x.date)


@router.get("/funnel", response_model=FunnelStatsResponse)
def get_funnel_counts(
    db: Session = Depends(get_db),
) -> FunnelStatsResponse:
    """Retrieve funnel stage counts (imported -> valid -> approved -> sent -> replied -> won)."""
    total_imported = db.scalar(select(func.count(Lead.id))) or 0

    valid_leads = (
        db.scalar(
            select(func.count(Lead.id)).where(
                Lead.status.in_(
                    [
                        LeadStatus.VALID.value,
                        LeadStatus.PERSONALIZED.value,
                        LeadStatus.APPROVED.value,
                        LeadStatus.IN_SEQUENCE.value,
                        LeadStatus.REPLIED.value,
                        LeadStatus.WON.value,
                        LeadStatus.COMPLETED.value,
                    ]
                )
            )
        )
        or 0
    )

    approved_leads = (
        db.scalar(
            select(func.count(Lead.id)).where(
                Lead.status.in_(
                    [
                        LeadStatus.APPROVED.value,
                        LeadStatus.IN_SEQUENCE.value,
                        LeadStatus.REPLIED.value,
                        LeadStatus.WON.value,
                        LeadStatus.COMPLETED.value,
                    ]
                )
            )
        )
        or 0
    )

    # Leads that received at least one email
    sent_leads = (
        db.scalar(
            select(func.count(func.distinct(EmailMessage.lead_id))).where(
                EmailMessage.status.in_(
                    [EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value]
                )
            )
        )
        or 0
    )

    replied_leads = (
        db.scalar(select(func.count(Lead.id)).where(Lead.status == LeadStatus.REPLIED.value)) or 0
    )
    won_leads = (
        db.scalar(select(func.count(Lead.id)).where(Lead.status == LeadStatus.WON.value)) or 0
    )

    return FunnelStatsResponse(
        imported=total_imported,
        valid=valid_leads,
        approved=approved_leads,
        sent=sent_leads,
        replied=replied_leads,
        won=won_leads,
    )


@router.get("/templates", response_model=list[VariantStatsItem])
def get_template_variant_stats(
    db: Session = Depends(get_db),
) -> list[VariantStatsItem]:
    """Calculate send volumes and reply conversion rates per sequence variant."""
    stmt = (
        select(
            EmailMessage.step,
            EmailMessage.subject_variant_index,
            EmailMessage.body_variant_index,
            func.count(EmailMessage.id).label("sent_count"),
        )
        .where(
            EmailMessage.status.in_(
                [EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value]
            )
        )
        .group_by(
            EmailMessage.step,
            EmailMessage.subject_variant_index,
            EmailMessage.body_variant_index,
        )
    )
    results = db.execute(stmt).all()

    items: list[VariantStatsItem] = []
    for r in results:
        step_val = r[0]
        sub_idx = r[1]
        body_idx = r[2]
        sent_c = r[3]

        # Count replies linked to emails with this step & variants
        reply_count_stmt = (
            select(func.count(Reply.id))
            .join(EmailMessage, Reply.email_message_id == EmailMessage.id)
            .where(
                EmailMessage.step == step_val,
                EmailMessage.subject_variant_index == sub_idx,
                EmailMessage.body_variant_index == body_idx,
            )
        )
        replies_c = db.scalar(reply_count_stmt) or 0
        rate = round((replies_c / sent_c) * 100.0, 2) if sent_c > 0 else 0.0

        items.append(
            VariantStatsItem(
                step=step_val,
                subject_variant_index=sub_idx,
                body_variant_index=body_idx,
                sent_count=sent_c,
                replied_count=replies_c,
                reply_rate_percent=rate,
            )
        )

    return items


@router.get("/bounce-rate")
def get_bounce_rate_stats(
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Retrieve current short-term and 7-day bounce rates."""
    clk_now = clock.now()
    metrics = calculate_health_metrics(db, clk_now)
    return {
        "bounce_rate_100_sends": metrics.bounce_rate_100,
        "bounce_rate_7_days": metrics.bounce_rate_7d,
        "total_sent_100": metrics.total_sent_100,
        "total_sent_7d": metrics.total_sent_7d,
        "sample_size_ok": metrics.sample_size_ok,
    }


@router.get("/capacity")
def get_today_capacity_stats(
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Retrieve today's dispatch cap usage."""
    today = clock.now().date()
    rem = daily_capacity(today, db=db, clock=clock)
    return {
        "date": today.isoformat(),
        "remaining_capacity": rem,
    }


@router.get("/health")
def get_overall_health_stats(
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Calculate overall deliverability health score (0-100)."""
    clk_now = clock.now()
    metrics = calculate_health_metrics(db, clk_now)
    killswitch = is_killswitch_active(db)

    # Health score algorithm: start at 100, subtract penalties
    score = 100
    if metrics.bounce_rate_100 > 1.0:
        score -= int(metrics.bounce_rate_100 * 15)
    if metrics.failure_rate > 2.0:
        score -= int(metrics.failure_rate * 5)
    if metrics.complaint_signals_7d > 0:
        score -= metrics.complaint_signals_7d * 20
    if metrics.auth_or_block_errors > 0:
        score -= 40
    if killswitch:
        score = min(score, 20)

    score = max(0, min(100, score))
    status_label = "healthy" if score >= 80 else ("warning" if score >= 50 else "critical")

    return {
        "health_score": score,
        "status": status_label,
        "killswitch_active": killswitch,
        "bounce_rate": metrics.bounce_rate,
        "failure_rate": metrics.failure_rate,
        "complaints": metrics.complaint_signals_7d,
        "auth_errors": metrics.auth_or_block_errors,
    }
