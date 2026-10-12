"""Approval queue management service.

Implements human review gatekeeper:
- list_pending(): Returns leads awaiting operator review (status 'personalized').
- approve(lead_id): Approves lead for sequence enrollment.
- approve_many(ids): Batch approves multiple leads.
- edit_and_approve(lead_id, new_note): Updates personal_note and approves.
- reject(lead_id): Rejects note and moves lead to 'needs_manual_note'.
- regenerate(lead_id): Re-runs web scraper and LLM personalizer.
"""

from datetime import UTC
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.logging import get_logger
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.services.fetcher import WebFetcher
from app.services.llm import LlmClient

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def list_pending(
    campaign_id: int | None = None,
    db: Session | None = None,
) -> list[Lead]:
    """Retrieve leads currently in 'personalized' status awaiting operator review.

    Args:
        campaign_id: Optional campaign filter.
        db: SQLAlchemy session.

    Returns:
        List of Lead records with draft note and evidence quote.
    """
    if db is None:
        raise ValueError("Database session is required")

    stmt = select(Lead).where(Lead.status == LeadStatus.PERSONALIZED.value)
    if campaign_id is not None:
        stmt = stmt.where(Lead.campaign_id == campaign_id)

    stmt = stmt.order_by(Lead.id.asc())
    return list(db.scalars(stmt).all())


def approve(
    lead_id: int,
    db: Session,
    clock: Clock | None = None,
) -> Lead:
    """Approve a personalized lead draft for sequence enrollment.

    Args:
        lead_id: Lead primary key.
        db: SQLAlchemy session.
        clock: Optional Clock implementation.

    Returns:
        Approved Lead instance.
    """
    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    if not lead.personal_note or not lead.personal_note.strip():
        raise ValueError(f"Lead #{lead_id} cannot be approved without a personal_note")

    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    lead.status = LeadStatus.APPROVED.value
    lead.validation_reason = None

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.VALIDATED.value,
            payload={
                "stage": "approval",
                "action": "approved",
                "note": lead.personal_note,
                "evidence": lead.evidence_quote,
            },
            created_at=now_dt,
        )
    )
    db.commit()
    logger.info("Lead #%d (%s) approved by operator", lead.id, lead.email)
    return lead


def approve_many(
    lead_ids: list[int],
    db: Session,
    clock: Clock | None = None,
) -> list[Lead]:
    """Batch approve multiple personalized leads.

    Args:
        lead_ids: List of Lead IDs.
        db: SQLAlchemy session.
        clock: Optional Clock implementation.

    Returns:
        List of approved Lead records.
    """
    approved_leads: list[Lead] = []
    for lid in lead_ids:
        try:
            lead = approve(lid, db=db, clock=clock)
            approved_leads.append(lead)
        except Exception as exc:
            logger.warning("Failed to approve lead #%d: %s", lid, exc)
    return approved_leads


def edit_and_approve(
    lead_id: int,
    new_note: str,
    db: Session,
    clock: Clock | None = None,
) -> Lead:
    """Update personal note with manual edit and immediately approve lead.

    Args:
        lead_id: Lead primary key.
        new_note: Operator-edited opening note.
        db: SQLAlchemy session.
        clock: Optional Clock implementation.

    Returns:
        Updated and approved Lead instance.
    """
    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    cleaned_note = (new_note or "").strip()
    if not cleaned_note:
        raise ValueError("New note cannot be empty")

    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    lead.personal_note = cleaned_note
    lead.status = LeadStatus.APPROVED.value
    lead.validation_reason = None

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.VALIDATED.value,
            payload={
                "stage": "approval",
                "action": "edited_and_approved",
                "note": cleaned_note,
            },
            created_at=now_dt,
        )
    )
    db.commit()
    logger.info("Lead #%d (%s) edited and approved by operator", lead.id, lead.email)
    return lead


def reject(
    lead_id: int,
    reason: str | None = None,
    db: Session | None = None,
    clock: Clock | None = None,
) -> Lead:
    """Reject personalized draft and transition lead to 'needs_manual_note'.

    Args:
        lead_id: Lead primary key.
        reason: Optional operator explanation.
        db: SQLAlchemy session.
        clock: Optional Clock implementation.

    Returns:
        Updated Lead instance.
    """
    if db is None:
        raise ValueError("Database session is required")

    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
    lead.validation_reason = reason or "Rejected during manual operator review"

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.ERROR.value,
            payload={
                "stage": "approval",
                "action": "rejected",
                "reason": lead.validation_reason,
            },
            created_at=now_dt,
        )
    )
    db.commit()
    logger.info("Lead #%d (%s) rejected: %s", lead.id, lead.email, lead.validation_reason)
    return lead


def regenerate(
    lead_id: int,
    db: Session,
    fetcher: WebFetcher,
    llm: LlmClient,
    clock: Clock | None = None,
) -> Lead:
    """Clear existing draft and regenerate personalization from scratch.

    Args:
        lead_id: Lead primary key.
        db: SQLAlchemy session.
        fetcher: WebFetcher implementation.
        llm: LlmClient implementation.
        clock: Optional Clock implementation.

    Returns:
        Updated Lead instance.
    """
    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    # Clear current draft so personalize_lead does not skip
    lead.personal_note = None
    lead.evidence_quote = None
    lead.status = LeadStatus.VALID.value
    db.commit()

    from app.services.personalizer import personalize_lead

    personalize_lead(lead, db=db, fetcher=fetcher, llm=llm, clock=clock)
    db.refresh(lead)
    return lead
