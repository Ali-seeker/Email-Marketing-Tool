"""Lead sequence enrollment API endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_clock, get_db
from app.api.schemas import EnrollRequest, EnrollResponse
from app.core.clock import Clock
from app.models.campaign import Campaign
from app.services.sequencer import enroll

router = APIRouter(prefix="/enrollment", tags=["Enrollment"])


@router.post("/enroll", response_model=EnrollResponse)
def enroll_approved_leads(
    payload: EnrollRequest,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> EnrollResponse:
    """Enroll all approved leads of a campaign into the outreach sequence."""
    campaign = db.get(Campaign, payload.campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {payload.campaign_id} not found.",
        )

    enrolled_leads = enroll(campaign, db=db, clock=clock)
    return EnrollResponse(
        campaign_id=campaign.id,
        enrolled_count=len(enrolled_leads),
        lead_ids=[ld.id for ld in enrolled_leads],
    )
