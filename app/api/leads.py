"""Leads management API endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_clock, get_db
from app.api.schemas import (
    LeadBulkValidateResponse,
    LeadDetailResponse,
    LeadEmailSummary,
    LeadReplySummary,
    LeadResponse,
    LeadTimelineEvent,
    PaginatedResponse,
    PersonalizeResponse,
)
from app.core.clock import Clock
from app.models.campaign import Campaign
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.reply import Reply
from app.services.importer import import_leads
from app.services.suppression import add as add_suppression
from app.services.validator import validate_leads

router = APIRouter(prefix="/leads", tags=["Leads"])


@router.post("/upload")
async def upload_leads_csv(
    file: Annotated[UploadFile, File(description="CSV lead file")],
    campaign_name: Annotated[str | None, Form()] = None,
    campaign_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Upload multipart CSV lead file and run import service."""
    if not campaign_name and not campaign_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must specify either campaign_name or campaign_id.",
        )

    target_campaign_name = campaign_name
    if not target_campaign_name and campaign_id:
        camp = db.get(Campaign, campaign_id)
        if not camp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign {campaign_id} not found.",
            )
        target_campaign_name = camp.name

    content = await file.read()
    try:
        csv_text = content.decode("utf-8")
    except UnicodeDecodeError:
        csv_text = content.decode("latin-1")

    report = import_leads(
        db=db,
        campaign_name=target_campaign_name,
        csv_file=csv_text,
        clock=clock,
    )
    return report.to_dict()


@router.get("", response_model=PaginatedResponse[LeadResponse])
def list_leads(
    pagination: Annotated[Pagination, Depends()],
    campaign_id: Annotated[int | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    risk_level: Annotated[str | None, Query()] = None,
    country: Annotated[str | None, Query()] = None,
    search: Annotated[
        str | None, Query(description="Search text across email, name, company")
    ] = None,
    sort_by: Annotated[str, Query()] = "id",
    sort_order: Annotated[str, Query()] = "desc",
    db: Session = Depends(get_db),
) -> PaginatedResponse[LeadResponse]:
    """List leads with multi-field filtering and pagination."""
    query = select(Lead)

    if campaign_id is not None:
        query = query.where(Lead.campaign_id == campaign_id)
    if status:
        query = query.where(Lead.status == status.lower())
    if risk_level:
        query = query.where(Lead.risk_level == risk_level.lower())
    if country:
        query = query.where(Lead.country.ilike(f"%{country}%"))
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Lead.email.ilike(search_pattern),
                Lead.first_name.ilike(search_pattern),
                Lead.last_name.ilike(search_pattern),
                Lead.company.ilike(search_pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    order_col = Lead.id.desc() if sort_order == "desc" else Lead.id.asc()
    if sort_by == "email":
        order_col = Lead.email.desc() if sort_order == "desc" else Lead.email.asc()
    elif sort_by == "created_at":
        order_col = Lead.created_at.desc() if sort_order == "desc" else Lead.created_at.asc()

    leads = db.scalars(
        query.order_by(order_col).offset(pagination.offset).limit(pagination.page_size)
    ).all()

    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 0
    items = [LeadResponse.model_validate(ld) for ld in leads]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.get("/{lead_id}", response_model=LeadDetailResponse)
def get_lead_detail(
    lead_id: int,
    db: Session = Depends(get_db),
) -> LeadDetailResponse:
    """Retrieve full lead profile with timeline events, emails, and replies."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found.",
        )

    events = db.scalars(
        select(Event).where(Event.lead_id == lead.id).order_by(Event.created_at.desc())
    ).all()
    emails = db.scalars(
        select(EmailMessage)
        .where(EmailMessage.lead_id == lead.id)
        .order_by(EmailMessage.step.asc())
    ).all()
    replies = db.scalars(
        select(Reply).where(Reply.lead_id == lead.id).order_by(Reply.created_at.desc())
    ).all()

    campaign_name = lead.campaign.name if lead.campaign else None

    return LeadDetailResponse(
        id=lead.id,
        campaign_id=lead.campaign_id,
        campaign_name=campaign_name,
        email=lead.email,
        first_name=lead.first_name,
        last_name=lead.last_name,
        company=lead.company,
        website=lead.website,
        title=lead.title,
        country=lead.country,
        timezone=lead.timezone,
        status=lead.status,
        risk_level=lead.risk_level,
        validation_reason=lead.validation_reason,
        personal_note=lead.personal_note,
        evidence_quote=lead.evidence_quote,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        events=[LeadTimelineEvent.model_validate(e) for e in events],
        emails=[LeadEmailSummary.model_validate(em) for em in emails],
        replies=[LeadReplySummary.model_validate(r) for r in replies],
    )


@router.post("/{lead_id}/pause", response_model=LeadResponse)
def pause_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Pause outreach for a specific lead and cancel draft/scheduled messages."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")

    lead.status = LeadStatus.PAUSED.value
    lead.updated_at = clock.now()

    # Cancel pending emails
    pending_emails = db.scalars(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status.in_(
                [
                    EmailMessageStatus.DRAFT.value,
                    EmailMessageStatus.APPROVED.value,
                    EmailMessageStatus.SCHEDULED.value,
                ]
            ),
        )
    ).all()
    for em in pending_emails:
        em.status = EmailMessageStatus.SKIPPED.value

    db.add(
        Event(
            lead_id=lead.id,
            type="manual_action",
            payload={"action": "pause", "cancelled_emails": len(pending_emails)},
            created_at=clock.now(),
        )
    )
    db.commit()
    db.refresh(lead)
    return LeadResponse.model_validate(lead)


@router.post("/{lead_id}/resume", response_model=LeadResponse)
def resume_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Resume a paused lead."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")

    if lead.status == LeadStatus.PAUSED.value:
        lead.status = LeadStatus.APPROVED.value
        lead.updated_at = clock.now()
        db.add(
            Event(
                lead_id=lead.id,
                type="manual_action",
                payload={"action": "resume"},
                created_at=clock.now(),
            )
        )
        db.commit()
        db.refresh(lead)

    return LeadResponse.model_validate(lead)


@router.post("/{lead_id}/mark-replied", response_model=LeadResponse)
def mark_lead_replied(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Mark lead as replied and cancel any remaining sequence emails."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")

    lead.status = LeadStatus.REPLIED.value
    lead.updated_at = clock.now()

    pending_emails = db.scalars(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status.in_(
                [
                    EmailMessageStatus.DRAFT.value,
                    EmailMessageStatus.APPROVED.value,
                    EmailMessageStatus.SCHEDULED.value,
                ]
            ),
        )
    ).all()
    for em in pending_emails:
        em.status = EmailMessageStatus.SKIPPED.value

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.REPLIED.value,
            payload={"action": "manual_mark_replied"},
            created_at=clock.now(),
        )
    )
    db.commit()
    db.refresh(lead)
    return LeadResponse.model_validate(lead)


@router.post("/{lead_id}/mark-won", response_model=LeadResponse)
def mark_lead_won(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Mark lead as won deal."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")

    lead.status = LeadStatus.WON.value
    lead.updated_at = clock.now()

    pending_emails = db.scalars(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status.in_(
                [
                    EmailMessageStatus.DRAFT.value,
                    EmailMessageStatus.APPROVED.value,
                    EmailMessageStatus.SCHEDULED.value,
                ]
            ),
        )
    ).all()
    for em in pending_emails:
        em.status = EmailMessageStatus.SKIPPED.value

    db.add(
        Event(
            lead_id=lead.id,
            type="manual_action",
            payload={"action": "mark_won"},
            created_at=clock.now(),
        )
    )
    db.commit()
    db.refresh(lead)
    return LeadResponse.model_validate(lead)


@router.post("/{lead_id}/mark-not-interested", response_model=LeadResponse)
def mark_lead_not_interested(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Mark lead as not interested, unsubscribe, and add to global suppression."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")

    lead.status = LeadStatus.UNSUBSCRIBED.value
    lead.updated_at = clock.now()

    # Cancel pending emails
    pending_emails = db.scalars(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status.in_(
                [
                    EmailMessageStatus.DRAFT.value,
                    EmailMessageStatus.APPROVED.value,
                    EmailMessageStatus.SCHEDULED.value,
                ]
            ),
        )
    ).all()
    for em in pending_emails:
        em.status = EmailMessageStatus.SKIPPED.value

    add_suppression(db=db, target=lead.email, reason="manual not interested", clock=clock)

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.UNSUBSCRIBED.value,
            payload={"action": "mark_not_interested"},
            created_at=clock.now(),
        )
    )
    db.commit()
    db.refresh(lead)
    return LeadResponse.model_validate(lead)


@router.post("/{lead_id}/skip-step", response_model=LeadResponse)
def skip_current_step(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Skip the current pending email message step for this lead."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")

    current_email = db.scalar(
        select(EmailMessage)
        .where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status.in_(
                [
                    EmailMessageStatus.DRAFT.value,
                    EmailMessageStatus.APPROVED.value,
                    EmailMessageStatus.SCHEDULED.value,
                ]
            ),
        )
        .order_by(EmailMessage.step.asc())
    )
    if current_email:
        current_email.status = EmailMessageStatus.SKIPPED.value
        # If there is a next step, approve it
        next_email = db.scalar(
            select(EmailMessage).where(
                EmailMessage.lead_id == lead.id,
                EmailMessage.step == current_email.step + 1,
            )
        )
        if next_email:
            next_email.status = EmailMessageStatus.APPROVED.value

        db.add(
            Event(
                lead_id=lead.id,
                type="manual_action",
                payload={"action": "skip_step", "skipped_step": current_email.step},
                created_at=clock.now(),
            )
        )
        db.commit()
        db.refresh(lead)

    return LeadResponse.model_validate(lead)


@router.post("/bulk-validate", response_model=LeadBulkValidateResponse)
@router.post("/validate", response_model=LeadBulkValidateResponse)
def bulk_validate_leads(
    campaign_id: Annotated[int | None, Query()] = None,
    campaign_name: Annotated[str | None, Query()] = None,
    allow_risky: Annotated[bool | None, Query()] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadBulkValidateResponse:
    """Bulk validate unverified 'new' leads using email validator service."""
    summary = validate_leads(
        db=db,
        campaign_name=campaign_name,
        campaign_id=campaign_id,
        allow_risky=allow_risky,
        clock=clock,
    )
    return LeadBulkValidateResponse(
        total_checked=summary.total_checked,
        valid_count=summary.valid_count,
        invalid_count=summary.invalid_count,
        risky_count=summary.risk_risky,
    )


@router.post("/personalize", response_model=PersonalizeResponse)
def bulk_personalize_leads(
    campaign_id: Annotated[int | None, Query()] = None,
    campaign_name: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> PersonalizeResponse:
    """Run personalization pipeline across valid/new leads to generate AI draft observations."""
    from app.core.config import get_settings
    from app.services.fetcher import RealFetcher
    from app.services.llm import get_llm_client
    from app.services.personalizer import personalize_campaign

    settings = get_settings()
    fetcher = RealFetcher(settings=settings)
    llm = get_llm_client(settings=settings)

    if campaign_id is not None or campaign_name is not None:
        target = campaign_id if campaign_id is not None else campaign_name
        try:
            stats = personalize_campaign(
                campaign_name_or_id=target,  # type: ignore[arg-type]
                db=db,
                fetcher=fetcher,
                llm=llm,
                clock=clock,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    else:
        # Process all campaigns that have eligible leads
        campaigns = db.scalars(
            select(Campaign).where(
                Campaign.id.in_(
                    select(Lead.campaign_id).where(
                        Lead.status.in_([LeadStatus.VALID.value, LeadStatus.NEW.value])
                    )
                )
            )
        ).all()
        stats = {"total": 0, "personalized": 0, "preserved": 0, "needs_manual_note": 0}
        for c in campaigns:
            c_stats = personalize_campaign(
                campaign_name_or_id=c.id,
                db=db,
                fetcher=fetcher,
                llm=llm,
                clock=clock,
            )
            for k in stats:
                stats[k] += c_stats.get(k, 0)

    return PersonalizeResponse(
        total=stats["total"],
        personalized=stats["personalized"],
        preserved=stats["preserved"],
        needs_manual_note=stats["needs_manual_note"],
        message=(
            f"Processed {stats['total']} leads: "
            f"{stats['personalized']} personalized, "
            f"{stats['preserved']} preserved, "
            f"{stats['needs_manual_note']} need manual review."
        ),
    )


@router.get("/jit-status")
def get_jit_lead_status(
    target_date: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, Any]:
    """Retrieve current Just-In-Time (JIT) lead pool metrics and daily capacity status."""
    from datetime import date

    from app.services.jit_planner import get_jit_status

    parsed_date = date.fromisoformat(target_date.strip()) if target_date else None
    status = get_jit_status(db=db, target_date=parsed_date, clock=clock)
    return status.to_dict()


@router.post("/jit-process")
def trigger_jit_batch_process(
    target_date: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, Any]:
    """Manually trigger a JIT batch personalization and scheduling cycle for today."""
    from datetime import date

    from app.services.jit_planner import reconcile_and_refresh_daily_schedule

    parsed_date = date.fromisoformat(target_date.strip()) if target_date else None
    result = reconcile_and_refresh_daily_schedule(
        db=db,
        target_date=parsed_date,
        clock=clock,
    )
    return result.to_dict()
