"""Campaign management API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_clock, get_db
from app.api.schemas import CampaignCreate, CampaignResponse, CampaignUpdate, PaginatedResponse
from app.core.clock import Clock
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage
from app.models.lead import Lead, LeadStatus
from app.services.planner import get_mailbox_tz, plan_day
from app.services.sequencer import check_and_update_completed_campaigns
from app.services.templates import load_default_sequence

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


@router.get("", response_model=PaginatedResponse[CampaignResponse])
def list_campaigns(
    pagination: Annotated[Pagination, Depends()],
    status: Annotated[str | None, Query(description="Filter by campaign status")] = None,
    sort_by: Annotated[str, Query(description="Field to sort by")] = "id",
    sort_order: Annotated[str, Query(description="asc or desc")] = "desc",
    db: Session = Depends(get_db),
) -> PaginatedResponse[CampaignResponse]:
    """Retrieve paginated list of campaigns with lead counts."""
    # Check and transition any completed campaigns
    check_and_update_completed_campaigns(db)

    query = select(Campaign)
    if status:
        query = query.where(Campaign.status == status.lower())

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    if sort_by == "name":
        order_col = Campaign.name.desc() if sort_order == "desc" else Campaign.name.asc()
    else:
        order_col = Campaign.id.desc() if sort_order == "desc" else Campaign.id.asc()

    campaigns = db.scalars(
        query.order_by(order_col).offset(pagination.offset).limit(pagination.page_size)
    ).all()

    items: list[CampaignResponse] = []
    for c in campaigns:
        total_leads = db.scalar(select(func.count(Lead.id)).where(Lead.campaign_id == c.id)) or 0
        active_leads = (
            db.scalar(
                select(func.count(Lead.id)).where(
                    Lead.campaign_id == c.id,
                    Lead.status.in_(
                        [
                            LeadStatus.APPROVED.value,
                            LeadStatus.IN_SEQUENCE.value,
                            LeadStatus.PERSONALIZED.value,
                        ]
                    ),
                )
            )
            or 0
        )
        item = CampaignResponse(
            id=c.id,
            name=c.name,
            niche=c.niche,
            status=c.status,
            created_at=c.created_at,
            leads_count=total_leads,
            active_leads_count=active_leads,
        )
        items.append(item)

    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 0
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Create a new campaign and auto-seed default sequence steps."""
    existing = db.scalar(select(Campaign).where(Campaign.name == payload.name))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Campaign with name '{payload.name}' already exists.",
        )

    campaign = Campaign(
        name=payload.name,
        niche=payload.niche,
        status=CampaignStatus.DRAFT.value,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    # Seed default sequence
    try:
        load_default_sequence(campaign.id, db)
    except Exception:
        pass

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        niche=campaign.niche,
        status=campaign.status,
        created_at=campaign.created_at,
        leads_count=0,
        active_leads_count=0,
    )


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Get single campaign details and summary counts."""
    # Check and transition if all leads finished
    check_and_update_completed_campaigns(db, campaign_id=campaign_id)

    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )

    total_leads = db.scalar(select(func.count(Lead.id)).where(Lead.campaign_id == campaign.id)) or 0
    active_leads = (
        db.scalar(
            select(func.count(Lead.id)).where(
                Lead.campaign_id == campaign.id,
                Lead.status.in_(
                    [
                        LeadStatus.APPROVED.value,
                        LeadStatus.IN_SEQUENCE.value,
                        LeadStatus.PERSONALIZED.value,
                    ]
                ),
            )
        )
        or 0
    )

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        niche=campaign.niche,
        status=campaign.status,
        created_at=campaign.created_at,
        leads_count=total_leads,
        active_leads_count=active_leads,
    )


@router.patch("/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Update campaign metadata."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )

    if payload.name is not None and payload.name != campaign.name:
        existing = db.scalar(select(Campaign).where(Campaign.name == payload.name))
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Campaign with name '{payload.name}' already exists.",
            )
        campaign.name = payload.name

    if payload.niche is not None:
        campaign.niche = payload.niche

    db.commit()
    db.refresh(campaign)
    return get_campaign(campaign_id, db=db)


@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
def pause_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Pause an active campaign."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )
    campaign.status = CampaignStatus.PAUSED.value
    db.commit()
    return get_campaign(campaign_id, db=db)


@router.post("/{campaign_id}/resume", response_model=CampaignResponse)
def resume_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Resume a paused campaign."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )
    campaign.status = CampaignStatus.ACTIVE.value
    db.commit()
    return get_campaign(campaign_id, db=db)


@router.post("/{campaign_id}/activate", response_model=CampaignResponse)
def activate_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> CampaignResponse:
    """Activate a draft or paused campaign and schedule its due emails."""
    res = resume_campaign(campaign_id, db=db)
    try:
        clk_now = clock.now()
        mailbox_tz = get_mailbox_tz(db)
        today_date = clk_now.astimezone(mailbox_tz).date()
        plan_day(target_date=today_date, db=db, clock=clock)
    except Exception:
        pass
    return res


@router.get("/{campaign_id}/schedule")
def get_campaign_schedule(
    campaign_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
):
    """Retrieve all email messages and their schedule/status for leads in this campaign."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )

    # Auto-schedule any approved due emails for active campaign if they have no scheduled_at
    if campaign.status == CampaignStatus.ACTIVE.value:
        try:
            clk_now = clock.now()
            mailbox_tz = get_mailbox_tz(db)
            today_date = clk_now.astimezone(mailbox_tz).date()
            plan_day(target_date=today_date, db=db, clock=clock)
        except Exception:
            pass

    emails = db.scalars(
        select(EmailMessage)
        .join(Lead, Lead.id == EmailMessage.lead_id)
        .where(Lead.campaign_id == campaign_id)
        .order_by(EmailMessage.step.asc(), EmailMessage.id.asc())
    ).all()

    return [
        {
            "id": em.id,
            "lead_id": em.lead_id,
            "email": em.lead.email if em.lead else "",
            "company": em.lead.company if em.lead else "",
            "step": em.step,
            "subject": em.subject,
            "status": em.status,
            "scheduled_at": em.scheduled_at,
            "sent_at": em.sent_at,
            "error": em.error,
        }
        for em in emails
    ]


@router.post("/{campaign_id}/complete", response_model=CampaignResponse)
def complete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Manually mark an active or paused campaign as completed."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )
    campaign.status = CampaignStatus.COMPLETED.value
    db.commit()
    return get_campaign(campaign_id, db=db)


@router.delete("/{campaign_id}")
def delete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    """Delete a campaign and cascade deletion to its sequence steps, leads, and messages."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )
    name = campaign.name
    db.delete(campaign)
    db.commit()
    return {"status": "deleted", "id": campaign_id, "name": name}
