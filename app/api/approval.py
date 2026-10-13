"""Approval queue API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_clock, get_db
from app.api.schemas import (
    ApproveManyRequest,
    EditAndApproveRequest,
    LeadResponse,
    PaginatedResponse,
    PendingApprovalItem,
    RejectRequest,
)
from app.core.clock import Clock
from app.services.approval import (
    approve,
    approve_many,
    edit_and_approve,
    list_pending,
    regenerate,
    reject,
)
from app.services.sequencer import enroll

router = APIRouter(prefix="/approval", tags=["Approval Queue"])


@router.get("/pending", response_model=PaginatedResponse[PendingApprovalItem])
def get_pending_approvals(
    pagination: Annotated[Pagination, Depends()],
    campaign_id: Annotated[int | None, Query()] = None,
    db: Session = Depends(get_db),
) -> PaginatedResponse[PendingApprovalItem]:
    """List pending AI-personalized drafts with evidence quote for operator review."""
    pending_leads = list_pending(campaign_id=campaign_id, db=db)
    total = len(pending_leads)

    sliced = pending_leads[pagination.offset : pagination.offset + pagination.page_size]

    items = [
        PendingApprovalItem(
            lead_id=ld.id,
            campaign_id=ld.campaign_id,
            campaign_name=ld.campaign.name if ld.campaign else None,
            email=ld.email,
            company=ld.company,
            website=ld.website,
            personal_note=ld.personal_note,
            evidence_quote=ld.evidence_quote,
        )
        for ld in sliced
    ]

    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 0
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.post("/{lead_id}/approve", response_model=LeadResponse)
def approve_draft(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Approve a personalized lead draft for sequence enrollment."""
    try:
        lead = approve(lead_id, db=db, clock=clock)
        if lead.campaign_id:
            try:
                enroll(lead.campaign_id, db=db, clock=clock)
            except Exception:
                pass
        return LeadResponse.model_validate(lead)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/approve-many")
def bulk_approve_drafts(
    payload: ApproveManyRequest,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Batch approve multiple personalized lead drafts."""
    approved_leads = approve_many(payload.lead_ids, db=db, clock=clock)
    campaign_ids = {ld.campaign_id for ld in approved_leads if ld.campaign_id}
    for cid in campaign_ids:
        try:
            enroll(cid, db=db, clock=clock)
        except Exception:
            pass
    return {
        "requested": len(payload.lead_ids),
        "approved": len(approved_leads),
        "lead_ids": [ld.id for ld in approved_leads],
    }


@router.post("/{lead_id}/edit-and-approve", response_model=LeadResponse)
def edit_and_approve_draft(
    lead_id: int,
    payload: EditAndApproveRequest,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Edit personal note and immediately approve lead."""
    try:
        lead = edit_and_approve(lead_id, new_note=payload.new_note, db=db, clock=clock)
        if lead.campaign_id:
            try:
                enroll(lead.campaign_id, db=db, clock=clock)
            except Exception:
                pass
        return LeadResponse.model_validate(lead)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/{lead_id}/reject", response_model=LeadResponse)
def reject_draft(
    lead_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Reject a draft and mark lead needs_manual_note."""
    try:
        lead = reject(lead_id, reason=payload.reason or "Rejected by reviewer", db=db, clock=clock)
        return LeadResponse.model_validate(lead)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{lead_id}/regenerate", response_model=LeadResponse)
def regenerate_draft(
    lead_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> LeadResponse:
    """Trigger AI re-generation of personalization for lead."""
    from app.core.config import get_settings
    from app.services.fetcher import RealFetcher
    from app.services.llm import get_llm_client

    settings = get_settings()
    fetcher = RealFetcher(settings=settings)
    llm = get_llm_client(settings=settings)

    try:
        lead = regenerate(lead_id, db=db, fetcher=fetcher, llm=llm, clock=clock)
        return LeadResponse.model_validate(lead)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

