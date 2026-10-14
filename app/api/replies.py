"""Inbound reply monitoring and classification API endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_clock, get_db
from app.api.schemas import PaginatedResponse, ReplyResponse
from app.core.clock import Clock
from app.models.event import Event
from app.models.lead import Lead, LeadStatus
from app.models.reply import Reply

router = APIRouter(prefix="/replies", tags=["Replies"])


@router.get("", response_model=PaginatedResponse[ReplyResponse])
def list_replies(
    pagination: Annotated[Pagination, Depends()],
    classification: Annotated[str | None, Query(description="Filter by classification")] = None,
    is_positive_hint: Annotated[bool | None, Query(description="Filter positive hints")] = None,
    lead_id: Annotated[int | None, Query(description="Filter by lead ID")] = None,
    to_email: Annotated[str | None, Query(description="Filter by mailbox email")] = None,
    db: Session = Depends(get_db),
) -> PaginatedResponse[ReplyResponse]:
    """List captured inbound replies with classifications and positive sentiment flags."""
    query = select(Reply)

    if classification:
        query = query.where(Reply.classification == classification.lower())
    if is_positive_hint is not None:
        query = query.where(Reply.is_positive_hint == is_positive_hint)
    if lead_id is not None:
        query = query.where(Reply.lead_id == lead_id)
    if to_email:
        query = query.where(Reply.to_email == to_email.strip().lower())

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    replies = db.scalars(
        query.order_by(Reply.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 0
    items = [ReplyResponse.model_validate(r) for r in replies]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.post("/{reply_id}/handle", response_model=ReplyResponse)
def handle_reply(
    reply_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> ReplyResponse:
    """Mark inbound reply as reviewed/handled by operator."""
    reply = db.get(Reply, reply_id)
    if not reply:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reply {reply_id} not found.",
        )

    db.add(
        Event(
            lead_id=reply.lead_id,
            type="reply_handled",
            payload={"reply_id": reply.id, "from_email": reply.from_email},
            created_at=clock.now(),
        )
    )
    db.commit()
    db.refresh(reply)
    return ReplyResponse.model_validate(reply)


@router.post("/{reply_id}/mark-won", response_model=ReplyResponse)
def mark_reply_won(
    reply_id: int,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> ReplyResponse:
    """Mark lead associated with this reply as won customer."""
    reply = db.get(Reply, reply_id)
    if not reply:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reply {reply_id} not found.",
        )

    if reply.lead_id:
        lead = db.get(Lead, reply.lead_id)
        if lead:
            lead.status = LeadStatus.WON.value
            lead.updated_at = clock.now()
            db.add(
                Event(
                    lead_id=lead.id,
                    type="lead_won",
                    payload={"reply_id": reply.id, "action": "mark_won"},
                    created_at=clock.now(),
                )
            )
            db.commit()

    return ReplyResponse.model_validate(reply)


@router.get("/inboxes")
def get_reply_inboxes(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Return distinct inboxes that have received replies, with count of replies."""
    from app.models.mailbox import Mailbox

    mailboxes = db.scalars(select(Mailbox).order_by(Mailbox.id.asc())).all()
    mb_map = {m.email.lower(): m.display_name for m in mailboxes}

    rows = db.execute(
        select(Reply.to_email, func.count(Reply.id))
        .where(Reply.to_email.isnot(None))
        .group_by(Reply.to_email)
    ).all()

    counts = {r[0].lower(): r[1] for r in rows if r[0]}

    # Combine registered mailboxes with actual reply destinations
    all_emails = set(mb_map.keys()) | set(counts.keys())
    results = []
    for email in sorted(all_emails):
        results.append(
            {
                "email": email,
                "display_name": mb_map.get(email, email.split("@")[0]),
                "replies_count": counts.get(email, 0),
            }
        )
    return results

