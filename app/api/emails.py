"""Outbound email messages and dry-run outbox API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db
from app.api.schemas import (
    EmailMessageResponse,
    OutboxEmailResponse,
    PaginatedResponse,
)
from app.models.email_message import EmailMessage
from app.models.lead import Lead
from app.models.outbox import OutboxEmail

router = APIRouter(prefix="/emails", tags=["Emails"])


@router.get("", response_model=PaginatedResponse[EmailMessageResponse])
def list_emails(
    pagination: Annotated[Pagination, Depends()],
    status: Annotated[str | None, Query(description="Filter by message status")] = None,
    campaign_id: Annotated[int | None, Query(description="Filter by campaign ID")] = None,
    lead_id: Annotated[int | None, Query(description="Filter by lead ID")] = None,
    db: Session = Depends(get_db),
) -> PaginatedResponse[EmailMessageResponse]:
    """List outbound email messages with status and campaign filtering."""
    query = select(EmailMessage)

    if status:
        query = query.where(EmailMessage.status == status.lower())
    if lead_id is not None:
        query = query.where(EmailMessage.lead_id == lead_id)
    if campaign_id is not None:
        query = query.join(Lead).where(Lead.campaign_id == campaign_id)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    emails = db.scalars(
        query.order_by(EmailMessage.id.desc()).offset(pagination.offset).limit(pagination.page_size)
    ).all()

    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 0
    items = [EmailMessageResponse.model_validate(em) for em in emails]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.get("/outbox", response_model=PaginatedResponse[OutboxEmailResponse])
def list_outbox_emails(
    pagination: Annotated[Pagination, Depends()],
    db: Session = Depends(get_db),
) -> PaginatedResponse[OutboxEmailResponse]:
    """List rendered outbox messages persisted during dry-run operation."""
    query = select(OutboxEmail)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    outbox_emails = db.scalars(
        query.order_by(OutboxEmail.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 0
    items = [OutboxEmailResponse.model_validate(oe) for oe in outbox_emails]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.get("/{email_id}", response_model=EmailMessageResponse)
def get_email_message(
    email_id: int,
    db: Session = Depends(get_db),
) -> EmailMessageResponse:
    """Retrieve single email message record."""
    email_msg = db.get(EmailMessage, email_id)
    if not email_msg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email message {email_id} not found.",
        )
    return EmailMessageResponse.model_validate(email_msg)
