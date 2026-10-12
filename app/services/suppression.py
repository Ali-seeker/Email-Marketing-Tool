"""Suppression list service.

Manages global email and domain suppression (do-not-contact lists).
When an email or domain is suppressed:
- Matching leads are transitioned to 'unsubscribed' (or 'bounced' if reason is bounce)
- Pending / scheduled email messages are canceled ('skipped')
- Audit events are emitted
"""

import csv
import io
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.logging import get_logger
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.suppression import Suppression

logger = get_logger(__name__)


def _is_domain_target(target: str, explicit_is_domain: bool | None = None) -> tuple[str, bool]:
    """Normalize target string and determine whether it represents a domain or single email."""
    cleaned = target.strip().lower()
    if explicit_is_domain is not None:
        if explicit_is_domain:
            cleaned = cleaned.lstrip("@")
        return cleaned, explicit_is_domain

    if cleaned.startswith("@"):
        return cleaned.lstrip("@"), True

    if "@" not in cleaned and "." in cleaned:
        return cleaned, True

    return cleaned, False


def add(
    db: Session,
    target: str,
    reason: str = "manual",
    is_domain: bool | None = None,
    clock: Clock | None = None,
) -> Suppression:
    """Add an email or domain to global suppression and flip matching leads.

    Args:
        db: SQLAlchemy session.
        target: Email address or domain (e.g. 'user@domain.com', 'competitor.com').
        reason: Reason for suppression ('manual', 'bounce', 'unsubscribe', etc.).
        is_domain: Explicit flag if target is a domain.
        clock: Optional Clock implementation.

    Returns:
        The created or existing Suppression record.
    """
    active_clock = clock or system_clock
    norm_target, domain_flag = _is_domain_target(target, is_domain)
    now_dt = active_clock.now()

    # 1. Fetch or create suppression record
    stmt = select(Suppression).where(Suppression.email == norm_target)
    record = db.scalar(stmt)
    if not record:
        record = Suppression(
            email=norm_target,
            reason=reason,
            is_domain=domain_flag,
            created_at=now_dt,
        )
        db.add(record)
        db.flush()
    else:
        record.reason = reason

    # 2. Find matching leads to cancel sequences and update status
    is_bounce = reason.strip().lower() in ("bounce", "bounced", "hard_bounce")
    new_lead_status = LeadStatus.BOUNCED.value if is_bounce else LeadStatus.UNSUBSCRIBED.value
    event_type = EventType.BOUNCED.value if is_bounce else EventType.UNSUBSCRIBED.value

    if domain_flag:
        # Match any email ending with @norm_target
        leads_stmt = select(Lead).where(Lead.email.like(f"%@{norm_target}"))
    else:
        leads_stmt = select(Lead).where(Lead.email == norm_target)

    matching_leads = db.scalars(leads_stmt).all()

    for lead in matching_leads:
        lead.status = new_lead_status
        lead.updated_at = now_dt

        # Emit event
        db.add(
            Event(
                lead_id=lead.id,
                type=event_type,
                payload={"reason": reason, "suppression_target": norm_target},
                created_at=now_dt,
            )
        )

        # Cancel pending / scheduled / approved / draft messages
        cancelable_statuses = (
            EmailMessageStatus.DRAFT.value,
            EmailMessageStatus.APPROVED.value,
            EmailMessageStatus.SCHEDULED.value,
        )
        msg_stmt = select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status.in_(cancelable_statuses),
        )
        for msg in db.scalars(msg_stmt).all():
            msg.status = EmailMessageStatus.SKIPPED.value
            msg.error = f"Canceled: suppressed ({reason})"

    db.commit()
    return record


def is_suppressed(db: Session, email: str) -> bool:
    """Check if an email address or its parent domain is suppressed."""
    norm_email = email.strip().lower()
    # 1. Check exact email
    direct = db.scalar(select(Suppression).where(Suppression.email == norm_email))
    if direct:
        return True

    # 2. Check domain suppression
    if "@" in norm_email:
        domain = norm_email.split("@")[1]
        domain_suppression = db.scalar(
            select(Suppression).where(
                Suppression.is_domain.is_(True),
                Suppression.email == domain,
            )
        )
        if domain_suppression:
            return True

    return False


def remove(db: Session, target: str) -> bool:
    """Remove an email or domain from the suppression list."""
    norm_target = target.strip().lower().lstrip("@")
    record = db.scalar(
        select(Suppression).where(
            or_(
                Suppression.email == norm_target,
                Suppression.email == f"@{norm_target}",
            )
        )
    )
    if record:
        db.delete(record)
        db.commit()
        return True
    return False


def import_from_csv(
    db: Session,
    csv_file: io.TextIOBase | str | Path,
    default_reason: str = "imported",
    clock: Clock | None = None,
) -> int:
    """Import suppressed emails/domains from a CSV file."""
    close_file = False
    if isinstance(csv_file, (str, Path)) and Path(str(csv_file)).is_file():
        f = open(str(csv_file), encoding="utf-8-sig")
        reader = csv.reader(f)
        close_file = True
    elif isinstance(csv_file, str):
        f = io.StringIO(csv_file)
        reader = csv.reader(f)
    else:
        f = csv_file
        reader = csv.reader(f)

    count = 0
    try:
        rows = list(reader)
        if not rows:
            return 0

        # Check for header
        header_skipped = False
        first_row = [c.strip().lower() for c in rows[0]]
        if "email" in first_row or "domain" in first_row or "target" in first_row:
            header_skipped = True
            email_col = 0
            reason_col = 1 if len(first_row) > 1 else None
            for idx, col in enumerate(first_row):
                if col in ("email", "domain", "target"):
                    email_col = idx
                elif col in ("reason", "note"):
                    reason_col = idx
        else:
            email_col = 0
            reason_col = 1 if len(rows[0]) > 1 else None

        start_idx = 1 if header_skipped else 0
        for row in rows[start_idx:]:
            if not row or not any(cell.strip() for cell in row):
                continue
            target_val = row[email_col].strip() if len(row) > email_col else ""
            if not target_val:
                continue
            reason_val = (
                row[reason_col].strip()
                if (reason_col is not None and len(row) > reason_col and row[reason_col].strip())
                else default_reason
            )
            add(db=db, target=target_val, reason=reason_val, clock=clock)
            count += 1
    finally:
        if close_file:
            f.close()

    return count


def export_to_csv(
    db: Session,
    output_dest: io.TextIOBase | str | Path,
) -> int:
    """Export all suppression entries to CSV."""
    records = db.scalars(select(Suppression).order_by(Suppression.id)).all()

    close_file = False
    if isinstance(output_dest, (str, Path)):
        f = open(str(output_dest), "w", newline="", encoding="utf-8")
        close_file = True
    else:
        f = output_dest

    count = 0
    try:
        writer = csv.DictWriter(f, fieldnames=["email", "reason", "is_domain", "created_at"])
        writer.writeheader()
        for rec in records:
            writer.writerow(
                {
                    "email": rec.email,
                    "reason": rec.reason,
                    "is_domain": rec.is_domain,
                    "created_at": rec.created_at.isoformat() if rec.created_at else "",
                }
            )
            count += 1
    finally:
        if close_file:
            f.close()

    return count
